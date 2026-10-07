from __future__ import annotations
import queue
import re
import threading
import time
from .util import digest, now_ms, finite, integer, compact
from .journal import TERMINAL
from .helper import call_helper
from .config import PROFILES

OPS={
    'config.get':'cmd/config/get', 'config.patch':'cmd/config/patch',
    'processing.set':'cmd/processing/set', 'service.restart':'cmd/service/restart',
    'ppp.restart':'cmd/service/ppp/restart',
    'system.reboot.prepare':'cmd/system/reboot/prepare', 'system.reboot.execute':'cmd/system/reboot/execute',
    'system.shutdown.prepare':'cmd/system/shutdown/prepare', 'system.shutdown.execute':'cmd/system/shutdown/execute',
    'operation.get':'cmd/operation/get', 'stream.set':'cmd/stream/set',
}
READ_OPS={'config.get','operation.get'}
class CommandManager:
    def __init__(self,agent):
        self.agent=agent; self.cfg=agent.cfg; self.journal=agent.journal
        self.lock=threading.Lock(); self.busy=False; self.active_id=None
        self.queue=queue.Queue(1); self.stop_event=threading.Event()
        self.thread=threading.Thread(target=self._worker,daemon=True,name='command-worker')
        self.last_rate={}
    def start(self): self.thread.start()
    def stop(self): self.stop_event.set(); self.thread.join(3)
    def _helper(self,r,timeout=8):
        return call_helper(self.cfg['control']['helper_socket'],r,timeout)
    def _send(self,id,op,stage,result=None):
        self.agent.operation_changed(id,op,stage,result or {})
    def submit(self,request,actor,*,retained=False,topic=None):
        a=self.agent
        try:
            if retained: raise ValueError('RETAINED_COMMAND_REJECTED')
            if actor not in ('ground-controller','local-admin'): raise ValueError('INVALID_COMMAND_ACTOR')
            if not isinstance(request,dict): raise ValueError('COMMAND_OBJECT_REQUIRED')
            allowed={'v','id','sid','boot','issued_ms','expires_ms','base_rev','op','changes','desired','profile','target_id','prepare_id','challenge'}
            if set(request)-allowed: raise ValueError('UNKNOWN_COMMAND_FIELD')
            id=request.get('id'); op=request.get('op')
            if not isinstance(id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}',id): raise ValueError('INVALID_COMMAND_ID')
            if op not in OPS or request.get('v')!=2: raise ValueError('UNSUPPORTED_OPERATION_OR_VERSION')
            if op=='config.get' and actor!='ground-controller':
                raise ValueError('SETTINGS_REQUEST_GROUND_ONLY')
            if topic and topic!=a.prefix+'/'+OPS[op]: raise ValueError('COMMAND_TOPIC_MISMATCH')
            old=self.journal.lookup(id)
            if old:
                if old['hash']!=digest(request): raise ValueError('COMMAND_ID_CONFLICT')
                self._send(id,op,old['stage'],old['result'])
                return self.public(old)
            if request.get('sid')!=a.sid or request.get('boot')!=a.boot: raise ValueError('SESSION_MISMATCH')
            n=now_ms()
            issued=integer(request.get('issued_ms'),1); expires=integer(request.get('expires_ms'),1)
            if expires<=n or issued>n+1000 or not 0<expires-issued<=30000: raise ValueError('EXPIRED_OR_INVALID_DEADLINE')
            if op in READ_OPS:
                # Bound remote GET traffic; no disk or parser probes on request.
                if time.monotonic()-self.last_rate.get(actor,0)<1: raise ValueError('REQUEST_RATE_LIMIT')
                self.last_rate[actor]=time.monotonic()
                if op=='config.get':
                    source=a.source
                    if not a.cfg['mqtt']['tls']: raise ValueError('TLS_REQUIRED_FOR_SETTINGS_REPORT')
                    if source.config_error or source.raw_settings is None or source.settings_seen is None:
                        raise ValueError('SETTINGS_UNAVAILABLE')
                    if time.monotonic()-source.settings_seen>self.cfg['freshness']['source_status_ms']/1000:
                        raise ValueError('SETTINGS_STALE')
                    payload={'v':2,'sid':a.sid,'boot':a.boot,'id':id,'rev':source.revision,
                             't':now_ms(),'settings_json':source.raw_settings.decode('utf-8')}
                    settings_payload=compact(payload)
                    if len(settings_payload)>8192: raise ValueError('SETTINGS_REPORT_TOO_LARGE')
                    result={'revision':source.revision,'proof':a.config_proof}
                    with a.settings_request_lock:
                        if a.settings_request_id is not None: raise ValueError('SETTINGS_REQUEST_PENDING')
                        self.journal.accept(request,actor); self.journal.update(id,'APPLIED',result)
                        a.settings_request_id=id; a.settings_request_payload=settings_payload
                else:
                    target=self.journal.lookup(str(request.get('target_id','')))
                    result={'operation':self.public(target) if target else None}
                    self.journal.accept(request,actor); self.journal.update(id,'APPLIED',result)
                self._send(id,op,'APPLIED',result)
                return {'id':id,'stage':'APPLIED','result':result}
            if a.demo: raise ValueError('DEMO_NO_HARDWARE_CONTROL')
            if actor=='ground-controller' and not a.capabilities()['remote_commands']:
                raise ValueError('REMOTE_COMMANDS_DISABLED')
            if not a.monitor.snapshot()['clock_trusted']: raise ValueError('CLOCK_UNTRUSTED')
            # Revision must be explicit; never replace the caller's stale revision.
            if request.get('base_rev')!=a.source.revision: raise ValueError('CONFIG_REVISION_CONFLICT')
            if op=='stream.set':
                if request.get('profile') not in PROFILES: raise ValueError('UNSUPPORTED_PROFILE')
            else:
                flag={'config.patch':'config_patch_enabled','processing.set':'processing_enabled',
                      'service.restart':'restart_enabled','ppp.restart':'ppp_restart_enabled',
                      'system.reboot.prepare':'reboot_enabled','system.reboot.execute':'reboot_enabled',
                      'system.shutdown.prepare':'shutdown_enabled','system.shutdown.execute':'shutdown_enabled'}[op]
                if self.cfg['runtime_mode']!='controlled' or not self.cfg['control'][flag]: raise ValueError('CAPABILITY_DISABLED')
                if op=='ppp.restart' and not a.helper_status.get('allow_ppp_restart',False):
                    raise ValueError('CAPABILITY_DISABLED')
                if op=='processing.set' and request.get('desired') not in ('RUNNING','STOPPED'): raise ValueError('INVALID_DESIRED_STATE')
                if op=='config.patch':
                    from .helper import FIELD_MAP
                    changes=request.get('changes')
                    if not isinstance(changes,dict) or not changes or set(changes)-set(FIELD_MAP): raise ValueError('INVALID_PATCH')
                    for v in changes.values(): finite(v,-200,5e9)
            with self.lock:
                if self.busy: raise ValueError('BUSY')
                self.journal.accept(request,actor)
                self.busy=True; self.active_id=id
                self.queue.put_nowait((request,actor))
            self._send(id,op,'ACCEPTED')
            return {'id':id,'stage':'ACCEPTED'}
        except Exception as e:
            code=str(e) if isinstance(e,ValueError) and str(e).isupper() else 'JOURNAL_OR_REQUEST_ERROR'
            if isinstance(request,dict) and isinstance(request.get('id'),str) and len(request['id'])<=80:
                self._send(request['id'],str(request.get('op','unknown')),'REJECTED',{'error':code})
            return {'id':request.get('id') if isinstance(request,dict) else None,'stage':'REJECTED','result':{'error':code}}
    @staticmethod
    def public(op):
        if not op: return None
        result=dict(op['result'])
        result.pop('challenge',None)
        return dict(id=op['id'],op=op['request']['op'],stage=op['stage'],result=result,updated_ms=op['updated_ms'])
    def _stage(self,r,stage,result=None):
        self.journal.update(r['id'],stage,result)
    def _worker(self):
        while not self.stop_event.is_set():
            try: r,actor=self.queue.get(timeout=0.2)
            except queue.Empty: continue
            try:
                self._stage(r,'APPLYING')
                stage,result=self._execute(r,actor)
                self._stage(r,stage,result)
                self._send(r['id'],r['op'],stage,result)
            except Exception as e:
                err=str(e) if isinstance(e,ValueError) and str(e).isupper() else type(e).__name__.upper()
                # Helper timeout after side effect may mean unknown, not failed.
                stage='OUTCOME_UNKNOWN' if isinstance(e,(TimeoutError,OSError)) else 'FAILED'
                try:
                    self._stage(r,stage,{'error':err}); self._send(r['id'],r['op'],stage,{'error':err})
                except Exception: pass
            finally:
                with self.lock: self.busy=False; self.active_id=None
                self.agent.bulk_resume_after=time.monotonic()+self.cfg['telemetry']['resume_stable_seconds']
                self.queue.task_done()
    def _execute(self,r,actor):
        a=self.agent; op=r['op']
        if op=='ppp.restart':
            result=self._helper({'op':'ppp.restart','origin':actor})
            if result!={'requested':True,'service':'t900-ppp.service'}:
                raise TimeoutError('SYSTEMD_ACTION_OUTCOME_UNKNOWN')
            return 'PPP_RESTART_REQUESTED',{
                'accepted_by_systemd':True,'requested_ms':r['issued_ms'],'service':'t900-ppp.service'}

        if op=='stream.set':
            a.profile=r['profile']; a.journal.set('profile',a.profile)
            return 'APPLIED',{'profile':a.profile,'proof':'SCHEDULER_PROFILE_ACTIVE'}
        if op=='config.patch':
            expected=a.source.raw_digest
            if not expected: raise ValueError('SETTINGS_UNAVAILABLE')
            before=now_ms()
            result=self._helper({'op':op,'changes':r['changes'],'expected_digest':expected})
            self._stage(r,'VERIFYING',{'persisted':True})
            deadline=time.monotonic()+self.cfg['control']['verify_seconds']
            # File readback is NOT proof that runtime adopted arbitrary parameters.
            proof={}
            while time.monotonic()<deadline and not self.stop_event.is_set():
                st=a.source.status; rec=a.source.record; safe=a.source.safe
                if all(safe.get(k)==v for k,v in r['changes'].items()):
                    for key,value in r['changes'].items():
                        if key=='vfo0_frequency_hz' and rec and rec['timestamp_ms']>before and rec['frequency_hz']==value:
                            proof[key]='FRESH_DOA_FREQUENCY'
                        if key=='center_frequency_hz' and st and st['timestamp_ms']>before and st.get('rf_center_frequency_hz')==value:
                            proof[key]='FRESH_DAQ_RF_CENTER'
                        if key=='gain_db' and st and st['timestamp_ms']>before and st.get('gain_db')==value:
                            proof[key]='FRESH_DAQ_GAIN'
                    if len(proof)==len(r['changes']):
                        a.config_proof='runtime'
                        return 'APPLIED',{'revision':a.source.revision,'proof':proof,'persisted':True}
                self.stop_event.wait(0.2)
            a.config_proof='persisted_unverified'
            return 'PERSISTED_UNVERIFIED',{'revision':a.source.revision,'persisted':True,'proof':proof,
                                          'reason':'NATIVE_RUNTIME_EVIDENCE_INCOMPLETE'}
        if op in ('processing.set','service.restart'):
            oldgen=a.monitor.snapshot()['generation']; started=now_ms()
            rpc={'op':op,'origin':actor}
            if op=='processing.set': rpc['desired']=r['desired']
            result=self._helper(rpc)
            self._stage(r,'VERIFYING',result)
            deadline=time.monotonic()+self.cfg['control']['start_timeout_seconds']
            while time.monotonic()<deadline and not self.stop_event.is_set():
                m=a.monitor.snapshot(); s=a.source.status
                if r.get('desired')=='STOPPED':
                    if m['service_state'] in ('INACTIVE','FAILED') and m.get('cgroup_empty') is True:
                        return 'APPLIED',{'proof':'APPROVED_UNIT_AND_CGROUP_STOPPED','scope':'SDR_STACK','desired':'STOPPED'}
                elif m['service_state']=='ACTIVE' and s and s['timestamp_ms']>started and a.source.view(m['clock_trusted'])['daq']['healthy']:
                    if op!='service.restart' or (m['generation'] and m['generation']!=oldgen):
                        return 'APPLIED',{'proof':'NEW_FRESH_DAQ_AND_UNIT_ACTIVE','scope':'SDR_STACK','desired':'RUNNING'}
                self.stop_event.wait(0.25)
            return 'OUTCOME_UNKNOWN',{'reason':'LIFECYCLE_VERIFICATION_TIMEOUT','accepted':True}
        if op in ('system.reboot.prepare','system.shutdown.prepare'):
            return 'APPLIED',self._helper({'op':op,'id':r['id'],'origin':actor})
        if op in ('system.reboot.execute','system.shutdown.execute'):
            action='shutdown' if op.startswith('system.shutdown.') else 'reboot'
            if not isinstance(r.get('prepare_id'),str) or not isinstance(r.get('challenge'),str):
                raise ValueError(f'{action.upper()}_PREPARE_REQUIRED')
            stage=f'{action.upper()}_SCHEDULED'
            self._stage(r,stage,{'boot_before':a.boot})
            result=self._helper({'op':op,'id':r['prepare_id'],'challenge':r['challenge'],'origin':actor})
            if action=='reboot': a.planned_reboot=True
            return stage,result
        raise ValueError('UNSUPPORTED_OPERATION')
