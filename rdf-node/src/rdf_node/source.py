from __future__ import annotations
import csv
import io
import hashlib
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from .util import finite, integer, stable_read, strict_json, digest, now_ms

# Native keys and units follow project documentation. No secret fields exported.
FIELD_MAP = {
    'center_frequency_hz': ('center_freq', 1e6),
    'gain_db': ('uniform_gain', 1),
    'vfo0_frequency_hz': ('vfo_freq_0', 1),
    'vfo0_bandwidth_hz': ('vfo_bw_0', 1),
    'vfo0_squelch_db': ('vfo_squelch_0', 1),
    # UCA radius (adjacent-element spacing for ULA), metres. The engine's own band rule sets 0.3 m in
    # VHF and 0.128 m in UHF on every retune and at start; outside those bands it keeps this value.
    'ant_spacing_m': ('ant_spacing_meters', 1),
    # rdfsdr computes DoA only while the VFO's peak exceeds its squelch. Manual uses vfo0_squelch_db; Auto is the
    # mean of the whole spectrum + 5 dB, Auto Channel the mean around the VFO + 3 dB; Default follows
    # vfo_default_squelch_mode.
    'vfo0_squelch_mode': ('vfo_squelch_mode_0', 1),
}
# Fields carried as text, with the values the engine knows.
SQUELCH_MODES = ('Manual', 'Auto', 'Auto Channel', 'Default')
STRING_FIELDS = {'vfo0_squelch_mode': SQUELCH_MODES}

def squelch_is_auto(safe:dict) -> bool:
    """True when rdfsdr sets VFO0's squelch itself (its own rule, Default resolved). Missing keys take rdfsdr's
    defaults: vfo_squelch_mode_0 'Default', vfo_default_squelch_mode 'Auto'."""
    mode=safe.get('vfo0_squelch_mode','Default')
    if mode=='Default': mode=safe.get('vfo_default_squelch_mode','Auto')
    return mode in ('Auto','Auto Channel')

def safe_settings(data:dict, invalid:list|None=None) -> dict:
    """Safe subset of native settings. A field that does not parse is left out (and named in
    `invalid` when given) instead of making every other setting unavailable."""
    if not isinstance(data,dict):
        raise ValueError('SETTINGS_OBJECT_REQUIRED')
    result={}
    for name,(key,scale) in FIELD_MAP.items():
        if name in STRING_FIELDS:
            if key in data:
                if data[key] in STRING_FIELDS[name]: result[name]=data[key]
                elif invalid is not None: invalid.append(name)
            continue
        if key in data:
            try: n=finite(data[key],-1e6,5e9)*scale
            except (ValueError,TypeError,OverflowError):
                if invalid is not None: invalid.append(name)
                continue
            result[name]=int(round(n)) if name.endswith('_hz') else n
    for key in ('ant_arrangement','doa_method','active_vfos','output_vfo','en_doa','vfo_default_squelch_mode'):
        val=data.get(key)
        if isinstance(val,(str,int,bool)) and len(str(val))<64:
            result[key]=val
    return result

def _optional(row, index, parse, name, null_fields):
    # Metadata KrakenSDR may leave empty or NaN (no GPS, no compass): null, never a lost record.
    try: return parse(row[index])
    except (ValueError,TypeError,OverflowError):
        null_fields.append(name); return None

