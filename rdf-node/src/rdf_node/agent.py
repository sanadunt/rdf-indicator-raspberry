from __future__ import annotations
import copy
import queue
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from . import __version__
from .util import now_ms, compact, strict_json, read_boot_id, atomic_write, stable_read
from .config import PROFILES
from .journal import Journal
from .source import Source
from .monitor import Monitor
from .control import CommandManager, OPS, READ_OPS
from .helper import call_helper
from .mqtt import Client
from .mqtt_ws import validate_mqtt_host,validate_websocket_path
from .codec import FLAG_PARSED, FLAG_FRESH, FLAG_DAQ, FLAG_CONVENTION, FLAG_CONFIG, FLAG_AUTHORITY
UNVERIFIED_REASONS=frozenset(('SOURCE_UNVERIFIED','ANGLE_UNVERIFIED'))
# Cost-byte/s reserves based on current Control fixtures; invalid-source reserve omits normal DoA.
ANGULAR_CONTROL_BASELINE_VALID=800
ANGULAR_CONTROL_BASELINE_INVALID=510
# Bulk yields the shared PPP link while a Control PUBACK is this late or this many Bulk bytes are unACKed.
BULK_YIELD_PENDING_MS=1000
BULK_YIELD_BACKLOG_BYTES=2048

class Agent:
    def __init__(self,cfg,demo=False):
        self.cfg=cfg; self.demo=demo; self.boot=read_boot_id(); self.instance=str(uuid.uuid4()); self.sid=uuid.uuid4().hex[:8]
        self.prefix=('sdr/demo/v2/' if demo else 'sdr/v2/')+cfg['node_id']
        self.journal=Journal(Path(cfg['state_dir'])/'state.sqlite3'); self.journal.recover(self.boot)
        self.source=Source(cfg,self.journal); self.monitor=Monitor(cfg); self.commands=CommandManager(self)
        self.profile=self.journal.get('profile',cfg['telemetry']['profile'])
        if self.profile not in PROFILES: self.profile=cfg['telemetry']['profile']
        self.config_proof='unverified'; self.planned_reboot=False
        self.lock=threading.RLock(); self.snapshot_data={}; self.snapshot_seq=0; self.mqtt_topic_delivery={}
        self.settings_request_lock=threading.Lock()
        self.settings_request_id=None; self.settings_request_payload=None
        self.stop_event=threading.Event(); self.events=queue.Queue(32)
        self.bulk_resume_after=time.monotonic()+cfg['telemetry']['resume_stable_seconds']
        self.hq=0; self.last_doa_q=0; self.last_angular_q=0
        self.diagnostic_q=0; self.diagnostic_angular_q=0
        self.angular_abort=0; self.bulk_reason='BOOTSTRAP'; self.helper_status={}
        self.clients={}; self._generation=-1; self.last_config_rev=None; self.last_sent_state=None
        self.mqtt_state_path=Path(cfg['state_dir'])/'mqtt-ui-settings.json'
        self.mqtt_credentials_paths={name:Path(cfg['state_dir'])/f'mqtt-ui-{name}.json' for name in ('control','bulk')}
        self.mqtt_base_credentials={name:cfg['mqtt'].get(f'{name}_credentials_file') for name in ('control','bulk')}
        self.mqtt_config_lock=threading.RLock(); self.mqtt_apply_lock=threading.RLock()
        self.mqtt_reconfigure=threading.Event(); self.mqtt_pending=None; self.mqtt_running=False
        # Inbound commands are tagged per link; a reconfigure bumps the epoch so stale sessions are ignored.
        self.link_epoch={'primary':0,'backup':0}
        self.mqtt_desired=self._load_mqtt_settings()
        self.cfg['mqtt']=self._resolved_mqtt_config(self.mqtt_desired)
        self.clients=self._new_mqtt_clients(self.mqtt_desired)
        # Backup broker: a LAN/online mirror with its own client, settings and delivery map.
        self.backup_state_path=Path(cfg['state_dir'])/'mqtt-ui-backup-settings.json'
        self.backup_account_path=Path(cfg['state_dir'])/'mqtt-ui-backup-account.json'
        self.backup_reconfigure=threading.Event(); self.backup_pending=None
        self.mqtt_backup_topic_delivery={}; self._backup_generation=-1
        self.backup_bulk_reason=None; self.backup_last_angular_q=0; self.backup_angular_abort=0
        self.settings_request_link=None; self.backup_client_id=None
        # Operator-visible command history and PPP link recovery state (panel only; no extra radio traffic).
        self.operation_seq=0; self.remote_rejects=deque(maxlen=5)
        self.ppp_recovery=None; self._last_ppp=None; self._last_probe=None
        self.backup_desired=self._load_backup_settings()
        self.backup_active=self.backup_desired
        self.backup_client=self._new_backup_client(self.backup_desired)
        self.threads=[]
    @staticmethod
    def _validate_mqtt_endpoint(host,port,client_id):
        validate_mqtt_host(host)
        if type(port) is not int or not 1<=port<=65535: raise ValueError('INVALID_MQTT_PORT')
        if not isinstance(client_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,47}',client_id):
            raise ValueError('INVALID_MQTT_CLIENT_ID')
    @staticmethod
    def _credentials_ready(path):
        if not path: return False
        try:
            value=strict_json(stable_read(Path(path),4096,nofollow=True))
            return isinstance(value,dict) and set(value)=={'username','password'} and all(isinstance(x,str) and x for x in value.values())
        except (OSError,TypeError,ValueError): return False
    def _mqtt_defaults(self):
        m=self.cfg['mqtt']
        return {'enabled':m['enabled'],'host':m['host'],'port':m['port'],
                'client_id':m.get('client_id') or self.cfg['node_id'],'transport':m.get('transport','tcp'),
                'websocket_path':m.get('websocket_path','/mqtt'),'tls':bool(m.get('tls',True)),
                'control_custom':False,'bulk_custom':False}
    def _mqtt_credentials_path(self,channel,settings):
        return self.mqtt_credentials_paths[channel] if settings[f'{channel}_custom'] else self.mqtt_base_credentials[channel]
    def _mqtt_transport_ready(self,settings,replacements=None):
        m=self.cfg['mqtt'];tls=settings.get('tls',m.get('tls',True));ca_file=m.get('ca_file')
        if type(tls) is not bool or (tls and ca_file and not Path(ca_file).is_file()): return False
        replacements=replacements or {}
        for channel in ('control','bulk'):
            path=self._mqtt_credentials_path(channel,settings)
            if channel not in replacements and path and not self._credentials_ready(path): return False
        return True
    def _load_mqtt_settings(self):
        defaults=self._mqtt_defaults()
        if self.demo: return defaults
        try:
            value=strict_json(stable_read(self.mqtt_state_path,4096,nofollow=True))
            schema1={'schema','enabled','host','port','client_id','control_custom','bulk_custom'}
            schema2=schema1|{'transport','websocket_path'}
            schema3=schema2|{'tls'}
            if not isinstance(value,dict) or type(value.get('schema')) is not int:
                return defaults
            if value['schema']==1 and set(value)==schema1:
                settings={key:value[key] for key in ('enabled','host','port','client_id','control_custom','bulk_custom')}
                settings.update(transport='tcp',websocket_path='/mqtt',tls=defaults['tls'])
            elif value['schema']==2 and set(value)==schema2:
                settings={key:value[key] for key in ('enabled','host','port','client_id','transport','websocket_path',
                                                      'control_custom','bulk_custom')}
                settings['tls']=defaults['tls']
            elif value['schema']==3 and set(value)==schema3:
                settings={key:value[key] for key in ('enabled','host','port','client_id','transport','websocket_path',
                                                      'tls','control_custom','bulk_custom')}
            else:return defaults
            if (type(settings['enabled']) is not bool or type(settings['tls']) is not bool or
                type(settings['control_custom']) is not bool or type(settings['bulk_custom']) is not bool or
                settings['transport'] not in ('tcp','websocket')):
                return defaults
            self._validate_mqtt_endpoint(settings['host'],settings['port'],settings['client_id'])
            validate_websocket_path(settings['websocket_path'])
            if settings['enabled'] and not self._mqtt_transport_ready(settings): return defaults
            return settings
        except (OSError,KeyError,TypeError,ValueError): return defaults
    def _resolved_mqtt_config(self,settings):
        m=dict(self.cfg['mqtt'])
        m.update(enabled=settings['enabled'],host=settings['host'],port=settings['port'],client_id=settings['client_id'],
                 transport=settings['transport'],websocket_path=settings['websocket_path'],tls=settings['tls'])
        for channel in ('control','bulk'):
            path=self._mqtt_credentials_path(channel,settings)
            m[f'{channel}_credentials_file']=str(path) if path else None
        return m
    def _new_mqtt_clients(self,settings):
        if not self.cfg['mqtt']['enabled']: return {}
        m=self.cfg['mqtt']; demo_suffix='-demo' if self.demo else ''
        subs=self._command_subscriptions(); will=self._will()
        epoch=self.link_epoch['primary']
        return {
            'control':Client(cfg=m,client_id=settings['client_id']+'-control'+demo_suffix,
                credentials_file=m['control_credentials_file'],subscriptions=subs,will=will,
                on_message=lambda topic,payload,retained,e=epoch:self._incoming(topic,payload,retained,link='primary',epoch=e),
                rate=self.cfg['telemetry']['control_budget_bytes_s']),
            'bulk':Client(cfg=m,client_id=settings['client_id']+'-bulk'+demo_suffix,
                credentials_file=m['bulk_credentials_file'],rate=self.cfg['telemetry']['bulk_budget_bytes_s'],bulk=True)
        }
    def _command_subscriptions(self):
        return [(self.prefix+'/'+suffix,1,True) for suffix in OPS.values()]
    def _will(self):
        return (self.prefix+'/availability',compact({'v':2,'sid':self.sid,'online':False,'reason':'CONNECTION_LOST'}))
    def mqtt_settings_view(self):
        with self.mqtt_config_lock:
            settings=dict(self.mqtt_desired); m=self.cfg['mqtt'];tls=settings['tls']
            ready={channel:self._credentials_ready(self._mqtt_credentials_path(channel,settings)) for channel in ('control','bulk')}
            ca=bool(m.get('ca_file') and Path(m['ca_file']).is_file())
            trust='plaintext' if not tls else 'custom' if ca else 'unavailable' if m.get('ca_file') else 'system'
            return {'enabled':settings['enabled'],'host':settings['host'],'port':settings['port'],
                    'client_id':settings['client_id'],'transport':settings['transport'],
                    'websocket_path':settings['websocket_path'],'tls':tls,'ca_configured':ca,
                    'trust_mode':trust,'control_credentials_set':ready['control'],'bulk_credentials_set':ready['bulk'],
                    'editable':not self.demo,'apply_pending':self.mqtt_reconfigure.is_set()}
    def configure_mqtt(self,value):
        if self.demo or not isinstance(value,dict) or set(value)!={'enabled','host','port','client_id','transport','tls','websocket_path','control','bulk'}:
            raise ValueError('INVALID_MQTT_SETTINGS')
        if type(value['enabled']) is not bool or type(value['tls']) is not bool: raise ValueError('INVALID_MQTT_SETTINGS')
        if value['transport'] not in ('tcp','websocket'): raise ValueError('INVALID_MQTT_TRANSPORT')
        validate_websocket_path(value['websocket_path'])
        self._validate_mqtt_endpoint(value['host'],value['port'],value['client_id'])
        replacements={}
        for channel in ('control','bulk'):
            account=value[channel]
            if not isinstance(account,dict) or set(account)!={'username','password'}:
                raise ValueError('INVALID_MQTT_CREDENTIALS')
            username=account['username']; password=account['password']
            if not isinstance(username,str) or not isinstance(password,str): raise ValueError('INVALID_MQTT_CREDENTIALS')
            if bool(username)!=bool(password): raise ValueError('INCOMPLETE_MQTT_CREDENTIALS')
            if username:
                try: username_bytes=username.encode('utf-8'); password_bytes=password.encode('utf-8')
                except UnicodeEncodeError: raise ValueError('INVALID_MQTT_CREDENTIALS')
                if len(username_bytes)>512 or len(password_bytes)>512 or '\x00' in username or '\x00' in password:
                    raise ValueError('INVALID_MQTT_CREDENTIALS')
                replacements[channel]={'username':username,'password':password}
        with self.mqtt_config_lock:
            settings={'enabled':value['enabled'],'host':value['host'],'port':value['port'],'client_id':value['client_id'],
                      'transport':value['transport'],'tls':value['tls'],'websocket_path':value['websocket_path'],
                      'control_custom':self.mqtt_desired['control_custom'] or 'control' in replacements,
                      'bulk_custom':self.mqtt_desired['bulk_custom'] or 'bulk' in replacements}
            if settings['enabled'] and not self._mqtt_transport_ready(settings,replacements):
                raise ValueError('MQTT_TLS_OR_CREDENTIALS_UNAVAILABLE')
            for channel,account in replacements.items():
                atomic_write(self.mqtt_credentials_paths[channel],compact(account),0o600)
            atomic_write(self.mqtt_state_path,compact({'schema':3,**settings}),0o600)
            self.mqtt_desired=settings; self.mqtt_pending=settings; self.mqtt_reconfigure.set()
            return self.mqtt_settings_view()
    def _apply_pending_mqtt(self):
        with self.mqtt_apply_lock:
            with self.mqtt_config_lock:
                if not self.mqtt_reconfigure.is_set(): return
                settings=self.mqtt_pending; self.mqtt_pending=None; self.mqtt_reconfigure.clear()
            if settings is None: return
            old=self.clients; self.clients={}
            # Commands still queued from the old primary session are dropped by epoch, not by draining
            # the shared queue (which also carries backup commands).
            self.link_epoch['primary']+=1
            self.cfg['mqtt']=self._resolved_mqtt_config(settings)
            self.clients=self._new_mqtt_clients(settings)
            self._retire(list(old.values()),list(self.clients.values()))
            if self.backup_client and self.backup_client_id!=self._backup_client_id():
                # The backup id derives from the primary base id; reconnect it under the new id.
                with self.mqtt_config_lock:
                    if not self.backup_reconfigure.is_set():
                        self.backup_pending=self.backup_desired; self.backup_reconfigure.set()
            with self.settings_request_lock:
                if self.settings_request_link=='primary':
                    self.settings_request_id=None; self.settings_request_payload=None
            with self.lock:
                self.mqtt_topic_delivery.clear()
            self._generation=-1; self.last_sent_state=None
            self.bulk_resume_after=time.monotonic()+self.cfg['telemetry']['resume_stable_seconds']
            self.bulk_reason='MQTT_RECONFIGURED'
    # ---- backup broker (LAN/online mirror) ----
    BACKUP_KEYS=('enabled','host','port','transport','websocket_path','tls','commands','auth')
    def _backup_defaults(self):
        b=self.cfg['mqtt']['backup']
        return {'enabled':b['enabled'],'host':b['host'] or '','port':b['port'],'transport':b['transport'],
                'websocket_path':b['websocket_path'],'tls':b['tls'],'commands':b['commands'],
                'auth':'config' if b['credentials_file'] else 'none'}
    def _backup_credentials_path(self,settings):
        if settings['auth']=='account': return self.backup_account_path
        if settings['auth']=='config': return self.cfg['mqtt']['backup']['credentials_file']
        return None
    def _backup_transport_ready(self,settings,replacing_account=False):
        ca_file=self.cfg['mqtt']['backup']['ca_file']
        if settings['tls'] and ca_file and not Path(ca_file).is_file(): return False
        path=self._backup_credentials_path(settings)
        if path and not (replacing_account and settings['auth']=='account') and not self._credentials_ready(path): return False
        return True
    def _validate_backup_settings(self,settings):
        for key in ('enabled','tls','commands'):
            if type(settings[key]) is not bool: raise ValueError('INVALID_MQTT_BACKUP_SETTINGS')
        if settings['transport'] not in ('tcp','websocket'): raise ValueError('INVALID_MQTT_TRANSPORT')
        if settings['auth'] not in ('none','account','config'): raise ValueError('INVALID_MQTT_BACKUP_SETTINGS')
        validate_websocket_path(settings['websocket_path'])
        if type(settings['port']) is not int or not 1<=settings['port']<=65535: raise ValueError('INVALID_MQTT_PORT')
        if not isinstance(settings['host'],str): raise ValueError('INVALID_MQTT_HOST')
        if settings['host'] or settings['enabled']: validate_mqtt_host(settings['host'])
    def _load_backup_settings(self):
        defaults=self._backup_defaults()
        if self.demo: return dict(defaults,enabled=False)
        try:
            value=strict_json(stable_read(self.backup_state_path,4096,nofollow=True))
            if not isinstance(value,dict) or value.get('schema')!=1 or set(value)!={'schema',*self.BACKUP_KEYS}:
                return defaults
            settings={key:value[key] for key in self.BACKUP_KEYS}
            self._validate_backup_settings(settings)
            if settings['auth']=='config' and not self.cfg['mqtt']['backup']['credentials_file']: return defaults
            if settings['enabled'] and not self._backup_transport_ready(settings): return defaults
            return settings
        except (OSError,KeyError,TypeError,ValueError): return defaults
    def _backup_client_id(self):
        return self.mqtt_desired['client_id']+'-backup'+('-demo' if self.demo else '')
    def _new_backup_client(self,settings):
        if self.demo or not settings['enabled'] or not settings['host']: return None
        b=self.cfg['mqtt']['backup']; epoch=self.link_epoch['backup']
        # Own flat cfg: never inherit the primary's private CA (public brokers need the system store).
        cfg={'host':settings['host'],'port':settings['port'],'tls':settings['tls'],'ca_file':b['ca_file'],
             'transport':settings['transport'],'websocket_path':settings['websocket_path'],
             'keepalive':self.cfg['mqtt']['keepalive'],'reconnect_max':self.cfg['mqtt']['reconnect_max']}
        path=self._backup_credentials_path(settings)
        self.backup_client_id=self._backup_client_id()
        return Client(cfg=cfg,client_id=self.backup_client_id,credentials_file=str(path) if path else None,
                      subscriptions=self._command_subscriptions() if settings['commands'] else (),will=self._will(),
                      on_message=lambda topic,payload,retained,e=epoch:self._incoming(topic,payload,retained,link='backup',epoch=e),
                      rate=b['budget_bytes_s'])
    def _backup_status(self):
        active=self.backup_active; client=self.backup_client
        status=client.status() if client else {'state':'DISABLED','ready':False,'error':None,'depth':0,'pending_age_ms':0}
        with self.lock: delivery=copy.deepcopy(self.mqtt_backup_topic_delivery)
        return {**status,'enabled':bool(client),'commands':bool(active['commands']),
                'bulk_pause':self.backup_bulk_reason if client else None,
                'angular_aborted':self.backup_angular_abort,'topic_delivery':delivery}
    def _reusable_backup_auth(self):
        current=self.backup_desired['auth']
        candidates=([current] if current in ('account','config') else [])+['account','config']
        for auth in candidates:
            path=self._backup_credentials_path({'auth':auth})
            if path and self._credentials_ready(path): return auth
        return None
    def mqtt_backup_view(self):
        with self.mqtt_config_lock:
            settings=dict(self.backup_desired); ca_file=self.cfg['mqtt']['backup']['ca_file']
            ca=bool(ca_file and Path(ca_file).is_file())
            trust='plaintext' if not settings['tls'] else 'custom' if ca else 'unavailable' if ca_file else 'system'
            # credentials_source: the account a blank 'Pakai akun' save would use (panel or config.yaml).
            source=settings['auth'] if settings['auth']!='none' else self._reusable_backup_auth()
            path=self._backup_credentials_path({'auth':source}) if source else None
            ready=bool(path and self._credentials_ready(path))
            return {'enabled':settings['enabled'],'host':settings['host'],'port':settings['port'],
                    'transport':settings['transport'],'websocket_path':settings['websocket_path'],
                    'tls':settings['tls'],'commands':settings['commands'],
                    'auth':'none' if settings['auth']=='none' else 'account',
                    'credentials_set':ready,
                    'credentials_source':({'account':'panel','config':'config'}[source] if ready else None),
                    'trust_mode':trust,
                    'client_id':self.backup_client_id if self.backup_client else self._backup_client_id(),
                    'editable':not self.demo,
                    'apply_pending':self.backup_reconfigure.is_set()}
    def configure_mqtt_backup(self,value):
        keys={'enabled','host','port','transport','websocket_path','tls','commands','auth','username','password'}
        if self.demo or not isinstance(value,dict) or set(value)!=keys: raise ValueError('INVALID_MQTT_BACKUP_SETTINGS')
        if value['auth'] not in ('none','account'): raise ValueError('INVALID_MQTT_BACKUP_SETTINGS')
        username=value['username']; password=value['password']
        if not isinstance(username,str) or not isinstance(password,str): raise ValueError('INVALID_MQTT_CREDENTIALS')
        settings={key:value[key] for key in self.BACKUP_KEYS}
        self._validate_backup_settings(settings)
        account=None
        if value['auth']=='none':
            if username or password: raise ValueError('INVALID_MQTT_CREDENTIALS')
        else:
            if bool(username)!=bool(password): raise ValueError('INCOMPLETE_MQTT_CREDENTIALS')
            if username:
                try: username_bytes=username.encode('utf-8'); password_bytes=password.encode('utf-8')
                except UnicodeEncodeError: raise ValueError('INVALID_MQTT_CREDENTIALS')
                if len(username_bytes)>512 or len(password_bytes)>512 or '\x00' in username or '\x00' in password:
                    raise ValueError('INVALID_MQTT_CREDENTIALS')
                account={'username':username,'password':password}
        with self.mqtt_config_lock:
            if value['auth']=='account' and account is None:
                # Blank pair reuses an existing account: the one in use, else the panel's, else config.yaml's.
                settings['auth']=self._reusable_backup_auth() or 'account'
            if settings['enabled']:
                ca_file=self.cfg['mqtt']['backup']['ca_file']
                if settings['tls'] and ca_file and not Path(ca_file).is_file(): raise ValueError('MQTT_BACKUP_TLS_CA_UNAVAILABLE')
                if not self._backup_transport_ready(settings,replacing_account=account is not None):
                    raise ValueError('MQTT_BACKUP_CREDENTIALS_REQUIRED')
            if account is not None:
                atomic_write(self.backup_account_path,compact(account),0o600)
            atomic_write(self.backup_state_path,compact({'schema':1,**settings}),0o600)
            self.backup_desired=settings; self.backup_pending=settings; self.backup_reconfigure.set()
            return self.mqtt_backup_view()
    def _apply_pending_backup(self):
        with self.mqtt_apply_lock:
            with self.mqtt_config_lock:
                if not self.backup_reconfigure.is_set(): return
                settings=self.backup_pending; self.backup_pending=None; self.backup_reconfigure.clear()
            if settings is None: return
            old=self.backup_client; self.backup_client=None
            self.link_epoch['backup']+=1
            self.backup_active=settings
            self.backup_client=self._new_backup_client(settings)
            # Tell consumers on the old backup broker that this node left it (a clean DISCONNECT drops the Will).
            farewell={'v':2,'sid':self.sid,'online':False,'t':now_ms(),
                      'reason':'BACKUP_RECONFIGURED' if self.backup_client else 'BACKUP_DISABLED'}
            self._retire([old],[self.backup_client],farewell=farewell if old and old.ready else None)
            with self.settings_request_lock:
                if self.settings_request_link=='backup':
                    self.settings_request_id=None; self.settings_request_payload=None
            with self.lock:
                self.mqtt_backup_topic_delivery.clear()
            self._backup_generation=-1; self.backup_last_angular_q=0
            self.backup_bulk_reason='MQTT_RECONFIGURED' if self.backup_client else None
    def _retire(self,old,new,farewell=None):
        # Stop replaced clients and start their successors off the scheduler thread, so a client stuck in
        # connect/TLS/DNS on one link never pauses publishing on the other. Successors start only after the
        # old session is gone, so the same client id never overlaps on one broker.
        old=[c for c in old if c]; new=[c for c in new if c]
        if farewell:
            for c in old: c.offer('offline',self.prefix+'/availability',farewell,qos=1,retain=True,priority=0)
        def run():
            if farewell:
                deadline=time.monotonic()+2
                while time.monotonic()<deadline and any(c.connected and (c.outbox.data or c.pending_age_ms) for c in old):
                    time.sleep(.05)
            for c in old: c.stop_event.set()
            for c in old: c.stop()
            with self.mqtt_apply_lock:
                if self.mqtt_running:
                    for c in new:
                        if c is self.backup_client or any(c is x for x in self.clients.values()): c.start()
        if self.mqtt_running and (old or new):
            threading.Thread(target=run,name='mqtt-retire',daemon=True).start()
        else:
            for c in old: c.stop()
    def start(self):
        self._apply_pending_mqtt(); self._apply_pending_backup()
        with self.mqtt_apply_lock:
            self.mqtt_running=True
            self.monitor.start(); self.commands.start()
            for client in self.clients.values(): client.start()
            if self.backup_client: self.backup_client.start()
            for fn,name in ((self._collect,'rdf-collector'),(self._schedule,'rdf-scheduler'),(self._events,'rdf-inbound')):
                t=threading.Thread(target=fn,name=name,daemon=True); self.threads.append(t); t.start()
    def stop(self):
        self.stop_event.set()
        self.commands.stop()
        with self.mqtt_apply_lock:
            self.mqtt_running=False
            for c in self.clients.values(): c.stop()
            if self.backup_client: self.backup_client.stop()
        self.monitor.stop()
        for t in self.threads: t.join(3)
        # A long lifecycle worker may still be reconciling; daemon exits without replay.
        if not self.commands.thread.is_alive(): self.journal.close()
    def _incoming(self,topic,payload,retained,link='primary',epoch=None):
        if len(payload)>4096: return
        # Keep half of the queue for the primary (Ground) link.
        if link=='backup' and self.events.qsize()>=self.events.maxsize//2: return
        try: self.events.put_nowait((link,epoch,topic,payload,retained))
        except queue.Full: pass
    def _events(self):
        while not self.stop_event.is_set():
            try: link,epoch,topic,data,retained=self.events.get(timeout=.2)
            except queue.Empty: continue
            try:
                if epoch is not None and epoch!=self.link_epoch.get(link): continue
                j=strict_json(data)
                self.commands.submit(j,'ground-controller',retained=retained,topic=topic,link=link)
            except (ValueError,TypeError,KeyError,RecursionError): pass
            finally: self.events.task_done()
    def _collect(self):
        count=0
        while not self.stop_event.is_set():
            try:
                self.source.poll()
                if count%20==0 and not self.demo:
                    try: self.helper_status=call_helper(self.cfg['control']['helper_socket'],{'op':'status'},timeout=1)
                    except Exception: self.helper_status={}
                self._snapshot()
                if count%240==0: self.journal.prune()
            except Exception as e:
                with self.lock:
                    self.snapshot_data['collector_error']=type(e).__name__
            count+=1; self.stop_event.wait(self.cfg['source']['poll_ms']/1000)
    def _snapshot(self):
        host=self.monitor.snapshot()
        if self.demo: host['clock_trusted']=True; host['clock_state']='DEMO'
        view=self.source.view(host['clock_trusted'],self.commands.busy)
        diagnostic=self.source.diagnostic_view(view['reasons'])
        processing='UNKNOWN'
        if host['service_state']=='ACTIVE': processing='RUNNING'
        elif host['service_state']=='FAILED': processing='ERROR'
        elif host['service_state']=='INACTIVE': processing='STOPPED'
        elif self.source.status and view['daq']['source_age_ms'] is not None and view['daq']['source_age_ms']<3000:
            processing='RUNNING'
        if self.commands.busy:
            latest=self.journal.lookup(self.commands.active_id)
            if latest and latest['request']['op'] in ('processing.set','service.restart'):
                processing='STOPPING' if latest['request'].get('desired')=='STOPPED' else 'STARTING'
        ctrl=self.clients.get('control'); bulk=self.clients.get('bulk')
        cs=ctrl.status() if ctrl else {'state':'DISABLED','ready':False,'depth':0,'pending_age_ms':0}
        bs=bulk.status() if bulk else {'state':'DISABLED','ready':False,'depth':0,'pending_age_ms':0}
        with self.lock: topic_delivery=copy.deepcopy(self.mqtt_topic_delivery)
        # A read-only source can prove VFO frequency but not every native parameter.
        if view['valid'] and self.config_proof=='unverified': self.config_proof='source_correlated'
        if self.source.revision!=self.last_config_rev:
            self.config_proof='source_correlated' if view['valid'] else 'unverified'
            self.last_config_rev=self.source.revision
        self._watch_link(host)
        latest=self.journal.latest(1,READ_OPS)
        remote=self.journal.latest(1,READ_OPS,links=('ground','backup'))
        alerts=[]
        if self.demo: alerts.append({'severity':'warning','code':'DEMO','text':'DEMO - bukan data perangkat'})
        if not self.source.path: alerts.append({'severity':'warning','code':'SETUP_REQUIRED','text':'Pilih folder output SDR terlebih dahulu'})
        if host.get('undervoltage'): alerts.append({'severity':'error','code':'UNDERVOLTAGE','text':'Tegangan rendah: periksa power'})
        if host.get('temperature_c') is not None and host['temperature_c']>=80: alerts.append({'severity':'error','code':'HOT','text':'Suhu tinggi: periksa pendinginan'})
        if host.get('disk_free_percent') is not None and host['disk_free_percent']<5: alerts.append({'severity':'error','code':'DISK_LOW','text':'Storage hampir penuh'})
        if processing=='RUNNING' and view['daq']['state']!='HEALTHY': alerts.append({'severity':'error','code':'DAQ_DEGRADED','text':'DAQ belum sehat / data tidak valid'})
        if not view['valid'] and view['reasons']: alerts.append({'severity':'warning','code':view['reasons'][0],'text':view['reasons'][0]})
        if self.source.error: alerts.append({'severity':'warning','code':'SOURCE_READ','text':self.source.error})
        self.snapshot_seq+=1
        snap={'schema_version':2,'version':__version__,'mode':'DEMO' if self.demo else 'LIVE',
              'node_id':self.cfg['node_id'],'sid':self.sid,'boot_id':self.boot,'agent_instance_id':self.instance,
              'snapshot_seq':self.snapshot_seq,'snapshot_ms':now_ms(),
              'processing':{'observed':processing,'desired':self.helper_status.get('desired'),'scope':'SDR_STACK'},
              'detection':view,'diagnostic_doa':diagnostic,'daq':view['daq'],'host':host,
              'link':{'usb':host['usb'],'ppp':host['ppp'],'interface':host['interface'],
                      'ppp_probe':host['ppp_probe'],'ppp_peer':self.cfg['link']['peer_ip'],
                      'mqtt_control':cs,'mqtt_bulk':bs,'mqtt_topic_delivery':topic_delivery,
                      'mqtt_backup':self._backup_status(),
                      'ppp_service':{k:v for k,v in (host.get('ppp_service') or {}).items() if k!='since'} or None,
                      'ppp_recovery':self._ppp_recovery_view(),
                      'tx_kbit_s':host['tx_kbit_s'],'rx_kbit_s':host['rx_kbit_s'],
                      'traffic_layer':'PPP_IP_COUNTERS','profile':self.profile,'bulk_pause':self.bulk_reason,'angular_aborted':self.angular_abort},
              'config':self.config_view(),'capabilities':self.capabilities(),
              'last_operation':self.commands.public(latest[0]) if latest else None,
              'last_remote_operation':self.commands.public(remote[0]) if remote else None,
              'operation_seq':self.operation_seq,'remote_rejects':list(self.remote_rejects),'active_alerts':alerts}
        with self.lock: self.snapshot_data=snap
    def snapshot(self):
        with self.lock: return copy.deepcopy(self.snapshot_data)
    def config_view(self):
        return {'sdr_revision':self.source.revision,'digest':self.source.safe_digest,'proof':self.config_proof,
                'safe_settings':dict(self.source.safe),
                'profile':self.profile,'source_configured':self.source.path is not None,
                'authority_verified':self.cfg['source']['authority_verified'],'angle_verified':self.cfg['source']['angle_verified'],
                'mqtt_configured':self.cfg['mqtt']['enabled'],
                'mqtt_backup_configured':bool(self.backup_active['enabled'] and self.backup_active['host']),'preferences':self.journal.get('display',self.cfg['display'])}
    def capabilities(self):
        c=self.cfg['control']
        remote=not self.demo
        helper_available=bool(self.helper_status)
        settings_target=self.helper_status.get('settings_target_available') is True
        lifecycle_target=self.helper_status.get('lifecycle_target_available') is True
        return {'v':2,'sid':self.sid,'boot':self.boot,'instance':self.instance,'version':__version__,
                'mode':'DEMO' if self.demo else self.cfg['runtime_mode'],'codecs':['json'],
                'angle':self.cfg['source']['angle_mode'],'native_axis':1,'count':360,
                'profiles':list(PROFILES),'scope':'SDR_STACK','helper_available':helper_available,
                'remote_commands':remote,
                'remote_config_patch':remote and settings_target,
                'remote_processing':remote and lifecycle_target,
                'remote_restart':remote and lifecycle_target,
                'remote_reboot':remote and helper_available,
                'remote_shutdown':remote and helper_available,
                'remote_ppp_restart':remote and helper_available,
                'config_patch':c['config_patch_enabled'] and self.helper_status.get('allow_config',False),
                'processing':c['processing_enabled'] and self.helper_status.get('allow_lifecycle',False),
                'restart':c['restart_enabled'] and self.helper_status.get('allow_lifecycle',False),
                'reboot':c['reboot_enabled'] and self.helper_status.get('allow_reboot',False),
                'shutdown':c['shutdown_enabled'] and self.helper_status.get('allow_shutdown',False),
                'ppp_restart':self.cfg['runtime_mode']=='controlled' and c['ppp_restart_enabled'] and
                    self.helper_status.get('allow_ppp_restart',False)}
    def _topic_delivery_update(self,suffix,state,qos,error=None,link='primary'):
        timestamp=now_ms()
        if error is not None and (not isinstance(error,str) or not re.fullmatch(r'[A-Z0-9_]{1,64}',error)):
            error='PUBLISH_FAILED'
        delivery=self.mqtt_backup_topic_delivery if link=='backup' else self.mqtt_topic_delivery
        with self.lock:
            previous=delivery.get(suffix,{})
            delivery[suffix]={
                'state':state,'qos':qos,'confirmation':'PUBACK' if qos==1 else 'SOCKET_WRITE',
                'updated_ms':timestamp,'sent_ms':timestamp if state=='SENT' else previous.get('sent_ms'),
                'error':error}
    def _offer(self,key,suffix,payload,qos=0,retain=False,expiry=None,priority=2,bulk=False,links=None):
        # Mirror to every requested link (default: primary and backup); True when any link accepted it.
        targets=('primary','backup') if links is None else links
        clients=[]
        for link in targets:
            client=self.backup_client if link=='backup' else self.clients.get('bulk' if bulk else 'control')
            if client: clients.append((link,client))
        if not clients: return False
        data=payload if isinstance(payload,bytes) else compact(payload)
        accepted=False
        def current(link,client):
            return client is (self.backup_client if link=='backup' else self.clients.get('bulk' if bulk else 'control'))
        for link,client in clients:
            self._topic_delivery_update(suffix,'PENDING',qos,link=link)
            # A retired client finishing in the background must not write into the new session's map.
            def delivered(link=link,client=client):
                if current(link,client): self._topic_delivery_update(suffix,'SENT',qos,link=link)
            def failed(reason,link=link,client=client):
                if current(link,client): self._topic_delivery_update(suffix,'ERROR',qos,error=reason,link=link)
            if client.offer(key,self.prefix+'/'+suffix,data,qos=qos,retain=retain,expiry=expiry,
                            priority=priority,on_sent=delivered,on_error=failed):
                accepted=True
        return accepted
    def operation_changed(self,id,op,stage,result,link=None):
        self.operation_seq+=1
        suffix='ack/config' if op.startswith('config.') else 'ack/operation'
        # Commands from the backup broker are answered there only, so they never use the T900 budget.
        self._offer('op:'+id,suffix,{'v':2,'sid':self.sid,'id':id,'status':stage,'t':now_ms(),'rev':self.source.revision,'result':result},
                    qos=1,expiry=30,priority=0,links=('backup',) if link=='backup' else None)
    # ---- PPP/T900 link recovery ----
    PPP_RECOVERY_TIMEOUT_S=120; PPP_RECOVERY_LINGER_S=90
    def remote_rejected(self,origin,op,error):
        self.remote_rejects.appendleft({'t':now_ms(),'origin':origin,'op':op,'error':error})
    def ppp_recovery_active(self):
        r=self.ppp_recovery
        return bool(r and r['phase'] not in ('DONE','TIMEOUT','FAILED'))
    def ppp_recovery_begin(self,id,origin,link):
        host=self.monitor.snapshot(); ctrl=self.clients.get('control'); svc=host.get('ppp_service') or {}
        fast=getattr(self.monitor,'fast_probe',None)
        if fast: fast(self.PPP_RECOVERY_TIMEOUT_S+30)
        with self.lock:
            self.ppp_recovery={'id':id,'origin':origin,'link':link,'phase':'REQUESTED','stage':'PPP_RESTART_REQUESTED',
                'reason':None,'started_ms':now_ms(),'started':time.monotonic(),'finished':None,
                'steps':{'requested':True,'cycled':False,'ppp_up':False,'peer_reply':False,
                         'mqtt_ready':None if ctrl is None else False},
                'gen0':ctrl.generation if ctrl else None,'svc0':(svc.get('since'),svc.get('restarts')),'rx_at_up':None}
    def ppp_recovery_failed(self,id,origin,stage,error):
        with self.lock:
            self.ppp_recovery={'id':id,'origin':origin,'link':None,'phase':'FAILED','stage':stage,'reason':error,
                'started_ms':now_ms(),'started':time.monotonic(),'finished':time.monotonic(),
                'steps':{'requested':False,'cycled':False,'ppp_up':False,'peer_reply':False,'mqtt_ready':None},
                'gen0':None,'svc0':(None,None),'rx_at_up':None}
    def _ppp_recovery_view(self):
        r=self.ppp_recovery
        if not r: return None
        end=r['finished'] or time.monotonic()
        return {'id':r['id'],'origin':r['origin'],'phase':r['phase'],'steps':dict(r['steps']),'stage':r['stage'],
                'reason':r['reason'],'started_ms':r['started_ms'],'elapsed_s':round(end-r['started'],1),
                'timeout_s':self.PPP_RECOVERY_TIMEOUT_S,'late':bool(r.get('late'))}
    def _kick_primary(self):
        for client in self.clients.values():
            kick=getattr(client,'kick',None)
            if kick: kick()
    def _watch_link(self,host):
        ppp=host.get('ppp'); probe=host.get('ppp_probe')
        # PPP came back: reconnect MQTT now instead of sitting out the remaining backoff.
        if (self._last_ppp is not None and self._last_ppp!='UP' and ppp=='UP') or \
           (self._last_probe is not None and self._last_probe!='REPLY' and probe=='REPLY'):
            self._kick_primary()
        self._last_ppp=ppp; self._last_probe=probe
        r=self.ppp_recovery
        if not r: return
        now=time.monotonic()
        if r['finished']:
            if now-r['finished']>self.PPP_RECOVERY_LINGER_S: self.ppp_recovery=None; return
            # A timed-out recovery keeps watching while it lingers, so a late recovery is shown too.
            if r['phase']!='TIMEOUT': return
        steps=r['steps']; ctrl=self.clients.get('control'); svc=host.get('ppp_service') or {}
        cycled=(steps['cycled'] or ppp!='UP' or (svc.get('since'),svc.get('restarts'))!=r['svc0'] or
                (ctrl is not None and r['gen0'] is not None and ctrl.generation!=r['gen0']))
        steps['cycled']=cycled
        if cycled and ppp=='UP':
            if not steps['ppp_up']:
                steps['ppp_up']=True; r['rx_at_up']=ctrl.rx_bytes if ctrl else None
        if steps['ppp_up'] and probe=='REPLY': steps['peer_reply']=True
        if ctrl is None: steps['mqtt_ready']=None
        elif steps['ppp_up'] and ctrl.ready and (ctrl.generation!=r['gen0'] or
                (r['rx_at_up'] is not None and ctrl.rx_bytes>r['rx_at_up'])):
            # A fresh session, or broker traffic received after PPP returned, proves MQTT over the new link.
            steps['mqtt_ready']=True
        progress=('PEER_OK' if steps['peer_reply'] else 'PPP_UP' if steps['ppp_up'] else
                  'RESTARTING' if cycled else 'REQUESTED')
        done=steps['peer_reply'] and steps['mqtt_ready'] is not False
        if r['finished']:
            # The journal and ACK keep OUTCOME_UNKNOWN; only the panel view turns green.
            if done: r['phase']='DONE'; r['late']=True; r['finished']=now
            return
        r['phase']=progress
        timed_out=now-r['started']>self.PPP_RECOVERY_TIMEOUT_S
        if not (done or timed_out): return
        elapsed=round((now-r['started'])*1000)
        if done:
            stage='APPLIED'; result={'proof':{'ppp':'UP','peer':'REPLY',
                'mqtt':'READY' if steps['mqtt_ready'] else 'DISABLED','cycled':True},'elapsed_ms':elapsed}
            r['phase']='DONE'
        else:
            stage='OUTCOME_UNKNOWN'; r['phase']='TIMEOUT'
            # Name the most likely cause from what the Edge already observed.
            r['reason']=('T900_USB_MISSING' if host.get('usb')=='MISSING' else
                         'MQTT_NOT_RECOVERED' if steps['peer_reply'] else
                         'PPP_RECOVERY_TIMEOUT' if cycled else 'PPP_CYCLE_NOT_OBSERVED')
            result={'reason':r['reason'],'phase':progress,'steps':dict(steps),'elapsed_ms':elapsed}
        r['stage']=stage; r['finished']=now
        try:
            self.journal.update(r['id'],stage,result)
            self.operation_changed(r['id'],'ppp.restart',stage,result,link=r['link'])
        except Exception: pass
    def link_tls(self,link):
        if link=='backup': return bool(self.backup_client and self.backup_active['tls'])
        return bool(self.cfg['mqtt']['tls'])
    def _state(self,s):
        return {'v':2,'sid':self.sid,'boot':self.boot,'instance':self.instance,'t':now_ms(),
                'run':s.get('processing',{}).get('observed','UNKNOWN'),'daq':s.get('daq',{}).get('healthy',False),
                'cfg':self.source.revision,'profile':self.profile,'clock':s.get('host',{}).get('clock_state','UNTRUSTED')}
    def _angular_flags(self,d,r,h,daq):
        flags=FLAG_PARSED
        if (h['clock_trusted'] and d['source_age_ms'] is not None and
                d['source_age_ms']<=self.cfg['freshness']['doa_ms'] and
                r['timestamp_ms']<=now_ms()+1000):
            flags|=FLAG_FRESH
        if daq['healthy']: flags|=FLAG_DAQ
        if self.cfg['source']['angle_verified']: flags|=FLAG_CONVENTION
        if d['config_attributed']: flags|=FLAG_CONFIG
        if self.cfg['source']['authority_verified']: flags|=FLAG_AUTHORITY
        return flags
    def _live_angular(self,d,r,h,daq):
        return compact({'v':2,'encoding':'json','sid':self.sid,'q':r['q'],
            'timestamp_ms':r['timestamp_ms'],'frequency_hz':r['frequency_hz'],
            'revision':d['revision'],'vfo':self.cfg['source']['output_vfo'],
            'convention':1,'flags':self._angular_flags(d,r,h,daq),'raw_doa_deg':r['raw_doa_deg'],
            'confidence_native_db':r['confidence_native_db'],'values':r['values']})
    def _diagnostic_angular(self,d,r,h,daq):
        self.diagnostic_angular_q+=1
        if self.diagnostic_angular_q>0xffffffff:
            raise RuntimeError('DIAGNOSTIC_ANGULAR_SEQUENCE_EXHAUSTED_RESTART_AGENT')
        return compact({'v':2,'encoding':'json','sid':self.sid,'q':self.diagnostic_angular_q,
            'timestamp_ms':r['timestamp_ms'],'frequency_hz':r['frequency_hz'],
            'revision':d['revision'] if d['config_attributed'] else None,
            'vfo':self.cfg['source']['output_vfo'],'convention':1,'flags':self._angular_flags(d,r,h,daq),
            'raw_doa_deg':r['raw_doa_deg'],'confidence_native_db':r['confidence_native_db'],
            'values':r['values']})
    def _schedule(self):
        due={}
        while not self.stop_event.is_set():
            try:
                self._apply_pending_mqtt(); self._apply_pending_backup()
                s=self.snapshot(); now=time.monotonic(); ctrl=self.clients.get('control'); bulk=self.clients.get('bulk')
                bk=self.backup_client
                p_ready=bool(ctrl and ctrl.ready); b_ready=bool(bk and bk.ready)
                if not p_ready:
                    if ctrl: ctrl.outbox.discard('diagnostic-angular','MQTT_NOT_READY')
                    self.bulk_reason='MQTT_NOT_READY'
                if not b_ready:
                    if bk: bk.outbox.discard('diagnostic-angular','MQTT_NOT_READY')
                    self.backup_bulk_reason='MQTT_NOT_READY' if bk else None
                # The backup keeps receiving while the primary (PPP/T900) is down, and vice versa.
                if not s or not (p_ready or b_ready):
                    self.stop_event.wait(.1); continue
                ready=tuple(link for link,ok in (('primary',p_ready),('backup',b_ready)) if ok)
                if p_ready and ctrl.generation!=self._generation:
                    self._generation=ctrl.generation
                    self.bulk_resume_after=now+self.cfg['telemetry']['resume_stable_seconds']
                    self._offer('available','availability',{'v':2,'sid':self.sid,'online':True,'t':now_ms()},1,True,priority=0,
                                links=('primary',))
                    self._offer('caps','capabilities',self.capabilities(),1,True,priority=3,links=('primary',))
                    due['state']=0; due['diagnostic-angular-invalid']=0; due['diagnostic-angular-blocked']=0
                backup_hello=False
                if b_ready and bk.generation!=self._backup_generation:
                    # Backup reconnect republishes its own retained topics without touching the T900 schedule.
                    self._backup_generation=bk.generation; backup_hello=True
                    self._offer('available','availability',{'v':2,'sid':self.sid,'online':True,'t':now_ms()},1,True,priority=0,
                                links=('backup',))
                    self._offer('caps','capabilities',self.capabilities(),1,True,priority=3,links=('backup',))
                    due['diagnostic-angular-invalid:backup']=0; due['diagnostic-angular-blocked:backup']=0
                def tick(key,seconds):
                    if now>=due.get(key,0): due[key]=now+seconds; return True
                    return False
                d=s['detection']; h=s['host']; daq=s['daq']; run=s['processing']['observed']
                unverified_only=(not d['valid'] and bool(d['reasons']) and
                                 all(reason in UNVERIFIED_REASONS for reason in d['reasons']))
                if tick('health',1):
                    self.hq+=1
                    payload={'v':2,'sid':self.sid,'q':self.hq,'t':now_ms(),
                             'run':{'STOPPED':0,'RUNNING':1,'STARTING':2,'STOPPING':3,'ERROR':4}.get(run,255),
                             'daq':1 if daq['healthy'] else 2 if daq['state']=='UNKNOWN' else 0,
                             'drop':daq['dropped_frames'],'age':d['source_age_ms'],'temp':h['temperature_c'],
                             'clk':1 if h['clock_trusted'] else 0,'rev':self.source.revision}
                    self._offer('health','telemetry/health',payload,expiry=5,priority=1,links=ready)
                if tick('detail',10):
                    self._offer('detail','telemetry/health/detail',{'v':2,'sid':self.sid,'t':now_ms(),
                        'usb':h['usb_count'],'sync':list(daq['sync'].values()),'cpu':h['cpu_percent'],'mem':h['memory_percent'],
                        'disk_free':h['disk_free_percent'],'throt':h['throttled'],'uv':h['undervoltage'],
                        'tx':h['tx_kbit_s'],'rx':h['rx_kbit_s'],'adrop':self.angular_abort,'parse':self.source.parse_errors,
                        'ppp':h.get('ppp'),'peer':h.get('ppp_probe'),
                        'ppp_svc':(h.get('ppp_service') or {}).get('state'),'ppp_restarts':(h.get('ppp_service') or {}).get('restarts')},
                        expiry=15,priority=4,links=ready)
                state=self._state(s); state_changed=(state['run'],state['daq'],state['cfg'],state['profile'],state['clock'])
                state_sent=False
                if (state_changed!=self.last_sent_state and now-due.get('state_event_last',-10)>1) or now>=due.get('state',0):
                    if self._offer('state','state',state,1,True,priority=1,links=ready):
                        due['state']=now+60; due['state_event_last']=now; self.last_sent_state=state_changed
                        state_sent='backup' in ready
                if backup_hello and not state_sent:
                    self._offer('state','state',state,1,True,priority=1,links=('backup',))
                with self.settings_request_lock:
                    # A report for a session that dropped can never be delivered; free the shared slot so the
                    # link that is still up can ask. (Primary-only deployments never reach this while down.)
                    if self.settings_request_id and (self.settings_request_link or 'primary') not in ready:
                        self.settings_request_id=None; self.settings_request_payload=None
                    settings_id=self.settings_request_id; settings_payload=self.settings_request_payload
                    settings_link=self.settings_request_link or 'primary'
                # Raw settings only travel over TLS, and only back to the link that asked for them.
                if settings_id and settings_payload is not None and settings_link in ready and self.link_tls(settings_link):
                    if self._offer('settings:'+settings_id,'settings/reported',settings_payload,
                                   1,False,expiry=30,priority=3,links=(settings_link,)):
                        with self.settings_request_lock:
                            if self.settings_request_id==settings_id:
                                self.settings_request_id=None; self.settings_request_payload=None
                if tick('doa',PROFILES[self.profile]['doa_s']) and d['q']!=self.last_doa_q:
                    if d['valid']:
                        payload={'v':2,'sid':self.sid,'q':d['q'],'t':d['source_timestamp_ms'],'f':d['frequency_hz'],
                                 'a':round(d['relative_doa_deg'],2),'c':round(d['confidence_native_db'],2),
                                 'p':round(d['power_native_db'],2),'rev':d['revision'],'ok':1}
                    elif unverified_only:
                        payload={'v':2,'sid':self.sid,'q':d['q'],'t':d['source_timestamp_ms'],'f':d['frequency_hz'],
                                 'a':round(d['raw_doa_deg'],2),'c':round(d['confidence_native_db'],2),
                                 'p':round(d['power_native_db'],2),'rev':d['revision'],'ok':0,
                                 'trust':'UNVERIFIED','angle_reference':'RAW',
                                 'validation_reasons':list(d['reasons'])}
                    else:
                        payload=None
                    if payload is not None and self._offer('doa','telemetry/doa',payload,expiry=3,links=ready):
                        self.last_doa_q=d['q']
                diag=s['diagnostic_doa']
                if diag['available'] and tick('diagnostic',3):
                    self.diagnostic_q+=1
                    if self.diagnostic_q>0xffffffff: raise RuntimeError('DIAGNOSTIC_SEQUENCE_EXHAUSTED_RESTART_AGENT')
                    payload={'v':2,'sid':self.sid,'q':self.diagnostic_q,'source':'doa.xml',
                             'source_timestamp_ms':diag['source_timestamp_ms'],
                             'observed_timestamp_ms':diag['observed_timestamp_ms'],
                             'raw_doa_deg':round(diag['raw_doa_deg'],2),
                             'frequency_mhz':round(diag['frequency_mhz'],6),
                             'trust':'UNVERIFIED','validation_reasons':diag['validation_reasons']}
                    self._offer('diagnostic-doa','telemetry/diagnostic/doa',payload,expiry=3,priority=3,links=ready)
                # Source-level reasons stop angular on every link; the rest are T900-link specific.
                source_reason=None
                if self.profile=='control': source_reason='PROFILE_CONTROL'
                elif not d['valid'] and not unverified_only: source_reason='SOURCE_NOT_ELIGIBLE'
                elif self.commands.busy: source_reason='CONTROL_IN_PROGRESS'
                r=self.source.record
                diagnostic_period=6 if not d['valid'] else 30
                diagnostic_schedule='diagnostic-angular-invalid' if not d['valid'] else 'diagnostic-angular-blocked'
                if p_ready:
                    reason=source_reason
                    if reason is None and (not bulk or not bulk.ready): reason='BULK_NOT_READY'
                    elif reason is None and (getattr(ctrl,'queue_age_ms',0)>3000 or ctrl.pending_age_ms>2500):
                        reason='CONTROL_BACKLOG'
                    diagnostic_needed=reason is not None or now<self.bulk_resume_after
                    if (self.profile!='control' and diagnostic_needed and r and r['q']==d['q'] and
                            tick(diagnostic_schedule,diagnostic_period)):
                        data=self._diagnostic_angular(d,r,h,daq)
                        control_baseline=(ANGULAR_CONTROL_BASELINE_VALID
                            if d['valid'] or unverified_only else ANGULAR_CONTROL_BASELINE_INVALID)
                        control_available=max(1,self.cfg['telemetry']['control_budget_bytes_s']-control_baseline)
                        period=max(diagnostic_period,
                            (len(data)+len(self.prefix+'/telemetry/diagnostic/angular')+160)/
                            control_available)
                        due[diagnostic_schedule]=now+period
                        self._offer('diagnostic-angular','telemetry/diagnostic/angular',data,
                                    expiry=3,priority=3,links=('primary',))
                    if self.profile=='control' or not diagnostic_needed:
                        ctrl.outbox.discard('diagnostic-angular','DIAGNOSTIC_NOT_NEEDED')
                    if reason:
                        if bulk: bulk.outbox.clear('BULK_PAUSED_'+reason)
                        self.bulk_reason=reason
                        self.bulk_resume_after=now+self.cfg['telemetry']['resume_stable_seconds']
                    elif now<self.bulk_resume_after:
                        self.bulk_reason='WAIT_STABLE'
                    else:
                        self.bulk_reason=None
                        interval=PROFILES[self.profile]['angular_s']
                        # Control goes first on the shared link: hold the next frame while a Control PUBACK
                        # is late or the previous Bulk frame is still unACKed in the kernel (no 20 s pause).
                        backlog=getattr(bulk,'socket_backlog',None)
                        yield_link=(ctrl.pending_age_ms>BULK_YIELD_PENDING_MS or
                                    (backlog is not None and backlog>BULK_YIELD_BACKLOG_BYTES))
                        if not yield_link and now>=due.get('angular',0) and d['q']!=self.last_angular_q:
                            r=self.source.record
                            if r and r['q']==d['q']:
                                data=self._live_angular(d,r,h,daq)
                                period=max(interval,
                                    (len(data)+len(self.prefix+'/telemetry/angular')+160)/
                                    self.cfg['telemetry']['bulk_budget_bytes_s'])
                                if self._offer('angular','telemetry/angular',data,expiry=3,
                                               priority=3,bulk=True,links=('primary',)):
                                    self.last_angular_q=r['q']
                                else:
                                    self.angular_abort+=1
                                due['angular']=now+period
                if b_ready:
                    budget=self.cfg['mqtt']['backup']['budget_bytes_s']
                    b_schedule=diagnostic_schedule+':backup'
                    if (self.profile!='control' and source_reason is not None and r and r['q']==d['q'] and
                            tick(b_schedule,diagnostic_period)):
                        data=self._diagnostic_angular(d,r,h,daq)
                        due[b_schedule]=now+max(diagnostic_period,
                            (len(data)+len(self.prefix+'/telemetry/diagnostic/angular')+160)/budget)
                        self._offer('diagnostic-angular','telemetry/diagnostic/angular',data,
                                    expiry=3,priority=3,links=('backup',))
                    if self.profile=='control' or source_reason is None:
                        bk.outbox.discard('diagnostic-angular','DIAGNOSTIC_NOT_NEEDED')
                    if source_reason:
                        bk.outbox.discard('angular','BULK_PAUSED_'+source_reason)
                        self.backup_bulk_reason=source_reason
                    else:
                        self.backup_bulk_reason=None
                        r=self.source.record
                        if now>=due.get('angular:backup',0) and r and r['q']==d['q'] and r['q']!=self.backup_last_angular_q:
                            data=self._live_angular(d,r,h,daq)
                            period=max(PROFILES[self.profile]['angular_s'],
                                (len(data)+len(self.prefix+'/telemetry/angular')+160)/budget)
                            if self._offer('angular','telemetry/angular',data,expiry=3,priority=3,
                                           bulk=True,links=('backup',)):
                                self.backup_last_angular_q=r['q']
                            else:
                                self.backup_angular_abort+=1
                            due['angular:backup']=now+period
            except Exception as e:
                self.bulk_reason='SCHEDULER_'+type(e).__name__.upper()
            self.stop_event.wait(.05)
