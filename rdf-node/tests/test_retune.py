"""config.patch on a running SDR engine: live retune first, engine restart when needed or when the live
one fails. A fake engine reproduces what rdfsdr did on the Pi (2026-11-01/02): a live retune re-applies the
band gain and can lose DAQ sync; at start the engine takes settings.json as written."""
import tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from rdf_node.config import load_config
from rdf_node.control import CommandManager
from rdf_node.journal import Journal
from rdf_node.util import now_ms

class FakeEngine:
    def __init__(self,*,running=True,live='keep',start_healthy=True):
        self.lock=threading.Lock(); self.running=running; self.healthy=running; self.live=live
        self.start_healthy=start_healthy; self.digest=1; self.status_ts=now_ms()
        self.safe={'center_frequency_hz':105900000,'vfo0_frequency_hz':105900000,'gain_db':12.5,
                   'vfo0_bandwidth_hz':12500,'vfo0_squelch_db':-100.0}
        self.calls=[]; self.writes_while_running=0; self.revision=1
    # ---- what CommandManager reads ----
    @property
    def raw_digest(self):
        with self.lock: return f'd{self.digest}'
    @property
    def status(self):
        with self.lock:
            if self.running and self.healthy: self.status_ts=now_ms()
            return {'timestamp_ms':self.status_ts}
    record=None
    def view(self,clock):
        with self.lock: return {'daq':{'healthy':self.running and self.healthy}}
    def snapshot(self):
        with self.lock: return {'service_state':'ACTIVE' if self.running else 'INACTIVE','cgroup_empty':not self.running,
                                'clock_trusted':True,'generation':'g'}
    # ---- the helper ----
    def helper(self,r,timeout=8):
        self.calls.append((r['op'],r.get('desired'),dict(r.get('changes') or {})))
        if r['op']=='config.patch':
            assert r['expected_digest']==self.raw_digest,'stale digest'
            with self.lock:
                self.safe.update(r['changes']); self.digest+=1; self.revision+=1
                running=self.running
            if running:
                self.writes_while_running+=1
                if self.live=='override_gain': threading.Timer(.3,self._override,args=('gain_db',25.4)).start()
                elif self.live=='crash': threading.Timer(.3,self._crash).start()
            return {'persisted':True}
        if r['op']=='processing.set':
            if r['desired']=='STOPPED':
                with self.lock: self.running=False; self.healthy=False
            else:
                def up():
                    with self.lock: self.running=True; self.healthy=self.start_healthy
                threading.Timer(.4,up).start()
            return {'accepted':True}
        raise AssertionError(r)
    def _override(self,key,value):
        with self.lock: self.safe[key]=value; self.digest+=1
    def _crash(self):
        with self.lock: self.healthy=False

