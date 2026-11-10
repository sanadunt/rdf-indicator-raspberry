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
    def __init__(self,*,running=True,live='keep',start_healthy=True,strict=False):
        self.lock=threading.Lock(); self.running=running; self.healthy=running; self.live=live
        self.start_healthy=start_healthy; self.digest=1; self.status_ts=now_ms()
        self.safe={'center_frequency_hz':105900000,'vfo0_frequency_hz':105900000,'gain_db':12.5,
                   'vfo0_bandwidth_hz':12500,'vfo0_squelch_db':-100.0,'ant_spacing_m':0.3,
                   'ant_arrangement':'UCA','vfo0_squelch_mode':'Manual','vfo_default_squelch_mode':'Auto'}
        self.calls=[]; self.writes_while_running=0; self.revision=1
        # strict: the helper's own checks (policy table, center == VFO0 in one write) refuse before writing.
        self.strict=strict
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
            if self.strict:
                from rdf_node.helper import DEFAULT_POLICY,HelperError,validate_changes
                ch=validate_changes(r['changes'],DEFAULT_POLICY)
                if 'center_frequency_hz' in ch and 'vfo0_frequency_hz' not in ch:
                    raise HelperError('CENTER_CHANGE_REQUIRES_VFO0_TARGET')
                if 'center_frequency_hz' in ch and ch['center_frequency_hz']!=ch['vfo0_frequency_hz']:
                    raise HelperError('MVP_CENTER_MUST_EQUAL_VFO0')
            with self.lock:
                self.safe.update(r['changes']); self.digest+=1; self.revision+=1
                running=self.running
            if running:
                self.writes_while_running+=1
                if self.live=='override_gain': threading.Timer(.3,self._override,args=('gain_db',25.4)).start()
                elif self.live=='crash': threading.Timer(.3,self._crash).start()
                elif self.live=='manual_int_save' and 'center_frequency_hz' in r['changes']:
                    # rdfsdr's retune saves the Manual level it runs: int() of the file's value.
                    threading.Timer(.3,self._override,args=('vfo0_squelch_db',int(self.safe['vfo0_squelch_db']))).start()
                elif self.live=='auto_squelch_save' and 'center_frequency_hz' in r['changes']:
                    # rdfsdr's retune saves the computed auto squelch level back into settings.json.
                    threading.Timer(.3,self._override,args=('vfo0_squelch_db',-93.27)).start()
                elif self.live=='band_spacing' and 'center_frequency_hz' in r['changes']:
                    # rdfsdr's retune: band radius 0.3 m in VHF, saved back into settings.json.
                    threading.Timer(.3,self._override,args=('ant_spacing_m',0.3)).start()
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
        stage,result=cm._execute(r,'ground-controller',lambda st,res,within=None:steps.append((st,dict(res))))
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
    def test_live_bound_follows_the_fallback_decision_not_a_later_status_read(self):
        # The helper status poll can fail ({}) between the fallback decision and the VERIFYING{LIVE} ACK;
        # the restart still runs, so the bound must still cover it.
        e=FakeEngine(live='crash');cm,agent=self.manager(e,retune_live_window_seconds=1);steps=[]
        def helper(r):
            if r['op']=='config.patch':agent.helper_status={}
            return e.helper(r)
        cm._helper=helper
        r={'id':'t1','op':'config.patch','changes':{'vfo0_frequency_hz':105700000,'center_frequency_hz':105700000}}
        stage,result=cm._execute(r,'ground-controller',lambda st,res,within=None:steps.append((st,res.get('method'),within)))
        self.assertEqual((stage,result['method']),('APPLIED','RESTART'))
        self.assertEqual(steps[0],('VERIFYING','LIVE',cm._live_within_s(True)))
        self.assertGreater(cm._live_within_s(True),cm._restart_within_s())
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
    def test_band_spacing_alone_applies_live(self):
        e=FakeEngine();e.safe['ant_spacing_m']=0.2
        stage,result,_,_=self.run_patch(e,{'ant_spacing_m':0.30001})
        self.assertEqual((stage,result['method']),('APPLIED','LIVE'))
        self.assertEqual(self.lifecycle(e),[]);self.assertEqual(e.safe['ant_spacing_m'],0.3)
    def test_spacing_against_the_engine_band_is_refused_before_writing(self):
        # rdfsdr sets 0.3 m in VHF on every retune and at start: 0.2 m could never run.
        e=FakeEngine()
        stage,result,_,_=self.run_patch(e,{'center_frequency_hz':103100000,'vfo0_frequency_hz':103100000,'ant_spacing_m':0.2})
        self.assertEqual((stage,result['error'],result['band_spacing_m']),('FAILED','SPACING_SET_BY_ENGINE_BAND',0.3))
        self.assertEqual(e.calls,[])
        stage,result,_,_=self.run_patch(FakeEngine(running=False),{'ant_spacing_m':0.128})
        self.assertEqual((stage,result['error']),('FAILED','SPACING_SET_BY_ENGINE_BAND'))
    def test_engine_band_spacing_on_retune_is_not_an_override(self):
        e=FakeEngine(live='band_spacing')
        stage,result,_,_=self.run_patch(e,{'center_frequency_hz':103100000,'vfo0_frequency_hz':103100000})
        self.assertEqual((stage,result['method']),('APPLIED','LIVE'));self.assertEqual(self.lifecycle(e),[])
    def test_band_change_writes_the_band_spacing(self):
        e=FakeEngine()
        stage,result,_,_=self.run_patch(e,{'center_frequency_hz':433000000,'vfo0_frequency_hz':433000000})
        self.assertEqual((stage,result['reason']),('APPLIED','LARGE_FREQUENCY_JUMP'))
        self.assertEqual(e.safe['ant_spacing_m'],0.128)
    def test_near_band_spacing_snaps_and_far_or_out_of_range_is_refused(self):
        e=FakeEngine(live='band_spacing')
        stage,result,_,_=self.run_patch(e,{'center_frequency_hz':103100000,'vfo0_frequency_hz':103100000,'ant_spacing_m':0.3001})
        self.assertEqual((stage,result['method']),('APPLIED','LIVE'));self.assertEqual(e.safe['ant_spacing_m'],0.3)
        self.assertEqual(self.lifecycle(e),[])
        for bad,code in ((0.299,'SPACING_SET_BY_ENGINE_BAND'),(5.0,'NUMBER_OUT_OF_RANGE'),(-1,'NUMBER_OUT_OF_RANGE')):
            stage,result,_,_=self.run_patch(FakeEngine(),{'ant_spacing_m':bad})
            self.assertEqual((stage,result['error']),('FAILED',code))
    def test_string_frequency_still_meets_the_band_rule_and_bad_frequency_gets_its_own_error(self):
        stage,result,_,_=self.run_patch(FakeEngine(running=False),{'center_frequency_hz':'433000000','vfo0_frequency_hz':'433000000','ant_spacing_m':0.5})
        self.assertEqual((stage,result['error'],result['band_spacing_m']),('FAILED','SPACING_SET_BY_ENGINE_BAND',0.128))
        self.assertTrue(result['message'])
        stage,result,_,_=self.run_patch(FakeEngine(),{'center_frequency_hz':433000000,'ant_spacing_m':0.5})
        self.assertEqual((stage,result['error']),('FAILED','CENTER_CHANGE_REQUIRES_VFO0_TARGET'))
    def test_any_patch_in_band_corrects_a_stale_spacing_and_says_so(self):
        e=FakeEngine();e.safe['ant_spacing_m']=0.5;del e.safe['ant_arrangement']   # old file: starts as UCA
        stage,result,_,_=self.run_patch(e,{'vfo0_squelch_db':-90.0})
        self.assertEqual((stage,result['method'],result['band_spacing_m']),('APPLIED','LIVE',0.3))
        self.assertEqual(e.safe['ant_spacing_m'],0.3)
    def test_spacing_outside_the_band_rule_is_free(self):
        e=FakeEngine();e.safe['ant_arrangement']='ULA'
        stage,result,_,_=self.run_patch(e,{'ant_spacing_m':0.2})
        self.assertEqual((stage,result['method']),('APPLIED','LIVE'));self.assertEqual(e.safe['ant_spacing_m'],0.2)
    def test_squelch_level_is_whole_db_and_applies_live(self):
        e=FakeEngine()
        stage,result,_,_=self.run_patch(e,{'vfo0_squelch_db':-70.4})
        self.assertEqual((stage,result['method']),('APPLIED','LIVE'));self.assertEqual(e.safe['vfo0_squelch_db'],-70)
        self.assertNotIn('squelch_mode',result)
    def test_squelch_level_switches_an_automatic_squelch_to_manual(self):
        e=FakeEngine();e.safe['vfo0_squelch_mode']='Auto Channel'
        stage,result,_,_=self.run_patch(e,{'vfo0_squelch_db':-70})
        self.assertEqual((stage,result['squelch_mode']),('APPLIED','Manual'))
        self.assertEqual((e.safe['vfo0_squelch_mode'],e.safe['vfo0_squelch_db']),('Manual',-70))
    def test_auto_squelch_mode_applies_live_and_engine_level_is_not_an_override(self):
        e=FakeEngine(live='auto_squelch_save')
        stage,result,_,_=self.run_patch(e,{'vfo0_squelch_mode':'Auto Channel'})
        self.assertEqual((stage,result['method']),('APPLIED','LIVE'))
        stage,result,_,_=self.run_patch(e,{'center_frequency_hz':103100000,'vfo0_frequency_hz':103100000})
        self.assertEqual((stage,result['method']),('APPLIED','LIVE'));self.assertEqual(self.lifecycle(e),[])
        self.assertEqual(e.safe['vfo0_squelch_db'],-93.27)
    def test_leaving_auto_writes_the_level_the_engine_runs_so_a_retune_stays_live(self):
        e=FakeEngine(live='manual_int_save');e.safe.update(vfo0_squelch_mode='Auto',vfo0_squelch_db=-93.27)
        stage,result,_,_=self.run_patch(e,{'vfo0_squelch_mode':'Manual'})
        self.assertEqual((stage,e.safe['vfo0_squelch_db']),('APPLIED',-93))
        stage,result,_,_=self.run_patch(e,{'center_frequency_hz':103100000,'vfo0_frequency_hz':103100000})
        self.assertEqual((stage,result['method']),('APPLIED','LIVE'));self.assertEqual(self.lifecycle(e),[])
    def test_missing_mode_keys_mean_default_auto_like_the_engine(self):
        e=FakeEngine();del e.safe['vfo0_squelch_mode'];del e.safe['vfo_default_squelch_mode']
        stage,result,_,_=self.run_patch(e,{'vfo0_squelch_db':-50})
        self.assertEqual((stage,result['squelch_mode']),('APPLIED','Manual'))
    def test_unknown_squelch_mode_is_refused(self):
        stage,result,_,_=self.run_patch(FakeEngine(),{'vfo0_squelch_mode':'Loud'})
        self.assertEqual((stage,result['error']),('FAILED','SQUELCH_MODE_INVALID'))
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

    # A refused write must never leave the engine stopped (found 2026-11-10 in a read-only audit).
    def offset_engine(self,**kw):
        e=FakeEngine(strict=True,**kw)
        e.safe.update(center_frequency_hz=400000000,vfo0_frequency_hz=399999712,ant_spacing_m=0.128)
        return e
    def test_restart_onto_the_old_vfo_also_writes_the_vfo(self):
        # Center moves onto the VFO0 it already had, with a gain change: VFO0 equals the file, but the helper
        # needs it in the same write as the center.
        e=self.offset_engine()
        stage,result,_,_=self.run_patch(e,{'center_frequency_hz':399999712,'vfo0_frequency_hz':399999712,'gain_db':15.7})
        self.assertEqual((stage,result['method'],result['reason']),('APPLIED','RESTART','GAIN_CHANGE'))
        self.assertEqual((e.safe['center_frequency_hz'],e.safe['vfo0_frequency_hz'],e.safe['gain_db']),(399999712,399999712,15.7))
        self.assertEqual(self.lifecycle(e),['STOPPED','RUNNING'])
    def test_a_write_the_helper_refuses_after_the_stop_starts_the_engine_again(self):
        e=self.offset_engine()
        stage,result,_,_=self.run_patch(e,{'gain_db':13.0})  # not in the approved gain table
        self.assertEqual((stage,result['error'],result['persisted']),('FAILED','GAIN_NOT_IN_APPROVED_TABLE',False))
        self.assertEqual(result['engine'],'RESTARTED')
        self.assertEqual(self.lifecycle(e),['STOPPED','RUNNING'])
        self.assertTrue(e.running);self.assertEqual(e.safe['gain_db'],12.5)
    def test_a_refused_write_whose_restart_fails_is_outcome_unknown(self):
        e=self.offset_engine(start_healthy=False)
        stage,result,_,_=self.run_patch(e,{'gain_db':13.0})
        self.assertEqual((stage,result['error'],result['engine']),('OUTCOME_UNKNOWN','GAIN_NOT_IN_APPROVED_TABLE','NOT_HEALTHY_AFTER_RESTART'))
        self.assertFalse(result['persisted'])
    def test_error_messages_have_no_duplicate_codes(self):
        import ast,inspect
        from rdf_node import control
        tree=ast.parse(inspect.getsource(control))
        for node in ast.walk(tree):
            if isinstance(node,ast.Assign) and any(getattr(t,'id',None)=='ERROR_MESSAGES' for t in node.targets):
                keys=[k.value for k in node.value.keys]
                self.assertEqual(sorted({k for k in keys if keys.count(k)>1}),[])

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
    def test_state_carries_squelch_mode_and_hides_the_level_when_automatic(self):
        import validate as contract
        a=self.a
        a.source.safe={**a.source.safe,'vfo0_squelch_mode':'Manual','vfo0_squelch_db':-80.0}
        rf=a._state(a.snapshot())['rf'];self.assertEqual((rf['sqm'],rf['sq']),('Manual',-80.0))
        a.source.safe={**a.source.safe,'vfo0_squelch_mode':'Default','vfo_default_squelch_mode':'Auto'}
        state=a._state(a.snapshot());self.assertEqual(state['rf']['sqm'],'Default');self.assertNotIn('sq',state['rf'])
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

