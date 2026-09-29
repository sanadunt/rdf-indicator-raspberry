"""Independent minimal MQTT5 broker fixture, ONLY for tests on loopback.
Not shipped as a deployment broker. No imports from rdf_node.mqtt.
"""
import socket,ssl,threading,time,struct

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

class Broker:
    def __init__(self,tls_context=None,deny_sub=None,auth=None):
        self.listener=socket.socket();self.listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        self.listener.bind(('127.0.0.1',0));self.listener.listen(20);self.listener.settimeout(.2)
        self.port=self.listener.getsockname()[1];self.tls_context=tls_context
        self.deny_sub=deny_sub;self.auth=auth or {'test':'test-password'}
        self.lock=threading.RLock();self.clients=[];self.retained={};self.messages=[];self.errors=[]
        self.stop_event=threading.Event();self.thread=threading.Thread(target=self._accept,daemon=True);self.thread.start()
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
        c={'sock':s,'subs':{},'lock':threading.Lock()}
        graceful=False;will=None
        try:
            s.settimeout(5)
            if self.tls_context:
                s=self.tls_context.wrap_socket(s,server_side=True);c['sock']=s
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
                        protocol,p=string(b,0)
                        assert protocol=='MQTT' and b[p]==5
                        flags=b[p+1];assert flags&2
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
                        if q:self._send(c,pkt(0x40,mid))
                    elif kind==12:self._send(c,b'\xd0\x00')
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