def parse_csv(data: bytes, settings: dict | None, output_vfo=0) -> dict:
    if b'\x00' in data:
        raise ValueError('NUL_IN_CSV')
    lines=[r for r in csv.reader(io.StringIO(data.decode('utf-8'))) if r and any(x.strip() for x in r)]
    if not lines:
        raise ValueError('EMPTY_CSV')
    parsed=[]
    for row in lines:
        # A trailing delimiter in upstream is tolerated; not arbitrary short rows.
        if len(row)==378 and not row[-1].strip():
            row=row[:-1]
        if len(row)!=377:
            raise ValueError('CSV_FIELD_COUNT')
        # Timestamp, angle, confidence, power, frequency, and the 360 values are the sample itself and
        # stay strict; the metadata in between becomes null with its name in `null_fields`.
        null_fields=[]
        rec={
            'timestamp_ms':integer(row[0],1000000000000,9999999999999),
            'raw_doa_deg':finite(row[1],0,360)%360,
            'confidence_native_db':finite(row[2],-327.67,327.67),
            'power_native_db':finite(row[3],-1e6,1e6),
            'frequency_hz':integer(row[4],1,0xffffffff),
            'array':row[5][:24],
            'acquisition_latency_ms':_optional(row,6,lambda v:finite(v,0,3600000),'acquisition_latency_ms',null_fields),
            'station_id':row[7][:64],
            'latitude':_optional(row,8,lambda v:finite(v,-90,90),'latitude',null_fields),
            'longitude':_optional(row,9,lambda v:finite(v,-180,180),'longitude',null_fields),
            'gps_heading_deg':_optional(row,10,lambda v:finite(v,0,360),'gps_heading_deg',null_fields),
            'compass_heading_deg':_optional(row,11,lambda v:finite(v,0,360),'compass_heading_deg',null_fields),
            'heading_source':row[12][:32],
            'values':[finite(x,-1e8,1e8) for x in row[17:]],
            'null_fields':null_fields,
        }
        parsed.append(rec)
    if settings:
        active=integer(settings.get('active_vfos',1),1,16)
        selected=integer(settings.get('output_vfo',output_vfo),0,15)
        if selected != output_vfo or selected>=active:
            raise ValueError('OUTPUT_VFO_MISMATCH')
        freq=settings.get(f'vfo_freq_{output_vfo}')
        if freq is not None:
            candidates=[r for r in parsed if r['frequency_hz']==integer(freq,1,0xffffffff)]
            if len(candidates)!=1:
                raise ValueError('VFO_RECORD_AMBIGUOUS_OR_MISSING')
            return candidates[0]
    if len(parsed)!=1:
        raise ValueError('MULTIPLE_RECORDS_WITHOUT_VFO_AUTHORITY')
    return parsed[0]

def parse_doa_xml(data:bytes) -> dict:
    if not data: raise ValueError('XML_EMPTY')
    if b'\x00' in data: raise ValueError('XML_NUL')
    upper=data.upper()
    if b'<!DOCTYPE' in upper or b'<!ENTITY' in upper: raise ValueError('XML_DTD_FORBIDDEN')
    try: root=ET.fromstring(data)
    except ET.ParseError as e: raise ValueError('XML_PARSE_ERROR') from e
    if root.tag!='DATA': raise ValueError('XML_ROOT_INVALID')
    def field(name):
        matches=[child for child in root if child.tag==name]
        if len(matches)!=1 or list(matches[0]) or matches[0].text is None: raise ValueError('XML_FIELD_INVALID')
        return matches[0].text.strip()
    return {'source_timestamp_ms':integer(field('TIME'),1000000000000,9999999999999),
            'raw_doa_deg':finite(field('DOA'),0,360),
            'frequency_mhz':finite(field('FREQUENCY'),0.001,5000)}


def _db(v):
    if v is None: return None
    if isinstance(v,bool) or not isinstance(v,(int,float)): raise ValueError('VFO_PEAKS_INVALID')
    return finite(v,-400,200)

def parse_vfo_peaks(v) -> list:
    # rdfsdr's channel peak per VFO and the squelch it was held against (deploy/kraken/vfo_peaks.patch).
    # Measured every frame, also when nothing clears squelch; stock KrakenSDR writes no such key.
    if not isinstance(v,list) or len(v)>16: raise ValueError('VFO_PEAKS_INVALID')
    out=[]
    for e in v:
        if not isinstance(e,dict): raise ValueError('VFO_PEAKS_INVALID')
        out.append({'vfo':integer(e.get('vfo'),0,15),'frequency_hz':integer(e.get('frequency_hz'),1,0xffffffff),
                    'bandwidth_hz':integer(e.get('bandwidth_hz'),1,100000000),
                    'peak_db':_db(e.get('peak_db')),'squelch_db':_db(e.get('squelch_db')),
                    'timestamp_ms':integer(e.get('timestamp_ms'),1000000000000,9999999999999)})
    return out

