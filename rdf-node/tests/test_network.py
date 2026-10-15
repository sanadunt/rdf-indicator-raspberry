import json,os,socket,ssl,subprocess,tempfile,threading,time,unittest
from pathlib import Path
from rdf_node.mqtt import Client,Message,Outbox
from broker_fixture import Broker,wait,pkt,text,server_frame

class LinkProxy:
    """Loopback TCP relay in front of the broker fixture that stands in for the PPP/T900 link.
    'up' relays both ways; 'down' drops live connections and closes new ones right after accept;
    'blackhole' keeps sockets open but silently discards broker->client bytes (half-open link)."""
    def __init__(self,target_port,mode='up'):
        self.target_port=target_port;self.mode=mode;self.lock=threading.Lock();self.pairs=[];self.accepted=0
        self.listener=socket.socket();self.listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        self.listener.bind(('127.0.0.1',0));self.listener.listen(8);self.listener.settimeout(.1)
        self.port=self.listener.getsockname()[1];self.stop_event=threading.Event()
        self.thread=threading.Thread(target=self._accept,daemon=True);self.thread.start()
    def _accept(self):
        while not self.stop_event.is_set():
            try:client,_=self.listener.accept()
            except socket.timeout:continue
            except OSError:return
            with self.lock:self.accepted+=1;mode=self.mode
            if mode=='down':client.close();continue
            try:upstream=socket.create_connection(('127.0.0.1',self.target_port),timeout=2)
            except OSError:client.close();continue
            with self.lock:self.pairs.append((client,upstream))
            threading.Thread(target=self._pump,args=(client,upstream,False),daemon=True).start()
            threading.Thread(target=self._pump,args=(upstream,client,True),daemon=True).start()
    def _pump(self,src,dst,to_client):
        src.settimeout(.1)
        while not self.stop_event.is_set():
            try:data=src.recv(8192)
            except socket.timeout:continue
            except OSError:break
            if not data:break
            if to_client and self.mode=='blackhole':continue
            try:dst.sendall(data)
            except OSError:break
        for sock in (src,dst):
            try:sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            sock.close()
    def set_mode(self,mode):
        with self.lock:
            self.mode=mode;dropped=self.pairs if mode=='down' else []
            if mode=='down':self.pairs=[]
        for pair in dropped:
            for sock in pair:
                try:sock.shutdown(socket.SHUT_RDWR)
                except OSError:pass
    def stop(self):
        self.set_mode('down');self.stop_event.set();self.listener.close();self.thread.join(1)

class BackoffScheduleTests(unittest.TestCase):
    """Drive the real Client._run with a fake clock and scripted sessions; record each reconnect wait."""
    def run_schedule(self,script,kicks=(),reconnect_max=30):
        c=Client(cfg={'reconnect_max':reconnect_max},client_id='backoff');clock=[1000.0];c._clock=lambda:clock[0]
        steps=list(script);waits=[]
        def session():
            kind,seconds=steps.pop(0)
            if kind=='fail':clock[0]+=.05;raise OSError('refused')
            # 'up': CONNACK arrived, then the link dropped `seconds` later.
            c._connected_at=clock[0];c.connected=True;clock[0]+=seconds;raise OSError('dropped')
        def backoff_wait(seconds):
            kicked=len(waits) in kicks;waits.append(seconds);clock[0]+=seconds
            if not steps:c.stop_event.set()
            return kicked
        c._session=session;c._backoff_wait=backoff_wait;c._run()
        self.assertTrue(all(0<=w-int(w)<=.3 for w in waits),waits)
        self.assertEqual(c.reconnects,len(script))
        return [int(w) for w in waits]
    def test_never_connecting_grows_to_reconnect_max(self):
        self.assertEqual(self.run_schedule([('fail',0)]*8),[1,2,4,8,16,30,30,30])
        self.assertEqual(self.run_schedule([('fail',0)]*5,reconnect_max=5),[1,2,4,5,5])
    def test_short_post_connack_sessions_back_off_instead_of_storming(self):
        # SUBACK/PUBACK rejection, oversize packet or client-id takeover right after CONNACK (MQTT-2).
        self.assertEqual(self.run_schedule([('up',.1)]*7),[1,2,4,8,16,30,30])
    def test_stable_session_resets_backoff(self):
        # Outage grows the delay to 30 s; each drop after a 10-minute session retries after ~1 s (MQTT-1).
        script=[('fail',0)]*6+[('up',600)]*3+[('fail',0)]*2
        self.assertEqual(self.run_schedule(script),[1,2,4,8,16,30,1,1,1,2,4])
    def test_stable_threshold_is_twenty_seconds_after_connack(self):
        script=[('fail',0)]*3+[('up',19.5),('up',20.5),('up',19.5)]
        self.assertEqual(self.run_schedule(script),[1,2,4,8,1,2])
    def test_kicked_wait_resets_backoff(self):
        self.assertEqual(self.run_schedule([('fail',0)]*8,kicks={4}),[1,2,4,8,16,1,2,4])

