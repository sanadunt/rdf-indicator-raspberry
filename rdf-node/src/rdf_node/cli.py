from __future__ import annotations
import argparse
import getpass
import json
import math
import os
from pathlib import Path
import pwd
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import yaml
from .config import load_config, save_config, ROOT
from .util import atomic_write, compact, now_ms, stable_read

def chown_config(path):
    try:
        import grp
        os.chown(path,0,grp.getgrnam('rdf-edge').gr_gid)
    except (KeyError,PermissionError): pass

def require_root():
    if os.geteuid()!=0: raise SystemExit('Jalankan dengan sudo untuk konfigurasi deployment.')

def discover_sources():
    found=[]
    # Local read-only discovery, not an assumed user/path.
    for base in (Path('/home'),Path('/opt')):
        if not base.exists(): continue
        for root,dirs,files in os.walk(base):
            dirs[:]=[d for d in dirs if not d.startswith('.') and d not in ('node_modules','miniforge3','miniconda3','anaconda3','venv','site-packages')]
            if len(Path(root).relative_to(base).parts)>6: dirs[:]=[]; continue
            if Path(root).name=='_share' and 'settings.json' in files and ('status.json' in files or 'DOA_value.html' in files):
                found.append(Path(root)); dirs[:]=[]
            if len(found)>8: return found
    return found

def grant_read(path:Path):
    import shutil
    if not shutil.which('setfacl'): raise SystemExit('Perlu paket acl: sudo apt install acl, lalu ulangi setup.')
    path=path.resolve()
    # Traversal, not recursive access to the entire HOME.
    for parent in list(reversed(path.parents)):
        if parent==Path('/'): continue
        subprocess.run(['setfacl','-m','u:rdf-edge:--x',str(parent)],check=True)
    subprocess.run(['setfacl','-m','u:rdf-edge:r-x,d:u:rdf-edge:r-x',str(path)],check=True)
    for name in ('settings.json','status.json','DOA_value.html','doa.xml'):
        p=path/name
        if p.is_file(): subprocess.run(['setfacl','-m','u:rdf-edge:r--',str(p)],check=True)

def setup(args):
    require_root(); cfg=load_config(args.config)
    src=args.share_dir
    if not src:
        candidates=discover_sources()
        print('Folder output yang ditemukan (tidak mengubah engine):')
        for i,p in enumerate(candidates,1): print(f'  {i}. {p}')
        answer=input('Pilih nomor atau masukkan path absolut _share (kosong = local setup mode): ').strip()
        if answer.isdigit() and 1<=int(answer)<=len(candidates): src=str(candidates[int(answer)-1])
        elif answer: src=answer
    if src:
        p=Path(src).expanduser().resolve()
        if not p.is_dir() or not (p/'settings.json').is_file(): raise SystemExit('Folder harus berisi settings.json native yang sudah ada.')
        cfg['source']['share_dir']=str(p)
        if args.grant_read or input('Beri user rdf-edge akses baca _share dan traversal parent? [y/N]: ').lower()=='y': grant_read(p)
    if args.engine_unit:
        cfg['link']['engine_service']=args.engine_unit
    elif not cfg['link']['engine_service']:
        p=subprocess.run(['systemctl','show','sdr-doa.service','-p','LoadState','--value'],capture_output=True,text=True)
        if p.stdout.strip()=='loaded': cfg['link']['engine_service']='sdr-doa.service'
    if args.verify_source:
        print('Approval ini harus berdasarkan data fresh, healthy DAQ dan uji orientasi yang sudah Anda lakukan.')
        if args.yes or input('Ketik VERIFIED untuk mengizinkan source LIVE: ').strip()=='VERIFIED':
            cfg['source']['authority_verified']=True; cfg['source']['angle_verified']=True
    save_config(Path(args.config),cfg); chown_config(args.config)
    print('Config tersimpan. Restart bridge saja: sudo systemctl restart rdf-edge.service')
    print('Kontrol engine tetap mengikuti policy, tidak otomatis diaktifkan.')

