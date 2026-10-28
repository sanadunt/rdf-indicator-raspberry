from __future__ import annotations
import queue
import re
import sqlite3
import threading
import time
from .util import digest, now_ms, finite, integer, compact
from .journal import TERMINAL
from .helper import call_helper,HelperUnavailable
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
SUMMARY_KIND={'config.patch':'config','stream.set':'profile','processing.set':'processing','service.restart':'restart',
              'ppp.restart':'ppp','system.reboot.prepare':'reboot','system.reboot.execute':'reboot',
              'system.shutdown.prepare':'shutdown','system.shutdown.execute':'shutdown',
              'config.get':'read','operation.get':'read'}
PPP_RESTART_MIN_GAP_S=15
# Operations that only the root helper can carry out (stream.set and the read ops stay in the agent).
HELPER_OPS=set(OPS)-READ_OPS-{'stream.set'}
CODE_RE=re.compile(r'[A-Z][A-Z0-9_]{0,63}')
# Operator text for every ACK error. `error` stays the stable machine code; `message` explains it and
# `detail` (when there is one) keeps the technical cause for whoever debugs it.
ERROR_MESSAGES={
    'HELPER_UNAVAILABLE':'Helper kontrol (rdf-control-helper) tidak berjalan atau socket-nya tidak ada; perintah tidak dijalankan.',
    'HELPER_ACCESS_DENIED':'Agent tidak punya izin membuka socket helper kontrol; perintah tidak dijalankan.',
    'HELPER_TIMEOUT':'Helper kontrol tidak menjawab tepat waktu; perintah mungkin sudah atau belum dijalankan.',
    'HELPER_CONNECTION_LOST':'Koneksi ke helper kontrol terputus setelah perintah dikirim; hasilnya belum pasti.',
    'HELPER_CLOSED':'Helper kontrol menutup koneksi tanpa jawaban; hasilnya belum pasti.',
    'HELPER_RESPONSE_LIMIT':'Jawaban helper kontrol terlalu besar dan tidak dibaca.',
    'HELPER_INTERNAL_ERROR':'Helper kontrol mengalami error internal; lihat detail.',
    'HELPER_FAILED':'Helper kontrol menolak perintah tanpa alasan.',
    'INTERNAL_ERROR':'Error internal di agent Edge; lihat detail.',
    'INVALID_COMMAND_FIELD':'Ada field perintah yang tipe atau nilainya tidak valid.',
    'JOURNAL_UNAVAILABLE':'Journal perintah di Edge tidak bisa ditulis; perintah tidak dijalankan.',
    'RETAINED_COMMAND_REJECTED':'Perintah retained ditolak; kirim perintah tanpa flag retain.',
    'INVALID_COMMAND_ACTOR':'Asal perintah tidak dikenal.',
    'COMMAND_OBJECT_REQUIRED':'Isi perintah harus berupa objek JSON.',
    'UNKNOWN_COMMAND_FIELD':'Perintah memuat field yang tidak dikenal.',
    'INVALID_COMMAND_ID':'ID perintah kosong, terlalu panjang, atau memuat karakter tidak sah.',
    'UNSUPPORTED_OPERATION_OR_VERSION':'Operasi atau versi perintah (v) tidak didukung Edge ini.',
    'UNSUPPORTED_OPERATION':'Operasi tidak didukung Edge ini.',
    'SETTINGS_REQUEST_GROUND_ONLY':'Permintaan settings hanya boleh dari Ground.',
    'COMMAND_TOPIC_MISMATCH':'Operasi tidak cocok dengan topik tempat perintah dikirim.',
    'COMMAND_ID_CONFLICT':'ID perintah sudah dipakai untuk perintah lain.',
    'SESSION_MISMATCH':'sid/boot perintah tidak cocok dengan sesi Edge sekarang; baca ulang state lalu kirim lagi.',
    'EXPIRED_OR_INVALID_DEADLINE':'Perintah sudah kedaluwarsa atau batas waktunya tidak sah (maks. 30 dtk); periksa jam Ground.',
    'REQUEST_RATE_LIMIT':'Permintaan baca terlalu cepat; tunggu 1 dtk.',
    'TLS_REQUIRED_FOR_SETTINGS_REPORT':'Settings tidak dikirim lewat broker cadangan tanpa TLS kecuali diizinkan.',
    'SETTINGS_UNAVAILABLE':'Settings KrakenSDR belum terbaca di Edge.',
    'SETTINGS_STALE':'Settings KrakenSDR yang terbaca sudah basi.',
    'SETTINGS_REPORT_TOO_LARGE':'Settings KrakenSDR terlalu besar untuk dikirim lewat MQTT (maks. 8 KiB).',
    'SETTINGS_REQUEST_PENDING':'Permintaan settings sebelumnya masih dikirim; coba lagi sebentar.',
    'DEMO_NO_HARDWARE_CONTROL':'Mode demo tidak mengendalikan perangkat.',
    'REMOTE_COMMANDS_DISABLED':'Perintah dari Ground dinonaktifkan di Edge ini.',
    'CLOCK_UNTRUSTED':'Jam Edge belum tersinkron, jadi batas waktu perintah tidak bisa dipercaya.',
    'CONFIG_REVISION_CONFLICT':'Revisi config (base_rev) sudah berubah; baca ulang settings lalu kirim lagi.',
    'UNSUPPORTED_PROFILE':'Profil stream tidak dikenal.',
    'CAPABILITY_DISABLED':'Fitur ini belum diaktifkan atau belum disetujui root di Edge.',
    'INVALID_DESIRED_STATE':'Status yang diminta harus RUNNING atau STOPPED.',
    'INVALID_PATCH':'Perubahan settings kosong atau memuat field yang tidak boleh diubah.',
    'BUSY':'Operasi lain sedang berjalan; tunggu sampai selesai.',
    'PPP_RESTART_IN_PROGRESS':'Restart T900 sebelumnya masih berjalan atau dipantau.',
    'PPP_RESTART_TOO_SOON':'Restart T900 terakhir kurang dari 15 dtk lalu.',
    'PPP_RESTART_PENDING':'Job systemd untuk t900-ppp masih antre.',
    'PPP_RESTART_NOT_APPROVED':'Restart T900 belum disetujui root (rdf-node controls approve --ppp-restart).',
    'PPP_SERVICE_NOT_LOADED':'Unit t900-ppp.service tidak ada di Edge.',
    'PPP_SERVICE_NOT_ACTIVE':'Unit t900-ppp.service tidak aktif; hanya panel lokal yang boleh menyalakannya.',
    'PPP_SERVICE_STATUS_UNKNOWN':'Status unit t900-ppp.service tidak bisa dibaca.',
    'SYSTEMD_ACTION_FAILED':'systemd menolak atau gagal menjalankan aksi.',
    'SYSTEMD_ACTION_OUTCOME_UNKNOWN':'systemd tidak memberi jawaban pasti; aksi mungkin sudah berjalan.',
    'LIFECYCLE_NOT_APPROVED':'Start/stop SDR belum disetujui root.',
    'LIFECYCLE_TARGET_NOT_READY':'Unit SDR yang disetujui belum siap.',
    'UNSAFE_ENGINE_UNIT':'Unit SDR yang dikonfigurasi tidak aman untuk dikendalikan.',
    'CONFIG_ADAPTER_NOT_APPROVED':'Penulisan settings KrakenSDR belum disetujui root.',
    'CONFIG_TARGET_NOT_READY':'File settings KrakenSDR yang disetujui belum siap.',
    'CONFIG_CHANGED_BEFORE_WRITE':'Settings berubah sebelum sempat ditulis; baca ulang lalu kirim lagi.',
    'EXTERNAL_WRITER_CONFLICT':'Settings diubah program lain saat ditulis; perubahan dibatalkan.',
    'CONTROL_REQUIRES_SINGLE_VFO0':'Ubah settings hanya didukung dengan satu VFO aktif (VFO0).',
    'CENTER_CHANGE_REQUIRES_VFO0_TARGET':'Frekuensi tengah hanya boleh diubah bersama frekuensi VFO0.',
    'MVP_CENTER_MUST_EQUAL_VFO0':'Frekuensi tengah harus sama dengan frekuensi VFO0.',
    'GAIN_NOT_IN_APPROVED_TABLE':'Nilai gain tidak ada di tabel gain yang diizinkan.',
    'UNSUPPORTED_SETTINGS_FIELD':'Field settings ini tidak boleh diubah.',
    'SETTINGS_FILE_INVALID':'File settings KrakenSDR tidak valid.',
    'SETTINGS_OBJECT_REQUIRED':'File settings KrakenSDR bukan objek JSON.',
    'SETTINGS_TOO_LARGE':'File settings KrakenSDR terlalu besar.',
    'NUMBER_OUT_OF_RANGE':'Ada nilai di luar batas aman.',
    'INTEGER_OUT_OF_RANGE':'Ada nilai bilangan bulat di luar batas.',
    'BOOLEAN_AS_NUMBER':'Nilai angka dikirim sebagai boolean.',
    'BOOLEAN_AS_INTEGER':'Nilai bilangan bulat dikirim sebagai boolean.',
    'PEER_UID_DENIED':'Helper kontrol menolak proses pemanggil.',
    'RPC_SIZE_LIMIT':'Permintaan ke helper kontrol terlalu besar.',
    'TOO_MANY_ACTIVE_CHALLENGES':'Terlalu banyak persiapan reboot/shutdown yang masih aktif.',
    'REBOOT_PREPARE_REQUIRED':'Reboot perlu langkah persiapan (prepare) dan challenge-nya.',
    'SHUTDOWN_PREPARE_REQUIRED':'Shutdown perlu langkah persiapan (prepare) dan challenge-nya.',
    'SHUTDOWN_ALREADY_SCHEDULED':'Shutdown sudah dijadwalkan.',
    'SHUTDOWN_UNIT_ACTIVE':'Unit shutdown masih aktif.',
    'SHUTDOWN_UNIT_STATUS_UNKNOWN':'Status unit shutdown tidak bisa dibaca.',
    'SHUTDOWN_INTENT_INVALID':'Catatan rencana shutdown tidak valid.',
    'LIFECYCLE_VERIFICATION_TIMEOUT':'SDR belum terbukti berjalan/berhenti dalam batas waktu verifikasi.',
    'NATIVE_RUNTIME_EVIDENCE_INCOMPLETE':'Settings tersimpan, tetapi belum terbukti dipakai KrakenSDR.',
    # Codes an older helper or agent may still report.
    'OSERROR':'Error sistem saat menghubungi helper kontrol (kode lama).',
    'TIMEOUTERROR':'Helper kontrol tidak menjawab tepat waktu (kode lama).',
    'RPC_TIMEOUT':'Helper kontrol tidak menjawab tepat waktu.',
    'JOURNAL_OR_REQUEST_ERROR':'Perintah tidak bisa diproses (kode lama).',
}
def error_result(code,error=None):
    """ACK result for a failure: the stable code, operator text, and the technical cause if any."""
    result={'error':code,'message':ERROR_MESSAGES.get(code,'Perintah tidak dapat diproses (kode '+code+').')}
    detail=getattr(error,'detail',None)
    if detail is None and error is not None:
        cause=error.__cause__ or error
        if cause is not error or not CODE_RE.fullmatch(str(error)):
            detail=f'{type(cause).__name__}: {cause}' if str(cause) else type(cause).__name__
    if isinstance(detail,str) and detail: result['detail']=detail[:200]
    return result
