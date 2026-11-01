import json,os,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest import mock
from rdf_node import nav
from rdf_node.nav import parse_gy26,find_gy26_frame,parse_nmea,NmeaState,Compass,Gps,open_serial,SensorFault,is_link_port
from rdf_node.config import load_config,validate_config
from rdf_node.ground import Ground
from rdf_node.util import compact,now_ms
from broker_fixture import wait

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'contract'))
import validate as contract

# Captured from the GY-26 and the u-blox 7 on the Raspberry (2026-10-31), plus a textbook fix.
GY26_201_1=bytes.fromhex('0d0a3230312e3109')
NO_FIX=['$GPRMC,101236.00,V,,,,,,,311026,,,N*7D','$GPGGA,101235.00,,,,,0,00,99.99,,,,,,*62',
        '$GPGSA,A,1,,,,,,,,,,,,,99.99,99.99,99.99*30']

def gy26(tenths):
    body=b'\r\n'+f'{tenths//10:03d}.{tenths%10}'.encode()
    return body+bytes([sum(body)&0xFF])

def nmea(body):
    check=0
    for ch in body: check^=ord(ch)
    return f'${body}*{check:02X}'

def fix_lines(lat='0612.3456',ns='S',lon='10648.6000',ew='E',knots='5.0',course='90.0'):
    return [nmea(f'GPRMC,101300.00,A,{lat},{ns},{lon},{ew},{knots},{course},311026,,,A'),
            nmea(f'GPGGA,101300.00,{lat},{ns},{lon},{ew},1,07,1.2,15.5,M,10.0,M,,'),
            nmea('GPGSA,A,3,01,02,03,04,05,06,07,,,,,,2.0,1.2,1.6'),nmea('GPGSV,2,1,09,01,40,083,40,,,,,,,,,,,,')]

# Same shape as a real u-blox 7 DGPS fix (2026-10-31: RMC course empty, GGA quality 2, trailing station
# id), with the coordinates moved away from the site.
DGPS_FIX=[nmea('GPRMC,102846.00,A,0612.34567,S,10648.60000,E,0.282,,311026,,,D'),
          nmea('GPVTG,,T,,M,0.282,N,0.522,K,D'),
          nmea('GPGGA,102846.00,0612.34567,S,10648.60000,E,2,05,1.68,715.9,M,2.2,M,,0000'),
          nmea('GPGSA,A,3,03,04,09,01,02,,,,,,,,4.17,1.68,3.82'),
          nmea('GPGSV,3,1,09,01,67,097,40,02,50,050,38,03,29,164,34,04,53,197,35'),
          nmea('GPGLL,0612.34567,S,10648.60000,E,102846.00,A,D')]

def nav_cfg(**over):
    cfg={'compass':{'enabled':False,'device':None,'baud':9600,'poll_ms':100,'stale_ms':1000,'offset_deg':0.0},
         'gps':{'enabled':False,'device':None,'baud':9600,'stale_ms':1000,'publish_position':False}}
    for name,values in over.items(): cfg[name].update(values)
    return cfg

