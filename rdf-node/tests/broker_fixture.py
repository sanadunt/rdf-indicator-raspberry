"""Independent minimal MQTT5 broker fixture, ONLY for tests on loopback.
Not shipped as a deployment broker. No imports from rdf_node.mqtt.
"""
import base64,hashlib,select,socket,ssl,threading,time,struct

def enc_len(n):
    out=[]
    while n>127: out.append((n&127)|128); n>>=7
    return bytes(out+[n])
def pkt(h,b=b''): return bytes([h])+enc_len(len(b))+b
def text(s):
    b=s.encode();return len(b).to_bytes(2,'big')+b
def vint(b,p):
    n=0;shift=0
    while True:
        v=b[p];p+=1;n|=(v&127)<<shift
        if v<128:return n,p
        shift+=7
        if shift>21:raise ValueError('bad vint')
def string(b,p):
    n=int.from_bytes(b[p:p+2],'big');p+=2
    s=b[p:p+n].decode();return s,p+n
def props(b,p):
    n,p=vint(b,p);return p+n

def matches(pattern,topic):
    pp=pattern.split('/');tt=topic.split('/')
    for i,x in enumerate(pp):
        if x=='#':return i==len(pp)-1
        if i>=len(tt) or (x!='+' and x!=tt[i]):return False
    return len(pp)==len(tt)

def server_frame(payload,opcode=2):
    length=len(payload)
    if length<126: header=bytes([0x80|opcode,length])
    elif length<65536: header=bytes([0x80|opcode,126])+length.to_bytes(2,'big')
    else: header=bytes([0x80|opcode,127])+length.to_bytes(8,'big')
    return header+payload

