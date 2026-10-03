import json,os,ssl,subprocess,tempfile,time,unittest
from pathlib import Path
from rdf_node.mqtt import Client
from broker_fixture import Broker,wait,pkt,text,server_frame

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
    def test_puback_rejection_reports_publish_error(self):
        b=self.broker(puback_reason=0x87);c=self.client(b)
        self.assertTrue(wait(lambda:c.ready))
        sent=[];errors=[]
        self.assertTrue(c.offer('q','rdf/rejected',b'{}',qos=1,on_sent=lambda:sent.append(True),
                                on_error=errors.append))
        self.assertTrue(wait(lambda:errors))
        self.assertEqual(errors,['PUBLISH_REJECTED_0X87'])
        self.assertFalse(sent)
    def test_payload_encoding_failure_reports_publish_error(self):
        c=Client(cfg={},client_id='serialize')
        c.connected=True;errors=[]
        with self.assertRaises(TypeError):
            c.offer('bad','rdf/data',object(),on_error=errors.append)
        self.assertEqual(errors,['PUBLISH_PAYLOAD_ENCODE_FAILED'])

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

    def test_wss_mqtt_round_trip(self):
        if not __import__('shutil').which('openssl'):self.skipTest('openssl unavailable')
        cert=self.path/'cert.pem';key=self.path/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key)
        b=self.broker(tls_context=ctx,websocket=True)
        c=self.client(b,tls=True,ca_file=str(cert),transport='websocket',websocket_path='/mqtt')
        self.assertTrue(wait(lambda:c.ready,6),c.status())
        self.assertTrue(c.offer('wss','rdf/wss',b'payload'))
        self.assertTrue(wait(lambda:any(m['topic']=='rdf/wss' for m in b.messages)))
        self.assertFalse(b.errors)

    def test_wss_echoes_upgrade_close_without_mqtt_connect(self):
        if not __import__('shutil').which('openssl'):self.skipTest('openssl unavailable')
        cert=self.path/'cert.pem';key=self.path/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key)
        b=self.broker(tls_context=ctx,websocket=True,websocket_early_close=True)
        c=self.client(b,tls=True,ca_file=str(cert),transport='websocket',websocket_path='/mqtt')
        self.assertTrue(wait(lambda:any(any(event[0]==8 for event in connection['ws_events']) for connection in b.connections),6),b.errors)
        self.assertEqual(b.mqtt_connect_count,0)
        self.assertFalse(b.clients)

    def test_wss_mqtt_keepalive_survives_websocket_pings(self):
        if not __import__('shutil').which('openssl'):self.skipTest('openssl unavailable')
        cert=self.path/'cert.pem';key=self.path/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key)
        b=self.broker(tls_context=ctx,websocket=True)
        c=self.client(b,tls=True,ca_file=str(cert),transport='websocket',websocket_path='/mqtt')
        self.assertTrue(wait(lambda:c.ready,6),c.status())
        connection=b.clients[0];started=time.monotonic()
        while time.monotonic()-started<6.2:
            b.send_control(connection,9,b'keepalive')
            time.sleep(.1)
        self.assertGreaterEqual(b.mqtt_pingreq_count,1)

    def test_wss_close_does_not_send_mqtt_after_close(self):
        if not __import__('shutil').which('openssl'):self.skipTest('openssl unavailable')
        cert=self.path/'cert.pem';key=self.path/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key)
        b=self.broker(tls_context=ctx,websocket=True);received=[]
        c=self.client(b,[('rdf/inbound',1,True)],lambda *args:received.append(args),
                      tls=True,ca_file=str(cert),transport='websocket',websocket_path='/mqtt')
        self.assertTrue(wait(lambda:c.ready,6),c.status())
        connection=b.clients[0];message=pkt(0x32,text('rdf/inbound')+bytes([0,1,0])+b'payload')
        b.send_frames(connection,[server_frame(message),server_frame(b'',8)])
        self.assertTrue(wait(lambda:received,3))
        self.assertTrue(wait(lambda:any(event[0]=='server-close' for event in connection['ws_events']),3))
        self.assertFalse(any(event[0]=='after-close' for event in connection['ws_events']))

    def test_wss_mqtt_disconnect_completes_websocket_close(self):
        if not __import__('shutil').which('openssl'):self.skipTest('openssl unavailable')
        cert=self.path/'cert.pem';key=self.path/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key)
        b=self.broker(tls_context=ctx,websocket=True)
        c=self.client(b,tls=True,ca_file=str(cert),transport='websocket',websocket_path='/mqtt')
        self.assertTrue(wait(lambda:c.ready,6),c.status())
        connection=b.clients[0];b.send_packet(connection,pkt(0xe0))
        self.assertTrue(wait(lambda:any(event[0]=='server-close' for event in connection['ws_events']),3))

    def test_wss_uses_default_ca_store_without_custom_file(self):
        if not __import__('shutil').which('openssl'):self.skipTest('openssl unavailable')
        cert=self.path/'cert.pem';key=self.path/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key)
        b=self.broker(tls_context=ctx,websocket=True)
        previous=os.environ.get('SSL_CERT_FILE');os.environ['SSL_CERT_FILE']=str(cert)
        c=None
        try:
            c=self.client(b,tls=True,ca_file=None,transport='websocket',websocket_path='/mqtt')
            self.assertTrue(wait(lambda:c.ready,6),c.status())
            self.assertTrue(c.offer('system-trust','rdf/system-trust',b'payload'))
            self.assertTrue(wait(lambda:any(m['topic']=='rdf/system-trust' for m in b.messages)))
        finally:
            if c:c.stop()
            if previous is None:os.environ.pop('SSL_CERT_FILE',None)
            else:os.environ['SSL_CERT_FILE']=previous

    def test_wss_system_trust_rejects_unknown_ca_before_mqtt(self):
        if not __import__('shutil').which('openssl'):self.skipTest('openssl unavailable')
        cert=self.path/'cert.pem';key=self.path/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key)
        b=self.broker(tls_context=ctx,websocket=True)
        c=self.client(b,tls=True,ca_file=None,transport='websocket',websocket_path='/mqtt')
        self.assertTrue(wait(lambda:c.error=='TLS_CERTIFICATE_VERIFY_FAILED',6),c.status())
        self.assertFalse(b.clients)
