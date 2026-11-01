"""GY-26 compass and NMEA GPS readers on USB-serial ports (standard library only).

Each sensor has its own thread and owns its port. A missing, silent or garbled sensor only changes its
own state; it never blocks the collector, the scheduler or the DoA gates. A reading older than
`stale_ms` is reported as STALE with no number, never as the last known value. The T900 link port
(link.serial_alias) is never opened here, whatever the nav config says.
"""
from __future__ import annotations
import math
import os
import re
import select
import stat
import threading
import time
try:
    import fcntl
    import termios
except ImportError:  # pragma: no cover - non-POSIX hosts
    fcntl = termios = None

GY26_READ_ANGLE=b'\x31'
GY26_FRAME_LEN=8
# Consecutive unanswered GY-26 requests before the port is reopened.
GY26_REOPEN_AFTER=10
REOPEN_S=2.0
NMEA_MAX_LINE=96

class SensorFault(Exception):
    """A port problem with a fixed, Ground-safe code (A-Z0-9_)."""

def usb_identity(rdev):
    """(idVendor, idProduct) of the USB device behind a character device, or None (no sysfs)."""
    try: node=os.path.realpath(f'/sys/dev/char/{os.major(rdev)}:{os.minor(rdev)}')
    except (OSError,ValueError): return None
    while node not in ('/','/sys'):
        try:
            with open(os.path.join(node,'idVendor')) as v, open(os.path.join(node,'idProduct')) as p:
                return v.read().strip().lower(),p.read().strip().lower()
        except OSError: node=os.path.dirname(node)
    return None

def is_link_port(fd,link):
    """True when the open `fd` is the T900 radio: the same device node as link.serial_alias, or a USB
    device with the T900's VID:PID (also when the /dev/t900 alias is missing after re-enumeration)."""
    if not link: return False
    rdev=os.fstat(fd).st_rdev
    try:
        if link.get('serial_alias') and os.stat(link['serial_alias']).st_rdev==rdev: return True
    except OSError: pass
    vid,pid=(str(link.get('expected_vid') or '').lower(),str(link.get('expected_pid') or '').lower())
    return bool(vid and pid) and usb_identity(rdev)==(vid,pid)