def import_bundle(args):
    require_root(); cfg=load_config(args.config); bundle=Path(args.directory)
    data=json.loads(stable_read(bundle/'bundle.json',4096,nofollow=True))
    if data.get('node_id')!=cfg['node_id']: raise SystemExit('Node ID bundle tidak cocok.')
    dest=Path('/etc/rdf-node/credentials'); dest.mkdir(mode=0o750,parents=True,exist_ok=True)
    import grp
    gid=grp.getgrnam('rdf-edge').gr_gid; os.chown(dest,0,gid)
    for source,target in [('ca.crt','ca.crt'),('uav-control.json','control.json'),('uav-bulk.json','bulk.json')]:
        content=stable_read(bundle/source,16384,nofollow=True)
        atomic_write(dest/target,content,0o640); os.chown(dest/target,0,gid)
    import ssl
    ssl.create_default_context(cafile=str(dest/'ca.crt'))
    cfg['mqtt'].update(enabled=True,host=data['host'],port=data['port'],tls=True,
                       ca_file=str(dest/'ca.crt'),control_credentials_file=str(dest/'control.json'),
                       bulk_credentials_file=str(dest/'bulk.json'))
    save_config(Path(args.config),cfg); chown_config(args.config)
    print('TLS/credential terpasang. Bundle berisi secret; simpan offline aman atau hapus setelah provisioning.')
    print('sudo systemctl restart rdf-edge.service')

def doctor(args):
    cfg=load_config(args.config)
    from .source import parse_csv,parse_status,safe_settings
    from .monitor import run
    print('RDF Node preflight (read-only; tidak membuka SDR/serial)')
    print('Python:',sys.version.split()[0]); print('MQTT:', 'configured' if cfg['mqtt']['enabled'] else 'off')
    print('Profile:',cfg['telemetry']['profile']); print('Runtime mode:',cfg['runtime_mode'])
    path=cfg['source']['share_dir']
    if path:
        p=Path(path)
        try:
            settings=json.loads(stable_read(p/'settings.json',65536)); safe_settings(settings)
            status=parse_status(stable_read(p/'status.json',16384))
            rec=parse_csv(stable_read(p/'DOA_value.html',cfg['source']['max_record_bytes']),settings,cfg['source']['output_vfo'])
            print('Source: READABLE; CSV fields:377; angular samples:360')
            print('DAQ flag:',status['daq_ok'],'frame:',status['frame_index'])
            print('Source age (ms):',now_ms()-rec['timestamp_ms'])
            print('Authority:',cfg['source']['authority_verified'],'angle:',cfg['source']['angle_verified'])
        except Exception as e: print('Source error:',type(e).__name__,str(e)[:80])
    else: print('Source: SETUP REQUIRED')
    print('PPP route:',run(['ip','route','get',cfg['link']['peer_ip']]) or 'unavailable')
    print('Clock synchronized:',run(['timedatectl','show','-p','NTPSynchronized','--value']) or 'unknown')
    for name in ('ca_file','control_credentials_file','bulk_credentials_file'):
        p=cfg['mqtt'].get(name); print(name,':','readable' if p and os.access(p,os.R_OK) else 'missing/not readable')
    print('TLS identity tidak di-bypass. Jangan tempel credential/settings mentah ke chat.')

