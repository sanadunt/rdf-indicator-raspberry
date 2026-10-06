"""RDF2 binary contract from design, 48-byte frame + 12-byte chunk envelope.
No pickle, compression, Base64, second logarithm, absolute values or clipping.
"""
from __future__ import annotations
from collections import OrderedDict
import math
import struct
import time
from .util import finite, integer

HEADER = struct.Struct('<4sBBHIIQIIBBHffHh')
CHUNK = struct.Struct('<IIBBH')
COUNT = 360
FLAG_PARSED=1
FLAG_FRESH=2
FLAG_DAQ=4
FLAG_CONVENTION=8
FLAG_CONFIG=16
UNKNOWN_REV = 0xffffffff

def encode(values, *, sid, seq, timestamp_ms, frequency_hz, revision=UNKNOWN_REV,
           vfo=0, convention=1, flags=31, raw_doa=None, confidence=None, encoding='q16'):
    if len(values) != COUNT:
        raise ValueError('EXPECTED_360_SAMPLES')
    vals = [finite(v,-1e8,1e8) for v in values]
    sid=integer(sid,0,0xffffffff); seq=integer(seq,0,0xffffffff)
    t=integer(timestamp_ms,1,0x7fffffffffffffff); f=integer(frequency_hz,1,0xffffffff)
    rev=UNKNOWN_REV if revision is None else integer(revision,0,UNKNOWN_REV)
    vf=integer(vfo,0,15); conv=integer(convention,0,255); flag=integer(flags,0,31)
    a=65535 if raw_doa is None else int(round((finite(raw_doa,0,360)%360)*100))%36000
    c=-32768 if confidence is None else int(round(finite(confidence,-327.67,327.67)*100))
    if encoding == 'q16':
        scale, offset, enc = 0.01, 0.0, 1
        q=[round(v/scale) for v in vals]
        if any(v < -32767 or v > 32767 for v in q):
            raise ValueError('Q16_RANGE_NO_CLIPPING')
        body=struct.pack('<360h',*q)
    elif encoding == 'u8':
        offset=min(vals); scale=(max(vals)-offset)/255; enc=2
        q=[0 if scale == 0 else round((v-offset)/scale) for v in vals]
        body=bytes(q)
    else:
        raise ValueError('UNSUPPORTED_ENCODING')
    return HEADER.pack(b'RDF2',2,enc,flag,sid,seq,t,f,rev,vf,conv,COUNT,scale,offset,a,c)+body

def decode(frame: bytes) -> dict:
    if len(frame) < HEADER.size:
        raise ValueError('SHORT_FRAME')
    magic,v,enc,flags,sid,q,t,f,rev,vfo,conv,n,scale,offset,a,c=HEADER.unpack_from(frame)
    if magic != b'RDF2' or v != 2 or flags & ~31 or n != 360 or vfo > 15 or conv not in (0,1):
        raise ValueError('INVALID_HEADER')
    if a != 65535 and not 0 <= a < 36000:
        raise ValueError('INVALID_ANGLE')
    if not t or not f or not math.isfinite(scale) or not math.isfinite(offset) or scale < 0:
        raise ValueError('INVALID_FRAME_METADATA')
    if enc == 1:
        if len(frame) != 768 or not math.isclose(scale,0.01,rel_tol=1e-6) or offset != 0:
            raise ValueError('INVALID_Q16_FRAME')
        samples=struct.unpack_from('<360h',frame,48)
        if -32768 in samples:
            raise ValueError('INVALID_Q16_SAMPLE')
    elif enc == 2:
        if len(frame)!=408:
            raise ValueError('INVALID_U8_FRAME')
        samples=frame[48:]
    else:
        raise ValueError('UNSUPPORTED_ENCODING')
    values=[offset+scale*x for x in samples]
    if any(not math.isfinite(x) or abs(x)>1e8 for x in values):
        raise ValueError('BAD_DECODED_VALUE')
    return dict(version=2,encoding='q16' if enc==1 else 'u8',flags=flags,sid=f'{sid:08x}',q=q,
                timestamp_ms=t,frequency_hz=f,revision=None if rev==UNKNOWN_REV else rev,
                vfo=vfo,convention=conv,values=values,raw_doa_deg=None if a==65535 else a/100,
                confidence_native_db=None if c==-32768 else c/100,
                peak_index=max(range(360),key=values.__getitem__))

def split(frame: bytes) -> list[bytes]:
    d=decode(frame)
    width=384 if len(frame)==768 else 408
    parts=[frame[i:i+width] for i in range(0,len(frame),width)]
    return [CHUNK.pack(int(d['sid'],16),d['q'],i,len(parts),len(frame))+p for i,p in enumerate(parts)]

class Assembler:
    """Bounded and deadline-aware. Reset on session change; never render fragments."""
    def __init__(self, timeout=3.0):
        self.timeout=timeout
        self.frames=OrderedDict()
    def clear(self):
        self.frames.clear()
    def add(self,payload:bytes,now=None):
        now=time.monotonic() if now is None else now
        for key,v in list(self.frames.items()):
            if now-v['start']>self.timeout:
                del self.frames[key]
        if len(payload)<12:
            raise ValueError('SHORT_CHUNK')
        sid,q,idx,count,total=CHUNK.unpack_from(payload)
        if (total,count) not in ((768,2),(408,1)) or idx>=count:
            raise ValueError('INVALID_CHUNK_HEADER')
        expected=384 if count==2 else 408
        if len(payload)!=12+expected:
            raise ValueError('INVALID_CHUNK_SIZE')
        key=(sid,q)
        if key not in self.frames:
            if len(self.frames)>=2:
                self.frames.popitem(last=False)
            self.frames[key]=dict(start=now,count=count,total=total,parts={})
        r=self.frames[key]
        if (r['count'],r['total'])!=(count,total):
            del self.frames[key]
            raise ValueError('CHUNK_METADATA_CONFLICT')
        part=payload[12:]
        if idx in r['parts'] and part!=r['parts'][idx]:
            del self.frames[key]
            raise ValueError('CHUNK_DUPLICATE_CONFLICT')
        r['parts'][idx]=part
        if len(r['parts'])<count:
            return None
        frame=b''.join(r['parts'][i] for i in range(count))
        del self.frames[key]
        result=decode(frame)
        if result['sid']!=f'{sid:08x}' or result['q']!=q:
            raise ValueError('INNER_OUTER_MISMATCH')
        return result
