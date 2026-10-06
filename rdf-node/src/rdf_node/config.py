from __future__ import annotations
import copy
import ipaddress
import re
from pathlib import Path
import yaml
from .mqtt_ws import validate_mqtt_host,validate_websocket_path
from .util import finite

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SDR_SERVICE = 'rdfsdr.service'

PROFILES = {
    'control': {'doa_s': 1.0, 'angular_s': None, 'encoding': 'q16'},
    'balanced': {'doa_s': 1.0, 'angular_s': 4.0, 'encoding': 'q16'},
    'graph_u8': {'doa_s': 1.0, 'angular_s': 2.0, 'encoding': 'u8'},
}

def _merge(base, value, path=''):
    if not isinstance(value, dict):
        raise ValueError(f'{path or "config"}: OBJECT_REQUIRED')
    for key, v in value.items():
        if key not in base:
            raise ValueError(f'{path}{key}: UNKNOWN_CONFIG_KEY')
        if isinstance(base[key], dict):
            _merge(base[key], v, path+key+'.')
        else:
            old = base[key]
            if isinstance(old, bool) and type(v) is not bool:
                raise ValueError(f'{path}{key}: BOOLEAN_REQUIRED')
            if type(old) is int and (type(v) is not int):
                raise ValueError(f'{path}{key}: INTEGER_REQUIRED')
            if isinstance(old, str) and not isinstance(v, str):
                raise ValueError(f'{path}{key}: STRING_REQUIRED')
            if old is None and v is not None and not isinstance(v, str):
                raise ValueError(f'{path}{key}: STRING_OR_NULL_REQUIRED')
            base[key] = v

def load_config(path: str | Path | None = None) -> dict:
    base = yaml.safe_load((ROOT/'config/example.yaml').read_text())
    if path:
        text = Path(path).read_text()
        if len(text) > 65536:
            raise ValueError('CONFIG_TOO_LARGE')
        value = yaml.safe_load(text)
        telemetry = value.get('telemetry') if isinstance(value, dict) else None
        if isinstance(telemetry, dict) and 'require_ground_receipt_for_bulk' in telemetry:
            legacy_gate = telemetry.pop('require_ground_receipt_for_bulk')
            if type(legacy_gate) is not bool:
                raise ValueError('telemetry.require_ground_receipt_for_bulk: BOOLEAN_REQUIRED')
        _merge(base, value)
    validate_config(base)
    return base

def validate_config(c: dict) -> None:
    if c['schema_version'] != 2 or c['runtime_mode'] not in ('read_only','controlled'):
        raise ValueError('INVALID_MODE_OR_SCHEMA')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}', c['node_id']):
        raise ValueError('INVALID_NODE_ID')
    if c['telemetry']['profile'] not in PROFILES:
        raise ValueError('UNKNOWN_PROFILE')
    if c['source']['angle_mode'] not in ('theta_mirror', 'csv_native'):
        raise ValueError('UNKNOWN_ANGLE_CONVENTION')
    if not 0 <= c['source']['output_vfo'] <= 15:
        raise ValueError('INVALID_VFO')
    for obj, key, lo, hi in [(c['source'],'poll_ms',100,5000),(c['source'],'max_record_bytes',4096,1048576),
                             (c['api'],'port',1024,65535),(c['api'],'session_seconds',60,3600),
                             (c['api'],'max_clients',2,64),(c['mqtt'],'port',1,65535),
                             (c['mqtt'],'keepalive',5,120),(c['mqtt'],'reconnect_max',5,120),
                             (c['telemetry'],'control_budget_bytes_s',500,1250),
                             (c['telemetry'],'bulk_budget_bytes_s',200,500),
                             (c['telemetry'],'resume_stable_seconds',0,120),
                             (c['control'],'verify_seconds',1,60),(c['control'],'start_timeout_seconds',5,600)]:
        finite(obj[key],lo,hi)
    validate_mqtt_host(c['mqtt']['host'])
    if c['mqtt']['transport'] not in ('tcp','websocket'): raise ValueError('INVALID_MQTT_TRANSPORT')
    validate_websocket_path(c['mqtt']['websocket_path'])
    if c['telemetry']['control_budget_bytes_s']+c['telemetry']['bulk_budget_bytes_s']>1500:
        raise ValueError('AGGREGATE_BUDGET_TOO_HIGH')
    for k,v in c['freshness'].items():
        finite(v,500,120000)
    if c['freshness']['receipt_warn_ms'] >= c['freshness']['receipt_lost_ms']:
        raise ValueError('RECEIPT_THRESHOLDS')
    if c['display']['theme'] not in ('dark','light'):
        raise ValueError('INVALID_THEME')
    if c['display']['accent'] not in ('teal','blue','amber'):
        raise ValueError('INVALID_DISPLAY_ACCENT')
    if c['display']['font'] not in ('system','serif','mono'):
        raise ValueError('INVALID_DISPLAY_FONT')
    finite(c['display']['blank_after_seconds'],0,86400)
    for p in (c['state_dir'], c['source']['share_dir'], c['mqtt']['ca_file'], c['mqtt']['control_credentials_file'],
              c['mqtt']['bulk_credentials_file'], c['api']['admin_hash_file'], c['control']['helper_socket']):
        if p is not None and not Path(p).is_absolute():
            raise ValueError('ABSOLUTE_PATH_REQUIRED')
    unit = c['link']['engine_service']
    if unit and not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service', unit):
        raise ValueError('INVALID_SERVICE_NAME')
    for k in ('local_ip','peer_ip'):
        ipaddress.IPv4Address(c['link'][k])
    if not c['mqtt']['tls']:
        try:
            is_local = ipaddress.ip_address(c['mqtt']['host']).is_loopback
        except ValueError:
            is_local = c['mqtt']['host'] == 'localhost'
        if is_local and not c['mqtt']['allow_insecure_loopback']:
            raise ValueError('PLAINTEXT_ONLY_EXPLICIT_LOOPBACK_TEST')
    if c['mqtt']['enabled']:
        if not c['mqtt']['control_credentials_file'] or not c['mqtt']['bulk_credentials_file']:
            raise ValueError('MQTT_CREDENTIALS_REQUIRED')
    if c['runtime_mode'] == 'read_only' and any(c['control'][k] for k in ('config_patch_enabled','processing_enabled','restart_enabled','reboot_enabled','shutdown_enabled')):
        raise ValueError('MUTATION_REQUIRES_CONTROLLED_MODE')

def save_config(path: Path, cfg: dict) -> None:
    from .util import atomic_write
    validate_config(cfg)
    atomic_write(path, yaml.safe_dump(cfg, sort_keys=False).encode(),0o640)
