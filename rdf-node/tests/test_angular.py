import json,math,os,sys,tempfile,unittest
from pathlib import Path
from rdf_node import angular,codec
from rdf_node.config import load_config,validate_config
from rdf_node.util import compact
from test_system import make_agent,angular_payload

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'contract'))
from validate import errors,schema_for

def spike(index,size=180):
    v=[0.0]*size;v[index]=1.0;return v

class PairTests(unittest.TestCase):
    def test_average_pairs_example(self):
        out=angular.average_pairs([0.1,0.5]+[0.0]*358)
        self.assertEqual(len(out),180);self.assertEqual(out[0],0.3)
    def test_average_pairs_rounds_to_hundredths(self):
        out=angular.average_pairs([0.77,0.78]*180)
        self.assertTrue(all(x==round(x,2) for x in out),out[:3])
    def test_upsample_constant(self):
        for x in angular.upsample_pairs([0.7]*180): self.assertAlmostEqual(x,0.7,places=9)
    def test_upsample_reconstructs_smooth_curve(self):
        # A lobe about as sharp as a real one; 1+cos is so flat at 0.01 resolution that its peak is a plateau.
        orig=[round(1.3*math.exp(-(((d-137+180)%360-180)/10)**2),2) for d in range(360)]
        up=angular.upsample_pairs(angular.average_pairs(orig))
        self.assertEqual(len(up),360)
        self.assertLess(max(abs(a-b) for a,b in zip(up,orig)),0.02)
        self.assertLessEqual(abs(up.index(max(up))-137),1)
    def test_upsample_no_overshoot(self):
        up=angular.upsample_pairs(spike(90))
        self.assertGreaterEqual(min(up),0.0);self.assertLessEqual(max(up),1.0)
    def test_upsample_wraps(self):
        # Point 179 sits at 358.5 and point 0 at 0.5: the curve is symmetric about each across 359 -> 0.
        up=angular.upsample_pairs(spike(179));self.assertIn(up.index(max(up)),(358,359))
        self.assertAlmostEqual(up[359],up[358]);self.assertAlmostEqual(up[0],up[357])
        up=angular.upsample_pairs(spike(0));self.assertIn(up.index(max(up)),(0,1))
        self.assertAlmostEqual(up[0],up[1]);self.assertAlmostEqual(up[359],up[2])

class EdgeAngularTests(unittest.TestCase):
    def agent(self,points=None):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        a=make_agent(Path(os.path.realpath(tmp.name)));self.addCleanup(a.journal.close)
        if points is not None: a.cfg['telemetry']['angular_points']=points
        return a
    def frame(self,a,build):
        s=a.snapshot();return build(s['detection'],a.source.record,s['host'],s['daq'])
    def check(self,payload,topic):
        frame=json.loads(payload)
        self.assertEqual(len(frame['values']),180);self.assertEqual(frame['step_deg'],2)
        self.assertEqual(list(frame)[-1],'values');self.assertEqual(list(frame)[-2],'step_deg')
        self.assertEqual(errors(frame,schema_for(topic)),[])
    def test_live_angular_180_payload(self):
        a=self.agent();self.check(self.frame(a,a._live_angular),'telemetry/angular')
        a.cfg['telemetry']['angular_points']=360
        frame=json.loads(self.frame(a,a._live_angular))
        self.assertNotIn('step_deg',frame);self.assertEqual(frame['values'],a.source.record['values'])
    def test_diagnostic_angular_180_payload(self):
        a=self.agent();self.check(self.frame(a,a._diagnostic_angular),'telemetry/diagnostic/angular')
    def test_capabilities_count_follows_points(self):
        c=self.agent().capabilities();self.assertEqual((c['count'],c['angular_step_deg']),(180,2))
        self.assertEqual(errors(c,schema_for('capabilities')),[])
        c=self.agent(360).capabilities();self.assertEqual((c['count'],c['angular_step_deg']),(360,1))

class CodecTests(unittest.TestCase):
    def test_codec_decode_upsamples_180(self):
        p=angular_payload('1234abcd',1,1790000000000,1,63,values=[round(i/200,2) for i in range(180)])
        p=dict(list(p.items())[:-1]+[('step_deg',2),('values',p['values'])])
        out=codec.decode(compact(p))
        self.assertEqual(len(out['values']),360);self.assertEqual(out['upsampled_from'],180)
        del p['step_deg']
        with self.assertRaisesRegex(ValueError,'EXPECTED_360_SAMPLES'): codec.decode(compact(p))

class ConfigTests(unittest.TestCase):
    def test_config_bulk_budget_2000_ok_and_aggregate_3000(self):
        c=load_config();c['telemetry'].update(bulk_budget_bytes_s=2000,control_budget_bytes_s=1000);validate_config(c)
        c['telemetry']['bulk_budget_bytes_s']=2001
        with self.assertRaises(ValueError): validate_config(c)
        c['telemetry'].update(bulk_budget_bytes_s=2000,control_budget_bytes_s=1001)
        with self.assertRaisesRegex(ValueError,'AGGREGATE_BUDGET_TOO_HIGH'): validate_config(c)
    def test_config_angular_points(self):
        c=load_config();self.assertEqual(c['telemetry']['angular_points'],180)
        c['telemetry']['angular_points']=90
        with self.assertRaises(ValueError): validate_config(c)

if __name__=='__main__': unittest.main()
