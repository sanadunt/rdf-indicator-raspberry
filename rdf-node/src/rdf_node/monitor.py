from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

def run(args,timeout=2):
    try:
        p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=timeout,check=False)
        return p.stdout[:65536].strip() if p.returncode==0 else None
    except (OSError,subprocess.TimeoutExpired): return None

def probe_peer(interface,target):
    if not interface: return 'NO_INTERFACE'
    try:
        p=subprocess.run(['ping','-n','-I',interface,'-c','1','-W','1',target],
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=2,check=False)
    except (OSError,subprocess.TimeoutExpired): return 'ERROR'
    if p.returncode==0: return 'REPLY'
    if p.returncode==1: return 'NO_REPLY'
    return 'ERROR'

class Monitor:
    def __init__(self,cfg):
        self.cfg=cfg; self.lock=threading.Lock(); self.stop_event=threading.Event(); self.thread=None
        self.data=dict(cpu_percent=None,memory_percent=None,temperature_c=None,disk_free_percent=None,
                       uptime_s=None,clock_state='UNTRUSTED',clock_trusted=False,usb='UNKNOWN',usb_count=None,
                       ppp='DOWN',interface=None,ppp_probe='UNKNOWN',tx_kbit_s=None,rx_kbit_s=None,
                       service_state='UNKNOWN',substate=None,generation=None,cgroup_empty=None,throttled=None,undervoltage=None)
        self.prev_cpu=None; self.prev_net=None
    def snapshot(self):
        with self.lock: return dict(self.data)
    def start(self):
        self.thread=threading.Thread(target=self._loop,daemon=True,name='host-monitor'); self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(5)
    def _loop(self):
        n=0
        while not self.stop_event.is_set():
            try: self.probe(n)
            except Exception: pass
            n+=1; self.stop_event.wait(1)
    def probe(self,n):
        d={}
        try:
            fields=list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
            total=sum(fields); idle=fields[3]+fields[4]
            if self.prev_cpu:
                dt=total-self.prev_cpu[0]
                d['cpu_percent']=round(100*(1-(idle-self.prev_cpu[1])/dt),1) if dt>0 else None
            self.prev_cpu=(total,idle)
            mem={line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()}
            d['memory_percent']=round(100*(1-mem.get('MemAvailable',mem.get('MemFree',0))/mem['MemTotal']),1)
            d['uptime_s']=int(float(Path('/proc/uptime').read_text().split()[0]))
        except (OSError,ValueError,KeyError): pass
        try:
            temp=Path('/sys/class/thermal/thermal_zone0/temp')
            if temp.exists(): d['temperature_c']=round(int(temp.read_text())/1000,1)
        except (OSError,ValueError): pass
        if n%30==0:
            try:
                disk=shutil.disk_usage(self.cfg['state_dir']); d['disk_free_percent']=round(100*disk.free/disk.total,1)
            except OSError: pass
            clock=run(['timedatectl','show','-p','NTPSynchronized','--value'])
            d['clock_trusted']=clock=='yes'; d['clock_state']='SYNCED' if clock=='yes' else 'UNTRUSTED'
        if n%2==0:
            unit=self.cfg['link']['engine_service']
            if unit:
                out=run(['systemctl','show',unit,'--no-pager','-p','ActiveState','-p','SubState',
                         '-p','ExecMainStartTimestampMonotonic','-p','ControlGroup'])
                if out:
                    vals=dict(x.split('=',1) for x in out.splitlines() if '=' in x)
                    d['service_state']=vals.get('ActiveState','UNKNOWN').upper(); d['substate']=vals.get('SubState')
                    d['generation']=vals.get('ExecMainStartTimestampMonotonic')
                    cg=vals.get('ControlGroup','')
                    d['cgroup_empty']=None
                    if not cg and d['service_state'] in ('INACTIVE','FAILED'):
                        d['cgroup_empty']=True
                    elif cg and cg.startswith('/') and '..' not in Path(cg).parts:
                        try:
                            base=Path('/sys/fs/cgroup')/cg.lstrip('/')
                            events=base/'cgroup.events'
                            if events.exists():
                                ev=dict(x.split() for x in events.read_text().splitlines())
                                d['cgroup_empty']=ev.get('populated')=='0'
                            elif not base.exists() and d['service_state'] in ('INACTIVE','FAILED'):
                                d['cgroup_empty']=True
                        except (OSError,ValueError): pass
            ip=run(['ip','-j','addr','show'])
            d.update(ppp='DOWN',interface=None)
            if ip:
                try:
                    for link in json.loads(ip):
                        for addr in link.get('addr_info',[]):
                            local=addr.get('local'); peer=addr.get('address',addr.get('peer','')).split('/')[0]
                            if local==self.cfg['link']['local_ip'] and peer==self.cfg['link']['peer_ip'] and 'UP' in link.get('flags',[]):
                                d['ppp']='UP'; d['interface']=link['ifname']
                except (ValueError,AttributeError): pass
            if d['ppp']!='UP': d['ppp_probe']='NO_INTERFACE'
        with self.lock:
            ppp=d.get('ppp',self.data['ppp'])
            iface=d.get('interface',self.data['interface'])
        if iface:
            try:
                root=Path('/sys/class/net')/iface/'statistics'
                tx=int((root/'tx_bytes').read_text()); rx=int((root/'rx_bytes').read_text()); mono=time.monotonic()
                if self.prev_net and self.prev_net[0]==iface:
                    dt=mono-self.prev_net[3]
                    d['tx_kbit_s']=round(max(0,tx-self.prev_net[1])*8/1000/dt,2)
                    d['rx_kbit_s']=round(max(0,rx-self.prev_net[2])*8/1000/dt,2)
                self.prev_net=(iface,tx,rx,mono)
            except (OSError,ValueError): pass
        else:
            self.prev_net=None; d['tx_kbit_s']=None; d['rx_kbit_s']=None
        if n%5==0:
            alias=self.cfg['link']['serial_alias']; d['usb']='MISSING'
            if Path(alias).exists():
                out=run(['udevadm','info','--query=property','--name='+alias])
                props=dict(x.split('=',1) for x in (out or '').splitlines() if '=' in x)
                match=props.get('ID_VENDOR_ID')==self.cfg['link']['expected_vid'] and props.get('ID_MODEL_ID')==self.cfg['link']['expected_pid']
                match=match and props.get('ID_PATH')==self.cfg['link']['expected_usb_path']
                d['usb']='PRESENT' if match else 'IDENTITY_MISMATCH'
            try:
                count=0
                for dev in Path('/sys/bus/usb/devices').iterdir():
                    try:
                        if (dev/'idVendor').read_text().strip()=='0bda' and (dev/'idProduct').read_text().strip()=='2838': count+=1
                    except OSError: pass
                d['usb_count']=count
            except OSError: pass
            out=run(['vcgencmd','get_throttled'])
            if out and '0x' in out:
                try:
                    flags=int(out.split('=')[-1],16)
                    d['undervoltage']=bool(flags&1); d['throttled']=bool(flags&4)
                except ValueError: pass
            d['ppp_probe']=probe_peer(iface if ppp=='UP' else None,self.cfg['link']['peer_ip'])
        with self.lock: self.data.update(d)
