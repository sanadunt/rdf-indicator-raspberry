"""Small MQTT 5 TCP/WebSocket client with optional verified TLS for the bounded RDF profile.

Implemented: Clean Start, Session Expiry 0, QoS 0/1, subscriptions + SUBACK,
retained state, LWT, Message Expiry, PING, verified TLS, and MQTT binary frames
over WebSocket. Not implemented: QoS 2, persistent broker sessions,
topic aliases, enhanced authentication or a broker. Only one outbound publish
is in flight. No offline replay: each reconnect creates a fresh protocol session.
Plaintext transports do not encrypt credentials or MQTT payloads.
"""
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
import json
import random
import select
import socket
import ssl
import struct
import threading
import time
from pathlib import Path
from .util import compact, strict_json, stable_read
from .mqtt_ws import WebSocketReader,client_handshake,encode_client_frame,validate_websocket_path

MAX_PACKET = 16384

def vi(n:int) -> bytes:
    if type(n) is not int or not 0<=n<=268435455: raise ValueError('MQTT_VARINT_RANGE')
    b=bytearray()
    while True:
        v=n%128; n//=128
        b.append(v|(128 if n else 0))
        if not n: return bytes(b)

def read_vi(data, pos=0):
    value=0
    for i in range(4):
        if pos+i>=len(data): raise EOFError('INCOMPLETE_VARINT')
        b=data[pos+i]; value+=(b&127)*(128**i)
        if not b&128:
            if i and b==0: raise ValueError('NONCANONICAL_VARINT')
            return value,pos+i+1
    raise ValueError('BAD_VARINT')

def utf(s:str) -> bytes:
    if not isinstance(s,str) or '\x00' in s or any(0xd800<=ord(x)<=0xdfff for x in s):
        raise ValueError('BAD_MQTT_STRING')
    b=s.encode('utf-8')
    if len(b)>65535: raise ValueError('MQTT_STRING_TOO_LONG')
    return struct.pack('!H',len(b))+b

def take_utf(data,pos=0):
    if pos+2>len(data): raise ValueError('SHORT_STRING')
    n=int.from_bytes(data[pos:pos+2],'big'); pos+=2
    if pos+n>len(data): raise ValueError('SHORT_STRING')
    s=bytes(data[pos:pos+n]).decode('utf-8')
    if '\x00' in s: raise ValueError('BAD_STRING')
    return s,pos+n

def packet(header:int,body:bytes=b'') -> bytes:
    if len(body)>MAX_PACKET: raise ValueError('PACKET_TOO_LARGE')
    return bytes([header])+vi(len(body))+body

def properties(data,pos):
    """Parse standard property types, with bounds. Reject unknown properties."""
    n,start=read_vi(data,pos); end=start+n
    if end>len(data): raise ValueError('SHORT_PROPERTIES')
    vals={}; p=start
    kinds={1:1,2:4,3:'s',8:'s',9:'b',11:'v',17:4,18:'s',19:2,21:'s',22:'b',23:1,24:4,
           25:1,26:'s',28:'s',31:'s',33:2,34:2,35:2,36:1,37:1,38:'ss',39:4,40:1,41:1,42:1}
    while p<end:
        key,p=read_vi(data,p)
        kind=kinds.get(key)
        if kind is None: raise ValueError('UNKNOWN_MQTT_PROPERTY')
        if type(kind) is int:
            if p+kind>end: raise ValueError('SHORT_PROPERTY')
            val=int.from_bytes(data[p:p+kind],'big'); p+=kind
        elif kind=='v': val,p=read_vi(data,p)
        elif kind in ('s','ss'):
            val,p=take_utf(data,p)
            if kind=='ss':
                v2,p=take_utf(data,p); val=(val,v2)
        else:
            if p+2>end: raise ValueError('SHORT_BINARY')
            length=int.from_bytes(data[p:p+2],'big'); p+=2
            val=bytes(data[p:p+length]); p+=length
        if p>end: raise ValueError('PROPERTY_LENGTH')
        if key in vals and key not in (11,38): raise ValueError('DUPLICATE_PROPERTY')
        vals[key]=val
    return vals,end

