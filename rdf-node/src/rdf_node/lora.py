"""MQTT over a transparent LoRa serial link, without PPP or TCP/IP (RLS1, see docs/LORA_SERIAL.md).

One bridge runs on each side of the radio and talks to its own local MQTT broker:

* edge:   subscribes the Edge topics (telemetry, state, availability, ACKs...) on the Pi's broker and
          sends them over LoRa; publishes Ground commands it receives back into that broker.
* ground: subscribes the node's `cmd/#` on the Ground broker and sends them over LoRa; publishes what
          the Edge sent into the Ground broker with the same topic, payload bytes and retain flag.

So rdf-edge, rdf-ground and any other consumer keep the MQTT contract unchanged. A frame is
`header | chunk | CRC-16`, COBS-encoded and ended by 0x00. Payloads are zlib-compressed with a shared
dictionary when that is smaller, split into fragments of at most `frame_max` bytes on the wire, and
paced by a byte budget so the module's ~1 KB buffer never overflows. There is no retransmission:
telemetry is replaced by newer telemetry, retained topics are resent when the link comes back, ACKs
are sent twice and commands three times (the receiver drops the copies).
"""
from __future__ import annotations
import json
import os
import select
import struct
import threading
import time
import zlib
from collections import deque
from .mqtt import Client
from .util import now_ms
from .timesync import ChronySock
try:
    import fcntl
    import termios
except ImportError:  # pragma: no cover - non-POSIX hosts
    fcntl = termios = None

MAGIC=0xA1
DATA=1
HEARTBEAT=2
TIME=4  # 3 is the TEST frame of lora_test
TIME_REQ=struct.Struct('!BQ')        # op 1, t1 (edge wall clock, us)
TIME_RESP=struct.Struct('!BQQQ')     # op 2, t1, t2 (ground receive), t3 (ground send), us
HEADER=struct.Struct('!BBHBBBBB')  # magic type seq msg topic flags index count
F_RETAIN=1; F_QOS1=2; F_ZLIB=4
# Half-duplex turn taking: the edge marks a frame with F_TURN about once a second and then stays quiet
# for EDGE_QUIET_S; the ground transmits only right after such a frame (or freely when the edge has been
# silent for GROUND_FREE_AFTER_S), so the two radios rarely talk at once.
F_TURN=8
# An Angular curve (topics 4, 6) whose values went over as one byte each; the receiver rebuilds the JSON.
F_PACKED=16
# Air time per byte while frames go back to back: a single frame takes ~0.48 ms/byte, but a run of frames
# only moves ~1.2-1.5 KB/s. With 0.5 ms the estimate fell 0.2-0.3 s short after a 5-frame Angular curve,
# the edge came back inside Ground's reply and ~92% of Ground->edge frames were lost (2026-11-10).
AIR_S_PER_BYTE=0.0008
# The quiet window counts from the moment the turn frame really leaves the air: frames written just
# before it still sit in the module's buffer, and Ground needs its reply window (GROUND_REPLY_WINDOW_S),
# up to GROUND_WINDOW_BYTES on the air (~0.12 s) and its own module's latency inside that window.
EDGE_TURN_EVERY_S=1.0; EDGE_QUIET_S=0.45
GROUND_REPLY_WINDOW_S=0.08; GROUND_FREE_AFTER_S=3.0; GROUND_WINDOW_BYTES=250
CRC_LEN=2
# Fragment state older than this is dropped (a lost fragment never completes its message).
REASSEMBLY_S=15.0
DEDUP_S=30.0

# Topic ids are part of the wire format: never renumber, only append.
EDGE_TOPICS={
    1:'telemetry/health', 2:'telemetry/health/detail', 3:'telemetry/doa', 4:'telemetry/angular',
    5:'telemetry/diagnostic/doa', 6:'telemetry/diagnostic/angular', 7:'state', 8:'capabilities',
    9:'availability', 10:'settings/reported', 11:'ack/config', 12:'ack/operation',
}
GROUND_TOPICS={
    64:'cmd/config/get', 65:'cmd/config/patch', 66:'cmd/processing/set', 67:'cmd/service/restart',
    68:'cmd/service/ppp/restart', 69:'cmd/system/reboot/prepare', 70:'cmd/system/reboot/execute',
    71:'cmd/system/shutdown/prepare', 72:'cmd/system/shutdown/execute', 73:'cmd/operation/get',
    74:'cmd/stream/set',
}
TOPIC_IDS={suffix:i for i,suffix in {**EDGE_TOPICS,**GROUND_TOPICS}.items()}

