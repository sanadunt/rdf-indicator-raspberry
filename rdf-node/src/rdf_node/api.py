"""Loopback-only status API. Bounded clients, body limits and explicit write auth."""
from __future__ import annotations
import base64
import hashlib
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit
from .util import compact, strict_json, atomic_write

ROOT=Path(__file__).resolve().parents[2]

def hash_pin(pin:str):
    if not isinstance(pin,str) or len(pin)!=6 or not pin.isascii() or not pin.isdigit():
        raise ValueError('PIN must contain exactly 6 digits.')
    salt=secrets.token_bytes(16); iterations=260000
    derived=hashlib.pbkdf2_hmac('sha256',pin.encode(),salt,iterations)
    return dict(credential='pin-6',algorithm='pbkdf2-sha256',iterations=iterations,salt=salt.hex(),hash=derived.hex())

def set_pin(path:Path,pin:str):
    atomic_write(path,compact(hash_pin(pin)),0o640)

class Auth:
    def __init__(self,hash_file,seconds=600):
        self.file=Path(hash_file); self.seconds=seconds; self.sessions={}; self.attempts=[]; self.lock=threading.RLock()
    def login(self,pin):
        if not isinstance(pin,str) or len(pin)!=6 or not pin.isascii() or not pin.isdigit(): return None
        with self.lock:
            now=time.monotonic(); self.attempts=[x for x in self.attempts if now-x<60]
            if len(self.attempts)>=6: return None
            self.attempts.append(now)
            try:
                data=strict_json(self.file.read_bytes())
                expected=bytes.fromhex(data['hash']); salt=bytes.fromhex(data['salt'])
                if data.get('credential')!='pin-6' or data['algorithm']!='pbkdf2-sha256' or not 100000<=data['iterations']<=1000000: return None
                candidate=hashlib.pbkdf2_hmac('sha256',pin.encode(),salt,data['iterations'])
                if not hmac.compare_digest(candidate,expected): return None
            except (OSError,ValueError,KeyError): return None
            self.attempts=[]
            self.sessions={k:v for k,v in self.sessions.items() if v[0]>now}
            if len(self.sessions)>=8: self.sessions.pop(next(iter(self.sessions)))
            token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(24)
            self.sessions[token]=(now+self.seconds,csrf)
            return token,csrf
    def session(self,cookie):
        token=None
        for part in (cookie or '').split(';'):
            if part.strip().startswith('rdf_session='): token=part.strip().split('=',1)[1]
        with self.lock:
            item=self.sessions.get(token)
            if not item or item[0]<=time.monotonic(): return None
            return token,item[1]

class Server(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self,provider,cfg,ground=False):
        self.provider=provider; self.cfg=cfg; self.ground=ground
        self.auth=Auth(cfg['api']['admin_hash_file'],cfg['api']['session_seconds'])
        self.slots=threading.BoundedSemaphore(cfg['api']['max_clients'])
        super().__init__(('127.0.0.1',cfg['api']['port']),Handler)
    def process_request(self,request,address):
        if not self.slots.acquire(False):
            try: request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n')
            except OSError: pass
            self.shutdown_request(request); return
        try: super().process_request(request,address)
        except Exception:
            self.slots.release(); raise
    def process_request_thread(self,request,address):
        try: super().process_request_thread(request,address)
        finally: self.slots.release()