class ParserTests(unittest.TestCase):
    def test_gy26_frame_from_hardware(self):
        self.assertEqual(parse_gy26(GY26_201_1),2011)
    def test_gy26_rejects_bad_checksum_and_shape(self):
        for frame in (GY26_201_1[:7]+b'\x0b',b'\r\n201,1\x09',GY26_201_1[:7],b'\r\n2X1.1'+bytes([sum(b'\r\n2X1.1')&0xFF])):
            with self.assertRaises(ValueError): parse_gy26(frame)
    def test_gy26_360_is_north_and_above_is_rejected(self):
        self.assertEqual(parse_gy26(gy26(3600)),0)
        with self.assertRaises(ValueError): parse_gy26(gy26(3601))
    def test_find_frame_skips_noise_and_bad_frames(self):
        bad=GY26_201_1[:7]+b'\x0b'
        self.assertEqual(find_gy26_frame(b'\x00\xff'+bad+gy26(1234)),(1234,b''))
        self.assertEqual(find_gy26_frame(b'xx\r\n20'),(None,b'\r\n20'))
        self.assertEqual(find_gy26_frame(b'xx\r'),(None,b'\r'))
    def test_nmea_checksum_and_shape(self):
        self.assertEqual(parse_nmea(NO_FIX[0])[0],'RMC')
        for line in (NO_FIX[0][:-1]+'E','GPRMC,1*00','$GPRMC,1*0',nmea('G1RMC,1')):
            with self.assertRaises(ValueError): parse_nmea(line)
    def test_no_fix_sentences_from_ublox(self):
        s=NmeaState()
        for line in NO_FIX: s.feed(*parse_nmea(line))
        self.assertFalse(s.valid);self.assertEqual((s.quality,s.sats_used,s.mode),(0,0,1));self.assertIsNone(s.lat)
    def test_fix_southern_and_eastern_hemispheres(self):
        s=NmeaState()
        for line in fix_lines(): s.feed(*parse_nmea(line))
        self.assertTrue(s.valid)
        self.assertAlmostEqual(s.lat,-(6+12.3456/60),6);self.assertAlmostEqual(s.lon,106+48.6/60,6)
        self.assertEqual((s.sats_used,s.sats_view(time.monotonic(),5),s.mode,s.alt_m,s.hdop),(7,9,3,15.5,1.2))
        self.assertAlmostEqual(s.speed_mps,2.57,2);self.assertEqual(s.course_deg,90.0)
        s.feed(*parse_nmea(fix_lines(ns='N',ew='W')[1]))
        self.assertGreater(s.lat,0);self.assertLess(s.lon,0)
    def test_nan_inf_and_exponents_never_become_numbers(self):
        for body in ('GPGGA,1,0612.3456,S,10648.6000,E,1,07,nan,15.5,M,,M,,','GPGGA,1,0612.3456,S,10648.6000,E,1,07,1.0,1e999,M,,M,,',
                     'GPGGA,1,0612.3456,S,-0648.6000,E,1,07,1.0,1.0,M,,M,,','GPRMC,1,A,0612.3456,S,10648.6000,E,inf,90,311026,,,A',
                     'GPRMC,1,A,0612.3456,S,10648.6000,E,1.0,400,311026,,,A','GPGGA,1,612.3456,S,10648.6000,E,1,07,1.0,1.0,M,,M,,'):
            s=NmeaState()
            with self.assertRaises(ValueError,msg=body): s.feed(*parse_nmea(nmea(body)))
            self.assertIsNone(s.lat);self.assertIsNone(s.speed_mps)
        s=NmeaState();s.feed(*parse_nmea(nmea('GPGGA,1,0612.3456,S,10648.6000,E,1,07,1.0,-12.5,M,,M,,')))
        self.assertEqual(s.alt_m,-12.5)
    def test_satellites_in_view_sum_constellations(self):
        s=NmeaState();now=time.monotonic()
        for body in ('GPGSV,2,1,09,01,40,083,40','GLGSV,1,1,03,70,40,083,40','GPGSV,2,1,08,01,40,083,40'):
            s.feed(*parse_nmea(nmea(body)))
        self.assertEqual(s.sats_view(time.monotonic(),1),11)
        self.assertIsNone(s.sats_view(time.monotonic()+5,1))
    def test_bad_coordinates_raise(self):
        s=NmeaState()
        with self.assertRaises(ValueError): s.feed(*parse_nmea(fix_lines(lat='0675.0000')[1]))
    def test_dgps_fix_from_gga_with_empty_rmc_course(self):
        s=NmeaState()
        for line in DGPS_FIX: s.feed(*parse_nmea(line))
        self.assertTrue(s.valid)
        self.assertEqual((s.quality,s.sats_used,s.sats_view(time.monotonic(),5),s.mode,s.hdop,s.alt_m),(2,5,9,3,1.68,715.9))
        self.assertAlmostEqual(s.lat,-(6+12.34567/60),7);self.assertAlmostEqual(s.lon,106+48.6/60,7)
        self.assertEqual(s.speed_mps,0.15);self.assertIsNone(s.course_deg)
    def test_gga_alone_is_enough_and_losing_it_drops_the_fix(self):
        s=NmeaState();s.feed(*parse_nmea(DGPS_FIX[2]))
        self.assertTrue(s.valid);self.assertIsNone(s.speed_mps)
        s.feed(*parse_nmea(NO_FIX[1]))
        self.assertFalse(s.valid);self.assertIsNone(s.hdop)