# Sending policy per topic id: (priority: lower first, max age s or None, latest-wins, sent twice).
# A topic "sent twice" that is a command goes COMMAND_SENDS times.
# Ages stay inside what Ground accepts as fresh (DoA 5 s, angular 10 s, health 8 s). DoA comes first
# among telemetry and goes twice; health is the newest one at most every 2 s (Ground needs <= 8 s).
POLICY={
    1:(2,6,True,False), 2:(4,15,True,False), 3:(1,3,True,True), 4:(5,8,True,False),
    5:(2,3,True,False), 6:(5,8,True,False), 7:(0,None,True,False), 8:(3,None,True,False),
    9:(0,None,True,False), 10:(2,30,False,True), 11:(0,30,False,True), 12:(0,30,False,True),
}
# Commands carry a 15 s deadline (expires_ms); one that cannot leave within 10 s is dropped here.
for _id in GROUND_TOPICS: POLICY[_id]=(0,10,False,True)
# Ground to edge loses more frames (~13%) than the other way, and a lost command is only seen as no ACK
# 20 s later: three sends of a one-frame command lose it ~0.2% of the time instead of ~1.7%. Commands are
# rare and small, so the airtime costs nothing.
COMMAND_SENDS=3
RETAINED_RESEND=(9,7,8)  # availability, state, capabilities: resent when the link comes back
# ...and every few minutes: a retained topic Edge publishes once (capabilities at connect) is lost for good
# when one of its frames is, and a few hundred bytes every 2 min cost nothing on the link.
RETAINED_REFRESH_S=120.0

# Shared zlib dictionary: the keys and values Edge payloads repeat. Part of the protocol (RLS1).
ZDICT=(b'"availability","online":true,"contract":"1.6","capabilities","remote_","profile":"balanced",'
       b'"clock":"SYNCED","run":"RUNNING","daq":true,"cfg":"boot":"instance":"why":[],"rev":"clk":1,'
       b'"temp":"age":"drop":"hs":"OK","hdg":"gs":"FIX","fix":3,"sat":"lat":"lon":"alt":"sog":"cog":'
       b'"trust":"UNVERIFIED","angle_reference":"RAW","validation_reasons":["SOURCE_UNVERIFIED",'
       b'"ANGLE_UNVERIFIED","NO_FRESH_DOA","DOA_FILE_NOT_UPDATING","EMPTY_CSV","DAQ_NOT_HEALTHY",'
       b'"reason":[],"ok":1,"f":433920000,"a":"c":"p":"t":17934,"q":"sid":"v":2,'
       b'"encoding":"json","timestamp_ms":17934,"frequency_hz":"revision":"vfo":0,"convention":1,'
       b'"flags":63,"raw_doa_deg":"confidence_native_db":"values":[0.0,1.0,2.0,"status":"APPLIED",'
       b'"result":{},"id":"op":"source":"doa.xml","observed_timestamp_ms":"source_timestamp_ms":')