class Handler(BaseHTTPRequestHandler):
    server_version='RDFNode/1.0'
    sys_version=''
    protocol_version='HTTP/1.0'
    def setup(self):
        super().setup(); self.connection.settimeout(4)
    def log_message(self,fmt,*args):
        # Do not log command bodies, authentication material or connection URLs.
        pass
    def _host(self):
        host=self.headers.get('Host','')
        port=self.server.server_port
        return host in (f'127.0.0.1:{port}',f'localhost:{port}')
    def _origin(self):
        origin=self.headers.get('Origin','')
        return origin in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}')
    def reply(self,status,obj,kind='application/json; charset=utf-8',extra=None):
        data=obj if isinstance(obj,bytes) else compact(obj)
        self.send_response(status)
        self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY'); self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for k,v in (extra or {}).items(): self.send_header(k,v)
        self.end_headers()
        try: self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError,TimeoutError): pass
    def do_OPTIONS(self): self.reply(403,{'error':'CROSS_ORIGIN_NOT_ALLOWED'})
    def do_GET(self):
        if not self._host(): return self.reply(403,{'error':'HOST_NOT_ALLOWED'})
        p=urlsplit(self.path).path; app=self.server.provider
        try:
            static={'/':'ground.html' if self.server.ground else 'index.html','/app.js':'app.js','/style.css':'style.css','/ground.js':'ground.js','/ground.css':'ground.css'}
            if p in static:
                file=ROOT/'web'/static[p]
                kind='text/html; charset=utf-8' if file.suffix=='.html' else 'text/javascript; charset=utf-8' if file.suffix=='.js' else 'text/css; charset=utf-8'
                return self.reply(200,file.read_bytes(),kind)
            if p=='/api/v2/session':
                s=self.server.auth.session(self.headers.get('Cookie'))
                return self.reply(200,{'authenticated':bool(s),'csrf':s[1] if s else None})
            if p=='/api/v2/mqtt/settings':
                if self.server.ground: return self.reply(404,{'error':'NOT_FOUND'})
                if not self.server.auth.session(self.headers.get('Cookie')): return self.reply(401,{'error':'AUTHENTICATION_REQUIRED'})
                return self.reply(200,app.mqtt_settings_view())
            snap=app.snapshot()
            if p=='/api/v2/snapshot': return self.reply(200,snap)
            if p=='/api/v2/link': return self.reply(200,snap.get('link',{}))
            if p=='/api/v2/system': return self.reply(200,{'host':snap.get('host',{}),'daq':snap.get('daq',{}),'alerts':snap.get('active_alerts',[])})
            if p=='/api/v2/config': return self.reply(200,app.config_view())
            if p=='/api/v2/capabilities': return self.reply(200,app.capabilities())
            if p=='/api/v2/operations/latest':
                return self.reply(200,app.operations_view() if self.server.ground else [app.commands.public(x) for x in app.journal.latest()])
            if p=='/api/v2/angular/latest':
                if self.server.ground: return self.reply(200,app.angular_view())
                r=app.source.record
                return self.reply(200,{'q':r['q'],'timestamp_ms':r['timestamp_ms'],'values':r['values'],'live':snap.get('detection',{}).get('valid',False)} if r else None)
            if p=='/api/v2/healthz':
                age=max(0,__import__('time').time()*1000-snap.get('snapshot_ms',0))
                return self.reply(200 if age<5000 else 503,{'api':True,'snapshot_progress_age_ms':int(age),'daq_health_not_implied':True})
            if p=='/api/v2/readyz': return self.reply(200,{'link':snap.get('link',{}),'detection':snap.get('detection',{}),'capabilities':app.capabilities()})
            return self.reply(404,{'error':'NOT_FOUND'})
        except Exception:
            return self.reply(500,{'error':'LOCAL_API_ERROR'})
    def _body(self):
        if self.headers.get('Transfer-Encoding'): raise ValueError('TRANSFER_ENCODING_NOT_ALLOWED')
        if len(self.headers.get_all('Content-Length',[]))!=1: raise ValueError('CONTENT_LENGTH_REQUIRED')
        n=int(self.headers['Content-Length'])
        if not 0<n<=8192: raise ValueError('BODY_SIZE_LIMIT')
        if self.headers.get_content_type()!='application/json': raise ValueError('JSON_REQUIRED')
        data=self.rfile.read(n)
        if len(data)!=n: raise ValueError('INCOMPLETE_BODY')
        obj=strict_json(data)
        if not isinstance(obj,dict): raise ValueError('OBJECT_REQUIRED')
        return obj
    def do_POST(self):
        if not self._host() or not self._origin(): return self.reply(403,{'error':'ORIGIN_NOT_ALLOWED'})
        try: obj=self._body()
        except (ValueError,OSError,RecursionError): return self.reply(400,{'error':'INVALID_JSON_REQUEST'})
        p=urlsplit(self.path).path
        if p=='/api/v2/login':
            result=self.server.auth.login(obj.get('pin'))
            if not result: return self.reply(401,{'error':'LOGIN_FAILED_OR_RATE_LIMITED'})
            token,csrf=result
            return self.reply(200,{'authenticated':True,'csrf':csrf},extra={'Set-Cookie':f'rdf_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={self.server.cfg["api"]["session_seconds"]}'})
        session=self.server.auth.session(self.headers.get('Cookie'))
        if not session or not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),session[1]):
            return self.reply(403,{'error':'AUTHENTICATION_AND_CSRF_REQUIRED'})
        app=self.server.provider
        if p=='/api/v2/logout':
            with self.server.auth.lock: self.server.auth.sessions.pop(session[0],None)
            return self.reply(200,{'authenticated':False},extra={'Set-Cookie':'rdf_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/'})
        try:
            if p=='/api/v2/operation/result':
                op=app.journal.lookup(str(obj.get('id','')))
                if not op: return self.reply(404,{'error':'OPERATION_NOT_FOUND'})
                return self.reply(200,{'id':op['id'],'stage':op['stage'],'result':op['result']})
            if p=='/api/v2/commands':
                if self.server.ground: result=app.submit_command(obj)
                else: result=app.commands.submit(obj,'local-admin')
                return self.reply(400 if result.get('stage')=='REJECTED' else 202,result)
            if p=='/api/v2/mqtt/settings' and not self.server.ground:
                return self.reply(200,app.configure_mqtt(obj))
            if p=='/api/v2/display/preferences' and not self.server.ground:
                if set(obj)-{'theme','accent','font','blank_after_seconds'}: raise ValueError('UNKNOWN_PREFERENCE')
                prefs=dict(app.cfg['display'])
                prefs.update(app.journal.get('display',{}))
                prefs.update(obj)
                if (prefs['theme'] not in ('dark','light') or prefs['accent'] not in ('teal','blue','amber') or
                    prefs['font'] not in ('system','serif','mono') or type(prefs['blank_after_seconds']) is not int or
                    not 0<=prefs['blank_after_seconds']<=86400):
                    raise ValueError('BAD_PREFERENCE')
                app.journal.set('display',prefs)
                return self.reply(200,prefs)
            return self.reply(404,{'error':'NOT_FOUND'})
        except (ValueError,KeyError): return self.reply(400,{'error':'INVALID_REQUEST'})
        except Exception: return self.reply(500,{'error':'OPERATION_UNAVAILABLE'})