def classify_failure(error):
    """(stage, code) for an exception raised while running an accepted operation."""
    # A helper that was never reached ran nothing; a socket error after sending leaves it unknown.
    unknown=isinstance(error,OSError)
    code=str(error) if CODE_RE.fullmatch(str(error)) else (
        'HELPER_TIMEOUT' if isinstance(error,TimeoutError) else
        'HELPER_CONNECTION_LOST' if isinstance(error,OSError) else 'INTERNAL_ERROR')
    return ('OUTCOME_UNKNOWN' if unknown else 'FAILED'),code
class CommandManager:
    def __init__(self,agent):
        self.agent=agent; self.cfg=agent.cfg; self.journal=agent.journal
        self.lock=threading.Lock(); self.busy=False; self.active_id=None
        self.queue=queue.Queue(1); self.stop_event=threading.Event()
        self.thread=threading.Thread(target=self._worker,daemon=True,name='command-worker')
        self.last_rate={}
        # PPP restart has its own lane: an SDR verification (BUSY) must never block link recovery.
        self.ppp_lock=threading.Lock(); self.ppp_active=None; self.ppp_last=None; self.ppp_thread=None
    def start(self): self.thread.start()
    def stop(self): self.stop_event.set(); self.thread.join(3)
    def _helper(self,r,timeout=8):
        return call_helper(self.cfg['control']['helper_socket'],r,timeout)
    def _require_helper(self):
        # Reject before journaling when the helper is known to be gone: nothing would run anyway.
        # The cached status is only a hint, so an empty one is checked once more against the socket.
        a=self.agent
        if a.helper_status: return
        try: status=self._helper({'op':'status'},timeout=1)
        except OSError: return  # reached but slow: the worker reports what the operation itself gets
        if isinstance(status,dict) and status: a.helper_status=status
        else: raise HelperUnavailable('HELPER_UNAVAILABLE')
    def _send(self,id,op,stage,result=None,link=None):
        self.agent.operation_changed(id,op,stage,result or {},link=link)
    @staticmethod
    def origin(actor,link):
        return 'local' if actor=='local-admin' else 'backup' if link=='backup' else 'ground'
    def _before(self,op,request):
        a=self.agent
        if op=='config.patch' and isinstance(request.get('changes'),dict):
            return {'before':{k:a.source.safe.get(k) for k in request['changes']}}
        if op=='stream.set': return {'before':{'profile':a.profile}}
        if op=='processing.set': return {'before':{'desired':a.helper_status.get('desired')}}
        return {}
    def submit(self,request,actor,*,retained=False,topic=None,link='primary'):
        # link: broker the command arrived on ('primary' Ground link or 'backup'); ACKs go back there.
        a=self.agent; origin=self.origin(actor,link)
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
                self._send(id,op,old['stage'],old['result'],link=link)
                return self.public(old)
            if request.get('sid')!=a.sid or request.get('boot')!=a.boot: raise ValueError('SESSION_MISMATCH')
            n=now_ms()
            issued=integer(request.get('issued_ms'),1); expires=integer(request.get('expires_ms'),1)
            if expires<=n or issued>n+1000 or not 0<expires-issued<=30000: raise ValueError('EXPIRED_OR_INVALID_DEADLINE')
            if op in READ_OPS:
                # Bound remote GET traffic; no disk or parser probes on request.
                if time.monotonic()-self.last_rate.get((actor,link),0)<1: raise ValueError('REQUEST_RATE_LIMIT')
                self.last_rate[(actor,link)]=time.monotonic()
                if op=='config.get':
                    source=a.source
                    if not a.settings_report_allowed(link): raise ValueError('TLS_REQUIRED_FOR_SETTINGS_REPORT')
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
                        self.journal.accept(request,actor,origin); self.journal.update(id,'APPLIED',result)
                        a.settings_request_id=id; a.settings_request_payload=settings_payload
                        a.settings_request_link=link
                else:
                    target=self.journal.lookup(str(request.get('target_id','')))
                    result={'operation':self.public(target) if target else None}
                    self.journal.accept(request,actor,origin); self.journal.update(id,'APPLIED',result)
                self._send(id,op,'APPLIED',result,link=link)
                return {'id':id,'stage':'APPLIED','result':result}
            if a.demo: raise ValueError('DEMO_NO_HARDWARE_CONTROL')
            if actor=='ground-controller' and not a.capabilities()['remote_commands']:
                raise ValueError('REMOTE_COMMANDS_DISABLED')
            # A Pi without RTC gets NTP over the PPP link, so the clock is untrusted exactly when a local
            # PPP restart is needed; its deadline uses the same host clock. It also never touches settings.
            local_ppp=op=='ppp.restart' and actor=='local-admin'
            if not local_ppp and not a.monitor.snapshot()['clock_trusted']: raise ValueError('CLOCK_UNTRUSTED')
            # Revision must be explicit; never replace the caller's stale revision.
            if not local_ppp and request.get('base_rev')!=a.source.revision: raise ValueError('CONFIG_REVISION_CONFLICT')
            if op=='stream.set':
                if request.get('profile') not in PROFILES: raise ValueError('UNSUPPORTED_PROFILE')
            else:
                if actor!='ground-controller':
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
                if op=='processing.set' and request.get('desired') not in ('RUNNING','STOPPED'): raise ValueError('INVALID_DESIRED_STATE')
                if op=='config.patch':
                    from .helper import FIELD_MAP
                    changes=request.get('changes')
                    if not isinstance(changes,dict) or not changes or set(changes)-set(FIELD_MAP): raise ValueError('INVALID_PATCH')
                    for v in changes.values(): finite(v,-200,5e9)
            if op in HELPER_OPS: self._require_helper()
            meta=self._before(op,request)
            if op=='ppp.restart':
                return self._submit_ppp(request,actor,link,origin,meta)
            with self.lock:
                if self.busy: raise ValueError('BUSY')
                self.journal.accept(request,actor,origin,meta)
                self.busy=True; self.active_id=id
                self.queue.put_nowait((request,actor,link))
            self._send(id,op,'ACCEPTED',link=link)
            return {'id':id,'stage':'ACCEPTED'}
        except Exception as e:
            if CODE_RE.fullmatch(str(e)) and isinstance(e,ValueError): code=str(e)
            elif isinstance(e,sqlite3.Error): code='JOURNAL_UNAVAILABLE'
            elif isinstance(e,(TypeError,ValueError,KeyError,OverflowError)): code='INVALID_COMMAND_FIELD'
            else: code='INTERNAL_ERROR'
            result=error_result(code,e)
            if actor=='ground-controller':
                op_name=request.get('op') if isinstance(request,dict) and request.get('op') in OPS else 'unknown'
                remember=getattr(a,'remote_rejected',None)
                if remember: remember(origin,op_name,code)
            if isinstance(request,dict) and isinstance(request.get('id'),str) and len(request['id'])<=80:
                self._send(request['id'],str(request.get('op','unknown')),'REJECTED',result,link=link)
            return {'id':request.get('id') if isinstance(request,dict) else None,'stage':'REJECTED','result':result}
    @staticmethod
    def public(op):
        if not op: return None
        result=dict(op['result'])
        result.pop('challenge',None)
        request=op['request']; name=request.get('op'); before=(op.get('meta') or {}).get('before',{})
        changes={}
        if name=='config.patch' and isinstance(request.get('changes'),dict):
            changes={k:{'from':before.get(k),'to':v} for k,v in request['changes'].items()}
        elif name=='stream.set': changes={'profile':{'from':before.get('profile'),'to':request.get('profile')}}
        elif name=='processing.set': changes={'desired':{'from':before.get('desired'),'to':request.get('desired')}}
        reason=result.get('error') or result.get('reason')
        return dict(id=op['id'],op=name,stage=op['stage'],result=result,updated_ms=op['updated_ms'],
                    origin=op.get('link') if op.get('link') in ('local','ground','backup') else None,
                    summary={'kind':SUMMARY_KIND.get(name,'other'),'changes':changes},
                    reason=reason if isinstance(reason,str) else None)
    def _submit_ppp(self,request,actor,link,origin,meta):
        a=self.agent; id=request['id']
        with self.ppp_lock:
            recovering=getattr(a,'ppp_recovery_active',lambda:False)()
            if self.ppp_active or recovering: raise ValueError('PPP_RESTART_IN_PROGRESS')
            if self.ppp_last is not None and time.monotonic()-self.ppp_last<PPP_RESTART_MIN_GAP_S:
                raise ValueError('PPP_RESTART_TOO_SOON')
            self.journal.accept(request,actor,origin,meta)
            self.ppp_active=id; self.ppp_last=time.monotonic()
        self._send(id,'ppp.restart','ACCEPTED',link=link)
        self.ppp_thread=threading.Thread(target=self._run_ppp,args=(request,actor,link,origin),daemon=True,name='ppp-restart')
        self.ppp_thread.start()
        return {'id':id,'stage':'ACCEPTED'}
    def join_ppp(self,timeout=10):
        thread=self.ppp_thread
        if thread: thread.join(timeout)
    def _run_ppp(self,r,actor,link,origin):
        a=self.agent
        try:
            self._stage(r,'APPLYING')
            stage,result=self._execute(r,actor)
            self._stage(r,stage,result); self._send(r['id'],r['op'],stage,result,link=link)
            begin=getattr(a,'ppp_recovery_begin',None)
            if begin: begin(r['id'],origin,link)
        except Exception as e:
            stage,err=classify_failure(e); result=error_result(err,e)
            try:
                self._stage(r,stage,result); self._send(r['id'],r['op'],stage,result,link=link)
            except Exception: pass
            failed=getattr(a,'ppp_recovery_failed',None)
            if failed: failed(r['id'],origin,stage,err)
        finally:
            with self.ppp_lock: self.ppp_active=None
    def _stage(self,r,stage,result=None):
        self.journal.update(r['id'],stage,result)
        # Journal-only stages (APPLYING, VERIFYING, *_SCHEDULED) still change what the panel shows.
        if hasattr(self.agent,'operation_seq'): self.agent.operation_seq+=1
    def _worker(self):
        while not self.stop_event.is_set():
            try: r,actor,link=self.queue.get(timeout=0.2)
            except queue.Empty: continue
            try:
                self._stage(r,'APPLYING')
                stage,result=self._execute(r,actor)
                self._stage(r,stage,result)
                self._send(r['id'],r['op'],stage,result,link=link)
            except Exception as e:
                # Helper timeout after side effect may mean unknown, not failed.
                stage,err=classify_failure(e); result=error_result(err,e)
                try:
                    self._stage(r,stage,result); self._send(r['id'],r['op'],stage,result,link=link)
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
            result=self._helper({'op':op,'origin':actor,'changes':r['changes'],'expected_digest':expected})
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