class SpacingFieldTests(unittest.TestCase):
    """ant_spacing_m (contract 1.8): the helper accepts it in metres and writes ant_spacing_meters."""
    def test_helper_accepts_spacing_in_range_only(self):
        from rdf_node.helper import validate_changes,HelperError,DEFAULT_POLICY
        self.assertEqual(validate_changes({'ant_spacing_m':0.3},DEFAULT_POLICY),{'ant_spacing_m':0.3})
        for bad in (0.01,1.5,float('nan'),True,None):
            with self.assertRaises((HelperError,ValueError,TypeError)): validate_changes({'ant_spacing_m':bad},DEFAULT_POLICY)
    def test_helper_squelch_fields(self):
        from rdf_node.helper import validate_changes,HelperError,DEFAULT_POLICY
        self.assertEqual(validate_changes({'vfo0_squelch_db':-70.6,'vfo0_squelch_mode':'Auto Channel'},DEFAULT_POLICY),
                         {'vfo0_squelch_db':-71,'vfo0_squelch_mode':'Auto Channel'})
        for bad in ('auto',3,None):
            with self.assertRaises((HelperError,ValueError,TypeError)): validate_changes({'vfo0_squelch_mode':bad},DEFAULT_POLICY)
    def test_safe_settings_reads_squelch_mode_and_drops_unknown(self):
        from rdf_node.source import safe_settings
        bad=[]
        self.assertEqual(safe_settings({'vfo_squelch_mode_0':'Manual','vfo_default_squelch_mode':'Auto'})['vfo0_squelch_mode'],'Manual')
        self.assertNotIn('vfo0_squelch_mode',safe_settings({'vfo_squelch_mode_0':'Weird'},bad));self.assertEqual(bad,['vfo0_squelch_mode'])
    def test_safe_settings_and_capabilities_list_it(self):
        from rdf_node.source import safe_settings,FIELD_MAP
        self.assertEqual(safe_settings({'ant_spacing_meters':0.128})['ant_spacing_m'],0.128)
        self.assertIn('ant_spacing_m',FIELD_MAP)

if __name__=='__main__': unittest.main()