class RetuneTests(unittest.TestCase):
    def manager(self,engine,**control):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        cfg=load_config();cfg['control'].update({'processing_enabled':True,'retune_live_window_seconds':3,
                                              'retune_settle_seconds':1,'start_timeout_seconds':6,**control})
        journal=Journal(Path(tmp.name)/'j.sqlite3');self.addCleanup(journal.close)
        agent=SimpleNamespace(cfg=cfg,journal=journal,helper_status={'allow_lifecycle':True},config_proof='unverified',
                              operation_seq=0,source=engine,monitor=engine,operation_changed=lambda *a,**k:None)
        cm=CommandManager(agent);cm._helper=engine.helper
        return cm,agent
    def run_patch(self,engine,changes,**control):
        cm,agent=self.manager(engine,**control);steps=[]
        r={'id':'t1','op':'config.patch','changes':changes}
        stage,result=cm._execute(r,'ground-controller',lambda st,res:steps.append((st,dict(res))))
        return stage,result,steps,agent
    def lifecycle(self,engine):
        return [d for op,d,_ in engine.calls if op=='processing.set']
    def test_small_frequency_change_applies_live(self):
        e=FakeEngine()
        stage,result,steps,agent=self.run_patch(e,{'center_frequency_hz':103100000,'vfo0_frequency_hz':103100000})
        self.assertEqual((stage,result['method']),('APPLIED','LIVE'))
        self.assertEqual(result['proof']['daq'],'FRESH_HEALTHY_AFTER_RETUNE')
        self.assertEqual(self.lifecycle(e),[]);self.assertEqual(steps[0][0],'VERIFYING')
        self.assertEqual(agent.config_proof,'runtime')
    def test_engine_overriding_gain_on_live_retune_falls_back_to_restart(self):
        e=FakeEngine(live='override_gain')
        stage,result,steps,_=self.run_patch(e,{'center_frequency_hz':103100000,'vfo0_frequency_hz':103100000})
        self.assertEqual((stage,result['method']),('APPLIED','RESTART'))
        self.assertEqual(result['reason'],'LIVE_FAILED:ENGINE_OVERRODE:gain_db')
        self.assertEqual(self.lifecycle(e),['STOPPED','RUNNING'])
        # The gain we had is written back while the engine is stopped, and stays after start.
        self.assertEqual((e.safe['gain_db'],e.safe['center_frequency_hz']),(12.5,103100000))
        self.assertIn(('APPLYING','RESTART'),[(st,res.get('method')) for st,res in steps])
    def test_lost_daq_sync_after_live_retune_falls_back_to_restart(self):
        e=FakeEngine(live='crash')
        stage,result,_,_=self.run_patch(e,{'vfo0_frequency_hz':105700000,'center_frequency_hz':105700000})
        self.assertEqual((stage,result['method'],result['reason']),('APPLIED','RESTART','LIVE_FAILED:DAQ_NOT_HEALTHY_AFTER_RETUNE'))
    def test_gain_change_restarts_without_a_live_attempt(self):
        e=FakeEngine(live='crash')
        stage,result,_,_=self.run_patch(e,{'gain_db':7.7})
        self.assertEqual((stage,result['method'],result['reason']),('APPLIED','RESTART','GAIN_CHANGE'))
        self.assertEqual(e.writes_while_running,0);self.assertEqual(e.safe['gain_db'],7.7)
    def test_large_jump_restarts_without_a_live_attempt(self):
        e=FakeEngine(live='crash')
        stage,result,_,_=self.run_patch(e,{'center_frequency_hz':402225000,'vfo0_frequency_hz':402225000})
        self.assertEqual((stage,result['reason']),('APPLIED','LARGE_FREQUENCY_JUMP'))
        self.assertEqual(e.writes_while_running,0)
    def test_stopped_engine_only_gets_the_file(self):
        e=FakeEngine(running=False)
        stage,result,_,_=self.run_patch(e,{'gain_db':7.7})
        self.assertEqual((stage,result['method'],result['reason']),('PERSISTED_UNVERIFIED','FILE','ENGINE_NOT_RUNNING'))
        self.assertEqual(self.lifecycle(e),[])
    def test_without_lifecycle_approval_a_failed_live_retune_is_reported_not_restarted(self):
        e=FakeEngine(live='override_gain')
        stage,result,_,_=self.run_patch(e,{'center_frequency_hz':103100000,'vfo0_frequency_hz':103100000},processing_enabled=False)
        self.assertEqual(stage,'PERSISTED_UNVERIFIED');self.assertEqual(result['live_failure'],'ENGINE_OVERRODE:gain_db')
        self.assertEqual(self.lifecycle(e),[])
    def test_engine_unhealthy_after_restart_is_outcome_unknown(self):
        e=FakeEngine(start_healthy=False)
        stage,result,_,_=self.run_patch(e,{'gain_db':7.7})
        self.assertEqual((stage,result['error']),('OUTCOME_UNKNOWN','DAQ_NOT_HEALTHY_AFTER_RESTART'))
        self.assertTrue(result['persisted'])

if __name__=='__main__': unittest.main()

class RfStatePayloadTests(unittest.TestCase):
    """state.rf and health/detail.ovr (contract 1.7): Ground's frequency/gain/overdrive without Data Out."""
    def setUp(self):
        import os,sys
        from test_system import make_agent
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'contract'))
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.a=make_agent(Path(os.path.realpath(tmp.name)));self.addCleanup(self.a.journal.close)
    def test_state_carries_the_sdr_settings(self):
        import validate as contract
        a=self.a;state=a._state(a.snapshot())
        self.assertEqual(set(state['rf']),{'f','vfo','g','bw','sq'}&set(state['rf']))
        self.assertEqual(state['rf']['f'],a.source.safe['center_frequency_hz'])
        self.assertEqual(contract.errors(state,contract.schema_for('state')),[])
    def test_detail_carries_adc_overdrive(self):
        import json,validate as contract
        from rdf_node.mqtt import Client
        a=self.a;out=[];c=Client(cfg={},client_id='x');c.ready=True
        c.offer=lambda key,topic,payload,**o: out.append((topic,payload)) or True
        a.clients={'control':c};a._generation=-1;snap=a.snapshot()
        def once(): a.stop_event.set();return snap
        a.snapshot=once;a.stop_event.clear();a._schedule()
        detail=next(json.loads(p) for t,p in out if t.endswith('/telemetry/health/detail'))
        self.assertIn('ovr',detail);self.assertIn(detail['ovr'],(True,False,None))
        self.assertEqual(contract.errors(detail,contract.schema_for('telemetry/health/detail')),[])
