import copy,json,math,os,shutil,struct,sys,tempfile,time,unittest
from pathlib import Path
from unittest import mock
from rdf_node.config import load_config,validate_config
from rdf_node.util import compact,strict_json,now_ms,digest
from rdf_node.source import parse_csv,parse_doa_xml,parse_status,safe_settings,Source
from rdf_node.codec import decode
from rdf_node.journal import Journal
from rdf_node.helper import DEFAULT_POLICY,Controller,HelperError,validate_changes,patch_file
from rdf_node.mqtt import vi,read_vi,Reader,publish_packet,connect_packet,properties,utf,Message,Outbox
from rdf_node import monitor

def settings():
    return {'center_freq':433.92,'uniform_gain':15.7,'vfo_freq_0':433920000,'vfo_bw_0':12500,
            'active_vfos':1,'output_vfo':0,'ant_arrangement':'UCA','doa_method':'MUSIC','en_doa':True}
def record(t=None):
    return [now_ms() if t is None else t,10.0,8.27,-90.17,433920000,'UCA',436,'TEST',0,0,0,0,'GPS','R','R','R','R']+[-10+i/100 for i in range(360)]
def csv_bytes(r):return (','.join(map(str,r))+'\n').encode()
def status(t=None,idx=1):
    return {'timestamp_ms':now_ms() if t is None else t,'daq_ok':True,'daq_num_dropped_frames':2,
            'daq_status':{'data_frame_index':idx,'frame_sync':True,'sample_delay_sync':True,'iq_sync':True}}
class PppProbeTests(unittest.TestCase):
    def test_ppp_probe_status_starts_unknown(self):
        self.assertEqual(monitor.Monitor(load_config()).snapshot().get('ppp_probe'),'UNKNOWN')
    def test_ppp_probe_without_interface_does_not_claim_reachability(self):
        with mock.patch.object(monitor.subprocess,'run') as command:
            self.assertEqual(monitor.probe_peer(None,load_config()['link']['peer_ip']),'NO_INTERFACE')
        command.assert_not_called()
    def test_ppp_probe_reports_no_reply(self):
        result=mock.Mock(returncode=1)
        with mock.patch.object(monitor.subprocess,'run',return_value=result):
            self.assertEqual(monitor.probe_peer('ppp0',load_config()['link']['peer_ip']),'NO_REPLY')
    def test_ppp_probe_no_reply_keeps_interface_status_up(self):
        host=monitor.Monitor(load_config())
        host.data.update(ppp='UP',interface='ppp0')
        with mock.patch.object(monitor.subprocess,'run',return_value=mock.Mock(returncode=1)):
            host.probe(5)
        state=host.snapshot()
        self.assertEqual(state['ppp'],'UP')
        self.assertEqual(state.get('ppp_probe'),'NO_REPLY')
    @unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('ping'),'Linux ping is required')
    def test_ppp_probe_reaches_peer_on_selected_interface(self):
        self.assertEqual(monitor.probe_peer('lo','127.0.0.1'),'REPLY')
    @unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('ping'),'Linux ping is required')
    def test_ppp_probe_does_not_fall_back_when_interface_is_missing(self):
        self.assertEqual(monitor.probe_peer('rdf-no-such-interface','127.0.0.1'),'ERROR')
    def test_ppp_monitor_detects_point_to_point_peer(self):
        host=monitor.Monitor(load_config())
        ip_json=json.dumps([{'ifname':'ppp0','flags':['POINTOPOINT','MULTICAST','NOARP','UP','LOWER_UP'],
                             'addr_info':[{'family':'inet','local':'10.90.0.2','address':'10.90.0.1','prefixlen':32}]}])
        def command(args,timeout=2):
            return ip_json if args==['ip','-j','addr','show'] else None
        with mock.patch.object(monitor,'run',side_effect=command):
            host.probe(2)
        state=host.snapshot()
        self.assertEqual(state['ppp'],'UP')
        self.assertEqual(state['interface'],'ppp0')

