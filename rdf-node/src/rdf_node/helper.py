"""Root helper: fixed Unix RPC, no shell, no arbitrary path/unit from the network."""
from __future__ import annotations
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import socket
import socketserver
import stat
import struct
import subprocess
import threading
import time
import yaml
from .util import atomic_write, compact, digest, strict_json, finite, integer, read_boot_id
from .source import FIELD_MAP

DEFAULT_POLICY={
    'allowed_user':'rdf-edge', 'socket':'/run/rdf-node-control/control.sock',
    'state_dir':'/var/lib/rdf-node-control', 'settings_path':None,
    'engine_service':None, 'allow_config':False, 'single_writer_confirmed':False,
    'allow_lifecycle':False, 'lifecycle_audited':False, 'allow_reboot':False, 'allow_shutdown':False,
    'allow_ppp_restart':False, 'allow_remote_control':False,
    'frequency_min_hz':24000000, 'frequency_max_hz':1766000000,
    'bandwidth_max_hz':2400000,
    'gain_values_db':[0.0,0.9,1.4,2.7,3.7,7.7,8.7,12.5,14.4,15.7,16.6,19.7,20.7,22.9,25.4,28.0,29.7,32.8,33.8,36.4,37.2,38.6,40.2,42.1,43.4,43.9,44.5,48.0,49.6],
}

class HelperError(ValueError): pass

def validate_changes(changes,policy):
    if not isinstance(changes,dict) or not changes or len(changes)>5 or set(changes)-set(FIELD_MAP):
        raise HelperError('UNSUPPORTED_SETTINGS_FIELD')
    out={}
    for key,val in changes.items():
        if key in ('center_frequency_hz','vfo0_frequency_hz'):
            out[key]=integer(val,policy['frequency_min_hz'],policy['frequency_max_hz'])
        elif key=='vfo0_bandwidth_hz': out[key]=integer(val,100,policy['bandwidth_max_hz'])
        elif key=='gain_db':
            val=finite(val,-10,100)
            if not any(abs(val-g)<0.001 for g in policy['gain_values_db']): raise HelperError('GAIN_NOT_IN_APPROVED_TABLE')
            out[key]=val
        elif key=='vfo0_squelch_db': out[key]=finite(val,-200,50)
    return out

def secure_parent(path):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts: raise HelperError('INVALID_APPROVED_PATH')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for name in path.parts[1:-1]:
            new=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd); fd=new
        return fd,path.name
    except Exception:
        os.close(fd); raise

def read_at(fd,name):
    f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
    try:
        st=os.fstat(f)
        if not stat.S_ISREG(st.st_mode) or st.st_size>65536: raise HelperError('SETTINGS_FILE_INVALID')
        raw=os.read(f,65537)
        if len(raw)>65536: raise HelperError('SETTINGS_TOO_LARGE')
        return raw,st
    finally: os.close(f)

def patch_file(path,changes,expected_raw_digest,policy):
    changes=validate_changes(changes,policy)
    fd,name=secure_parent(path); tmp='.rdf-'+secrets.token_hex(16)
    try:
        raw,st=read_at(fd,name)
        if hashlib.sha256(raw).hexdigest()!=expected_raw_digest: raise HelperError('CONFIG_CHANGED_BEFORE_WRITE')
        j=strict_json(raw)
        if not isinstance(j,dict): raise HelperError('SETTINGS_OBJECT_REQUIRED')
        active=integer(j.get('active_vfos',1),1,16)
        if active!=1 or int(j.get('output_vfo',0))!=0: raise HelperError('CONTROL_REQUIRES_SINGLE_VFO0')
        for key,val in changes.items():
            native,scale=FIELD_MAP[key]
            j[native]=val/scale if scale!=1 else val
        # Retune policy keeps center and VFO coordinated; standalone center change
        # must not leave a VFO in an unknown passband.
        if 'center_frequency_hz' in changes and 'vfo0_frequency_hz' not in changes:
            raise HelperError('CENTER_CHANGE_REQUIRES_VFO0_TARGET')
        if 'center_frequency_hz' in changes and changes['center_frequency_hz']!=changes['vfo0_frequency_hz']:
            raise HelperError('MVP_CENTER_MUST_EQUAL_VFO0')
        j['ext_upd_flag']=True
        data=compact(j)
        out=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,st.st_mode&0o777,dir_fd=fd)
        try:
            os.fchown(out,st.st_uid,st.st_gid)
            with os.fdopen(out,'wb',closefd=False) as f:
                f.write(data); f.flush(); os.fsync(out)
        finally: os.close(out)
        check,_=read_at(fd,name)
        if hashlib.sha256(check).hexdigest()!=expected_raw_digest: raise HelperError('EXTERNAL_WRITER_CONFLICT')
        # Backups are handled by Controller in its private state directory.
        os.replace(tmp,name,src_dir_fd=fd,dst_dir_fd=fd); os.fsync(fd)
        return {'persisted':True,'raw_digest':hashlib.sha256(data).hexdigest(),'changes':changes}
    finally:
        for candidate in (tmp,tmp+'-backup'):
            try: os.unlink(candidate,dir_fd=fd)
            except FileNotFoundError: pass
        os.close(fd)

