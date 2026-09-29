import copy,json,math,os,struct,tempfile,time,unittest
from pathlib import Path
from unittest import mock
from rdf_node.config import load_config,validate_config
from rdf_node.util import compact,strict_json,now_ms,digest
from rdf_node.source import parse_csv,parse_status,safe_settings,Source
from rdf_node.codec import HEADER,CHUNK,encode,decode,split,Assembler
from rdf_node.journal import Journal
from rdf_node.helper import DEFAULT_POLICY,Controller,validate_changes,patch_file
from rdf_node.mqtt import vi,read_vi,Reader,publish_packet,connect_packet,properties,utf,Message,Outbox

def settings():
    return {'center_freq':433.92,'uniform_gain':15.7,'vfo_freq_0':433920000,'vfo_bw_0':12500,
            'active_vfos':1,'output_vfo':0,'ant_arrangement':'UCA','doa_method':'MUSIC','en_doa':True}
def record(t=None):
    return [now_ms() if t is None else t,10.0,8.27,-90.17,433920000,'UCA',436,'TEST',0,0,0,0,'GPS','R','R','R','R']+[-10+i/100 for i in range(360)]
def csv_bytes(r):return (','.join(map(str,r))+'\n').encode()
def status(t=None,idx=1):
    return {'timestamp_ms':now_ms() if t is None else t,'daq_ok':True,'daq_num_dropped_frames':2,
            'daq_status':{'data_frame_index':idx,'frame_sync':True,'sample_delay_sync':True,'iq_sync':True}}

class ConfigTests(unittest.TestCase):
    def test_default_config(self): self.assertEqual(load_config()['api']['port'],8790)
    def test_plaintext_remote_rejected(self):
        c=load_config();c['mqtt']['tls']=False
        with self.assertRaises(ValueError):validate_config(c)
    def test_explicit_loopback_test_allowed(self):
        c=load_config();c['mqtt'].update(tls=False,host='127.0.0.1',allow_insecure_loopback=True);validate_config(c)
    def test_unknown_yaml_key_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'c.yaml';p.write_text('dangerous_new_key: true\n')
            with self.assertRaises(ValueError):load_config(p)
    def test_boolean_string_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'c.yaml';p.write_text('mqtt:\n  enabled: "false"\n')
            with self.assertRaises(ValueError):load_config(p)
    def test_invalid_unit(self):
        c=load_config();c['link']['engine_service']='x; reboot'
        with self.assertRaises(ValueError):validate_config(c)
    def test_unapproved_fast_profile(self):
        c=load_config();c['telemetry']['profile']='fast'
        with self.assertRaises(ValueError):validate_config(c)

class ParserTests(unittest.TestCase):
    def test_377_csv_values(self):
        r=parse_csv(csv_bytes(record()),settings());self.assertEqual(len(r['values']),360);self.assertEqual(r['raw_doa_deg'],10)
    def test_negative_power_preserved(self):self.assertEqual(parse_csv(csv_bytes(record()),settings())['power_native_db'],-90.17)
    def test_confidence_not_probability(self):self.assertEqual(parse_csv(csv_bytes(record()),settings())['confidence_native_db'],8.27)
    def test_trailing_delimiter(self):self.assertEqual(len(parse_csv(csv_bytes(record()).rstrip()+b',\n',settings())['values']),360)
    def test_short_record_rejected(self):
        with self.assertRaises(ValueError):parse_csv(csv_bytes(record()[:-1]),settings())
    def test_nan_rejected(self):
        r=record();r[20]='NaN'
        with self.assertRaises(ValueError):parse_csv(csv_bytes(r),settings())
    def test_inf_rejected(self):
        r=record();r[3]='Infinity'
        with self.assertRaises(ValueError):parse_csv(csv_bytes(r),settings())
    def test_null_byte_rejected(self):
        with self.assertRaises(ValueError):parse_csv(b'\0'+csv_bytes(record()),settings())
    def test_multi_record_ambiguous(self):
        with self.assertRaises(ValueError):parse_csv(csv_bytes(record())*2,settings())
    def test_multi_record_no_authority(self):
        with self.assertRaises(ValueError):parse_csv(csv_bytes(record())*2,None)
    def test_frequency_mismatch(self):
        r=record();r[4]=100000000
        with self.assertRaises(ValueError):parse_csv(csv_bytes(r),settings())
    def test_settings_redaction(self):
        s=settings();s.update(api_key='do-not-export',password='secret',remote_url='secret')
        result=safe_settings(s);self.assertNotIn('secret',str(result));self.assertNotIn('api_key',result)
    def test_frequency_unit_conversion(self):self.assertEqual(safe_settings(settings())['center_frequency_hz'],433920000)
    def test_status_string_true_not_true(self):
        s=status();s['daq_ok']='true';self.assertIsNone(parse_status(compact(s))['daq_ok'])
    def test_duplicate_json_rejected(self):
        with self.assertRaises(ValueError):strict_json('{"x":1,"x":2}')
    def test_json_nan_rejected(self):
        with self.assertRaises(ValueError):strict_json('{"x":NaN}')