class ConfigTests(unittest.TestCase):
    def test_default_config(self):
        c=load_config()
        self.assertEqual(c['api']['port'],8790)
        self.assertFalse(c['control']['shutdown_enabled'])
        self.assertFalse(c['control']['ppp_restart_enabled'])
    def test_ppp_restart_requires_controlled_mode(self):
        c=load_config();c['control']['ppp_restart_enabled']=True
        with self.assertRaisesRegex(ValueError,'MUTATION_REQUIRES_CONTROLLED_MODE'):validate_config(c)
    def test_shutdown_requires_controlled_mode(self):
        c=load_config();c['control']['shutdown_enabled']=True
        with self.assertRaisesRegex(ValueError,'MUTATION_REQUIRES_CONTROLLED_MODE'):validate_config(c)

    def test_enabled_tls_can_use_system_ca_trust(self):
        c=load_config()
        c['mqtt'].update(enabled=True,control_credentials_file='/etc/rdf-node/control.json',
                         bulk_credentials_file='/etc/rdf-node/bulk.json')
        validate_config(c)

    def test_mqtt_transport_and_websocket_path_are_validated(self):
        c=load_config();c['mqtt']['transport']='udp'
        with self.assertRaises(ValueError):validate_config(c)
        c=load_config();c['mqtt']['websocket_path']='/mqtt\r\nHost:attacker'
        with self.assertRaises(ValueError):validate_config(c)
    def test_display_accent_and_font_are_allowlisted(self):
        for key,value in (('accent','ultraviolet'),('font','comic-sans')):
            c=load_config();c['display'][key]=value
            with self.assertRaises(ValueError):validate_config(c)
    def test_plaintext_remote_transport_can_use_tcp_or_websocket(self):
        for transport in ('tcp','websocket'):
            c=load_config()
            c['mqtt'].update(enabled=True,host='10.90.0.1',tls=False,transport=transport,
                             control_credentials_file='/etc/rdf-node/control.json',
                             bulk_credentials_file='/etc/rdf-node/bulk.json')
            validate_config(c)
    def test_plaintext_loopback_requires_explicit_test_opt_in(self):
        c=load_config();c['mqtt'].update(tls=False,host='127.0.0.1')
        with self.assertRaises(ValueError):validate_config(c)
        c['mqtt']['allow_insecure_loopback']=True
        validate_config(c)
    def test_unknown_yaml_key_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'c.yaml';p.write_text('dangerous_new_key: true\n')
            with self.assertRaises(ValueError):load_config(p)
    def test_legacy_ground_receipt_setting_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'c.yaml'
            p.write_text('telemetry:\n  require_ground_receipt_for_bulk: true\n')
            with self.assertRaisesRegex(ValueError,'telemetry.require_ground_receipt_for_bulk: UNKNOWN_CONFIG_KEY'):
                load_config(p)
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
    def test_doa_xml_keeps_source_units_and_time(self):
        raw=b'<DATA><TIME>1791169415361</TIME><FREQUENCY>137.0</FREQUENCY><DOA>200.0</DOA></DATA>'
        self.assertEqual(parse_doa_xml(raw),{'source_timestamp_ms':1791169415361,
                         'raw_doa_deg':200.0,'frequency_mhz':137.0})
    def test_doa_xml_rejects_duplicate_fields_and_entities(self):
        documents=(b'<DATA><TIME>1791169415361</TIME><TIME>1791169415362</TIME><FREQUENCY>137</FREQUENCY><DOA>200</DOA></DATA>',
                   b'<!DOCTYPE DATA [<!ENTITY angle "200">]><DATA><TIME>1791169415361</TIME><FREQUENCY>137</FREQUENCY><DOA>&angle;</DOA></DATA>')
        for document in documents:
            with self.subTest(document=document):
                with self.assertRaises(ValueError):parse_doa_xml(document)


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
    def test_raw_settings_preserve_exact_successful_file_bytes(self):
        raw=b' \n'+compact(settings())+b'\n'
        (self.path/'settings.json').write_bytes(raw)
        self.src.poll(force=True)
        self.assertEqual(self.src.raw_settings,raw)
        self.assertGreater(self.src.settings_seen,0)
        (self.path/'settings.json').unlink()
        self.src.poll(force=True)
        self.assertEqual(self.src.raw_settings,raw)
        self.assertIsNotNone(self.src.config_error)

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
    def test_xml_diagnostic_survives_empty_csv_without_authorizing_detection(self):
        raw=b'<DATA><TIME>1791169415361</TIME><FREQUENCY>137.0</FREQUENCY><DOA>200.0</DOA></DATA>'
        (self.path/'doa.xml').write_bytes(raw);(self.path/'DOA_value.html').write_bytes(b'')
        self.src.poll(force=True)
        detection=self.src.view(True)
        diagnostic=self.src.diagnostic_view(detection['reasons'])
        self.assertFalse(detection['valid']);self.assertIsNone(detection['relative_doa_deg'])
        self.assertTrue(diagnostic['available']);self.assertEqual(diagnostic['raw_doa_deg'],200)
        self.assertEqual(diagnostic['frequency_mhz'],137)
        self.assertIn('DIAGNOSTIC_UNVERIFIED',diagnostic['validation_reasons'])
        self.assertIn('EMPTY_CSV',diagnostic['validation_reasons'])
        self.assertGreater(diagnostic['observed_timestamp_ms'],0)
    def test_missing_xml_does_not_block_valid_csv(self):
        self.src.poll(force=True);self.write(2);self.src.poll(force=True)
        detection=self.src.view(True)
        self.assertTrue(detection['valid'])
        self.assertFalse(self.src.diagnostic_view(detection['reasons'])['available'])


    def test_config_change_does_not_relabel_old_record(self):
        self.src.poll(force=True);oldrev=self.src.record['observed_revision'];s=settings();s['uniform_gain']=20.7
        (self.path/'settings.json').write_bytes(compact(s));self.src.poll(force=True)
        self.assertNotEqual(self.src.revision,oldrev);self.assertEqual(self.src.record['observed_revision'],oldrev);self.assertFalse(self.src.view(True)['config_attributed'])

class CodecTests(unittest.TestCase):
    def frame(self,values=None,**updates):
        payload={'v':2,'encoding':'json','sid':'00001234','q':8,'timestamp_ms':now_ms(),
                 'frequency_hz':433920000,'revision':4,'vfo':0,'convention':1,'flags':63,
                 'raw_doa_deg':10.0,'confidence_native_db':8.27,
                 'values':[-20+math.sin(i/15)*5 for i in range(360)]}
        if values is not None: payload['values']=values
        payload.update(updates)
        return compact(payload)
    def test_json_round_trip_preserves_all_samples_and_metadata(self):
        values=[-30+i/17 for i in range(360)]
        payload=self.frame(values);expected=json.loads(payload);result=decode(payload)
        self.assertEqual(result['values'],values)
        self.assertEqual((result['sid'],result['q'],result['flags']),('00001234',8,63))
        self.assertEqual((result['timestamp_ms'],result['frequency_hz'],result['revision']),
                         (expected['timestamp_ms'],433920000,4))
        self.assertEqual(result['encoding'],'json')
        self.assertEqual(result['peak_index'],max(range(360),key=values.__getitem__))
    def test_wrong_sample_count_rejected(self):
        with self.assertRaises(ValueError): decode(self.frame([1]*359))
    def test_out_of_range_sample_rejected(self):
        values=[0]*360;values[5]=1e100
        with self.assertRaises(ValueError): decode(self.frame(values))
    def test_unknown_flags_rejected(self):
        with self.assertRaises(ValueError): decode(self.frame(flags=64))
    def test_non_json_frame_rejected(self):
        with self.assertRaises(ValueError): decode(b'RDF2'+b'\0'*100)

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
    def test_shutdown_schedule_never_implies_success_after_restart(self):
        self.j.accept({'id':'shutdown','op':'system.shutdown.execute','boot':'old'},'test')
        self.j.update('shutdown','SHUTDOWN_SCHEDULED')
        self.j.recover('new')
        self.assertEqual(self.j.lookup('shutdown')['stage'],'OUTCOME_UNKNOWN')
    def test_revision_stable(self):self.assertEqual(self.j.config_revision('a'),self.j.config_revision('a'));self.assertEqual(self.j.config_revision('b'),2)