def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)."""
    crc=0xFFFF
    for b in data:
        crc^=b<<8
        for _ in range(8):
            crc=((crc<<1)^0x1021)&0xFFFF if crc&0x8000 else (crc<<1)&0xFFFF
    return crc

def cobs_encode(data: bytes) -> bytes:
    out=bytearray(); block=bytearray()
    for b in data:
        if b==0:
            out.append(len(block)+1); out+=block; block.clear()
        else:
            block.append(b)
            if len(block)==254:
                out.append(255); out+=block; block.clear()
    out.append(len(block)+1); out+=block
    return bytes(out)

def cobs_decode(data: bytes) -> bytes:
    out=bytearray(); i=0
    while i<len(data):
        code=data[i]
        if code==0 or i+code>len(data): raise ValueError('COBS_BAD')
        out+=data[i+1:i+code]; i+=code
        if code<255 and i<len(data): out.append(0)
    return bytes(out)

def compress(payload: bytes):
    c=zlib.compressobj(9,zlib.DEFLATED,-15,9,zlib.Z_DEFAULT_STRATEGY,ZDICT)
    packed=c.compress(payload)+c.flush()
    return (packed,True) if len(packed)<len(payload) else (payload,False)

def decompress(packed: bytes, limit=16384) -> bytes:
    d=zlib.decompressobj(-15,ZDICT)
    out=d.decompress(packed,limit)
    if d.unconsumed_tail or not d.eof: raise ValueError('ZLIB_BAD_OR_TOO_LARGE')
    return out

# Packed Angular body: version, the smallest value in hundredths, the JSON up to its values array, then one
# byte per value (hundredths above that smallest). Only when it rebuilds the original bytes exactly.
PACKED_HEAD=struct.Struct('!BiH'); PACKED_V1=1; VALUES_KEY=b',"values":['

def _angular_json(header,low,steps):
    return header+VALUES_KEY+b','.join(json.dumps((low+d)/100).encode() for d in steps)+b']}'

def pack_angular(payload: bytes):
    """The packed body of an Angular JSON payload, or None when it cannot go over losslessly."""
    cut=payload.rfind(VALUES_KEY)
    if cut<0 or not payload.endswith(b']}'): return None
    try: values=json.loads(payload[cut+len(VALUES_KEY)-1:-1])
    except ValueError: return None
    if not values or not all(type(v) is float for v in values): return None
    hundredths=[round(v*100) for v in values]
    if any(n/100!=v for n,v in zip(hundredths,values)): return None
    low=min(hundredths); steps=[n-low for n in hundredths]
    if max(steps)>255 or not -2**31<=low<2**31 or cut>0xFFFF: return None
    if _angular_json(payload[:cut],low,steps)!=payload: return None
    return PACKED_HEAD.pack(PACKED_V1,low,cut)+payload[:cut]+bytes(steps)

def unpack_angular(body: bytes) -> bytes:
    if len(body)<PACKED_HEAD.size: raise ValueError('PACKED_SHORT')
    version,low,cut=PACKED_HEAD.unpack_from(body)
    if version!=PACKED_V1 or len(body)<PACKED_HEAD.size+cut: raise ValueError('PACKED_BAD')
    start=PACKED_HEAD.size+cut
    return _angular_json(body[PACKED_HEAD.size:start],low,body[start:])

def split_message(kind,msg,topic,payload,*,retain=False,qos1=False,frame_max=200,packed=False):
    """Unsent frames of one message: (kind, msg, topic, flags, index, count, chunk). The sequence number
    is given at send time, so frames that are sent later (a heartbeat in between) keep the order."""
    body,zipped=compress(payload) if kind==DATA else (payload,False)
    flags=(F_RETAIN if retain else 0)|(F_QOS1 if qos1 else 0)|(F_ZLIB if zipped else 0)|(F_PACKED if packed else 0)
    # Worst-case COBS adds one byte per 254, plus the code byte and the 0x00 delimiter.
    room=frame_max-HEADER.size-CRC_LEN-2-(frame_max//254+1)
    if room<16: raise ValueError('FRAME_MAX_TOO_SMALL')
    chunks=[body[i:i+room] for i in range(0,len(body),room)] or [b'']
    if len(chunks)>255: raise ValueError('MESSAGE_TOO_LARGE')
    return [(kind,msg,topic,flags,index,len(chunks),chunk) for index,chunk in enumerate(chunks)]

def frame_bytes(seq,part):
    kind,msg,topic,flags,index,count,chunk=part
    raw=HEADER.pack(MAGIC,kind,seq&0xFFFF,msg,topic,flags,index,count)+chunk
    return cobs_encode(raw+struct.pack('!H',crc16(raw)))+b'\x00'

def encode_frames(kind,seq_start,msg,topic,payload,*,retain=False,qos1=False,frame_max=200):
    """Wire frames (COBS + 0x00) for one message; seq numbers follow seq_start."""
    return [frame_bytes(seq_start+i,part) for i,part in
            enumerate(split_message(kind,msg,topic,payload,retain=retain,qos1=qos1,frame_max=frame_max))]

def decode_frame(wire: bytes):
    """(kind, seq, msg, topic, flags, index, count, chunk) from one frame without its 0x00."""
    raw=cobs_decode(wire)
    if len(raw)<HEADER.size+CRC_LEN: raise ValueError('FRAME_SHORT')
    if struct.unpack('!H',raw[-2:])[0]!=crc16(raw[:-2]): raise ValueError('CRC_MISMATCH')
    magic,kind,seq,msg,topic,flags,index,count=HEADER.unpack_from(raw)
    if magic!=MAGIC or kind not in (DATA,HEARTBEAT,TIME) or not count or index>=count: raise ValueError('FRAME_BAD_HEADER')
    return kind,seq,msg,topic,flags,index,count,raw[HEADER.size:-2]

class Receiver:
    """Splits the byte stream into frames, checks them and reassembles messages."""
    def __init__(self):
        self.buf=bytearray(); self.parts={}; self.recent={}; self.last_seq=None
        self.stats=dict(frames=0,crc_errors=0,bad_frames=0,lost_frames=0,messages=0,duplicates=0,
                        incomplete=0,heartbeats=0,peer_restarts=0)
        self.peer=None; self.peer_clock=None; self.time_frames=[]; self.turn_at=None
    def feed(self,data,now=None):
        now=time.monotonic() if now is None else now
        out=[]; self.buf+=data
        while True:
            end=self.buf.find(0)
            if end<0:
                if len(self.buf)>4096: self.buf.clear(); self.stats['bad_frames']+=1
                break
            wire=bytes(self.buf[:end]); del self.buf[:end+1]
            if not wire: continue
            try: kind,seq,msg,topic,flags,index,count,chunk=decode_frame(wire)
            except ValueError as e:
                self.stats['crc_errors' if str(e)=='CRC_MISMATCH' else 'bad_frames']+=1; continue
            self.stats['frames']+=1
            if flags&F_TURN: self.turn_at=now
            if self.last_seq is not None:
                gap=(seq-self.last_seq-1)&0xFFFF
                if 0<gap<1000: self.stats['lost_frames']+=gap
            self.last_seq=seq
            if kind==TIME:
                # Arrival wall clock, as close to the radio as this process gets.
                self.time_frames.append((chunk,time.time_ns()//1000)); continue
            if kind==HEARTBEAT:
                self.stats['heartbeats']+=1
                if len(chunk)>=20:
                    up,rx,crc,lost,clock=struct.unpack('!IIHHQ',chunk[:20])
                    # The peer bridge restarted: its uptime went down, or stayed the same while its wall clock
                    # moved on a second or more (a run shorter than one heartbeat period sends 0 each time). Its
                    # message ids start again, so ids this side completed in the last DEDUP_S are new messages
                    # now, not copies.
                    if self.peer and (up<self.peer['uptime_s'] or
                                      (up==self.peer['uptime_s'] and clock-self.peer_clock>=1000)):
                        self.recent.clear(); self.parts.clear(); self.stats['peer_restarts']+=1
                    self.peer_clock=clock
                    # Peer wall clock minus ours at arrival (includes one-way latency, ~0.1 s).
                    self.peer=dict(uptime_s=up,rx_frames=rx,crc_errors=crc,lost_frames=lost,
                                   clock_offset_ms=clock-now_ms(),at=now)
                continue
            message=self._assemble(msg,topic,flags,index,count,chunk,now)
            if message: out.append(message)
        for key in [k for k,(t,_,_) in self.parts.items() if now-t>REASSEMBLY_S]:
            del self.parts[key]; self.stats['incomplete']+=1
        for key in [k for k,t in self.recent.items() if now-t>DEDUP_S]: del self.recent[key]
        return out
    def _assemble(self,msg,topic,flags,index,count,chunk,now):
        key=(msg,topic,count)
        if key in self.recent: self.stats['duplicates']+=1; return None
        started,meta,pieces=self.parts.get(key,(now,flags,{}))
        pieces[index]=chunk; self.parts[key]=(started,meta,pieces)
        if len(pieces)<count: return None
        del self.parts[key]; self.recent[key]=now
        body=b''.join(pieces[i] for i in range(count))
        try:
            payload=decompress(body) if flags&F_ZLIB else body
            if flags&F_PACKED: payload=unpack_angular(payload)
        except (ValueError,zlib.error,struct.error): self.stats['bad_frames']+=1; return None
        self.stats['messages']+=1
        return topic,payload,bool(flags&F_RETAIN),bool(flags&F_QOS1)

# The copy of a command or ACK leaves this long after the first, so one collision on the half-duplex
# radio cannot take both; a command's third send waits twice as long, so it lands in a later turn.
REPEAT_GAP_S=0.5

class Pending:
    __slots__=('topic','payload','retain','qos1','priority','created','repeat','msg','not_before','packed')
    def __init__(self,topic,payload,retain,qos1,priority,created,repeat,msg=None,not_before=0.0,packed=False):
        self.topic=topic; self.payload=payload; self.retain=retain; self.qos1=qos1; self.packed=packed
        self.priority=priority; self.created=created; self.repeat=repeat; self.msg=msg; self.not_before=not_before

class Sender:
    """Per-topic mailbox with priorities and ages, and a byte budget that releases one frame at a time.

    The radio module has a ~1 KB buffer and no flow control, so the bucket holds at most `burst` bytes:
    a multi-frame message (an Angular curve) leaves frame by frame at `rate` bytes/s, never in one go."""
    def __init__(self,rate,frame_max,min_interval=None,burst=400,turn_every_s=None,quiet_s=0.0):
        self.rate=rate; self.frame_max=frame_max; self.min_interval=dict(min_interval or {})
        self.turn_every_s=turn_every_s; self.quiet_s=quiet_s; self.last_turn=-1e9; self.next_at=0.0
        # When the radio is expected to finish what was written to it (frames queue in its buffer).
        self.air_until=0.0
        self.burst=max(burst,frame_max*2)
        # A random first message id: should the peer miss this side's first heartbeat after a restart (it
        # then still remembers the old ids), new messages rarely reuse an id it completed recently.
        self.lock=threading.Lock(); self.box={}; self.fifo=0; self.seq=0; self.msg=os.urandom(1)[0]
        self.wire=deque(); self.last_sent={}; self.tokens=float(self.burst); self.refilled=time.monotonic()
        self.stats=dict(queued=0,superseded=0,expired=0,sent_messages=0,sent_frames=0,sent_bytes=0,too_large=0)
    def offer(self,topic,payload,retain=False,qos1=False,now=None,packed=False):
        now=time.monotonic() if now is None else now
        priority,_,latest,twice=POLICY[topic]
        with self.lock:
            if latest:
                if topic in self.box: self.stats['superseded']+=1
                key=topic
            else:
                self.fifo+=1; key=(topic,self.fifo)
            sends=(COMMAND_SENDS if topic in GROUND_TOPICS else 2) if twice else 1
            self.box[key]=Pending(topic,payload,retain,qos1,priority,now,sends,packed=packed)
            self.stats['queued']+=1
    def _expire(self,now):
        for key,p in list(self.box.items()):
            age=POLICY[p.topic][1]
            if age is not None and now-p.created>age:
                del self.box[key]; self.stats['expired']+=1
    def _load(self,now):
        self._expire(now)
        due=[(p.priority,p.created,order,key) for order,(key,p) in enumerate(self.box.items())
             if now>=p.not_before and (p.msg is not None or
                 now-self.last_sent.get(p.topic,-1e9)>=self.min_interval.get(p.topic,0))]
        if not due: return
        p=self.box.pop(min(due,key=lambda d:d[:3])[3])
        if p.msg is None: self.msg=(self.msg+1)&0xFF; msg=self.msg
        else: msg=p.msg
        try: parts=split_message(DATA,msg,p.topic,p.payload,retain=p.retain,qos1=p.qos1,frame_max=self.frame_max,
                                 packed=p.packed)
        except ValueError: self.stats['too_large']+=1; return
        self.wire.extend(parts)
        if p.repeat>1:
            # The copy keeps the message id, so the receiver drops it when the first one arrived.
            self.fifo+=1
            gap=REPEAT_GAP_S if p.msg is None else 2*REPEAT_GAP_S
            self.box[('copy',self.fifo)]=Pending(p.topic,p.payload,p.retain,p.qos1,p.priority,p.created,
                                                 p.repeat-1,msg,now+gap,p.packed)
        if p.msg is None: self.last_sent[p.topic]=now; self.stats['sent_messages']+=1
    @staticmethod
    def _stamp(part):
        # TIME frames carry the wall clock of the moment they really leave, not when they were queued.
        kind,msg,topic,flags,index,count,chunk=part
        if kind==TIME and chunk[:1]==b'\x01': chunk=TIME_REQ.pack(1,time.time_ns()//1000)
        elif kind==TIME and chunk[:1]==b'\x02' and len(chunk)==TIME_RESP.size:
            _,t1,t2,_=TIME_RESP.unpack(chunk); chunk=TIME_RESP.pack(2,t1,t2,time.time_ns()//1000)
        elif kind==HEARTBEAT and len(chunk)>=20:
            # The peer derives clock_offset_ms from this wall clock, so it must be the send time too.
            chunk=chunk[:12]+struct.pack('!Q',now_ms())+chunk[20:]
        return kind,msg,topic,flags,index,count,chunk
    def _take(self,limit,now,max_bytes):
        out=[]; used=0
        while self.wire and now>=self.next_at:
            part=self._stamp(self.wire[0])
            turn=self.turn_every_s is not None and now-self.last_turn>=self.turn_every_s
            if turn: part=part[:3]+(part[3]|F_TURN,)+part[4:]
            frame=frame_bytes(self.seq,part)
            if (len(frame)>self.tokens or (limit is not None and len(out)>=limit) or
                    (max_bytes is not None and used+len(frame)>max_bytes)): break
            self.wire.popleft(); self.seq=(self.seq+1)&0xFFFF; self.tokens-=len(frame); out.append(frame); used+=len(frame)
            self.stats['sent_frames']+=1; self.stats['sent_bytes']+=len(frame)
            self.air_until=max(self.air_until,now)+len(frame)*AIR_S_PER_BYTE
            if turn:
                # Stay off the air until this frame (and whatever was queued before it) is out, then
                # leave the other side its turn.
                self.last_turn=now; self.next_at=self.air_until+self.quiet_s; break
        return out
    def next_frames(self,now=None,limit=None,max_bytes=None):
        """Frames the budget (and the turn taking) allows now, possibly part of a message, or []."""
        now=time.monotonic() if now is None else now
        with self.lock:
            self.tokens=min(float(self.burst),self.tokens+max(0.0,now-self.refilled)*self.rate); self.refilled=now
            if not self.wire: self._load(now)
            return self._take(limit,now,max_bytes)
    def urgent(self,kind,body):
        """One small control frame (TIME) ahead of everything queued."""
        with self.lock: self.wire.appendleft(split_message(kind,0,0,body,frame_max=self.frame_max)[0])
    def heartbeat(self,uptime_s,rx,now=None):
        """Heartbeat frame ahead of any queued fragment; it still waits for budget."""
        now=time.monotonic() if now is None else now
        body=struct.pack('!IIHHQ',int(uptime_s)&0xFFFFFFFF,rx['frames']&0xFFFFFFFF,
                         min(rx['crc_errors'],0xFFFF),min(rx['lost_frames'],0xFFFF),now_ms())
        with self.lock:
            self.wire.appendleft(split_message(HEARTBEAT,0,0,body,frame_max=self.frame_max)[0])
    def depth(self):
        with self.lock: return len(self.box)+len(self.wire)

def open_port(path,baud):
    if termios is None: raise OSError(0,'TERMIOS_UNAVAILABLE')
    fd=os.open(path,os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
    try:
        a=termios.tcgetattr(fd)
        a[0]=0; a[1]=0; a[2]=termios.CS8|termios.CREAD|termios.CLOCAL; a[3]=0
        a[4]=a[5]=getattr(termios,f'B{baud}'); a[6][termios.VMIN]=0; a[6][termios.VTIME]=0
        termios.tcsetattr(fd,termios.TCSANOW,a); termios.tcflush(fd,termios.TCIOFLUSH)
        try: fcntl.ioctl(fd,termios.TIOCEXCL)
        except OSError: pass
    except BaseException:
        os.close(fd); raise
    return fd

class Bridge:
    def __init__(self,*,role,device,baud,mqtt_cfg,node_id,credentials_file=None,demo=False,rate=1000,
                 frame_max=200,heartbeat_s=5.0,link_timeout_s=15.0,angular_every_s=1.5,detail_every_s=30.0,
                 status_every_s=5.0,time_every_s=16.0,chrony_sock=None,health_every_s=2.0):
        if role not in ('edge','ground'): raise ValueError('ROLE')
        self.role=role; self.device=device; self.baud=baud; self.node_id=node_id
        self.prefix=('sdr/demo/v2/' if demo else 'sdr/v2/')+node_id
        self.heartbeat_s=heartbeat_s; self.link_timeout_s=link_timeout_s; self.status_every_s=status_every_s
        intervals={4:angular_every_s,6:angular_every_s,2:detail_every_s,1:health_every_s} if role=='edge' else {}
        self.sender=(Sender(rate,frame_max,intervals,turn_every_s=EDGE_TURN_EVERY_S,quiet_s=EDGE_QUIET_S)
                     if role=='edge' else Sender(rate,frame_max,intervals))
        self.receiver=Receiver(); self.rx_event=threading.Event()
        self.started=time.monotonic(); self.last_rx=None; self.last_frame_rx=None; self.link_up=False; self.link_changes=0
        self.retained={}; self.stop_event=threading.Event(); self.fd=None; self.port_error=None
        self.mqtt_dropped=0; self.ignored=0; self.lock=threading.Lock(); self.threads=[]
        # Edge asks Ground for the time (NTP-style t1..t4); the best of the last samples feeds chrony.
        self.time_every_s=time_every_s; self.time_samples=deque(maxlen=8); self.time_requests=0
        self.chrony=ChronySock(chrony_sock) if role=='edge' and chrony_sock else None
        outgoing=EDGE_TOPICS if role=='edge' else GROUND_TOPICS
        subs=[(self.prefix+'/'+suffix,1,role=='ground') for suffix in outgoing.values()]
        self.client=Client(cfg=mqtt_cfg,client_id=f'{node_id}-lora-{role}',credentials_file=credentials_file,
                           subscriptions=subs,on_message=self._from_mqtt,rate=1000000)
    # ---- MQTT -> radio ----
    def _from_mqtt(self,topic,payload,retained):
        suffix=topic[len(self.prefix)+1:] if topic.startswith(self.prefix+'/') else None
        tid=TOPIC_IDS.get(suffix)
        allowed=EDGE_TOPICS if self.role=='edge' else GROUND_TOPICS
        if tid not in allowed: self.ignored+=1; return
        if self.role=='ground' and retained: self.ignored+=1; return  # never replay a retained command
        retain=self.role=='edge' and tid in (7,8,9)
        if retain:
            with self.lock: self.retained[tid]=payload
        body=pack_angular(payload) if tid in (4,6) else None
        self.sender.offer(tid,payload if body is None else body,retain=retain,
                          qos1=tid in (7,8,9,10,11,12) or tid>=64,packed=body is not None)
    # ---- radio -> MQTT ----
    def _deliver(self,tid,payload,retain,qos1):
        incoming=GROUND_TOPICS if self.role=='edge' else EDGE_TOPICS
        if tid not in incoming: self.ignored+=1; return
        key=f'lora:{tid}' if POLICY[tid][2] else f'lora:{tid}:{time.monotonic_ns()}'
        if not self.client.offer(key,self.prefix+'/'+incoming[tid],payload,qos=1 if qos1 else 0,
                                 retain=retain and self.role=='ground',priority=POLICY[tid][0]):
            self.mqtt_dropped+=1
    def _rx_loop(self):
        while not self.stop_event.is_set():
            fd=self.fd
            if fd is None: self.stop_event.wait(.2); continue
            try:
                r,_,_=select.select([fd],[],[],.2)
                if not r: continue
                data=os.read(fd,1024)
                if not data: raise OSError(0,'SERIAL_EOF')
            except (OSError,ValueError) as e:
                self._port_failed(e); continue
            now=time.monotonic(); before=self.receiver.stats['frames']
            messages=self.receiver.feed(data,now)
            if self.receiver.stats['frames']>before:
                self.last_frame_rx=now; self.rx_event.set()
            if self.receiver.stats['frames'] or messages:
                was=self.link_up; self.last_rx=now; self.link_up=True
                if not was:
                    self.link_changes+=1
                    # The other side may have missed every retained topic while the link was down.
                    if self.role=='edge': self._resend_retained()
            for m in messages: self._deliver(*m)
            while self.receiver.time_frames: self._on_time(*self.receiver.time_frames.pop(0))
    def _on_time(self,chunk,arrival_us):
        if self.role=='ground' and len(chunk)==TIME_REQ.size and chunk[0]==1:
            _,t1=TIME_REQ.unpack(chunk)
            self.sender.urgent(TIME,TIME_RESP.pack(2,t1,arrival_us,time.time_ns()//1000))
        elif self.role=='edge' and len(chunk)==TIME_RESP.size and chunk[0]==2:
            _,t1,t2,t3=TIME_RESP.unpack(chunk); t4=arrival_us
            delay=(t4-t1)-(t3-t2)
            if not 0<=delay<5_000_000: return  # a stale or mismatched reply
            offset=((t2-t1)+(t3-t4))/2
            with self.lock:
                self.time_samples.append((offset,delay,time.monotonic()))
                best=min(self.time_samples,key=lambda x:x[1])
            if self.chrony and best[1]<1_000_000: self.chrony.send(best[0]/1e6)
    def _time_view(self):
        with self.lock: samples=list(self.time_samples)
        if not samples: return None
        best=min(samples,key=lambda x:x[1]); last=samples[-1]
        view={'offset_ms':round(best[0]/1000,1),'delay_ms':round(best[1]/1000,1),'samples':len(samples),
              'last_age_s':round(time.monotonic()-last[2],1),'requests':self.time_requests}
        if self.chrony: view['chrony']={'sent':self.chrony.sent,'errors':self.chrony.errors,'error':self.chrony.error}
        return view
    def _resend_retained(self):
        with self.lock: saved=dict(self.retained)
        for tid in RETAINED_RESEND:
            if tid in saved: self.sender.offer(tid,saved[tid],retain=True,qos1=True)
    def _write(self,frames):
        for frame in frames:
            view=memoryview(frame)
            while view:
                if self.stop_event.is_set(): return
                _,w,_=select.select([],[self.fd],[],1.0)
                if not w: raise OSError(0,'SERIAL_WRITE_TIMEOUT')
                n=os.write(self.fd,view); view=view[n:]
    def _tx_loop(self):
        last_beat=0.0; last_status=0.0; last_time=-1e9; last_refresh=time.monotonic()
        while not self.stop_event.is_set():
            now=time.monotonic()
            if self.fd is None:
                try: self.fd=open_port(self.device,self.baud); self.port_error=None
                except OSError as e: self.port_error=f'ERRNO_{e.errno}' if e.errno else str(e) or 'OPEN_FAILED'; self.stop_event.wait(2); continue
            if self.link_up and self.last_rx is not None and now-self.last_rx>self.link_timeout_s:
                self.link_up=False; self.link_changes+=1
            try:
                if now-last_beat>=self.heartbeat_s:
                    last_beat=now; self.sender.heartbeat(now-self.started,self.receiver.stats,now)
                if self.role=='edge' and now-last_time>=self.time_every_s:
                    last_time=now; self.time_requests+=1
                    self.sender.urgent(TIME,TIME_REQ.pack(1,time.time_ns()//1000))
                if self.role=='ground':
                    # Talk only in the edge's quiet window, or freely when the edge has gone silent.
                    turn=self.receiver.turn_at
                    free=self.last_frame_rx is None or now-self.last_frame_rx>GROUND_FREE_AFTER_S
                    if free: frames=self.sender.next_frames(now)
                    elif turn is not None and now-turn<=GROUND_REPLY_WINDOW_S:
                        frames=self.sender.next_frames(now,max_bytes=GROUND_WINDOW_BYTES)
                        if frames: self.receiver.turn_at=None
                    else: frames=[]
                else: frames=self.sender.next_frames(now)
                if frames: self._write(frames)
            except (OSError,ValueError) as e:
                self._port_failed(e); continue
            if self.role=='edge' and now-last_refresh>=RETAINED_REFRESH_S:
                last_refresh=now
                if self.link_up: self._resend_retained()
            if now-last_status>=self.status_every_s and self.client.connected:
                last_status=now
                self.client.offer('lora:status',self.prefix+'/lora/'+self.role,json.dumps(self.status(),separators=(',',':')).encode(),retain=True)
            # Wake at once when a frame arrives (the ground's turn is short).
            self.rx_event.wait(.02 if frames else .05); self.rx_event.clear()
    def _port_failed(self,e):
        fd,self.fd=self.fd,None
        self.port_error=f'ERRNO_{e.errno}' if getattr(e,'errno',None) else (str(e) or type(e).__name__)
        if fd is not None:
            try: os.close(fd)
            except OSError: pass
        self.stop_event.wait(1)
    def status(self):
        now=time.monotonic(); peer=self.receiver.peer
        return {'v':1,'role':self.role,'t':now_ms(),'link':'UP' if self.link_up else 'DOWN',
                'last_rx_ms':None if self.last_rx is None else round((now-self.last_rx)*1000),
                'port':'OPEN' if self.fd is not None else 'ERROR','port_error':self.port_error,
                'mqtt':'CONNECTED' if self.client.connected else 'DISCONNECTED',
                'rx':dict(self.receiver.stats),'tx':dict(self.sender.stats),'queue':self.sender.depth(),
                'peer':None if not peer else {k:v for k,v in peer.items() if k!='at'},
                'mqtt_dropped':self.mqtt_dropped,'ignored':self.ignored,'link_changes':self.link_changes,
                'time':self._time_view() if self.role=='edge' else None}
    def start(self):
        self.client.start()
        for fn,name in ((self._rx_loop,'lora-rx'),(self._tx_loop,'lora-tx')):
            t=threading.Thread(target=fn,name=name,daemon=True); t.start(); self.threads.append(t)
    def stop(self):
        self.stop_event.set()
        for t in self.threads: t.join(3)
        self.client.stop()
        if self.chrony: self.chrony.close()
        if self.fd is not None:
            try: os.close(self.fd)
            except OSError: pass
            self.fd=None