def controls(args):
    require_root()
    from .helper import DEFAULT_POLICY
    path=Path('/etc/rdf-node/helper.yaml'); policy=dict(DEFAULT_POLICY)
    if path.exists(): policy.update(yaml.safe_load(path.read_text()) or {})
    cfg=load_config(args.config)
    if args.action=='approve':
        print('Approval memberi akses write ke engine. Dibutuhkan single-writer settings dan audit unit/watchdog.')
        if input('Ketik APPROVE untuk lanjut: ').strip()!='APPROVE': raise SystemExit('Tidak diubah.')
        if args.settings:
            if not cfg['source']['share_dir']: raise SystemExit('Setup source lebih dahulu.')
            if input('Semua writer config lain sudah dikoordinasikan/dinonaktifkan? ketik SINGLE: ').strip()!='SINGLE': raise SystemExit('Belum approved.')
            policy.update(allow_config=True,single_writer_confirmed=True,settings_path=str(Path(cfg['source']['share_dir'])/'settings.json'))
            cfg['control']['config_patch_enabled']=True
        if args.lifecycle:
            unit=cfg['link']['engine_service']
            if not unit: raise SystemExit('Setup engine unit yang benar lebih dahulu.')
            print('Unit terpilih:',unit)
            if input('Unit stop hanya mencakup SDR dan watchdog telah diaudit? ketik AUDITED: ').strip()!='AUDITED': raise SystemExit('Belum approved.')
            # Explicit takeover: prevent old UI-root watchdog from undoing STOP.
            for u in ('sdr-watchdog.timer',):
                p=subprocess.run(['systemctl','is-enabled',u],capture_output=True,text=True)
                if p.stdout.strip()=='enabled':
                    if input(f'Nonaktifkan {u} yang dapat membatalkan Stop? [y/N]: ').lower()!='y': raise SystemExit('Lifecycle dibatalkan; watchdog konflik.')
                    subprocess.run(['systemctl','disable','--now',u],check=True)
            directory=Path('/etc/systemd/system')/(unit+'.d'); directory.mkdir(parents=True,exist_ok=True)
            atomic_write(directory/'50-rdf-node-intent.conf',b'[Unit]\nConditionPathExists=!/var/lib/rdf-node-control/engine.stopped\n',0o644)
            policy.update(allow_lifecycle=True,lifecycle_audited=True,engine_service=unit)
            cfg['control'].update(processing_enabled=True,restart_enabled=True)
        if args.reboot:
            if input('Izinkan fitur reboot terproteksi (tetap memerlukan lease sudo)? ketik REBOOT: ').strip()!='REBOOT': raise SystemExit('Tidak diubah.')
            policy['allow_reboot']=True; cfg['control']['reboot_enabled']=True
        cfg['runtime_mode']='controlled'
        cfg['control']['remote_commands_enabled']=args.remote
        atomic_write(path,yaml.safe_dump(policy,sort_keys=False).encode(),0o600)
        save_config(Path(args.config),cfg); chown_config(args.config)
        subprocess.run(['systemctl','daemon-reload'],check=True)
        subprocess.run(['systemctl','restart','rdf-control-helper.service','rdf-edge.service'],check=True)
        print('Approval tersimpan. Buka lease maintenance terpisah untuk lifecycle/reboot.')
    elif args.action in ('maintenance-open','maintenance-close'):
        from .helper import call_helper
        result=call_helper(cfg['control']['helper_socket'],{'op':'maintenance.open','seconds':args.seconds} if args.action.endswith('open') else {'op':'maintenance.close'})
        print(json.dumps(result))

def generate_demo(directory:Path,stop):
    directory.mkdir(parents=True,exist_ok=True)
    settings={'center_freq':433.92,'uniform_gain':15.7,'vfo_freq_0':433920000,'vfo_bw_0':12500,'vfo_squelch_0':-90,
              'active_vfos':1,'output_vfo':0,'en_doa':True,'ant_arrangement':'UCA','doa_method':'MUSIC'}
    atomic_write(directory/'settings.json',compact(settings),0o644)
    count=0
    while not stop.is_set():
        count+=1; ts=now_ms(); theta=(130+18*math.sin(count/18))%360
        vals=[]
        for x in range(360):
            delta=((x-theta+180)%360)-180
            vals.append(round(-28+28*math.exp(-(delta/22)**2)+3*math.cos(x/30),3))
        row=[ts,(360-theta)%360,8.27,-54.2,433920000,'UCA',436,'DEMO',0,0,0,0,'None','R','R','R','R']+vals
        atomic_write(directory/'DOA_value.html',(','.join(map(str,row))+'\n').encode(),0o644)
        atomic_write(directory/'status.json',compact({'timestamp_ms':ts,'daq_ok':True,'daq_num_dropped_frames':0,'gps_status':'Disabled',
            'daq_status':{'data_frame_index':count,'frame_sync':True,'sample_delay_sync':True,'iq_sync':True,'adc_overdrive':0}}),0o644)
        stop.wait(.5)