class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
        self.cfg=load_config();self.cfg['source'].update(share_dir=str(self.path),authority_verified=True,angle_verified=True)
        self.j=Journal(self.path/'test.sqlite3');self.addCleanup(self.j.close)
        (self.path/'settings.json').write_bytes(compact(settings()))
        time.sleep(.01);self.write(1)
        self.src=Source(self.cfg,self.j)
    def write(self,idx):
        t=now_ms();(self.path/'status.json').write_bytes(compact(status(t,idx)));(self.path/'DOA_value.html').write_bytes(csv_bytes(record(t)))
    def test_same_timestamp_does_not_increment_q(self):
        self.src.poll(force=True);q=self.src.seq;self.src.poll(force=True);self.assertEqual(self.src.seq,q)
    def test_clock_untrusted_blocks_live(self):
        self.src.poll(force=True);self.assertFalse(self.src.view(False)['valid'])
    def test_unverified_source_blocks_live(self):
        self.cfg['source']['authority_verified']=False;self.src.poll(force=True);self.assertIn('SOURCE_UNVERIFIED',self.src.view(True)['reasons'])
    def test_status_false_blocks_live(self):
        s=status();s['daq_ok']=False;(self.path/'status.json').write_bytes(compact(s));self.src.poll(force=True);self.assertFalse(self.src.view(True)['daq']['healthy'])
    def test_stale_record_rejected(self):
        (self.path/'DOA_value.html').write_bytes(csv_bytes(record(now_ms()-30000)));self.src.poll(force=True);self.assertIn('NO_FRESH_DOA',self.src.view(True)['reasons'])
    def test_partial_file_preserves_old_age(self):
        self.src.poll(force=True);q=self.src.seq;r=self.src.record
        (self.path/'DOA_value.html').write_bytes(b'123,456,');self.src.poll(force=True)
        self.assertEqual(self.src.seq,q);self.assertEqual(self.src.record,r);self.assertFalse(self.src.view(True)['valid'])
    def test_no_path_local_mode(self):
        self.cfg['source']['share_dir']=None;s=Source(self.cfg,self.j);s.poll();self.assertEqual(s.view(True)['state'],'SETUP_REQUIRED')
    def test_config_change_does_not_relabel_old_record(self):
        self.src.poll(force=True);oldrev=self.src.record['observed_revision'];s=settings();s['uniform_gain']=20.7
        (self.path/'settings.json').write_bytes(compact(s));self.src.poll(force=True)
        self.assertNotEqual(self.src.revision,oldrev);self.assertEqual(self.src.record['observed_revision'],oldrev);self.assertFalse(self.src.view(True)['config_attributed'])