class WebSocketSocket:
    def __init__(self,sock,events):
        self.sock=sock;self.buf=bytearray();self.data=bytearray();self.closed=False;self.events=events
    @classmethod
    def upgrade(cls,sock,path,events,early_close=False):
        request=bytearray()
        while b'\r\n\r\n' not in request:
            part=sock.recv(4096)
            if not part or len(request)+len(part)>8192: raise ValueError('bad websocket request')
            request.extend(part)
        head,extra=request.split(b'\r\n\r\n',1)
        lines=head.decode('ascii').split('\r\n')
        headers={line.split(':',1)[0].lower():line.split(':',1)[1].strip() for line in lines[1:]}
        if lines[0]!=f'GET {path} HTTP/1.1' or headers.get('sec-websocket-protocol')!='mqtt':
            raise ValueError('bad websocket upgrade')
        accept=base64.b64encode(hashlib.sha1((headers['sec-websocket-key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        response=('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                  f'Sec-WebSocket-Accept: {accept}\r\nSec-WebSocket-Protocol: mqtt\r\n\r\n').encode()
        if early_close:response+=server_frame(b'',8)
        sock.sendall(response)
        if extra: raise ValueError('unexpected websocket data')
        return cls(sock,events)
    def __getattr__(self,name): return getattr(self.sock,name)
    def sendall(self,data): self.sock.sendall(server_frame(data))
    def send_control(self,opcode,payload=b''): self.sock.sendall(server_frame(payload,opcode))
    def send_frames(self,frames): self.sock.sendall(b''.join(frames))
    def recv(self,size):
        while not self.data:
            if self.closed: return b''
            raw=self.sock.recv(8192)
            if not raw: return b''
            self.buf.extend(raw)
            while len(self.buf)>=2:
                first,second=self.buf[0],self.buf[1];marker=second&127;pos=2
                if not second&128: raise ValueError('unmasked client websocket frame')
                if marker==126:
                    if len(self.buf)<4: break
                    length=int.from_bytes(self.buf[2:4],'big');pos=4
                elif marker==127:
                    if len(self.buf)<10: break
                    length=int.from_bytes(self.buf[2:10],'big');pos=10
                else:length=marker
                if length>65536: raise ValueError('client websocket frame too large')
                if len(self.buf)<pos+4+length: break
                mask=bytes(self.buf[pos:pos+4]);pos+=4
                payload=bytes(self.buf[pos+i]^mask[i&3] for i in range(length))
                del self.buf[:pos+length]
                opcode=first&15
                if first&0x70: raise ValueError('websocket extensions unsupported')
                self.events.append((opcode,payload))
                if opcode==2 and first&0x80:self.data.extend(payload)
                elif opcode==10:pass
                elif opcode==8:
                    self.sock.sendall(server_frame(payload,8));self.events.append(('server-close',payload));self.closed=True
                    if self.buf:self.events.append(('after-close',bytes(self.buf)))
                    elif select.select([self.sock],[],[],.05)[0]:
                        trailing=self.sock.recv(8192)
                        if trailing:self.events.append(('after-close',trailing))
                    break
                else:raise ValueError('unexpected client websocket opcode')
        out=bytes(self.data[:size]);del self.data[:size];return out

    

class Broker:
    def __init__(self,tls_context=None,deny_sub=None,auth=None,websocket=False,websocket_path='/mqtt',websocket_early_close=False,puback_reason=0):
        self.listener=socket.socket();self.listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        self.listener.bind(('127.0.0.1',0));self.listener.listen(20);self.listener.settimeout(.2)
        self.port=self.listener.getsockname()[1];self.tls_context=tls_context
        self.deny_sub=deny_sub;self.auth=auth or {'test':'test-password'}
        self.puback_reason=puback_reason
        self.websocket=websocket;self.websocket_path=websocket_path;self.websocket_early_close=websocket_early_close
        self.lock=threading.RLock();self.clients=[];self.connections=[];self.retained={};self.messages=[];self.errors=[]
        self.mqtt_connect_count=0;self.mqtt_pingreq_count=0
        self.stop_event=threading.Event();self.thread=threading.Thread(target=self._accept,daemon=True);self.thread.start()
    def send_control(self,c,opcode,payload=b''):
        with c['lock']:c['sock'].send_control(opcode,payload)
    def send_frames(self,c,frames):
        with c['lock']:c['sock'].send_frames(frames)
    def send_packet(self,c,data):
        with c['lock']:c['sock'].sendall(data)
    def stop(self):
        self.stop_event.set();self.disconnect_all();self.listener.close();self.thread.join(1)
    def disconnect_all(self):
        with self.lock:
            for c in self.clients:
                try:c['sock'].shutdown(socket.SHUT_RDWR);c['sock'].close()
                except OSError:pass
            self.clients=[]
    def _accept(self):
        while not self.stop_event.is_set():
            try:s,_=self.listener.accept()
            except socket.timeout:continue
            except OSError:return
            t=threading.Thread(target=self._client,args=(s,),daemon=True);t.start()
    def _send(self,c,data):
        try:
            with c['lock']:c['sock'].sendall(data)
        except OSError:pass
    def _broadcast(self,topic,payload,qos,retain=False):
        with self.lock:
            targets=list(self.clients)
        for c in targets:
            for pattern,options in list(c['subs'].items()):
                if matches(pattern,topic):
                    q=min(qos,options&3);mid=1
                    ret=retain if options&8 else False
                    b=text(topic)+(mid.to_bytes(2,'big') if q else b'')+b'\x00'+payload
                    self._send(c,pkt(0x30|(q<<1)|int(ret),b));break
    def _client(self,s):
        c={'sock':s,'subs':{},'lock':threading.Lock(),'ws_events':[],'connect_flags':None}
        with self.lock:self.connections.append(c)
        graceful=False;will=None
        try:
            s.settimeout(5)
            if self.tls_context:
                s=self.tls_context.wrap_socket(s,server_side=True);c['sock']=s
            if self.websocket:
                s=WebSocketSocket.upgrade(s,self.websocket_path,c['ws_events'],self.websocket_early_close);c['sock']=s
            s.settimeout(.2);buf=bytearray();connected=False
            while not self.stop_event.is_set():
                try:d=s.recv(8192)
                except socket.timeout:continue
                if not d:break
                buf.extend(d)
                while len(buf)>=2:
                    try:n,pos=vint(buf,1)
                    except IndexError:break
                    if len(buf)<n+pos:break
                    h=buf[0];b=bytes(buf[pos:pos+n]);del buf[:pos+n];kind=h>>4
                    if kind==1:
                        with self.lock:self.mqtt_connect_count+=1
                        protocol,p=string(b,0)
                        assert protocol=='MQTT' and b[p]==5
                        flags=b[p+1];assert flags&2
                        c['connect_flags']=flags
                        p+=4;p=props(b,p);client_id,p=string(b,p)
                        if flags&4:
                            p=props(b,p);wt,p=string(b,p);wp,p=string(b,p);will=(wt,wp.encode())
                        user,p=string(b,p) if flags&128 else ('',p)
                        password,p=string(b,p) if flags&64 else ('',p)
                        if self.auth.get(user)!=password:
                            self._send(c,pkt(0x20,b'\x00\x86\x00'));return
                        assert p==len(b)
                        self._send(c,pkt(0x20,b'\x00\x00\x00'));connected=True
                        with self.lock:self.clients.append(c)
                    elif kind==8:
                        assert connected and h==0x82
                        mid=b[:2];p=props(b,2);codes=[]
                        while p<len(b):
                            topic,p=string(b,p);options=b[p];p+=1
                            if self.deny_sub and self.deny_sub in topic:codes.append(0x87);continue
                            c['subs'][topic]=options;codes.append(options&3)
                            if (options>>4)&3 != 2:
                                for t,v in self.retained.items():
                                    if matches(topic,t):self._send(c,pkt(0x31,text(t)+b'\x00'+v))
                        self._send(c,pkt(0x90,mid+b'\x00'+bytes(codes)))
                    elif kind==3:
                        assert connected
                        q=(h>>1)&3;t,p=string(b,0);mid=b[p:p+2] if q else None
                        if q:p+=2
                        p=props(b,p);payload=b[p:]
                        with self.lock:
                            self.messages.append({'topic':t,'payload':payload,'qos':q,'retain':bool(h&1),'time':time.monotonic()})
                            if h&1:
                                if payload:self.retained[t]=payload
                                else:self.retained.pop(t,None)
                        self._broadcast(t,payload,q,bool(h&1))
                        if q:
                            ack=pkt(0x40,mid) if self.puback_reason==0 else pkt(0x40,mid+bytes([self.puback_reason,0]))
                            self._send(c,ack)
                    elif kind==12:
                        with self.lock:self.mqtt_pingreq_count+=1
                        self._send(c,b'\xd0\x00')
                    elif kind==14:graceful=True;return
                    elif kind==4:pass
                    else:raise ValueError('unexpected packet')
        except (OSError,ssl.SSLError):pass
        except Exception as e:self.errors.append(type(e).__name__+':'+str(e))
        finally:
            with self.lock:
                if c in self.clients:self.clients.remove(c)
            if will and not graceful and not self.stop_event.is_set():self._broadcast(*will,qos=1,retain=True)
            try:s.close()
            except OSError:pass

def wait(predicate,seconds=5):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        if predicate():return True
        time.sleep(.03)
    return False
