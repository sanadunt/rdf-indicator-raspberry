import json,os,ssl,subprocess,tempfile,time,unittest
from pathlib import Path
from rdf_node.mqtt import Client
from broker_fixture import Broker,wait

class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
        self.creds=self.path/'creds.json';self.creds.write_text(json.dumps({'username':'test','password':'test-password'}))
    def client(self,broker,subscriptions=(),callback=None,**extra):
        cfg={'host':'127.0.0.1','port':broker.port,'tls':False,'allow_insecure_loopback':True,'keepalive':5,'reconnect_max':5}
        cfg.update(extra)
        c=Client(cfg=cfg,client_id='test-'+str(time.monotonic_ns()),credentials_file=str(self.creds),subscriptions=subscriptions,on_message=callback,rate=10000)
        self.addCleanup(c.stop);c.start();return c
    def broker(self,**kw):
        b=Broker(**kw);self.addCleanup(b.stop);return b
    def test_qos0_round_trip(self):
        b=self.broker();messages=[];r=self.client(b,[('rdf/data',0,True)],lambda *args:messages.append(args));p=self.client(b)
        self.assertTrue(wait(lambda:r.ready and p.ready));self.assertTrue(p.offer('d','rdf/data',b'payload',expiry=3))
        self.assertTrue(wait(lambda:messages));self.assertEqual(messages[0][1],b'payload');self.assertFalse(b.errors)
    def test_qos1_callback_after_puback(self):
        b=self.broker();sent=[];c=self.client(b);self.assertTrue(wait(lambda:c.ready))
        c.offer('q','rdf/state',b'{}',qos=1,retain=True,on_sent=lambda:sent.append(True))
        self.assertTrue(wait(lambda:sent));self.assertIn('rdf/state',b.retained)
    def test_suback_failure_not_ready(self):
        b=self.broker(deny_sub='forbidden');c=self.client(b,[('forbidden',1,True)])
        self.assertTrue(wait(lambda:c.error=='SUBACK_REJECTED'));self.assertFalse(c.ready)
    def test_authentication_failure(self):
        b=self.broker(auth={'other':'secret'});c=self.client(b)
        self.assertTrue(wait(lambda:c.error=='CONNACK_REJECTED'));self.assertFalse(c.connected)
    def test_reconnect_creates_fresh_session(self):
        b=self.broker();c=self.client(b);self.assertTrue(wait(lambda:c.ready));g=c.generation;b.disconnect_all()
        self.assertTrue(wait(lambda:c.generation>g and c.ready,8))
    def test_retained_command_flag_preserved(self):
        b=self.broker();messages=[];r=self.client(b,[('rdf/cmd',1,True)],lambda *args:messages.append(args));p=self.client(b)
        self.assertTrue(wait(lambda:r.ready and p.ready));p.offer('c','rdf/cmd',b'{}',qos=1,retain=True)
        self.assertTrue(wait(lambda:messages));self.assertTrue(messages[0][2])
    def test_retained_history_not_subscribed(self):
        b=self.broker();b.retained['rdf/cmd']=b'old';messages=[];r=self.client(b,[('rdf/cmd',1,True)],lambda *args:messages.append(args));self.assertTrue(wait(lambda:r.ready));time.sleep(.2);self.assertFalse(messages)
    def test_tls_certificate_validated(self):
        if not __import__('shutil').which('openssl'):self.skipTest('openssl unavailable')
        cert=self.path/'cert.pem';key=self.path/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key)
        b=self.broker(tls_context=ctx);c=self.client(b,tls=True,ca_file=str(cert));self.assertTrue(wait(lambda:c.ready,6))
    def test_tls_unknown_ca_rejected(self):
        if not __import__('shutil').which('openssl'):self.skipTest('openssl unavailable')
        cert=self.path/'cert.pem';key=self.path/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key)
        b=self.broker(tls_context=ctx);c=self.client(b,tls=True,ca_file=None);self.assertTrue(wait(lambda:c.error=='TLS_CERTIFICATE_VERIFY_FAILED',6))
