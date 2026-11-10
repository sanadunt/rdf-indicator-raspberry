"""JSON contract for Angular MQTT frames."""
from .angular import upsample_pairs
from .util import finite, integer, strict_json

FLAG_PARSED=1
FLAG_FRESH=2
FLAG_DAQ=4
FLAG_CONVENTION=8
FLAG_CONFIG=16
FLAG_AUTHORITY=32
ALL_FLAGS=63


def decode(payload: bytes) -> dict:
    j=strict_json(payload)
    if not isinstance(j,dict) or j.get('v')!=2 or j.get('encoding')!='json':
        raise ValueError('BAD_ANGULAR_JSON')
    sid=j.get('sid')
    if (not isinstance(sid,str) or len(sid)!=8 or
            any(c not in '0123456789abcdef' for c in sid)):
        raise ValueError('BAD_ANGULAR_SESSION')
    q=integer(j.get('q'),1,0xffffffff)
    timestamp=integer(j.get('timestamp_ms'),1,0x7fffffffffffffff)
    frequency=integer(j.get('frequency_hz'),1,0xffffffff)
    revision=j.get('revision')
    if revision is not None: revision=integer(revision,0,0xfffffffe)
    vfo=integer(j.get('vfo'),0,15)
    convention=integer(j.get('convention'),0,1)
    flags=integer(j.get('flags'),0,ALL_FLAGS)
    raw_doa=j.get('raw_doa_deg')
    if raw_doa is not None: raw_doa=finite(raw_doa,0,360)
    confidence=j.get('confidence_native_db')
    if confidence is not None: confidence=finite(confidence,-327.67,327.67)
    samples=j.get('values')
    # Contract 1.12: 180 points two degrees apart ("Seimbang") are rebuilt into 360 smooth values.
    half=isinstance(samples,list) and len(samples)==180 and j.get('step_deg')==2
    if not isinstance(samples,list) or (len(samples)!=360 and not half):
        raise ValueError('EXPECTED_360_SAMPLES')
    values=[finite(value,-1e8,1e8) for value in samples]
    extra={}
    if half: values=upsample_pairs(values); extra['upsampled_from']=180
    return dict(**extra,version=2,encoding='json',flags=flags,sid=sid,q=q,
                timestamp_ms=timestamp,frequency_hz=frequency,revision=revision,
                vfo=vfo,convention=convention,values=values,raw_doa_deg=raw_doa,
                confidence_native_db=confidence,
                peak_index=max(range(360),key=values.__getitem__))
