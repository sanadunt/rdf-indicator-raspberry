"""Throughput, loss and latency test for a transparent LoRa serial link (RLS1 TEST frames).

Roles, one per side of the radio:
  rx    count TEST frames: delivered bytes/s, loss from sequence gaps, CRC errors, one-way delay
        (meaningful only when both clocks agree), printed per step and as a JSON summary.
  echo  send every TEST frame straight back (for round-trip time).
  tx    send TEST frames at each rate x frame size of a sweep; with --expect-echo, also time the echoes.

A TEST frame is an RLS1 frame of type 3 whose chunk starts with run (u16), step (u16), seq (u32),
sender wall clock ms (u64) and a flags byte (bit0: echo), padded to the requested frame size.
"""
from __future__ import annotations
import json
import os
import select
import struct
import time
from .lora import HEADER, CRC_LEN, MAGIC, Receiver, cobs_encode, crc16, open_port
from .util import now_ms

TEST=3
BODY=struct.Struct('!HHIQB')

def test_frame(run,step,seq,size,echo=False,t=None):
    """One TEST frame of exactly `size` bytes on the wire (COBS + 0x00 included), when size allows."""
    t=now_ms() if t is None else t
    head=BODY.pack(run,step,seq,t,1 if echo else 0)
    pad=max(0,size-(HEADER.size+CRC_LEN+len(head)+3))
    for _ in range(3):
        raw=HEADER.pack(MAGIC,TEST,seq&0xFFFF,0,0,0,0,1)+head+bytes((i*37+11)%250+1 for i in range(pad))
        frame=cobs_encode(raw+struct.pack('!H',crc16(raw)))+b'\x00'
        if len(frame)==size or pad==0: return frame
        pad=max(0,pad-(len(frame)-size))
    return frame

def parse_test(raw_frame):
    """(run, step, seq, t_ms, echo, wire_len) from a frame without its 0x00, or None."""
    from .lora import cobs_decode
    raw=cobs_decode(raw_frame)
    if len(raw)<HEADER.size+BODY.size+CRC_LEN: raise ValueError('FRAME_SHORT')
    if struct.unpack('!H',raw[-2:])[0]!=crc16(raw[:-2]): raise ValueError('CRC_MISMATCH')
    magic,kind=raw[0],raw[1]
    if magic!=MAGIC or kind!=TEST: return None
    run,step,seq,t,flags=BODY.unpack_from(raw,HEADER.size)
    return run,step,seq,t,bool(flags&1),len(raw_frame)+1

class Splitter:
    def __init__(self): self.buf=bytearray(); self.crc_errors=0; self.bad=0
    def feed(self,data):
        self.buf+=data; out=[]
        while True:
            end=self.buf.find(0)
            if end<0:
                if len(self.buf)>4096: self.buf.clear(); self.bad+=1
                return out
            wire=bytes(self.buf[:end]); del self.buf[:end+1]
            if not wire: continue
            try:
                parsed=parse_test(wire)
                if parsed: out.append(parsed)
            except ValueError as e:
                if str(e)=='CRC_MISMATCH': self.crc_errors+=1
                else: self.bad+=1

def _write_all(fd,data,deadline):
    view=memoryview(data)
    while view:
        left=deadline-time.monotonic()
        if left<=0: return False
        _,w,_=select.select([],[fd],[],left)
        if w: view=view[os.write(fd,view):]
    return True

