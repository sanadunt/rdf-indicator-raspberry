import copy,http.client,json,os,stat,tempfile,threading,time,unittest,uuid
from pathlib import Path
from unittest import mock
from rdf_node.agent import Agent
from rdf_node.ground import Ground
from rdf_node.mqtt import Client,Message
from rdf_node.config import load_config
from rdf_node.api import Auth,Server,set_pin
from rdf_node.util import now_ms,compact,atomic_write
from rdf_node.monitor import Monitor
from test_core import settings,record,csv_bytes,status
from broker_fixture import Broker,wait

class FakeMonitor:
    def __init__(self,cfg):
        self.data=Monitor(cfg).snapshot();self.data.update(clock_trusted=True,clock_state='SYNCED',service_state='ACTIVE',generation='test-generation',cgroup_empty=False)
    def start(self):pass
    def stop(self):pass
    def snapshot(self):return dict(self.data)

def make_agent(path):
    cfg=load_config();cfg['state_dir']=str(path/'state');cfg['source']['share_dir']=str(path/'share')
    cfg['source'].update(authority_verified=True,angle_verified=True)
    cfg['api']['admin_hash_file']=str(path/'admin.json');cfg['api']['port']=0
    (path/'share').mkdir();(path/'share'/'settings.json').write_bytes(compact(settings()))
    time.sleep(.005)
    (path/'share'/'status.json').write_bytes(compact(status(idx=1)))
    (path/'share'/'DOA_value.html').write_bytes(csv_bytes(record()))
    a=Agent(cfg);a.monitor=FakeMonitor(cfg);a.source.poll(force=True)
    (path/'share'/'status.json').write_bytes(compact(status(idx=2)))
    a.source.poll(force=True);a._snapshot();return a
