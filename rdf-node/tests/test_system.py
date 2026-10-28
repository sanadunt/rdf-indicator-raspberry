import copy,http.client,json,os,socket,stat,tempfile,threading,time,unittest,uuid
from pathlib import Path
from unittest import mock
from rdf_node.agent import Agent
from rdf_node.ground import Ground
from rdf_node.mqtt import Client,Message
from rdf_node.config import load_config
from rdf_node.api import Auth,Server,set_pin
from rdf_node.util import now_ms,compact,atomic_write
from rdf_node.monitor import Monitor
from rdf_node.control import OPS
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
            ('unavailable', 'SETTINGS_UNAVAILABLE'),
            ('stale', 'SETTINGS_STALE'),
            ('oversized', 'SETTINGS_REPORT_TOO_LARGE'),
        )
        for case,error in cases:
            agent.cfg['mqtt']['tls']=True;agent.source.config_error=None
            agent.source.raw_settings=compact(settings())
            agent.source.settings_seen=time.monotonic()
            if case=='unavailable':
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

    def test_config_get_answers_on_plaintext_primary(self):
        # mqtt.tls false is the operator's trusted PPP/T900 link; telemetry and config.patch already use it.
        agent=self.a;agent.cfg['mqtt']['tls']=False
        (agent.source.path/'settings.json').write_bytes(compact(settings()));agent.source.poll(force=True)
        result=agent.commands.submit(self.request(),'ground-controller',topic=agent.prefix+'/cmd/config/get')
        self.assertEqual(result['stage'],'APPLIED',result)
        self.assertEqual((agent.settings_request_id,agent.settings_request_link),(result['id'],'primary'))

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
        self.a.helper_status={'allow_reboot':True}
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
        ground.receive(ground.prefix+'/telemetry/diagnostic/doa',compact(older))
        self.assertEqual(ground.snapshot()['diagnostic_doa']['q'],2)
        unlabelled={k:v for k,v in dict(sample,q=4,source_timestamp_ms=timestamp+2000).items() if k!='validation_reasons'}
        ground.receive(ground.prefix+'/telemetry/diagnostic/doa',compact(unlabelled))
        self.assertEqual(ground.snapshot()['diagnostic_doa']['validation_reasons'],['DIAGNOSTIC_UNVERIFIED'])

    def test_ground_shows_labelled_doa_instead_of_dropping_it(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground-labelled')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        sid='12ab34cd';t=now_ms()
        ground.receive(ground.prefix+'/state',compact({'v':2,'sid':sid,'boot':'boot-test','instance':'test','cfg':7}))
        ground.receive(ground.prefix+'/telemetry/health',compact(
            {'v':2,'sid':sid,'q':1,'t':t,'run':1,'daq':0,'drop':0,'age':9000,'temp':None,'clk':0,'rev':7,
             'why':['DAQ_NOT_HEALTHY','CLOCK_UNTRUSTED']}))
        # Edge 1.4+: a stale, clock-untrusted sample is labelled ok=0 with its reasons.
        labelled={'v':2,'sid':sid,'q':1,'t':t-60000,'f':437425000,'a':123.4,'c':3.1,'p':-80.0,'rev':7,'ok':0,
                  'clk':0,'age':60000,'reason':['DAQ_NOT_HEALTHY','CLOCK_UNTRUSTED','NO_FRESH_DOA'],
                  'trust':'UNVERIFIED','angle_reference':'RAW'}
        ground.receive(ground.prefix+'/telemetry/doa',compact(labelled))
        shown=ground.snapshot()['diagnostic_doa']
        self.assertEqual((shown['raw_doa_deg'],shown['trust'],shown['clk'],shown['source_age_ms']),(123.4,'UNVERIFIED',0,60000))
        self.assertEqual(shown['validation_reasons'],['DAQ_NOT_HEALTHY','CLOCK_UNTRUSTED','NO_FRESH_DOA','SOURCE_STALE'])
        self.assertTrue(shown['stale']);self.assertFalse(ground.snapshot()['detection']['valid'])
        # ok=1 that Ground cannot confirm (DAQ not healthy here) is shown with its relative angle, not LIVE.
        ground.receive(ground.prefix+'/telemetry/doa',compact(dict(labelled,q=2,t=now_ms(),ok=1,reason=[])))
        shown=ground.snapshot()['diagnostic_doa']
        self.assertEqual((shown['relative_doa_deg'],shown['raw_doa_deg'],shown['angle_reference']),(123.4,None,'RELATIVE'))
        self.assertEqual(shown['validation_reasons'],['DAQ_NOT_HEALTHY'])
        self.assertEqual(ground.doa,{});self.assertEqual(ground.rejected,0)
        # What cannot be read at all is still counted, with its reason.
        ground._incoming(ground.prefix+'/telemetry/doa',b'not json',False)
        worker=threading.Thread(target=ground._loop,daemon=True);worker.start()
        ground.events.join();ground.stop_event.set();worker.join(2)
        self.assertEqual(ground.rejected,1)
        self.assertEqual(ground.snapshot()['link']['reject_reasons'],{'JSONDECODEERROR':1})

    def test_ground_keeps_health_with_a_wrong_clock_but_never_trusts_it(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground-skewed')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        ground.client=mock.Mock(ready=True);ground.client.offer.return_value=True
        sid='12ab34cd'
        ground.receive(ground.prefix+'/state',compact({'v':2,'sid':sid,'boot':'boot-test','instance':'test','cfg':7}))
        ground.receive(ground.prefix+'/telemetry/health',compact(
            {'v':2,'sid':sid,'q':1,'t':now_ms()-3600000,'run':1,'daq':1,'drop':0,'age':0,'temp':None,'clk':0,'rev':7}))
        snapshot=ground.snapshot()
        self.assertEqual(snapshot['health']['q'],1)
        self.assertFalse(snapshot['health_fresh']);self.assertFalse(snapshot['health_time_ok'])
        self.assertEqual(ground.submit_command({'op':'stream.set','profile':'balanced'})['error'],'NODE_NOT_FRESH')

    def test_ground_accepts_settings_report_with_extra_fields(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground-settings-extra')
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        sid='12ab34cd';boot='boot-current'
        ground.receive(ground.prefix+'/state',compact({'v':2,'sid':sid,'boot':boot,'instance':'test','cfg':7}))
        ground.settings_request_id='settings-1';raw=compact(settings()).decode('utf-8')
        ground.receive(ground.prefix+'/settings/reported',compact(
            {'v':2,'sid':sid,'boot':boot,'id':'settings-1','rev':7,'t':now_ms(),'settings_json':raw,'contract':'1.6'}))
        self.assertEqual(ground.config_view()['settings_json'],raw)
        self.assertNotIn('contract',ground.config_view()['reported'])

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
        # Without fresh health or with incomplete evidence a frame is shown labelled, never LIVE.
        ground.receive(ground.prefix+'/telemetry/angular',compact(dict(payload,q=3)))
        self.assertIn('HEALTH_STALE',ground.diagnostic_angular_view()['validation_reasons'])
        ground.health_seen=time.monotonic()
        ground.receive(ground.prefix+'/telemetry/angular',compact(dict(payload,q=4,flags=7)))
        self.assertIn('CONFIG_ATTRIBUTION_UNVERIFIED',ground.diagnostic_angular_view()['validation_reasons'])
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
        incomplete=dict(payload,q=2,flags=27)
        ground.receive(ground.prefix+'/telemetry/angular',compact(incomplete))
        view=ground.diagnostic_angular_view()
        self.assertEqual((view['q'],view['trust']),(2,'UNVERIFIED'))
        self.assertIn('DAQ_NOT_HEALTHY_AT_EDGE',view['validation_reasons'])
        self.assertIsNone(ground.angular_view())

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
        self.assertEqual((doa['reason'],doa['clk'],doa['age']),
                         (['SOURCE_UNVERIFIED','ANGLE_UNVERIFIED'],1,snapshot['detection']['source_age_ms']))
        angular=json.loads(next(payload for key,topic,payload in offers if key=='angular'))
        self.assertEqual((angular['encoding'],angular['flags'],len(angular['values'])),('json',23,360))
        self.assertEqual(angular['values'],agent.source.record['values'])
        agent.last_doa_q=0;agent.last_angular_q=0
        blocked=copy.deepcopy(snapshot);blocked['detection']['reasons'].append('NO_FRESH_DOA')
        blocked_offers=self._run_agent_scheduler_once(agent,blocked)
        blocked_keys=[key for key,topic,payload in blocked_offers]
        # A blocked sample still goes out, labelled; only the Bulk Angular frame waits for an eligible source.
        doa=next(payload for key,topic,payload in blocked_offers if key=='doa')
        self.assertEqual((doa['ok'],doa['trust'],doa['angle_reference']),(0,'UNVERIFIED','RAW'))
        self.assertEqual(doa['reason'],['SOURCE_UNVERIFIED','ANGLE_UNVERIFIED','NO_FRESH_DOA'])
        self.assertNotIn('validation_reasons',doa)
        self.assertNotIn('angular',blocked_keys)
    def test_blocked_doa_is_published_with_clock_age_and_reasons(self):
        agent=self.a;agent.profile='balanced';snapshot=agent.snapshot()
        self.assertTrue(snapshot['detection']['valid'])
        valid=next(p for k,t,p in self._run_agent_scheduler_once(agent,snapshot) if k=='doa')
        self.assertEqual((valid['ok'],valid['clk'],valid['reason']),(1,1,[]))
        self.assertNotIn('trust',valid);self.assertNotIn('validation_reasons',valid)
        untrusted=copy.deepcopy(snapshot);untrusted['host']['clock_trusted']=False
        untrusted['detection'].update(valid=False,relative_doa_deg=None,reasons=['CLOCK_UNTRUSTED'])
        agent.last_doa_q=0
        doa=next(p for k,t,p in self._run_agent_scheduler_once(agent,untrusted) if k=='doa')
        self.assertEqual((doa['ok'],doa['clk'],doa['reason'],doa['a']),
                         (0,0,['CLOCK_UNTRUSTED'],round(snapshot['detection']['raw_doa_deg'],2)))
        self.assertEqual(doa['age'],snapshot['detection']['source_age_ms'])
        self.assertNotIn('validation_reasons',doa)

    def test_doa_is_not_resent_without_a_new_sample_and_health_says_why(self):
        agent=self.a;agent.profile='balanced';snapshot=agent.snapshot()
        self.assertIn('doa',[k for k,t,p in self._run_agent_scheduler_once(agent,snapshot)])
        stale=copy.deepcopy(snapshot)
        stale['detection'].update(valid=False,relative_doa_deg=None,reasons=['EMPTY_CSV','NO_FRESH_DOA'])
        offers=self._run_agent_scheduler_once(agent,stale)
        self.assertNotIn('doa',[k for k,t,p in offers])
        health=next(p for k,t,p in offers if k=='health')
        self.assertEqual(health['why'],['EMPTY_CSV','NO_FRESH_DOA'])
        self.assertEqual(next(p for k,t,p in self._run_agent_scheduler_once(agent,snapshot) if k=='health')['why'],[])

    def test_one_failing_topic_does_not_stop_the_others(self):
        agent=self.a;agent.profile='balanced';snapshot=agent.snapshot()
        def broken(d,h): raise RuntimeError('unexpected source value')
        agent._doa_payload=broken
        keys=[k for k,t,p in self._run_agent_scheduler_once(agent,snapshot)]
        self.assertTrue({'health','state','diagnostic-angular'}<=set(keys) or {'health','state','angular'}<=set(keys),keys)
        self.assertNotIn('doa',keys)
        self.assertEqual(agent.publish_errors,{'telemetry/doa':'RUNTIMEERROR'})
        detail=next(p for k,t,p in self._run_agent_scheduler_once(agent,snapshot) if k=='detail')
        self.assertEqual(detail['perr'],['telemetry/doa'])
        agent._snapshot();self.assertEqual(agent.snapshot_data['link']['publish_errors'],{'telemetry/doa':'RUNTIMEERROR'})
        del agent._doa_payload;agent.last_doa_q=0
        self.assertIn('doa',[k for k,t,p in self._run_agent_scheduler_once(agent,snapshot)])
        self.assertEqual(agent.publish_errors,{})

    def test_diagnostic_angular_frames_are_never_back_to_back(self):
        # A state flip right after (re)connect used to send one frame per schedule within half a second.
        agent=self.a;agent.profile='balanced';valid=agent.snapshot()
        invalid=copy.deepcopy(valid);invalid['detection'].update(valid=False,relative_doa_deg=None,reasons=['DAQ_NOT_HEALTHY'])
        control=Client(cfg={},client_id='spacing-control');bulk=Client(cfg={},client_id='spacing-bulk')
        control.ready=bulk.ready=True;agent.clients={'control':control,'bulk':bulk};agent._generation=0
        agent.bulk_resume_after=time.monotonic()+60;offers=[];snaps=[invalid,valid,valid]
        agent._offer=lambda *a,**k:offers.append(a[0]) or True
        def next_snapshot():
            snap=snaps.pop(0)
            if not snaps:agent.stop_event.set()
            return snap
        agent.snapshot=next_snapshot;agent.stop_event.clear();agent._schedule()
        self.assertEqual(offers.count('diagnostic-angular'),1,offers)

    def test_diagnostic_angular_that_expired_unsent_is_offered_again(self):
        # An expired frame never used the link, so the next one must not wait a whole budget period.
        agent=self.a;agent.profile='balanced';invalid=agent.snapshot()
        invalid['detection'].update(valid=False,relative_doa_deg=None,reasons=['DAQ_NOT_HEALTHY'])
        control=Client(cfg={},client_id='expiry-control');bulk=Client(cfg={},client_id='expiry-bulk')
        control.ready=bulk.ready=True;agent.clients={'control':control,'bulk':bulk};agent._generation=0
        agent.bulk_resume_after=time.monotonic()+60;offers=[];snaps=[invalid]*3
        def offer(key,*a,**k):
            offers.append(key)
            if key=='diagnostic-angular' and offers.count(key)==1:
                agent.mqtt_topic_delivery['telemetry/diagnostic/angular']={
                    'state':'ERROR','updated_ms':now_ms(),'error':'OUTBOX_EXPIRED'}
            return True
        agent._offer=offer
        def next_snapshot():
            snap=snaps.pop(0)
            if not snaps:agent.stop_event.set()
            return snap
        agent.snapshot=next_snapshot;agent.stop_event.clear();agent._schedule()
        self.assertEqual(offers.count('diagnostic-angular'),2,offers)

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
        ground.health={'q':1,'t':now_ms()};ground.health_seen=time.monotonic()
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
        ground.health={'q':1,'t':now_ms()};ground.health_seen=time.monotonic()
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

class LaptopTests(unittest.TestCase):
    """Testing without a Pi: the demo source and the explicit TEST clock."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(os.path.realpath(self.tmp.name))
    def test_demo_source_writes_what_the_edge_and_ground_console_read(self):
        from rdf_node.cli import generate_demo
        from rdf_node.source import parse_status,parse_doa_xml
        stop=threading.Event();source=self.path/'source'
        thread=threading.Thread(target=generate_demo,args=(source,stop),daemon=True);thread.start()
        self.assertTrue(wait(lambda:(source/'doa.xml').exists(),3));stop.set();thread.join(3)
        status=json.loads((source/'status.json').read_bytes())
        self.assertIs(status['daq_status']['adc_overdrive'],False)
        self.assertIs(parse_status((source/'status.json').read_bytes())['adc_overdrive'],False)
        xml=parse_doa_xml((source/'doa.xml').read_bytes())
        self.assertEqual((xml['frequency_mhz'],xml['source_timestamp_ms']),(433.92,status['timestamp_ms']))
    def test_test_clock_lets_data_through_but_never_claims_sync(self):
        a=make_agent(self.path);self.addCleanup(a.journal.close)
        a.monitor.data.update(clock_trusted=False,clock_state='TEST')
        a.source.poll(force=True);a._snapshot();snap=a.snapshot()
        self.assertTrue(snap['detection']['valid'],snap['detection']['reasons'])
        self.assertEqual(a._state(snap)['clock'],'TEST')
        self.assertEqual(a._doa_payload(snap['detection'],snap['host'])['clk'],0)
        request=lambda op,**x:dict(v=2,id='lap-'+uuid.uuid4().hex,sid=a.sid,boot=a.boot,issued_ms=now_ms(),
                                    expires_ms=now_ms()+15000,base_rev=a.source.revision,op=op,**x)
        self.assertEqual(a.commands.submit(request('stream.set',profile='balanced'),'ground-controller')['stage'],'ACCEPTED')
        a.helper_status={'allow_lifecycle':False}
        rejected=a.commands.submit(request('processing.set',desired='STOPPED'),'ground-controller')
        self.assertEqual(rejected['result']['error'],'CLOCK_UNTRUSTED')

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
        # Unknown fields are dropped and named, never obeyed: the origin stays the broker it came from.
        request=self.request('stream.set',profile='balanced',origin='local-admin');acks=[]
        self.a.operation_changed=lambda id,op,stage,result,link=None:acks.append((stage,result))
        result=self.a.commands.submit(request,'ground-controller')
        self.assertEqual((result['stage'],result['result']),('ACCEPTED',{'ignored':['origin']}))
        self.assertEqual(acks[0],('ACCEPTED',{'ignored':['origin']}))
        operation=self.a.journal.lookup(request['id'])
        self.assertEqual(operation['link'],'ground');self.assertNotIn('origin',operation['request'])

    def test_dropped_commands_are_counted_shown_and_answered_when_possible(self):
        acks=[];self.a.operation_changed=lambda id,op,stage,result,link=None:acks.append((id,stage,result['error']))
        topic=self.a.prefix+'/'+OPS['stream.set']
        big=self.request('stream.set',profile='balanced',note='x'*5000)
        self.a._incoming(topic,compact(big),False)
        self.a._incoming(topic,b'{not json',False)
        self.a._incoming(topic,b'x'*70000,False)
        worker=threading.Thread(target=self.a._events,daemon=True);worker.start()
        self.a.events.join();self.a.stop_event.set();worker.join(2)
        self.assertEqual(acks,[(big['id'],'REJECTED','COMMAND_TOO_LARGE')])
        self.assertEqual(self.a.inbound_rejects,{'COMMAND_TOO_LARGE':2,'COMMAND_NOT_JSON':1})
        self.assertEqual([(r['op'],r['error']) for r in self.a.remote_rejects],
                         [('stream.set','COMMAND_NOT_JSON'),('stream.set','COMMAND_TOO_LARGE'),('stream.set','COMMAND_TOO_LARGE')])

    def test_unknown_fields_never_excuse_a_missing_safeguard(self):
        # A misspelled base_rev is ignored, so the real one is missing and the write is still refused.
        request=self.request('stream.set',profile='balanced',base_revision=self.a.source.revision)
        del request['base_rev']
        result=self.a.commands.submit(request,'ground-controller')
        self.assertEqual(result['result']['error'],'CONFIG_REVISION_CONFLICT')
        self.assertEqual(result['result']['ignored'],['base_revision'])

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
        self.a.helper_status={'allow_config':False}
        result=self.a.commands.submit(self.request('config.patch',changes={'gain_db':20.7}),'ground-controller')
        self.assertEqual(result['stage'],'ACCEPTED',result)

    def test_write_without_helper_is_rejected_up_front_with_a_reason(self):
        missing=str(self.path/'missing.sock');self.a.cfg['control']['helper_socket']=missing
        self.a.helper_status={};acks=[]
        self.a.operation_changed=lambda id,op,stage,result,link=None:acks.append((stage,result))
        request=self.request('config.patch',changes={'gain_db':20.7})
        result=self.a.commands.submit(request,'ground-controller')
        self.assertEqual((result['stage'],result['result']['error']),('REJECTED','HELPER_UNAVAILABLE'))
        self.assertIn('rdf-control-helper',result['result']['message'])
        self.assertIn(missing,result['result']['detail'])
        self.assertEqual(acks,[('REJECTED',result['result'])])
        self.assertIsNone(self.a.journal.lookup(request['id']))
        self.assertEqual(self.a.remote_rejects[0]['error'],'HELPER_UNAVAILABLE')
        # stream.set never needs the helper.
        self.assertEqual(self.a.commands.submit(self.request('stream.set',profile='balanced'),'ground-controller')['stage'],
                         'ACCEPTED')

    def test_helper_gone_after_accept_fails_instead_of_unknown(self):
        self.a.cfg['control']['helper_socket']=str(self.path/'missing.sock')
        self.a.helper_status={'allow_lifecycle':False}
        self.a.commands.start();self.addCleanup(self.a.commands.stop)
        request=self.request('processing.set',desired='STOPPED')
        self.assertEqual(self.a.commands.submit(request,'ground-controller')['stage'],'ACCEPTED')
        self.a.commands.queue.join()
        operation=self.a.journal.lookup(request['id'])
        self.assertEqual((operation['stage'],operation['result']['error']),('FAILED','HELPER_UNAVAILABLE'))
        self.assertIn('tidak dijalankan',operation['result']['message'])

    def test_unexpected_errors_get_a_code_message_and_detail(self):
        self.a.helper_status={'allow_lifecycle':False}
        self.a.commands.start();self.addCleanup(self.a.commands.stop)
        cases=((TimeoutError('timed out'),'OUTCOME_UNKNOWN','HELPER_TIMEOUT'),
               (BrokenPipeError(32,'Broken pipe'),'OUTCOME_UNKNOWN','HELPER_CONNECTION_LOST'),
               (RuntimeError('boom'),'FAILED','INTERNAL_ERROR'))
        for error,stage,code in cases:
            request=self.request('processing.set',desired='STOPPED')
            with self.subTest(code=code),mock.patch.object(self.a.commands,'_helper',side_effect=error):
                self.a.commands.submit(request,'ground-controller');self.a.commands.queue.join()
                result=self.a.journal.lookup(request['id'])['result']
                self.assertEqual((self.a.journal.lookup(request['id'])['stage'],result['error']),(stage,code))
                self.assertTrue(result['message']);self.assertIn(type(error).__name__,result['detail'])
        # A malformed field is named as such, not as a journal error.
        malformed=self.request('stream.set',profile='balanced');malformed['issued_ms']='soon'
        rejected=self.a.commands.submit(malformed,'ground-controller')
        self.assertEqual(rejected['result']['error'],'INVALID_COMMAND_FIELD')
        self.assertIn('ValueError',rejected['result']['detail'])

    def test_ground_lifecycle_write_bypasses_root_capability_flags(self):
        self.a.cfg['runtime_mode']='read_only'
        self.a.cfg['control'].update(processing_enabled=False,restart_enabled=False)
        self.a.helper_status={'allow_lifecycle':False}
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
            self.a.commands.join_ppp()
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
            self.a.commands.join_ppp()
            self.assertEqual(accepted['stage'],'ACCEPTED')
            self.assertEqual(self.a.journal.lookup(request['id'])['stage'],'OUTCOME_UNKNOWN')
            duplicate=self.a.commands.submit(request,'ground-controller')
        self.assertEqual(duplicate['stage'],'OUTCOME_UNKNOWN')
        helper.assert_called_once_with({'op':'ppp.restart','origin':'ground-controller'})

    def test_remote_payload_cannot_choose_ground_confirmation(self):
        self.a.helper_status={'allow_ppp_restart':False}
        self.a.commands.start();self.addCleanup(self.a.commands.stop)
        request=self.request('ppp.restart',confirm_previous_unknown=True)
        with mock.patch.object(self.a.commands,'_helper',return_value={'requested':True,'service':'t900-ppp.service'}):
            result=self.a.commands.submit(request,'ground-controller');self.a.commands.join_ppp()
        self.assertEqual((result['stage'],result['result']),('ACCEPTED',{'ignored':['confirm_previous_unknown']}))
        self.assertNotIn('confirm_previous_unknown',self.a.journal.lookup(request['id'])['request'])

    def test_unrecognized_actor_is_rejected(self):
        result=self.a.commands.submit(self.request('stream.set',profile='balanced'),'untrusted-caller')
        self.assertEqual(result['result']['error'],'INVALID_COMMAND_ACTOR')

class PppRecoveryTests(unittest.TestCase):
    """Local T900 restart lane, link recovery observer, and operator-visible command origin."""
    HELPER_OK={'requested':True,'service':'t900-ppp.service'}
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.a=make_agent(Path(os.path.realpath(self.tmp.name)));self.addCleanup(self.a.journal.close)
        a=self.a;a.cfg['runtime_mode']='controlled';a.cfg['control']['ppp_restart_enabled']=True
        a.helper_status={'allow_ppp_restart':True}
        self.ctrl=capture_client('cap-control');self.ctrl.generation=3;self.kicks=[]
        self.ctrl.kick=lambda:self.kicks.append(1)
        a.clients={'control':self.ctrl}
        self.service={'state':'ACTIVE','substate':'running','restarts':0,'since':'100'}
        a.monitor.data.update(ppp='UP',interface='ppp0',ppp_probe='REPLY',ppp_service=dict(self.service))
        a._snapshot();self.kicks.clear()
    def request(self,op='ppp.restart',**extras):
        request=dict(v=2,id='ppp-'+uuid.uuid4().hex,sid=self.a.sid,boot=self.a.boot,issued_ms=now_ms(),
                     expires_ms=now_ms()+15000,base_rev=self.a.source.revision,op=op)
        request.update(extras);return request
    def restart(self,actor='local-admin',link='primary',helper=None,**extras):
        request=self.request(**extras)
        with mock.patch.object(self.a.commands,'_helper',side_effect=helper,
                               return_value=self.HELPER_OK) as call:
            result=self.a.commands.submit(request,actor,link=link);self.a.commands.join_ppp()
        return request,result,call
    def host(self,**state):
        self.a.monitor.data.update(state);self.a._snapshot();return self.a.snapshot()['link']['ppp_recovery']

    def test_local_restart_needs_no_trusted_clock_or_current_revision(self):
        self.a.monitor.data['clock_trusted']=False
        request,result,call=self.restart(base_rev=None)
        self.assertEqual(result['stage'],'ACCEPTED',result)
        call.assert_called_once_with({'op':'ppp.restart','origin':'local-admin'})
        self.assertEqual(self.a.journal.lookup(request['id'])['stage'],'PPP_RESTART_REQUESTED')
        self.a.commands.ppp_last=None;self.a.ppp_recovery=None
        for actor,op,extras in (('ground-controller','ppp.restart',{}),('local-admin','stream.set',{'profile':'balanced'})):
            with self.subTest(actor=actor,op=op):
                result=self.a.commands.submit(self.request(op,**extras),actor)
                self.assertEqual(result['result']['error'],'CLOCK_UNTRUSTED')

    def test_local_restart_is_not_blocked_by_sdr_operation(self):
        self.a.commands.busy=True
        try:
            self.assertEqual(self.a.commands.submit(self.request('stream.set',profile='balanced'),'local-admin')['result']['error'],'BUSY')
            request,result,_=self.restart()
        finally:self.a.commands.busy=False
        self.assertEqual(result['stage'],'ACCEPTED',result)

    def test_local_restart_still_needs_root_approval(self):
        self.a.helper_status={'allow_ppp_restart':False}
        request,result,call=self.restart()
        self.assertEqual(result['result']['error'],'CAPABILITY_DISABLED')
        call.assert_not_called()

    def test_restart_rejects_overlap_and_rapid_repeat(self):
        self.restart()
        self.assertTrue(self.a.ppp_recovery_active())
        for actor in ('local-admin','ground-controller'):
            self.assertEqual(self.restart(actor)[1]['result']['error'],'PPP_RESTART_IN_PROGRESS')
        self.a.ppp_recovery=None
        self.assertEqual(self.restart()[1]['result']['error'],'PPP_RESTART_TOO_SOON')
        self.a.commands.ppp_last-=60
        self.assertEqual(self.restart()[1]['stage'],'ACCEPTED')

    def test_observer_shows_each_step_and_proves_reconnect(self):
        request,_,_=self.restart()
        view=self.a.snapshot()['link']['ppp_recovery'] if self.a.snapshot()['link']['ppp_recovery'] else self.host()
        self.assertEqual((view['id'],view['origin'],view['phase'],view['stage']),
                         (request['id'],'local','REQUESTED','PPP_RESTART_REQUESTED'))
        self.assertEqual(view['steps'],{'requested':True,'cycled':False,'ppp_up':False,'peer_reply':False,'mqtt_ready':False})
        self.assertEqual(view['timeout_s'],120)
        self.assertEqual(self.host(ppp='DOWN',interface=None,ppp_probe='NO_INTERFACE')['phase'],'RESTARTING')
        self.ctrl.ready=False
        view=self.host(ppp='UP',interface='ppp0',ppp_probe='NO_REPLY',ppp_service=dict(self.service,since='200'))
        self.assertEqual((view['phase'],view['steps']['ppp_up'],view['steps']['peer_reply']),('PPP_UP',True,False))
        self.assertEqual(len(self.kicks),1)
        view=self.host(ppp_probe='REPLY')
        self.assertEqual((view['phase'],view['steps']['mqtt_ready']),('PEER_OK',False))
        self.assertEqual(len(self.kicks),2)
        self.ctrl.ready=True;self.ctrl.generation=4
        view=self.host()
        self.assertEqual((view['phase'],view['stage'],view['steps']['mqtt_ready']),('DONE','APPLIED',True))
        operation=self.a.journal.lookup(request['id'])
        self.assertEqual(operation['stage'],'APPLIED')
        self.assertEqual(operation['result']['proof'],{'ppp':'UP','peer':'REPLY','mqtt':'READY','cycled':True})
        last=self.a.snapshot()['last_operation']
        self.assertEqual((last['op'],last['stage'],last['origin'],last['summary']['kind']),('ppp.restart','APPLIED','local','ppp'))
        self.assertIsNone(self.a.snapshot()['last_remote_operation'])
        self.a.ppp_recovery['finished']-=self.a.PPP_RECOVERY_LINGER_S+1
        self.assertIsNone(self.host())

    def test_mqtt_traffic_after_ppp_returns_proves_reconnect_without_new_session(self):
        self.restart()
        self.ctrl.rx_bytes=10
        self.host(ppp='DOWN',interface=None,ppp_probe='NO_INTERFACE')
        self.host(ppp='UP',interface='ppp0',ppp_probe='REPLY')
        self.assertEqual(self.host()['phase'],'PEER_OK')
        self.ctrl.rx_bytes=12
        self.assertEqual(self.host()['phase'],'DONE')

    def test_disabled_primary_mqtt_finishes_on_peer_reply(self):
        self.a.clients={}
        request,_,_=self.restart()
        self.assertIsNone(self.host()['steps']['mqtt_ready'])
        self.host(ppp='DOWN',interface=None,ppp_probe='NO_INTERFACE')
        view=self.host(ppp='UP',interface='ppp0',ppp_probe='REPLY')
        self.assertEqual(view['phase'],'DONE')
        self.assertEqual(self.a.journal.lookup(request['id'])['result']['proof']['mqtt'],'DISABLED')

    def test_observer_times_out_as_unknown_with_observed_cause(self):
        down=dict(ppp='DOWN',interface=None,ppp_probe='NO_INTERFACE')
        for steps,reason in (((),'PPP_CYCLE_NOT_OBSERVED'),((down,),'PPP_RECOVERY_TIMEOUT'),
                             ((down,dict(ppp='UP',interface='ppp0',ppp_probe='REPLY')),'MQTT_NOT_RECOVERED'),
                             ((dict(down,usb='MISSING'),),'T900_USB_MISSING')):
            with self.subTest(reason=reason):
                self.a.ppp_recovery=None;self.a.commands.ppp_last=None;self.ctrl.ready=False
                self.a.monitor.data.update(ppp='UP',interface='ppp0',ppp_probe='REPLY',usb='PRESENT',ppp_service=dict(self.service))
                request,_,_=self.restart(actor='ground-controller')
                for state in steps:self.host(**state)
                self.a.ppp_recovery['started']-=self.a.PPP_RECOVERY_TIMEOUT_S+1
                view=self.host()
                self.assertEqual((view['phase'],view['stage'],view['reason'],view['origin']),
                                 ('TIMEOUT','OUTCOME_UNKNOWN',reason,'ground'))
                operation=self.a.journal.lookup(request['id'])
                self.assertEqual((operation['stage'],operation['result']['reason']),('OUTCOME_UNKNOWN',reason))
                self.assertFalse(self.a.ppp_recovery_active())

    def test_late_recovery_turns_view_green_without_rewriting_journal(self):
        self.ctrl.ready=False
        request,_,_=self.restart()
        self.host(ppp='DOWN',interface=None,ppp_probe='NO_INTERFACE')
        self.a.ppp_recovery['started']-=self.a.PPP_RECOVERY_TIMEOUT_S+1
        view=self.host()
        self.assertEqual((view['phase'],view['late']),('TIMEOUT',False))
        self.assertEqual(self.host(ppp='UP',interface='ppp0',ppp_probe='NO_REPLY')['steps']['ppp_up'],True)
        self.assertEqual(self.host(ppp_probe='REPLY')['phase'],'TIMEOUT')
        self.assertFalse(self.a.ppp_recovery_active())
        self.ctrl.ready=True;self.ctrl.generation+=1
        view=self.host()
        self.assertEqual((view['phase'],view['late'],view['stage'],view['reason']),
                         ('DONE',True,'OUTCOME_UNKNOWN','PPP_RECOVERY_TIMEOUT'))
        self.assertEqual(self.a.journal.lookup(request['id'])['stage'],'OUTCOME_UNKNOWN')
        self.a.ppp_recovery['finished']-=self.a.PPP_RECOVERY_LINGER_S+1
        self.assertIsNone(self.host())

    def test_journal_only_stages_bump_operation_seq(self):
        a=self.a
        request=self.request('stream.set',profile='graph_u8')
        a.journal.accept(request,'ground-controller','ground')
        seq=a.operation_seq
        a.commands._stage(request,'APPLYING')
        self.assertEqual(a.operation_seq,seq+1)
        a._snapshot()
        self.assertEqual((a.snapshot()['operation_seq'],a.snapshot()['last_operation']['stage']),(seq+1,'APPLYING'))

    def test_failed_restart_request_is_shown_with_reason(self):
        request,result,_=self.restart(helper=ValueError('PPP_SERVICE_NOT_ACTIVE'))
        self.assertEqual(result['stage'],'ACCEPTED')
        self.assertEqual(self.a.journal.lookup(request['id'])['stage'],'FAILED')
        view=self.host()
        self.assertEqual((view['phase'],view['stage'],view['reason']),('FAILED','FAILED','PPP_SERVICE_NOT_ACTIVE'))
        self.assertFalse(self.a.ppp_recovery_active())

    def test_ppp_return_kicks_mqtt_without_restart_request(self):
        self.host(ppp='DOWN',interface=None,ppp_probe='NO_INTERFACE');self.assertEqual(self.kicks,[])
        self.host(ppp='UP',interface='ppp0',ppp_probe='NO_REPLY');self.assertEqual(len(self.kicks),1)
        self.host(ppp_probe='REPLY');self.assertEqual(len(self.kicks),2)
        self.host();self.assertEqual(len(self.kicks),2)

    def test_link_snapshot_and_health_detail_show_ppp_service(self):
        self.a.monitor.data['ppp_service']=dict(self.service,state='FAILED',restarts=4)
        self.a._snapshot();link=self.a.snapshot()['link']
        self.assertEqual(link['ppp_service'],{'state':'FAILED','substate':'running','restarts':4})
        self.a.profile='balanced';snap=self.a.snapshot();self.a.stop_event.clear()
        def one_snapshot():
            self.a.stop_event.set();return snap
        with mock.patch.object(self.a,'snapshot',side_effect=one_snapshot):self.a._schedule()
        detail=[json.loads(payload) for _,topic,payload,_ in self.ctrl.sent if topic.endswith('/telemetry/health/detail')]
        self.assertTrue(detail)
        self.assertEqual({k:detail[0][k] for k in ('ppp','peer','ppp_svc','ppp_restarts')},
                         {'ppp':'UP','peer':'REPLY','ppp_svc':'FAILED','ppp_restarts':4})

    def test_remote_operations_carry_origin_summary_and_rejections(self):
        a=self.a;seq=a.snapshot()['operation_seq'];a.commands.start();self.addCleanup(a.commands.stop)
        request=self.request('stream.set',profile='graph_u8')
        before=a.profile
        self.assertEqual(a.commands.submit(request,'ground-controller',link='backup')['stage'],'ACCEPTED')
        a.commands.queue.join()
        read=self.request('operation.get',target_id=request['id'])
        self.assertEqual(a.commands.submit(read,'ground-controller')['stage'],'APPLIED')
        a._snapshot();snap=a.snapshot()
        for key in ('last_operation','last_remote_operation'):
            with self.subTest(key=key):
                op=snap[key]
                self.assertEqual((op['id'],op['op'],op['stage'],op['origin']),(request['id'],'stream.set','APPLIED','backup'))
                self.assertEqual(op['summary'],{'kind':'profile','changes':{'profile':{'from':before,'to':'graph_u8'}}})
                self.assertIsNone(op['reason'])
        self.assertGreater(snap['operation_seq'],seq)
        stale=self.request('stream.set',profile='balanced',base_rev='stale-revision')
        self.assertEqual(a.commands.submit(stale,'ground-controller')['result']['error'],'CONFIG_REVISION_CONFLICT')
        a.commands.submit(self.request('stream.set',profile='nope'),'local-admin')
        a._snapshot();rejects=a.snapshot()['remote_rejects']
        self.assertEqual(len(rejects),1)
        self.assertEqual({k:rejects[0][k] for k in ('origin','op','error')},
                         {'origin':'ground','op':'stream.set','error':'CONFIG_REVISION_CONFLICT'})
        for _ in range(7):a.commands.submit(self.request('stream.set',profile='x'),'ground-controller')
        self.assertEqual(len(a.snapshot()['remote_rejects']),1)
        a._snapshot();self.assertEqual(len(a.snapshot()['remote_rejects']),5)

    def test_config_patch_summary_reports_previous_value(self):
        a=self.a;request=self.request('config.patch',changes={'vfo0_frequency_hz':434000000})
        before=a.source.safe.get('vfo0_frequency_hz')
        a.journal.accept(request,'ground-controller','ground',a.commands._before('config.patch',request))
        op=a.commands.public(a.journal.lookup(request['id']))
        self.assertEqual((op['origin'],op['summary']),
                         ('ground',{'kind':'config','changes':{'vfo0_frequency_hz':{'from':before,'to':434000000}}}))
        legacy=dict(a.journal.lookup(request['id']),link=None,meta={})
        self.assertIsNone(a.commands.public(legacy)['origin'])
        self.assertEqual(a.commands.public(legacy)['summary']['changes']['vfo0_frequency_hz']['from'],None)

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
        self.a.helper_status={'allow_reboot':True}
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


    def test_ground_settings_request_roundtrip_over_plaintext_primary(self):
        # The deployed setup: primary broker over the private PPP/T900 link with mqtt.tls false.
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);initial=make_agent(path);cfg=initial.cfg;initial.journal.close()
            broker=Broker();creds=path/'mqtt.json'
            creds.write_bytes(compact({'username':'test','password':'test-password'}))
            cfg['mqtt'].update(enabled=True,host='127.0.0.1',port=broker.port,tls=False,allow_insecure_loopback=True,
                               control_credentials_file=str(creds),bulk_credentials_file=str(creds))
            raw=compact(settings())
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
                                     ground.node_state.get('sid')==agent.sid,15),
                                [agent.snapshot(),ground.snapshot(),broker.errors])
                self.assertEqual(ground.submit_command({'id':'ground-settings-plain','op':'config.get'})['stage'],'REQUESTED')
                self.assertTrue(wait(lambda:ground.settings_json==raw.decode('utf-8'),10),
                                [agent.snapshot(),ground.snapshot(),broker.errors])
                with broker.lock: messages=list(broker.messages)
                acks=[json.loads(m['payload']) for m in messages if m['topic']=='sdr/v2/uav-01/ack/config']
                self.assertIn(('ground-settings-plain','APPLIED'),[(a['id'],a['status']) for a in acks])
                self.assertFalse(any(a.get('result',{}).get('error') for a in acks))
                reports=[m for m in messages if m['topic']=='sdr/v2/uav-01/settings/reported']
                self.assertEqual(len(reports),1)
                self.assertEqual((reports[0]['qos'],reports[0]['retain']),(1,False))
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


def capture_client(name,ready=True,confirm=False):
    """Fake MQTT client: records offers; `confirm` reports delivery like a socket write."""
    client=Client(cfg={},client_id=name);client.ready=ready;client.connected=ready;client.sent=[]
    def offer(key,topic,payload,**options):
        client.sent.append((key,topic,payload,options))
        if confirm and options.get('on_sent'):options['on_sent']()
        return True
    client.offer=offer;return client

def backup_body(**changes):
    body={'enabled':True,'host':'backup.example.net','port':8883,'transport':'tcp','websocket_path':'/mqtt',
          'tls':True,'commands':True,'auth':'account','username':'backup-user','password':'backup-secret'}
    body.update(changes);return body

BACKUP_DEFAULT_VIEW={'enabled':False,'host':'','port':8883,'transport':'tcp','websocket_path':'/mqtt','tls':True,
                     'commands':True,'settings_report_plaintext':False,'auth':'none','credentials_set':False,'credentials_source':None,'trust_mode':'system',
                     'client_id':'uav-01-backup','editable':True,'apply_pending':False}


class BackupLinkTests(unittest.TestCase):
    """Backup broker scheduling, ACK routing and delivery maps with fake clients (no network)."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(os.path.realpath(self.tmp.name))
        self.a=make_agent(self.path);self.addCleanup(self.a.journal.close)
    def links(self,primary=True,bulk=True,backup=True,backup_tls=True):
        a=self.a
        control=capture_client('cap-control',primary);bulk_client=capture_client('cap-bulk',bulk)
        backup_client=capture_client('cap-backup',backup)
        a.clients={'control':control,'bulk':bulk_client};a.backup_client=backup_client
        a.backup_active=dict(a.backup_active,enabled=True,host='backup.example.net',tls=backup_tls)
        return control,bulk_client,backup_client
    def run_once(self,snapshot=None):
        a=self.a;snap=snapshot if snapshot is not None else a.snapshot();real=a.snapshot
        a.stop_event.clear()
        def one_snapshot():
            a.stop_event.set();return snap
        a.snapshot=one_snapshot
        try:a._schedule()
        finally:a.snapshot=real
        self.assertFalse(str(a.bulk_reason).startswith('SCHEDULER_'),a.bulk_reason)
    def suffixes(self,client):
        return [topic.split(self.a.prefix+'/',1)[1] for _,topic,_,_ in client.sent]
    def payloads(self,client,suffix):
        return [json.loads(payload) for _,topic,payload,_ in client.sent if topic==self.a.prefix+'/'+suffix]
    def request(self,op,**extras):
        a=self.a
        return dict(v=2,id=op.replace('.','-')+'-'+uuid.uuid4().hex[:12],sid=a.sid,boot=a.boot,issued_ms=now_ms(),
                    expires_ms=now_ms()+15000,base_rev=a.source.revision,op=op,**extras)
    def ack_ids(self,client,suffix='ack/operation'):
        return [(p['id'],p['status']) for p in self.payloads(client,suffix)]

    def test_disabled_backup_snapshot_shape(self):
        self.assertIsNone(self.a.backup_client)
        snapshot=self.a.snapshot()
        self.assertEqual(snapshot['link']['mqtt_backup'],
                         {'enabled':False,'commands':True,'state':'DISABLED','ready':False,'error':None,'depth':0,
                          'pending_age_ms':0,'bulk_pause':None,'angular_aborted':0,'topic_delivery':{}})
        self.assertFalse(snapshot['config']['mqtt_backup_configured'])

    def test_scheduler_mirrors_every_topic_to_primary_and_backup(self):
        a=self.a;a.profile='balanced';a.cfg['telemetry']['resume_stable_seconds']=0;a.bulk_resume_after=0
        control,bulk,backup=self.links()
        self.run_once()
        expected={'availability','capabilities','state','telemetry/health','telemetry/doa'}
        self.assertLessEqual(expected,set(self.suffixes(control)),self.suffixes(control))
        self.assertLessEqual(expected,set(self.suffixes(backup)),self.suffixes(backup))
        self.assertEqual(self.suffixes(bulk),['telemetry/angular'])
        self.assertIn('telemetry/angular',self.suffixes(backup))
        self.assertNotIn('telemetry/angular',self.suffixes(control))
        self.assertNotIn('telemetry/diagnostic/angular',self.suffixes(backup))
        for suffix in ('availability','capabilities','state'):
            options=[o for _,topic,_,o in backup.sent if topic==a.prefix+'/'+suffix][0]
            self.assertEqual((options['qos'],options['retain']),(1,True),suffix)
        for suffix in ('telemetry/health','telemetry/doa','state'):
            self.assertEqual(self.payloads(control,suffix),self.payloads(backup,suffix),suffix)
        primary_frame=self.payloads(bulk,'telemetry/angular')[0];backup_frame=self.payloads(backup,'telemetry/angular')[0]
        self.assertEqual(primary_frame,backup_frame)
        self.assertEqual((backup_frame['encoding'],len(backup_frame['values']),backup_frame['flags']),('json',360,63))
        self.assertEqual(self.payloads(backup,'availability')[0]['online'],True)
        self.assertIsNone(a.bulk_reason);self.assertIsNone(a.backup_bulk_reason)
        self.assertEqual(a.backup_last_angular_q,a.last_angular_q)

    def test_backup_angular_ignores_primary_only_pauses(self):
        a=self.a
        for reason in ('BULK_NOT_READY','CONTROL_BACKLOG','WAIT_STABLE'):
            with self.subTest(reason=reason):
                a.profile='balanced';a.bulk_resume_after=0;a.last_angular_q=0;a.backup_last_angular_q=0
                control,bulk,backup=self.links(bulk=reason!='BULK_NOT_READY')
                a._generation=control.generation;a._backup_generation=backup.generation
                if reason=='CONTROL_BACKLOG':control.pending_age_ms=6000
                if reason=='WAIT_STABLE':a.bulk_resume_after=time.monotonic()+60
                self.run_once()
                self.assertEqual(a.bulk_reason,reason)
                self.assertIsNone(a.backup_bulk_reason)
                self.assertNotIn('telemetry/angular',self.suffixes(bulk))
                self.assertIn('telemetry/angular',self.suffixes(backup))
                self.assertNotIn('telemetry/diagnostic/angular',self.suffixes(backup))
                self.assertEqual(len(self.payloads(backup,'telemetry/angular')[0]['values']),360)

    def test_control_backlog_waits_for_queued_age_not_one_slow_puback(self):
        a=self.a
        for pending,queued,expected in ((1000,0,None),(2400,2900,None),(3000,0,'CONTROL_BACKLOG'),(0,4000,'CONTROL_BACKLOG')):
            with self.subTest(pending=pending,queued=queued):
                a.profile='balanced';a.bulk_resume_after=0;a.last_angular_q=0;a.backup_last_angular_q=0
                control,bulk,backup=self.links(backup=False)
                a._generation=control.generation;control.pending_age_ms=pending
                with mock.patch.object(Client,'queue_age_ms',new_callable=mock.PropertyMock,return_value=queued):
                    self.run_once()
                self.assertEqual(a.bulk_reason,expected)
                # Below the pause thresholds a late PUBACK only holds the next frame (see the yield test).
                self.assertEqual('telemetry/angular' in self.suffixes(bulk),expected is None and pending<=1000)

    def test_bulk_yields_to_late_control_or_unacked_bulk_without_pause(self):
        a=self.a
        for pending,backlog,sent in ((900,None,True),(900,2048,True),(1500,None,False),(0,3000,False)):
            with self.subTest(pending=pending,backlog=backlog):
                a.profile='balanced';a.bulk_resume_after=0;a.last_angular_q=0;a.backup_last_angular_q=0
                control,bulk,backup=self.links(backup=False)
                a._generation=control.generation;control.pending_age_ms=pending
                with mock.patch.object(Client,'socket_backlog',new_callable=mock.PropertyMock,return_value=backlog):
                    self.run_once()
                self.assertIsNone(a.bulk_reason)
                self.assertEqual(a.bulk_resume_after,0)
                self.assertEqual('telemetry/angular' in self.suffixes(bulk),sent)

    def test_diagnostic_doa_goes_ahead_of_diagnostic_angular(self):
        a=self.a
        atomic_write(a.source.path/'doa.xml',f'<DATA><TIME>{now_ms()}</TIME><FREQUENCY>433.92</FREQUENCY><DOA>200.0</DOA></DATA>'.encode())
        a.source.poll(force=True);a._snapshot()
        control,bulk,backup=self.links(backup=False)
        self.run_once()
        options={t.split(a.prefix+'/',1)[1]:o for _,t,_,o in control.sent}
        self.assertEqual(options['telemetry/diagnostic/doa']['priority'],2)
        self.assertLess(options['telemetry/diagnostic/doa']['priority'],3)

    def test_source_level_reasons_pause_angular_on_both_links(self):
        a=self.a;live=a.snapshot()
        ineligible=copy.deepcopy(live);ineligible['detection'].update(valid=False,reasons=['DAQ_NOT_HEALTHY'])
        for reason in ('PROFILE_CONTROL','CONTROL_IN_PROGRESS','SOURCE_NOT_ELIGIBLE'):
            with self.subTest(reason=reason):
                a.profile='control' if reason=='PROFILE_CONTROL' else 'balanced'
                a.commands.busy=reason=='CONTROL_IN_PROGRESS';a.bulk_resume_after=0
                a.last_angular_q=0;a.backup_last_angular_q=0
                control,bulk,backup=self.links()
                a._generation=control.generation;a._backup_generation=backup.generation
                try:self.run_once(ineligible if reason=='SOURCE_NOT_ELIGIBLE' else live)
                finally:a.commands.busy=False
                self.assertEqual((a.bulk_reason,a.backup_bulk_reason),(reason,reason))
                self.assertNotIn('telemetry/angular',self.suffixes(bulk))
                self.assertNotIn('telemetry/angular',self.suffixes(backup))
                diagnostic='telemetry/diagnostic/angular' in self.suffixes(backup)
                self.assertEqual(diagnostic,reason!='PROFILE_CONTROL')

    def test_backup_keeps_publishing_when_primary_is_down_or_disabled(self):
        a=self.a;a.profile='balanced'
        for case in ('down','disabled'):
            with self.subTest(case=case):
                a.backup_last_angular_q=0;a.last_angular_q=0;a.last_doa_q=0;a.last_sent_state=None;a._backup_generation=-1
                control,bulk,backup=self.links(primary=False,bulk=False)
                if case=='disabled':a.clients={}
                self.run_once()
                self.assertEqual(a.bulk_reason,'MQTT_NOT_READY')
                self.assertIsNone(a.backup_bulk_reason)
                self.assertLessEqual({'availability','capabilities','state','telemetry/health','telemetry/doa',
                                      'telemetry/angular'},set(self.suffixes(backup)))
                self.assertEqual((control.sent,bulk.sent),([],[]))

    def test_backup_down_leaves_primary_schedule_unchanged(self):
        a=self.a;a.profile='balanced';a.cfg['telemetry']['resume_stable_seconds']=0;a.bulk_resume_after=0
        control,bulk,backup=self.links(backup=False)
        self.run_once()
        self.assertEqual(backup.sent,[])
        self.assertEqual(a.backup_bulk_reason,'MQTT_NOT_READY');self.assertIsNone(a.bulk_reason)
        self.assertIn('telemetry/health',self.suffixes(control));self.assertIn('telemetry/angular',self.suffixes(bulk))

    def test_command_acks_return_to_the_link_that_sent_them(self):
        a=self.a;control,bulk,backup=self.links()
        query=self.request('operation.get',target_id='missing')
        result=a.commands.submit(query,'ground-controller',topic=a.prefix+'/cmd/operation/get',link='backup')
        self.assertEqual(result['stage'],'APPLIED')
        self.assertEqual(self.ack_ids(backup),[(query['id'],'APPLIED')])
        self.assertEqual(self.ack_ids(control),[])
        options=[o for _,topic,_,o in backup.sent if topic.endswith('/ack/operation')][0]
        self.assertEqual((options['qos'],options['retain'],options['priority']),(1,False,0))
        rejected=self.request('stream.set',profile='turbo')
        a.commands.submit(rejected,'ground-controller',topic=a.prefix+'/cmd/stream/set',link='backup')
        self.assertIn((rejected['id'],'REJECTED'),self.ack_ids(backup))
        self.assertEqual(self.ack_ids(control),[])
        primary=self.request('operation.get',target_id=query['id'])
        a.commands.submit(primary,'ground-controller',topic=a.prefix+'/cmd/operation/get',link='primary')
        self.assertEqual(self.ack_ids(control),[(primary['id'],'APPLIED')])
        self.assertIn((primary['id'],'APPLIED'),self.ack_ids(backup))
        local=self.request('operation.get',target_id=query['id'])
        self.assertEqual(a.commands.submit(local,'local-admin')['stage'],'APPLIED')
        self.assertIn((local['id'],'APPLIED'),self.ack_ids(control))
        self.assertIn((local['id'],'APPLIED'),self.ack_ids(backup))
        # Replaying a journaled backup command answers on the backup link only.
        control.sent.clear();backup.sent.clear()
        a.commands.submit(query,'ground-controller',topic=a.prefix+'/cmd/operation/get',link='backup')
        self.assertEqual(self.ack_ids(backup),[(query['id'],'APPLIED')]);self.assertEqual(control.sent,[])

    def test_worker_command_from_backup_acks_only_on_backup(self):
        a=self.a;control,bulk,backup=self.links()
        a.commands.start();self.addCleanup(a.commands.stop)
        command=self.request('stream.set',profile='graph_u8')
        result=a.commands.submit(command,'ground-controller',topic=a.prefix+'/cmd/stream/set',link='backup')
        self.assertEqual(result['stage'],'ACCEPTED')
        self.assertTrue(wait(lambda:(command['id'],'APPLIED') in self.ack_ids(backup),5),self.ack_ids(backup))
        self.assertIn((command['id'],'ACCEPTED'),self.ack_ids(backup))
        self.assertEqual(self.ack_ids(control),[])
        self.assertEqual(a.profile,'graph_u8')
        self.assertEqual((a.journal.lookup(command['id'])['actor'],a.journal.lookup(command['id'])['stage']),
                         ('ground-controller','APPLIED'))

    def test_pending_backup_settings_request_is_freed_when_backup_drops(self):
        a=self.a
        (a.source.path/'settings.json').write_bytes(compact(settings()));a.source.poll(force=True)
        a.cfg['mqtt']['tls']=True
        control,bulk,backup=self.links()
        topic=a.prefix+'/cmd/config/get'
        request=self.request('config.get')
        self.assertEqual(a.commands.submit(request,'ground-controller',topic=topic,link='backup')['stage'],'APPLIED')
        backup.ready=False
        self.run_once()
        self.assertIsNone(a.settings_request_id)
        self.assertNotIn('settings/reported',self.suffixes(control)+self.suffixes(backup))
        a.commands.last_rate.clear()
        primary=self.request('config.get')
        self.assertEqual(a.commands.submit(primary,'ground-controller',topic=topic,link='primary')['stage'],'APPLIED')
        self.assertEqual(a.settings_request_link,'primary')

    def test_config_get_plaintext_rule_follows_the_requesting_link(self):
        a=self.a
        (a.source.path/'settings.json').write_bytes(compact(settings()));a.source.poll(force=True)
        a.cfg['mqtt']['tls']=False
        control,bulk,backup=self.links(backup_tls=False)
        topic=a.prefix+'/cmd/config/get'
        # Plaintext backup without the opt-in: rejected, ACK on the backup only, nothing queued.
        plain=self.request('config.get')
        result=a.commands.submit(plain,'ground-controller',topic=topic,link='backup')
        self.assertEqual((result['stage'],result['result']['error']),('REJECTED','TLS_REQUIRED_FOR_SETTINGS_REPORT'))
        self.assertEqual(self.ack_ids(backup,'ack/config'),[(plain['id'],'REJECTED')])
        self.assertEqual(self.ack_ids(control,'ack/config'),[])
        self.assertIsNone(a.settings_request_id)
        # Plaintext primary (mqtt.tls false): applied, and the report goes to the primary only.
        a.commands.last_rate.clear()
        primary=self.request('config.get')
        self.assertEqual(a.commands.submit(primary,'ground-controller',topic=topic,link='primary')['stage'],'APPLIED')
        self.assertEqual(a.settings_request_link,'primary')
        for client in (control,bulk,backup):client.sent.clear()
        self.run_once()
        reports=self.payloads(control,'settings/reported')
        self.assertEqual([p['id'] for p in reports],[primary['id']])
        self.assertEqual(set(reports[0]),{'v','sid','boot','id','rev','t','settings_json'})
        options=[o for _,t,_,o in control.sent if t.endswith('/settings/reported')][0]
        self.assertEqual((options['qos'],options['retain'],options['expiry']),(1,False,30))
        self.assertNotIn('settings/reported',self.suffixes(backup)+self.suffixes(bulk))
        self.assertIsNone(a.settings_request_id)
        # Explicit opt-in on the backup: applied and reported to the backup only.
        a.backup_active=dict(a.backup_active,settings_report_plaintext=True);a.commands.last_rate.clear()
        opted=self.request('config.get')
        self.assertEqual(a.commands.submit(opted,'ground-controller',topic=topic,link='backup')['stage'],'APPLIED')
        for client in (control,bulk,backup):client.sent.clear()
        self.run_once()
        self.assertEqual([p['id'] for p in self.payloads(backup,'settings/reported')],[opted['id']])
        self.assertNotIn('settings/reported',self.suffixes(control)+self.suffixes(bulk))
        # A TLS backup never needed the opt-in.
        a.backup_active=dict(a.backup_active,tls=True,settings_report_plaintext=False);a.commands.last_rate.clear()
        secure=self.request('config.get')
        self.assertEqual(a.commands.submit(secure,'ground-controller',topic=topic,link='backup')['stage'],'APPLIED')
        for client in (control,bulk,backup):client.sent.clear()
        self.run_once()
        self.assertEqual([p['id'] for p in self.payloads(backup,'settings/reported')],[secure['id']])
        self.assertNotIn('settings/reported',self.suffixes(control))

    def test_backup_opt_in_turned_off_before_delivery_keeps_report_on_hold(self):
        a=self.a
        (a.source.path/'settings.json').write_bytes(compact(settings()));a.source.poll(force=True)
        control,bulk,backup=self.links(backup_tls=False)
        a.backup_active=dict(a.backup_active,settings_report_plaintext=True)
        request=self.request('config.get')
        self.assertEqual(a.commands.submit(request,'ground-controller',topic=a.prefix+'/cmd/config/get',
                                           link='backup')['stage'],'APPLIED')
        a.backup_active=dict(a.backup_active,settings_report_plaintext=False)
        self.run_once()
        self.assertNotIn('settings/reported',self.suffixes(backup)+self.suffixes(control))

    def test_config_get_rate_limit_is_per_link(self):
        a=self.a
        (a.source.path/'settings.json').write_bytes(compact(settings()));a.source.poll(force=True)
        a.cfg['mqtt']['tls']=True;self.links()
        first=self.request('operation.get',target_id='missing');second=self.request('operation.get',target_id='missing')
        topic=a.prefix+'/cmd/operation/get'
        self.assertEqual(a.commands.submit(first,'ground-controller',topic=topic,link='primary')['stage'],'APPLIED')
        self.assertEqual(a.commands.submit(second,'ground-controller',topic=topic,link='backup')['stage'],'APPLIED')
        third=self.request('operation.get',target_id='missing')
        self.assertEqual(a.commands.submit(third,'ground-controller',topic=topic,link='backup')['result']['error'],
                         'REQUEST_RATE_LIMIT')

    def test_backup_publish_failures_use_a_separate_delivery_map(self):
        a=self.a
        a.clients={'control':capture_client('cap-control',confirm=True)}
        a.backup_client=Client(cfg={},client_id='offline-backup')
        self.assertTrue(a._offer('health','telemetry/health',{'v':2}))
        self.assertFalse(a._offer('caps','capabilities',{'v':2},1,True,links=('backup',)))
        a._snapshot();link=a.snapshot()['link']
        primary=link['mqtt_topic_delivery'];backup=link['mqtt_backup']['topic_delivery']
        self.assertEqual((primary['telemetry/health']['state'],primary['telemetry/health']['error']),('SENT',None))
        self.assertNotIn('capabilities',primary)
        self.assertEqual((backup['telemetry/health']['state'],backup['telemetry/health']['error']),('ERROR','MQTT_DISCONNECTED'))
        self.assertEqual((backup['capabilities']['state'],backup['capabilities']['qos'],backup['capabilities']['confirmation']),
                         ('ERROR',1,'PUBACK'))
        self.assertTrue(link['mqtt_backup']['enabled'])
        # And the reverse: a failing primary never overwrites backup delivery evidence.
        a.clients={'control':Client(cfg={},client_id='offline-control')}
        a.backup_client=capture_client('cap-backup',confirm=True)
        self.assertTrue(a._offer('detail','telemetry/health/detail',{'v':2}))
        a._snapshot();link=a.snapshot()['link']
        self.assertEqual(link['mqtt_topic_delivery']['telemetry/health/detail']['error'],'MQTT_DISCONNECTED')
        self.assertEqual(link['mqtt_backup']['topic_delivery']['telemetry/health/detail']['state'],'SENT')
        self.assertEqual(link['mqtt_topic_delivery']['telemetry/health']['state'],'SENT')

    def test_backup_client_identity_subscriptions_and_budget(self):
        a=self.a
        a.configure_mqtt_backup(backup_body(auth='none',username='',password=''))
        a._apply_pending_backup();client=a.backup_client
        self.assertEqual(client.client_id,'uav-01-backup')
        self.assertEqual(sorted(topic for topic,_,_ in client.subscriptions),
                         sorted(a.prefix+'/'+suffix for suffix in OPS.values()))
        self.assertTrue(all(qos==1 for _,qos,_ in client.subscriptions))
        self.assertEqual(client.will[0],a.prefix+'/availability')
        self.assertEqual(json.loads(client.will[1])['online'],False)
        self.assertEqual((client.cfg['host'],client.cfg['port'],client.cfg['tls'],client.cfg['ca_file']),
                         ('backup.example.net',8883,True,None))
        self.assertIsNone(client.credentials_file)
        self.assertEqual(client.bucket.rate,a.cfg['mqtt']['backup']['budget_bytes_s'])
        self.assertFalse(client.bulk)
        self.assertNotIn(client,a.clients.values())
        a.configure_mqtt_backup(backup_body(auth='none',username='',password='',commands=False))
        a._apply_pending_backup()
        self.assertEqual(a.backup_client.subscriptions,[])
        self.assertTrue(client.stop_event.is_set())
        a._snapshot();self.assertFalse(a.snapshot()['link']['mqtt_backup']['commands'])

    def test_live_reconfigure_keeps_the_other_link_clients(self):
        a=self.a
        control=Client(cfg={},client_id='keep-control');bulk=Client(cfg={},client_id='keep-bulk',bulk=True)
        a.clients={'control':control,'bulk':bulk}
        a.configure_mqtt_backup(backup_body(auth='none',username='',password=''))
        a._apply_pending_backup()
        self.assertIs(a.clients['control'],control);self.assertIs(a.clients['bulk'],bulk)
        self.assertFalse(control.stop_event.is_set() or bulk.stop_event.is_set())
        backup=a.backup_client;self.assertIsNotNone(backup)
        a.mqtt_topic_delivery['telemetry/health']={'state':'SENT'}
        a.configure_mqtt({'enabled':False,'host':'ground.example','port':8883,'client_id':'uav-01',
                          'transport':'tcp','tls':True,'websocket_path':'/mqtt',
                          'control':{'username':'','password':''},'bulk':{'username':'','password':''}})
        a._apply_pending_mqtt()
        self.assertEqual(a.clients,{})
        self.assertTrue(control.stop_event.is_set())
        self.assertIs(a.backup_client,backup);self.assertFalse(backup.stop_event.is_set())
        # Disabling the backup stops only the backup client.
        primary=capture_client('cap-control');a.clients={'control':primary}
        a.configure_mqtt_backup(backup_body(enabled=False,host='',auth='none',username='',password=''))
        a._apply_pending_backup()
        self.assertIsNone(a.backup_client);self.assertTrue(backup.stop_event.is_set())
        self.assertIs(a.clients['control'],primary)
        a._snapshot();snapshot=a.snapshot()
        self.assertEqual(snapshot['link']['mqtt_backup']['state'],'DISABLED')
        self.assertFalse(snapshot['config']['mqtt_backup_configured'])


class BackupApiTests(unittest.TestCase):
    setUp=ApiTests.setUp;shutdown=ApiTests.shutdown;http=ApiTests.http;login=ApiTests.login
    def post(self,body):
        code,_,response=self.http('POST','/api/v2/mqtt/backup',body);return code,json.loads(response)
    def state(self,name):return Path(self.a.cfg['state_dir'])/name

    def test_backup_settings_require_admin_session_and_csrf(self):
        code,_,response=self.http('GET','/api/v2/mqtt/backup')
        self.assertEqual((code,json.loads(response)),(401,{'error':'AUTHENTICATION_REQUIRED'}))
        self.assertEqual(self.http('POST','/api/v2/mqtt/backup',backup_body())[0],403)
        self.login();csrf=self.csrf;self.csrf=''
        self.assertEqual(self.http('POST','/api/v2/mqtt/backup',backup_body())[0],403)
        self.csrf='wrong-'+csrf
        self.assertEqual(self.http('POST','/api/v2/mqtt/backup',backup_body())[0],403)
        self.assertFalse(self.state('mqtt-ui-backup-settings.json').exists())
        self.assertFalse(self.state('mqtt-ui-backup-account.json').exists())
        self.csrf=csrf
        code,_,response=self.http('GET','/api/v2/mqtt/backup')
        self.assertEqual((code,json.loads(response)),(200,BACKUP_DEFAULT_VIEW))

    def test_backup_settings_reject_invalid_bodies_with_spec_codes(self):
        self.login()
        missing=backup_body();del missing['commands']
        cases=(
            (dict(backup_body(),extra=1),'INVALID_MQTT_BACKUP_SETTINGS'),
            (missing,'INVALID_MQTT_BACKUP_SETTINGS'),
            (backup_body(auth='config'),'INVALID_MQTT_BACKUP_SETTINGS'),
            (backup_body(auth='anonymous',username='',password=''),'INVALID_MQTT_BACKUP_SETTINGS'),
            (backup_body(enabled='true'),'INVALID_MQTT_BACKUP_SETTINGS'),
            (backup_body(tls=0),'INVALID_MQTT_BACKUP_SETTINGS'),
            (backup_body(commands=None),'INVALID_MQTT_BACKUP_SETTINGS'),
            (backup_body(host='broker.local/path'),'INVALID_MQTT_HOST'),
            (backup_body(host=''),'INVALID_MQTT_HOST'),
            (backup_body(host=' 192.168.1.20'),'INVALID_MQTT_HOST'),
            (backup_body(host=5),'INVALID_MQTT_HOST'),
            (backup_body(enabled=False,host='bad host'),'INVALID_MQTT_HOST'),
            (backup_body(port=0),'INVALID_MQTT_PORT'),(backup_body(port=65536),'INVALID_MQTT_PORT'),
            (backup_body(port='8883'),'INVALID_MQTT_PORT'),(backup_body(port=True),'INVALID_MQTT_PORT'),
            (backup_body(port=1883.5),'INVALID_MQTT_PORT'),
            (backup_body(transport='udp'),'INVALID_MQTT_TRANSPORT'),
            (backup_body(transport='websocket',websocket_path='/mqtt\r\nHost:attacker'),'INVALID_MQTT_WEBSOCKET_PATH'),
            (backup_body(auth='none'),'INVALID_MQTT_CREDENTIALS'),
            (backup_body(auth='none',username='',password='p'),'INVALID_MQTT_CREDENTIALS'),
            (backup_body(username='backup-user',password=''),'INCOMPLETE_MQTT_CREDENTIALS'),
            (backup_body(username='',password='backup-secret'),'INCOMPLETE_MQTT_CREDENTIALS'),
            (backup_body(username=5),'INVALID_MQTT_CREDENTIALS'),
            (backup_body(password='x'*513),'INVALID_MQTT_CREDENTIALS'),
            (backup_body(username='user\x00'),'INVALID_MQTT_CREDENTIALS'),
            (backup_body(username='',password=''),'MQTT_BACKUP_CREDENTIALS_REQUIRED'),
        )
        for body,error in cases:
            with self.subTest(body=body):
                self.assertEqual(self.post(body),(400,{'error':error}))
        self.assertEqual(self.http('POST','/api/v2/mqtt/backup',raw=b'[]')[0],400)
        self.assertFalse(self.state('mqtt-ui-backup-settings.json').exists())
        self.assertFalse(self.state('mqtt-ui-backup-account.json').exists())
        self.assertEqual(self.a.mqtt_backup_view(),BACKUP_DEFAULT_VIEW)

    def test_backup_settings_disabled_allows_blank_host_and_anonymous(self):
        self.login()
        code,view=self.post(backup_body(enabled=False,host='',auth='none',username='',password=''))
        self.assertEqual(code,200,view)
        self.assertEqual(view,dict(BACKUP_DEFAULT_VIEW,apply_pending=True))
        code,view=self.post(backup_body(auth='none',username='',password='',tls=False,port=1883,commands=False))
        self.assertEqual(code,200,view)
        self.assertEqual((view['enabled'],view['auth'],view['credentials_set'],view['trust_mode'],view['commands']),
                         (True,'none',False,'plaintext',False))
        self.assertFalse(self.state('mqtt-ui-backup-account.json').exists())

    def test_backup_account_secrets_stay_in_private_files(self):
        self.login()
        code,_,response=self.http('POST','/api/v2/mqtt/backup',backup_body())
        self.assertEqual(code,200,response)
        self.assertNotIn(b'backup-secret',response);self.assertNotIn(b'backup-user',response)
        self.assertEqual(json.loads(response),dict(BACKUP_DEFAULT_VIEW,enabled=True,host='backup.example.net',
                                                    auth='account',credentials_set=True,credentials_source='panel',
                                                    apply_pending=True))
        settings_file=self.state('mqtt-ui-backup-settings.json');account_file=self.state('mqtt-ui-backup-account.json')
        self.assertEqual(json.loads(settings_file.read_bytes()),
                         {'schema':1,'enabled':True,'host':'backup.example.net','port':8883,'transport':'tcp',
                          'websocket_path':'/mqtt','tls':True,'commands':True,'auth':'account',
                          'settings_report_plaintext':False})
        self.assertEqual(json.loads(account_file.read_bytes()),{'username':'backup-user','password':'backup-secret'})
        self.assertEqual(stat.S_IMODE(settings_file.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(account_file.stat().st_mode),0o600)
        code,_,response=self.http('GET','/api/v2/mqtt/backup')
        self.assertEqual(code,200);self.assertNotIn(b'backup-secret',response);self.assertNotIn(b'backup-user',response)
        self.a._apply_pending_backup();self.a._snapshot()
        self.assertEqual(self.a.backup_client.credentials_file,str(account_file))
        code,_,response=self.http('GET','/api/v2/snapshot')
        self.assertEqual(code,200)
        for secret in (b'backup-secret',b'backup-user',b'backup.example.net',b'mqtt-ui-backup-account'):
            self.assertNotIn(secret,response)
        snapshot=json.loads(response)
        self.assertTrue(snapshot['config']['mqtt_backup_configured'])
        self.assertEqual((snapshot['link']['mqtt_backup']['enabled'],snapshot['link']['mqtt_backup']['commands']),(True,True))
        self.assertEqual(json.loads(self.http('GET','/api/v2/mqtt/backup')[2])['apply_pending'],False)
        # A new Agent restores the saved backup settings and account.
        self.a.journal.close()
        restored=Agent(copy.deepcopy(self.a.cfg))
        try:
            self.assertEqual(restored.mqtt_backup_view(),dict(BACKUP_DEFAULT_VIEW,enabled=True,host='backup.example.net',
                                                              auth='account',credentials_set=True,
                                                              credentials_source='panel'))
            self.assertEqual(restored.backup_client.client_id,'uav-01-backup')
            self.assertEqual(restored.backup_client.credentials_file,str(account_file))
        finally:restored.journal.close()

    def test_backup_blank_account_keeps_stored_credentials(self):
        self.login()
        self.assertEqual(self.post(backup_body())[0],200)
        account_file=self.state('mqtt-ui-backup-account.json');original=account_file.read_bytes()
        code,view=self.post(backup_body(host='backup2.example.net',username='',password=''))
        self.assertEqual(code,200,view)
        self.assertEqual((view['host'],view['auth'],view['credentials_set']),('backup2.example.net','account',True))
        self.assertEqual(account_file.read_bytes(),original)
        self.assertEqual(self.post(backup_body(host='backup3.example.net',username='new-user',password='')),
                         (400,{'error':'INCOMPLETE_MQTT_CREDENTIALS'}))
        self.assertEqual(account_file.read_bytes(),original)
        self.assertEqual(self.a.mqtt_backup_view()['host'],'backup2.example.net')
        code,view=self.post(backup_body(auth='none',username='',password=''))
        self.assertEqual((code,view['auth'],view['credentials_set'],view['credentials_source']),(200,'none',True,'panel'))
        self.assertEqual(json.loads(self.state('mqtt-ui-backup-settings.json').read_bytes())['auth'],'none')
        self.a._apply_pending_backup();self.assertIsNone(self.a.backup_client.credentials_file)
        code,view=self.post(backup_body(username='',password=''))
        self.assertEqual((code,view['auth'],view['credentials_set']),(200,'account',True))
        code,view=self.post(backup_body(username='other-user',password='other-secret'))
        self.assertEqual(code,200)
        self.assertEqual(json.loads(account_file.read_bytes()),{'username':'other-user','password':'other-secret'})

    def test_backup_blank_account_keeps_config_credentials_file(self):
        credentials=self.path/'backup-config.json'
        credentials.write_bytes(compact({'username':'cfg-user','password':'cfg-secret'}))
        self.a.cfg['mqtt']['backup']['credentials_file']=str(credentials)
        self.a.backup_desired=self.a._load_backup_settings()
        self.login()
        view=json.loads(self.http('GET','/api/v2/mqtt/backup')[2])
        self.assertEqual((view['auth'],view['credentials_set']),('account',True))
        code,view=self.post(backup_body(username='',password=''))
        self.assertEqual((code,view['auth'],view['credentials_set']),(200,'account',True))
        self.assertEqual(json.loads(self.state('mqtt-ui-backup-settings.json').read_bytes())['auth'],'config')
        self.assertFalse(self.state('mqtt-ui-backup-account.json').exists())
        self.a._apply_pending_backup();self.assertEqual(self.a.backup_client.credentials_file,str(credentials))
        credentials.unlink()
        self.assertEqual(self.post(backup_body(username='',password='')),
                         (400,{'error':'MQTT_BACKUP_CREDENTIALS_REQUIRED'}))

    def test_backup_trust_modes_and_unavailable_custom_ca(self):
        ca=self.path/'backup-ca.pem';self.a.cfg['mqtt']['backup']['ca_file']=str(ca)
        self.login()
        self.assertEqual(json.loads(self.http('GET','/api/v2/mqtt/backup')[2])['trust_mode'],'unavailable')
        body=backup_body(auth='none',username='',password='')
        self.assertEqual(self.post(body),(400,{'error':'MQTT_BACKUP_TLS_CA_UNAVAILABLE'}))
        code,view=self.post(dict(body,tls=False))
        self.assertEqual((code,view['trust_mode']),(200,'plaintext'))
        ca.write_bytes(b'-----BEGIN CERTIFICATE-----\n')
        code,view=self.post(body)
        self.assertEqual((code,view['trust_mode']),(200,'custom'))
        self.a._apply_pending_backup()
        self.assertEqual(self.a.backup_client.cfg['ca_file'],str(ca))

    def test_backup_settings_report_plaintext_option_round_trips(self):
        self.login();body=backup_body(tls=False,port=1883)
        code,view=self.post(dict(body,settings_report_plaintext=True))
        self.assertEqual((code,view.get('settings_report_plaintext')),(200,True),view)
        saved=json.loads(self.a.backup_state_path.read_text())
        self.assertIs(saved['settings_report_plaintext'],True)
        self.assertNotIn('backup-secret',self.a.backup_state_path.read_text())
        self.a._apply_pending_backup()
        self.assertTrue(self.a.settings_report_allowed('backup'))
        for value in ('true',1,None):
            with self.subTest(value=value):
                self.assertEqual(self.post(dict(body,settings_report_plaintext=value)),
                                 (400,{'error':'INVALID_MQTT_BACKUP_SETTINGS'}))
        # A panel that predates the option sends no key: the safe default applies.
        code,view=self.post(body)
        self.assertEqual((code,view['settings_report_plaintext']),(200,False))
        self.a._apply_pending_backup()
        self.assertFalse(self.a.settings_report_allowed('backup'))
        self.assertTrue(self.a.settings_report_allowed('primary'))

    def test_invalid_saved_backup_settings_fall_back_to_config(self):
        defaults=self.a._backup_defaults()
        saved={'schema':1,'enabled':True,'host':'backup.example.net','port':8883,'transport':'tcp',
               'websocket_path':'/mqtt','tls':True,'commands':True,'auth':'none'}
        for value in (dict(saved,schema=2),dict(saved,extra=True),dict(saved,port='8883'),dict(saved,host=''),
                      dict(saved,auth='account')):
            with self.subTest(value=value):
                atomic_write(self.a.backup_state_path,compact(value),0o600)
                self.assertEqual(self.a._load_backup_settings(),defaults)
        # A file saved before settings_report_plaintext existed keeps working with the config default.
        atomic_write(self.a.backup_state_path,compact(saved),0o600)
        self.assertEqual(self.a._load_backup_settings(),dict({k:saved[k] for k in saved if k!='schema'},
                                                            settings_report_plaintext=False))
        self.a.cfg['mqtt']['backup']['settings_report_plaintext']=True
        self.assertTrue(self.a._load_backup_settings()['settings_report_plaintext'])
        self.a.cfg['mqtt']['backup']['settings_report_plaintext']=False
        atomic_write(self.a.backup_state_path,compact(dict(saved,settings_report_plaintext=True)),0o600)
        self.assertTrue(self.a._load_backup_settings()['settings_report_plaintext'])
        atomic_write(self.a.backup_state_path,compact(dict(saved,settings_report_plaintext='yes')),0o600)
        self.assertEqual(self.a._load_backup_settings(),defaults)
        # 'config' credentials disappear from config.yaml: fall back to config, never to anonymous.
        atomic_write(self.a.backup_state_path,compact(dict(saved,auth='config')),0o600)
        self.assertIsNone(self.a.cfg['mqtt']['backup']['credentials_file'])
        self.assertEqual(self.a._load_backup_settings(),defaults)

    def test_backup_settings_unavailable_on_ground_and_in_demo(self):
        cfg=load_config();cfg['state_dir']=str(self.path/'ground-backup')
        cfg['api']['admin_hash_file']=str(self.path/'ground-backup-auth.json');cfg['api']['port']=0
        set_pin(Path(cfg['api']['admin_hash_file']),self.pin)
        ground=Ground(cfg);self.addCleanup(ground.journal.close)
        server=Server(ground,cfg,ground=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        port=self.port
        try:
            self.port=server.server_port
            code,_,response=self.http('GET','/api/v2/mqtt/backup')
            self.assertEqual((code,json.loads(response)),(404,{'error':'NOT_FOUND'}))
            self.login()
            code,_,response=self.http('GET','/api/v2/mqtt/backup')
            self.assertEqual((code,json.loads(response)),(404,{'error':'NOT_FOUND'}))
            code,_,response=self.http('POST','/api/v2/mqtt/backup',backup_body())
            self.assertEqual((code,json.loads(response)),(404,{'error':'NOT_FOUND'}))
        finally:
            self.port=port;server.shutdown();server.server_close();thread.join(2)
        demo_cfg=copy.deepcopy(self.a.cfg);demo_cfg['state_dir']=str(self.path/'demo-backup')
        demo_cfg['mqtt']['backup'].update(enabled=True,host='backup.example.net')
        demo=Agent(demo_cfg,demo=True)
        try:
            self.assertIsNone(demo.backup_client)
            view=demo.mqtt_backup_view()
            self.assertEqual((view['enabled'],view['editable'],view['client_id']),(False,False,'uav-01-backup-demo'))
            with self.assertRaisesRegex(ValueError,'INVALID_MQTT_BACKUP_SETTINGS'):
                demo.configure_mqtt_backup(backup_body())
        finally:demo.journal.close()


class BackupBrokerEndToEndTests(unittest.TestCase):
    """Primary and backup loopback brokers with a running Agent."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(os.path.realpath(self.tmp.name))
        initial=make_agent(self.path);self.cfg=initial.cfg;initial.journal.close()
        self.creds=self.path/'mqtt.json';self.creds.write_bytes(compact({'username':'test','password':'test-password'}))
        self.cfg['telemetry']['resume_stable_seconds']=1
        self.writer_stop=threading.Event()
        def writer():
            index=2
            while not self.writer_stop.wait(.25):
                index+=1
                atomic_write(self.path/'share'/'status.json',compact(status(idx=index)))
                atomic_write(self.path/'share'/'DOA_value.html',csv_bytes(record()))
        thread=threading.Thread(target=writer,daemon=True);thread.start()
        self.addCleanup(lambda:(self.writer_stop.set(),thread.join(2)))
    def broker(self,**options):
        broker=Broker(**options);self.addCleanup(broker.stop);return broker
    def start_agent(self,primary=None,backup=None,**backup_options):
        cfg=copy.deepcopy(self.cfg)
        if primary:
            cfg['mqtt'].update(enabled=True,host='127.0.0.1',port=primary.port,tls=False,allow_insecure_loopback=True,
                               control_credentials_file=str(self.creds),bulk_credentials_file=str(self.creds))
        if backup:
            cfg['mqtt']['backup'].update(enabled=True,host='127.0.0.1',port=backup.port,tls=False,**backup_options)
        agent=Agent(cfg);agent.monitor=FakeMonitor(cfg);self.addCleanup(agent.stop);agent.start();return agent
    def received(self,broker,agent,suffix):
        with broker.lock:return [m for m in broker.messages if m['topic']==agent.prefix+'/'+suffix]
    def acks(self,broker,agent,id):
        return [json.loads(m['payload'])['status'] for m in self.received(broker,agent,'ack/operation')
                if json.loads(m['payload'])['id']==id]
    def request(self,agent,op,**extras):
        return dict(v=2,id=op.replace('.','-')+'-'+uuid.uuid4().hex[:12],sid=agent.sid,boot=agent.boot,
                    issued_ms=now_ms(),expires_ms=now_ms()+15000,base_rev=agent.source.revision,op=op,**extras)
    def wait_ready(self,agent,primary=True):
        self.assertTrue(wait(lambda:(not primary or (agent.clients and all(c.ready for c in agent.clients.values()))) and
                             agent.backup_client is not None and agent.backup_client.ready,10),
                        [{k:c.status() for k,c in agent.clients.items()},
                         agent.backup_client.status() if agent.backup_client else None])

    def silent_listener(self):
        # Accepts TCP but never answers: a TLS handshake against it blocks until the client's timeout.
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(16);held=[]
        def accept():
            while True:
                try:connection,_=listener.accept();held.append(connection)
                except OSError:return
        threading.Thread(target=accept,daemon=True).start()
        self.addCleanup(lambda:([c.close() for c in held],listener.close()));return listener.getsockname()[1]

    def test_backup_reconfigure_never_pauses_the_primary(self):
        primary=self.broker();stuck=self.silent_listener()
        agent=self.start_agent(primary,None)
        agent.cfg['mqtt']['backup'].update(enabled=True,host='127.0.0.1',port=stuck,tls=True)
        agent.backup_reconfigure.clear();agent.backup_desired=agent._backup_defaults()
        agent.backup_pending=agent.backup_desired;agent.backup_reconfigure.set()
        self.assertTrue(wait(lambda:agent.backup_client is not None and agent.clients and
                             all(c.ready for c in agent.clients.values()),10))
        time.sleep(1.5)  # the backup client is now blocked in its TLS handshake
        before=len(self.received(primary,agent,'telemetry/health'))
        agent.configure_mqtt_backup({'enabled':False,'host':'','port':8883,'transport':'tcp','websocket_path':'/mqtt',
                                     'tls':True,'commands':True,'auth':'none','username':'','password':''})
        time.sleep(4)
        times=[m['time'] for m in self.received(primary,agent,'telemetry/health')]
        self.assertGreater(len(times)-before,2)
        gaps=[b-a for a,b in zip(times[before-1:],times[before:])]
        self.assertLess(max(gaps),2.5,'backup reconfigure stalled primary telemetry')
        self.assertIsNone(agent.backup_client)

    def test_disabling_backup_publishes_offline_availability(self):
        primary=self.broker();backup=self.broker(auth={'':''})
        agent=self.start_agent(primary,backup)
        self.wait_ready(agent)
        self.assertTrue(wait(lambda:json.loads(backup.retained.get(agent.prefix+'/availability',b'{}') or b'{}').get('online') is True,8))
        agent.configure_mqtt_backup({'enabled':False,'host':'127.0.0.1','port':backup.port,'transport':'tcp',
                                     'websocket_path':'/mqtt','tls':False,'commands':True,'auth':'none',
                                     'username':'','password':''})
        def offline():
            value=json.loads(backup.retained.get(agent.prefix+'/availability',b'{}') or b'{}')
            return value.get('online') is False and value.get('reason')=='BACKUP_DISABLED'
        self.assertTrue(wait(offline,8),backup.retained.get(agent.prefix+'/availability'))
        self.assertTrue(wait(lambda:not backup.clients,8))
        self.assertTrue(all(c.ready for c in agent.clients.values()))
        self.assertFalse(primary.errors or backup.errors)

    def test_backup_mirrors_telemetry_and_survives_primary_outage(self):
        primary=self.broker();backup=self.broker(auth={'':''})
        agent=self.start_agent(primary,backup)
        topics=('availability','capabilities','state','telemetry/health','telemetry/doa','telemetry/angular')
        self.assertTrue(wait(lambda:all(self.received(b,agent,t) for b in (primary,backup) for t in topics),15),
                        [agent.snapshot().get('link'),primary.errors,backup.errors])
        self.assertEqual((primary.mqtt_connect_count,backup.mqtt_connect_count),(2,1))
        with backup.lock:flags=backup.clients[0]['connect_flags']
        self.assertEqual(flags&0xc0,0,'backup must connect anonymously when auth is none')
        self.assertTrue(flags&4,'backup must register the availability will')
        for suffix in ('availability','capabilities','state'):
            message=self.received(backup,agent,suffix)[0]
            self.assertEqual((message['qos'],message['retain']),(1,True),suffix)
        for suffix in ('telemetry/health','telemetry/doa','telemetry/angular'):
            self.assertTrue(all(m['qos']==0 and not m['retain'] for m in self.received(backup,agent,suffix)),suffix)
        self.assertTrue(json.loads(self.received(backup,agent,'availability')[0]['payload'])['online'])
        primary_frame=json.loads(self.received(primary,agent,'telemetry/angular')[0]['payload'])
        backup_frame=json.loads(self.received(backup,agent,'telemetry/angular')[0]['payload'])
        self.assertEqual(set(primary_frame),set(backup_frame))
        for key in ('v','encoding','sid','flags','values','frequency_hz','revision','vfo','convention'):
            self.assertEqual(primary_frame[key],backup_frame[key],key)
        self.assertEqual((backup_frame['flags'],len(backup_frame['values'])),(63,360))
        # Primary (PPP/T900) broker goes away: the backup keeps health and angular flowing.
        primary.stop()
        self.assertTrue(wait(lambda:not agent.clients['control'].ready,5))
        def counts():return (len(self.received(backup,agent,'telemetry/health')),
                             len(self.received(backup,agent,'telemetry/angular')))
        health,angular=counts()
        self.assertTrue(wait(lambda:counts()[0]>=health+2 and counts()[1]>=angular+1,12),
                        [counts(),agent.snapshot().get('link')])
        def degraded():
            link=agent.snapshot()['link']
            return (link['mqtt_backup']['state']=='CONNECTED' and link['bulk_pause']=='MQTT_NOT_READY' and
                    link['mqtt_control']['state']!='CONNECTED')
        self.assertTrue(wait(degraded,5),agent.snapshot().get('link'))
        snapshot=agent.snapshot();link=snapshot['link']['mqtt_backup']
        self.assertEqual((link['enabled'],link['ready'],link['bulk_pause'],link['commands']),(True,True,None,True))
        self.assertEqual(link['topic_delivery']['telemetry/health']['state'],'SENT')
        self.assertNotIn('host',link)
        self.assertTrue(snapshot['config']['mqtt_backup_configured'])
        self.assertFalse(backup.errors);self.assertFalse(primary.errors)

    def test_backup_alone_carries_telemetry_when_primary_is_disabled(self):
        backup=self.broker(auth={'':''})
        agent=self.start_agent(None,backup)
        self.assertEqual(agent.clients,{})
        self.assertTrue(wait(lambda:all(self.received(backup,agent,t) for t in
                                        ('availability','state','telemetry/health','telemetry/doa','telemetry/angular')),10),
                        [agent.snapshot().get('link'),backup.errors])
        self.assertTrue(wait(lambda:agent.snapshot()['link']['mqtt_backup']['state']=='CONNECTED',5))
        snapshot=agent.snapshot()
        self.assertEqual((snapshot['link']['mqtt_control']['state'],snapshot['link']['bulk_pause']),('DISABLED','MQTT_NOT_READY'))
        self.assertEqual((snapshot['config']['mqtt_configured'],snapshot['config']['mqtt_backup_configured']),(False,True))
        frame=json.loads(self.received(backup,agent,'telemetry/angular')[0]['payload'])
        self.assertEqual((frame['flags'],len(frame['values'])),(63,360))
        self.assertFalse(backup.errors)

    def test_backup_commands_execute_and_ack_on_their_own_link(self):
        primary=self.broker();backup=self.broker(auth={'':''})
        agent=self.start_agent(primary,backup)
        self.wait_ready(agent)
        with backup.lock:self.assertEqual(len(backup.clients[0]['subs']),len(OPS))
        from_backup=self.request(agent,'stream.set',profile='graph_u8')
        backup._broadcast(agent.prefix+'/cmd/stream/set',compact(from_backup),1)
        self.assertTrue(wait(lambda:'APPLIED' in self.acks(backup,agent,from_backup['id']),10),
                        [self.received(backup,agent,'ack/operation'),agent.snapshot().get('last_operation')])
        self.assertEqual(agent.profile,'graph_u8')
        self.assertEqual(agent.journal.lookup(from_backup['id'])['actor'],'ground-controller')
        from_primary=self.request(agent,'operation.get',target_id=from_backup['id'])
        primary._broadcast(agent.prefix+'/cmd/operation/get',compact(from_primary),1)
        self.assertTrue(wait(lambda:'APPLIED' in self.acks(primary,agent,from_primary['id']) and
                             'APPLIED' in self.acks(backup,agent,from_primary['id']),10),
                        [self.received(primary,agent,'ack/operation'),self.received(backup,agent,'ack/operation')])
        # The primary ACK above was queued after any mirrored backup ACK, so its absence is final.
        self.assertEqual(self.acks(primary,agent,from_backup['id']),[],
                         'a command received from the backup broker must not be acknowledged over the T900 link')
        result=[json.loads(m['payload']) for m in self.received(primary,agent,'ack/operation')
                if json.loads(m['payload'])['id']==from_primary['id']][-1]['result']
        self.assertEqual((result['operation']['id'],result['operation']['stage']),(from_backup['id'],'APPLIED'))
        self.assertFalse(backup.errors);self.assertFalse(primary.errors)

    def test_backup_without_commands_has_no_subscriptions(self):
        primary=self.broker();backup=self.broker(auth={'':''})
        agent=self.start_agent(primary,backup,commands=False)
        self.wait_ready(agent)
        self.assertEqual(agent.backup_client.subscriptions,[])
        with backup.lock:self.assertEqual([c['subs'] for c in backup.clients],[{}])
        ignored=self.request(agent,'stream.set',profile='graph_u8')
        backup._broadcast(agent.prefix+'/cmd/stream/set',compact(ignored),1)
        accepted=self.request(agent,'operation.get',target_id='missing')
        primary._broadcast(agent.prefix+'/cmd/operation/get',compact(accepted),1)
        self.assertTrue(wait(lambda:'APPLIED' in self.acks(primary,agent,accepted['id']) and
                             'APPLIED' in self.acks(backup,agent,accepted['id']),10))
        self.assertIsNone(agent.journal.lookup(ignored['id']))
        self.assertEqual(agent.profile,'balanced')
        self.assertEqual((self.acks(primary,agent,ignored['id']),self.acks(backup,agent,ignored['id'])),([],[]))
        self.assertTrue(wait(lambda:agent.snapshot()['link']['mqtt_backup']['commands'] is False,5))
        self.assertFalse(backup.errors)

    def test_live_reconfigure_never_restarts_the_other_link(self):
        primary=self.broker();backup=self.broker(auth={'':''});replacement=self.broker(auth={'':''})
        agent=self.start_agent(primary,backup)
        self.wait_ready(agent)
        control,bulk=agent.clients['control'],agent.clients['bulk'];old_backup=agent.backup_client
        view=agent.configure_mqtt_backup({'enabled':True,'host':'127.0.0.1','port':replacement.port,'transport':'tcp',
                                          'websocket_path':'/mqtt','tls':False,'commands':True,'auth':'none',
                                          'username':'','password':''})
        self.assertEqual((view['port'],view['trust_mode']),(replacement.port,'plaintext'))
        self.assertTrue(wait(lambda:agent.backup_client is not old_backup and agent.backup_client is not None and
                             agent.backup_client.ready and not backup.clients,10))
        self.assertTrue(wait(lambda:self.received(replacement,agent,'availability') and
                             self.received(replacement,agent,'telemetry/health'),8))
        self.assertIs(agent.clients['control'],control);self.assertIs(agent.clients['bulk'],bulk)
        self.assertTrue(control.ready and bulk.ready)
        self.assertEqual(primary.mqtt_connect_count,2,'backup reconfigure must not reconnect the primary clients')
        current_backup=agent.backup_client;base=agent.mqtt_desired['client_id']
        agent.configure_mqtt({'enabled':True,'host':'127.0.0.1','port':primary.port,'client_id':base,
                              'transport':'tcp','tls':False,'websocket_path':'/mqtt',
                              'control':{'username':'','password':''},'bulk':{'username':'','password':''}})
        self.assertTrue(wait(lambda:agent.clients.get('control') not in (None,control) and
                             all(c.ready for c in agent.clients.values()),10))
        self.assertIs(agent.backup_client,current_backup);self.assertTrue(current_backup.ready)
        self.assertEqual(replacement.mqtt_connect_count,1,'primary reconfigure must not reconnect the backup client')
        # A new primary base id is also the backup's id: the backup reconnects under it.
        second=agent.clients['control']
        agent.configure_mqtt({'enabled':True,'host':'127.0.0.1','port':primary.port,'client_id':'uav-01b',
                              'transport':'tcp','tls':False,'websocket_path':'/mqtt',
                              'control':{'username':'','password':''},'bulk':{'username':'','password':''}})
        self.assertTrue(wait(lambda:agent.clients.get('control') not in (None,second) and
                             all(c.ready for c in agent.clients.values()) and
                             agent.backup_client is not current_backup and agent.backup_client is not None and
                             agent.backup_client.ready,10))
        self.assertEqual(agent.clients['control'].client_id,'uav-01b-control')
        self.assertEqual(agent.backup_client.client_id,'uav-01b-backup')
        self.assertEqual(agent.mqtt_backup_view()['client_id'],'uav-01b-backup')
        self.assertFalse(primary.errors or backup.errors or replacement.errors)