def open_serial(path,baud,link=None):
    """Open `path` raw 8N1 at `baud`, non-blocking and exclusive. Never the T900 port described by `link`
    (its termios and data stream belong to pppd): that is refused before any setting is touched."""
    if termios is None: raise SensorFault('TERMIOS_UNAVAILABLE')
    real=os.path.realpath(path)
    if link and link.get('serial_alias') and real==os.path.realpath(link['serial_alias']):
        raise SensorFault('DEVICE_IS_LINK_PORT')
    try: mode=os.stat(real).st_mode
    except FileNotFoundError: raise SensorFault('DEVICE_MISSING') from None
    if not stat.S_ISCHR(mode): raise SensorFault('NOT_A_TTY')
    fd=os.open(real,os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
    try:
        if is_link_port(fd,link): raise SensorFault('DEVICE_IS_LINK_PORT')
        speed=getattr(termios,f'B{baud}')
        a=termios.tcgetattr(fd)
        a[0]=0; a[1]=0; a[2]=termios.CS8|termios.CREAD|termios.CLOCAL; a[3]=0
        a[4]=a[5]=speed; a[6][termios.VMIN]=0; a[6][termios.VTIME]=0
        termios.tcsetattr(fd,termios.TCSANOW,a); termios.tcflush(fd,termios.TCIOFLUSH)
        # Keep a second opener (ModemManager probing, a stray terminal) off the sensor while we own it.
        try: fcntl.ioctl(fd,termios.TIOCEXCL)
        except OSError: pass
    except BaseException:
        os.close(fd); raise
    return fd

def parse_gy26(frame: bytes) -> int:
    """Heading in tenths of a degree (0..3599) from one GY-26 frame: CR LF d100 d10 d1 '.' d0.1 checksum."""
    if len(frame)!=GY26_FRAME_LEN or frame[:2]!=b'\r\n' or frame[5:6]!=b'.':
        raise ValueError('GY26_BAD_FRAME')
    if sum(frame[:7])&0xFF!=frame[7]: raise ValueError('GY26_BAD_CHECKSUM')
    digits=frame[2:5]+frame[6:7]
    if not all(0x30<=b<=0x39 for b in digits): raise ValueError('GY26_BAD_FRAME')
    tenths=int(digits.decode('ascii'))
    if tenths>3600: raise ValueError('GY26_OUT_OF_RANGE')
    return tenths%3600

def find_gy26_frame(buf: bytes):
    """(tenths, rest) for the first checksummed frame in `buf`, or (None, tail worth keeping)."""
    start=buf.find(b'\r\n')
    while start>=0:
        if len(buf)-start<GY26_FRAME_LEN: return None,buf[start:]
        try: return parse_gy26(buf[start:start+GY26_FRAME_LEN]),buf[start+GY26_FRAME_LEN:]
        except ValueError: start=buf.find(b'\r\n',start+1)
    return None,buf[-1:] if buf.endswith(b'\r') else b''

def parse_nmea(line: str):
    """(sentence type, fields, talker) for a checksummed NMEA 0183 sentence."""
    line=line.strip()
    if not line.startswith('$') or len(line)>NMEA_MAX_LINE or '*' not in line: raise ValueError('NMEA_BAD_SENTENCE')
    body,_,check=line[1:].rpartition('*')
    if len(check)!=2: raise ValueError('NMEA_BAD_CHECKSUM')
    try: expected=int(check,16)
    except ValueError: raise ValueError('NMEA_BAD_CHECKSUM') from None
    calc=0
    for ch in body: calc^=ord(ch)
    if calc!=expected: raise ValueError('NMEA_BAD_CHECKSUM')
    fields=body.split(',')
    if len(fields[0])!=5 or not fields[0].isalpha(): raise ValueError('NMEA_BAD_SENTENCE')
    return fields[0][2:],fields[1:],fields[0][:2]

UNSIGNED=re.compile(r'\d{1,9}(\.\d{1,9})?\Z')
SIGNED=re.compile(r'-?\d{1,9}(\.\d{1,9})?\Z')

def _coord(value,hemi,degree_digits):
    if not value or hemi not in ('N','S','E','W'): return None
    # ddmm.mmmm / dddmm.mmmm: exactly two minute digits before the point.
    if not UNSIGNED.match(value) or len(value.split('.')[0])!=degree_digits+2: raise ValueError('NMEA_BAD_COORDINATE')
    deg=int(value[:degree_digits]); minutes=float(value[degree_digits:])
    if not 0<=minutes<60: raise ValueError('NMEA_BAD_COORDINATE')
    out=deg+minutes/60
    if out>(90 if degree_digits==2 else 180): raise ValueError('NMEA_BAD_COORDINATE')
    return -out if hemi in ('S','W') else out

def _num(value,lo,hi,signed=False):
    """A plain decimal NMEA number in [lo, hi], None when empty. 'nan', 'inf', '1e999' and the like
    are rejected, so they never reach JSON."""
    if not value: return None
    if not (SIGNED if signed else UNSIGNED).match(value): raise ValueError('NMEA_BAD_NUMBER')
    n=float(value)
    if not math.isfinite(n) or not lo<=n<=hi: raise ValueError('NMEA_BAD_NUMBER')
    return n

class NmeaState:
    """Latest GPS fix. GGA is the authority for the fix (quality, position, satellites, HDOP, altitude);
    RMC adds speed and course when the receiver sends them."""
    def __init__(self):
        self.valid=False; self.mode=None; self.quality=None; self.lat=None; self.lon=None; self.alt_m=None
        self.sats_used=None; self.hdop=None; self.speed_mps=None; self.course_deg=None
        self.fix_mono=None; self.rmc_mono=None; self.in_view={}
    def feed(self,kind,f,talker='GP'):
        if kind=='GGA' and len(f)>=9:
            # Every field is checked before any is stored: a bad sentence changes nothing.
            quality=int(f[5]) if f[5].isdigit() else 0
            lat=_coord(f[1],f[2],2); lon=_coord(f[3],f[4],3)
            sats=int(f[6]) if f[6].isdigit() and int(f[6])<=255 else None
            hdop=_num(f[7],0,99.99); alt=_num(f[8],-1000,100000,signed=True)
            self.quality=quality; self.sats_used=sats
            # Quality 6 is dead reckoning: no satellite fix behind the position.
            self.valid=quality in (1,2,3,4,5,8) and lat is not None and lon is not None
            if self.valid:
                self.lat=lat; self.lon=lon; self.hdop=hdop; self.alt_m=alt; self.fix_mono=time.monotonic()
            else:
                self.hdop=None; self.alt_m=None
        elif kind=='RMC' and len(f)>=8:
            if f[1]=='A':
                knots=_num(f[6],0,1000); course=_num(f[7],0,360)
                self.speed_mps=round(knots*0.514444,2) if knots is not None else None
                self.course_deg=course%360 if course is not None else None
                self.rmc_mono=time.monotonic()
            else:
                self.speed_mps=self.course_deg=None; self.rmc_mono=None
        elif kind=='GSA' and len(f)>=2:
            self.mode=int(f[1]) if f[1] in ('1','2','3') else None
        elif kind=='GSV' and len(f)>=3:
            # One GSV group per constellation (GP, GL, GA, GB...): satellites in view is their sum.
            if f[2].isdigit() and int(f[2])<=255: self.in_view[talker]=(int(f[2]),time.monotonic())
    def sats_view(self,now,stale):
        seen=[count for count,mono in self.in_view.values() if now-mono<=stale]
        return sum(seen) if seen else None

class _Sensor:
    name='sensor'
    def __init__(self,cfg,link=None):
        self.cfg=cfg; self.link=link; self.lock=threading.Lock(); self.stop_event=threading.Event()
        self.thread=None; self.fd=None
        self.fault='DISABLED' if not cfg['enabled'] else 'STARTING'; self.error=None; self.errors=0
    def start(self):
        if not self.cfg['enabled']: return
        self.thread=threading.Thread(target=self._loop,daemon=True,name='rdf-'+self.name); self.thread.start()
    def stop(self):
        # Only the sensor thread closes its fd (no close racing a read); it does so on the way out.
        self.stop_event.set()
        if self.thread: self.thread.join(3)
    def _close(self):
        if self.fd is not None:
            try: os.close(self.fd)
            except OSError: pass
            self.fd=None
    def _fault(self,state,error):
        with self.lock: self.fault=state; self.error=error; self.errors+=1
    def _loop(self):
        try:
            while not self.stop_event.is_set():
                try:
                    if self.fd is None: self.fd=open_serial(self.cfg['device'],self.cfg['baud'],self.link)
                    self._run()
                except SensorFault as e:
                    self._close(); self._fault('NO_DEVICE',str(e)); self.stop_event.wait(REOPEN_S)
                except (OSError,termios.error) as e:
                    # termios.error (unplugged during tcflush/tcgetattr) carries the errno in args[0].
                    code=e.errno if isinstance(e,OSError) else (e.args[0] if e.args and isinstance(e.args[0],int) else 0)
                    self._close(); self._fault('NO_DEVICE',f'ERRNO_{code}' if code else 'SERIAL_EOF')
                    self.stop_event.wait(REOPEN_S)
                except Exception as e:
                    self._close(); self._fault('ERROR',type(e).__name__.upper()[:64]); self.stop_event.wait(REOPEN_S)
        finally:
            self._close()

class Compass(_Sensor):
    """Polls a GY-26 with the read-angle command and keeps the latest checksummed heading."""
    name='compass'
    def __init__(self,cfg,link=None):
        super().__init__(cfg,link); self.tenths=None; self.mono=None; self.misses=0
        self.bad_frames=0; self.no_reply=0
        self.offset_tenths=round(cfg['offset_deg']*10)
    def _run(self):
        period=self.cfg['poll_ms']/1000; wait=min(.5,period)
        while not self.stop_event.is_set():
            began=time.monotonic(); buf=b''; tenths=None; received=False
            termios.tcflush(self.fd,termios.TCIFLUSH)
            try: os.write(self.fd,GY26_READ_ANGLE)
            except BlockingIOError: pass
            deadline=began+wait
            while tenths is None and not self.stop_event.is_set():
                left=deadline-time.monotonic()
                if left<=0: break
                r,_,_=select.select([self.fd],[],[],left)
                if r:
                    chunk=os.read(self.fd,64)
                    if not chunk: raise OSError(0,'SERIAL_EOF')
                    received=True; tenths,buf=find_gy26_frame(buf+chunk)
            if tenths is not None:
                self.misses=0
                with self.lock: self.tenths=tenths; self.mono=time.monotonic(); self.fault=None
            elif not self.stop_event.is_set():
                self.misses+=1
                with self.lock:
                    if received: self.bad_frames+=1
                    else: self.no_reply+=1
                    self.fault='BAD_FRAME' if received else 'NO_REPLY'; self.error='GY26_BAD_FRAME' if received else 'GY26_NO_REPLY'
                if self.misses>=GY26_REOPEN_AFTER:
                    self.misses=0; self._close(); return
            self.stop_event.wait(max(0,period-(time.monotonic()-began)))
    def snapshot(self):
        with self.lock:
            age=round((time.monotonic()-self.mono)*1000) if self.mono is not None else None
            fresh=age is not None and age<=self.cfg['stale_ms']
            if self.fault: state=self.fault
            elif fresh: state='OK'
            else: state='STALE' if self.mono is not None else 'STARTING'
            ok=state=='OK'
            heading=((self.tenths+self.offset_tenths)%3600)/10 if ok else None
            return {'state':state,'heading_deg':heading,'raw_deg':self.tenths/10 if ok else None,
                    'offset_deg':self.cfg['offset_deg'],'age_ms':age,'error':self.error,'errors':self.errors,
                    'bad_frames':self.bad_frames,'no_reply':self.no_reply,
                    'device':self.cfg['device'] if self.cfg['enabled'] else None}

class Gps(_Sensor):
    """Reads NMEA sentences from a GNSS receiver (u-blox 7 on USB CDC-ACM) and keeps the latest fix."""
    name='gps'
    def __init__(self,cfg,link=None):
        super().__init__(cfg,link); self.nmea=NmeaState(); self.data_mono=None; self.bytes_mono=None
        self.open_mono=None; self.bad=0
    def _run(self):
        buf=b''
        with self.lock: self.open_mono=time.monotonic(); self.fault=None
        while not self.stop_event.is_set():
            r,_,_=select.select([self.fd],[],[],.5)
            if not r: continue
            chunk=os.read(self.fd,512)
            if not chunk: raise OSError(0,'SERIAL_EOF')
            with self.lock: self.bytes_mono=time.monotonic()
            buf+=chunk
            *lines,buf=buf.split(b'\n')
            if len(buf)>NMEA_MAX_LINE: buf=b''
            for raw in lines:
                with self.lock:
                    try:
                        kind,fields,talker=parse_nmea(raw.decode('ascii'))
                        self.nmea.feed(kind,fields,talker)
                    except (ValueError,IndexError,UnicodeDecodeError):
                        self.bad+=1; continue
                    self.data_mono=time.monotonic(); self.fault=None
    def snapshot(self):
        with self.lock:
            n=self.nmea; now=time.monotonic(); stale=self.cfg['stale_ms']/1000
            data_age=round((now-self.data_mono)*1000) if self.data_mono is not None else None
            fix=(n.valid and n.fix_mono is not None and now-n.fix_mono<=stale and
                 data_age is not None and data_age<=self.cfg['stale_ms'])
            bytes_fresh=self.bytes_mono is not None and now-self.bytes_mono<=stale
            starting=data_age is None and (self.open_mono is None or now-self.open_mono<=stale)
            if self.fault: state=self.fault
            elif data_age is not None and data_age<=self.cfg['stale_ms']: state='FIX' if fix else 'NO_FIX'
            elif starting: state='STARTING'
            # Bytes but no valid sentence: wrong baud or not a GPS. Nothing at all: NO_DATA.
            elif bytes_fresh: state='BAD_DATA'
            else: state='NO_DATA'
            fix=state=='FIX'
            mode=(n.mode if n.mode in (2,3) else 2) if fix else 0
            current=state in ('FIX','NO_FIX')
            motion=fix and n.rmc_mono is not None and now-n.rmc_mono<=stale
            return {'state':state,'fix':mode,
                    'lat':round(n.lat,6) if fix else None,'lon':round(n.lon,6) if fix else None,
                    'alt_m':round(n.alt_m,1) if fix and mode==3 and n.alt_m is not None else None,
                    'speed_mps':n.speed_mps if motion else None,
                    'course_deg':round(n.course_deg,1) if motion and n.course_deg is not None else None,
                    'sats_used':n.sats_used if current else None,'sats_view':n.sats_view(now,stale) if current else None,
                    'hdop':n.hdop if fix else None,'age_ms':round((now-n.fix_mono)*1000) if fix else None,
                    'data_age_ms':data_age,'error':self.error,'errors':self.errors,'bad_sentences':self.bad,
                    'device':self.cfg['device'] if self.cfg['enabled'] else None}

class Nav:
    def __init__(self,cfg,link=None):
        self.compass=Compass(cfg['compass'],link); self.gps=Gps(cfg['gps'],link)
    def start(self): self.compass.start(); self.gps.start()
    def stop(self): self.compass.stop(); self.gps.stop()
    def snapshot(self): return {'compass':self.compass.snapshot(),'gps':self.gps.snapshot()}