def serve(args,ground=False,demo=False):
    from .api import Server
    cfg=load_config(args.config if not demo else None)
    stop_demo=threading.Event(); generator=None
    if demo:
        state=Path(args.state_dir or tempfile.mkdtemp(prefix='rdf-demo-')).resolve()
        state.mkdir(parents=True,exist_ok=True)
        cfg['state_dir']=str(state); cfg['api']['port']=args.port
        cfg['api']['admin_hash_file']=str(state/'admin-hash.json')
        cfg['source'].update(share_dir=str(state/'source'),authority_verified=True,angle_verified=True)
        from .api import set_password
        if not Path(cfg['api']['admin_hash_file']).exists():
            password=secrets.token_urlsafe(18); set_password(Path(cfg['api']['admin_hash_file']),password)
            atomic_write(state/'demo-password.txt',password.encode()+b'\n')
        generator=threading.Thread(target=generate_demo,args=(state/'source',stop_demo),daemon=True); generator.start(); time.sleep(.1)
        print('DEMO - data sintetis, MQTT OFF, tidak mengubah perangkat.')
        print('Password demo tersimpan lokal di:',state/'demo-password.txt')
    if ground:
        from .ground import Ground
        app=Ground(cfg,demo=args.demo_namespace)
    else:
        from .agent import Agent
        app=Agent(cfg,demo=demo)
    server=Server(app,cfg,ground=ground); app.start()
    def end(sig,frame):
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,end); signal.signal(signal.SIGINT,end)
    print(f'RDF Node {"Ground" if ground else "Edge"}: http://127.0.0.1:{cfg["api"]["port"]}')
    try: server.serve_forever(poll_interval=.2)
    finally:
        stop_demo.set(); app.stop(); server.server_close()

def main():
    p=argparse.ArgumentParser(description='RDF Node service and deployment CLI')
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('edge','ground','doctor','setup','set-password','import-bundle','controls'):
        s=sub.add_parser(name)
        s.add_argument('--config',default='/etc/rdf-ground/config.yaml' if name=='ground' else '/etc/rdf-node/config.yaml')
        if name=='ground': s.add_argument('--demo-namespace',action='store_true')
        if name=='setup':
            s.add_argument('--share-dir');s.add_argument('--engine-unit');s.add_argument('--grant-read',action='store_true')
            s.add_argument('--verify-source',action='store_true');s.add_argument('--yes',action='store_true')
        if name=='set-password': s.add_argument('--password-file')
        if name=='import-bundle': s.add_argument('directory')
        if name=='controls':
            s.add_argument('action',choices=['approve','maintenance-open','maintenance-close']);s.add_argument('--settings',action='store_true')
            s.add_argument('--lifecycle',action='store_true');s.add_argument('--reboot',action='store_true');s.add_argument('--remote',action='store_true')
            s.add_argument('--seconds',type=int,default=300)
    s=sub.add_parser('demo');s.add_argument('--port',type=int,default=8790);s.add_argument('--state-dir')
    s=sub.add_parser('helper');s.add_argument('--policy',default='/etc/rdf-node/helper.yaml')
    sub.add_parser('selftest')
    a=p.parse_args()
    if a.command in ('edge','ground','demo'): serve(a,ground=a.command=='ground',demo=a.command=='demo')
    elif a.command=='helper':
        from .helper import serve as helper_serve
        helper_serve(a.policy)
    elif a.command=='doctor': doctor(a)
    elif a.command=='setup': setup(a)
    elif a.command=='import-bundle': import_bundle(a)
    elif a.command=='controls': controls(a)
    elif a.command=='set-password':
        require_root(); cfg=load_config(a.config)
        password=Path(a.password_file).read_text().strip() if a.password_file else getpass.getpass('Password admin baru (>=12): ')
        from .api import set_password
        set_password(Path(cfg['api']['admin_hash_file']),password)
        target=Path(cfg['api']['admin_hash_file'])
        try:
            import grp
            os.chown(target,0,grp.getgrnam('rdf-ground' if str(target).startswith('/etc/rdf-ground') else 'rdf-edge').gr_gid)
        except KeyError: pass
        print('Password diperbarui; login berikutnya memakai hash baru.')
    elif a.command=='selftest':
        import unittest
        sys.path.insert(0,str(ROOT/'tests'))
        suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