def parse_status(data:bytes) -> dict:
    j=strict_json(data)
    if not isinstance(j,dict):
        raise ValueError('STATUS_OBJECT_REQUIRED')
    ts=integer(j.get('timestamp_ms'),1000000000000,9999999999999)
    daq=j.get('daq_status')
    if not isinstance(daq,dict):
        daq={}
    # Boolean fields must not become true because of nonempty strings.
    def b(v):
        if type(v) is bool: return v
        if type(v) is int and v in (0,1): return bool(v)
        return None
    # Only timestamp_ms is required; a counter that is missing or malformed is null and named.
    null_fields=[]
    def counter(obj,key,name):
        if key not in obj: return None
        try: return integer(obj[key],0)
        except (ValueError,TypeError,OverflowError):
            null_fields.append(name); return None
    out=dict(timestamp_ms=ts,daq_ok=b(j.get('daq_ok')),
             dropped_frames=counter(j,'daq_num_dropped_frames','dropped_frames'),
             frame_index=counter(daq,'data_frame_index','frame_index'),
             sync={k:b(daq.get(src)) for k,src in [('frame','frame_sync'),('sample_delay','sample_delay_sync'),('iq','iq_sync')]},
             adc_overdrive=b(daq.get('adc_overdrive')),gps_status=str(j.get('gps_status','Unknown'))[:40],
             rf_center_frequency_hz=daq.get('rf_center_frequency_hz',daq.get('rf_center_freq')),
             gain_db=daq.get('gain_db'),null_fields=null_fields)
    # None when the key is missing (stock KrakenSDR) or malformed (named; the rest of the status still counts).
    out['vfo_peaks_reported']='vfo_peaks' in j; out['vfo_peaks']=None
    if out['vfo_peaks_reported']:
        try: out['vfo_peaks']=parse_vfo_peaks(j['vfo_peaks'])
        except (ValueError,TypeError,OverflowError): null_fields.append('vfo_peaks')
    return out

