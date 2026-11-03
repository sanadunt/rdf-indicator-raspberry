from __future__ import annotations
import collections
import math
import queue
import re
import sqlite3
import threading
import time
from .util import digest, now_ms, finite, integer, compact
from .journal import TERMINAL
from .helper import call_helper,HelperUnavailable
from .config import PROFILES
from .source import SQUELCH_MODES, squelch_is_auto

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
# A command rejected before the journal is remembered this long (longer than any command deadline), so a
# duplicate delivery of the same id gets the same REJECTED answer instead of a second evaluation.
REJECTED_MEMORY_S=60; REJECTED_MEMORY_MAX=64
# Each helper RPC waits at most HELPER_TIMEOUT_S. Progress ACKs carry result.final_within_ms, the latest the
# final ACK leaves, summed from these and the configured timeouts; FINAL_ACK_SLACK_S covers the 0.25 s polling.
HELPER_TIMEOUT_S=8; SETTLE_DIGEST_S=4.0; FINAL_ACK_SLACK_S=2.0
COMMAND_FIELDS=frozenset({'v','id','sid','boot','issued_ms','expires_ms','base_rev','op','changes','desired','profile',
                          'target_id','prepare_id','challenge'})
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
    'SPACING_SET_BY_ENGINE_BAND':'Di band ini KrakenSDR sendiri memasang jarak UCA (VHF 0,3 m, UHF 0,128 m); jarak lain tidak akan dipakai. Tidak ada yang ditulis.',
    'NUMBER_OUT_OF_RANGE':'Nilai di luar rentang yang diizinkan; tidak ada yang ditulis.',
    'CENTER_CHANGE_REQUIRES_VFO0_TARGET':'Frekuensi center harus dikirim bersama frekuensi VFO0.',
    'MVP_CENTER_MUST_EQUAL_VFO0':'Frekuensi center dan VFO0 harus sama.',
    'INTEGER_OUT_OF_RANGE':'Nilai bilangan bulat di luar rentang yang diizinkan.',
    'SQUELCH_MODE_INVALID':'Mode squelch harus Manual, Auto, Auto Channel, atau Default.',
    'JOURNAL_UNAVAILABLE':'Journal perintah di Edge tidak bisa ditulis; perintah tidak dijalankan.',
    'COMMAND_TOO_LARGE':'Perintah lebih besar dari 4.096 byte dan tidak diproses.',
    'COMMAND_NOT_JSON':'Isi perintah bukan JSON yang sah, jadi tidak bisa dijawab per ID.',
    'COMMAND_QUEUE_FULL':'Antrean perintah masuk penuh; perintah dibuang tanpa diproses.',
    'RETAINED_COMMAND_REJECTED':'Perintah retained ditolak; kirim perintah tanpa flag retain.',
    'INVALID_COMMAND_ACTOR':'Asal perintah tidak dikenal.',
    'COMMAND_OBJECT_REQUIRED':'Isi perintah harus berupa objek JSON.',
    'UNKNOWN_COMMAND_FIELD':'Perintah memuat field yang tidak dikenal (kode lama; field asing kini diabaikan).',
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
    'REBOOT_DISABLED':'Reboot lokal belum disetujui root (rdf-node controls approve --reboot).',
    'SHUTDOWN_DISABLED':'Shutdown lokal belum disetujui root (rdf-node controls approve --shutdown).',
    'INVALID_REBOOT_ID':'ID persiapan reboot kosong atau terlalu panjang.',
    'INVALID_SHUTDOWN_ID':'ID persiapan shutdown kosong atau terlalu panjang.',
    'REBOOT_CHALLENGE_INVALID':'Challenge reboot salah, sudah dipakai, atau kedaluwarsa (30 dtk); ulangi dari persiapan.',
    'SHUTDOWN_CHALLENGE_INVALID':'Challenge shutdown salah, sudah dipakai, atau kedaluwarsa (30 dtk); ulangi dari persiapan.',
    'INVALID_APPROVED_PATH':'Path yang disetujui root tidak valid.',
    'INVALID_RPC':'Permintaan ke helper kontrol tidak valid.',
    'INVALID_RPC_ARGUMENT':'Argumen permintaan ke helper kontrol tidak valid.',
    'UNSUPPORTED_RPC':'Helper kontrol tidak mengenal permintaan ini; versi helper mungkin berbeda.',
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
        # id -> (request digest, op, result, monotonic time) of commands rejected before the journal.
        self.rejected=collections.OrderedDict()
        # PPP restart has its own lane: an SDR verification (BUSY) must never block link recovery.
        self.ppp_lock=threading.Lock(); self.ppp_active=None; self.ppp_last=None; self.ppp_thread=None
    def start(self): self.thread.start()
    def stop(self): self.stop_event.set(); self.thread.join(3)
    def _helper(self,r,timeout=HELPER_TIMEOUT_S):
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
        a=self.agent; origin=self.origin(actor,link); ignored=[]
        try:
            if retained: raise ValueError('RETAINED_COMMAND_REJECTED')
            if actor not in ('ground-controller','local-admin'): raise ValueError('INVALID_COMMAND_ACTOR')
            if not isinstance(request,dict): raise ValueError('COMMAND_OBJECT_REQUIRED')
            # Fields this Edge does not know are dropped and named in the ACK, so a newer Ground can still
            # command it. Everything that keeps a command safe (session, deadline, revision, value limits,
            # each operation's own fields) is still required below.
            ignored=sorted(str(k)[:64] for k in request if k not in COMMAND_FIELDS)[:16]
            if ignored: request={k:v for k,v in request.items() if k in COMMAND_FIELDS}
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
            with self.lock: seen=self.rejected.get(id)
            if seen and time.monotonic()-seen[3]<=REJECTED_MEMORY_S:
                # A repeated delivery (MQTT DUP resend, a restarted radio bridge): the reason it was refused
                # (BUSY, say) may be gone now, but Ground already holds the REJECTED as final.
                if seen[0]!=digest(request): raise ValueError('COMMAND_ID_CONFLICT')
                self._send(id,seen[1],'REJECTED',seen[2],link=link)
                return {'id':id,'stage':'REJECTED','result':seen[2]}
            extra={'ignored':ignored} if ignored else {}
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
                    result={'revision':source.revision,'proof':a.config_proof,**extra}
                    with a.settings_request_lock:
                        if a.settings_request_id is not None: raise ValueError('SETTINGS_REQUEST_PENDING')
                        self.journal.accept(request,actor,origin); self.journal.update(id,'APPLIED',result)
                        a.settings_request_id=id; a.settings_request_payload=settings_payload
                        a.settings_request_link=link
                else:
                    target=self.journal.lookup(str(request.get('target_id','')))
                    result={'operation':self.public(target) if target else None,**extra}
                    self.journal.accept(request,actor,origin); self.journal.update(id,'APPLIED',result)
                self._send(id,op,'APPLIED',result,link=link)
                return {'id':id,'stage':'APPLIED','result':result}
            if a.demo: raise ValueError('DEMO_NO_HARDWARE_CONTROL')
            if actor=='ground-controller' and not a.capabilities()['remote_commands']:
                raise ValueError('REMOTE_COMMANDS_DISABLED')
            # A Pi without RTC gets NTP over the PPP link, so the clock is untrusted exactly when a local
            # PPP restart is needed; its deadline uses the same host clock. It also never touches settings.
            local_ppp=op=='ppp.restart' and actor=='local-admin'
            clock=a.monitor.snapshot()
            # A TEST clock (laptop testing) may time a stream.set; anything the helper runs needs a verified clock.
            test_clock=clock.get('clock_state')=='TEST' and op not in HELPER_OPS
            if not local_ppp and not clock['clock_trusted'] and not test_clock: raise ValueError('CLOCK_UNTRUSTED')
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
                        from .source import STRING_FIELDS
                        changes=request.get('changes')
                        if not isinstance(changes,dict) or not changes or set(changes)-set(FIELD_MAP): raise ValueError('INVALID_PATCH')
                        for k,v in changes.items():
                            if k not in STRING_FIELDS: finite(v,-200,5e9)
                if op=='processing.set' and request.get('desired') not in ('RUNNING','STOPPED'): raise ValueError('INVALID_DESIRED_STATE')
                if op=='config.patch':
                    from .helper import FIELD_MAP
                    from .source import STRING_FIELDS
                    changes=request.get('changes')
                    if not isinstance(changes,dict) or not changes or set(changes)-set(FIELD_MAP): raise ValueError('INVALID_PATCH')
                    for k,v in changes.items():
                        if k not in STRING_FIELDS: finite(v,-200,5e9)
            if op in HELPER_OPS: self._require_helper()
            meta=self._before(op,request)
            if op=='ppp.restart':
                return self._submit_ppp(request,actor,link,origin,meta,extra)
            with self.lock:
                if self.busy: raise ValueError('BUSY')
                self.journal.accept(request,actor,origin,meta)
                self.busy=True; self.active_id=id
                self.queue.put_nowait((request,actor,link))
            within=self._accepted_within_s(op)
            self._send(id,op,'ACCEPTED',{**extra,'final_within_ms':self.within_ms(within)} if within else extra,link=link)
            return {'id':id,'stage':'ACCEPTED',**({'result':extra} if extra else {})}
        except Exception as e:
            if CODE_RE.fullmatch(str(e)) and isinstance(e,ValueError): code=str(e)
            elif isinstance(e,sqlite3.Error): code='JOURNAL_UNAVAILABLE'
            elif isinstance(e,(TypeError,ValueError,KeyError,OverflowError)): code='INVALID_COMMAND_FIELD'
            else: code='INTERNAL_ERROR'
            return self.reject(request,actor,link,code,e,ignored)
    def reject(self,request,actor,link,code,error=None,ignored=()):
        """Answer a command that will not run: ACK it when its id is readable and keep it visible."""
        result=error_result(code,error)
        if ignored: result['ignored']=list(ignored)
        if actor=='ground-controller':
            op_name=request.get('op') if isinstance(request,dict) and request.get('op') in OPS else 'unknown'
            remember=getattr(self.agent,'remote_rejected',None)
            if remember: remember(self.origin(actor,link),op_name,code)
        if isinstance(request,dict) and isinstance(request.get('id'),str) and len(request['id'])<=80:
            op=str(request.get('op','unknown'))[:64]
            self._send(request['id'],op,'REJECTED',result,link=link)
            # COMMAND_ID_CONFLICT answers for an id that belongs to another command; keep that one's answer.
            if code!='COMMAND_ID_CONFLICT' and re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}',request['id']):
                try: h=digest(request)
                except (TypeError,ValueError): h=None
                if h:
                    with self.lock:
                        now=time.monotonic(); self.rejected.pop(request['id'],None)
                        self.rejected[request['id']]=(h,op,result,now)
                        while self.rejected and (len(self.rejected)>REJECTED_MEMORY_MAX or
                                                 now-next(iter(self.rejected.values()))[3]>REJECTED_MEMORY_S):
                            self.rejected.popitem(last=False)
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
    def _submit_ppp(self,request,actor,link,origin,meta,extra=None):
        a=self.agent; id=request['id']
        with self.ppp_lock:
            recovering=getattr(a,'ppp_recovery_active',lambda:False)()
            if self.ppp_active or recovering: raise ValueError('PPP_RESTART_IN_PROGRESS')
            if self.ppp_last is not None and time.monotonic()-self.ppp_last<PPP_RESTART_MIN_GAP_S:
                raise ValueError('PPP_RESTART_TOO_SOON')
            self.journal.accept(request,actor,origin,meta)
            self.ppp_active=id; self.ppp_last=time.monotonic()
        self._send(id,'ppp.restart','ACCEPTED',extra,link=link)
        self.ppp_thread=threading.Thread(target=self._run_ppp,args=(request,actor,link,origin),daemon=True,name='ppp-restart')
        self.ppp_thread.start()
        return {'id':id,'stage':'ACCEPTED',**({'result':extra} if extra else {})}
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
                def progress(stage,result,within=None,r=r,link=link):
                    # An intermediate ACK, so Ground can follow a long operation (live retune, fallback). Its
                    # final_within_ms is sent, not journaled: it counts from this ACK. A caller whose next step
                    # depends on a decision it already made passes `within` from that decision.
                    self._stage(r,stage,result); within=self._progress_within_s(stage,result) if within is None else within
                    self._send(r['id'],r['op'],stage,{**result,'final_within_ms':self.within_ms(within)} if within else result,link=link)
                stage,result=self._execute(r,actor,progress)
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
    # ---- final_within_ms: the latest the final ACK leaves, from the configured timeouts ----
    @staticmethod
    def within_ms(seconds): return int(math.ceil(seconds*1000))
    def _start_within_s(self):
        # VERIFYING{RESTART}: start RPC, then DAQ fresh and healthy within start_timeout_seconds, then 1 s.
        return HELPER_TIMEOUT_S+self.cfg['control']['start_timeout_seconds']+1+FINAL_ACK_SLACK_S
    def _restart_within_s(self):
        # APPLYING{RESTART}: stop RPC + 30 s stop check, settled digest + write RPC, then the start above.
        return HELPER_TIMEOUT_S+30+SETTLE_DIGEST_S+HELPER_TIMEOUT_S+self._start_within_s()
    def _accepted_within_s(self,op):
        """Bound for the ACCEPTED ACK of a worker operation, or None where none is given."""
        c=self.cfg['control']; pickup=0.25
        if op=='config.patch':
            # Worst path: live write and retune window, then the restart fallback. The worker decides on the
            # fallback later, from the helper status it reads then, so the bound counts it whenever it can be.
            return pickup+SETTLE_DIGEST_S+HELPER_TIMEOUT_S+self._live_within_s(bool(c['processing_enabled']))
        if op in ('processing.set','service.restart'):
            return pickup+HELPER_TIMEOUT_S+c['start_timeout_seconds']+FINAL_ACK_SLACK_S
        if op in ('system.reboot.prepare','system.reboot.execute','system.shutdown.prepare','system.shutdown.execute'):
            return pickup+HELPER_TIMEOUT_S+FINAL_ACK_SLACK_S
        return None
    def _progress_within_s(self,stage,result):
        """Bound for a config.patch progress ACK, from the step it reports."""
        method=result.get('method')
        if stage=='APPLYING' and method=='RESTART': return self._restart_within_s()
        if stage=='VERIFYING' and method=='RESTART': return self._start_within_s()
        return None
    def _live_within_s(self,restart):
        """VERIFYING{LIVE}: the retune window, then the restart fallback when the live change does not hold.
        `restart` is the fallback decision _config_patch_apply made, not the helper status read again now."""
        return (self.cfg['control']['retune_live_window_seconds']+FINAL_ACK_SLACK_S+
                (self._restart_within_s() if restart else 0))
    # ---- config.patch: live retune first, engine restart when it is needed or the live one fails ----
    # Some SDR engines re-apply their own band gain on a live retune (rdfsdr: 25.4 dB in VHF) and can lose
    # DAQ sync; at start they take settings.json as written. So a gain change or a large frequency jump goes
    # straight to stop -> write -> start, and a live change that the engine overrides or that leaves the DAQ
    # unhealthy falls back to the same restart. Ground follows each step through intermediate ACKs.
    def _settled_digest(self,timeout=SETTLE_DIGEST_S):
        a=self.agent; end=time.monotonic()+timeout; last=None; stable=0
        while time.monotonic()<end and not self.stop_event.is_set():
            d=a.source.raw_digest
            if d and d==last: stable+=1
            else: stable=0; last=d
            if stable>=4: return d
            self.stop_event.wait(0.25)
        return a.source.raw_digest
    def _write_settings(self,actor,changes):
        expected=self._settled_digest()
        if not expected: raise ValueError('SETTINGS_UNAVAILABLE')
        return self._helper({'op':'config.patch','origin':actor,'changes':changes,'expected_digest':expected})
    def _daq_healthy_since(self,t0):
        a=self.agent; st=a.source.status; m=a.monitor.snapshot()
        return bool(st and st.get('timestamp_ms',0)>t0 and a.source.view(m['clock_trusted'])['daq']['healthy'])
    def _lifecycle(self,actor,desired,timeout):
        a=self.agent; started=now_ms()
        self._helper({'op':'processing.set','origin':actor,'desired':desired})
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline and not self.stop_event.is_set():
            m=a.monitor.snapshot()
            if desired=='STOPPED' and m['service_state'] in ('INACTIVE','FAILED') and m.get('cgroup_empty') is True: return True
            if desired=='RUNNING' and m['service_state']=='ACTIVE' and self._daq_healthy_since(started): return True
            self.stop_event.wait(0.25)
        return False
    def _restart_available(self):
        a=self.agent
        return bool(self.cfg['control']['processing_enabled'] and a.helper_status.get('allow_lifecycle'))
    def _patch_with_restart(self,r,actor,intended,reason,progress):
        a=self.agent; c=self.cfg['control']
        progress('APPLYING',{'method':'RESTART','reason':reason,'changes':intended})
        if not self._lifecycle(actor,'STOPPED',30):
            return 'OUTCOME_UNKNOWN',{'method':'RESTART','reason':reason,'error':'ENGINE_DID_NOT_STOP','persisted':False}
        safe=a.source.safe
        missing={k:v for k,v in intended.items() if safe.get(k)!=v}
        if missing: self._write_settings(actor,missing)
        progress('VERIFYING',{'method':'RESTART','reason':reason,'persisted':True})
        if not self._lifecycle(actor,'RUNNING',c['start_timeout_seconds']):
            return 'OUTCOME_UNKNOWN',{'method':'RESTART','reason':reason,'persisted':True,
                                      'error':'DAQ_NOT_HEALTHY_AFTER_RESTART','revision':a.source.revision}
        self.stop_event.wait(1.0)
        moved=sorted(k for k,v in intended.items() if a.source.safe.get(k)!=v
                     and not (k=='vfo0_squelch_db' and squelch_is_auto(a.source.safe)))
        if moved:
            return 'PERSISTED_UNVERIFIED',{'method':'RESTART','reason':reason,'persisted':True,'revision':a.source.revision,
                                           'error':'ENGINE_OVERRODE_AT_START','fields':moved}
        a.config_proof='runtime'
        return 'APPLIED',{'method':'RESTART','reason':reason,'persisted':True,'revision':a.source.revision,
                          'proof':{'daq':'FRESH_HEALTHY_AFTER_RESTART','settings':'INTACT_AFTER_START'}}
    @staticmethod
    def _band_spacing(safe,center_hz):
        """The UCA radius rdfsdr sets itself on every retune and at start, or None where it keeps the file's.
        A settings file without ant_arrangement starts as UCA (rdfsdr's start-up default)."""
        if safe.get('ant_arrangement','UCA')!='UCA' or not isinstance(center_hz,(int,float)): return None
        mhz=center_hz/1e6
        return 0.3 if 30<=mhz<300 else 0.128 if 300<=mhz<=3000 else None
    def _config_patch(self,r,actor,progress):
        a=self.agent; changes=dict(r['changes'])
        before=dict(a.source.safe); running=a.monitor.snapshot()['service_state']=='ACTIVE'
        # The helper's own checks first, with the same types it writes, so the band rule below sees the
        # numbers the file will hold and a bad frequency gets its own error, not a spacing one.
        try:
            for key in ('center_frequency_hz','vfo0_frequency_hz'):
                if key in changes: changes[key]=integer(changes[key],1,10**10)
            if 'center_frequency_hz' in changes and changes['center_frequency_hz']!=changes.get('vfo0_frequency_hz'):
                raise ValueError('MVP_CENTER_MUST_EQUAL_VFO0' if 'vfo0_frequency_hz' in changes else 'CENTER_CHANGE_REQUIRES_VFO0_TARGET')
            if 'ant_spacing_m' in changes: changes['ant_spacing_m']=round(finite(changes['ant_spacing_m'],0.05,1.0),4)
            if 'vfo0_squelch_db' in changes: changes['vfo0_squelch_db']=int(round(finite(changes['vfo0_squelch_db'],-200,50)))
            if 'vfo0_squelch_mode' in changes and changes['vfo0_squelch_mode'] not in SQUELCH_MODES: raise ValueError('SQUELCH_MODE_INVALID')
        except (ValueError,TypeError) as e:
            code=str(e) if str(e) in ERROR_MESSAGES else 'INVALID_COMMAND_FIELD'
            return 'FAILED',{**error_result(code),'persisted':False}
        band=self._band_spacing(before,changes.get('center_frequency_hz',before.get('center_frequency_hz')))
        added=False
        if band is not None:
            if 'ant_spacing_m' in changes:
                # In VHF/UHF the engine's band radius always wins: another spacing could never run.
                if abs(changes['ant_spacing_m']-band)>5e-4:
                    return 'FAILED',{**error_result('SPACING_SET_BY_ENGINE_BAND'),'persisted':False,'band_spacing_m':band}
                changes['ant_spacing_m']=band
            elif before.get('ant_spacing_m')!=band:
                # Any write makes the engine reload the file's spacing live: write the band radius too, so
                # settings.json (and state.rf.uca) says what the engine runs and an old radius never returns.
                changes['ant_spacing_m']=band; added=True
        # A squelch level only counts in Manual: setting one switches an automatic squelch to Manual.
        manual=False
        if 'vfo0_squelch_db' in changes and 'vfo0_squelch_mode' not in changes and squelch_is_auto(before):
            changes['vfo0_squelch_mode']='Manual'; manual=True
        # A Manual squelch runs int(level): a fractional level left in the file (an automatic squelch's saved
        # level, or an older Edge) is written as the engine runs it, so a later retune does not look like an override.
        level=before.get('vfo0_squelch_db')
        if ('vfo0_squelch_db' not in changes and not squelch_is_auto({**before,**changes})
                and isinstance(level,float) and level!=int(level)):
            changes['vfo0_squelch_db']=int(level)
        stage,result=self._config_patch_apply(r,actor,progress,changes,before,running)
        if added: result['band_spacing_m']=band
        if manual: result['squelch_mode']='Manual'
        return stage,result
    def _config_patch_apply(self,r,actor,progress,changes,before,running):
        a=self.agent; c=self.cfg['control']
        new_f=changes.get('center_frequency_hz',changes.get('vfo0_frequency_hz'))
        old_f=before.get('center_frequency_hz')
        jump=abs(new_f-old_f) if isinstance(new_f,int) and isinstance(old_f,int) else 0
        restart=self._restart_available()
        reason=None
        if running and restart:
            if 'gain_db' in changes: reason='GAIN_CHANGE'
            elif jump>c['retune_live_max_hz']: reason='LARGE_FREQUENCY_JUMP'
        if reason: return self._patch_with_restart(r,actor,changes,reason,progress)
        started=now_ms(); old_digest=a.source.raw_digest
        self._write_settings(actor,changes)
        if not running:
            # The engine reads settings.json when it starts; nothing runs it now.
            return 'PERSISTED_UNVERIFIED',{'method':'FILE','persisted':True,'revision':a.source.revision,
                                           'reason':'ENGINE_NOT_RUNNING'}
        progress('VERIFYING',{'method':'LIVE','persisted':True},self._live_within_s(restart))
        # Values that must survive the retune: everything we did not change (gain above all). Not the
        # antenna spacing: the engine sets its band value (0.3 m VHF, 0.128 m UHF) on a retune by design;
        # it is checked only when the patch asks for a spacing.
        # An automatic squelch is the engine's to change (it saves its computed level on a retune).
        auto_sq=squelch_is_auto({**before,**changes})
        keep={k:v for k,v in before.items() if k in ('center_frequency_hz','gain_db','vfo0_frequency_hz',
                                                      'vfo0_bandwidth_hz','vfo0_squelch_db','vfo0_squelch_mode')
              and k not in changes and not (auto_sq and k=='vfo0_squelch_db')}
        expect={k:v for k,v in changes.items() if not (auto_sq and k=='vfo0_squelch_db')}
        deadline=time.monotonic()+c['retune_live_window_seconds']; healthy_since=None; why='DAQ_NOT_HEALTHY_AFTER_RETUNE'
        while time.monotonic()<deadline and not self.stop_event.is_set():
            safe=a.source.safe
            if a.source.raw_digest!=old_digest:
                # Our write is visible; any field now off its intended value was rewritten by the engine.
                overrode=sorted(k for k,v in {**keep,**expect}.items() if safe.get(k)!=v)
                if overrode: why='ENGINE_OVERRODE:'+','.join(overrode); break
            if all(safe.get(k)==v for k,v in expect.items()) and self._daq_healthy_since(started):
                healthy_since=healthy_since or time.monotonic()
                # Healthy for a few seconds in a row, not just the status written before the retune settled.
                if time.monotonic()-healthy_since>=c['retune_settle_seconds']:
                    a.config_proof='runtime'
                    proof={'daq':'FRESH_HEALTHY_AFTER_RETUNE','settings':'KEPT_BY_ENGINE'}
                    rec=a.source.record
                    if rec and rec['timestamp_ms']>started and rec['frequency_hz']==changes.get('vfo0_frequency_hz'):
                        proof['vfo0_frequency_hz']='FRESH_DOA_FREQUENCY'
                    return 'APPLIED',{'method':'LIVE','persisted':True,'revision':a.source.revision,'proof':proof}
            else:
                healthy_since=None
            self.stop_event.wait(0.25)
        if not restart:
            a.config_proof='persisted_unverified'
            return 'PERSISTED_UNVERIFIED',{'method':'LIVE','persisted':True,'revision':a.source.revision,
                                           'reason':'NATIVE_RUNTIME_EVIDENCE_INCOMPLETE','live_failure':why}
        # Fall back: restart with everything we meant to have, including what the engine overrode.
        return self._patch_with_restart(r,actor,{**keep,**changes},'LIVE_FAILED:'+why,progress)
    def _execute(self,r,actor,progress=None):
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
            return self._config_patch(r,actor,progress or (lambda stage,result,within=None:None))
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
