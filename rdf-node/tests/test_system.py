import copy,http.client,json,os,tempfile,threading,time,unittest,uuid
from pathlib import Path
from unittest import mock
from rdf_node.agent import Agent
from rdf_node.ground import Ground
from rdf_node.config import load_config
from rdf_node.api import Server,set_pin
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
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
        self.a=make_agent(self.path);self.addCleanup(self.a.journal.close)
    def request(self,op='config.get',**extras):
        return dict(v=2,id='test-'+uuid.uuid4().hex,sid=self.a.sid,boot=self.a.boot,issued_ms=now_ms(),expires_ms=now_ms()+15000,base_rev=self.a.source.revision,op=op,**extras)
    def test_first_observation_not_frame_progress(self):
        from rdf_node.source import Source
        s=Source(self.a.cfg,self.a.journal);s.poll(force=True)
        self.assertFalse(s.view(True)['daq']['frame_progressing'])
    def test_second_frame_proves_progress(self):self.assertTrue(self.a.source.view(True)['valid'])
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
    def test_snapshot_no_secrets(self):
        code,h,b=self.http('GET','/api/v2/snapshot');self.assertEqual(code,200);self.assertNotIn(self.pin.encode(),b);self.assertNotIn(b'admin_hash_file',b);self.assertEqual(h['Cache-Control'],'no-store')
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

class EndToEndTests(unittest.TestCase):
    def test_agent_graph_receipt_query_and_stopped_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);a=make_agent(path)
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
            thread=threading.Thread(target=writer,daemon=True);thread.start();a.start();g.start()
            try:
                self.assertTrue(wait(lambda:g.snapshot()['health_fresh'],12),[a.snapshot(),g.snapshot(),b.errors])
                self.assertTrue(wait(lambda:a.receipt_view()['state']=='RECEIVING',12),[a.receipt_view(),g.snapshot()])
                self.assertTrue(wait(lambda:g.angular_view() is not None,20),[a.snapshot(),g.snapshot(),b.errors])
                self.assertEqual(len(g.angular_view()['values']),360)
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