class ConfigTests(unittest.TestCase):
    def test_nav_defaults_off(self):
        c=load_config();self.assertFalse(c['nav']['compass']['enabled']);self.assertFalse(c['nav']['gps']['enabled'])
        self.assertFalse(c['nav']['gps']['publish_position'])
    def test_nav_validation(self):
        cases=[(('compass','enabled'),True,'NAV_COMPASS_DEVICE_REQUIRED'),(('compass','device'),'ttyUSB1','ABSOLUTE_PATH_REQUIRED'),
               (('gps','baud'),1234,'INVALID_NAV_BAUD'),(('compass','poll_ms'),50,'NUMBER_OUT_OF_RANGE'),
               (('compass','offset_deg'),400,'NUMBER_OUT_OF_RANGE'),(('gps','publish_position'),1,'BOOLEAN_REQUIRED'),
               (('compass','offset_deg'),'10','NUMBER_REQUIRED'),(('compass','stale_ms'),900,'NAV_STALE_SHORTER_THAN_TWO_POLLS')]
        for (section,key),value,error in cases:
            c=load_config();c['nav'][section][key]=value
            with self.assertRaisesRegex(ValueError,error): validate_config(c)
    def test_nav_device_cannot_be_the_t900_port_or_shared(self):
        c=load_config();c['nav']['compass'].update(enabled=True,device=c['link']['serial_alias'])
        with self.assertRaisesRegex(ValueError,'NAV_DEVICE_CONFLICT'): validate_config(c)
        c=load_config();c['nav']['compass'].update(enabled=True,device='/dev/ttyUSB1');c['nav']['gps'].update(enabled=True,device='/dev/ttyUSB1')
        with self.assertRaisesRegex(ValueError,'NAV_DEVICE_CONFLICT'): validate_config(c)