class CodecTests(unittest.TestCase):
    def frame(self,values=None,encoding='q16'):
        return encode(values or [-20+math.sin(i/15)*5 for i in range(360)],sid=0x1234,seq=8,timestamp_ms=now_ms(),frequency_hz=433920000,revision=4,raw_doa=10,confidence=8.27,encoding=encoding)
    def test_header_sizes(self):self.assertEqual(HEADER.size,48);self.assertEqual(CHUNK.size,12)
    def test_q16_full_roundtrip(self):
        vals=[-30+i/17 for i in range(360)];out=decode(self.frame(vals));self.assertLess(max(abs(a-b) for a,b in zip(vals,out['values'])),.0051)
    def test_q16_payload_size(self):self.assertEqual(len(self.frame()),768);self.assertEqual([len(x) for x in split(self.frame())],[396,396])
    def test_u8_payload_size(self):self.assertEqual(len(self.frame(encoding='u8')),408);self.assertEqual(len(split(self.frame(encoding='u8'))[0]),420)
    def test_constant_u8(self):self.assertEqual(decode(self.frame([-4]*360,'u8'))['values'],[-4.0]*360)
    def test_q16_range_not_clipped(self):
        with self.assertRaises(ValueError):self.frame([400]*360)
    def test_nan_codec_rejected(self):
        with self.assertRaises(ValueError):self.frame([math.nan]*360)
    def test_wrong_count(self):
        with self.assertRaises(ValueError):self.frame([1]*359)
    def test_chunk_reverse_order(self):
        chunks=split(self.frame());a=Assembler();self.assertIsNone(a.add(chunks[1]));self.assertEqual(len(a.add(chunks[0])['values']),360)
    def test_duplicate_conflict(self):
        c=split(self.frame());a=Assembler();a.add(c[0]);bad=c[0][:-1]+bytes([c[0][-1]^1])
        with self.assertRaises(ValueError):a.add(bad)
    def test_assembly_deadline(self):
        c=split(self.frame());a=Assembler();a.add(c[0],now=1);self.assertIsNone(a.add(c[1],now=5))
    def test_bad_magic(self):
        f=self.frame()
        with self.assertRaises(ValueError):decode(b'BAD!'+f[4:])
    def test_envelope_sid_mismatch(self):
        c=split(self.frame());c=[b'\x99\0\0\0'+p[4:] for p in c];a=Assembler();a.add(c[0])
        with self.assertRaises(ValueError):a.add(c[1])

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.j=Journal(Path(self.tmp.name)/'j.db');self.addCleanup(self.j.close)
    def test_idempotent_same_request(self):
        r={'id':'x','op':'config.get'};self.assertTrue(self.j.accept(r,'test')[0]);self.assertFalse(self.j.accept(r,'test')[0])
    def test_id_conflict(self):
        self.j.accept({'id':'x','op':'config.get'},'test')
        with self.assertRaises(ValueError):self.j.accept({'id':'x','op':'service.restart'},'test')
    def test_crash_recovery_no_replay(self):
        self.j.accept({'id':'x','op':'config.patch','boot':'old'},'test');self.j.recover('old');self.assertEqual(self.j.lookup('x')['stage'],'OUTCOME_UNKNOWN')
    def test_reboot_requires_new_boot(self):
        self.j.accept({'id':'x','op':'system.reboot.execute','boot':'old'},'test');self.j.update('x','REBOOT_SCHEDULED');self.j.recover('new');self.assertEqual(self.j.lookup('x')['stage'],'APPLIED')
    def test_revision_stable(self):self.assertEqual(self.j.config_revision('a'),self.j.config_revision('a'));self.assertEqual(self.j.config_revision('b'),2)