class Source:
    def __init__(self, cfg, journal):
        self.cfg=cfg; self.journal=journal
        self.path=Path(cfg['source']['share_dir']) if cfg['source']['share_dir'] else None
        self.record=None; self.status=None; self.settings={}; self.safe={}
        self.raw_settings=None; self.settings_seen=None; self.settings_invalid=[]
        self.raw_digest=None; self.safe_digest=None; self.config_mtime_ms=0
        self.seq=0; self.record_signature=None; self.status_signature=None
        self.record_seen=None; self.status_seen=None; self.frame_seen=None; self.last_frame=None
        self.config_changed_mono=time.monotonic(); self.revision=None
        self.error=None; self.status_error=None; self.config_error=None
        self.parse_errors=0; self.frame_reset=False; self.drop_delta=None; self.last_drop=None
        self.diagnostic_record=None; self.diagnostic_signature=None; self.diagnostic_seen=None
        self.diagnostic_observed_ms=None; self.diagnostic_error=None
        # When KrakenSDR last wrote DOA_value.html, whatever it wrote: an empty file that keeps being
        # rewritten is a different state from a file that stopped changing.
        self.record_file_mtime_ms=None
        self.source_tick=0; self.next_settings=0; self.next_status=0; self.settings_stat=None
        # Set once status.json has carried vfo_peaks: from then on the peak is reported, stale or not.
        self.peak_capable=False
    def poll(self, force=False):
        self.source_tick+=1
        if not self.path:
            self.error='SOURCE_NOT_CONFIGURED'; return
        limit=self.cfg['source']['max_record_bytes']
        now=time.monotonic()
        if force or now>=self.next_settings:
            self.next_settings=now+1
            try:
                raw=stable_read(self.path/'settings.json',65536)
                j=strict_json(raw)
                invalid=[]; safe=safe_settings(j,invalid)
                h=digest(safe)
                if h!=self.safe_digest:
                    self.revision=self.journal.config_revision(h)
                    self.config_changed_mono=time.monotonic()
                    self.config_mtime_ms=(self.path/'settings.json').stat().st_mtime_ns//1000000
                    self.safe_digest=h
                self.raw_digest=hashlib.sha256(raw).hexdigest(); self.raw_settings=raw
                self.settings_seen=time.monotonic(); self.settings=j; self.safe=safe; self.config_error=None
                self.settings_invalid=invalid
            except (OSError,ValueError,TypeError,KeyError,RecursionError) as e:
                self.config_error=type(e).__name__+':SETTINGS_UNAVAILABLE'
        if force or now>=self.next_status:
            self.next_status=now+0.5
            try:
                raw=stable_read(self.path/'status.json',limit)
                st=parse_status(raw)
                sig=st['timestamp_ms']
                new_status=sig!=self.status_signature
                if new_status:
                    self.status_seen=time.monotonic(); self.status_signature=sig
                idx=st['frame_index']
                self.frame_reset=False
                if idx is not None and idx!=self.last_frame:
                    self.frame_reset=self.last_frame is not None and idx<self.last_frame
                    if self.last_frame is not None and idx>self.last_frame:
                        self.frame_seen=time.monotonic()
                    elif self.frame_reset:
                        self.frame_seen=None
                    self.last_frame=idx
                drop=st['dropped_frames']
                if drop is not None and new_status:
                    self.drop_delta=max(0,drop-self.last_drop) if self.last_drop is not None else None
                    self.last_drop=drop
                self.status=st; self.status_error=None
                if st['vfo_peaks_reported']: self.peak_capable=True
            except (OSError,ValueError,TypeError,KeyError,RecursionError) as e:
                self.status_error=type(e).__name__+':STATUS_UNAVAILABLE'
        try: self.record_file_mtime_ms=(self.path/'DOA_value.html').stat().st_mtime_ns//1000000
        except OSError: self.record_file_mtime_ms=None
        try:
            # One CSV row per active VFO (about 2.7 KB each): the read limit grows with them, up to 16.
            vfos=self.safe.get('active_vfos')
            vfos=vfos if type(vfos) is int and 1<=vfos<=16 else 1
            raw=stable_read(self.path/'DOA_value.html',limit*vfos)
            rec=parse_csv(raw,self.settings or None,self.cfg['source']['output_vfo'])
            # Source timestamp is the identity. Reformatting/re-reading is not a sample.
            sig=(rec['timestamp_ms'],rec['frequency_hz'])
            if self.record_signature is not None and rec['timestamp_ms']<self.record_signature[0]:
                raise ValueError('SOURCE_TIME_REGRESSED')
            if sig!=self.record_signature:
                self.seq+=1
                if self.seq>0xffffffff: raise RuntimeError('SESSION_SEQUENCE_EXHAUSTED_RESTART_AGENT')
                self.record_signature=sig; self.record_seen=time.monotonic()
                rec['q']=self.seq; rec['observed_revision']=self.revision
                rec['config_digest_at_read']=self.safe_digest
                self.record=rec
            self.error=None
        except (OSError,ValueError,TypeError,KeyError,RecursionError) as e:
            self.parse_errors+=1
            self.error=str(e)[:64] if isinstance(e,ValueError) else type(e).__name__+':DOA_UNAVAILABLE'
        try:
            raw=stable_read(self.path/'doa.xml',limit)
            rec=parse_doa_xml(raw); sig=rec['source_timestamp_ms']
            if self.diagnostic_signature is not None and sig<self.diagnostic_signature:
                raise ValueError('DOA_XML_TIME_REGRESSED')
            if sig!=self.diagnostic_signature:
                self.diagnostic_signature=sig; self.diagnostic_seen=time.monotonic()
                self.diagnostic_observed_ms=now_ms(); self.diagnostic_record=rec
            self.diagnostic_error=None
        except (OSError,ValueError,TypeError,KeyError,RecursionError) as e:
            code=str(e)
            self.diagnostic_error=('DOA_'+code if code.startswith('XML_') else
                                   code if code.startswith('DOA_XML_') else 'DOA_XML_UNAVAILABLE')

    def view(self, clock_trusted:bool, mutation=False) -> dict:
        mono=time.monotonic(); utc=now_ms(); rec=self.record; st=self.status
        def age(obj, seen):
            if not obj or seen is None: return None
            # Never refresh the age by reading an unchanged cached source.
            return max(0,utc-obj['timestamp_ms'],int((mono-seen)*1000))
        a=age(rec,self.record_seen); sa=age(st,self.status_seen)
        frame_progress=bool(self.frame_seen is not None and (mono-self.frame_seen)*1000<=self.cfg['freshness']['frame_progress_ms'])
        status_fresh=bool(st and sa is not None and sa<=self.cfg['freshness']['source_status_ms'] and st['timestamp_ms']<=utc+1000 and not self.status_error)
        sync=st['sync'] if st else dict(frame=None,sample_delay=None,iq=None)
        health=bool(status_fresh and st['daq_ok'] is True and all(v is True for v in sync.values())
                    and (frame_progress or not self.cfg['source']['require_frame_progress']))
        reasons=[]
        if not self.path: reasons.append('SETUP_REQUIRED')
        if self.error: reasons.append(self.error)
        if not rec: reasons.append('NO_RECORD')
        if not status_fresh: reasons.append('STATUS_STALE_OR_UNAVAILABLE')
        if not health: reasons.append('DAQ_NOT_HEALTHY')
        if not clock_trusted: reasons.append('CLOCK_UNTRUSTED')
        if rec and (a>self.cfg['freshness']['doa_ms'] or rec['timestamp_ms']>utc+1000): reasons.append('NO_FRESH_DOA')
        mtime=self.record_file_mtime_ms
        if self.path and mtime is not None and utc-mtime>self.cfg['freshness']['doa_ms']:
            reasons.append('DOA_FILE_NOT_UPDATING')
        if not self.cfg['source']['authority_verified']: reasons.append('SOURCE_UNVERIFIED')
        if not self.cfg['source']['angle_verified']: reasons.append('ANGLE_UNVERIFIED')
        if self.config_error or not self.settings or self.safe_digest is None: reasons.append('CONFIG_UNAVAILABLE')
        attrib=bool(rec and self.revision is not None and rec.get('config_digest_at_read')==self.safe_digest
                    and rec['timestamp_ms']>=self.config_mtime_ms)
        if not attrib: reasons.append('CONFIG_ATTRIBUTION_UNVERIFIED')
        if mutation: reasons.append('CONTROL_IN_PROGRESS')
        raw=rec['raw_doa_deg'] if rec else None
        vfo_peak=self.vfo_peak_view(st,status_fresh,sa,utc) if self.peak_capable else None
        angle=(360-raw)%360 if raw is not None and self.cfg['source']['angle_mode']=='theta_mirror' else raw
        valid=not reasons
        return dict(valid=valid,reasons=reasons,state='VALID' if valid else ('SETUP_REQUIRED' if not self.path else 'UNVERIFIED' if any('UNVERIFIED' in r for r in reasons) else 'NO_FRESH_DOA'),
                    relative_doa_deg=angle if valid else None,raw_doa_deg=raw,source_age_ms=a,
                    source_timestamp_ms=rec['timestamp_ms'] if rec else None,
                    frequency_hz=rec['frequency_hz'] if rec else None,
                    confidence_native_db=rec['confidence_native_db'] if rec else None,
                    power_native_db=rec['power_native_db'] if rec else None,
                    angle_convention=self.cfg['source']['angle_mode'],q=rec['q'] if rec else 0,
                    revision=rec.get('observed_revision') if rec and attrib else None,
                    config_attributed=attrib,parse_errors=self.parse_errors,
                    null_fields=list(rec.get('null_fields',())) if rec else [],vfo_peak=vfo_peak,
                    daq=dict(state='HEALTHY' if health else 'DEGRADED' if status_fresh else 'UNKNOWN',
                             healthy=health,sync=sync,source_age_ms=sa,frame_progressing=frame_progress,
                             frame_index=st['frame_index'] if st else None,
                             dropped_frames=st['dropped_frames'] if st else None,drop_delta=self.drop_delta,
                             adc_overdrive=st['adc_overdrive'] if st else None,
                             null_fields=list(st.get('null_fields',())) if st else []))

    def vfo_peak_view(self, st, status_fresh, status_age, utc) -> dict:
        # The output VFO's peak against its squelch from the latest status, also while squelch holds DoA back:
        # it tells a missing signal from a squelch set too high. STALE carries no numbers.
        out=dict(state='STALE',vfo=None,frequency_hz=None,bandwidth_hz=None,peak_db=None,squelch_db=None,
                 margin_db=None,above_squelch=None,age_ms=None)
        want=self.cfg['source']['output_vfo']; peaks=(st or {}).get('vfo_peaks') or []
        e=next((p for p in peaks if p['vfo']==want),None) if want>=0 else (peaks[0] if peaks else None)
        if not (e and status_fresh and e['peak_db'] is not None and e['timestamp_ms']<=utc+1000): return out
        a=max(0,utc-e['timestamp_ms'],status_age or 0)
        if a>self.cfg['freshness']['source_status_ms']: return out
        sq=e['squelch_db']; margin=None if sq is None else round(e['peak_db']-sq,1)
        # rdfsdr compares unrounded values and writes 0.1 dB: a margin of 0.0 cannot say which side it was.
        out.update(state='OK',vfo=e['vfo'],frequency_hz=e['frequency_hz'],bandwidth_hz=e['bandwidth_hz'],
                   peak_db=round(e['peak_db'],1),squelch_db=None if sq is None else round(sq,1),
                   margin_db=margin,above_squelch=None if not margin else margin>0,age_ms=a)
        return out

    def diagnostic_view(self, validation_reasons=()) -> dict:
        mono=time.monotonic(); utc=now_ms(); rec=self.diagnostic_record
        age=max(0,utc-rec['source_timestamp_ms'],int((mono-self.diagnostic_seen)*1000)) if rec else None
        reasons=['DIAGNOSTIC_UNVERIFIED']
        reasons.extend(reason.upper() for reason in (validation_reasons or ()) if isinstance(reason,str))
        if self.diagnostic_error: reasons.append(self.diagnostic_error)
        if rec:
            if rec['source_timestamp_ms']>utc+1000: reasons.append('DOA_XML_TIME_FUTURE')
            if age>self.cfg['freshness']['doa_ms']: reasons.append('DOA_XML_STALE')
        elif not self.diagnostic_error: reasons.append('DOA_XML_UNAVAILABLE')
        return {'available':bool(rec),'source':'doa.xml','trust':'UNVERIFIED',
                'source_timestamp_ms':rec['source_timestamp_ms'] if rec else None,
                'observed_timestamp_ms':self.diagnostic_observed_ms,
                'raw_doa_deg':rec['raw_doa_deg'] if rec else None,
                'frequency_mhz':rec['frequency_mhz'] if rec else None,
                'source_age_ms':age,'validation_reasons':list(dict.fromkeys(reasons))}