def run_rx(fd,idle_s=8.0,out=print):
    """Count frames until `idle_s` without data after the last step; per-step stats and a summary."""
    sp=Splitter(); steps={}; last=time.monotonic(); started=None
    while True:
        r,_,_=select.select([fd],[],[],.2)
        now=time.monotonic()
        if r:
            data=os.read(fd,4096)
            if not data: break
            for run,step,seq,t,echo,size in sp.feed(data):
                if echo: continue
                started=started or now; last=now
                s=steps.setdefault((run,step),dict(run=run,step=step,first=now,last=now,frames=0,bytes=0,
                                                   min_seq=seq,max_seq=seq,delay=[]))
                s['last']=now; s['frames']+=1; s['bytes']+=size
                s['min_seq']=min(s['min_seq'],seq); s['max_seq']=max(s['max_seq'],seq)
                s['delay'].append(now_ms()-t)
        elif started and now-last>idle_s: break
    report=[]
    for s in sorted(steps.values(),key=lambda s:(s['run'],s['step'])):
        span=max(s['last']-s['first'],1e-3); expected=s['max_seq']-s['min_seq']+1
        d=sorted(s['delay'])
        report.append(dict(run=s['run'],step=s['step'],frames=s['frames'],expected=expected,
                           loss_pct=round(100*(1-s['frames']/expected),1),bytes_per_s=round(s['bytes']/span),
                           delay_ms_p50=d[len(d)//2],delay_ms_p95=d[int(len(d)*.95)-1 if len(d)>1 else 0]))
        out(json.dumps(report[-1]))
    out(json.dumps(dict(summary='rx',crc_errors=sp.crc_errors,bad_frames=sp.bad,steps=len(report))))
    return report

def run_echo(fd,idle_s=30.0,out=print):
    sp=Splitter(); count=0; last=time.monotonic()
    while time.monotonic()-last<idle_s:
        r,_,_=select.select([fd],[],[],.2)
        if not r: continue
        data=os.read(fd,4096)
        if not data: break
        for run,step,seq,t,echo,size in sp.feed(data):
            if echo: continue
            last=time.monotonic(); count+=1
            _write_all(fd,test_frame(run,step,seq,size,echo=True,t=t),last+1)
    out(json.dumps(dict(summary='echo',echoed=count,crc_errors=sp.crc_errors,bad_frames=sp.bad)))

def run_tx(fd,rates,sizes,duration,run_id,expect_echo=False,gap_s=3.0,out=print):
    sp=Splitter(); results=[]; step=0
    for size in sizes:
        for rate in rates:
            step+=1; seq=0; sent=0; rtts=[]; echoed=0; interval=size/rate
            t0=time.monotonic(); nxt=t0
            while time.monotonic()-t0<duration:
                now=time.monotonic()
                if now>=nxt:
                    seq+=1
                    if _write_all(fd,test_frame(run_id,step,seq,size),now+1): sent+=1
                    nxt+=interval
                    if nxt<now-interval*4: nxt=now  # the port blocked: do not burst to catch up
                r,_,_=select.select([fd],[],[],max(0,min(nxt-time.monotonic(),.05)))
                if r:
                    for run,st,sq,t,echo,_ in sp.feed(os.read(fd,4096)):
                        if echo and run==run_id and st==step: echoed+=1; rtts.append(now_ms()-t)
            # Let late echoes and the radio queue drain before the next step.
            end=time.monotonic()+gap_s
            while time.monotonic()<end:
                r,_,_=select.select([fd],[],[],.1)
                if r:
                    for run,st,sq,t,echo,_ in sp.feed(os.read(fd,4096)):
                        if echo and run==run_id and st==step: echoed+=1; rtts.append(now_ms()-t)
            rtts.sort()
            res=dict(run=run_id,step=step,size=size,target_bytes_per_s=rate,sent=sent,
                     offered_bytes_per_s=round(sent*size/duration))
            if expect_echo:
                res.update(echoed=echoed,echo_loss_pct=round(100*(1-echoed/sent),1) if sent else None,
                           rtt_ms_p50=rtts[len(rtts)//2] if rtts else None,
                           rtt_ms_p95=rtts[int(len(rtts)*.95)-1] if len(rtts)>1 else (rtts[0] if rtts else None))
            results.append(res); out(json.dumps(res))
    out(json.dumps(dict(summary='tx',steps=len(results),crc_errors=sp.crc_errors)))
    return results

def main(a):
    fd=open_port(a.device,a.baud)
    try:
        if a.role=='rx': run_rx(fd,a.idle)
        elif a.role=='echo': run_echo(fd,a.idle)
        else:
            rates=[int(x) for x in a.rates.split(',')]; sizes=[int(x) for x in a.sizes.split(',')]
            run_tx(fd,rates,sizes,a.duration,a.run or (now_ms()//1000)&0xFFFF,a.expect_echo,a.gap)
    finally:
        os.close(fd)
