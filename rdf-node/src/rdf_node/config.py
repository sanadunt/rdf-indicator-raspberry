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
    'control': {'doa_s': 1.0, 'angular_s': None},
    'balanced': {'doa_s': 1.0, 'angular_s': 4.0},
    'graph_u8': {'doa_s': 1.0, 'angular_s': 2.0},
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
    validate_backup_config(c['mqtt']['backup'], c['mqtt']['allow_insecure_loopback'])
    for k,v in c['freshness'].items():
        finite(v,500,120000)
    if c['testing']['clock'] not in (None,'system'):
        raise ValueError('INVALID_TEST_CLOCK')
    if c['testing']['clock'] and c['runtime_mode']!='read_only':
        raise ValueError('TEST_CLOCK_REQUIRES_READ_ONLY')
    if c['display']['theme'] not in ('dark','light','night'):
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
    if not c['mqtt']['tls'] and _is_loopback(c['mqtt']['host']) and not c['mqtt']['allow_insecure_loopback']:
        raise ValueError('PLAINTEXT_ONLY_EXPLICIT_LOOPBACK_TEST')
    if c['mqtt']['enabled']:
        if not c['mqtt']['control_credentials_file'] or not c['mqtt']['bulk_credentials_file']:
            raise ValueError('MQTT_CREDENTIALS_REQUIRED')
    validate_nav_config(c['nav'], c['link']['serial_alias'])
    if c['runtime_mode'] == 'read_only' and any(c['control'][k] for k in ('config_patch_enabled','processing_enabled','restart_enabled','reboot_enabled','shutdown_enabled','ppp_restart_enabled')):
        raise ValueError('MUTATION_REQUIRES_CONTROLLED_MODE')

def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == 'localhost'

def validate_backup_config(b: dict, allow_insecure_loopback: bool) -> None:
    # The backup broker is a LAN or online mirror; its budget is not part of the T900 aggregate.
    for key in ('enabled', 'tls', 'commands', 'settings_report_plaintext'):
        if type(b[key]) is not bool:
            raise ValueError('BOOLEAN_REQUIRED')
    for key, lo, hi in (('port', 1, 65535), ('budget_bytes_s', 1000, 200000)):
        if type(b[key]) is not int or not lo <= b[key] <= hi:
            raise ValueError('INTEGER_OUT_OF_RANGE')
    if b['transport'] not in ('tcp', 'websocket'):
        raise ValueError('INVALID_MQTT_TRANSPORT')
    validate_websocket_path(b['websocket_path'])
    if b['host'] is not None:
        validate_mqtt_host(b['host'])
    elif b['enabled']:
        raise ValueError('MQTT_BACKUP_HOST_REQUIRED')
    for p in (b['ca_file'], b['credentials_file']):
        if p is not None and (not isinstance(p, str) or not Path(p).is_absolute()):
            raise ValueError('ABSOLUTE_PATH_REQUIRED')
    if b['host'] is not None and not b['tls'] and _is_loopback(b['host']) and not allow_insecure_loopback:
        raise ValueError('PLAINTEXT_ONLY_EXPLICIT_LOOPBACK_TEST')

NAV_BAUDS = (4800, 9600, 19200, 38400, 57600, 115200)

def validate_nav_config(n: dict, link_port: str | None = None) -> None:
    for name in ('compass', 'gps'):
        s = n[name]
        if type(s['enabled']) is not bool:
            raise ValueError('BOOLEAN_REQUIRED')
        if s['device'] is not None and (not isinstance(s['device'], str) or not Path(s['device']).is_absolute()):
            raise ValueError('ABSOLUTE_PATH_REQUIRED')
        if s['enabled'] and s['device'] is None:
            raise ValueError(f'NAV_{name.upper()}_DEVICE_REQUIRED')
        if type(s['baud']) is not int or s['baud'] not in NAV_BAUDS:
            raise ValueError('INVALID_NAV_BAUD')
        finite(s['stale_ms'], 500, 60000)
    finite(n['compass']['poll_ms'], 100, 5000)
    # A reading must survive at least one missed poll before it reads as STALE.
    if n['compass']['stale_ms'] < 2 * n['compass']['poll_ms']:
        raise ValueError('NAV_STALE_SHORTER_THAN_TWO_POLLS')
    offset = n['compass']['offset_deg']
    if isinstance(offset, bool) or type(offset) not in (int, float):
        raise ValueError('NUMBER_REQUIRED')
    finite(offset, -360, 360)
    if type(n['gps']['publish_position']) is not bool:
        raise ValueError('BOOLEAN_REQUIRED')
    # The T900 link port belongs to pppd; two sensors cannot share one port.
    devices = [n[name]['device'] for name in ('compass', 'gps') if n[name]['enabled']]
    if len(set(devices)) != len(devices) or (link_port and link_port in devices):
        raise ValueError('NAV_DEVICE_CONFLICT')

def save_config(path: Path, cfg: dict) -> None:
    from .util import atomic_write
    validate_config(cfg)
    atomic_write(path, yaml.safe_dump(cfg, sort_keys=False).encode(),0o640)
