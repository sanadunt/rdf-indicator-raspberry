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
}

def safe_settings(data:dict) -> dict:
    if not isinstance(data,dict):
        raise ValueError('SETTINGS_OBJECT_REQUIRED')
    result={}
    for name,(key,scale) in FIELD_MAP.items():
        if key in data:
            n=finite(data[key],-1e6,5e9)*scale
            result[name]=int(round(n)) if name.endswith('_hz') else n
    for key in ('ant_arrangement','doa_method','active_vfos','output_vfo','en_doa'):
        val=data.get(key)
        if isinstance(val,(str,int,bool)) and len(str(val))<64:
            result[key]=val
    return result

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
        rec={
            'timestamp_ms':integer(row[0],1000000000000,9999999999999),
            'raw_doa_deg':finite(row[1],0,360)%360,
            'confidence_native_db':finite(row[2],-327.67,327.67),
            'power_native_db':finite(row[3],-1e6,1e6),
            'frequency_hz':integer(row[4],1,0xffffffff),
            'array':row[5][:24],
            'acquisition_latency_ms':finite(row[6],0,3600000),
            'station_id':row[7][:64],
            'latitude':finite(row[8],-90,90), 'longitude':finite(row[9],-180,180),
            'gps_heading_deg':finite(row[10],0,360), 'compass_heading_deg':finite(row[11],0,360),
            'heading_source':row[12][:32],
            'values':[finite(x,-1e8,1e8) for x in row[17:]],
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
    out=dict(timestamp_ms=ts,daq_ok=b(j.get('daq_ok')),
             dropped_frames=integer(j['daq_num_dropped_frames'],0) if 'daq_num_dropped_frames' in j else None,
             frame_index=integer(daq['data_frame_index'],0) if 'data_frame_index' in daq else None,
             sync={k:b(daq.get(src)) for k,src in [('frame','frame_sync'),('sample_delay','sample_delay_sync'),('iq','iq_sync')]},
             adc_overdrive=daq.get('adc_overdrive'),gps_status=str(j.get('gps_status','Unknown'))[:40],
             rf_center_frequency_hz=daq.get('rf_center_frequency_hz',daq.get('rf_center_freq')),
             gain_db=daq.get('gain_db'))
    return out

class Source:
    def __init__(self, cfg, journal):
        self.cfg=cfg; self.journal=journal
        self.path=Path(cfg['source']['share_dir']) if cfg['source']['share_dir'] else None
        self.record=None; self.status=None; self.settings={}; self.safe={}
        self.raw_digest=None; self.safe_digest=None; self.config_mtime_ms=0
        self.seq=0; self.record_signature=None; self.status_signature=None
        self.record_seen=None; self.status_seen=None; self.frame_seen=None; self.last_frame=None
        self.config_changed_mono=time.monotonic(); self.revision=None
        self.error=None; self.status_error=None; self.config_error=None
        self.parse_errors=0; self.frame_reset=False; self.drop_delta=None; self.last_drop=None
        self.diagnostic_record=None; self.diagnostic_signature=None; self.diagnostic_seen=None
        self.diagnostic_observed_ms=None; self.diagnostic_error=None
        self.source_tick=0; self.next_settings=0; self.next_status=0; self.settings_stat=None
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
                safe=safe_settings(j)
                h=digest(safe)
                if h!=self.safe_digest:
                    self.revision=self.journal.config_revision(h)
                    self.config_changed_mono=time.monotonic()
                    self.config_mtime_ms=(self.path/'settings.json').stat().st_mtime_ns//1000000
                    self.safe_digest=h
                self.raw_digest=hashlib.sha256(raw).hexdigest(); self.settings=j; self.safe=safe; self.config_error=None
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
            except (OSError,ValueError,TypeError,KeyError,RecursionError) as e:
                self.status_error=type(e).__name__+':STATUS_UNAVAILABLE'
        try:
            raw=stable_read(self.path/'DOA_value.html',limit)
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
        if not self.cfg['source']['authority_verified']: reasons.append('SOURCE_UNVERIFIED')
        if not self.cfg['source']['angle_verified']: reasons.append('ANGLE_UNVERIFIED')
        if self.config_error or not self.settings or self.safe_digest is None: reasons.append('CONFIG_UNAVAILABLE')
        attrib=bool(rec and self.revision is not None and rec.get('config_digest_at_read')==self.safe_digest
                    and rec['timestamp_ms']>=self.config_mtime_ms)
        if not attrib: reasons.append('CONFIG_ATTRIBUTION_UNVERIFIED')
        if mutation: reasons.append('CONTROL_IN_PROGRESS')
        raw=rec['raw_doa_deg'] if rec else None
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
                    daq=dict(state='HEALTHY' if health else 'DEGRADED' if status_fresh else 'UNKNOWN',
                             healthy=health,sync=sync,source_age_ms=sa,frame_progressing=frame_progress,
                             frame_index=st['frame_index'] if st else None,
                             dropped_frames=st['dropped_frames'] if st else None,drop_delta=self.drop_delta,
                             adc_overdrive=st['adc_overdrive'] if st else None))

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