class LifecycleSetupTests(unittest.TestCase):
    def test_setup_discovers_rdfsdr_for_panel_lifecycle_actions(self):
        from types import SimpleNamespace
        from rdf_node import cli
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config.yaml';config.write_text('{}')
            args=SimpleNamespace(config=str(config),share_dir='',grant_read=False,engine_unit=None,
                                 verify_source=False,yes=False)
            with (
                mock.patch.object(cli,'require_root'),
                mock.patch.object(cli,'discover_sources',return_value=[]),
                mock.patch('builtins.input',return_value=''),
                mock.patch.object(cli.subprocess,'run',return_value=mock.Mock(stdout='loaded\n')) as probe,
                mock.patch.object(cli,'chown_config')
            ):
                cli.setup(args)
            cfg=load_config(config)
            self.assertEqual(cfg['link']['engine_service'],'rdfsdr.service')
            self.assertEqual(probe.call_args.args[0],
                             ['systemctl','show','rdfsdr.service','-p','LoadState','--value'])
            policy=dict(DEFAULT_POLICY,state_dir=str(root/'helper'),allowed_user=None,
                        allow_lifecycle=True,lifecycle_audited=True,
                        engine_service=cfg['link']['engine_service'])
            controller=Controller(policy)
            actions=(('processing.set','RUNNING'),('processing.set','STOPPED'),('service.restart',None))
            with mock.patch.object(controller,'maintenance',return_value=True), \
                    mock.patch.object(controller,'_run') as run:
                for op,desired in actions:
                    request={'op':op,'origin':'local-admin'}
                    if desired is not None:request['desired']=desired
                    controller.dispatch(request,os.getuid())
            self.assertEqual([call.args[0] for call in run.call_args_list],[
                ['/usr/bin/systemctl','--no-block','start','rdfsdr.service'],
                ['/usr/bin/systemctl','--no-block','stop','rdfsdr.service'],
                ['/usr/bin/systemctl','--no-block','restart','rdfsdr.service']])