class KickTests(unittest.TestCase):
    def test_kick_cuts_backoff_wait_short(self):
        c=Client(cfg={},client_id='kick-wait');result=[]
        t=threading.Thread(target=lambda:result.append((c._backoff_wait(30),time.monotonic())));started=time.monotonic();t.start()
        time.sleep(.2);self.assertTrue(c.kick());t.join(3)
        self.assertFalse(t.is_alive());self.assertTrue(result[0][0]);self.assertLess(result[0][1]-started,2)
        self.assertFalse(c._wake.is_set())
    def test_kick_is_noop_while_connected_and_stop_is_not_a_kick(self):
        c=Client(cfg={},client_id='kick-connected');c.connected=True
        self.assertFalse(c.kick());self.assertFalse(c._wake.is_set())
        c.connected=False;result=[]
        t=threading.Thread(target=lambda:result.append(c._backoff_wait(30)));started=time.monotonic();t.start()
        time.sleep(.1);c.stop();t.join(3)
        self.assertFalse(t.is_alive());self.assertEqual(result,[False]);self.assertLess(time.monotonic()-started,2)
        self.assertFalse(c.kick())
    def test_bare_stop_event_still_ends_backoff_wait(self):
        c=Client(cfg={},client_id='kick-stop-event');result=[]
        t=threading.Thread(target=lambda:result.append(c._backoff_wait(30)));t.start()
        time.sleep(.1);c.stop_event.set();t.join(2)
        self.assertFalse(t.is_alive());self.assertEqual(result,[False])

class QueueAgeTests(unittest.TestCase):
    def test_outbox_oldest_age(self):
        o=Outbox()
        self.assertEqual(o.oldest_age_ms(now=50.0),0)
        o.offer(Message('a','t/a',b'1',0,False,None,2,10.0));o.offer(Message('b','t/b',b'2',0,False,None,1,12.0))
        self.assertEqual(o.oldest_age_ms(now=13.25),3250)
        o.offer(Message('a','t/a',b'3',0,False,None,2,13.0))  # supersede: the newer value replaces the old age
        self.assertEqual(o.oldest_age_ms(now=13.25),1250)
        self.assertEqual(o.oldest_age_ms(now=11.0),0)  # never negative
    def test_queue_age_excludes_in_flight_message(self):
        c=Client(cfg={},client_id='queue-age');c.connected=True
        self.assertEqual(c.queue_age_ms,0);self.assertEqual(c.status()['queue_age_ms'],0)
        self.assertTrue(c.offer('old','rdf/old',b'1',priority=0));self.assertTrue(c.offer('new','rdf/new',b'2',priority=1))
        c.outbox.data['old'].offered-=2.5;c.outbox.data['new'].offered-=.5
        self.assertTrue(2500<=c.queue_age_ms<2900,c.queue_age_ms)
        self.assertTrue(2500<=c.status()['queue_age_ms']<2900)
        in_flight=c.outbox.pop();self.assertEqual(in_flight.key,'old');c._publish=(in_flight,0,time.monotonic())
        self.assertTrue(500<=c.queue_age_ms<900,c.queue_age_ms)
        c.outbox.pop();self.assertEqual(c.queue_age_ms,0);self.assertEqual(c.status()['queue_age_ms'],0)

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
    def proxy(self,broker,mode='up'):
        p=LinkProxy(broker.port,mode);self.addCleanup(p.stop);return p
    def test_kick_skips_remaining_backoff_after_link_returns(self):
        b=self.broker();link=self.proxy(b,'down')
        cfg={'host':'127.0.0.1','port':link.port,'tls':False,'keepalive':5,'reconnect_max':30}
        c=Client(cfg=cfg,client_id='kick-'+str(time.monotonic_ns()),credentials_file=str(self.creds),rate=10000)
        c._backoff_initial=30  # make the first wait long enough that only kick() can explain a prompt reconnect
        self.addCleanup(c.stop);c.start()
        self.assertTrue(wait(lambda:c.reconnects>=1 and link.accepted>=1,5),c.status())
        link.set_mode('up');time.sleep(1)
        self.assertFalse(c.connected);self.assertEqual(link.accepted,1)  # still waiting out the 30 s backoff
        self.assertTrue(c.kick())
        self.assertTrue(wait(lambda:c.ready,3),c.status())
        g=c.generation;self.assertFalse(c.kick());time.sleep(.3)
        self.assertEqual(c.generation,g);self.assertTrue(c.ready);self.assertEqual(link.accepted,2)
    def test_pingreq_on_receive_silence_while_qos0_flows(self):
        # The transmit side never idles for keepalive seconds, so only RX silence can trigger PINGREQ (MQTT-4).
        b=self.broker();c=self.client(b,keepalive=1);self.assertTrue(wait(lambda:c.ready));g=c.generation
        started=time.monotonic();n=0
        while time.monotonic()-started<2.6:
            self.assertTrue(c.offer('health','rdf/health',str(n).encode()));n+=1;time.sleep(.1)
        self.assertGreaterEqual(b.mqtt_pingreq_count,2)
        self.assertEqual(c.generation,g);self.assertTrue(c.ready);self.assertIsNone(c.error)
        self.assertGreaterEqual(len([m for m in b.messages if m['topic']=='rdf/health']),20);self.assertFalse(b.errors)
    def test_half_open_link_detected_while_qos0_flows(self):
        b=self.broker();link=self.proxy(b);c=self.client(b,port=link.port,keepalive=1)
        self.assertTrue(wait(lambda:c.ready));g=c.generation;link.set_mode('blackhole')
        cut=time.monotonic();seen=[]
        while time.monotonic()-cut<5 and not seen:
            c.offer('health','rdf/health',b'x')  # QoS0 writes keep succeeding into the socket buffer
            if c.error=='PING_TIMEOUT':seen.append(time.monotonic()-cut)
            time.sleep(.05)
        self.assertTrue(seen,c.status());self.assertLess(seen[0],3.5)
        # The PINGREQ reached the broker; only its PINGRESP was lost on the way back.
        self.assertGreaterEqual(b.mqtt_pingreq_count,1);self.assertFalse(c.connected);self.assertEqual(c.generation,g)
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