class HelperTests(unittest.TestCase):
    def setUp(self):
        import pwd
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
        self.p=dict(DEFAULT_POLICY);self.p.update(state_dir=str(self.path/'state'),allowed_user=pwd.getpwuid(os.getuid()).pw_name,engine_service='test-sdr.service')
        self.ctrl=Controller(self.p)
    def test_arbitrary_rpc_denied(self):
        with self.assertRaises(ValueError):self.ctrl.dispatch({'op':'shell','command':'anything'},os.getuid())
    def test_wrong_peer_uid_denied(self):
        with self.assertRaises(ValueError):self.ctrl.dispatch({'op':'status'},999999)
    def test_unknown_rpc_argument_denied(self):
        with self.assertRaises(ValueError):self.ctrl.dispatch({'op':'status','unit':'ssh.service'},os.getuid())
    def test_lifecycle_disabled(self):
        with self.assertRaises(ValueError):self.ctrl.dispatch({'op':'processing.set','desired':'STOPPED'},os.getuid())
    def test_reboot_disabled(self):
        with self.assertRaises(ValueError):self.ctrl.dispatch({'op':'system.reboot.prepare','id':'x'},os.getuid())
    def test_patch_digest_compare_swap(self):
        file=self.path/'settings.json';file.write_bytes(compact(settings()))
        with self.assertRaises(ValueError):patch_file(file,{'gain_db':15.7},'wrong',self.p)
    def test_config_patch_preserves_secrets_local(self):
        import hashlib
        file=self.path/'settings.json';s=settings();s['api_key']='fixture-local-only';raw=compact(s);file.write_bytes(raw)
        r=patch_file(file,{'gain_db':20.7},hashlib.sha256(raw).hexdigest(),self.p)
        self.assertNotIn('fixture-local-only',str(r));self.assertEqual(json.loads(file.read_bytes())['api_key'],'fixture-local-only')
    def test_patch_forbids_full_settings(self):
        with self.assertRaises(ValueError):validate_changes({'api_key':'x'},self.p)
    def test_native_symlink_rejected(self):
        import hashlib
        file=self.path/'settings.json';target=self.path/'other.json';raw=compact(settings());target.write_bytes(raw);file.symlink_to(target)
        with self.assertRaises(OSError):patch_file(file,{'gain_db':20.7},hashlib.sha256(raw).hexdigest(),self.p)
    def test_stop_persists_marker_no_real_systemctl(self):
        self.p.update(allow_lifecycle=True,lifecycle_audited=True)
        with mock.patch.object(self.ctrl,'maintenance',return_value=True),mock.patch.object(self.ctrl,'_run') as run:
            r=self.ctrl.dispatch({'op':'processing.set','desired':'STOPPED'},os.getuid())
        self.assertTrue((self.path/'state/engine.stopped').exists());self.assertEqual(r['requested'],'STOPPED');run.assert_called_once()
    def test_reboot_challenge_consumed_once_no_real_reboot(self):
        self.p['allow_reboot']=True
        with mock.patch.object(self.ctrl,'maintenance',return_value=True),mock.patch.object(self.ctrl,'_run'):
            r=self.ctrl.dispatch({'op':'system.reboot.prepare','id':'p'},os.getuid())
            e={'op':'system.reboot.execute','id':'p','challenge':r['challenge']}
            self.assertTrue(self.ctrl.dispatch(e,os.getuid())['scheduled'])
            with self.assertRaises(ValueError):self.ctrl.dispatch(e,os.getuid())

class MqttWireTests(unittest.TestCase):
    def test_varints(self):
        for n in (0,127,128,16383,16384,268435455):self.assertEqual(read_vi(vi(n))[0],n)
    def test_malformed_varint(self):
        with self.assertRaises(ValueError):read_vi(b'\xff\xff\xff\xff\x01')
    def test_utf_nul(self):
        with self.assertRaises(ValueError):utf('bad\x00')
    def test_fragmented_packet(self):
        raw=publish_packet('test/topic',b'hello',qos=0);r=Reader();out=[]
        for b in raw:out+=r.feed(bytes([b]))
        self.assertEqual(len(out),1);self.assertEqual(out[0][0],0x30)
    def test_connect_mqtt5(self):
        raw=connect_packet('client','u','p');self.assertIn(b'\x00\x04MQTT\x05',raw);self.assertEqual(raw[0],0x10)
    def test_packet_limit(self):
        with self.assertRaises(ValueError):Reader().feed(b'\x30'+vi(20000))
    def test_latest_value_queue(self):
        q=Outbox();q.offer(Message('h','t',b'1',0,False,1,1,time.monotonic()));q.offer(Message('h','t',b'2',0,False,1,1,time.monotonic()));self.assertEqual(q.pop().payload,b'2');self.assertEqual(q.superseded,1)
    def test_expiry_queue(self):
        q=Outbox();q.offer(Message('h','t',b'1',0,False,1,1,0));self.assertIsNone(q.pop(now=10))
    def test_priority(self):
        q=Outbox();q.offer(Message('bulk','t',b'b',0,False,10,3,time.monotonic()));q.offer(Message('ack','t',b'a',1,False,10,0,time.monotonic()));self.assertEqual(q.pop().payload,b'a')
    def test_bounded_queue(self):
        q=Outbox(max_messages=1);self.assertTrue(q.offer(Message('a','t',b'a',0,False,None,1,0)));self.assertFalse(q.offer(Message('b','t',b'b',0,False,None,1,0)))