def connect_packet(client_id,username=None,password=None,keepalive=15,will=None):
    flags=2
    payload=utf(client_id)
    if will:
        topic,data=will; flags|=4|8|32
        payload+=b'\x00'+utf(topic)+struct.pack('!H',len(data))+data
    if username is not None:
        flags|=128; payload+=utf(username)
    if password is not None:
        flags|=64; payload+=utf(password)
    props=b'\x11\x00\x00\x00\x00'+b'\x21\x00\x10'+b'\x27'+struct.pack('!I',MAX_PACKET)
    return packet(0x10,utf('MQTT')+bytes([5,flags])+struct.pack('!H',keepalive)+vi(len(props))+props+payload)

def publish_packet(topic,payload,mid=0,qos=0,retain=False,expiry=None):
    if qos not in (0,1) or '+' in topic or '#' in topic or not topic:
        raise ValueError('INVALID_PUBLISH')
    props=b'' if expiry is None else b'\x02'+struct.pack('!I',max(1,int(expiry)))
    return packet(0x30|(qos<<1)|int(retain),utf(topic)+(struct.pack('!H',mid) if qos else b'')+vi(len(props))+props+payload)

class Reader:
    def __init__(self): self.buf=bytearray()
    def feed(self,data):
        self.buf.extend(data)
        if len(self.buf)>MAX_PACKET*3: raise ValueError('INPUT_BUFFER_LIMIT')
        result=[]
        while self.buf:
            if len(self.buf)<2: break
            try: n,pos=read_vi(self.buf,1)
            except EOFError: break
            if n>MAX_PACKET: raise ValueError('INCOMING_PACKET_LIMIT')
            if len(self.buf)<pos+n: break
            result.append((self.buf[0],bytes(self.buf[pos:pos+n])))
            del self.buf[:pos+n]
        return result

@dataclass
class Message:
    key:str
    topic:str
    payload:bytes
    qos:int
    retain:bool
    expiry:float | None
    priority:int
    offered:float
    on_sent:object=None
    on_error:object=None

def _notify_error(callback,reason):
    if callback:
        try: callback(reason)
        except Exception: pass


class Outbox:
    def __init__(self,max_messages=16,max_bytes=16384):
        self.lock=threading.RLock(); self.data=OrderedDict(); self.max_messages=max_messages; self.max_bytes=max_bytes
        self.superseded=0; self.rejected=0; self.expired=0
    def offer(self,m):
        reason=None
        with self.lock:
            size=sum(len(x.payload) for k,x in self.data.items() if k!=m.key)+len(m.payload)
            if m.key not in self.data and len(self.data)>=self.max_messages:
                self.rejected+=1; reason='OUTBOX_FULL'
            elif size>self.max_bytes:
                self.rejected+=1; reason='OUTBOX_BYTE_LIMIT'
            else:
                if m.key in self.data: self.superseded+=1
                self.data[m.key]=m
        if reason: _notify_error(m.on_error,reason)
        return reason is None
    def pop(self,now=None):
        now=time.monotonic() if now is None else now
        expired=None
        with self.lock:
            for k,m in list(self.data.items()):
                if m.expiry is not None and now-m.offered>m.expiry:
                    del self.data[k]; self.expired+=1
                    if expired is None: expired=[]
                    expired.append(m)
            if not self.data: selected=None
            else:
                key=min(self.data,key=lambda k:(self.data[k].priority,self.data[k].offered))
                selected=self.data.pop(key)
        if expired:
            for m in expired: _notify_error(m.on_error,'OUTBOX_EXPIRED')
        return selected
    def clear(self,reason='OUTBOX_CLEARED'):
        with self.lock:
            messages=list(self.data.values()) if self.data else None
            self.data.clear()
        if messages:
            for m in messages: _notify_error(m.on_error,reason)
    def discard(self,key,reason='OUTBOX_DISCARDED'):
        with self.lock: message=self.data.pop(key,None)
        if message: _notify_error(message.on_error,reason); return True
        return False
    def status(self):
        with self.lock:
            return dict(depth=len(self.data),bytes=sum(len(m.payload) for m in self.data.values()),
                        superseded=self.superseded,rejected=self.rejected,expired=self.expired)

