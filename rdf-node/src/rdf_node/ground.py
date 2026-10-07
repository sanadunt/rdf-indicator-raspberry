"""Optional Ubuntu companion: MQTT v2 decoder, receipt and local integration API.
Does not replace an existing Ground dashboard. The radio subscriptions stay active
when its preview browser is closed.
"""
from __future__ import annotations
import copy
from pathlib import Path
import queue
import threading
import time
import re
import uuid
from .util import now_ms, strict_json, integer, finite, compact
from .mqtt import Client
from .codec import (Assembler, FLAG_PARSED, FLAG_FRESH, FLAG_DAQ, FLAG_CONVENTION,
                    FLAG_CONFIG, FLAG_AUTHORITY, ALL_FLAGS)
from .journal import Journal, TERMINAL
from .control import OPS, CommandManager

class Ground:
    def __init__(self,cfg,demo=False):
        self.cfg=cfg; self.demo=demo; self.prefix=('sdr/demo/v2/' if demo else 'sdr/v2/')+cfg['node_id']
        self.lock=threading.RLock(); self.stop_event=threading.Event(); self.events=queue.Queue(64)
        self.journal=Journal(Path(cfg['state_dir'])/'ground.sqlite3')
        self.node_state={}; self.node_config={}; self.caps={}; self.health={}; self.doa={}; self.diagnostic_doa=None
        self.regular_unverified_doa=None; self.regular_unverified_doa_seen=None
        self.diagnostic_doa_q=0; self.regular_unverified_doa_q=0; self.diagnostic_doa_source_time=0
        self.diagnostic_angular=None; self.angular=None; self.detail={}
        self.diagnostic_angular_q=0; self.regular_unverified_angular_q=0
        self.health_seen=0; self.doa_seen=0; self.diagnostic_seen=None; self.diagnostic_angular_seen=None
        self.angular_seen=0; self.state_seen=0; self.sid_map={}
        self.assembler=Assembler(); self.diagnostic_assembler=Assembler()
        self.rejected=0; self.receipt_count=0; self.snapshot_seq=0; self.current_gen=-1
        self.client=Client(cfg=cfg['mqtt'],client_id=cfg['node_id']+'-ground'+('-demo' if demo else ''),
            credentials_file=cfg['mqtt']['control_credentials_file'],rate=1000,
            subscriptions=[(self.prefix+'/telemetry/'+s,0,True) for s in
                ('doa','health','health/detail','angular','diagnostic/doa','diagnostic/angular')]+
                          [(self.prefix+'/'+s,1,False) for s in ('state','capabilities','config/reported','availability','ack/config','ack/operation')],
            on_message=self._incoming)
        self.thread=threading.Thread(target=self._loop,name='ground-consumer',daemon=True)
    def start(self): self.client.start(); self.thread.start()
    def stop(self):
        self.stop_event.set(); self.thread.join(3); self.client.stop(); self.journal.close()
    def _incoming(self,topic,payload,retained):
        try: self.events.put_nowait((topic,payload,retained))
        except queue.Full: self.rejected+=1
    def _loop(self):
        next_receipt=0
        while not self.stop_event.is_set():
            try:
                topic,data,retained=self.events.get(timeout=.1)
                self.receive(topic,data,retained)
                self.events.task_done()
            except queue.Empty: pass
            except Exception: self.rejected+=1
            now=time.monotonic()
            if self.client.generation!=self.current_gen:
                self.current_gen=self.client.generation; self.assembler.clear(); self.diagnostic_assembler.clear()
            if now>=next_receipt and self.client.ready:
                next_receipt=now+5
                with self.lock:
                    if self.health and now-self.health_seen<8:
                        r={'v':2,'sid':self.node_state.get('sid'),'dq':self.doa.get('q',0),
                           'hq':self.health.get('q',0),'aq':self.angular['q'] if self.angular else 0,
                           'rev':self.node_config.get('rev')}
                        if self.client.offer('receipt',self.prefix+'/ground/receipt',r,expiry=5,priority=1): self.receipt_count+=1
            with self.lock: self.snapshot_seq+=1
    def _fresh(self,t,limit=10000):
        n=integer(t,1000000000000,9999999999999)
        if not -1000<=now_ms()-n<=limit: raise ValueError('SOURCE_TIMESTAMP_NOT_FRESH')
    def _reconcile_ppp_restart_health(self):
        if not self.health or not self.node_state or time.monotonic()-self.health_seen>8: return
        health_ms=self.health.get('t'); sid=self.node_state.get('sid')
        if type(health_ms) is not int or not isinstance(sid,str): return
        for operation in self.journal.pending_ppp_restarts():
            request=operation['request']; result=operation['result']
            requested_ms=result.get('requested_ms')
            if (operation['stage']!='PPP_RESTART_REQUESTED' or request.get('sid')!=sid or
                    result.get('accepted_by_systemd') is not True or type(requested_ms) is not int or
                    requested_ms!=request.get('issued_ms') or health_ms<=requested_ms):
                continue
            self.journal.update(operation['id'],'APPLIED',{
                'proof':'SYSTEMD_ACCEPTED_AND_FRESH_NODE_HEALTH',
                'accepted_by_systemd':True,'service':'t900-ppp.service',
                'requested_ms':requested_ms,'health_timestamp_ms':health_ms})
    def receive(self,topic,data,retained=False):
        if not topic.startswith(self.prefix+'/'): return
        suffix=topic[len(self.prefix)+1:]
        with self.lock:
            if suffix=='telemetry/diagnostic/angular':
                if retained or not self.node_state: return
                a=self.diagnostic_assembler.add(data)
                if a is None: return
                if a['sid']!=self.node_state.get('sid'): raise ValueError('DIAGNOSTIC_ANGULAR_SESSION_MISMATCH')
                if a['q']==0: raise ValueError('DIAGNOSTIC_ANGULAR_SEQUENCE_INVALID')
                if a['q']<=self.diagnostic_angular_q: return
                a['source']='DOA_value.html'; a['trust']='UNVERIFIED'
                self.diagnostic_angular_q=a['q']; self.diagnostic_angular=a
                self.diagnostic_angular_seen=time.monotonic()
                return
            if suffix=='telemetry/angular':
                if retained or not self.node_state: return
                a=self.assembler.add(data)
                if a is None: return
                if a['sid']!=self.node_state.get('sid') or a['revision']!=self.node_config.get('rev'):
                    raise ValueError('ANGULAR_AUTHORITY_MISMATCH')
                if a['q']==0: raise ValueError('ANGULAR_SEQUENCE_INVALID')
                self._fresh(a['timestamp_ms'],10000)
                required=FLAG_PARSED|FLAG_FRESH|FLAG_DAQ|FLAG_CONFIG
                if a['flags']&required!=required: raise ValueError('ANGULAR_EVIDENCE_INCOMPLETE')
                if not self.health or self.health.get('daq')!=1 or time.monotonic()-self.health_seen>8:
                    raise ValueError('ANGULAR_WITHOUT_HEALTH')
                if a['flags']==ALL_FLAGS:
                    if self.angular and a['q']<=self.angular['q']: return
                    a['received_ms']=now_ms(); self.angular=a; self.angular_seen=time.monotonic()
                else:
                    if a['q']<=self.regular_unverified_angular_q: return
                    a['source']='DOA_value.html'; a['trust']='UNVERIFIED'
                    self.regular_unverified_angular_q=a['q']
                    self.diagnostic_angular=a; self.diagnostic_angular_seen=time.monotonic()
                return
            j=strict_json(data)
            if not isinstance(j,dict) or j.get('v')!=2 or not isinstance(j.get('sid'),str): raise ValueError('BAD_JSON_PROTOCOL')
            sid=j['sid']
            if suffix=='state':
                if len(sid)!=8 or not isinstance(j.get('boot'),str) or not isinstance(j.get('instance'),str): raise ValueError('BAD_IDENTITY')
                int(sid,16)
                identity=(j['boot'],j['instance'])
                if sid in self.sid_map and self.sid_map[sid]!=identity: raise ValueError('SESSION_ALIAS_COLLISION')
                if len(self.sid_map)>256: self.sid_map.clear()
                self.sid_map[sid]=identity
                if self.node_state.get('sid')!=sid:
                    for old in self.journal.pending_shutdowns():
                        self.journal.update(old['id'],'OUTCOME_UNKNOWN',{'reason':'SHUTDOWN_COMPLETION_UNVERIFIED','boot':j['boot']})
                    for old in self.journal.pending_ppp_restarts():
                        result=dict(old['result']);result['reason']='PPP_RESTART_SESSION_CHANGED'
                        self.journal.update(old['id'],'OUTCOME_UNKNOWN',result)
                    self.health={}; self.doa={}; self.diagnostic_doa=None; self.diagnostic_seen=None
                    self.regular_unverified_doa=None; self.regular_unverified_doa_seen=None
                    self.diagnostic_doa_q=0; self.regular_unverified_doa_q=0; self.diagnostic_doa_source_time=0
                    self.diagnostic_angular=None; self.diagnostic_angular_seen=None; self.angular=None
                    self.diagnostic_angular_q=0; self.regular_unverified_angular_q=0
                    self.node_config={}; self.assembler.clear(); self.diagnostic_assembler.clear()
                self.node_state=j; self.state_seen=time.monotonic(); return
            if suffix=='capabilities': self.caps=j; return
            if sid!=self.node_state.get('sid'): return
            if suffix=='config/reported':
                if not isinstance(j.get('effective'),dict): raise ValueError('BAD_CONFIG_REPORT')
                if j.get('rev')!=self.node_config.get('rev'):
                    self.angular=None; self.doa={}; self.regular_unverified_doa=None
                    self.regular_unverified_doa_seen=None; self.assembler.clear()
                self.node_config=j; return
            if suffix.startswith('telemetry/') and retained: raise ValueError('RETAINED_TELEMETRY')
            if suffix=='telemetry/health':
                self._fresh(j.get('t'),8000); q=integer(j.get('q'),1,0xffffffff)
                if q<=self.health.get('q',0): return
                if j.get('daq') not in (0,1,2) or j.get('run') not in (0,1,2,3,4,5,255): raise ValueError('BAD_HEALTH_ENUM')
                self.health=j; self.health_seen=time.monotonic()
                for old in self.journal.latest():
                    if old['stage']=='REBOOT_SCHEDULED' and old['request'].get('boot')!=self.node_state.get('boot'):
                        self.journal.update(old['id'],'APPLIED',{'proof':'NEW_BOOT_AND_FRESH_HEALTH','boot':self.node_state.get('boot')})
                self._reconcile_ppp_restart_health()
            elif suffix=='telemetry/doa':
                self._fresh(j.get('t'),5000); q=integer(j.get('q'),1,0xffffffff)
                ok=j.get('ok')
                if ok==1:
                    if q<=self.doa.get('q',0): return
                    if j.get('rev')!=self.node_config.get('rev'): raise ValueError('DOA_NOT_VERIFIED')
                    finite(j['a'],0,360); finite(j['c'],-327.67,327.67); finite(j['p'],-1e6,1e6); integer(j['f'],1,0xffffffff)
                    if not self.health or self.health.get('daq')!=1 or time.monotonic()-self.health_seen>8: raise ValueError('DOA_WITHOUT_HEALTH')
                    self.doa=j; self.doa_seen=time.monotonic()
                elif ok==0:
                    if q<=self.regular_unverified_doa_q: return
                    reasons=j.get('validation_reasons')
                    allowed={'SOURCE_UNVERIFIED','ANGLE_UNVERIFIED'}
                    if (j.get('trust')!='UNVERIFIED' or j.get('angle_reference')!='RAW' or
                            j.get('rev')!=self.node_config.get('rev') or
                            not isinstance(reasons,list) or not 1<=len(reasons)<=2 or
                            any(not isinstance(r,str) or r not in allowed for r in reasons) or
                            len(set(reasons))!=len(reasons)):
                        raise ValueError('BAD_UNVERIFIED_DOA')
                    raw=finite(j['a'],0,360); frequency=integer(j['f'],1,0xffffffff)
                    confidence=finite(j['c'],-327.67,327.67); power=finite(j['p'],-1e6,1e6)
                    if not self.health or self.health.get('daq')!=1 or time.monotonic()-self.health_seen>8:
                        raise ValueError('DOA_WITHOUT_HEALTH')
                    self.regular_unverified_doa_q=q
                    self.regular_unverified_doa={'v':2,'sid':sid,'q':q,'source':'DOA_value.html',
                        'source_timestamp_ms':j['t'],'observed_timestamp_ms':now_ms(),'raw_doa_deg':raw,
                        'frequency_mhz':frequency/1000000,'confidence_native_db':confidence,
                        'power_native_db':power,'trust':'UNVERIFIED','validation_reasons':list(reasons)}
                    self.regular_unverified_doa_seen=time.monotonic()
                else:
                    raise ValueError('DOA_NOT_VERIFIED')
            elif suffix=='telemetry/diagnostic/doa':
                q=integer(j.get('q'),1,0xffffffff)
                source_time=integer(j.get('source_timestamp_ms'),1000000000000,9999999999999)
                integer(j.get('observed_timestamp_ms'),1000000000000,9999999999999)
                angle=finite(j.get('raw_doa_deg'),0,360)
                finite(j.get('frequency_mhz'),0.001,5000)
                reasons=j.get('validation_reasons')
                if (j.get('source')!='doa.xml' or j.get('trust')!='UNVERIFIED' or
                    not isinstance(reasons,list) or not 1<=len(reasons)<=32 or
                    any(not isinstance(r,str) or not re.fullmatch(r'[A-Z0-9_:-]{1,64}',r) for r in reasons) or
                    'DIAGNOSTIC_UNVERIFIED' not in reasons):
                    raise ValueError('BAD_DIAGNOSTIC_DOA')
                if q<=self.diagnostic_doa_q: return
                if source_time<self.diagnostic_doa_source_time:
                    raise ValueError('DIAGNOSTIC_SOURCE_TIME_REGRESSED')
                self.diagnostic_doa_q=q; self.diagnostic_doa_source_time=source_time
                self.diagnostic_doa=j; self.diagnostic_seen=time.monotonic()
            elif suffix=='telemetry/health/detail': self.detail=j
            elif suffix in ('ack/config','ack/operation'):
                old=self.journal.lookup(str(j.get('id','')))
                stages=TERMINAL|{'ACCEPTED','APPLYING','VERIFYING','REBOOT_SCHEDULED',
                                 'SHUTDOWN_SCHEDULED','PPP_RESTART_REQUESTED'}
                stage=j.get('status')
                if not isinstance(stage,str): return
                if old and old['request'].get('op')=='system.shutdown.execute':
                    if old['stage']=='SHUTDOWN_SCHEDULED' and stage not in ('SHUTDOWN_SCHEDULED','OUTCOME_UNKNOWN'): return
                    if old['stage']=='OUTCOME_UNKNOWN' and stage!='OUTCOME_UNKNOWN': return
                if old and old['request'].get('op')=='system.shutdown.execute' and stage in {
                        'APPLIED','PERSISTED_UNVERIFIED','REBOOT_SCHEDULED'}: return
                if old and old['request'].get('op')=='ppp.restart':
                    if old['stage'] in TERMINAL and stage!=old['stage']: return
                    if stage=='APPLIED': return
                    if old['stage']=='PPP_RESTART_REQUESTED' and stage!='PPP_RESTART_REQUESTED': return
                    if old['stage']=='OUTCOME_UNKNOWN' and old['result'].get('confirmed_by'): return
                    if stage=='PPP_RESTART_REQUESTED':
                        result=j.get('result')
                        requested_ms=old['request'].get('issued_ms')
                        if (not isinstance(result,dict) or result.get('accepted_by_systemd') is not True or
                                type(result.get('requested_ms')) is not int or result.get('requested_ms')!=requested_ms or
                                result.get('service')!='t900-ppp.service'):
                            return
                if old and old['request'].get('sid')==sid and stage in stages:
                    if old['stage'] in TERMINAL and stage not in TERMINAL: return
                    result=j.get('result',{})
                    if not isinstance(result,dict): return
                    self.journal.update(old['id'],stage,result)
                    if old['request'].get('op')=='ppp.restart' and stage=='PPP_RESTART_REQUESTED':
                        self._reconcile_ppp_restart_health()
    def snapshot(self):
        with self.lock:
            now=time.monotonic()
            ha=round((now-self.health_seen)*1000) if self.health else None
            da=round((now-self.doa_seen)*1000) if self.doa else None
            fresh=bool(ha is not None and ha<8000)
            doa_valid=bool(fresh and self.health.get('daq')==1 and self.doa and da<5000 and now_ms()-self.doa.get('t',0)<5000)
            diagnostic_seen=self.diagnostic_seen
            if (self.regular_unverified_doa is not None and self.regular_unverified_doa_seen is not None and
                    now-self.regular_unverified_doa_seen<=5):
                diagnostic=copy.deepcopy(self.regular_unverified_doa)
                diagnostic_seen=self.regular_unverified_doa_seen
            else:
                diagnostic=copy.deepcopy(self.diagnostic_doa)
            diagnostic_age=round((now-diagnostic_seen)*1000) if diagnostic else None
            if diagnostic:
                diagnostic['available']=True
                diagnostic['received_age_ms']=diagnostic_age
                diagnostic['stale']=diagnostic_age>5000
            latest=self.journal.latest(1)
            return {'schema_version':2,'snapshot_seq':self.snapshot_seq,'snapshot_ms':now_ms(),'node_id':self.cfg['node_id'],
                    'mode':'DEMO' if self.demo else 'LIVE','sid':self.node_state.get('sid'),'boot_id':self.node_state.get('boot'),
                    'node_state':copy.deepcopy(self.node_state),'health':dict(self.health),'health_age_ms':ha,'health_fresh':fresh,
                    'detection':{'valid':doa_valid,'relative_doa_deg':self.doa.get('a') if doa_valid else None,
                                 'frequency_hz':self.doa.get('f'),'source_timestamp_ms':self.doa.get('t'),'confidence_native_db':self.doa.get('c'),
                                 'power_native_db':self.doa.get('p'),'receipt_age_ms':da},
                    'diagnostic_doa':diagnostic,
                    'config':self.config_view(),'link':{'mqtt_control':self.client.status(),'receipt_sent':self.receipt_count,'rejected':self.rejected},
                    'capabilities':self.capabilities(),'last_operation':CommandManager.public(latest[0]) if latest else None}
    def angular_view(self):
        with self.lock:
            if not self.angular: return None
            a=copy.deepcopy(self.angular); a['source_age_ms']=max(0,now_ms()-a['timestamp_ms'])
            reasons=[]
            if a['source_age_ms']>10000: reasons.append('SOURCE_STALE')
            if not self.health or time.monotonic()-self.health_seen>8: reasons.append('HEALTH_STALE')
            elif self.health.get('daq')!=1 or self.health.get('run')!=1: reasons.append('DAQ_OR_PROCESSING_NOT_HEALTHY')
            if a['revision']!=self.node_config.get('rev'): reasons.append('PREVIOUS_CONFIG')
            a['stale']=bool(reasons); a['reasons']=reasons
            return a
    def diagnostic_angular_view(self):
        with self.lock:
            if not self.diagnostic_angular: return None
            a=copy.deepcopy(self.diagnostic_angular); now=time.monotonic(); utc=now_ms()
            age=utc-a['timestamp_ms']; source_age=max(0,age)
            flags=a['flags']; reasons=[]
            if not flags&FLAG_PARSED: reasons.append('SOURCE_PARSE_UNVERIFIED')
            if not flags&FLAG_FRESH: reasons.append('SOURCE_FRESHNESS_UNVERIFIED')
            if not flags&FLAG_AUTHORITY: reasons.append('SOURCE_AUTHORITY_UNVERIFIED_AT_EDGE')
            if not flags&FLAG_DAQ: reasons.append('DAQ_NOT_HEALTHY_AT_EDGE')
            if not flags&FLAG_CONVENTION: reasons.append('ANGLE_UNVERIFIED_AT_EDGE')
            if not flags&FLAG_CONFIG: reasons.append('CONFIG_ATTRIBUTION_UNVERIFIED')
            if age < -1000: reasons.append('SOURCE_TIME_FUTURE')
            elif source_age>self.cfg['freshness']['doa_ms']: reasons.append('SOURCE_STALE')
            if not self.health or now-self.health_seen>8:
                reasons.append('HEALTH_STALE')
            else:
                if self.health.get('daq')!=1: reasons.append('DAQ_NOT_HEALTHY')
                if self.health.get('run')!=1: reasons.append('PROCESSING_NOT_RUNNING')
            if (a['revision'] is not None and self.node_config.get('rev') is not None and
                    a['revision']!=self.node_config.get('rev')):
                reasons.append('PREVIOUS_CONFIG')
            a.update(available=True,source_timestamp_ms=a['timestamp_ms'],source_age_ms=source_age,
                     received_age_ms=round((now-self.diagnostic_angular_seen)*1000),
                     validation_reasons=list(dict.fromkeys(reasons)),stale=bool(reasons))
            return a

    def config_view(self):
        with self.lock:
            return {'sdr_revision':self.node_config.get('rev'),'proof':self.node_config.get('proof','UNVERIFIED'),
                    'safe_settings':self.node_config.get('effective',{}),'reported':copy.deepcopy(self.node_config)}
    def capabilities(self): return copy.deepcopy(self.caps)
    def operations_view(self): return [CommandManager.public(r) for r in self.journal.latest()]
    def submit_command(self,obj):
        if not isinstance(obj,dict) or obj.get('op') not in OPS: return {'stage':'REJECTED','error':'UNSUPPORTED_OPERATION'}
        if not self.client.ready or not self.health or time.monotonic()-self.health_seen>8:
            return {'stage':'REJECTED','error':'NODE_NOT_FRESH'}
        op=obj['op']; confirm_present='confirm_previous_unknown' in obj
        if confirm_present and op!='ppp.restart':
            return {'stage':'REJECTED','error':'UNKNOWN_INTENT_FIELD'}
        confirm=obj.get('confirm_previous_unknown',False)
        if type(confirm) is not bool: return {'stage':'REJECTED','error':'INVALID_CONFIRMATION_FLAG'}
        allowed={'id','op','changes','desired','profile','target_id','prepare_id','challenge','base_rev',
                 'confirm_previous_unknown'}
        if set(obj)-allowed: return {'stage':'REJECTED','error':'UNKNOWN_INTENT_FIELD'}
        command_id=obj.get('id')
        if 'id' in obj and (not isinstance(command_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}',command_id)):
            return {'stage':'REJECTED','error':'INVALID_COMMAND_ID'}
        with self.lock:
            pending=self.journal.pending_ppp_restarts() if op=='ppp.restart' else []
            if confirm and not pending:
                return {'stage':'REJECTED','error':'PPP_RESTART_CONFIRMATION_NOT_REQUIRED'}
            if pending and not confirm:
                return {'stage':'REJECTED','error':'PPP_RESTART_CONFIRMATION_REQUIRED'}
            if pending:
                health_ms=self.health.get('t')
                if type(health_ms) is not int or any(
                        type(old['request'].get('issued_ms')) is not int or
                        health_ms<=old['request']['issued_ms'] for old in pending):
                    return {'stage':'REJECTED','error':'PPP_RESTART_HEALTH_NOT_NEWER'}
            command_id=command_id or 'ground-'+uuid.uuid4().hex[:20]
            if pending and any(old['id']==command_id for old in pending):
                return {'stage':'REJECTED','error':'PPP_RESTART_REQUIRES_NEW_ID'}
            existing=self.journal.lookup(command_id)
            if existing:
                intent={key:value for key,value in obj.items() if key!='confirm_previous_unknown'}
                if (op=='ppp.restart' and existing['request'].get('op')=='ppp.restart' and not pending and
                        all(existing['request'].get(key)==value for key,value in intent.items())):
                    return {'id':command_id,'stage':existing['stage'],'request':existing['request']}
                return {'stage':'REJECTED','error':'COMMAND_ID_CONFLICT'}
            # The confirmation is local Ground intent and never enters the v2 MQTT envelope.
            request={key:value for key,value in obj.items() if key!='confirm_previous_unknown'}
            request.update(v=2,id=command_id,sid=self.node_state['sid'],boot=self.node_state['boot'],
                           issued_ms=now_ms(),expires_ms=now_ms()+15000)
            request.setdefault('base_rev',self.node_config.get('rev'))
            self.journal.accept(request,'ground-local-admin'); self.journal.update(request['id'],'REQUESTED',{})
            if not self.client.offer('cmd:'+request['id'],self.prefix+'/'+OPS[request['op']],request,
                                     qos=1,expiry=15,priority=0):
                self.journal.update(request['id'],'REJECTED',{'error':'LOCAL_QUEUE_REJECTED'})
                return {'stage':'REJECTED','error':'LOCAL_QUEUE_REJECTED'}
            for old in pending:
                result=dict(old['result'])
                result.setdefault('reason','OPERATOR_CONFIRMED_NEW_REQUEST')
                result['confirmed_by']=request['id']
                self.journal.update(old['id'],'OUTCOME_UNKNOWN',result)
            return {'id':request['id'],'stage':'REQUESTED','request':request}
