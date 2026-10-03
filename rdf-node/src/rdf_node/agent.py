from __future__ import annotations
from collections import deque
import copy
import queue
import re
import threading
import time
import uuid
from pathlib import Path
from . import __version__
from .util import now_ms, compact, strict_json, read_boot_id, integer, atomic_write, stable_read
from .config import PROFILES
from .journal import Journal
from .source import Source
from .monitor import Monitor
from .control import CommandManager, OPS
from .helper import call_helper
from .mqtt import Client
from .mqtt_ws import validate_mqtt_host,validate_websocket_path
from .codec import encode, split

class Agent:
    def __init__(self,cfg,demo=False):
        self.cfg=cfg; self.demo=demo; self.boot=read_boot_id(); self.instance=str(uuid.uuid4()); self.sid=uuid.uuid4().hex[:8]
        self.prefix=('sdr/demo/v2/' if demo else 'sdr/v2/')+cfg['node_id']
        self.journal=Journal(Path(cfg['state_dir'])/'state.sqlite3'); self.journal.recover(self.boot)
        self.source=Source(cfg,self.journal); self.monitor=Monitor(cfg); self.commands=CommandManager(self)
        self.profile=self.journal.get('profile',cfg['telemetry']['profile'])
        if self.profile not in PROFILES: self.profile=cfg['telemetry']['profile']
        self.config_proof='unverified'; self.request_config_report=True; self.planned_reboot=False
        self.lock=threading.RLock(); self.snapshot_data={}; self.snapshot_seq=0; self.mqtt_topic_delivery={}
        self.stop_event=threading.Event(); self.events=queue.Queue(32)
        self.bulk_resume_after=time.monotonic()+cfg['telemetry']['resume_stable_seconds']
        self.receipt=None; self.receipt_progress=None; self.receipt_last_seen=None; self.receipt_rejects=0
        self.sent_health=deque(maxlen=120); self.sent_doa=deque(maxlen=120); self.sent_angular=deque(maxlen=30)
        self.hq=0; self.last_doa_q=0; self.last_angular_q=0; self.angular_parts=[]; self.angular_started=0
        self.angular_q=0; self.angular_abort=0; self.bulk_reason='BOOTSTRAP'; self.helper_status={}
        self.clients={}; self._generation=-1; self.last_config_rev=None; self.last_sent_state=None
        self.mqtt_state_path=Path(cfg['state_dir'])/'mqtt-ui-settings.json'
        self.mqtt_credentials_paths={name:Path(cfg['state_dir'])/f'mqtt-ui-{name}.json' for name in ('control','bulk')}
        self.mqtt_base_credentials={name:cfg['mqtt'].get(f'{name}_credentials_file') for name in ('control','bulk')}
        self.mqtt_config_lock=threading.RLock(); self.mqtt_apply_lock=threading.RLock()
        self.mqtt_reconfigure=threading.Event(); self.mqtt_pending=None; self.mqtt_running=False
        self.mqtt_desired=self._load_mqtt_settings()
        self.cfg['mqtt']=self._resolved_mqtt_config(self.mqtt_desired)
        self.clients=self._new_mqtt_clients(self.mqtt_desired)
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
        subs=[(self.prefix+'/'+suffix,1,True) for suffix in OPS.values()]+[(self.prefix+'/ground/receipt',0,True)]
        will=(self.prefix+'/availability',compact({'v':2,'sid':self.sid,'online':False,'reason':'CONNECTION_LOST'}))
        return {
            'control':Client(cfg=m,client_id=settings['client_id']+'-control'+demo_suffix,
                credentials_file=m['control_credentials_file'],subscriptions=subs,will=will,on_message=self._incoming,
                rate=self.cfg['telemetry']['control_budget_bytes_s']),
            'bulk':Client(cfg=m,client_id=settings['client_id']+'-bulk'+demo_suffix,
                credentials_file=m['bulk_credentials_file'],rate=self.cfg['telemetry']['bulk_budget_bytes_s'],bulk=True)
        }
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
            for client in old.values(): client.stop()
            self.cfg['mqtt']=self._resolved_mqtt_config(settings)
            self.clients=self._new_mqtt_clients(settings)
            if self.mqtt_running:
                for client in self.clients.values(): client.start()
            if self.angular_parts: self.angular_abort+=1
            self.angular_parts=[]
            while True:
                try: self.events.get_nowait(); self.events.task_done()
                except queue.Empty: break
            with self.lock:
                self.receipt=None; self.receipt_progress=None; self.receipt_last_seen=None
                self.sent_health.clear(); self.sent_doa.clear(); self.sent_angular.clear(); self.mqtt_topic_delivery.clear()
            self._generation=-1; self.last_sent_state=None; self.request_config_report=True
            self.bulk_resume_after=time.monotonic()+self.cfg['telemetry']['resume_stable_seconds']
            self.bulk_reason='MQTT_RECONFIGURED'
    def start(self):
        self._apply_pending_mqtt()
        with self.mqtt_apply_lock:
            self.mqtt_running=True
            self.monitor.start(); self.commands.start()
            for client in self.clients.values(): client.start()
            for fn,name in ((self._collect,'rdf-collector'),(self._schedule,'rdf-scheduler'),(self._events,'rdf-inbound')):
                t=threading.Thread(target=fn,name=name,daemon=True); self.threads.append(t); t.start()
    def stop(self):
        self.stop_event.set()
        self.commands.stop()
        with self.mqtt_apply_lock:
            self.mqtt_running=False
            for c in self.clients.values(): c.stop()
        self.monitor.stop()
        for t in self.threads: t.join(3)
        # A long lifecycle worker may still be reconciling; daemon exits without replay.
        if not self.commands.thread.is_alive(): self.journal.close()
    def _incoming(self,topic,payload,retained):
        if len(payload)>4096: return
        try: self.events.put_nowait((topic,payload,retained))
        except queue.Full: pass
    def _events(self):
        while not self.stop_event.is_set():
            try: topic,data,retained=self.events.get(timeout=.2)
            except queue.Empty: continue
            try:
                j=strict_json(data)
                if topic==self.prefix+'/ground/receipt': self.accept_receipt(j,retained)
                else: self.commands.submit(j,'ground-controller',retained=retained,topic=topic)
            except (ValueError,TypeError,KeyError,RecursionError): pass
            finally: self.events.task_done()
    def accept_receipt(self,r,retained=False):
        try:
            if retained or not isinstance(r,dict) or r.get('v')!=2 or r.get('sid')!=self.sid: raise ValueError('BAD_RECEIPT_SESSION')
            hq=integer(r['hq'],0,0xffffffff); dq=integer(r.get('dq',0),0,0xffffffff); aq=integer(r.get('aq',0),0,0xffffffff)
            with self.lock:
                if hq not in self.sent_health: raise ValueError('RECEIPT_HEALTH_NOT_SENT')
                if dq and dq not in self.sent_doa: raise ValueError('RECEIPT_DOA_NOT_SENT')
                if aq and aq not in self.sent_angular: raise ValueError('RECEIPT_ANGULAR_NOT_SENT')
                if self.receipt and hq<self.receipt['hq']: raise ValueError('RECEIPT_REGRESSED')
                if not self.receipt or hq>self.receipt['hq']: self.receipt_progress=time.monotonic()
                self.receipt_last_seen=time.monotonic(); self.receipt=dict(r)
        except (ValueError,KeyError,TypeError): self.receipt_rejects+=1
    def _remember(self,kind,q):
        def sent():
            with self.lock:
                target={'health':self.sent_health,'doa':self.sent_doa,'angular':self.sent_angular}[kind]
                if q not in target: target.append(q)
        return sent
    def receipt_view(self):
        with self.lock:
            age=int((time.monotonic()-self.receipt_progress)*1000) if self.receipt_progress is not None else None
            state='UNCONFIRMED' if age is None else 'LOST' if age>self.cfg['freshness']['receipt_lost_ms'] else 'LATE' if age>self.cfg['freshness']['receipt_warn_ms'] else 'RECEIVING'
            return {'state':state,'age_ms':age,'last':dict(self.receipt) if self.receipt else None,'rejected':self.receipt_rejects}
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
        receipt=self.receipt_view()
        ctrl=self.clients.get('control'); bulk=self.clients.get('bulk')
        cs=ctrl.status() if ctrl else {'state':'DISABLED','ready':False,'depth':0,'pending_age_ms':0}
        bs=bulk.status() if bulk else {'state':'DISABLED','ready':False,'depth':0,'pending_age_ms':0}
        with self.lock: topic_delivery=copy.deepcopy(self.mqtt_topic_delivery)
        # A read-only source can prove VFO frequency but not every native parameter.
        if view['valid'] and self.config_proof=='unverified': self.config_proof='source_correlated'
        if self.source.revision!=self.last_config_rev:
            self.config_proof='source_correlated' if view['valid'] else 'unverified'
            self.last_config_rev=self.source.revision; self.request_config_report=True
        sync='UNVERIFIED'
        if self.source.revision is not None and receipt['last']:
            if receipt['last'].get('rev')!=self.source.revision: sync='PENDING'
            elif receipt['state']=='RECEIVING' and self.config_proof=='runtime': sync='SYNCED'
            else: sync='REPORTED_SAME' if receipt['state']=='RECEIVING' else 'LAST_KNOWN'
        latest=self.journal.latest(1)
        alerts=[]
        if self.demo: alerts.append({'severity':'warning','code':'DEMO','text':'DEMO - bukan data perangkat'})
        if not self.source.path: alerts.append({'severity':'warning','code':'SETUP_REQUIRED','text':'Pilih folder output SDR terlebih dahulu'})
        if host.get('undervoltage'): alerts.append({'severity':'error','code':'UNDERVOLTAGE','text':'Tegangan rendah: periksa power'})
        if host.get('temperature_c') is not None and host['temperature_c']>=80: alerts.append({'severity':'error','code':'HOT','text':'Suhu tinggi: periksa pendinginan'})
        if host.get('disk_free_percent') is not None and host['disk_free_percent']<5: alerts.append({'severity':'error','code':'DISK_LOW','text':'Storage hampir penuh'})
        if processing=='RUNNING' and view['daq']['state']!='HEALTHY': alerts.append({'severity':'error','code':'DAQ_DEGRADED','text':'DAQ belum sehat / data tidak valid'})
        if not view['valid'] and view['reasons']: alerts.append({'severity':'warning','code':view['reasons'][0],'text':view['reasons'][0]})
        if self.source.error: alerts.append({'severity':'warning','code':'SOURCE_READ','text':self.source.error})
        if receipt['state'] in ('LATE','LOST'): alerts.append({'severity':'warning','code':'GROUND_LOST','text':'Ground belum menerima data terbaru'})
        self.snapshot_seq+=1
        snap={'schema_version':2,'version':__version__,'mode':'DEMO' if self.demo else 'LIVE',
              'node_id':self.cfg['node_id'],'sid':self.sid,'boot_id':self.boot,'agent_instance_id':self.instance,
              'snapshot_seq':self.snapshot_seq,'snapshot_ms':now_ms(),
              'processing':{'observed':processing,'desired':self.helper_status.get('desired'),'scope':'SDR_STACK'},
              'detection':view,'daq':view['daq'],'host':host,
              'link':{'usb':host['usb'],'ppp':host['ppp'],'interface':host['interface'],
                      'ppp_probe':host['ppp_probe'],'ppp_peer':self.cfg['link']['peer_ip'],
                      'mqtt_control':cs,'mqtt_bulk':bs,'mqtt_topic_delivery':topic_delivery,'ground':receipt,
                      'tx_kbit_s':host['tx_kbit_s'],'rx_kbit_s':host['rx_kbit_s'],
                      'traffic_layer':'PPP_IP_COUNTERS','profile':self.profile,'bulk_pause':self.bulk_reason,'angular_aborted':self.angular_abort},
              'config':self.config_view(sync),'capabilities':self.capabilities(),
              'last_operation':self.commands.public(latest[0]) if latest else None,'active_alerts':alerts}
        with self.lock: self.snapshot_data=snap
    def snapshot(self):
        with self.lock: return copy.deepcopy(self.snapshot_data)
    def config_view(self,sync=None):
        return {'sdr_revision':self.source.revision,'digest':self.source.safe_digest,'proof':self.config_proof,
                'ground_sync':sync or 'UNVERIFIED','safe_settings':dict(self.source.safe),
                'profile':self.profile,'source_configured':self.source.path is not None,
                'authority_verified':self.cfg['source']['authority_verified'],'angle_verified':self.cfg['source']['angle_verified'],
                'mqtt_configured':self.cfg['mqtt']['enabled'],'preferences':self.journal.get('display',self.cfg['display'])}
    def capabilities(self):
        c=self.cfg['control']
        return {'v':2,'sid':self.sid,'boot':self.boot,'instance':self.instance,'version':__version__,
                'mode':'DEMO' if self.demo else self.cfg['runtime_mode'],'codecs':['q16','u8'],
                'angle':self.cfg['source']['angle_mode'],'native_axis':1,'count':360,
                'profiles':list(PROFILES),'scope':'SDR_STACK','helper_available':bool(self.helper_status),
                'maintenance':self.helper_status.get('maintenance',False),
                'remote_commands':c['remote_commands_enabled'],
                'config_patch':c['config_patch_enabled'] and self.helper_status.get('allow_config',False),
                'processing':c['processing_enabled'] and self.helper_status.get('allow_lifecycle',False),
                'restart':c['restart_enabled'] and self.helper_status.get('allow_lifecycle',False),
                'reboot':c['reboot_enabled'] and self.helper_status.get('allow_reboot',False),
                'shutdown':c['shutdown_enabled'] and self.helper_status.get('allow_shutdown',False)}
    def _topic_delivery_update(self,suffix,state,qos,error=None):
        timestamp=now_ms()
        if error is not None and (not isinstance(error,str) or not re.fullmatch(r'[A-Z0-9_]{1,64}',error)):
            error='PUBLISH_FAILED'
        with self.lock:
            previous=self.mqtt_topic_delivery.get(suffix,{})
            self.mqtt_topic_delivery[suffix]={
                'state':state,'qos':qos,'confirmation':'PUBACK' if qos==1 else 'SOCKET_WRITE',
                'updated_ms':timestamp,'sent_ms':timestamp if state=='SENT' else previous.get('sent_ms'),
                'error':error}
    def _offer(self,key,suffix,payload,qos=0,retain=False,expiry=None,priority=2,sent=None,bulk=False):
        client=self.clients.get('bulk' if bulk else 'control')
        if not client: return False
        self._topic_delivery_update(suffix,'PENDING',qos)
        def delivered():
            try:
                if sent: sent()
            finally:
                self._topic_delivery_update(suffix,'SENT',qos)
        def failed(reason): self._topic_delivery_update(suffix,'ERROR',qos,error=reason)
        return client.offer(key,self.prefix+'/'+suffix,payload,qos=qos,retain=retain,expiry=expiry,
                            priority=priority,on_sent=delivered,on_error=failed)
    def operation_changed(self,id,op,stage,result):
        suffix='ack/config' if op.startswith('config.') else 'ack/operation'
        self._offer('op:'+id,suffix,{'v':2,'sid':self.sid,'id':id,'status':stage,'t':now_ms(),'rev':self.source.revision,'result':result},
                    qos=1,expiry=30,priority=0)
    def _state(self,s):
        return {'v':2,'sid':self.sid,'boot':self.boot,'instance':self.instance,'t':now_ms(),
                'run':s.get('processing',{}).get('observed','UNKNOWN'),'daq':s.get('daq',{}).get('healthy',False),
                'cfg':self.source.revision,'profile':self.profile,'clock':s.get('host',{}).get('clock_state','UNTRUSTED')}
    def _schedule(self):
        due={}; last_bootstrap=0; parts_next=0
        while not self.stop_event.is_set():
            try:
                self._apply_pending_mqtt()
                s=self.snapshot(); now=time.monotonic(); ctrl=self.clients.get('control'); bulk=self.clients.get('bulk')
                if not s or not ctrl or not ctrl.ready:
                    if self.angular_parts: self.angular_abort+=1
                    self.angular_parts=[]; self.bulk_reason='MQTT_NOT_READY'
                    self.stop_event.wait(.1); continue
                if ctrl.generation!=self._generation:
                    self._generation=ctrl.generation; self.angular_parts=[]; self.request_config_report=True
                    self.bulk_resume_after=now+self.cfg['telemetry']['resume_stable_seconds']
                    self._offer('available','availability',{'v':2,'sid':self.sid,'online':True,'t':now_ms()},1,True,priority=0)
                    self._offer('caps','capabilities',self.capabilities(),1,True,priority=3)
                    due['state']=0
                def tick(key,seconds):
                    if now>=due.get(key,0): due[key]=now+seconds; return True
                    return False
                d=s['detection']; h=s['host']; daq=s['daq']; run=s['processing']['observed']
                if tick('health',1):
                    self.hq+=1
                    payload={'v':2,'sid':self.sid,'q':self.hq,'t':now_ms(),
                             'run':{'STOPPED':0,'RUNNING':1,'STARTING':2,'STOPPING':3,'ERROR':4}.get(run,255),
                             'daq':1 if daq['healthy'] else 2 if daq['state']=='UNKNOWN' else 0,
                             'drop':daq['dropped_frames'],'age':d['source_age_ms'],'temp':h['temperature_c'],
                             'clk':1 if h['clock_trusted'] else 0,'rev':self.source.revision}
                    self._offer('health','telemetry/health',payload,expiry=5,priority=1,sent=self._remember('health',self.hq))
                if tick('detail',10):
                    self._offer('detail','telemetry/health/detail',{'v':2,'sid':self.sid,'t':now_ms(),
                        'usb':h['usb_count'],'sync':list(daq['sync'].values()),'cpu':h['cpu_percent'],'mem':h['memory_percent'],
                        'disk_free':h['disk_free_percent'],'throt':h['throttled'],'uv':h['undervoltage'],
                        'tx':h['tx_kbit_s'],'rx':h['rx_kbit_s'],'adrop':self.angular_abort,'parse':self.source.parse_errors},expiry=15,priority=4)
                state=self._state(s); state_changed=(state['run'],state['daq'],state['cfg'],state['profile'],state['clock'])
                if (state_changed!=self.last_sent_state and now-due.get('state_event_last',-10)>1) or now>=due.get('state',0):
                    if self._offer('state','state',state,1,True,priority=1):
                        due['state']=now+60; due['state_event_last']=now; self.last_sent_state=state_changed
                if self.request_config_report and now>=due.get('config',0):
                    reported={'v':2,'sid':self.sid,'rev':self.source.revision,'t':now_ms(),'proof':self.config_proof,
                              'digest':self.source.safe_digest,'effective':self.source.safe}
                    if self._offer('config','config/reported',reported,1,True,priority=3):
                        self.request_config_report=False; due['config']=now+2
                if tick('doa',PROFILES[self.profile]['doa_s']) and d['valid'] and d['q']!=self.last_doa_q:
                    payload={'v':2,'sid':self.sid,'q':d['q'],'t':d['source_timestamp_ms'],'f':d['frequency_hz'],
                             'a':round(d['relative_doa_deg'],2),'c':round(d['confidence_native_db'],2),
                             'p':round(d['power_native_db'],2),'rev':d['revision'],'ok':1}
                    if self._offer('doa','telemetry/doa',payload,expiry=3,sent=self._remember('doa',d['q'])): self.last_doa_q=d['q']
                reason=None
                if self.profile=='control': reason='PROFILE_CONTROL'
                elif not d['valid']: reason='SOURCE_NOT_ELIGIBLE'
                elif self.commands.busy: reason='CONTROL_IN_PROGRESS'
                elif not bulk or not bulk.ready: reason='BULK_NOT_READY'
                elif self.cfg['telemetry']['require_ground_receipt_for_bulk'] and s['link']['ground']['state']!='RECEIVING': reason='GROUND_RECEIPT_REQUIRED'
                elif ctrl.pending_age_ms>500: reason='CONTROL_BACKLOG'
                if reason:
                    if self.angular_parts: self.angular_abort+=1
                    self.angular_parts=[]
                    if bulk: bulk.outbox.clear('BULK_PAUSED_'+reason)
                    self.bulk_reason=reason
                    self.bulk_resume_after=now+self.cfg['telemetry']['resume_stable_seconds']
                elif now<self.bulk_resume_after:
                    self.bulk_reason='WAIT_STABLE'
                else:
                    self.bulk_reason=None
                    interval=PROFILES[self.profile]['angular_s']
                    if self.angular_parts and now-self.angular_started>3:
                        self.angular_parts=[]; self.angular_abort+=1
                    if not self.angular_parts and now>=due.get('angular',0) and d['q']!=self.last_angular_q:
                        r=self.source.record
                        if r and r['q']==d['q']:
                            frame=encode(r['values'],sid=int(self.sid,16),seq=r['q'],timestamp_ms=r['timestamp_ms'],
                                frequency_hz=r['frequency_hz'],revision=d['revision'],vfo=self.cfg['source']['output_vfo'],
                                raw_doa=r['raw_doa_deg'],confidence=r['confidence_native_db'],encoding=PROFILES[self.profile]['encoding'])
                            self.angular_parts=split(frame); self.angular_q=r['q']; self.angular_started=now
                            due['angular']=now+interval; parts_next=now
                    if self.angular_parts and now>=parts_next and bulk.outbox.status()['depth']==0 and bulk.pending_age_ms==0:
                        chunk=self.angular_parts.pop(0); q=self.angular_q
                        sent=self._remember('angular',q) if not self.angular_parts else None
                        if not self._offer('angular','telemetry/angular',chunk,expiry=3,priority=3,sent=sent,bulk=True):
                            self.angular_parts=[]; self.angular_abort+=1
                        else:
                            parts_next=now+.3
                            if not self.angular_parts: self.last_angular_q=q
            except Exception as e:
                self.bulk_reason='SCHEDULER_'+type(e).__name__.upper()
            self.stop_event.wait(.05)