def call_helper(sock_path,request,timeout=8):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(timeout); s.connect(sock_path); s.sendall(compact(request)+b'\n')
        buf=b''
        while b'\n' not in buf:
            b=s.recv(4096)
            if not b: raise HelperError('HELPER_CLOSED')
            buf+=b
            if len(buf)>16384: raise HelperError('HELPER_RESPONSE_LIMIT')
        result=strict_json(buf.split(b'\n',1)[0])
        if not result.get('ok'):
            error=result.get('error','HELPER_FAILED')
            if error=='SYSTEMD_ACTION_OUTCOME_UNKNOWN': raise TimeoutError(error)
            raise HelperError(error)
        return result['result']

def require_ppp_service_ready():
    """Fail closed unless the fixed PPP unit is loaded, active, and has no job."""
    try:
        result=subprocess.run(['/usr/bin/systemctl','show','--no-pager',
            '--property=LoadState,ActiveState,Job','t900-ppp.service'],timeout=5,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    except (OSError,subprocess.TimeoutExpired) as error:
        raise HelperError('PPP_SERVICE_STATUS_UNKNOWN') from error
    if result.returncode:
        raise HelperError('PPP_SERVICE_STATUS_UNKNOWN')
    state=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    if any(key not in state or not state[key] for key in ('LoadState','ActiveState','Job')):
        raise HelperError('PPP_SERVICE_STATUS_UNKNOWN')
    if state['LoadState']!='loaded':
        raise HelperError('PPP_SERVICE_NOT_LOADED')
    if state['ActiveState']!='active':
        raise HelperError('PPP_SERVICE_NOT_ACTIVE')
    if state['Job']!='0':
        raise HelperError('PPP_RESTART_PENDING')

class Controller:
    def __init__(self,policy):
        import re
        self.policy=policy; self.state=Path(policy['state_dir']); self.state.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.boot=read_boot_id(); self.lock=threading.Lock(); self.challenges={}
        self.uid=pwd.getpwnam(policy['allowed_user']).pw_uid if policy['allowed_user'] else os.getuid()
        u=policy.get('engine_service')
        if u and (not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service',u) or u in ('rdf-edge.service','t900-ppp.service','ssh.service')):
            raise HelperError('UNSAFE_ENGINE_UNIT')
    def maintenance(self):
        try:
            j=strict_json((self.state/'maintenance.json').read_bytes())
            return j.get('boot')==self.boot and time.monotonic()<j.get('until',0)
        except (ValueError,OSError): return False
    def _remove_state_file(self,path):
        try: path.unlink()
        except FileNotFoundError: return False
        fd=os.open(self.state,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
        return True
    def _shutdown_scheduled(self):
        path=self.state/'shutdown-intent.json'
        try: intent=strict_json(path.read_bytes())
        except FileNotFoundError: return False
        except (OSError,ValueError): return True
        if not isinstance(intent,dict) or not isinstance(intent.get('boot'),str) or not intent['boot']: return True
        if intent['boot']==self.boot: return True
        self._remove_state_file(path)
        return False
    def _reconcile_shutdown(self):
        path=self.state/'shutdown-intent.json'
        try: intent=strict_json(path.read_bytes())
        except FileNotFoundError: return {'cleared':False,'reason':'NO_PENDING_SHUTDOWN'}
        except (OSError,ValueError): raise HelperError('SHUTDOWN_INTENT_INVALID')
        if not isinstance(intent,dict) or not isinstance(intent.get('boot'),str) or not intent['boot']:
            raise HelperError('SHUTDOWN_INTENT_INVALID')
        if intent['boot']!=self.boot:
            self._remove_state_file(path)
            return {'cleared':True,'reason':'NEW_BOOT','boot':self.boot}
        import re
        unit=intent.get('unit')
        if not isinstance(unit,str) or not re.fullmatch(r'rdf-node-shutdown-[0-9a-f]{10}',unit):
            raise HelperError('SHUTDOWN_INTENT_INVALID')
        for suffix in ('.timer','.service'):
            try:
                result=subprocess.run(['/usr/bin/systemctl','show','--no-pager',
                    '--property=LoadState,ActiveState',unit+suffix],timeout=5,
                    stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
            except (OSError,subprocess.TimeoutExpired) as error:
                raise HelperError('SHUTDOWN_UNIT_STATUS_UNKNOWN') from error
            if result.returncode:
                raise HelperError('SHUTDOWN_UNIT_STATUS_UNKNOWN')
            state=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
            load=state.get('LoadState'); active=state.get('ActiveState')
            if load=='not-found' and active=='inactive': continue
            if load=='loaded' and active in ('inactive','failed'): continue
            if load=='loaded' and active in ('active','activating','reloading','deactivating'):
                raise HelperError('SHUTDOWN_UNIT_ACTIVE')
            raise HelperError('SHUTDOWN_UNIT_STATUS_UNKNOWN')
        self._remove_state_file(path)
        return {'cleared':True,'reason':'NO_ACTIVE_SHUTDOWN_UNIT','boot':self.boot}
    def _run(self,args):
        # Only called with argument arrays constructed below, never shell=True.
        p=subprocess.run(args,timeout=10,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        if p.returncode: raise HelperError('SYSTEMD_ACTION_FAILED')
        return p.stdout.strip()
    def state_info(self):
        desired=None
        try: desired=strict_json((self.state/'intent.json').read_bytes()).get('desired')
        except (OSError,ValueError): pass
        return dict(maintenance=self.maintenance(),desired=desired,
                    allow_config=self.policy['allow_config'] and self.policy['single_writer_confirmed'],
                    allow_lifecycle=self.policy['allow_lifecycle'] and self.policy['lifecycle_audited'],
                    allow_reboot=self.policy['allow_reboot'],allow_shutdown=self.policy['allow_shutdown'],
                    allow_ppp_restart=self.policy['allow_ppp_restart'],
                    allow_remote_control=self.policy['allow_remote_control'])
    def reconcile_intent(self):
        if not (self.policy['allow_lifecycle'] and self.policy['lifecycle_audited'] and self.policy['engine_service']):
            return
        try:
            desired=strict_json((self.state/'intent.json').read_bytes()).get('desired')
        except (OSError,ValueError):
            return
        if desired not in ('RUNNING','STOPPED'): return
        marker=self.state/'engine.stopped'
        if desired=='STOPPED': atomic_write(marker,b'operator stop\n')
        else: marker.unlink(missing_ok=True)
        self._run(['/usr/bin/systemctl','--no-block','start' if desired=='RUNNING' else 'stop',self.policy['engine_service']])
    def dispatch(self,req,uid):
        if uid not in (0,self.uid): raise HelperError('PEER_UID_DENIED')
        if not isinstance(req,dict) or not isinstance(req.get('op'),str): raise HelperError('INVALID_RPC')
        op=req['op']
        keys={'status':{'op'},'maintenance.open':{'op','seconds'},'maintenance.close':{'op'},
              'config.patch':{'op','changes','expected_digest'},'processing.set':{'op','desired','origin'},
              'service.restart':{'op','origin'},'ppp.restart':{'op','origin'},
              'system.reboot.prepare':{'op','id','origin'},
              'system.reboot.execute':{'op','id','challenge','origin'},
              'system.shutdown.prepare':{'op','id','origin'},
              'system.shutdown.execute':{'op','id','challenge','origin'},
              'system.shutdown.reconcile':{'op'}}
        if op not in keys: raise HelperError('UNSUPPORTED_RPC')
        if set(req)-keys[op]: raise HelperError('INVALID_RPC_ARGUMENT')
        if op in ('processing.set','service.restart','ppp.restart','system.reboot.prepare','system.reboot.execute',
                  'system.shutdown.prepare','system.shutdown.execute'):
            origin=req.get('origin')
            if origin not in ('ground-controller','local-admin'):
                raise HelperError('INVALID_RPC_ARGUMENT')
            if origin=='ground-controller' and not self.policy['allow_remote_control']:
                raise HelperError('REMOTE_CONTROL_NOT_APPROVED')
        if op=='status': return self.state_info()
        if op.startswith('maintenance.'):
            if uid!=0: raise HelperError('MAINTENANCE_REQUIRES_LOCAL_SUDO')
            sec=integer(req.get('seconds',300),30,900) if op.endswith('open') else 0
            atomic_write(self.state/'maintenance.json',compact({'boot':self.boot,'until':time.monotonic()+sec}))
            return {'maintenance':sec>0,'seconds':sec}
        with self.lock:
            if op=='system.shutdown.reconcile':
                if uid!=0: raise HelperError('SHUTDOWN_RECONCILE_REQUIRES_ROOT')
                return self._reconcile_shutdown()
            if op=='ppp.restart':
                if not self.policy['allow_ppp_restart']: raise HelperError('PPP_RESTART_NOT_APPROVED')
                if origin!='ground-controller' and not self.maintenance(): raise HelperError('MAINTENANCE_REQUIRED')
                require_ppp_service_ready()
                try:
                    self._run(['/usr/bin/systemctl','--no-block','restart','t900-ppp.service'])
                except (OSError,TimeoutError,subprocess.TimeoutExpired,HelperError) as error:
                    raise TimeoutError('SYSTEMD_ACTION_OUTCOME_UNKNOWN') from error
                return {'requested':True,'service':'t900-ppp.service'}

            if op=='config.patch':
                if not self.policy['allow_config'] or not self.policy['single_writer_confirmed'] or not self.policy['settings_path']:
                    raise HelperError('CONFIG_ADAPTER_NOT_APPROVED')
                # Never place raw settings backups in the HTTP-exposed _share.
                fd,name=secure_parent(self.policy['settings_path'])
                try: previous,_=read_at(fd,name)
                finally: os.close(fd)
                if hashlib.sha256(previous).hexdigest()!=req.get('expected_digest'):
                    raise HelperError('CONFIG_CHANGED_BEFORE_WRITE')
                atomic_write(self.state/'settings-backup.json',previous,0o600)
                return patch_file(self.policy['settings_path'],req.get('changes'),req.get('expected_digest'),self.policy)
            if op in ('processing.set','service.restart'):
                if not self.policy['allow_lifecycle'] or not self.policy['lifecycle_audited'] or not self.policy['engine_service']:
                    raise HelperError('LIFECYCLE_NOT_APPROVED')
                if origin!='ground-controller' and not self.maintenance(): raise HelperError('MAINTENANCE_REQUIRED')
                desired=req.get('desired') if op=='processing.set' else 'RUNNING'
                if desired not in ('RUNNING','STOPPED'): raise HelperError('INVALID_DESIRED_STATE')
                atomic_write(self.state/'intent.json',compact({'desired':desired,'boot':self.boot}))
                marker=self.state/'engine.stopped'
                if desired=='STOPPED': atomic_write(marker,b'operator stop\n')
                else:
                    marker.unlink(missing_ok=True)
                    fd=os.open(self.state,os.O_RDONLY|os.O_DIRECTORY); os.fsync(fd); os.close(fd)
                action='restart' if op=='service.restart' else 'start' if desired=='RUNNING' else 'stop'
                self._run(['/usr/bin/systemctl','--no-block',action,self.policy['engine_service']])
                return {'requested':desired,'action':action,'accepted':True}
            if op.startswith(('system.reboot.','system.shutdown.')):
                shutdown=op.startswith('system.shutdown.')
                action='shutdown' if shutdown else 'reboot'
                if not self.policy['allow_shutdown' if shutdown else 'allow_reboot']:
                    raise HelperError('SHUTDOWN_DISABLED' if shutdown else 'REBOOT_DISABLED')
                if origin!='ground-controller' and not self.maintenance(): raise HelperError('MAINTENANCE_REQUIRED')
                id=req.get('id')
                if not isinstance(id,str) or not 1<=len(id)<=80:
                    raise HelperError('INVALID_SHUTDOWN_ID' if shutdown else 'INVALID_REBOOT_ID')
                if op.endswith('prepare'):
                    if shutdown and self._shutdown_scheduled():
                        raise HelperError('SHUTDOWN_ALREADY_SCHEDULED')
                    now=time.monotonic()
                    for challenge_id in tuple(self.challenges):
                        if now>self.challenges[challenge_id][1]:
                            del self.challenges[challenge_id]
                    if id not in self.challenges and len(self.challenges)>=32:
                        raise HelperError('TOO_MANY_ACTIVE_CHALLENGES')
                    token=secrets.token_urlsafe(24)
                    self.challenges[id]=(hashlib.sha256(token.encode()).digest(),now+30,action)
                    return {'challenge':token,'valid_seconds':30,'prepare_id':id}
                previous=self.challenges.pop(id,None)
                token=req.get('challenge','')
                valid=(isinstance(token,str) and previous is not None and previous[2]==action
                       and time.monotonic()<=previous[1]
                       and secrets.compare_digest(hashlib.sha256(token.encode()).digest(),previous[0]))
                if not valid:
                    raise HelperError('SHUTDOWN_CHALLENGE_INVALID' if shutdown else 'REBOOT_CHALLENGE_INVALID')
                if shutdown and self._shutdown_scheduled():
                    raise HelperError('SHUTDOWN_ALREADY_SCHEDULED')
                unit='rdf-node-'+action+'-'+secrets.token_hex(5)
                atomic_write(self.state/f'{action}-intent.json',compact({'id':id,'boot':self.boot,**({'unit':unit} if shutdown else {})}))
                command=['/usr/bin/systemd-run','--quiet','--unit='+unit,'--on-active=5s',
                         '/usr/bin/systemctl','poweroff' if shutdown else 'reboot']
                try:
                    self._run(command)
                except HelperError as error:
                    if str(error)=='SYSTEMD_ACTION_FAILED':
                        raise TimeoutError('SYSTEMD_ACTION_OUTCOME_UNKNOWN') from error
                    raise
                return {'scheduled':True,'delay_seconds':5,'action':action}
        raise HelperError('UNSUPPORTED_RPC')

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            cred=self.request.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12)
            _,uid,_=struct.unpack('3i',cred)
            data=self.rfile.readline(8193)
            if len(data)>8192 or not data.endswith(b'\n'): raise HelperError('RPC_SIZE_LIMIT')
            req=strict_json(data)
            result=self.server.controller.dispatch(req,uid)
            self.wfile.write(compact({'ok':True,'result':result})+b'\n')
        except Exception as e:
            msg='SYSTEMD_ACTION_OUTCOME_UNKNOWN' if isinstance(e,(subprocess.TimeoutExpired,TimeoutError)) else (
                str(e) if isinstance(e,ValueError) and str(e).isupper() else type(e).__name__.upper())
            try: self.wfile.write(compact({'ok':False,'error':msg})+b'\n')
            except OSError: pass

def serve(policy_file):
    if os.geteuid()!=0: raise SystemExit('Helper must run as root under systemd.')
    path=Path(policy_file); st=path.stat()
    if st.st_uid!=0 or st.st_mode&0o022: raise SystemExit('Helper policy must be root-owned and not group/world writable.')
    value=yaml.safe_load(path.read_text())
    policy=dict(DEFAULT_POLICY)
    if not isinstance(value,dict) or set(value)-set(policy): raise SystemExit('Unknown helper policy key.')
    policy.update(value)
    for k in ('allow_config','single_writer_confirmed','allow_lifecycle','lifecycle_audited','allow_reboot','allow_shutdown','allow_ppp_restart','allow_remote_control'):
        if type(policy[k]) is not bool: raise SystemExit('Boolean policy required.')
    c=Controller(policy)
    c.reconcile_intent()
    sock=Path(policy['socket']); sock.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
    if sock.exists():
        if not stat.S_ISSOCK(sock.stat().st_mode): raise SystemExit('Socket path is not a socket.')
        sock.unlink()
    class Server(socketserver.UnixStreamServer):
        timeout=2
    with Server(str(sock),Handler) as s:
        s.controller=c; os.chown(sock,0,grp.getgrnam('rdf-edge').gr_gid); os.chmod(sock,0o660)
        s.serve_forever(poll_interval=0.5)