@unittest.skipUnless(hasattr(os,'openpty') and nav.termios is not None,'needs a POSIX pty')
class SerialTests(unittest.TestCase):
    def pty(self):
        master,slave=os.openpty();path=os.ttyname(slave)
        self.addCleanup(lambda:[os.close(fd) for fd in (master,slave) if self._close(fd)])
        return master,path
    @staticmethod
    def _close(fd):
        try: os.fstat(fd); return True
        except OSError: return False
    def fake_gy26(self,master,reply):
        stop=threading.Event();self.addCleanup(stop.set)
        def run():
            import select
            while not stop.is_set():
                try:
                    r,_,_=select.select([master],[],[],.05)
                    if not r: continue
                    data=os.read(master,64)
                except (OSError,ValueError): return
                for b in data:
                    if b==0x31:
                        out=reply()
                        if out: os.write(master,out)
        threading.Thread(target=run,daemon=True).start()
    def test_refuses_t900_port_and_non_tty(self):
        _,path=self.pty()
        with self.assertRaisesRegex(SensorFault,'DEVICE_IS_LINK_PORT'): open_serial(path,9600,{'serial_alias':path})
        with tempfile.NamedTemporaryFile() as f:
            with self.assertRaisesRegex(SensorFault,'NOT_A_TTY'): open_serial(f.name,9600)
        with self.assertRaisesRegex(SensorFault,'DEVICE_MISSING'): open_serial('/dev/rdf-no-such-device',9600)
    def test_compass_polls_reads_and_applies_offset(self):
        master,path=self.pty();value={'tenths':3595}
        self.fake_gy26(master,lambda:gy26(value['tenths']))
        c=Compass(nav_cfg(compass={'enabled':True,'device':path,'offset_deg':1.0})['compass'])
        c.start();self.addCleanup(c.stop)
        self.assertTrue(wait(lambda:c.snapshot()['state']=='OK',3),c.snapshot())
        snap=c.snapshot()
        # 359.5 + 1.0 wraps to 0.5, never 360.0.
        self.assertEqual((snap['heading_deg'],snap['raw_deg']),(0.5,359.5))
        value['tenths']=2011
        self.assertTrue(wait(lambda:c.snapshot()['heading_deg']==202.1,3),c.snapshot())
    def test_compass_silence_clears_heading(self):
        master,path=self.pty();answer={'on':True}
        self.fake_gy26(master,lambda:GY26_201_1 if answer['on'] else None)
        c=Compass(nav_cfg(compass={'enabled':True,'device':path})['compass'])
        c.start();self.addCleanup(c.stop)
        self.assertTrue(wait(lambda:c.snapshot()['heading_deg']==201.1,3))
        answer['on']=False
        self.assertTrue(wait(lambda:c.snapshot()['state']=='NO_REPLY',3),c.snapshot())
        self.assertIsNone(c.snapshot()['heading_deg'])
        self.assertGreater(c.snapshot()['no_reply'],0)
    def test_compass_garbage_is_bad_frame(self):
        master,path=self.pty()
        self.fake_gy26(master,lambda:GY26_201_1[:7]+b'\x0b')
        c=Compass(nav_cfg(compass={'enabled':True,'device':path})['compass'])
        c.start();self.addCleanup(c.stop)
        self.assertTrue(wait(lambda:c.snapshot()['state']=='BAD_FRAME',3),c.snapshot())
        self.assertIsNone(c.snapshot()['heading_deg'])
    def test_missing_device_is_reported_and_retried(self):
        c=Compass(nav_cfg(compass={'enabled':True,'device':'/dev/rdf-no-such-device'})['compass'])
        c.start();self.addCleanup(c.stop)
        self.assertTrue(wait(lambda:c.snapshot()['state']=='NO_DEVICE',2))
        self.assertEqual(c.snapshot()['error'],'DEVICE_MISSING')
    def test_t900_identity_refused_when_alias_points_elsewhere_by_name(self):
        _,path=self.pty();alias=Path(tempfile.mkdtemp())/'t900';self.addCleanup(lambda:alias.unlink() if alias.is_symlink() else None)
        alias.symlink_to(path)
        fd=os.open(path,os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK);self.addCleanup(os.close,fd)
        self.assertTrue(is_link_port(fd,{'serial_alias':str(alias)}))
        self.assertFalse(is_link_port(fd,{'serial_alias':'/dev/rdf-no-such-alias'}))
        with mock.patch.object(nav,'usb_identity',return_value=('1a86','7523')):
            self.assertTrue(is_link_port(fd,{'serial_alias':'/dev/rdf-no-such-alias','expected_vid':'1a86','expected_pid':'7523'}))
            with self.assertRaisesRegex(SensorFault,'DEVICE_IS_LINK_PORT'):
                open_serial(path,9600,{'serial_alias':'/dev/rdf-no-such-alias','expected_vid':'1a86','expected_pid':'7523'})
    def test_gps_garbage_is_bad_data(self):
        master,path=self.pty()
        g=Gps(nav_cfg(gps={'enabled':True,'device':path,'stale_ms':500})['gps'])
        g.start();self.addCleanup(g.stop)
        self.assertTrue(wait(lambda:g.fd is not None,2))
        stop=threading.Event();self.addCleanup(stop.set)
        def noise():
            while not stop.is_set():
                try: os.write(master,b'\x00\xfe\x80garbage-at-wrong-baud\r\n')
                except OSError: return
                time.sleep(.1)
        threading.Thread(target=noise,daemon=True).start()
        self.assertTrue(wait(lambda:g.snapshot()['state']=='BAD_DATA',3),g.snapshot())
    def test_rmc_motion_expires_while_gga_keeps_fix(self):
        master,path=self.pty()
        g=Gps(nav_cfg(gps={'enabled':True,'device':path,'stale_ms':600})['gps'])
        g.start();self.addCleanup(g.stop)
        self.assertTrue(wait(lambda:g.fd is not None,2))
        os.write(master,('\r\n'.join(DGPS_FIX)+'\r\n').encode())
        self.assertTrue(wait(lambda:g.snapshot()['speed_mps']==0.15,3),g.snapshot())
        stop=threading.Event();self.addCleanup(stop.set)
        def gga_only():
            while not stop.is_set():
                try: os.write(master,(DGPS_FIX[2]+'\r\n').encode())
                except OSError: return
                time.sleep(.1)
        threading.Thread(target=gga_only,daemon=True).start()
        self.assertTrue(wait(lambda:g.snapshot()['state']=='FIX' and g.snapshot()['speed_mps'] is None,3),g.snapshot())
    def test_disabled_sensor_starts_no_thread(self):
        c=Compass(nav_cfg()['compass']);c.start()
        self.assertIsNone(c.thread);self.assertEqual(c.snapshot()['state'],'DISABLED')
    def test_gps_fix_then_data_loss(self):
        master,path=self.pty()
        g=Gps(nav_cfg(gps={'enabled':True,'device':path})['gps'])
        g.start();self.addCleanup(g.stop)
        # open_serial flushes pending input: write only once the reader owns the port.
        self.assertTrue(wait(lambda:g.fd is not None,2))
        os.write(master,('\r\n'.join(NO_FIX)+'\r\n').encode())
        self.assertTrue(wait(lambda:g.snapshot()['state']=='NO_FIX',3),g.snapshot())
        self.assertIsNone(g.snapshot()['lat'])
        os.write(master,('\r\n'.join(DGPS_FIX)+'\r\n$GPGGA,garbage*00\r\n').encode())
        self.assertTrue(wait(lambda:g.snapshot()['state']=='FIX',3),g.snapshot())
        snap=g.snapshot()
        self.assertEqual((snap['fix'],snap['sats_used'],snap['alt_m'],snap['hdop']),(3,5,715.9,1.68))
        self.assertAlmostEqual(snap['lat'],-6.205761,6);self.assertGreaterEqual(snap['bad_sentences'],1)
        # No more sentences: the fix goes away instead of freezing at the last position.
        self.assertTrue(wait(lambda:g.snapshot()['state']=='NO_DATA',3),g.snapshot())
        self.assertIsNone(g.snapshot()['lat']);self.assertIsNone(g.snapshot()['sats_used'])