class HelperTests(unittest.TestCase):
    def setUp(self):
        import pwd
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(os.path.realpath(self.tmp.name))
        self.p=dict(DEFAULT_POLICY);self.p.update(state_dir=str(self.path/'state'),allowed_user=pwd.getpwuid(os.getuid()).pw_name,engine_service='test-sdr.service')
        self.ctrl=Controller(self.p)
    def test_arbitrary_rpc_denied(self):
        with self.assertRaises(ValueError):self.ctrl.dispatch({'op':'shell','command':'anything'},os.getuid())
    def test_wrong_peer_uid_denied(self):
        with self.assertRaises(ValueError):self.ctrl.dispatch({'op':'status'},999999)
    def test_unknown_rpc_argument_denied(self):
        with self.assertRaises(ValueError):self.ctrl.dispatch({'op':'status','unit':'ssh.service'},os.getuid())
    def test_lifecycle_disabled(self):
        with self.assertRaises(ValueError):self.ctrl.dispatch({'op':'processing.set','desired':'STOPPED','origin':'local-admin'},os.getuid())
    def test_reboot_disabled(self):
        with self.assertRaisesRegex(ValueError,'REBOOT_DISABLED'):
            self.ctrl.dispatch({'op':'system.reboot.prepare','id':'x','origin':'local-admin'},os.getuid())
    def test_local_reboot_still_requires_maintenance(self):
        self.p['allow_reboot']=True
        with mock.patch.object(self.ctrl,'maintenance',return_value=False):
            with self.assertRaisesRegex(ValueError,'MAINTENANCE_REQUIRED'):
                self.ctrl.dispatch({'op':'system.reboot.prepare','id':'x','origin':'local-admin'},os.getuid())
    def test_ground_reboot_needs_no_root_grants_or_lease(self):
        with mock.patch.object(self.ctrl,'_run') as run:
            prepared=self.ctrl.dispatch({'op':'system.reboot.prepare','id':'remote-reboot','origin':'ground-controller'},os.getuid())
            result=self.ctrl.dispatch({'op':'system.reboot.execute','id':'remote-reboot','challenge':prepared['challenge'],'origin':'ground-controller'},os.getuid())
        self.assertEqual((result['scheduled'],result['action']),(True,'reboot'))
        run.assert_called_once()
    def test_ground_shutdown_needs_no_root_grants_or_lease(self):
        with mock.patch.object(self.ctrl,'_run') as run:
            prepared=self.ctrl.dispatch({'op':'system.shutdown.prepare','id':'remote-shutdown','origin':'ground-controller'},os.getuid())
            result=self.ctrl.dispatch({'op':'system.shutdown.execute','id':'remote-shutdown','challenge':prepared['challenge'],'origin':'ground-controller'},os.getuid())
        self.assertEqual((result['scheduled'],result['action']),(True,'shutdown'))
        run.assert_called_once()
    def test_shutdown_disabled(self):
        with self.assertRaisesRegex(ValueError,'SHUTDOWN_DISABLED'):
            self.ctrl.dispatch({'op':'system.shutdown.prepare','id':'x','origin':'local-admin'},os.getuid())
    def test_shutdown_requires_maintenance(self):
        self.p['allow_shutdown']=True
        with self.assertRaisesRegex(ValueError,'MAINTENANCE_REQUIRED'):
            self.ctrl.dispatch({'op':'system.shutdown.prepare','id':'x','origin':'local-admin'},os.getuid())
    def test_ground_config_patch_uses_fixed_target_without_root_permission(self):
        import hashlib
        path=self.path/'settings.json';raw=compact(settings());path.write_bytes(raw)
        self.p.update(settings_path=str(path),single_writer_confirmed=True,allow_config=False)
        request={'op':'config.patch','origin':'ground-controller','changes':{'gain_db':20.7},
                 'expected_digest':hashlib.sha256(raw).hexdigest()}
        result=self.ctrl.dispatch(request,os.getuid())
        self.assertTrue(result['persisted'])
        self.assertEqual(json.loads(path.read_bytes())['uniform_gain'],20.7)
    def test_local_config_patch_still_needs_root_permission(self):
        import hashlib
        path=self.path/'settings.json';raw=compact(settings());path.write_bytes(raw)
        self.p.update(settings_path=str(path),single_writer_confirmed=True,allow_config=False)
        request={'op':'config.patch','origin':'local-admin','changes':{'gain_db':20.7},
                 'expected_digest':hashlib.sha256(raw).hexdigest()}
        with self.assertRaisesRegex(ValueError,'CONFIG_ADAPTER_NOT_APPROVED'):
            self.ctrl.dispatch(request,os.getuid())
    def test_local_lifecycle_still_requires_maintenance_when_remote_enabled(self):
        self.p.update(allow_remote_control=True,allow_lifecycle=True,lifecycle_audited=True)
        request={'op':'processing.set','desired':'STOPPED','origin':'local-admin'}
        with mock.patch.object(self.ctrl,'maintenance',return_value=False):
            with self.assertRaisesRegex(ValueError,'MAINTENANCE_REQUIRED'):
                self.ctrl.dispatch(request,os.getuid())
    def test_ground_lifecycle_uses_audited_fixed_target_without_root_permission(self):
        self.p.update(allow_remote_control=False,allow_lifecycle=False,lifecycle_audited=True)
        request={'op':'processing.set','desired':'STOPPED','origin':'ground-controller'}
        with mock.patch.object(self.ctrl,'maintenance',return_value=False) as maintenance, \
                mock.patch.object(self.ctrl,'_run') as run:
            result=self.ctrl.dispatch(request,os.getuid())
        self.assertEqual(json.loads((self.ctrl.state/'intent.json').read_bytes())['origin'],'ground-controller')
        self.assertEqual(result['requested'],'STOPPED')
        maintenance.assert_not_called()
        run.assert_called_once_with(['/usr/bin/systemctl','--no-block','stop','test-sdr.service'])
    def test_ground_lifecycle_intent_recovers_without_local_approval(self):
        self.p.update(allow_lifecycle=False,lifecycle_audited=True)
        (self.ctrl.state/'intent.json').write_bytes(compact({'desired':'STOPPED','origin':'ground-controller'}))
        with mock.patch.object(self.ctrl,'_run') as run:
            self.ctrl.reconcile_intent()
        run.assert_called_once_with(['/usr/bin/systemctl','--no-block','stop','test-sdr.service'])
    def test_local_lifecycle_intent_stays_gated_without_local_approval(self):
        self.p.update(allow_lifecycle=False,lifecycle_audited=True)
        (self.ctrl.state/'intent.json').write_bytes(compact({'desired':'STOPPED','origin':'local-admin'}))
        with mock.patch.object(self.ctrl,'_run') as run:
            self.ctrl.reconcile_intent()
        run.assert_not_called()
    def test_ppp_restart_rejects_invalid_rpc_shape(self):
        self.p.update(allow_ppp_restart=True,allow_remote_control=True)
        requests=([],{'op':'wrong','origin':'ground-controller'},{'op':'ppp.restart'},
                  {'op':'ppp.restart','origin':'ground-controller','service':'other.service'})
        with mock.patch('rdf_node.helper.subprocess.run') as status, \
                mock.patch.object(self.ctrl,'_run') as restart:
            for request in requests:
                with self.assertRaises(ValueError):
                    self.ctrl.dispatch(request,os.getuid())
        status.assert_not_called()
        restart.assert_not_called()
    def test_ppp_restart_requires_active_idle_unit(self):
        request={'op':'ppp.restart','origin':'ground-controller'}
        for result,error in (
                (mock.Mock(returncode=0,stdout=os.linesep.join(('LoadState=not-found','ActiveState=inactive','Job=0'))),
                 'PPP_SERVICE_NOT_LOADED'),
                (mock.Mock(returncode=0,stdout=os.linesep.join(('LoadState=loaded','ActiveState=inactive','Job=0'))),
                 'PPP_SERVICE_NOT_ACTIVE'),
                (mock.Mock(returncode=0,stdout=os.linesep.join(('LoadState=loaded','ActiveState=active','Job=17'))),
                 'PPP_RESTART_PENDING'),
                (mock.Mock(returncode=0,stdout=os.linesep.join(('LoadState=loaded','ActiveState=active'))),
                 'PPP_SERVICE_STATUS_UNKNOWN'),
                (mock.Mock(returncode=1,stdout=''),'PPP_SERVICE_STATUS_UNKNOWN')):
            with mock.patch('rdf_node.helper.subprocess.run',return_value=result), \
                    mock.patch.object(self.ctrl,'_run') as restart:
                with self.assertRaisesRegex(ValueError,error):
                    self.ctrl.dispatch(request,os.getuid())
            restart.assert_not_called()
    def test_ground_ppp_restart_needs_no_root_permission_but_checks_unit(self):
        request={'op':'ppp.restart','origin':'ground-controller'}
        status=mock.Mock(returncode=0,stdout=os.linesep.join(('LoadState=loaded','ActiveState=active','Job=0')))
        with mock.patch('rdf_node.helper.subprocess.run',return_value=status) as show, \
                mock.patch.object(self.ctrl,'_run') as restart:
            result=self.ctrl.dispatch(request,os.getuid())
        self.assertEqual(result,{'requested':True,'service':'t900-ppp.service'})
        show.assert_called_once()
        restart.assert_called_once_with(['/usr/bin/systemctl','--no-block','restart','t900-ppp.service'])
    def test_local_ppp_restart_still_needs_approval_and_lease(self):
        self.p['allow_ppp_restart']=True
        with mock.patch.object(self.ctrl,'maintenance',return_value=False):
            with self.assertRaisesRegex(ValueError,'MAINTENANCE_REQUIRED'):
                self.ctrl.dispatch({'op':'ppp.restart','origin':'local-admin'},os.getuid())
    def test_ppp_restart_action_failure_is_outcome_unknown(self):
        self.p.update(allow_ppp_restart=True,allow_remote_control=True)
        request={'op':'ppp.restart','origin':'ground-controller'}
        status=mock.Mock(returncode=0,stdout=os.linesep.join(('LoadState=loaded','ActiveState=active','Job=0')))
        with mock.patch('rdf_node.helper.subprocess.run',return_value=status), \
                mock.patch.object(self.ctrl,'_run',side_effect=HelperError('SYSTEMD_ACTION_FAILED')):
            with self.assertRaisesRegex(TimeoutError,'SYSTEMD_ACTION_OUTCOME_UNKNOWN'):
                self.ctrl.dispatch(request,os.getuid())

    def test_ppp_restart_requests_only_fixed_unit(self):
        self.p.update(allow_ppp_restart=True,allow_remote_control=True)
        request={'op':'ppp.restart','origin':'ground-controller'}
        status=mock.Mock(returncode=0,stdout=os.linesep.join(('LoadState=loaded','ActiveState=active','Job=0')))
        with mock.patch('rdf_node.helper.subprocess.run',return_value=status) as show, \
                mock.patch.object(self.ctrl,'_run') as restart:
            result=self.ctrl.dispatch(request,os.getuid())
        self.assertEqual(result,{'requested':True,'service':'t900-ppp.service'})
        self.assertEqual(show.call_args.args[0],
                         ['/usr/bin/systemctl','show','--no-pager',
                          '--property=LoadState,ActiveState,Job','t900-ppp.service'])
        restart.assert_called_once_with(
            ['/usr/bin/systemctl','--no-block','restart','t900-ppp.service'])

    def test_concurrent_ppp_restart_requests_are_serialized(self):
        import threading
        self.p.update(allow_ppp_restart=True,allow_remote_control=True)
        request={'op':'ppp.restart','origin':'ground-controller'}
        status_calls=[];restart_calls=[];pending={'value':False}
        restart_entered=threading.Event();release_restart=threading.Event()

        class ObservedLock:
            def __init__(self):
                self.lock=threading.Lock();self.second_waiting=threading.Event()
            def __enter__(self):
                if threading.current_thread().name=='ppp-second': self.second_waiting.set()
                self.lock.acquire()
                return self
            def __exit__(self,*_):
                self.lock.release()

        def systemctl_show(args,**kwargs):
            status_calls.append(args)
            job='17' if pending['value'] else '0'
            return mock.Mock(returncode=0,stdout=os.linesep.join(('LoadState=loaded','ActiveState=active',f'Job={job}')))

        def systemctl_restart(args):
            restart_calls.append(args)
            restart_entered.set()
            if not release_restart.wait(2): raise TimeoutError('test restart release timed out')
            pending['value']=True

        lock=ObservedLock();self.ctrl.lock=lock;outcomes={}
        def dispatch(name):
            try: outcomes[name]=self.ctrl.dispatch(dict(request),os.getuid())
            except Exception as error: outcomes[name]=error

        first=threading.Thread(target=dispatch,args=('first',),name='ppp-first')
        second=threading.Thread(target=dispatch,args=('second',),name='ppp-second')
        with mock.patch('rdf_node.helper.subprocess.run',side_effect=systemctl_show), \
                mock.patch.object(self.ctrl,'_run',side_effect=systemctl_restart):
            first.start()
            self.assertTrue(restart_entered.wait(2))
            second.start()
            try:
                self.assertTrue(lock.second_waiting.wait(2))
                self.assertEqual(len(status_calls),1)
            finally:
                release_restart.set()
                first.join(2);second.join(2)
        self.assertFalse(first.is_alive());self.assertFalse(second.is_alive())
        self.assertEqual(outcomes['first']['service'],'t900-ppp.service')
        self.assertEqual(str(outcomes['second']),'PPP_RESTART_PENDING')
        self.assertEqual(len(restart_calls),1)


    def test_helper_schedule_timeout_maps_to_unknown(self):
        from rdf_node.helper import call_helper
        connection=mock.MagicMock()
        connection.__enter__.return_value.recv.return_value=compact({'ok':False,'error':'SYSTEMD_ACTION_OUTCOME_UNKNOWN'})+b'\n'
        with mock.patch('rdf_node.helper.socket.socket',return_value=connection):
            with self.assertRaises(TimeoutError):call_helper('/tmp/control.sock',{'op':'status'})
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
            r=self.ctrl.dispatch({'op':'processing.set','desired':'STOPPED','origin':'local-admin'},os.getuid())
        self.assertTrue((self.path/'state/engine.stopped').exists());self.assertEqual(r['requested'],'STOPPED');run.assert_called_once()
    def test_reboot_challenge_consumed_once_no_real_reboot(self):
        self.p['allow_reboot']=True
        with mock.patch.object(self.ctrl,'maintenance',return_value=True),mock.patch.object(self.ctrl,'_run'):
            r=self.ctrl.dispatch({'op':'system.reboot.prepare','id':'p','origin':'local-admin'},os.getuid())
            e={'op':'system.reboot.execute','id':'p','challenge':r['challenge'],'origin':'local-admin'}
            self.assertTrue(self.ctrl.dispatch(e,os.getuid())['scheduled'])
            with self.assertRaises(ValueError):self.ctrl.dispatch(e,os.getuid())
    def test_shutdown_challenge_is_action_bound_and_one_use(self):
        self.p.update(allow_reboot=True,allow_shutdown=True)
        with mock.patch.object(self.ctrl,'maintenance',return_value=True),mock.patch.object(self.ctrl,'_run') as run:
            reboot=self.ctrl.dispatch({'op':'system.reboot.prepare','id':'reboot','origin':'local-admin'},os.getuid())
            with self.assertRaisesRegex(ValueError,'SHUTDOWN_CHALLENGE_INVALID'):
                self.ctrl.dispatch({'op':'system.shutdown.execute','id':'reboot','challenge':reboot['challenge'],'origin':'local-admin'},os.getuid())
            prepared=self.ctrl.dispatch({'op':'system.shutdown.prepare','id':'shutdown','origin':'local-admin'},os.getuid())
            request={'op':'system.shutdown.execute','id':'shutdown','challenge':prepared['challenge'],'origin':'local-admin'}
            result=self.ctrl.dispatch(request,os.getuid())
            self.assertTrue(result['scheduled'])
            self.assertEqual(result['delay_seconds'],5)
            intent=json.loads((self.path/'state/shutdown-intent.json').read_text())
            self.assertEqual((intent['id'],intent['boot']),('shutdown',self.ctrl.boot))
            run.assert_called_once()
            args=run.call_args.args[0]
            self.assertEqual(args[:2],['/usr/bin/systemd-run','--quiet'])
            self.assertTrue(args[2].startswith('--unit=rdf-node-shutdown-'))
            self.assertEqual(intent['unit'],args[2].split('=',1)[1])
            self.assertEqual(args[3:],['--on-active=5s','/usr/bin/systemctl','poweroff'])
            with self.assertRaisesRegex(ValueError,'SHUTDOWN_CHALLENGE_INVALID'):
                self.ctrl.dispatch(request,os.getuid())
    def test_shutdown_intent_blocks_second_schedule_for_current_boot(self):
        self.p['allow_shutdown']=True
        with mock.patch.object(self.ctrl,'maintenance',return_value=True),mock.patch.object(self.ctrl,'_run') as run:
            first=self.ctrl.dispatch({'op':'system.shutdown.prepare','id':'first','origin':'local-admin'},os.getuid())
            second=self.ctrl.dispatch({'op':'system.shutdown.prepare','id':'second','origin':'local-admin'},os.getuid())
            self.ctrl.dispatch({'op':'system.shutdown.execute','id':'first','challenge':first['challenge'],'origin':'local-admin'},os.getuid())
            with self.assertRaisesRegex(ValueError,'SHUTDOWN_ALREADY_SCHEDULED'):
                self.ctrl.dispatch({'op':'system.shutdown.execute','id':'second','challenge':second['challenge'],'origin':'local-admin'},os.getuid())
            with self.assertRaisesRegex(ValueError,'SHUTDOWN_ALREADY_SCHEDULED'):
                self.ctrl.dispatch({'op':'system.shutdown.prepare','id':'third','origin':'local-admin'},os.getuid())
        run.assert_called_once()
    def test_shutdown_intent_from_prior_boot_allows_new_schedule(self):
        self.p['allow_shutdown']=True;self.ctrl.boot='boot-current'
        self.ctrl.state.joinpath('shutdown-intent.json').write_bytes(compact({'id':'old','boot':'boot-previous'}))
        with mock.patch.object(self.ctrl,'maintenance',return_value=True),mock.patch.object(self.ctrl,'_run') as run:
            prepared=self.ctrl.dispatch({'op':'system.shutdown.prepare','id':'new','origin':'local-admin'},os.getuid())
            result=self.ctrl.dispatch({'op':'system.shutdown.execute','id':'new','challenge':prepared['challenge'],'origin':'local-admin'},os.getuid())
        self.assertTrue(result['scheduled']);run.assert_called_once()
        self.assertEqual(json.loads((self.ctrl.state/'shutdown-intent.json').read_text())['boot'],'boot-current')
    def test_malformed_shutdown_intent_blocks_prepare(self):
        self.p['allow_shutdown']=True;(self.ctrl.state/'shutdown-intent.json').write_text('{broken')
        with mock.patch.object(self.ctrl,'maintenance',return_value=True):
            with self.assertRaisesRegex(ValueError,'SHUTDOWN_ALREADY_SCHEDULED'):
                self.ctrl.dispatch({'op':'system.shutdown.prepare','id':'new','origin':'local-admin'},os.getuid())
    def test_prepare_preserves_other_live_challenges(self):
        self.p.update(allow_reboot=True,allow_shutdown=True)
        with mock.patch.object(self.ctrl,'maintenance',return_value=True),mock.patch.object(self.ctrl,'_run') as run:
            shutdown=self.ctrl.dispatch({'op':'system.shutdown.prepare','id':'shutdown','origin':'local-admin'},os.getuid())
            reboot=self.ctrl.dispatch({'op':'system.reboot.prepare','id':'reboot','origin':'local-admin'},os.getuid())
            for op,id,challenge in (
                    ('system.shutdown.execute','shutdown',shutdown['challenge']),
                    ('system.reboot.execute','reboot',reboot['challenge'])):
                result=self.ctrl.dispatch({'op':op,'id':id,'challenge':challenge,'origin':'local-admin'},os.getuid())
                self.assertTrue(result['scheduled'])
            self.assertEqual(run.call_count,2)
    def test_pending_challenges_are_bounded(self):
        self.p['allow_shutdown']=True
        with mock.patch.object(self.ctrl,'maintenance',return_value=True):
            for index in range(32):
                self.ctrl.dispatch({'op':'system.shutdown.prepare','id':f'shutdown-{index}','origin':'local-admin'},os.getuid())
            with self.assertRaisesRegex(ValueError,'TOO_MANY_ACTIVE_CHALLENGES'):
                self.ctrl.dispatch({'op':'system.shutdown.prepare','id':'shutdown-overflow','origin':'local-admin'},os.getuid())
    def test_systemd_schedule_failure_is_outcome_unknown(self):
        self.p['allow_shutdown']=True
        with mock.patch.object(self.ctrl,'maintenance',return_value=True),mock.patch.object(
                self.ctrl,'_run',side_effect=HelperError('SYSTEMD_ACTION_FAILED')):
            prepared=self.ctrl.dispatch({'op':'system.shutdown.prepare','id':'shutdown','origin':'local-admin'},os.getuid())
            with self.assertRaisesRegex(TimeoutError,'SYSTEMD_ACTION_OUTCOME_UNKNOWN'):
                self.ctrl.dispatch({'op':'system.shutdown.execute','id':'shutdown','challenge':prepared['challenge'],'origin':'local-admin'},os.getuid())
        self.assertTrue((self.path/'state/shutdown-intent.json').exists())
    def test_shutdown_reconcile_requires_local_root(self):
        with self.assertRaisesRegex(ValueError,'SHUTDOWN_RECONCILE_REQUIRES_ROOT'):
            self.ctrl.dispatch({'op':'system.shutdown.reconcile'},self.ctrl.uid)
    def test_shutdown_reconcile_clears_only_inactive_fixed_units(self):
        unit='rdf-node-shutdown-0123456789'
        path=self.ctrl.state/'shutdown-intent.json'
        path.write_bytes(compact({'id':'shutdown','boot':self.ctrl.boot,'unit':unit}))
        inactive=mock.Mock(returncode=0,stdout='LoadState=not-found\nActiveState=inactive\n')
        with mock.patch('rdf_node.helper.subprocess.run',side_effect=(inactive,inactive)) as run:
            result=self.ctrl.dispatch({'op':'system.shutdown.reconcile'},0)
        self.assertTrue(result['cleared']);self.assertFalse(path.exists())
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [['/usr/bin/systemctl','show','--no-pager','--property=LoadState,ActiveState',unit+'.timer'],
                          ['/usr/bin/systemctl','show','--no-pager','--property=LoadState,ActiveState',unit+'.service']])
    def test_shutdown_reconcile_keeps_marker_when_timer_is_active(self):
        path=self.ctrl.state/'shutdown-intent.json'
        path.write_bytes(compact({'id':'shutdown','boot':self.ctrl.boot,'unit':'rdf-node-shutdown-0123456789'}))
        active=mock.Mock(returncode=0,stdout='LoadState=loaded\nActiveState=active\n')
        with mock.patch('rdf_node.helper.subprocess.run',return_value=active):
            with self.assertRaisesRegex(ValueError,'SHUTDOWN_UNIT_ACTIVE'):
                self.ctrl.dispatch({'op':'system.shutdown.reconcile'},0)
        self.assertTrue(path.exists())
    def test_shutdown_reconcile_keeps_marker_when_systemctl_cannot_report(self):
        path=self.ctrl.state/'shutdown-intent.json'
        path.write_bytes(compact({'id':'shutdown','boot':self.ctrl.boot,'unit':'rdf-node-shutdown-0123456789'}))
        with mock.patch('rdf_node.helper.subprocess.run',return_value=mock.Mock(returncode=1,stdout='')):
            with self.assertRaisesRegex(ValueError,'SHUTDOWN_UNIT_STATUS_UNKNOWN'):
                self.ctrl.dispatch({'op':'system.shutdown.reconcile'},0)
        self.assertTrue(path.exists())
    def test_shutdown_challenge_expires_after_30_seconds(self):
        self.p['allow_shutdown']=True
        with mock.patch.object(self.ctrl,'maintenance',return_value=True),mock.patch('rdf_node.helper.time.monotonic',side_effect=(100,131)):
            prepared=self.ctrl.dispatch({'op':'system.shutdown.prepare','id':'shutdown','origin':'local-admin'},os.getuid())
            with self.assertRaisesRegex(ValueError,'SHUTDOWN_CHALLENGE_INVALID'):
                self.ctrl.dispatch({'op':'system.shutdown.execute','id':'shutdown','challenge':prepared['challenge'],'origin':'local-admin'},os.getuid())