def angular_payload(sid,q,timestamp,revision,flags,values=None,raw_doa=10.0,confidence=8.27):
    return {'v':2,'encoding':'json','sid':sid,'q':q,'timestamp_ms':timestamp,
            'frequency_hz':433920000,'revision':revision,'vfo':0,'convention':1,
            'flags':flags,'raw_doa_deg':raw_doa,'confidence_native_db':confidence,
            'values':[-10+i/100 for i in range(360)] if values is None else values}


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(os.path.realpath(self.tmp.name))
        self.a=make_agent(self.path);self.addCleanup(self.a.journal.close)
    def request(self,op='config.get',**extras):
        return dict(v=2,id='test-'+uuid.uuid4().hex,sid=self.a.sid,boot=self.a.boot,issued_ms=now_ms(),expires_ms=now_ms()+15000,base_rev=self.a.source.revision,op=op,**extras)
    def test_first_observation_not_frame_progress(self):
        from rdf_node.source import Source
        s=Source(self.a.cfg,self.a.journal);s.poll(force=True)
        self.assertFalse(s.view(True)['daq']['frame_progressing'])
    def test_second_frame_proves_progress(self):self.assertTrue(self.a.source.view(True)['valid'])
    def test_outbound_topic_failure_is_visible_in_snapshot(self):
        self.a.clients={'control':Client(cfg={},client_id='offline')}
        self.assertFalse(self.a._offer('health','telemetry/health',{'v':2}))
        self.a._snapshot()
        delivery=self.a.snapshot()['link']['mqtt_topic_delivery']['telemetry/health']
        self.assertEqual((delivery['state'],delivery['error'],delivery['qos']),
                         ('ERROR','MQTT_DISCONNECTED',0))
        self.assertEqual(delivery['confirmation'],'SOCKET_WRITE')
        self.assertIsNone(delivery['sent_ms'])
    def test_config_get_returns_raw_settings_once_without_auto_report(self):
        agent=self.a
        raw=b' \n'+compact(settings())+b'\n'
        (agent.source.path/'settings.json').write_bytes(raw);agent.source.poll(force=True)
        messages=[]
        control=Client(cfg={},client_id='settings-capture-control');bulk=Client(cfg={},client_id='settings-capture-bulk')
        control.ready=True;bulk.ready=True
        def capture(key,topic,payload,**options):messages.append((key,topic,payload,options));return True
        control.offer=capture;bulk.offer=capture;agent.clients={'control':control,'bulk':bulk};agent._generation=-1
        snapshot=agent.snapshot();real_snapshot=agent.snapshot
        def run_once():
            agent.stop_event.clear()
            def one_snapshot():
                agent.stop_event.set()
                return snapshot
            agent.snapshot=one_snapshot;agent._schedule();agent.snapshot=real_snapshot
        run_once()
        self.assertFalse(any(topic.endswith(('config/reported','settings/reported')) for _,topic,_,_ in messages))
        result=agent.commands.submit(self.request(),'ground-controller',topic=agent.prefix+'/cmd/config/get')
        self.assertEqual(result['stage'],'APPLIED')
        messages.clear();run_once()
        response=next((item for item in messages if item[1].endswith('/settings/reported')),None)
        self.assertIsNotNone(response)
        _,_,payload,options=response
        payload=json.loads(payload)
        self.assertEqual(payload['settings_json'],raw.decode('utf-8'))
        self.assertEqual((payload['v'],payload['sid'],payload['boot'],payload['id'],payload['rev']),
                         (2,agent.sid,agent.boot,result['id'],agent.source.revision))
        self.assertEqual((options['qos'],options['retain'],options['expiry']),(1,False,30))
        messages.clear();control.generation+=1;run_once()
        self.assertFalse(any(topic.endswith(('config/reported','settings/reported')) for _,topic,_,_ in messages))

    def test_local_config_get_cannot_queue_unmatched_settings_response(self):
        result=self.a.commands.submit(self.request(),'local-admin')
        self.assertEqual(result['stage'],'REJECTED')
        self.assertEqual(result['result']['error'],'SETTINGS_REQUEST_GROUND_ONLY')
        self.assertIsNone(getattr(self.a,'settings_request_id',None))

    def test_config_get_rejects_unavailable_tls_or_oversized_settings(self):
        agent=self.a
        cases=(
            ('tls', 'TLS_REQUIRED_FOR_SETTINGS_REPORT'),
            ('unavailable', 'SETTINGS_UNAVAILABLE'),
            ('stale', 'SETTINGS_STALE'),
            ('oversized', 'SETTINGS_REPORT_TOO_LARGE'),
        )
        for case,error in cases:
            agent.cfg['mqtt']['tls']=True;agent.source.config_error=None
            agent.source.raw_settings=compact(settings())
            agent.source.settings_seen=time.monotonic()
            if case=='tls':agent.cfg['mqtt']['tls']=False
            elif case=='unavailable':
                agent.source.raw_settings=None;agent.source.config_error='OSError:SETTINGS_UNAVAILABLE'
            elif case=='stale':agent.source.settings_seen-=4
            elif case=='oversized':
                raw=b' '*9000+compact(settings())
                (agent.source.path/'settings.json').write_bytes(raw);agent.source.poll(force=True)
            agent.commands.last_rate.clear()
            result=agent.commands.submit(self.request(),'ground-controller',topic=agent.prefix+'/cmd/config/get')
            self.assertEqual(result['stage'],'REJECTED',case)
            self.assertEqual(result['result']['error'],error,case)
        self.assertIsNone(getattr(agent,'settings_request_id',None))

    def test_read_only_query_works(self):
        self.assertEqual(self.a.commands.submit(
            self.request('operation.get',target_id='missing'),'local-admin')['stage'],'APPLIED')
    def test_retained_command_rejected(self):self.assertEqual(self.a.commands.submit(self.request(),'ground',retained=True)['stage'],'REJECTED')
    def test_command_topic_mismatch(self):self.assertEqual(self.a.commands.submit(self.request(),'ground',topic=self.a.prefix+'/cmd/service/restart')['stage'],'REJECTED')
    def test_stale_session_rejected(self):
        r=self.request();r['sid']='00000000';self.assertEqual(self.a.commands.submit(r,'local-admin')['stage'],'REJECTED')
    def test_expired_command_rejected(self):
        r=self.request();r['expires_ms']=now_ms()-1;self.assertEqual(self.a.commands.submit(r,'local-admin')['stage'],'REJECTED')
    def test_ground_write_works_without_remote_grants(self):
        self.assertTrue(self.a.capabilities()['remote_commands'])
        self.a.commands.start();self.addCleanup(self.a.commands.stop)
        request=self.request('stream.set',profile='control')
        accepted=self.a.commands.submit(request,'ground-controller')
        self.a.commands.queue.join()
        self.assertEqual(accepted['stage'],'ACCEPTED')
        self.assertEqual(self.a.journal.lookup(request['id'])['stage'],'APPLIED')
        self.assertEqual(self.a.profile,'control')
    def test_clock_untrusted_blocks_local_write(self):
        self.a.monitor.data['clock_trusted']=False;self.assertEqual(self.a.commands.submit(self.request('stream.set',profile='control'),'local-admin')['result']['error'],'CLOCK_UNTRUSTED')
    def test_config_revision_conflict(self):
        r=self.request('stream.set',profile='control');r['base_rev']=42;self.assertEqual(self.a.commands.submit(r,'local-admin')['result']['error'],'CONFIG_REVISION_CONFLICT')
    def test_no_hardware_write_in_demo(self):
        self.a.demo=True;self.assertEqual(self.a.commands.submit(self.request('stream.set',profile='control'),'local-admin')['result']['error'],'DEMO_NO_HARDWARE_CONTROL')
    def test_local_reboot_requires_edge_capability_approval(self):
        result=self.a.commands.submit(self.request('system.reboot.prepare'),'local-admin')
        self.assertEqual(result['stage'],'REJECTED')
        self.assertEqual(result['result']['error'],'CAPABILITY_DISABLED')
    def test_local_reboot_accepts_explicit_edge_capability(self):
        self.a.cfg['runtime_mode']='controlled';self.a.cfg['control']['reboot_enabled']=True
        result=self.a.commands.submit(self.request('system.reboot.prepare'),'local-admin')
        self.assertEqual(result['stage'],'ACCEPTED',result)
    def test_ground_reboot_bypasses_remote_and_capability_approval_flags(self):
        self.a.cfg['runtime_mode']='read_only'
        self.a.cfg['control'].update(remote_commands_enabled=False,reboot_enabled=False)
        self.a.helper_status={'allow_remote_control':False,'allow_reboot':False}
        self.assertTrue(self.a.capabilities()['remote_reboot'])
        result=self.a.commands.submit(self.request('system.reboot.prepare'),'ground-controller')
        self.assertEqual(result['stage'],'ACCEPTED',result)
    def test_disabled_shutdown_rejected(self):self.assertEqual(self.a.commands.submit(self.request('system.shutdown.prepare'),'local-admin')['result']['error'],'CAPABILITY_DISABLED')
    def test_shutdown_capability_requires_config_and_helper_approval(self):
        self.a.cfg['runtime_mode']='controlled';self.a.cfg['control']['shutdown_enabled']=True
        self.a.helper_status={'allow_shutdown':False}
        self.assertFalse(self.a.capabilities()['shutdown'])
        self.a.helper_status={'allow_shutdown':True}
        self.assertTrue(self.a.capabilities()['shutdown'])
    def test_ground_shutdown_bypasses_remote_and_capability_approval_flags(self):
        self.a.cfg['runtime_mode']='read_only'
        self.a.cfg['control'].update(remote_commands_enabled=False,shutdown_enabled=False)
        self.a.helper_status={'allow_remote_control':False,'allow_shutdown':False}
        self.assertTrue(self.a.capabilities()['remote_shutdown'])
        result=self.a.commands.submit(self.request('system.shutdown.prepare'),'ground-controller')
        self.assertEqual(result['stage'],'ACCEPTED',result)
    def test_shutdown_is_scheduled_only_after_durable_marker(self):
        request=self.request('system.shutdown.execute',prepare_id='prepared',challenge='fixture')
        self.a.journal.accept(request,'local-admin')
        observed=[]
        def helper(payload):
            observed.append((self.a.journal.lookup(request['id'])['stage'],payload))
            return {'scheduled':True,'delay_seconds':5}
        with mock.patch.object(self.a.commands,'_helper',side_effect=helper):
            stage,result=self.a.commands._execute(request,'local-admin')
        self.assertEqual(stage,'SHUTDOWN_SCHEDULED')
        self.assertEqual(observed[0][0],'SHUTDOWN_SCHEDULED')
        self.assertEqual(observed[0][1]['op'],'system.shutdown.execute')
        self.assertFalse(self.a.planned_reboot)
    def test_ground_keeps_diagnostic_doa_out_of_authoritative_detection(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground-diagnostic')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        sid='12ab34cd';ground.receive(ground.prefix+'/state',compact({'v':2,'sid':sid,'boot':'boot-test','instance':'test'}))
        timestamp=now_ms()
        sample={'v':2,'sid':sid,'q':1,'source':'doa.xml','source_timestamp_ms':timestamp,
                'observed_timestamp_ms':timestamp,'raw_doa_deg':200,'frequency_mhz':137,
                'trust':'UNVERIFIED','validation_reasons':['DIAGNOSTIC_UNVERIFIED','EMPTY_CSV']}
        ground.receive(ground.prefix+'/telemetry/diagnostic/doa',compact(sample))
        snapshot=ground.snapshot()
        self.assertEqual(snapshot['diagnostic_doa']['raw_doa_deg'],200)
        self.assertTrue(snapshot['diagnostic_doa']['available']);self.assertFalse(snapshot['diagnostic_doa']['stale'])
        self.assertEqual(snapshot['diagnostic_doa']['trust'],'UNVERIFIED')
        self.assertFalse(snapshot['detection']['valid']);self.assertIsNone(snapshot['detection']['relative_doa_deg'])
        self.assertEqual(ground.doa,{})
        newer=dict(sample,q=2,source_timestamp_ms=timestamp+1000,raw_doa_deg=201)
        ground.receive(ground.prefix+'/telemetry/diagnostic/doa',compact(newer))
        self.assertEqual(ground.snapshot()['diagnostic_doa']['q'],2)
        older=dict(newer,q=3,source_timestamp_ms=timestamp+500)
        with self.assertRaises(ValueError):
            ground.receive(ground.prefix+'/telemetry/diagnostic/doa',compact(older))

    def test_ground_routes_regular_unverified_doa_to_diagnostics(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground-unverified-doa')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        sid='12ab34cd';timestamp=now_ms()
        ground.receive(ground.prefix+'/state',compact({'v':2,'sid':sid,'boot':'boot-test','instance':'test','cfg':7}))
        ground.receive(ground.prefix+'/telemetry/health',compact(
            {'v':2,'sid':sid,'q':1,'t':timestamp,'run':1,'daq':1,'drop':0,'age':0,'temp':None,'clk':1,'rev':7}))
        sample={'v':2,'sid':sid,'q':1,'t':timestamp,'f':433920000,'a':10.0,'c':8.27,'p':-90.17,'rev':7,
                'ok':0,'trust':'UNVERIFIED','angle_reference':'RAW',
                'validation_reasons':['SOURCE_UNVERIFIED','ANGLE_UNVERIFIED']}
        ground.receive(ground.prefix+'/telemetry/doa',compact(sample))
        snapshot=ground.snapshot()
        self.assertEqual((snapshot['diagnostic_doa']['source'],snapshot['diagnostic_doa']['raw_doa_deg']),
                         ('DOA_value.html',10.0))
        self.assertEqual(snapshot['diagnostic_doa']['trust'],'UNVERIFIED')
        self.assertFalse(snapshot['detection']['valid'])
        self.assertEqual(ground.doa,{})
        xml={'v':2,'sid':sid,'q':1,'source':'doa.xml','source_timestamp_ms':timestamp,
             'observed_timestamp_ms':timestamp,'raw_doa_deg':200.0,'frequency_mhz':137.0,
             'trust':'UNVERIFIED','validation_reasons':['DIAGNOSTIC_UNVERIFIED']}
        ground.receive(ground.prefix+'/telemetry/diagnostic/doa',compact(xml))
        self.assertEqual(ground.snapshot()['diagnostic_doa']['source'],'DOA_value.html')

    def test_ground_accepts_json_angular_live_frame(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground-json-angular')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        sid='12ab34cd';timestamp=now_ms();values=[-10+i/100 for i in range(360)]
        ground.receive(ground.prefix+'/state',compact({'v':2,'sid':sid,'boot':'boot-test','instance':'test','cfg':8}))
        ground.receive(ground.prefix+'/telemetry/health',compact(
            {'v':2,'sid':sid,'q':1,'t':timestamp,'run':1,'daq':1,'drop':0,'age':0,'temp':None,'clk':1,'rev':7}))
        payload={'v':2,'encoding':'json','sid':sid,'q':1,'timestamp_ms':timestamp,
                 'frequency_hz':433920000,'revision':7,'vfo':0,'convention':1,'flags':63,
                 'raw_doa_deg':10.0,'confidence_native_db':8.27,'values':values}
        ground.receive(ground.prefix+'/telemetry/angular',compact(payload))
        view=ground.angular_view()
        self.assertEqual(view['values'],values)
        self.assertEqual(view['encoding'],'json')
        self.assertEqual(view['peak_index'],359)

    def test_live_doa_accepts_revision_mismatch_with_valid_evidence(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground-live-doa-revision')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        sid='12ab34cd';timestamp=now_ms()
        ground.receive(ground.prefix+'/state',compact(
            {'v':2,'sid':sid,'boot':'boot-test','instance':'test','cfg':8}))
        ground.receive(ground.prefix+'/telemetry/health',compact(
            {'v':2,'sid':sid,'q':1,'t':timestamp,'run':1,'daq':1,'drop':0,'age':0,'temp':None,'clk':1,'rev':8}))
        payload={'v':2,'sid':sid,'q':1,'t':timestamp,'f':433920000,'a':10.0,'c':8.27,'p':-90.17,'rev':7,'ok':1}
        ground.receive(ground.prefix+'/telemetry/doa',compact(payload))
        self.assertEqual(ground.doa['rev'],7)
        self.assertTrue(ground.snapshot()['detection']['valid'])

    def test_angular_live_accepts_revision_mismatch_and_keeps_other_gates(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground-live-angular-revision')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        sid='12ab34cd';timestamp=now_ms()
        ground.receive(ground.prefix+'/state',compact(
            {'v':2,'sid':sid,'boot':'boot-test','instance':'test','cfg':8}))
        ground.receive(ground.prefix+'/telemetry/health',compact(
            {'v':2,'sid':sid,'q':1,'t':timestamp,'run':1,'daq':1,'drop':0,'age':0,'temp':None,'clk':1,'rev':8}))
        payload=angular_payload(sid,1,timestamp,7,63)
        ground.receive(ground.prefix+'/telemetry/angular',compact(payload))
        self.assertEqual(ground.angular_view()['revision'],7)
        self.assertFalse(ground.angular_view()['stale'])
        with self.assertRaises(ValueError):
            ground.receive(ground.prefix+'/telemetry/angular',compact(dict(payload,q=2,sid='98ab76cd')))
        ground.health_seen=time.monotonic()-9
        with self.assertRaisesRegex(ValueError,'ANGULAR_WITHOUT_HEALTH'):
            ground.receive(ground.prefix+'/telemetry/angular',compact(dict(payload,q=3)))
        with self.assertRaisesRegex(ValueError,'ANGULAR_EVIDENCE_INCOMPLETE'):
            ground.receive(ground.prefix+'/telemetry/angular',compact(dict(payload,q=4,flags=7)))
        self.assertEqual(ground.angular_view()['q'],1)

    def test_settings_report_requires_current_request_session_and_boot(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground-settings-report')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        sid='12ab34cd';boot='boot-current';request_id='settings-request-1';timestamp=now_ms()
        ground.receive(ground.prefix+'/state',compact(
            {'v':2,'sid':sid,'boot':boot,'instance':'test','cfg':7}))
        ground.settings_request_id=request_id
        raw=compact(settings()).decode('utf-8')
        report={'v':2,'sid':sid,'boot':boot,'id':request_id,'rev':7,'t':timestamp,'settings_json':raw}
        for changes in ({'sid':'98ab76cd'},{'boot':'boot-old'},{'id':'other-request'}):
            with self.assertRaises(ValueError):
                ground.receive(ground.prefix+'/settings/reported',compact({**report,**changes}))
            self.assertEqual(ground.settings_request_id,request_id)
            self.assertIsNone(ground.config_view().get('settings_json'))
        with self.assertRaisesRegex(ValueError,'SOURCE_TIMESTAMP_NOT_FRESH'):
            ground.receive(ground.prefix+'/settings/reported',
                           compact({**report,'t':timestamp-30001}))
        with self.assertRaisesRegex(ValueError,'RETAINED_SETTINGS_REPORT'):
            ground.receive(ground.prefix+'/settings/reported',compact(report),retained=True)
        ground.receive(ground.prefix+'/settings/reported',compact(report))
        view=ground.config_view()
        self.assertEqual(view['settings_json'],raw)
        self.assertEqual(view['safe_settings']['center_frequency_hz'],433920000)
        self.assertIsNone(ground.settings_request_id)
        ground.receive(ground.prefix+'/state',compact(
            {'v':2,'sid':sid,'boot':boot,'instance':'test','cfg':8}))
        view=ground.config_view()
        self.assertEqual(view['settings_json'],raw)
        self.assertEqual(view['safe_settings'],{})

    def test_ground_routes_json_angular_without_authority_to_diagnostics(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground-unverified-angular')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        sid='12ab34cd';timestamp=now_ms()
        ground.receive(ground.prefix+'/state',compact({'v':2,'sid':sid,'boot':'boot-test','instance':'test','cfg':7}))
        ground.receive(ground.prefix+'/telemetry/health',compact(
            {'v':2,'sid':sid,'q':1,'t':timestamp,'run':1,'daq':1,'drop':0,'age':0,'temp':None,'clk':1,'rev':7}))
        payload={'v':2,'encoding':'json','sid':sid,'q':1,'timestamp_ms':timestamp,
                 'frequency_hz':433920000,'revision':7,'vfo':0,'convention':1,'flags':31,
                 'raw_doa_deg':10.0,'confidence_native_db':8.27,
                 'values':[-10+i/100 for i in range(360)]}
        ground.receive(ground.prefix+'/telemetry/angular',compact(payload))
        view=ground.diagnostic_angular_view()
        self.assertEqual((view['source'],view['trust']),('DOA_value.html','UNVERIFIED'))
        self.assertIn('SOURCE_AUTHORITY_UNVERIFIED_AT_EDGE',view['validation_reasons'])
        self.assertIsNone(ground.angular_view())
        self.assertFalse(ground.snapshot()['detection']['valid'])
        rejected=dict(payload,q=2,flags=27)
        with self.assertRaisesRegex(ValueError,'ANGULAR_EVIDENCE_INCOMPLETE'):
            ground.receive(ground.prefix+'/telemetry/angular',compact(rejected))
        self.assertEqual(ground.diagnostic_angular_view()['q'],1)

    def test_scheduler_publishes_trust_only_unverified_data_on_regular_topics(self):
        agent=self.a;agent.cfg['source'].update(authority_verified=False,angle_verified=False)
        self.assertEqual(agent.capabilities()['codecs'],['json'])
        agent.source.poll(force=True);agent._snapshot()
        snapshot=agent.snapshot()
        self.assertFalse(snapshot['detection']['valid'])
        self.assertEqual(set(snapshot['detection']['reasons']),{'SOURCE_UNVERIFIED','ANGLE_UNVERIFIED'})
        agent.profile='balanced';agent.bulk_resume_after=0
        offers=self._run_agent_scheduler_once(agent,snapshot)
        doa=next(payload for key,topic,payload in offers if key=='doa')
        self.assertEqual((doa['ok'],doa['trust'],doa['angle_reference'],doa['a']),
                         (0,'UNVERIFIED','RAW',10.0))
        self.assertEqual(doa['validation_reasons'],['SOURCE_UNVERIFIED','ANGLE_UNVERIFIED'])
        angular=json.loads(next(payload for key,topic,payload in offers if key=='angular'))
        self.assertEqual((angular['encoding'],angular['flags'],len(angular['values'])),('json',23,360))
        self.assertEqual(angular['values'],agent.source.record['values'])
        agent.last_doa_q=0;agent.last_angular_q=0
        blocked=copy.deepcopy(snapshot);blocked['detection']['reasons'].append('NO_FRESH_DOA')
        blocked_offers=self._run_agent_scheduler_once(agent,blocked)
        blocked_keys=[key for key,topic,payload in blocked_offers]
        self.assertNotIn('doa',blocked_keys)
        self.assertNotIn('angular',blocked_keys)
    def test_ground_keeps_diagnostic_angular_unverified_and_out_of_live_detection(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground-diagnostic-angular')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        sid='12ab34cd'
        ground.receive(ground.prefix+'/state',compact({'v':2,'sid':sid,'boot':'boot-test','instance':'test'}))
        timestamp=now_ms()-30000
        frame=angular_payload(sid,1,timestamp,None,1,raw_doa=123.45,confidence=8.2)
        ground.receive(ground.prefix+'/telemetry/diagnostic/angular',compact(frame))
        view=ground.diagnostic_angular_view()
        self.assertEqual((view['source'],view['trust'],view['timestamp_ms']),('DOA_value.html','UNVERIFIED',timestamp))
        self.assertEqual(len(view['values']),360)
        self.assertAlmostEqual(view['values'][0],-10.0,places=2)
        self.assertAlmostEqual(view['values'][-1],-6.41,places=2)
        self.assertEqual(view['raw_doa_deg'],123.45)
        self.assertTrue(view['stale']);self.assertIn('SOURCE_STALE',view['validation_reasons'])
        self.assertIsNone(ground.angular_view())
        self.assertFalse(ground.snapshot()['detection']['valid'])

    def test_ground_diagnostic_angular_api_returns_candidate_values(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground-angular-api')
        cfg['api']['admin_hash_file']=str(self.path/'ground-api-auth.json');cfg['api']['port']=0
        ground=Ground(cfg)
        sid='12ab34cd'
        ground.receive(ground.prefix+'/state',compact({'v':2,'sid':sid,'boot':'boot-test','instance':'test'}))
        timestamp=now_ms()
        ground.receive(ground.prefix+'/telemetry/diagnostic/angular',
                       compact(angular_payload(sid,1,timestamp,None,1)))
        server=Server(ground,cfg,ground=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            conn.request('GET','/api/v2/angular/diagnostic/latest')
            response=conn.getresponse();payload=json.loads(response.read());conn.close()
            self.assertEqual(response.status,200)
            self.assertEqual(payload['source_timestamp_ms'],timestamp)
            self.assertEqual((payload['trust'],len(payload['values'])),('UNVERIFIED',360))
            self.assertAlmostEqual(payload['values'][0],-10.0,places=2)
        finally:
            server.shutdown();server.server_close();thread.join(2);ground.journal.close()


    def _run_agent_scheduler_once(self,agent,snapshot,queued_diagnostic=False):
        offers=[]
        control=Client(cfg={},client_id='scheduler-control')
        bulk=Client(cfg={},client_id='scheduler-bulk')
        control.ready=True;bulk.ready=True
        agent.clients={'control':control,'bulk':bulk}
        agent._generation=0;agent.stop_event.clear()
        def offer(*args,**kwargs):
            offers.append((args[0],args[1],args[2]));return True
        def one_snapshot():
            if queued_diagnostic:
                control.outbox.offer(Message('diagnostic-angular','sdr/v2/test/telemetry/diagnostic/angular',
                                             b'pending',0,False,3,3,time.monotonic()))
            agent.stop_event.set()
            return snapshot
        agent._offer=offer;agent.snapshot=one_snapshot
        agent._schedule()
        return offers

    def test_diagnostic_angular_waits_for_matching_source_snapshot(self):
        agent=self.a;agent.profile='balanced';r=agent.source.record;snapshot=agent.snapshot()
        self.assertEqual(snapshot['detection']['q'],r['q'])
        snapshot['detection']['q']=r['q']-1
        offers=self._run_agent_scheduler_once(agent,snapshot)
        self.assertNotIn('diagnostic-angular',[item[0] for item in offers])

    def test_control_profile_discards_queued_diagnostic_angular_message(self):
        agent=self.a;snapshot=agent.snapshot();agent.profile='control'
        offers=self._run_agent_scheduler_once(agent,snapshot,queued_diagnostic=True)
        self.assertNotIn('diagnostic-angular',[item[0] for item in offers])
        self.assertEqual(agent.clients['control'].outbox.status()['depth'],0)


    def test_diagnostic_angular_not_duplicated_when_live_bulk_is_available(self):
        agent=self.a;agent.profile='balanced';agent.bulk_resume_after=0
        snapshot=agent.snapshot()
        offers=self._run_agent_scheduler_once(agent,snapshot)
        self.assertNotIn('diagnostic-angular',[item[0] for item in offers])

    def test_bulk_recovery_discards_queued_diagnostic_angular(self):
        agent=self.a;agent.profile='balanced';agent.bulk_resume_after=0
        snapshot=agent.snapshot()
        self._run_agent_scheduler_once(agent,snapshot,queued_diagnostic=True)
        self.assertEqual(agent.clients['control'].outbox.status()['depth'],0)

    def test_live_bulk_does_not_require_ground_receipt(self):
        agent=self.a;agent.profile='balanced';agent.bulk_resume_after=0
        snapshot=agent.snapshot()
        self.assertNotIn('ground',snapshot['link'])
        offers=self._run_agent_scheduler_once(agent,snapshot)
        keys=[item[0] for item in offers]
        self.assertIn('angular',keys)
        self.assertNotIn('diagnostic-angular',keys)

    def test_stopped_source_resets_valid_blocked_diagnostic_interval(self):
        agent=self.a;agent.profile='balanced';agent.bulk_resume_after=0
        live=agent.snapshot()
        self.assertTrue(live['detection']['valid']);self.assertTrue(live['daq']['healthy'])
        stopped=copy.deepcopy(live)
        stopped['detection']['valid']=False;stopped['daq']['healthy']=False
        stopped['processing']['observed']='STOPPED'
        control=Client(cfg={},client_id='scheduler-control');bulk=Client(cfg={},client_id='scheduler-bulk')
        control.ready=True;bulk.ready=False;agent.clients={'control':control,'bulk':bulk}
        agent._generation=0;agent.stop_event.clear()
        offers=[];snapshots=[live]*7+[stopped]
        def next_snapshot():
            result=snapshots.pop(0)
            if not snapshots:agent.stop_event.set()
            return result
        def offer(*args,**kwargs):
            offers.append((args[0],args[1],args[2]));return True
        agent.snapshot=next_snapshot;agent._offer=offer
        agent._schedule()
        diagnostic=[json.loads(payload) for key,topic,payload in offers if key=='diagnostic-angular']
        self.assertTrue(diagnostic)
        self.assertTrue(any(payload['flags']&4==0 for payload in diagnostic))

    def test_ground_rejects_success_ack_for_shutdown_execution(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        sid='12ab34cd';request={'id':'ground-shutdown','op':'system.shutdown.execute','sid':sid}
        ground.node_state={'sid':sid}
        ground.journal.accept(request,'ground-local-admin');ground.journal.update(request['id'],'REQUESTED',{})
        for status in ('APPLIED','REBOOT_SCHEDULED',{}):
            ground.receive(ground.prefix+'/ack/operation',compact({'v':2,'sid':sid,'id':request['id'],
                           'status':status,'result':{'proof':'fake'}}))
            self.assertEqual(ground.journal.lookup(request['id'])['stage'],'REQUESTED')
    def test_ground_preserves_ambiguous_shutdown_outcome_and_rejects_late_failure(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        sid='12ab34cd';request={'id':'scheduled-shutdown','op':'system.shutdown.execute','sid':sid}
        ground.node_state={'sid':sid}
        ground.journal.accept(request,'ground-local-admin');ground.journal.update(request['id'],'SHUTDOWN_SCHEDULED',{'scheduled':True})
        for status in ('FAILED','REJECTED'):
            ground.receive(ground.prefix+'/ack/operation',compact({'v':2,'sid':sid,'id':request['id'],
                           'status':status,'result':{'error':'late failure'}}))
            self.assertEqual(ground.journal.lookup(request['id'])['stage'],'SHUTDOWN_SCHEDULED')
        ground.receive(ground.prefix+'/ack/operation',compact({'v':2,'sid':sid,'id':request['id'],
                       'status':'OUTCOME_UNKNOWN','result':{'error':'helper timeout'}}))
        self.assertEqual(ground.journal.lookup(request['id'])['stage'],'OUTCOME_UNKNOWN')
        ground.receive(ground.prefix+'/ack/operation',compact({'v':2,'sid':sid,'id':request['id'],
                       'status':'FAILED','result':{'error':'stale failure'}}))
        self.assertEqual(ground.journal.lookup(request['id'])['stage'],'OUTCOME_UNKNOWN')
    def test_ground_accepts_shutdown_failure_before_schedule(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        sid='12ab34cd';request={'id':'failed-shutdown','op':'system.shutdown.execute','sid':sid}
        ground.node_state={'sid':sid}
        ground.journal.accept(request,'ground-local-admin');ground.journal.update(request['id'],'REQUESTED',{})
        ground.receive(ground.prefix+'/ack/operation',compact({'v':2,'sid':sid,'id':request['id'],
                       'status':'FAILED','result':{'error':'SHUTDOWN_DISABLED'}}))
        self.assertEqual(ground.journal.lookup(request['id'])['stage'],'FAILED')
    def test_ground_marks_pending_shutdown_unknown_when_node_returns(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        old_sid='12ab34cd';ids=[]
        for stage in ('REQUESTED','ACCEPTED','APPLYING','SHUTDOWN_SCHEDULED'):
            op_id='ground-shutdown-'+stage.lower()
            request={'id':op_id,'op':'system.shutdown.execute','sid':old_sid,'boot':'boot-old'}
            ground.journal.accept(request,'ground-local-admin')
            ground.journal.update(op_id,stage,{'scheduled':True})
            ids.append(op_id)
        ground.node_state={'sid':old_sid,'boot':'boot-old'}
        state={'v':2,'sid':'98ab76cd','boot':'boot-new','instance':'instance-new'}
        ground.receive(ground.prefix+'/state',compact(state))
        for op_id in ids:
            self.assertEqual(ground.journal.lookup(op_id)['stage'],'OUTCOME_UNKNOWN')
    def test_ground_reconciles_shutdown_beyond_recent_operation_window(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        sid='12ab34cd';request={'id':'old-shutdown','op':'system.shutdown.execute','sid':sid,'boot':'boot-old'}
        ground.journal.accept(request,'ground-local-admin');ground.journal.update(request['id'],'SHUTDOWN_SCHEDULED',{})
        first=ground.journal.lookup(request['id'])['updated_ms']+1
        with mock.patch('rdf_node.journal.now_ms',side_effect=iter(range(first,first+5000))):
            for index in range(2001):
                filler={'id':f'recent-{index}','op':'config.get','sid':sid}
                ground.journal.accept(filler,'ground-local-admin');ground.journal.update(filler['id'],'APPLIED',{})
        ground.node_state={'sid':sid,'boot':'boot-old'}
        state={'v':2,'sid':'98ab76cd','boot':'boot-new','instance':'instance-new'}
        ground.receive(ground.prefix+'/state',compact(state))
        self.assertEqual(ground.journal.lookup(request['id'])['stage'],'OUTCOME_UNKNOWN')
    def test_ground_command_preserves_client_id_for_result_recovery(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        ground.client=mock.Mock(ready=True);ground.client.offer.return_value=True
        ground.node_state={'sid':'12ab34cd','boot':'boot-current'};ground.node_config={'rev':7}
        ground.health={'q':1};ground.health_seen=time.monotonic()
        request={'id':'ground-client-0123456789abcdef','op':'system.shutdown.execute',
                 'prepare_id':'prepare-id','challenge':'challenge','base_rev':7}
        result=ground.submit_command(request)
        self.assertEqual(result['id'],request['id'])
        self.assertEqual(ground.journal.lookup(request['id'])['request']['id'],request['id'])
    def test_ground_rejects_invalid_client_command_id(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        ground.client=mock.Mock(ready=True);ground.client.offer.return_value=True
        ground.node_state={'sid':'12ab34cd','boot':'boot-current'};ground.node_config={'rev':7}
        ground.health={'q':1};ground.health_seen=time.monotonic()
        result=ground.submit_command({'id':'invalid id','op':'system.shutdown.prepare','base_rev':7})
        self.assertEqual(result['error'],'INVALID_COMMAND_ID')
    def test_idempotent_query_does_not_redo(self):
        r=self.request('operation.get',target_id='missing');self.a.commands.submit(r,'local-admin');old=self.a.journal.lookup(r['id'])
        self.a.commands.submit(r,'local-admin');self.assertEqual(old['updated_ms'],self.a.journal.lookup(r['id'])['updated_ms'])
    def test_public_operation_hides_challenge(self):
        r=self.request('system.reboot.prepare');self.a.journal.accept(r,'test');self.a.journal.update(r['id'],'APPLIED',{'challenge':'private-fixture'})
        self.assertNotIn('challenge',self.a.commands.public(self.a.journal.lookup(r['id']))['result'])
    def test_helper_backup_does_not_truncate_hardlink(self):
        from rdf_node.helper import patch_file,DEFAULT_POLICY
        import hashlib
        source=self.path/'share'/'settings.json';raw=source.read_bytes();target=self.path/'unrelated';target.write_bytes(b'unchanged')
        os.link(target,source.parent/'.rdf-settings-backup.json')
        patch_file(source,{'gain_db':20.7},hashlib.sha256(raw).hexdigest(),DEFAULT_POLICY)
        self.assertEqual(target.read_bytes(),b'unchanged')
    def test_helper_no_startup_action_without_intent(self):
        from rdf_node.helper import Controller,DEFAULT_POLICY
        p=dict(DEFAULT_POLICY,state_dir=str(self.path/'helper'),allowed_user=None,allow_lifecycle=True,lifecycle_audited=True,engine_service='approved-test.service')
        c=Controller(p)
        with mock.patch.object(c,'_run') as run:c.reconcile_intent();run.assert_not_called()
    def test_helper_stopped_intent_reconciled_not_rebooted(self):
        from rdf_node.helper import Controller,DEFAULT_POLICY

        p=dict(DEFAULT_POLICY,state_dir=str(self.path/'helper'),allowed_user=None,allow_lifecycle=True,lifecycle_audited=True,engine_service='approved-test.service')
        c=Controller(p);atomic_write(c.state/'intent.json',compact({'desired':'STOPPED'}))
        with mock.patch.object(c,'_run') as run:
            c.reconcile_intent();run.assert_called_once_with(['/usr/bin/systemctl','--no-block','stop','approved-test.service'])

    def test_live_mqtt_reconfiguration_applies_new_transport(self):
        broker=Broker();self.addCleanup(broker.stop)
        credentials=self.path/'mqtt.json';credentials.write_bytes(compact({'username':'test','password':'test-password'}))
        self.a.journal.close();cfg=self.a.cfg
        cfg['mqtt'].update(enabled=True,host='127.0.0.1',port=broker.port,tls=False,allow_insecure_loopback=True,
                           control_credentials_file=str(credentials),bulk_credentials_file=str(credentials))
        agent=Agent(cfg);agent.monitor=FakeMonitor(cfg);self.addCleanup(agent.stop);agent.start()
        self.assertTrue(wait(lambda:len(broker.clients)==2,8),broker.errors)
        agent.configure_mqtt({'enabled':False,'host':'ground.example','port':8883,'client_id':'new-node',
                              'transport':'tcp','tls':True,'websocket_path':'/mqtt',
                              'control':{'username':'','password':''},'bulk':{'username':'','password':''}})
        self.assertTrue(wait(lambda:not agent.clients and not broker.clients,8),[agent.clients,broker.clients])
        self.assertFalse(agent.cfg['mqtt']['enabled'])
        self.assertEqual(agent.cfg['mqtt']['host'],'ground.example')


class GroundCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        cfg=load_config();cfg['state_dir']=str(Path(self.tmp.name)/'ground-ppp')
        self.ground=Ground(cfg);self.addCleanup(self.ground.journal.close)
        self.sid='12ab34cd';self.boot='boot-current'
        self.ground.client=mock.Mock(ready=True);self.ground.client.offer.return_value=True
        self.ground.node_state={'sid':self.sid,'boot':self.boot,'instance':'ground-fixture','cfg':7}
        self.ground.node_config={'rev':7}
        self.ground.health={'v':2,'sid':self.sid,'q':1,'t':now_ms(),'daq':1,'run':1}
        self.ground.health_seen=time.monotonic()

    def test_config_get_without_fresh_health_still_requests_settings(self):
        self.ground.health={};self.ground.health_seen=0
        result=self.ground.submit_command({'id':'ground-settings-request','op':'config.get'})
        self.assertEqual(result['stage'],'REQUESTED')
        request=self.ground.journal.lookup(result['id'])['request']
        self.assertEqual(request['base_rev'],7)
        self.assertEqual(self.ground.settings_request_id,result['id'])
        self.assertEqual(self.ground.client.offer.call_args.args[1],
                         self.ground.prefix+'/cmd/config/get')
        duplicate=self.ground.submit_command({'id':'ground-settings-request-2','op':'config.get'})
        self.assertEqual((duplicate['stage'],duplicate['error']),
                         ('REJECTED','SETTINGS_REQUEST_PENDING'))
        raw=compact(settings()).decode('utf-8')
        report={'v':2,'sid':self.sid,'boot':self.boot,'id':result['id'],'rev':7,
                't':now_ms(),'settings_json':raw}
        self.ground.receive(self.ground.prefix+'/settings/reported',compact(report))
        self.assertEqual(self.ground.config_view()['settings_json'],raw)
        self.assertIsNone(self.ground.settings_request_id)
        self.ground.client.offer.assert_called_once()

    def test_caller_cannot_override_ground_base_revision(self):
        result=self.ground.submit_command({
            'id':'ground-base-revision','op':'config.patch','base_rev':99,
            'changes':{'gain_db':20.7}})
        self.assertEqual(result['stage'],'REQUESTED')
        request=self.ground.journal.lookup(result['id'])['request']
        self.assertEqual(request['base_rev'],7)
    def test_rejected_settings_request_allows_manual_retry(self):
        self.ground.health={};self.ground.health_seen=0
        first=self.ground.submit_command({'id':'ground-settings-rejected','op':'config.get'})
        ack={'v':2,'sid':self.sid,'id':first['id'],'status':'REJECTED',
             'result':{'error':'SETTINGS_STALE'}}
        self.ground.receive(self.ground.prefix+'/ack/config',compact(ack))
        self.assertIsNone(self.ground.settings_request_id)
        retry=self.ground.submit_command({'id':'ground-settings-retry','op':'config.get'})
        self.assertEqual(retry['stage'],'REQUESTED')
        self.assertEqual(self.ground.settings_request_id,retry['id'])
        self.assertEqual(self.ground.client.offer.call_count,2)

    def test_mutating_command_requires_fresh_health(self):
        self.ground.health_seen=time.monotonic()-9
        result=self.ground.submit_command(
            {'id':'ground-stale-config-patch','op':'config.patch','changes':{'gain_db':20.7}})
        self.assertEqual((result['stage'],result['error']),('REJECTED','NODE_NOT_FRESH'))
        self.ground.client.offer.assert_not_called()

    def add_operation(self,op_id,stage,*,issued_ms=None,sid=None,result=None):
        request={'id':op_id,'op':'ppp.restart','sid':sid or self.sid,'boot':self.boot,
                 'issued_ms':issued_ms or now_ms()-1000,'expires_ms':now_ms()+15000,'base_rev':7}
        self.ground.journal.accept(request,'ground-local-admin')
        self.ground.journal.update(op_id,stage,result or {})
        return request

    def test_ppp_restart_applies_only_after_fresh_health(self):
        requested_ms=now_ms()-1000
        request=self.add_operation('ground-ppp-health','REQUESTED',issued_ms=requested_ms)
        self.ground.health['t']=requested_ms-1
        ack={'v':2,'sid':self.sid,'id':request['id'],'status':'PPP_RESTART_REQUESTED',
             'result':{'accepted_by_systemd':True,'requested_ms':requested_ms,
                       'service':'t900-ppp.service'}}
        self.ground.receive(self.ground.prefix+'/ack/operation',compact(ack))
        self.assertEqual(self.ground.journal.lookup(request['id'])['stage'],'PPP_RESTART_REQUESTED')

        health={'v':2,'sid':self.sid,'q':2,'t':requested_ms+1,'daq':1,'run':1}
        self.ground.receive(self.ground.prefix+'/telemetry/health',compact(health))
        operation=self.ground.journal.lookup(request['id'])
        self.assertEqual(operation['stage'],'APPLIED')
        self.assertEqual(operation['result']['proof'],'SYSTEMD_ACCEPTED_AND_FRESH_NODE_HEALTH')
        self.assertEqual(operation['result']['health_timestamp_ms'],health['t'])
        self.assertNotIn('link_recovered',operation['result'])

    def test_unresolved_ppp_restart_requires_confirmation(self):
        self.add_operation('ground-ppp-unacked','REQUESTED')
        intent={'id':'ground-ppp-new','op':'ppp.restart','base_rev':7}
        rejected=self.ground.submit_command(intent)
        self.assertEqual(rejected['error'],'PPP_RESTART_CONFIRMATION_REQUIRED')
        self.ground.client.offer.assert_not_called()
        self.assertEqual(self.ground.journal.lookup('ground-ppp-unacked')['stage'],'REQUESTED')

        self.ground.health_seen=time.monotonic()-9
        stale=self.ground.submit_command({**intent,'confirm_previous_unknown':True})
        self.assertEqual(stale['error'],'NODE_NOT_FRESH')
        self.ground.client.offer.assert_not_called()

    def test_confirmed_new_ppp_request_preserves_old_unknown(self):
        old=self.add_operation('ground-ppp-old','OUTCOME_UNKNOWN',result={'reason':'ACK_LOST'})
        new_id='ground-ppp-confirmed-new'
        submitted=self.ground.submit_command({'id':new_id,'op':'ppp.restart','base_rev':7,
                                              'confirm_previous_unknown':True})
        self.assertEqual(submitted['stage'],'REQUESTED')
        self.assertNotEqual(submitted['id'],old['id'])
        previous=self.ground.journal.lookup(old['id'])
        self.assertEqual(previous['stage'],'OUTCOME_UNKNOWN')
        self.assertEqual(previous['result']['confirmed_by'],new_id)
        new=self.ground.journal.lookup(new_id)
        self.assertNotIn('confirm_previous_unknown',new['request'])
        self.assertNotIn('confirm_previous_unknown',self.ground.client.offer.call_args.args[2])
        self.assertEqual(self.ground.client.offer.call_args.args[1],
                         self.ground.prefix+'/cmd/service/ppp/restart')
        self.assertEqual([item['id'] for item in self.ground.journal.pending_ppp_restarts()],
                         [new_id])

    def test_session_change_keeps_ppp_restart_unknown(self):
        old=self.add_operation('ground-ppp-session-change','PPP_RESTART_REQUESTED',
                               result={'accepted_by_systemd':True,'requested_ms':now_ms()-2000})
        state={'v':2,'sid':'98ab76cd','boot':'boot-new','instance':'ground-returned'}
        self.ground.receive(self.ground.prefix+'/state',compact(state))
        self.assertEqual(self.ground.journal.lookup(old['id'])['stage'],'OUTCOME_UNKNOWN')
        health={'v':2,'sid':state['sid'],'q':1,'t':now_ms(),'daq':1,'run':1}
        self.ground.receive(self.ground.prefix+'/telemetry/health',compact(health))
        self.assertEqual(self.ground.journal.lookup(old['id'])['stage'],'OUTCOME_UNKNOWN')

class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(os.path.realpath(self.tmp.name))
        self.a=make_agent(self.path);self.addCleanup(self.a.journal.close)

    def request(self,op,**extras):
        return dict(v=2,id='command-'+uuid.uuid4().hex,sid=self.a.sid,boot=self.a.boot,
                    issued_ms=now_ms(),expires_ms=now_ms()+15000,base_rev=self.a.source.revision,
                    op=op,**extras)

    def test_ground_actor_is_internal_and_reaches_helper_without_root_approval(self):
        from rdf_node.helper import Controller,DEFAULT_POLICY
        self.a.cfg['runtime_mode']='read_only'
        self.a.helper_status={'allow_remote_control':False,'allow_reboot':False}
        helper=Controller(dict(DEFAULT_POLICY,state_dir=str(self.path/'remote-helper'),allowed_user=None))
        self.a.commands.start();self.addCleanup(self.a.commands.stop)
        with mock.patch.object(helper,'_run'), \
                mock.patch.object(self.a.commands,'_helper',
                                  side_effect=lambda request:helper.dispatch(request,os.getuid())):
            request=self.request('system.reboot.prepare')
            accepted=self.a.commands.submit(request,'ground-controller')
            self.a.commands.queue.join()
        operation=self.a.journal.lookup(request['id'])
        self.assertEqual(accepted['stage'],'ACCEPTED')
        self.assertEqual(operation['stage'],'APPLIED')
        self.assertEqual(helper.challenges[request['id']][2],'reboot')
    def test_remote_payload_cannot_choose_origin(self):
        request=self.request('stream.set',profile='balanced',origin='ground-controller')
        result=self.a.commands.submit(request,'ground-controller')
        self.assertEqual(result['result']['error'],'UNKNOWN_COMMAND_FIELD')

    def test_ground_remote_capabilities_do_not_enable_local_panel_actions(self):
        self.a.helper_status={'allow_config':False,'allow_lifecycle':False,'allow_reboot':False,
                              'allow_shutdown':False,'allow_ppp_restart':False,
                              'allow_remote_control':False,'settings_target_available':True,
                              'lifecycle_target_available':True}
        caps=self.a.capabilities()
        self.assertTrue(caps['remote_commands'])
        self.assertTrue(caps['remote_config_patch'])
        self.assertTrue(caps['remote_processing'])
        self.assertTrue(caps['remote_restart'])
        self.assertTrue(caps['remote_reboot'])
        self.assertTrue(caps['remote_shutdown'])
        self.assertTrue(caps['remote_ppp_restart'])
        self.assertFalse(caps['config_patch'])
        self.assertFalse(caps['processing'])
        self.assertFalse(caps['restart'])
        self.assertFalse(caps['shutdown'])
    def test_local_control_capabilities_need_neither_lease_nor_remote_grants(self):
        self.a.cfg['runtime_mode']='controlled'
        self.a.cfg['control'].update(
            config_patch_enabled=True,processing_enabled=True,restart_enabled=True,
            reboot_enabled=True,shutdown_enabled=True,ppp_restart_enabled=True,
            remote_commands_enabled=False)
        self.a.helper_status={
            'allow_config':True,'allow_lifecycle':True,
            'allow_reboot':True,'allow_shutdown':True,'allow_ppp_restart':True,
            'allow_remote_control':False,'settings_target_available':True,
            'lifecycle_target_available':True}
        caps=self.a.capabilities()
        self.assertNotIn('maintenance',caps)
        self.assertEqual(tuple(caps[key] for key in
                         ('config_patch','processing','restart','reboot','shutdown','ppp_restart')),
                         (True,True,True,True,True,True))
    def test_ground_settings_write_bypasses_root_capability_flag(self):
        self.a.cfg['runtime_mode']='read_only'
        self.a.cfg['control']['config_patch_enabled']=False
        result=self.a.commands.submit(self.request('config.patch',changes={'gain_db':20.7}),'ground-controller')
        self.assertEqual(result['stage'],'ACCEPTED',result)

    def test_ground_lifecycle_write_bypasses_root_capability_flags(self):
        self.a.cfg['runtime_mode']='read_only'
        self.a.cfg['control'].update(processing_enabled=False,restart_enabled=False)
        result=self.a.commands.submit(self.request('processing.set',desired='STOPPED'),'ground-controller')
        self.assertEqual(result['stage'],'ACCEPTED',result)

    def test_ground_stream_write_works_without_remote_grants(self):
        self.a.cfg['runtime_mode']='read_only'
        self.a.cfg['control']['remote_commands_enabled']=False
        self.a.helper_status={'allow_remote_control':False}
        self.assertTrue(self.a.capabilities()['remote_commands'])
        self.a.commands.start();self.addCleanup(self.a.commands.stop)
        request=self.request('stream.set',profile='balanced')
        accepted=self.a.commands.submit(request,'ground-controller')
        self.a.commands.queue.join()
        self.assertEqual(accepted['stage'],'ACCEPTED')
        self.assertEqual(self.a.journal.lookup(request['id'])['stage'],'APPLIED')
        self.assertEqual(self.a.profile,'balanced')

    def test_ground_ppp_restart_works_without_root_grants(self):
        self.a.cfg['runtime_mode']='read_only'
        self.a.cfg['control'].update(remote_commands_enabled=False,ppp_restart_enabled=False)
        self.a.helper_status={'allow_remote_control':False,'allow_ppp_restart':False}
        self.assertTrue(self.a.capabilities()['remote_ppp_restart'])
        self.a.commands.start();self.addCleanup(self.a.commands.stop)
        request=self.request('ppp.restart');observed=[]
        def helper(payload):
            observed.append((self.a.journal.lookup(request['id'])['stage'],payload))
            return {'requested':True,'service':'t900-ppp.service'}
        with mock.patch.object(self.a.commands,'_helper',side_effect=helper):
            accepted=self.a.commands.submit(request,'ground-controller')
            self.a.commands.queue.join()
        self.assertEqual(accepted['stage'],'ACCEPTED')
        self.assertEqual(observed,[('APPLYING',{'op':'ppp.restart','origin':'ground-controller'})])
        operation=self.a.journal.lookup(request['id'])
        self.assertEqual(operation['stage'],'PPP_RESTART_REQUESTED')
        self.assertTrue(operation['result']['accepted_by_systemd'])
    def test_ppp_restart_helper_timeout_stays_unknown(self):
        self.a.cfg['runtime_mode']='controlled'
        self.a.cfg['control'].update(remote_commands_enabled=True,ppp_restart_enabled=True)
        self.a.helper_status={'allow_remote_control':True,'allow_ppp_restart':True}
        self.a.commands.start();self.addCleanup(self.a.commands.stop)
        request=self.request('ppp.restart')
        with mock.patch.object(self.a.commands,'_helper',side_effect=TimeoutError('RPC_TIMEOUT')) as helper:
            accepted=self.a.commands.submit(request,'ground-controller')
            self.a.commands.queue.join()
            self.assertEqual(accepted['stage'],'ACCEPTED')
            self.assertEqual(self.a.journal.lookup(request['id'])['stage'],'OUTCOME_UNKNOWN')
            duplicate=self.a.commands.submit(request,'ground-controller')
        self.assertEqual(duplicate['stage'],'OUTCOME_UNKNOWN')
        helper.assert_called_once_with({'op':'ppp.restart','origin':'ground-controller'})

    def test_remote_payload_cannot_choose_ground_confirmation(self):
        request=self.request('ppp.restart',confirm_previous_unknown=True)
        result=self.a.commands.submit(request,'ground-controller')
        self.assertEqual(result['result']['error'],'UNKNOWN_COMMAND_FIELD')

    def test_unrecognized_actor_is_rejected(self):
        result=self.a.commands.submit(self.request('stream.set',profile='balanced'),'untrusted-caller')
        self.assertEqual(result['result']['error'],'INVALID_COMMAND_ACTOR')

class AuthTests(unittest.TestCase):
    def test_session_expiry_slides_on_authenticated_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'admin.json';set_pin(path,'381604');auth=Auth(path,seconds=10)
            with mock.patch('rdf_node.api.time.monotonic',return_value=0):
                token,csrf=auth.login('381604')
            cookie=f'rdf_session={token}'
            with mock.patch('rdf_node.api.time.monotonic',return_value=9):
                self.assertEqual(auth.session(cookie),(token,csrf))
            with mock.patch('rdf_node.api.time.monotonic',return_value=15):
                self.assertEqual(auth.session(cookie),(token,csrf))
            with mock.patch('rdf_node.api.time.monotonic',return_value=26):
                self.assertIsNone(auth.session(cookie))

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
        self.a=make_agent(self.path);self.addCleanup(self.a.journal.close)
        self.pin='381604';set_pin(Path(self.a.cfg['api']['admin_hash_file']),self.pin)
        self.server=Server(self.a,self.a.cfg);self.port=self.server.server_port
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.shutdown);self.cookie='';self.csrf=''
    def shutdown(self):self.server.shutdown();self.server.server_close();self.thread.join(2)
    def http(self,method,path,obj=None,headers=None,raw=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        h={'Origin':f'http://127.0.0.1:{self.port}','Content-Type':'application/json','Cookie':self.cookie,'X-CSRF-Token':self.csrf}
        h.update(headers or {});conn.request(method,path,body=raw if raw is not None else compact(obj) if obj is not None else None,headers=h)
        r=conn.getresponse();data=r.read();out=(r.status,dict(r.getheaders()),data);conn.close();return out
    def login(self):
        code,h,b=self.http('POST','/api/v2/login',{'pin':self.pin});self.assertEqual(code,200)
        self.cookie=h['Set-Cookie'].split(';')[0];self.csrf=json.loads(b)['csrf']
    def mqtt_payload(self,**changes):
        body={'enabled':False,'host':'127.0.0.1','port':8883,'client_id':'rdf-pi','transport':'tcp','tls':True,'websocket_path':'/mqtt',
              'control':{'username':'control-user','password':'control-secret'},
              'bulk':{'username':'bulk-user','password':'bulk-secret'}}
        body.update(changes);return body
    def assert_plain_mqtt_settings_connect(self,transport):
        broker=Broker(auth={'control-user':'control-secret','bulk-user':'bulk-secret'},
                      websocket=transport=='websocket')
        self.addCleanup(broker.stop)
        self.a.monitor=FakeMonitor(self.a.cfg);self.a.start();self.addCleanup(self.a.stop)
        self.login()
        body=self.mqtt_payload(enabled=True,host='127.0.0.1',port=broker.port,tls=False,transport=transport)
        code,_,response=self.http('POST','/api/v2/mqtt/settings',body)
        self.assertEqual(code,200,response)
        saved=json.loads(response)
        self.assertEqual((saved['transport'],saved['tls'],saved['trust_mode']),(transport,False,'plaintext'))
        stored=json.loads((Path(self.a.cfg['state_dir'])/'mqtt-ui-settings.json').read_bytes())
        self.assertEqual((stored['schema'],stored['tls']),(3,False))
        self.assertTrue(wait(lambda:len(broker.clients)==2 and all(c.ready for c in self.a.clients.values()),8),
                        [broker.errors,{k:v.status() for k,v in self.a.clients.items()}])
        self.assertEqual(broker.mqtt_connect_count,2)
        self.assertFalse(broker.errors)
    def test_plain_tcp_settings_connect_with_username_password(self):
        self.assert_plain_mqtt_settings_connect('tcp')
    def test_plain_websocket_settings_connect_with_username_password(self):
        self.assert_plain_mqtt_settings_connect('websocket')
    def test_plain_tcp_settings_connect_without_credentials(self):
        broker=Broker(auth={'':''})
        self.addCleanup(broker.stop)
        self.a.monitor=FakeMonitor(self.a.cfg);self.a.start();self.addCleanup(self.a.stop)
        self.login()
        body=self.mqtt_payload(enabled=True,host='127.0.0.1',port=broker.port,tls=False,
                               control={'username':'','password':''},bulk={'username':'','password':''})
        code,_,response=self.http('POST','/api/v2/mqtt/settings',body)
        self.assertEqual(code,200,response)
        saved=json.loads(response)
        self.assertFalse(saved['control_credentials_set']);self.assertFalse(saved['bulk_credentials_set'])
        self.assertTrue(wait(lambda:len(broker.clients)==2 and all(c.ready for c in self.a.clients.values()),8),
                        [broker.errors,{k:v.status() for k,v in self.a.clients.items()}])
        self.assertEqual(broker.mqtt_connect_count,2)
        self.assertTrue(all(not connection['connect_flags']&0xc0 for connection in broker.clients))
        self.assertFalse(broker.errors)
    def test_snapshot_no_secrets(self):
        code,h,b=self.http('GET','/api/v2/snapshot');self.assertEqual(code,200);self.assertNotIn(self.pin.encode(),b);self.assertNotIn(b'admin_hash_file',b);self.assertEqual(h['Cache-Control'],'no-store')
    def test_pending_shutdown_summary_includes_old_unknown_operation(self):
        request={'id':'old-shutdown','op':'system.shutdown.execute'}
        self.a.journal.accept(request,'test');self.a.journal.update(request['id'],'OUTCOME_UNKNOWN',{'reason':'lost ACK'})
        first=self.a.journal.lookup(request['id'])['updated_ms']+1
        with mock.patch('rdf_node.journal.now_ms',side_effect=iter(range(first,first+100))):
            for index in range(25):
                filler={'id':f'recent-{index}','op':'config.get'}
                self.a.journal.accept(filler,'test');self.a.journal.update(filler['id'],'APPLIED',{})
        code,_,body=self.http('GET','/api/v2/operations/pending-shutdowns')
        self.assertEqual((code,json.loads(body)),(200,{'pending':True}))
        self.a.journal.update(request['id'],'FAILED',{'error':'verified rejection'})
        code,_,body=self.http('GET','/api/v2/operations/pending-shutdowns')
        self.assertEqual((code,json.loads(body)),(200,{'pending':False}))
    def test_host_dns_rebinding_rejected(self):self.assertEqual(self.http('GET','/',headers={'Host':'attacker.invalid'})[0],403)
    def test_cors_write_rejected(self):self.assertEqual(self.http('POST','/api/v2/login',{'pin':self.pin},headers={'Origin':'https://attacker.invalid'})[0],403)
    def test_no_auth_write_rejected(self):self.assertEqual(self.http('POST','/api/v2/display/preferences',{'theme':'light'})[0],403)
    def test_local_reboot_requires_admin_and_edge_approval(self):
        request={'v':2,'id':'local-reboot','sid':self.a.sid,'boot':self.a.boot,
                 'issued_ms':now_ms(),'expires_ms':now_ms()+15000,
                 'base_rev':self.a.source.revision,'op':'system.reboot.prepare'}
        self.assertEqual(self.http('POST','/api/v2/commands',request)[0],403)
        self.login()
        code,_,response=self.http('POST','/api/v2/commands',request)
        self.assertEqual((code,json.loads(response)['result']['error']),(400,'CAPABILITY_DISABLED'))
        self.a.cfg['runtime_mode']='controlled';self.a.cfg['control']['reboot_enabled']=True
        request['id']='local-reboot-approved'
        code,_,response=self.http('POST','/api/v2/commands',request)
        self.assertEqual((code,json.loads(response)['stage']),(202,'ACCEPTED'))
    def test_wrong_pin_rejected(self):self.assertEqual(self.http('POST','/api/v2/login',{'pin':'000000'})[0],401)
    def test_login_requires_exact_ascii_pin_and_new_field(self):
        for body in ({'pin':'12345'},{'pin':'1234567'},{'pin':'１２３４５６'},{'password':self.pin}):
            self.assertEqual(self.http('POST','/api/v2/login',body)[0],401)
    def test_set_pin_requires_six_ascii_digits(self):
        for value in ('1','12345','1234567','１２３４５６'):
            with self.assertRaises(ValueError):set_pin(self.path/'invalid-pin-hash.json',value)
    def test_legacy_hash_requires_local_pin_rotation(self):
        path=Path(self.a.cfg['api']['admin_hash_file'])
        legacy=json.loads(path.read_bytes());legacy.pop('credential')
        path.write_bytes(compact(legacy))
        self.assertEqual(self.http('POST','/api/v2/login',{'pin':self.pin})[0],401)
    def test_missing_csrf_rejected(self):
        self.login();self.csrf='';self.assertEqual(self.http('POST','/api/v2/display/preferences',{'theme':'light'})[0],403)
    def test_authorized_preferences_persist_theme_accent_and_font(self):
        self.login()
        body={'theme':'light','accent':'blue','font':'mono'}
        code,_,response=self.http('POST','/api/v2/display/preferences',body)
        self.assertEqual(code,200)
        self.assertEqual(json.loads(response),{**self.a.cfg['display'],**body})
        self.assertEqual(self.a.journal.get('display'),{**self.a.cfg['display'],**body})
    def test_old_saved_preferences_receive_new_defaults(self):
        self.a.journal.set('display',{'theme':'light','blank_after_seconds':0})
        self.login()
        code,_,response=self.http('POST','/api/v2/display/preferences',{'accent':'amber'})
        self.assertEqual(code,200)
        self.assertEqual(json.loads(response)['font'],'system')
    def test_night_theme_persists_for_panel(self):
        self.login()
        code,_,response=self.http('POST','/api/v2/display/preferences',{'theme':'night'})
        self.assertEqual(code,200);self.assertEqual(json.loads(response)['theme'],'night')
        self.a._snapshot();self.assertEqual(self.a.snapshot()['config']['preferences']['theme'],'night')
    def test_panel_blank_choices_persist_and_non_integer_rejected(self):
        self.login()
        for seconds in (60,300,900,0):
            code,_,response=self.http('POST','/api/v2/display/preferences',{'blank_after_seconds':seconds})
            self.assertEqual(code,200);self.assertEqual(json.loads(response)['blank_after_seconds'],seconds)
        for value in ('300',300.5,-1,86401):
            self.assertEqual(self.http('POST','/api/v2/display/preferences',{'blank_after_seconds':value})[0],400)
        self.assertEqual(self.a.journal.get('display')['blank_after_seconds'],0)
    def test_angular_latest_matches_detection_sample_for_panel_spectrum(self):
        self.a._snapshot();detection=self.http('GET','/api/v2/snapshot')
        snap=json.loads(detection[2])['detection']
        code,_,body=self.http('GET','/api/v2/angular/latest');frame=json.loads(body)
        self.assertEqual(code,200)
        self.assertEqual((frame['q'],frame['live'],len(frame['values'])),(snap['q'],snap['valid'],360))
        self.assertEqual(snap['angle_convention'],'theta_mirror')
    def test_unknown_or_invalid_preference_rejected(self):
        self.login()
        for body in ({'run_shell':'true'},{'accent':'ultraviolet'},{'font':'comic-sans'},{'theme':'neon'}):
            self.assertEqual(self.http('POST','/api/v2/display/preferences',body)[0],400)
    def test_invalid_json_rejected(self):self.assertEqual(self.http('POST','/api/v2/login',raw=b'{bad')[0],400)
    def test_oversize_body_rejected(self):self.assertEqual(self.http('POST','/api/v2/login',raw=b' '*9000)[0],400)
    def test_healthz_does_not_imply_daq(self):
        code,h,b=self.http('GET','/api/v2/healthz');self.assertEqual(code,200);self.assertTrue(json.loads(b)['daq_health_not_implied'])
    def test_logout_revokes_session(self):
        self.login();self.assertEqual(self.http('POST','/api/v2/logout',{})[0],200);self.assertEqual(self.http('POST','/api/v2/display/preferences',{'theme':'light'})[0],403)

    def test_session_endpoint_refreshes_browser_cookie(self):
        self.login()
        code,headers,body=self.http('GET','/api/v2/session')
        self.assertEqual(code,200)
        self.assertEqual(json.loads(body),{'authenticated':True,'csrf':self.csrf})
        self.assertEqual(headers.get('Set-Cookie'),
                         f'{self.cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age=600')

    def test_mqtt_settings_require_admin_session_and_csrf(self):
        body=self.mqtt_payload()
        self.assertEqual(self.http('GET','/api/v2/mqtt/settings')[0],401)
        self.assertEqual(self.http('POST','/api/v2/mqtt/settings',body)[0],403)
        self.login();self.csrf=''
        self.assertEqual(self.http('POST','/api/v2/mqtt/settings',body)[0],403)
    def test_mqtt_settings_persist_separate_secrets_without_exposing_them(self):
        self.login()
        code,_,response=self.http('POST','/api/v2/mqtt/settings',self.mqtt_payload(transport='websocket',websocket_path='/mqtt?node=uav'))
        self.assertEqual(code,200)
        saved=json.loads(response)
        self.assertEqual((saved['host'],saved['port'],saved['client_id']),('127.0.0.1',8883,'rdf-pi'))
        self.assertEqual((saved['transport'],saved['websocket_path']),('websocket','/mqtt?node=uav'))
        self.assertNotIn(b'control-secret',response);self.assertNotIn(b'bulk-secret',response)
        state=Path(self.a.cfg['state_dir'])
        control=state/'mqtt-ui-control.json';bulk=state/'mqtt-ui-bulk.json'
        self.assertEqual(json.loads(control.read_bytes()),{'username':'control-user','password':'control-secret'})
        self.assertEqual(json.loads(bulk.read_bytes()),{'username':'bulk-user','password':'bulk-secret'})
        self.assertEqual(stat.S_IMODE(control.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(bulk.stat().st_mode),0o600)
        stored=(state/'mqtt-ui-settings.json').read_bytes()
        self.assertNotIn(b'control-secret',stored);self.assertNotIn(b'bulk-secret',stored)
        self.assertNotIn(b'control-secret',compact(self.a.snapshot()))
        code,_,response=self.http('GET','/api/v2/mqtt/settings')
        self.assertEqual(code,200);self.assertNotIn(b'control-secret',response);self.assertNotIn(b'bulk-secret',response)
        self.a.journal.close()
        restored=Agent(copy.deepcopy(self.a.cfg))
        try:
            view=restored.mqtt_settings_view()
            self.assertEqual((view['host'],view['port'],view['client_id']),('127.0.0.1',8883,'rdf-pi'))
            self.assertTrue(view['control_credentials_set']);self.assertTrue(view['bulk_credentials_set'])
            self.assertNotIn('password',view)
        finally:restored.journal.close()

    def test_mqtt_settings_schema1_migrates_to_tcp_defaults(self):
        legacy={'schema':1,'enabled':False,'host':'ground.local','port':8883,'client_id':'legacy-node',
                'control_custom':False,'bulk_custom':False}
        self.a.mqtt_state_path.write_bytes(compact(legacy))
        settings=self.a._load_mqtt_settings()
        self.assertEqual((settings['transport'],settings['websocket_path'],settings['tls']),('tcp','/mqtt',True))
    def test_mqtt_settings_schema2_migrates_tls_from_config(self):
        previous={'schema':2,'enabled':False,'host':'ground.local','port':8883,'client_id':'legacy-node',
                  'transport':'websocket','websocket_path':'/mqtt','control_custom':False,'bulk_custom':False}
        self.a.mqtt_state_path.write_bytes(compact(previous))
        settings=self.a._load_mqtt_settings()
        self.assertEqual((settings['transport'],settings['websocket_path'],settings['tls']),
                         ('websocket','/mqtt',True))
    def test_mqtt_settings_preserve_blank_pairs_and_reject_partial_credentials(self):
        self.login()
        code,_,_=self.http('POST','/api/v2/mqtt/settings',self.mqtt_payload())
        self.assertEqual(code,200)
        control=Path(self.a.cfg['state_dir'])/'mqtt-ui-control.json'
        original=control.read_bytes()
        body=self.mqtt_payload(host='ground.local',client_id='node-two',
                               control={'username':'','password':''},bulk={'username':'','password':''})
        code,_,_=self.http('POST','/api/v2/mqtt/settings',body)
        self.assertEqual(code,200);self.assertEqual(control.read_bytes(),original)
        body=self.mqtt_payload(control={'username':'new-user','password':''})
        self.assertEqual(self.http('POST','/api/v2/mqtt/settings',body)[0],400)
        self.assertEqual(control.read_bytes(),original)
        self.assertEqual(self.a.mqtt_settings_view()['host'],'ground.local')
    def test_mqtt_settings_allow_system_ca_and_anonymous_auth(self):
        self.login()
        self.assertEqual(self.http('POST','/api/v2/mqtt/settings',self.mqtt_payload(host='broker.local/path'))[0],400)
        empty=self.mqtt_payload(enabled=True,control={'username':'','password':''},bulk={'username':'','password':''})
        code,_,response=self.http('POST','/api/v2/mqtt/settings',empty)
        self.assertEqual(code,200,response)
        saved=json.loads(response)
        self.assertFalse(saved['control_credentials_set']);self.assertFalse(saved['bulk_credentials_set'])
        self.assertFalse(saved['ca_configured'])
        self.assertEqual(saved['trust_mode'],'system')
    def test_mqtt_settings_reject_websocket_path_injection(self):
        self.login()
        body=self.mqtt_payload(transport='websocket',websocket_path='/mqtt\r\nHost:attacker')
        code,_,response=self.http('POST','/api/v2/mqtt/settings',body)
        self.assertEqual(code,400)
        self.assertEqual(json.loads(response)['error'],'INVALID_MQTT_WEBSOCKET_PATH')

class EndToEndTests(unittest.TestCase):
    def test_telemetry_publishes_without_ground_consumer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);initial=make_agent(path);initial.journal.close()
            cfg=load_config()
            cfg['state_dir']=str(path/'state')
            cfg['source']['share_dir']=str(path/'share')
            cfg['source'].update(authority_verified=True,angle_verified=True)
            cfg['api']['admin_hash_file']=str(path/'admin.json');cfg['api']['port']=0
            broker=Broker();creds=path/'mqtt.json'
            creds.write_bytes(compact({'username':'test','password':'test-password'}))
            cfg['mqtt'].update(enabled=True,host='127.0.0.1',port=broker.port,tls=False,allow_insecure_loopback=True,
                               control_credentials_file=str(creds),bulk_credentials_file=str(creds))
            cfg['telemetry']['resume_stable_seconds']=1
            agent=Agent(cfg);agent.monitor=FakeMonitor(cfg)
            stop=threading.Event()
            def writer():
                index=1
                while not stop.wait(.25):
                    index+=1
                    atomic_write(path/'share'/'status.json',compact(status(idx=index)))
                    atomic_write(path/'share'/'DOA_value.html',csv_bytes(record()))
            thread=threading.Thread(target=writer,daemon=True);thread.start()
            agent.start()
            prefix='sdr/v2/uav-01/'
            def received(suffix):
                with broker.lock:
                    return [m for m in broker.messages if m['topic']==prefix+suffix]
            try:
                self.assertTrue(wait(lambda:received('telemetry/health') and
                                     received('telemetry/doa') and
                                     len(received('telemetry/angular'))>=2,15),
                                [agent.snapshot(),broker.errors])
                self.assertEqual(broker.mqtt_connect_count,2)
                health=received('telemetry/health');doa=received('telemetry/doa')
                angular=received('telemetry/angular')
                for messages in (health,doa,angular):
                    self.assertTrue(all(m['qos']==0 and not m['retain'] for m in messages))
                frame=json.loads(angular[0]['payload'])
                self.assertEqual((frame['encoding'],len(frame['values']),frame['flags']),('json',360,63))
                self.assertFalse(broker.errors)
            finally:
                stop.set();thread.join(2);agent.stop();broker.stop()
    def test_ground_settings_request_roundtrip_without_receipt(self):
        import shutil,ssl,subprocess
        if not shutil.which('openssl'): self.skipTest('openssl unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);initial=make_agent(path);cfg=initial.cfg;initial.journal.close()
            cert=path/'cert.pem';key=path/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),
                            '-out',str(cert),'-days','1','-subj','/CN=localhost',
                            '-addext','subjectAltName=IP:127.0.0.1'],
                           check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            tls_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls_context.load_cert_chain(cert,key)
            broker=Broker(tls_context=tls_context);credentials=path/'mqtt.json'
            credentials.write_bytes(compact({'username':'test','password':'test-password'}))
            cfg['mqtt'].update(enabled=True,host='127.0.0.1',port=broker.port,tls=True,ca_file=str(cert),
                               control_credentials_file=str(credentials),bulk_credentials_file=str(credentials))
            raw=bytes((32,10))+compact(settings())+bytes((10,))
            atomic_write(path/'share'/'settings.json',raw)
            agent=Agent(cfg);agent.monitor=FakeMonitor(cfg);agent.source.poll(force=True)
            ground_cfg=copy.deepcopy(cfg);ground_cfg['state_dir']=str(path/'ground')
            ground=Ground(ground_cfg)
            agent_started=False;ground_started=False
            try:
                agent.start();agent_started=True
                ground.start();ground_started=True
                self.assertTrue(wait(lambda:ground.client.ready and
                                     all(client.ready for client in agent.clients.values()) and
                                     ground.node_state.get('sid')==agent.sid and
                                     ground.node_state.get('boot')==agent.boot,15),
                                [agent.snapshot(),ground.snapshot(),broker.errors])
                request=ground.submit_command({'id':'ground-settings-e2e','op':'config.get'})
                self.assertEqual(request['stage'],'REQUESTED')
                self.assertTrue(wait(lambda:ground.settings_json==raw.decode('utf-8'),10),
                                [agent.snapshot(),ground.snapshot(),broker.errors])
                report=ground.config_view()['reported']
                self.assertEqual((report['id'],report['sid'],report['boot']),
                                 ('ground-settings-e2e',agent.sid,agent.boot))
                with broker.lock:
                    messages=list(broker.messages)
                    subscriptions=[topic for client in broker.clients for topic in client['subs']]
                response=next(message for message in messages
                              if message['topic']=='sdr/v2/uav-01/settings/reported')
                self.assertEqual(json.loads(response['payload'])['settings_json'],raw.decode('utf-8'))
                self.assertEqual((response['qos'],response['retain']),(1,False))
                self.assertFalse(any(message['topic'].endswith('/ground/receipt') for message in messages))
                self.assertFalse(any(topic.endswith('/ground/receipt') for topic in subscriptions))
                self.assertFalse(any(message['topic'].endswith('/config/reported') for message in messages))
                self.assertFalse(broker.errors)
            finally:
                if ground_started: ground.stop()
                if agent_started: agent.stop()
                broker.stop()


    def test_unverified_regular_telemetry_never_advances_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);initial=make_agent(path);cfg=initial.cfg;initial.journal.close()
            cfg['source'].update(authority_verified=False,angle_verified=False)
            broker=Broker();creds=path/'mqtt.json'
            creds.write_bytes(compact({'username':'test','password':'test-password'}))
            cfg['mqtt'].update(enabled=True,host='127.0.0.1',port=broker.port,tls=False,allow_insecure_loopback=True,
                               control_credentials_file=str(creds),bulk_credentials_file=str(creds))
            cfg['telemetry']['resume_stable_seconds']=1
            agent=Agent(cfg);agent.monitor=FakeMonitor(cfg)
            ground_cfg=copy.deepcopy(cfg);ground_cfg['state_dir']=str(path/'ground')
            ground=Ground(ground_cfg);stop=threading.Event()
            def writer():
                index=2
                while not stop.wait(.25):
                    index+=1
                    atomic_write(path/'share'/'status.json',compact(status(idx=index)))
                    atomic_write(path/'share'/'DOA_value.html',csv_bytes(record()))
            thread=threading.Thread(target=writer,daemon=True);thread.start()
            agent.start();ground.start()
            prefix='sdr/v2/uav-01/'
            def received(suffix):
                with broker.lock:
                    return [message for message in broker.messages if message['topic']==prefix+suffix]
            try:
                self.assertTrue(wait(lambda:
                    agent.snapshot().get('link',{}).get('mqtt_topic_delivery',{}).get('telemetry/doa',{}).get('state')=='SENT' and
                    agent.snapshot().get('link',{}).get('mqtt_topic_delivery',{}).get('telemetry/angular',{}).get('state')=='SENT' and
                    (ground.snapshot().get('diagnostic_doa') or {}).get('source')=='DOA_value.html' and
                    ground.diagnostic_angular_view() is not None,20),
                    [agent.snapshot(),ground.snapshot(),broker.errors])
                doa=json.loads(received('telemetry/doa')[-1]['payload'])
                self.assertEqual((doa['ok'],doa['trust'],doa['angle_reference'],doa['a']),
                                 (0,'UNVERIFIED','RAW',10.0))
                frame=json.loads(received('telemetry/angular')[-1]['payload'])
                self.assertEqual((frame['encoding'],len(frame['values']),frame['flags']),('json',360,23))
                self.assertTrue(wait(lambda:
                    ground.diagnostic_angular_view() is not None and
                    ground.diagnostic_angular_view()['q']>=frame['q'],5),
                    [frame,ground.diagnostic_angular_view()])
                self.assertEqual(ground.diagnostic_angular_view()['trust'],'UNVERIFIED')
                self.assertIsNone(ground.angular_view())
                self.assertEqual(ground.doa,{})
                self.assertFalse(ground.snapshot()['detection']['valid'])
                self.assertFalse(agent.snapshot()['detection']['valid'])
                for suffix in ('telemetry/doa','telemetry/angular'):
                    self.assertTrue(received(suffix))
                    self.assertTrue(all(message['qos']==0 and not message['retain']
                                        for message in received(suffix)))
                self.assertFalse(broker.errors)
            finally:
                stop.set();thread.join(2);agent.stop();ground.stop();broker.stop()

    def test_agent_graph_query_and_stopped_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);a=make_agent(path)
            xml_timestamp=now_ms()
            atomic_write(path/'share'/'doa.xml',
                         f'<DATA><TIME>{xml_timestamp}</TIME><FREQUENCY>137.0</FREQUENCY><DOA>200.0</DOA></DATA>'.encode())
            # Recreate the agent with network enabled; fixtures never target a real node.
            a.journal.close();cfg=a.cfg
            b=Broker();creds=path/'mqtt.json';creds.write_bytes(compact({'username':'test','password':'test-password'}))
            cfg['mqtt'].update(enabled=True,host='127.0.0.1',port=b.port,tls=False,allow_insecure_loopback=True,
                               control_credentials_file=str(creds),bulk_credentials_file=str(creds))
            cfg['telemetry']['resume_stable_seconds']=1 # only stabilization wait accelerated for test
            a=Agent(cfg);a.monitor=FakeMonitor(cfg)
            gc=copy.deepcopy(cfg);gc['state_dir']=str(path/'ground');g=Ground(gc)
            stop=threading.Event()
            def writer():
                i=3
                while not stop.wait(.25):
                    i+=1;atomic_write(path/'share'/'status.json',compact(status(idx=i)))
                    atomic_write(path/'share'/'DOA_value.html',csv_bytes(record()))
            diagnostic_offers=[];diagnostic_offers_lock=threading.Lock()
            control_client=a.clients['control'];original_offer=control_client.offer
            def record_offer(key,topic,payload,**kwargs):
                if topic.endswith('/telemetry/diagnostic/doa'):
                    with diagnostic_offers_lock: diagnostic_offers.append(time.monotonic())
                return original_offer(key,topic,payload,**kwargs)
            control_client.offer=record_offer
            thread=threading.Thread(target=writer,daemon=True);thread.start();a.start();g.start()
            try:
                self.assertTrue(wait(lambda:g.snapshot()['health_fresh'],12),[a.snapshot(),g.snapshot(),b.errors])
                self.assertTrue(wait(lambda:a.snapshot().get('link',{}).get('mqtt_topic_delivery',{}).get('telemetry/health',{}).get('state')=='SENT',8),a.snapshot())
                health_delivery=a.snapshot()['link']['mqtt_topic_delivery']['telemetry/health']
                self.assertEqual((health_delivery['qos'],health_delivery['confirmation']),(0,'SOCKET_WRITE'))
                self.assertIsNone(health_delivery['error'])
                self.assertTrue(wait(lambda:a.snapshot().get('link',{}).get('mqtt_topic_delivery',{}).get('state',{}).get('state')=='SENT',8),a.snapshot())
                state_delivery=a.snapshot()['link']['mqtt_topic_delivery']['state']
                self.assertEqual((state_delivery['qos'],state_delivery['confirmation']),(1,'PUBACK'))
                self.assertIsNone(state_delivery['error'])
                self.assertTrue(wait(lambda:g.angular_view() is not None,20),[a.snapshot(),g.snapshot(),b.errors])
                self.assertEqual(len(g.angular_view()['values']),360)
                self.assertTrue(wait(lambda:g.snapshot()['detection']['valid'],5),
                                [a.snapshot(),g.snapshot(),b.errors])
                self.assertTrue(wait(lambda:g.snapshot().get('diagnostic_doa') is not None,5),
                                [a.snapshot(),g.snapshot(),b.errors])
                first_diagnostic=g.snapshot()['diagnostic_doa']
                self.assertTrue(wait(lambda:g.snapshot()['diagnostic_doa']['q']>first_diagnostic['q'],5),
                                [first_diagnostic,g.snapshot(),b.errors])
                with diagnostic_offers_lock:
                    self.assertGreaterEqual(len(diagnostic_offers),2)
                    diagnostic_interval=diagnostic_offers[1]-diagnostic_offers[0]
                self.assertGreaterEqual(diagnostic_interval,2.5)
                repeated_diagnostic=g.snapshot()['diagnostic_doa']
                self.assertEqual(repeated_diagnostic['raw_doa_deg'],200)
                self.assertEqual(repeated_diagnostic['source_timestamp_ms'],xml_timestamp)
                self.assertTrue(g.snapshot()['detection']['valid'])
                oldq=g.health['q'];stop.set();thread.join(2);a.monitor.data.update(service_state='INACTIVE',cgroup_empty=True)
                # Local status file ceases too; bridge still generates fresh health.
                self.assertTrue(wait(lambda:g.health.get('run')==0 and g.health.get('q',0)>oldq,8))
                self.assertTrue(g.angular_view()['stale'])
                self.assertIn('DAQ_OR_PROCESSING_NOT_HEALTHY',g.angular_view()['reasons'])
                self.assertFalse(b.errors)
            finally:
                stop.set();thread.join(2);a.stop();g.stop();b.stop()
    def test_invalid_csv_flows_only_through_diagnostic_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);initial=make_agent(path);initial.journal.close();cfg=initial.cfg
            timestamp=now_ms()
            atomic_write(path/'share'/'DOA_value.html',b'')
            atomic_write(path/'share'/'doa.xml',
                         f'<DATA><TIME>{timestamp}</TIME><FREQUENCY>137.0</FREQUENCY><DOA>200.0</DOA></DATA>'.encode())
            broker=Broker();creds=path/'mqtt.json'
            creds.write_bytes(compact({'username':'test','password':'test-password'}))
            cfg['mqtt'].update(enabled=True,host='127.0.0.1',port=broker.port,tls=False,allow_insecure_loopback=True,
                               control_credentials_file=str(creds),bulk_credentials_file=str(creds))
            cfg['telemetry']['resume_stable_seconds']=1
            agent=Agent(cfg);agent.monitor=FakeMonitor(cfg);agent.source.poll(force=True)
            ground_cfg=copy.deepcopy(cfg);ground_cfg['state_dir']=str(path/'ground-diagnostic')
            ground=Ground(ground_cfg);stop=threading.Event()
            def writer():
                index=3
                while not stop.wait(.25):
                    index+=1;atomic_write(path/'share'/'status.json',compact(status(idx=index)))
            thread=threading.Thread(target=writer,daemon=True);thread.start()
            agent.start();ground.start()
            try:
                self.assertTrue(wait(lambda:ground.snapshot().get('diagnostic_doa') is not None,12),
                                [agent.snapshot(),ground.snapshot(),broker.errors])
                self.assertTrue(wait(lambda:agent.snapshot().get('link',{}).get('mqtt_topic_delivery',{}).get(
                    'telemetry/diagnostic/doa',{}).get('state')=='SENT',8),agent.snapshot())
                diagnostic=ground.snapshot()['diagnostic_doa']
                self.assertEqual((diagnostic['raw_doa_deg'],diagnostic['frequency_mhz']),(200,137))
                self.assertIn('EMPTY_CSV',diagnostic['validation_reasons'])
                initial_q=diagnostic['q']
                self.assertTrue(wait(lambda:ground.snapshot().get('diagnostic_doa',{}).get('q',0)>initial_q,5),
                                [diagnostic,ground.snapshot(),broker.errors])
                repeated=ground.snapshot()['diagnostic_doa']
                self.assertEqual(repeated['source_timestamp_ms'],diagnostic['source_timestamp_ms'])
                self.assertEqual(repeated['raw_doa_deg'],diagnostic['raw_doa_deg'])
                self.assertFalse(ground.snapshot()['detection']['valid']);self.assertEqual(ground.doa,{})
                self.assertNotEqual(agent.snapshot()['link']['mqtt_topic_delivery'].get(
                    'telemetry/doa',{}).get('state'),'SENT')
                self.assertFalse(broker.errors)
            finally:
                stop.set();thread.join(2);agent.stop();ground.stop();broker.stop()

    def test_stopped_angles_flow_as_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);initial=make_agent(path);cfg=initial.cfg;initial.journal.close()
            timestamp=now_ms();bad_status=status(timestamp,idx=4);bad_status['daq_ok']=False
            atomic_write(path/'share'/'status.json',compact(bad_status))
            angle_record=record(timestamp);angle_record[-1]=50000
            atomic_write(path/'share'/'DOA_value.html',csv_bytes(angle_record))
            broker=Broker();creds=path/'mqtt.json'
            creds.write_bytes(compact({'username':'test','password':'test-password'}))
            cfg['mqtt'].update(enabled=True,host='127.0.0.1',port=broker.port,tls=False,allow_insecure_loopback=True,
                               control_credentials_file=str(creds),bulk_credentials_file=str(creds))
            agent=Agent(cfg);agent.monitor=FakeMonitor(cfg)
            agent.monitor.data.update(service_state='INACTIVE',cgroup_empty=True)
            agent.source.poll(force=True);agent._snapshot()
            ground_cfg=copy.deepcopy(cfg);ground_cfg['state_dir']=str(path/'ground-stopped')
            ground=Ground(ground_cfg)
            agent.start();ground.start()
            try:
                self.assertTrue(wait(lambda:getattr(ground,'diagnostic_angular',None) is not None,10),
                                [agent.snapshot(),ground.snapshot(),broker.errors])
                diagnostic=ground.diagnostic_angular_view()
                self.assertEqual(diagnostic['timestamp_ms'],timestamp)
                self.assertEqual((len(diagnostic['values']),diagnostic['raw_doa_deg']),(360,10.0))
                self.assertAlmostEqual(diagnostic['values'][0],-10.0,places=2)
                self.assertEqual(diagnostic['encoding'],'json')
                self.assertAlmostEqual(diagnostic['values'][-1],50000.0,places=2)
                self.assertEqual(diagnostic['trust'],'UNVERIFIED')
                self.assertIn('DAQ_NOT_HEALTHY_AT_EDGE',diagnostic['validation_reasons'])
                self.assertFalse(ground.snapshot()['detection']['valid'])
                self.assertIsNone(ground.angular_view())
                self.assertFalse(broker.errors)
            finally:
                agent.stop();ground.stop();broker.stop()