class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
    def agent(self,**nav_over):
        from test_system import make_agent
        path=Path(os.path.realpath(self.tmp.name))/str(len(os.listdir(self.tmp.name)))
        path.mkdir()
        a=make_agent(path);self.addCleanup(a.journal.close)
        for name,values in nav_over.items(): a.cfg['nav'][name].update(values)
        a.nav_enabled=a.cfg['nav']['compass']['enabled'] or a.cfg['nav']['gps']['enabled']
        return a
    def nav_snapshot(self,heading=201.1,state='FIX'):
        fix=state=='FIX'
        return {'compass':{'state':'OK' if heading is not None else 'NO_REPLY','heading_deg':heading},
                'gps':{'state':state,'fix':3 if fix else 0,'sats_used':7 if fix else 0,
                       'lat':-6.205764 if fix else None,'lon':106.81 if fix else None,'alt_m':15.5 if fix else None,
                       'speed_mps':2.57 if fix else None,'course_deg':90.0 if fix else None}}
    def health(self,a,nav_snapshot,live=True):
        messages=[]
        from rdf_node.mqtt import Client
        control=Client(cfg={},client_id='nav-capture');control.ready=True
        control.offer=lambda key,topic,payload,**o:messages.append((topic,payload)) or True
        a.clients={'control':control};a._generation=-1
        snap=a.snapshot();snap['nav']=nav_snapshot
        # The scheduler reads the sensors at send time; tests stand in for them.
        if live: a.nav.snapshot=lambda:nav_snapshot
        def once():
            a.stop_event.set();return snap
        a.snapshot=once;a.stop_event.clear();a._schedule()
        return next(json.loads(p) for t,p in messages if t.endswith('/telemetry/health'))
    def test_health_unchanged_without_nav(self):
        h=self.health(self.agent(),self.nav_snapshot())
        self.assertFalse({'hdg','hs','gs','fix','sat','lat','lon'}&set(h))
    def test_compass_and_gps_fields_without_position_by_default(self):
        a=self.agent(compass={'enabled':True,'device':'/dev/ttyUSB1'},gps={'enabled':True,'device':'/dev/ttyACM0'})
        h=self.health(a,self.nav_snapshot())
        self.assertEqual((h['hdg'],h['hs'],h['gs'],h['fix'],h['sat']),(201.1,'OK','FIX',3,7))
        self.assertNotIn('lat',h)
        self.assertEqual(contract.errors(h,contract.schema_for('telemetry/health')),[])
    def test_position_only_with_publish_position(self):
        a=self.agent(gps={'enabled':True,'device':'/dev/ttyACM0','publish_position':True})
        h=self.health(a,self.nav_snapshot())
        self.assertEqual((h['lat'],h['lon'],h['alt'],h['sog'],h['cog']),(-6.20576,106.81,15.5,2.6,90))
        self.assertNotIn('hdg',h)
        self.assertEqual(contract.errors(h,contract.schema_for('telemetry/health')),[])
        h=self.health(a,self.nav_snapshot(state='NO_FIX'))
        self.assertEqual((h['gs'],h['fix']),('NO_FIX',0));self.assertNotIn('lat',h)
    def test_silent_compass_sends_null_heading(self):
        a=self.agent(compass={'enabled':True,'device':'/dev/ttyUSB1'})
        h=self.health(a,self.nav_snapshot(heading=None))
        self.assertIsNone(h['hdg']);self.assertEqual(h['hs'],'NO_REPLY')
        self.assertEqual(contract.errors(h,contract.schema_for('telemetry/health')),[])
    def test_nav_fields_reach_primary_and_backup_brokers(self):
        from rdf_node.mqtt import Client
        a=self.agent(compass={'enabled':True,'device':'/dev/ttyUSB1'},gps={'enabled':True,'device':'/dev/ttyACM0','publish_position':True})
        sent={'primary':[],'backup':[]}
        def client(link):
            c=Client(cfg={},client_id='nav-'+link);c.ready=True
            c.offer=lambda key,topic,payload,**o:sent[link].append((topic,payload)) or True
            return c
        a.clients={'control':client('primary')};a.backup_client=client('backup');a._generation=-1;a._backup_generation=-1
        snap=a.snapshot();nav_now=self.nav_snapshot();a.nav.snapshot=lambda:nav_now
        def once():
            a.stop_event.set();return snap
        a.snapshot=once;a.stop_event.clear();a._schedule()
        for link in ('primary','backup'):
            h=next(json.loads(p) for t,p in sent[link] if t.endswith('/telemetry/health'))
            self.assertEqual((h['hdg'],h['gs'],h['lat'],h['lon']),(201.1,'FIX',-6.20576,106.81),link)
        # Backup down: the primary still carries the nav fields.
        a.backup_client.ready=False;sent['primary'].clear();a.stop_event.clear();a._schedule()
        self.assertEqual(next(json.loads(p) for t,p in sent['primary'] if t.endswith('/telemetry/health'))['hdg'],201.1)
    def test_non_finite_or_out_of_range_values_never_cost_health(self):
        a=self.agent(compass={'enabled':True,'device':'/dev/ttyUSB1'},gps={'enabled':True,'device':'/dev/ttyACM0','publish_position':True})
        bad=self.nav_snapshot();bad['gps'].update(alt_m=float('inf'),speed_mps=float('nan'),course_deg=float('nan'))
        bad['compass']['heading_deg']=float('nan')
        a.nav.snapshot=lambda:bad
        h=self.health(a,bad)
        self.assertEqual((h['hdg'],h['alt'],h['sog'],h['cog'],h['lat']),(None,None,None,None,-6.20576))
        self.assertEqual(contract.errors(h,contract.schema_for('telemetry/health')),[])
        a.nav.snapshot=mock.Mock(side_effect=RuntimeError('boom'))
        h=self.health(a,bad,live=False)
        self.assertNotIn('hdg',h);self.assertEqual(h['run'],1)
        self.assertIn('telemetry/health:nav',a.publish_errors)
    def test_sensor_failure_does_not_freeze_snapshot(self):
        a=self.agent()
        a.nav.snapshot=mock.Mock(side_effect=RuntimeError('boom'))
        before=a.snapshot()['snapshot_seq'];a._snapshot()
        snap=a.snapshot()
        self.assertEqual(snap['snapshot_seq'],before+1)
        self.assertEqual(snap['nav']['compass']['state'],'ERROR')
    def test_capabilities_advertise_nav(self):
        a=self.agent(compass={'enabled':True,'device':'/dev/ttyUSB1'},gps={'enabled':True,'device':'/dev/ttyACM0'})
        self.assertEqual(a.capabilities()['nav'],{'compass':True,'gps':True,'position':False})

class GroundNavTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        cfg=load_config();cfg['state_dir']=self.tmp.name
        self.g=Ground(cfg);self.addCleanup(self.g.journal.close)
        self.sid='12ab34cd';self.q=0
        self.g.receive(self.g.prefix+'/state',compact({'v':2,'sid':self.sid,'boot':'b','instance':'i','cfg':1}))
    def health(self,**extra):
        self.q+=1
        body={'v':2,'sid':self.sid,'q':self.q,'t':now_ms(),'run':1,'daq':1,'drop':0,'age':0,'temp':50.0,'clk':1,'rev':1,'why':[]}
        body.update(extra);self.g.receive(self.g.prefix+'/telemetry/health',compact(body))
    def test_old_edge_health_has_no_nav(self):
        self.health();self.assertIsNone(self.g.snapshot()['nav'])
    def test_heading_and_track(self):
        self.health(hdg=201.1,hs='OK',gs='FIX',fix=3,sat=7,lat=-6.2,lon=106.8,alt=15.5,sog=0.0,cog=0)
        nav=self.g.snapshot()['nav']
        self.assertEqual((nav['hdg'],nav['hs'],nav['lat'],nav['fix'],nav['track_points']),(201.1,'OK',-6.2,3,1))
        self.assertTrue(nav['fresh'])
        # Under 2 m and 10 s apart: no new point. ~11 m north: a new point.
        self.health(hdg=202.0,hs='OK',gs='FIX',fix=3,sat=7,lat=-6.20001,lon=106.8)
        self.health(hdg=203.0,hs='OK',gs='FIX',fix=3,sat=7,lat=-6.1999,lon=106.8)
        track=self.g.nav_track_view()['points']
        self.assertEqual([p[1] for p in track],[-6.2,-6.1999])
        self.assertEqual(track[-1][3],203.0)
    def test_bad_nav_fields_never_cost_health(self):
        self.health(hdg=900,hs='OK',gs='FIX',fix=3,sat=6,lat=1.0,lon=2.0)
        snap=self.g.snapshot()
        self.assertEqual(snap['health']['q'],1);self.assertTrue(snap['health_fresh'])
        # A bad heading does not hide a good GPS fix, and the other way round.
        self.assertEqual((snap['nav']['hs'],snap['nav']['hdg'],snap['nav']['gs'],snap['nav']['lat']),('INVALID',None,'FIX',1.0))
        self.health(hdg=12.5,hs='OK',gs='FIX',fix=0,lat=1.0,lon=2.0)
        nav=self.g.snapshot()['nav']
        self.assertEqual((nav['hdg'],nav['gs'],nav['lat']),(12.5,'INVALID',None))
        self.health(hdg=12.5,hs='OK',gs='FIX',fix=3,sat=10**30,lat=1.0,lon=2.0)
        self.assertEqual(self.g.snapshot()['nav']['gs'],'INVALID')
        self.health(hdg=12.5,hs='OK',gs='FIX',fix=3,sat=6,lat=1.0,lon=2.0,alt=1e9)
        nav=self.g.snapshot()['nav'];self.assertEqual((nav['lat'],nav['alt']),(1.0,None))
        self.assertEqual(self.g.snapshot()['link']['reject_reasons'],
                         {'NUMBER_OUT_OF_RANGE':2,'BAD_NAV_POSITION':1,'INTEGER_OUT_OF_RANGE':1})
    def test_skewed_edge_clock_is_not_fresh_and_not_tracked(self):
        self.q+=1
        body={'v':2,'sid':self.sid,'q':self.q,'t':now_ms()-3600000,'run':1,'daq':1,'drop':0,'age':0,'temp':None,'clk':0,'rev':1,
              'hdg':5.0,'hs':'OK','gs':'FIX','fix':3,'sat':5,'lat':1.0,'lon':2.0}
        self.g.receive(self.g.prefix+'/telemetry/health',compact(body))
        nav=self.g.snapshot()['nav']
        self.assertFalse(nav['fresh']);self.assertIsNone(nav['lat']);self.assertEqual(nav['track_points'],0)
    def test_stale_nav_hides_numbers(self):
        self.health(hdg=10.0,hs='OK',gs='FIX',fix=3,sat=5,lat=1.0,lon=2.0)
        self.g.health_seen-=6;self.g.nav_seen-=6
        nav=self.g.snapshot()['nav']
        self.assertFalse(nav['fresh']);self.assertIsNone(nav['hdg']);self.assertIsNone(nav['lat'])
        self.assertEqual(nav['hs'],'OK')
    def test_track_api(self):
        from rdf_node.api import Server
        import http.client
        self.g.cfg['api']['port']=0
        server=Server(self.g,self.g.cfg,ground=True);threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        self.health(hdg=1.0,hs='OK',gs='FIX',fix=2,sat=4,lat=1.0,lon=2.0)
        conn=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=3)
        conn.request('GET','/api/v2/nav/track',headers={'Host':f'127.0.0.1:{server.server_address[1]}'})
        body=json.loads(conn.getresponse().read());conn.close()
        self.assertEqual([p[1:3] for p in body['points']],[[1.0,2.0]])

if __name__=='__main__': unittest.main()
