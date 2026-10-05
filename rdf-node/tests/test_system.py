import copy,http.client,json,os,stat,tempfile,threading,time,unittest,uuid
from pathlib import Path
from unittest import mock
from rdf_node.agent import Agent
from rdf_node.ground import Ground
from rdf_node.mqtt import Client
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
    def test_receipt_unknown_session_rejected(self):
        self.a.sent_health.append(1);self.a.accept_receipt({'v':2,'sid':'00000000','hq':1});self.assertEqual(self.a.receipt_rejects,1)
    def test_receipt_unsent_sequence_rejected(self):
        self.a.accept_receipt({'v':2,'sid':self.a.sid,'hq':1});self.assertEqual(self.a.receipt_view()['state'],'UNCONFIRMED')
    def test_duplicate_receipt_does_not_refresh_progress(self):
        self.a.sent_health.append(1);r={'v':2,'sid':self.a.sid,'hq':1};self.a.accept_receipt(r);n=self.a.receipt_progress
        time.sleep(.005);self.a.accept_receipt(r);self.assertEqual(n,self.a.receipt_progress)
    def test_receipt_during_stopped_engine_uses_health(self):
        self.a.monitor.data['service_state']='INACTIVE';self.a.sent_health.append(4)
        self.a.accept_receipt({'v':2,'sid':self.a.sid,'hq':4,'dq':0,'aq':0});self.assertEqual(self.a.receipt_view()['state'],'RECEIVING')
    def test_read_only_query_works(self):self.assertEqual(self.a.commands.submit(self.request(),'local-admin')['stage'],'APPLIED')
    def test_retained_command_rejected(self):self.assertEqual(self.a.commands.submit(self.request(),'ground',retained=True)['stage'],'REJECTED')
    def test_command_topic_mismatch(self):self.assertEqual(self.a.commands.submit(self.request(),'ground',topic=self.a.prefix+'/cmd/service/restart')['stage'],'REJECTED')
    def test_stale_session_rejected(self):
        r=self.request();r['sid']='00000000';self.assertEqual(self.a.commands.submit(r,'local-admin')['stage'],'REJECTED')
    def test_expired_command_rejected(self):
        r=self.request();r['expires_ms']=now_ms()-1;self.assertEqual(self.a.commands.submit(r,'local-admin')['stage'],'REJECTED')
    def test_remote_write_disabled(self):self.assertEqual(self.a.commands.submit(self.request('stream.set',profile='control'),'ground-controller')['result']['error'],'REMOTE_COMMANDS_DISABLED')
    def test_clock_untrusted_blocks_local_write(self):
        self.a.monitor.data['clock_trusted']=False;self.assertEqual(self.a.commands.submit(self.request('stream.set',profile='control'),'local-admin')['result']['error'],'CLOCK_UNTRUSTED')
    def test_config_revision_conflict(self):
        r=self.request('stream.set',profile='control');r['base_rev']=42;self.assertEqual(self.a.commands.submit(r,'local-admin')['result']['error'],'CONFIG_REVISION_CONFLICT')
    def test_no_hardware_write_in_demo(self):
        self.a.demo=True;self.assertEqual(self.a.commands.submit(self.request('stream.set',profile='control'),'local-admin')['result']['error'],'DEMO_NO_HARDWARE_CONTROL')
    def test_disabled_reboot_rejected(self):self.assertEqual(self.a.commands.submit(self.request('system.reboot.prepare'),'local-admin')['result']['error'],'CAPABILITY_DISABLED')
    def test_disabled_shutdown_rejected(self):self.assertEqual(self.a.commands.submit(self.request('system.shutdown.prepare'),'local-admin')['result']['error'],'CAPABILITY_DISABLED')
    def test_shutdown_capability_requires_config_and_helper_approval(self):
        self.a.cfg['runtime_mode']='controlled';self.a.cfg['control']['shutdown_enabled']=True
        self.a.helper_status={'allow_shutdown':False}
        self.assertFalse(self.a.capabilities()['shutdown'])
        self.a.helper_status={'allow_shutdown':True}
        self.assertTrue(self.a.capabilities()['shutdown'])
    def test_ground_shutdown_requires_remote_commands_enabled(self):
        self.a.cfg['runtime_mode']='controlled';self.a.cfg['control']['shutdown_enabled']=True
        self.assertEqual(self.a.commands.submit(self.request('system.shutdown.prepare'),'ground-controller')['result']['error'],'REMOTE_COMMANDS_DISABLED')
    def test_shutdown_is_scheduled_only_after_durable_marker(self):
        request=self.request('system.shutdown.execute',prepare_id='prepared',challenge='fixture')
        self.a.journal.accept(request,'local-admin')
        observed=[]
        def helper(payload):
            observed.append((self.a.journal.lookup(request['id'])['stage'],payload))
            return {'scheduled':True,'delay_seconds':5}
        with mock.patch.object(self.a.commands,'_helper',side_effect=helper):
            stage,result=self.a.commands._execute(request)
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
        r=self.request();self.a.commands.submit(r,'local-admin');old=self.a.journal.lookup(r['id'])
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

    def test_live_mqtt_reconfiguration_invalidates_old_ground_receipt(self):
        broker=Broker();self.addCleanup(broker.stop)
        credentials=self.path/'mqtt.json';credentials.write_bytes(compact({'username':'test','password':'test-password'}))
        self.a.journal.close();cfg=self.a.cfg
        cfg['mqtt'].update(enabled=True,host='127.0.0.1',port=broker.port,tls=False,allow_insecure_loopback=True,
                           control_credentials_file=str(credentials),bulk_credentials_file=str(credentials))
        agent=Agent(cfg);agent.monitor=FakeMonitor(cfg);self.addCleanup(agent.stop);agent.start()
        self.assertTrue(wait(lambda:len(broker.clients)==2,8),broker.errors)
        with agent.lock: agent.sent_health.append(0xfffffffe)
        agent.accept_receipt({'v':2,'sid':agent.sid,'hq':0xfffffffe})
        self.assertEqual(agent.receipt_view()['state'],'RECEIVING')
        agent.configure_mqtt({'enabled':False,'host':'ground.example','port':8883,'client_id':'new-node',
                              'transport':'tcp','tls':True,'websocket_path':'/mqtt',
                              'control':{'username':'','password':''},'bulk':{'username':'','password':''}})
        self.assertTrue(wait(lambda:not agent.clients and not broker.clients,8),[agent.clients,broker.clients])
        self.assertFalse(agent.cfg['mqtt']['enabled'])
        self.assertEqual(agent.cfg['mqtt']['host'],'ground.example')
        self.assertEqual(agent.receipt_view()['state'],'UNCONFIRMED')


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
    def test_unknown_or_invalid_preference_rejected(self):
        self.login()
        for body in ({'run_shell':'true'},{'accent':'ultraviolet'},{'font':'comic-sans'}):
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
    def test_agent_graph_receipt_query_and_stopped_health(self):
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
                self.assertTrue(wait(lambda:a.receipt_view()['state']=='RECEIVING',12),[a.receipt_view(),g.snapshot()])
                self.assertTrue(wait(lambda:g.angular_view() is not None,20),[a.snapshot(),g.snapshot(),b.errors])
                self.assertEqual(len(g.angular_view()['values']),360)
                self.assertTrue(g.snapshot()['detection']['valid'])
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
                op=g.submit_command({'op':'config.get'})
                self.assertTrue(wait(lambda:g.journal.lookup(op['id'])['stage']=='APPLIED',10),g.journal.lookup(op['id']))
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
                self.assertTrue(wait(lambda:agent.receipt_view()['state']=='RECEIVING',12),agent.receipt_view())
                self.assertEqual(agent.receipt_view()['last'].get('dq'),0)
                self.assertNotEqual(agent.snapshot()['link']['mqtt_topic_delivery'].get(
                    'telemetry/doa',{}).get('state'),'SENT')
                self.assertFalse(broker.errors)
            finally:
                stop.set();thread.join(2);agent.stop();ground.stop();broker.stop()