class ControlApprovalTests(unittest.TestCase):
    def test_root_approvals_only_configure_local_operation_gates(self):
        from types import SimpleNamespace
        from rdf_node import cli

        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config.yaml';policy_path=root/'helper.yaml'
            config.write_text(cli.yaml.safe_dump(load_config(),sort_keys=False))
            policy_path.write_text(cli.yaml.safe_dump({'allow_remote_control':False},sort_keys=False))
            real_path=Path

            def mapped_path(value):
                if str(value)=='/etc/rdf-node/helper.yaml': return policy_path
                return real_path(value)

            args=SimpleNamespace(action='approve',config=str(config),settings=False,lifecycle=False,
                                 reboot=True,shutdown=True,ppp_restart=False)
            with mock.patch.object(cli,'require_root'),mock.patch.object(cli,'chown_config'), \
                    mock.patch.object(cli,'Path',side_effect=mapped_path), \
                    mock.patch.object(cli.subprocess,'run'), \
                    mock.patch('builtins.input',side_effect=('APPROVE','REBOOT','SHUTDOWN')):
                cli.controls(args)

            saved=cli.yaml.safe_load(policy_path.read_text())
            approved=load_config(config)
            self.assertTrue(saved['allow_reboot'])
            self.assertTrue(saved['allow_shutdown'])
            self.assertFalse(saved['allow_remote_control'])
            self.assertTrue(approved['control']['reboot_enabled'])
            self.assertTrue(approved['control']['shutdown_enabled'])
            self.assertFalse(approved['control']['remote_commands_enabled'])
    def test_remote_grant_removed_but_local_reboot_approval_remains(self):
        import sys
        from rdf_node import cli

        with mock.patch.object(sys,'argv',['rdf-node','controls','approve','--remote']):
            with self.assertRaises(SystemExit):
                cli.main()
        with mock.patch.object(cli,'controls') as parsed, \
                mock.patch.object(sys,'argv',['rdf-node','controls','approve','--reboot']):
            cli.main()
        self.assertTrue(parsed.call_args.args[0].reboot)

    def test_ppp_restart_requires_root_unit_approval(self):
        import sys
        from types import SimpleNamespace
        from rdf_node import cli

        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config.yaml';policy_path=root/'helper.yaml'
            config.write_text(cli.yaml.safe_dump(load_config(),sort_keys=False))
            real_path=Path

            def mapped_path(value):
                if str(value)=='/etc/rdf-node/helper.yaml': return policy_path
                return real_path(value)

            with mock.patch.object(cli,'controls') as parsed, \
                    mock.patch.object(sys,'argv',['rdf-node','controls','approve','--config',str(config),
                                                  '--ppp-restart']):
                cli.main()
            self.assertTrue(parsed.call_args.args[0].ppp_restart)

            args=SimpleNamespace(action='approve',config=str(config),settings=False,lifecycle=False,
                                 reboot=False,shutdown=False,ppp_restart=True)
            status=mock.Mock(returncode=0,stdout=os.linesep.join(('LoadState=loaded','ActiveState=active','Job=0')))
            with mock.patch.object(cli,'require_root'),mock.patch.object(cli,'chown_config'), \
                    mock.patch.object(cli,'Path',side_effect=mapped_path), \
                    mock.patch('rdf_node.helper.subprocess.run',return_value=status) as systemctl, \
                    mock.patch('builtins.input',side_effect=('APPROVE','T900 PPP RESTART')):
                cli.controls(args)

            saved=cli.yaml.safe_load(policy_path.read_text())
            approved=load_config(config)
            self.assertTrue(saved['allow_ppp_restart'])
            self.assertFalse(saved['allow_remote_control'])
            self.assertTrue(approved['control']['ppp_restart_enabled'])
            self.assertEqual(systemctl.call_args_list[0].args[0],
                             ['/usr/bin/systemctl','show','--no-pager',
                              '--property=LoadState,ActiveState,Job','t900-ppp.service'])

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
    def test_discard_removes_only_matching_queued_message(self):
        q=Outbox();errors=[]
        q.offer(Message('diagnostic-angular','diag',b'candidate',0,False,3,3,0,on_error=errors.append))
        q.offer(Message('health','health',b'live',0,False,3,1,0))
        self.assertTrue(q.discard('diagnostic-angular','PROFILE_CONTROL'))
        self.assertEqual(q.status()['depth'],1)
        self.assertEqual(q.pop(now=1).key,'health')
        self.assertEqual(errors,['PROFILE_CONTROL'])
        self.assertFalse(q.discard('missing'))
    def test_expiry_queue(self):
        q=Outbox();q.offer(Message('h','t',b'1',0,False,1,1,0));self.assertIsNone(q.pop(now=10))
    def test_priority(self):
        q=Outbox();q.offer(Message('bulk','t',b'b',0,False,10,3,time.monotonic()));q.offer(Message('ack','t',b'a',1,False,10,0,time.monotonic()));self.assertEqual(q.pop().payload,b'a')
    def test_bounded_queue(self):
        q=Outbox(max_messages=1);errors=[]
        self.assertTrue(q.offer(Message('a','t',b'a',0,False,None,1,0)))
        self.assertFalse(q.offer(Message('b','t',b'b',0,False,None,1,0,on_error=errors.append)))
        self.assertEqual(errors,['OUTBOX_FULL'])
