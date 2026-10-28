import copy,json,os,sys,tempfile,threading,unittest,uuid
from pathlib import Path
from unittest import mock
from rdf_node import __version__,CONTRACT
from rdf_node.mqtt import Client
from rdf_node.util import now_ms,compact
from test_system import make_agent

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'contract'))
from validate import errors,schema_for,SCHEMA_DIR

EDGE_TOPICS=('availability','capabilities','state','telemetry/health','telemetry/health/detail','telemetry/doa',
             'telemetry/diagnostic/doa','telemetry/angular','telemetry/diagnostic/angular','settings/reported',
             'ack/config','ack/operation')

class ContractTests(unittest.TestCase):
    """Every payload the Edge builds must match contract/schema/<topic>.json."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.a=make_agent(Path(os.path.realpath(self.tmp.name)));self.addCleanup(self.a.journal.close)
        self.sent=[]
        for name in ('control','bulk'):
            client=Client(cfg={},client_id='contract-'+name);client.ready=True
            client.offer=lambda key,topic,payload,**options:self.sent.append((topic,payload)) or True
            self.a.clients[name]=client
    def tick(self,snapshot):
        a=self.a;a._generation=-1;a.stop_event.clear()
        def one():
            a.stop_event.set();return snapshot
        with mock.patch.object(a,'snapshot',side_effect=one):a._schedule()
    def request(self,op,**extras):
        return dict(v=2,id='contract-'+uuid.uuid4().hex,sid=self.a.sid,boot=self.a.boot,issued_ms=now_ms(),
                    expires_ms=now_ms()+15000,base_rev=self.a.source.revision,op=op,**extras)
    def assertConforms(self,topic,payload):
        value=json.loads(payload) if isinstance(payload,(bytes,str)) else payload
        self.assertEqual(errors(value,schema_for(topic)),[],f'{topic}: {value}')
    def published(self):
        seen={}
        for topic,payload in self.sent:
            suffix=topic.split('/v2/',1)[1].split('/',1)[1]
            self.assertConforms(suffix,payload);seen.setdefault(suffix,0);seen[suffix]+=1
        return seen

    def test_every_edge_topic_has_a_schema_that_names_it(self):
        for topic in EDGE_TOPICS+('cmd/config/patch',):
            schema=schema_for(topic)
            self.assertEqual(schema['x-topic'],'cmd' if topic.startswith('cmd/') else topic)
            self.assertTrue(schema['additionalProperties'])
        self.assertEqual(len(list(SCHEMA_DIR.glob('*.json'))),len(EDGE_TOPICS)+1)

    def test_contract_is_announced_with_the_release(self):
        self.assertTrue(__version__.startswith(CONTRACT+'.'),(__version__,CONTRACT))
        self.tick(self.a.snapshot())
        for topic,payload in self.sent:
            if topic.endswith(('/availability','/capabilities','/state')):
                self.assertEqual(json.loads(payload)['contract'],CONTRACT,topic)
        will_topic,will=self.a._will()
        self.assertConforms('availability',will);self.assertEqual(json.loads(will)['contract'],CONTRACT)

    def test_scheduled_telemetry_conforms_in_every_doa_state(self):
        valid=self.a.snapshot();self.tick(valid)
        labelled=copy.deepcopy(valid);labelled['detection'].update(valid=False,relative_doa_deg=None,
            reasons=['STATUS_STALE_OR_UNAVAILABLE','DAQ_NOT_HEALTHY','CLOCK_UNTRUSTED'],q=valid['detection']['q']+1)
        labelled['host']['clock_trusted']=False;self.tick(labelled)
        approval=copy.deepcopy(valid);approval['detection'].update(valid=False,relative_doa_deg=None,
            reasons=['SOURCE_UNVERIFIED'],q=valid['detection']['q']+2);self.tick(approval)
        diagnostic=copy.deepcopy(valid);diagnostic['diagnostic_doa'].update(available=True,source_timestamp_ms=now_ms(),
            observed_timestamp_ms=now_ms(),raw_doa_deg=123.4,frequency_mhz=433.92,
            validation_reasons=['DIAGNOSTIC_UNVERIFIED']);self.tick(diagnostic)
        seen=self.published()
        for topic in ('availability','capabilities','state','telemetry/health','telemetry/health/detail',
                      'telemetry/doa','telemetry/diagnostic/doa'):
            self.assertIn(topic,seen)
        oks=[json.loads(payload)['ok'] for topic,payload in self.sent if topic.endswith('/telemetry/doa')]
        self.assertEqual(sorted(set(oks)),[0,1])

    def test_angular_frames_conform(self):
        s=self.a.snapshot();d=s['detection'];r=self.a.source.record
        self.assertConforms('telemetry/angular',self.a._live_angular(d,r,s['host'],s['daq']))
        blocked=dict(d,config_attributed=False)
        self.assertConforms('telemetry/diagnostic/angular',self.a._diagnostic_angular(blocked,r,s['host'],s['daq']))

    def test_command_acks_and_settings_report_conform(self):
        a=self.a;acks=[]
        a.operation_changed=lambda id,op,stage,result,link=None:acks.append(
            ('ack/config' if op.startswith('config.') else 'ack/operation',
             {'v':2,'sid':a.sid,'id':id,'status':stage,'t':now_ms(),'rev':a.source.revision,'result':result}))
        a.commands.start();self.addCleanup(a.commands.stop)
        bad_profile=self.request('stream.set',profile='unknown')
        self.assertTrue(errors(bad_profile,schema_for('cmd/stream/set')))
        for request in (self.request('config.get'),self.request('stream.set',profile='balanced',future_field=1),
                        bad_profile,self.request('config.patch',changes={'gain_db':20.7})):
            if request is not bad_profile:self.assertConforms('cmd/'+request['op'],request)
            a.commands.submit(request,'ground-controller');a.commands.queue.join()
        for topic,payload in acks:self.assertConforms(topic,payload)
        self.assertEqual({payload['status'] for _,payload in acks},{'APPLIED','ACCEPTED','REJECTED'})
        self.tick(a.snapshot())
        reports=[payload for topic,payload in self.sent if topic.endswith('/settings/reported')]
        self.assertEqual(len(reports),1);self.assertConforms('settings/reported',reports[0])

    def test_validator_reports_real_breaks(self):
        good={'v':2,'sid':'0a1b2c3d','q':1,'t':now_ms(),'f':433920000,'a':12.5,'c':8.0,'p':-90.0,'rev':1,
              'ok':0,'clk':1,'age':10,'reason':['DAQ_NOT_HEALTHY'],'trust':'UNVERIFIED','angle_reference':'RAW'}
        schema=schema_for('sdr/v2/uav-01/telemetry/doa')
        self.assertEqual(errors(good,schema),[])
        self.assertEqual(errors(dict(good,extra='allowed'),schema),[])
        for broken in (dict(good,ok=2),{k:v for k,v in good.items() if k!='trust'},dict(good,ok=1),
                       dict(good,a=361),dict(good,sid='NOT-HEX!'),dict(good,ok=True),dict(good,reason='DAQ')):
            with self.subTest(broken=broken):self.assertTrue(errors(broken,schema))
        frame=schema_for('telemetry/angular')
        self.assertTrue(errors({'v':2,'values':[0.0]*359},frame))

    def test_documented_examples_match_their_schemas(self):
        import re
        docs=Path(__file__).resolve().parents[1]/'docs';checked=0
        for doc in ('MQTT_TOPIC_SUMMARY.md','PROTOCOL.md'):
            text=(docs/doc).read_text(encoding='utf-8')
            for topic,body in re.findall(r'^### `([a-z/]+)`\n(.*?)(?=^##)',text,re.S|re.M):
                if not (SCHEMA_DIR/(topic.replace('/','.')+'.json')).exists():continue
                for block in re.findall(r'```json\n(.*?)```',body,re.S):
                    for line in block.strip().splitlines():
                        self.assertEqual(errors(json.loads(line),schema_for(topic)),[],f'{doc} {topic}: {line[:80]}')
                        checked+=1
        self.assertGreaterEqual(checked,10)