class Bucket:
    def __init__(self,rate,capacity):
        self.rate=rate; self.capacity=capacity; self.tokens=capacity; self.ts=time.monotonic()
    def ready(self,cost):
        n=time.monotonic(); self.tokens=min(self.capacity,self.tokens+(n-self.ts)*self.rate); self.ts=n
        # A bounded bootstrap document may be larger than a regular burst. Its debt
        # is fully repaid before sending another application document.
        if self.tokens>=min(cost,self.capacity):
            self.tokens-=cost; return True
        return False

class Client:
    def __init__(self, *, cfg, client_id, credentials_file=None, subscriptions=(),will=None,
                 on_message=None, rate=850, bulk=False):
        self.cfg=cfg; self.client_id=client_id; self.credentials_file=credentials_file
        self.subscriptions=list(subscriptions); self.will=will; self.on_message=on_message
        self.outbox=Outbox(4 if bulk else 16); self.bulk=bulk
        self.bucket=Bucket(rate,650 if bulk else 1400)
        self.stop_event=threading.Event(); self.reset_event=threading.Event(); self.thread=None
        self.connected=False; self.ready=False; self.generation=0; self.error=None
        self.last_success=None; self.tx_bytes=0; self.rx_bytes=0; self.reconnects=0; self.pending_age_ms=0
        self.mid=0; self._publish=None; self._pub_result=None; self._socket=None
    def start(self):
        if self.thread and self.thread.is_alive(): return
        self.thread=threading.Thread(target=self._run,name=self.client_id,daemon=True); self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(7)
    def reset(self): self.reset_event.set()
    def offer(self,key,topic,payload,*,qos=0,retain=False,expiry=None,priority=2,on_sent=None,on_error=None):
        if not self.connected:
            _notify_error(on_error,'MQTT_DISCONNECTED'); return False
        if not isinstance(payload,bytes):
            try: payload=compact(payload)
            except Exception:
                _notify_error(on_error,'PUBLISH_PAYLOAD_ENCODE_FAILED'); raise
        if len(payload)>8192:
            _notify_error(on_error,'PUBLISH_PAYLOAD_LIMIT'); return False
        return self.outbox.offer(Message(key,topic,payload,qos,retain,expiry,priority,time.monotonic(),on_sent,on_error))
    def status(self):
        return dict(state='CONNECTED' if self.connected else 'ERROR' if self.error else 'CONNECTING',
                    ready=self.ready,error=self.error,generation=self.generation,tx_bytes=self.tx_bytes,
                    rx_bytes=self.rx_bytes,reconnects=self.reconnects,pending_age_ms=self.pending_age_ms,**self.outbox.status())
    def _next_id(self):
        self.mid=(self.mid%65535)+1; return self.mid
    def _credentials(self):
        if self.credentials_file:
            j=strict_json(stable_read(Path(self.credentials_file),4096))
            if set(j)!= {'username','password'} or not all(isinstance(x,str) and x for x in j.values()):
                raise ValueError('CREDENTIAL_FORMAT')
            return j['username'],j['password']
        return None,None
    def _run(self):
        delay=1
        while not self.stop_event.is_set():
            self.reset_event.clear()
            try:
                self._session()
                self.error=None if self.stop_event.is_set() else 'CONNECTION_CLOSED'
            except ssl.SSLCertVerificationError:
                self.error='TLS_CERTIFICATE_VERIFY_FAILED'
            except ssl.SSLError:
                self.error='TLS_ERROR'
            except Exception as e:
                # Never log broker payloads, usernames, passwords or full exception strings.
                self.error=str(e) if isinstance(e,ValueError) and str(e).isupper() else type(e).__name__.upper()
            finally:
                self.connected=False; self.ready=False; self.pending_age_ms=0
                failure=self.error or ('MQTT_STOPPED' if self.stop_event.is_set() else 'MQTT_CONNECTION_RESET')
                if failure=='PUBLISH_REJECTED': failure='MQTT_CONNECTION_RESET'
                if self._publish:
                    message=self._publish[0]
                    _notify_error(message.on_error,self.error or failure)
                self.outbox.clear(failure); self._publish=None; self._pub_result=None
                if self._socket:
                    try: self._socket.close()
                    except OSError: pass
                    self._socket=None
            if self.stop_event.is_set(): break
            self.reconnects+=1
            if self.last_success is not None and time.monotonic()-self.last_success>20:
                delay=max(delay,2)
            self.stop_event.wait(delay+random.uniform(0,0.3))
            delay=min(self.cfg.get('reconnect_max',30),delay*2)
            if self.last_success is not None and time.monotonic()-self.last_success<20:
                delay=1
    def _session(self):
        transport=self.cfg.get('transport','tcp')
        if transport not in ('tcp','websocket'):raise ValueError('UNSUPPORTED_MQTT_TRANSPORT')
        websocket=transport=='websocket'
        path=validate_websocket_path(self.cfg.get('websocket_path','/mqtt')) if websocket else None
        u,p=self._credentials()
        s=socket.create_connection((self.cfg['host'],self.cfg['port']),timeout=5)
        self._socket=s
        s.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        if self.cfg.get('tls',True):
            ctx=ssl.create_default_context(cafile=self.cfg.get('ca_file'))
            ctx.minimum_version=ssl.TLSVersion.TLSv1_2
            s=ctx.wrap_socket(s,server_hostname=self.cfg['host']); self._socket=s
        s.settimeout(5)
        ws_reader=WebSocketReader() if websocket else None
        early=client_handshake(s,self.cfg['host'],self.cfg['port'],path) if websocket else b''
        if early:
            for kind,payload in ws_reader.feed(early):
                if kind=='ping':
                    response=encode_client_frame(payload,10);s.sendall(response);self.tx_bytes+=len(response)
                elif kind=='close':
                    response=encode_client_frame(payload,8);s.sendall(response);self.tx_bytes+=len(response)
                    raise ValueError('WEBSOCKET_CLOSED')
                elif kind=='data':raise ValueError('WEBSOCKET_EARLY_MQTT_DATA')
        first=connect_packet(self.client_id,u,p,self.cfg.get('keepalive',15),self.will)
        wire=encode_client_frame(first) if websocket else first
        s.sendall(wire); self.tx_bytes+=len(wire); s.setblocking(False)
        reader=Reader(); self._pub_result=None; self._publish=None
        pending_sub=set(); writes=[]; current=None; sent_start=None
        last_mqtt_tx=time.monotonic(); last_rx=last_mqtt_tx; handshake_deadline=last_mqtt_tx+10; ping_at=None
        self._server_max_packet=MAX_PACKET; keepalive=self.cfg.get('keepalive',15)
        websocket_closing=False;websocket_close_sent=False;websocket_close_received=False;websocket_close_deadline=None
        def queue_wire(data):
            if websocket and websocket_closing:return
            if len(writes)>20: raise ValueError('MQTT_CONTROL_QUEUE_LIMIT')
            writes.append([data,0,None,False,True])
        def queue_ws_control(opcode,payload):
            nonlocal websocket_close_sent,websocket_close_deadline
            if opcode==10 and websocket_closing:return
            if opcode==8:
                if websocket_close_sent:return
                websocket_close_sent=True;websocket_close_deadline=time.monotonic()+0.5
            if len(writes)>20: raise ValueError('MQTT_CONTROL_QUEUE_LIMIT')
            writes.insert(0,[encode_client_frame(payload,opcode),0,None,True,False])
        while not self.stop_event.is_set() and not self.reset_event.is_set():
            now=time.monotonic()
            if websocket_close_received and current is None and not writes: raise ValueError('WEBSOCKET_CLOSED')
            if websocket_close_sent and not websocket_close_received and now>websocket_close_deadline:
                raise ValueError('WEBSOCKET_CLOSE_TIMEOUT')
            if not self.connected and now>handshake_deadline: raise ValueError('CONNACK_TIMEOUT')
            if self.connected and pending_sub and now>handshake_deadline: raise ValueError('SUBACK_TIMEOUT')
            if keepalive>0 and ping_at and now-ping_at>keepalive: raise ValueError('PING_TIMEOUT')
            if not websocket_closing and self.connected and keepalive>0 and now-last_mqtt_tx>=keepalive and ping_at is None:
                queue_wire(b'\xc0\x00'); ping_at=now
            if current is None and writes:
                current=writes.pop(0)
                if websocket and not current[3]:current[0]=encode_client_frame(current[0]);current[3]=True
            if self._publish:
                m,mid,started=self._publish
                self.pending_age_ms=int((now-started)*1000)
                if self.pending_age_ms>10000: raise ValueError('PUBLISH_PROGRESS_TIMEOUT')
                if self._pub_result is not None:
                    reason=self._pub_result
                    self._publish=None; self._pub_result=None; self.pending_age_ms=0
                    if reason>=0x80:
                        self.error='PUBLISH_REJECTED'
                        _notify_error(m.on_error,f'PUBLISH_REJECTED_0X{reason:02X}')
                        raise ValueError('PUBLISH_REJECTED')
                    if m.on_sent:
                        try: m.on_sent()
                        except Exception: pass
            if self.ready and current is None and not writes and self._publish is None:
                m=self.outbox.pop()
                if m:
                    ttl=None if m.expiry is None else max(1,int(m.expiry-(now-m.offered)))
                    mid=self._next_id() if m.qos else 0
                    self._publish=(m,mid,now)
                    data=publish_packet(m.topic,m.payload,mid,m.qos,m.retain,ttl)
                    if len(data)>self._server_max_packet: raise ValueError('SERVER_PACKET_LIMIT')
                    cost=len(data)+142+(144 if m.qos else 0)
                    if self.bucket.ready(cost):
                        current=[encode_client_frame(data) if websocket else data,0,m,True,True]
                    else:
                        self._publish=None
                        # Do not overwrite a newer value offered during the pop.
                        with self.outbox.lock:
                            if m.key not in self.outbox.data: self.outbox.data[m.key]=m
            want_write=current is not None
            try:
                readable,writable,_=select.select([s],[s] if want_write else [],[],0.02)
            except (ValueError,OSError): break
            if isinstance(s,ssl.SSLSocket) and s.pending(): readable=[s]
            if writable and current:
                try:
                    n=s.send(current[0][current[1]:]); current[1]+=n; self.tx_bytes+=n
                    if current[4]:last_mqtt_tx=time.monotonic()
                    if not n: raise OSError('socket closed')
                    if current[1]==len(current[0]):
                        if current[2] is not None and current[2].qos==0: self._pub_result=0
                        current=None
                except (ssl.SSLWantWriteError,ssl.SSLWantReadError,BlockingIOError): pass
            if readable:
                try: chunk=s.recv(8192)
                except (ssl.SSLWantReadError,ssl.SSLWantWriteError,BlockingIOError): continue
                if not chunk: raise OSError('socket closed')
                self.rx_bytes+=len(chunk); last_rx=time.monotonic()
                packets=[]
                if websocket:
                    for event,payload in ws_reader.feed(chunk):
                        if event=='data':
                            for offset in range(0,len(payload),8192):
                                packets.extend(reader.feed(payload[offset:offset+8192]))
                        elif event=='ping':queue_ws_control(10,payload)
                        elif event=='close':
                            websocket_closing=True;websocket_close_received=True
                            if current is not None and current[1]==0:
                                if current[2] is not None:self._publish=None;self._pub_result=None
                                current=None
                            writes.clear();queue_ws_control(8,payload)
                else:packets=reader.feed(chunk)
                for header,body in packets:
                    kind=header>>4
                    if kind==2:
                        if self.connected or header!=0x20 or len(body)<3: raise ValueError('BAD_CONNACK')
                        if body[0]!=0: raise ValueError('UNEXPECTED_SESSION_PRESENT')
                        if body[1]!=0: raise ValueError('CONNACK_REJECTED')
                        props,end=properties(body,2)
                        if end!=len(body) or props.get(36,1)<1 or props.get(37,1)!=1: raise ValueError('BROKER_CAPABILITY_UNSUPPORTED')
                        self._server_max_packet=props.get(39,MAX_PACKET)
                        keepalive=props.get(19,keepalive)
                        self.connected=True; self.error=None; self.generation+=1; self.last_success=time.monotonic()
                        handshake_deadline=time.monotonic()+10
                        for topic,qos,no_retained in self.subscriptions:
                            mid=self._next_id(); pending_sub.add(mid)
                            options=qos|8|(32 if no_retained else 0)
                            queue_wire(packet(0x82,struct.pack('!H',mid)+b'\x00'+utf(topic)+bytes([options])))
                        self.ready=not pending_sub
                    elif kind==9:
                        if header!=0x90 or len(body)<4: raise ValueError('BAD_SUBACK')
                        mid=int.from_bytes(body[:2],'big'); props,end=properties(body,2)
                        if mid not in pending_sub or not body[end:] or any(x>1 for x in body[end:]): raise ValueError('SUBACK_REJECTED')
                        pending_sub.remove(mid); self.ready=not pending_sub
                    elif kind==3:
                        qos=(header>>1)&3
                        if not self.connected or qos not in (0,1): raise ValueError('UNSUPPORTED_INCOMING_QOS')
                        topic,pos=take_utf(body,0)
                        if not topic or '+' in topic or '#' in topic: raise ValueError('BAD_INCOMING_TOPIC')
                        mid=None
                        if qos:
                            if pos+2>len(body): raise ValueError('SHORT_PUBLISH')
                            mid=int.from_bytes(body[pos:pos+2],'big'); pos+=2
                            if not mid: raise ValueError('ZERO_PACKET_ID')
                        props,pos=properties(body,pos)
                        if 35 in props: raise ValueError('TOPIC_ALIAS_NOT_NEGOTIATED')
                        payload=body[pos:]
                        if self.on_message:
                            self.on_message(topic,payload,bool(header&1))
                        if qos: queue_wire(packet(0x40,struct.pack('!H',mid)))
                    elif kind==4:
                        if header!=0x40 or len(body)<2: raise ValueError('BAD_PUBACK')
                        mid=int.from_bytes(body[:2],'big')
                        if self._publish and mid==self._publish[1]:
                            reason=body[2] if len(body)>2 else 0
                            if len(body)>3:
                                _,end=properties(body,3)
                                if end!=len(body): raise ValueError('BAD_PUBACK_PROPERTIES')
                            self._pub_result=reason
                    elif kind==13:
                        if header!=0xd0 or body: raise ValueError('BAD_PINGRESP')
                        ping_at=None
                    elif kind==14:
                        if websocket:
                            websocket_closing=True
                            if current is not None and current[1]==0:
                                if current[2] is not None:self._publish=None;self._pub_result=None
                                current=None
                            writes.clear();queue_ws_control(8,b'')
                            break
                        raise ValueError('BROKER_DISCONNECT')
                    else:
                        raise ValueError('UNSUPPORTED_MQTT_PACKET')
        can_close= current is None or current[1]==0
        if self.stop_event.is_set() and can_close:
            try:
                disconnect=encode_client_frame(b'\xe0\x00') if websocket else b'\xe0\x00'
                s.settimeout(0.5);s.sendall(disconnect)
            except OSError: pass
        if websocket and can_close:
            try:
                s.settimeout(0.5);s.sendall(encode_client_frame(b'',8))
                close_reader=WebSocketReader();deadline=time.monotonic()+0.5
                while time.monotonic()<deadline:
                    chunk=s.recv(8192)
                    if not chunk:break
                    events=close_reader.feed(chunk)
                    for kind,payload in events:
                        if kind=='ping':s.sendall(encode_client_frame(payload,10))
                    if any(kind=='close' for kind,_ in events):break
            except (OSError,ssl.SSLError,ValueError): pass
