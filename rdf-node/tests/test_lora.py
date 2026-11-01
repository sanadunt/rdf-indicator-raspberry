import json,os,select,tempfile,threading,time,unittest
from pathlib import Path
from rdf_node import lora
from rdf_node.lora import (DATA,EDGE_TOPICS,GROUND_TOPICS,TOPIC_IDS,Bridge,Receiver,Sender,cobs_decode,cobs_encode,
                           crc16,decode_frame,encode_frames)
from rdf_node.mqtt import Client
from broker_fixture import Broker,wait

ANGULAR=json.dumps({'v':2,'encoding':'json','sid':'a3da8b3b','q':1,'timestamp_ms':1793446155142,'frequency_hz':433920000,
                    'revision':1,'vfo':0,'convention':1,'flags':63,'raw_doa_deg':137.0,'confidence_native_db':8.27,
                    'values':[round((d*7919%1000)/50,2) for d in range(360)]},separators=(',',':')).encode()
HEALTH=b'{"v":2,"sid":"7a8b9c0d","q":86,"t":1793445697776,"run":1,"daq":1,"drop":12,"age":280,"temp":61.4,"clk":1,"rev":7,"why":[]}'

class CodecTests(unittest.TestCase):
    def test_cobs_round_trip_and_no_zero_bytes(self):
        for data in (b'',b'\x00',b'\x00'*300,bytes(range(256))*3,os.urandom(700)):
            enc=cobs_encode(data)
            self.assertNotIn(b'\x00',enc);self.assertEqual(cobs_decode(enc),data)
        with self.assertRaises(ValueError): cobs_decode(b'\x05ab')
    def test_crc16_ccitt_false_check_value(self):
        self.assertEqual(crc16(b'123456789'),0x29B1)
    def test_frames_respect_frame_max_and_round_trip_exact_bytes(self):
        for frame_max in (64,200,255):
            frames=encode_frames(DATA,10,5,4,ANGULAR,frame_max=frame_max)
            self.assertLessEqual(max(map(len,frames)),frame_max)
            self.assertTrue(all(f.endswith(b'\x00') and b'\x00' not in f[:-1] for f in frames))
            r=Receiver();out=r.feed(b''.join(frames))
            self.assertEqual(out,[(4,ANGULAR,False,False)])
    def test_compression_is_used_and_small_payloads_fit_one_frame(self):
        frames=encode_frames(DATA,0,1,1,HEALTH)
        self.assertEqual(len(frames),1);self.assertLess(len(frames[0]),len(HEALTH))
        self.assertLess(sum(map(len,encode_frames(DATA,0,1,4,ANGULAR))),len(ANGULAR))
    def test_corrupted_frame_is_rejected_by_crc_and_the_stream_recovers(self):
        good=encode_frames(DATA,0,1,1,HEALTH)[0];bad=bytearray(good);bad[5]^=0x40
        r=Receiver();out=r.feed(bytes(bad)+b'\x13\x37garbage'+b'\x00'+encode_frames(DATA,1,2,1,HEALTH)[0])
        self.assertEqual(len(out),1);self.assertEqual(r.stats['crc_errors']+r.stats['bad_frames'],2)
    def test_fragments_out_of_order_duplicates_and_loss(self):
        frames=encode_frames(DATA,0,9,4,ANGULAR)
        self.assertGreater(len(frames),2)
        r=Receiver()
        self.assertEqual(r.feed(b''.join(reversed(frames))),[(4,ANGULAR,False,False)])
        # The second copy of a message (same message id) is dropped.
        self.assertEqual(r.feed(b''.join(frames)),[]);self.assertGreaterEqual(r.stats['duplicates'],1)
        # A lost fragment: nothing is delivered and the partial message expires.
        r=Receiver();now=time.monotonic()
        self.assertEqual(r.feed(b''.join(frames[:-1]),now),[])
        r.feed(b'',now+lora.REASSEMBLY_S+1);self.assertEqual(r.stats['incomplete'],1)
    def test_sequence_gaps_count_lost_frames(self):
        r=Receiver()
        r.feed(encode_frames(DATA,0,1,1,HEALTH)[0]+encode_frames(DATA,5,2,1,HEALTH)[0])
        self.assertEqual(r.stats['lost_frames'],4)
    def test_retain_and_qos_flags_survive(self):
        r=Receiver();self.assertEqual(r.feed(encode_frames(DATA,0,1,7,b'{"v":2}',retain=True,qos1=True)[0]),[(7,b'{"v":2}',True,True)])
    def test_topic_table_is_stable(self):
        # Wire ids are part of RLS1: changing one breaks an older bridge on the other side.
        self.assertEqual((TOPIC_IDS['telemetry/health'],TOPIC_IDS['telemetry/angular'],TOPIC_IDS['state'],
                          TOPIC_IDS['cmd/config/patch'],TOPIC_IDS['cmd/stream/set']),(1,4,7,65,74))
        from rdf_node.control import OPS
        self.assertEqual(set(GROUND_TOPICS.values()),set(OPS.values()))

class SenderTests(unittest.TestCase):
    def test_priority_latest_wins_and_expiry(self):
        s=Sender(100000,200);now=1000.0
        s.offer(4,ANGULAR,now=now);s.offer(1,HEALTH,now=now);s.offer(1,HEALTH.replace(b'86',b'87'),now=now)
        s.offer(11,b'{"ack":1}',now=now);s.offer(11,b'{"ack":2}',now=now)
        order=[];r=Receiver();t=now
        for _ in range(200):
            t+=1
            for topic,*_ in r.feed(b''.join(s.next_frames(t))): order.append(topic)
            if not s.depth(): break
        self.assertEqual(order,[11,11,1,4])  # ACKs (FIFO, both kept), newest health only, then angular
        self.assertEqual(s.stats['superseded'],1)
        s.offer(3,b'{"doa":1}',now=now)
        s.next_frames(t+4);self.assertGreaterEqual(s.stats['expired'],1)
    def test_commands_and_acks_go_twice_and_arrive_once(self):
        s=Sender(100000,200);t=1000.0;s.offer(65,b'{"op":"config.patch"}',now=t)
        first=s.next_frames(t);r=Receiver()
        self.assertEqual(len(first),1)
        # The copy waits REPEAT_GAP_S so a single collision cannot take both.
        self.assertEqual(s.next_frames(t+lora.REPEAT_GAP_S/2),[])
        again=s.next_frames(t+lora.REPEAT_GAP_S+.01);self.assertEqual(len(again),1)
        self.assertEqual(len(r.feed(b''.join(first+again))),1);self.assertEqual(r.stats['duplicates'],1)
        self.assertEqual(s.depth(),0)
    def test_byte_budget_paces_and_min_interval_spaces_angular(self):
        s=Sender(500,200,{4:20});now=0.0
        sent=0
        for step in range(100):
            t=now+step*0.1
            s.offer(1,HEALTH,now=t)
            if step%10==0: s.offer(4,ANGULAR,now=t)
            sent+=sum(map(len,s.next_frames(t)))
        # 10 s at 500 B/s plus one bucket (400 B): never above the budget, and frame by frame.
        self.assertLessEqual(sent,500*10+400)
        self.assertGreater(sent,500*10*.8)
    def test_doa_goes_twice_and_ranks_above_health(self):
        s=Sender(100000,200);t=1000.0
        s.offer(1,HEALTH,now=t);s.offer(3,b'{"v":2,"a":12.5}',now=t)
        r=Receiver();got=[];x=t
        for _ in range(40):
            x+=.1
            for topic,*_ in r.feed(b''.join(s.next_frames(x))): got.append(topic)
        self.assertEqual(got,[3,1]);self.assertEqual(r.stats['duplicates'],1)
    def test_turn_frame_then_quiet_window(self):
        s=Sender(100000,200,turn_every_s=1.0,quiet_s=.3);t=1000.0
        for i in range(4): s.offer(11,b'{"ack":%d}'%i,now=t)
        first=s.next_frames(t)
        self.assertEqual(len(first),1);self.assertTrue(decode_frame(first[0][:-1])[4]&lora.F_TURN)
        self.assertEqual(s.next_frames(t+.2),[])  # quiet: the other side's turn
        later=s.next_frames(t+.4)
        self.assertTrue(later);self.assertFalse(any(decode_frame(f[:-1])[4]&lora.F_TURN for f in later))
        r=Receiver();r.feed(first[0]);self.assertIsNotNone(r.turn_at)
    def test_time_frames_are_stamped_when_sent_not_when_queued(self):
        s=Sender(100000,200)
        s.urgent(lora.TIME,lora.TIME_REQ.pack(1,1));s.urgent(lora.TIME,lora.TIME_RESP.pack(2,5,6,7))
        frames=s.next_frames(time.monotonic()+1)
        ops=[decode_frame(f[:-1])[-1] for f in frames]
        self.assertGreater(lora.TIME_RESP.unpack(ops[0])[3],1_000_000_000_000_000)
        self.assertEqual(lora.TIME_RESP.unpack(ops[0])[1:3],(5,6))
        self.assertGreater(lora.TIME_REQ.unpack(ops[1])[1],1_000_000_000_000_000)
        # Heartbeats too: queued now, sent later, the clock in them is the send time.
        from unittest import mock
        s.heartbeat(1,Receiver().stats)
        with mock.patch.object(lora,'now_ms',return_value=1_800_000_000_000):
            beat=s.next_frames(time.monotonic()+2)
        r=Receiver()
        with mock.patch.object(lora,'now_ms',return_value=1_800_000_000_000): r.feed(beat[0])
        self.assertEqual(r.peer['clock_offset_ms'],0)
    def test_a_large_message_never_bursts_past_the_module_buffer(self):
        s=Sender(1000,200);s.offer(4,ANGULAR,now=0.0)
        first=sum(map(len,s.next_frames(0.0)))
        self.assertLessEqual(first,400)
        total=first;t=0.0
        while s.depth(): t+=.05;total+=sum(map(len,s.next_frames(t)))
        self.assertGreater(t,.3)  # ~860 B at 1000 B/s after a 400 B bucket
    def test_heartbeat_jumps_the_queue_and_keeps_sequence_order(self):
        s=Sender(100000,200);s.offer(4,ANGULAR,now=0.0)
        first=s.next_frames(0.0,limit=1)
        s.heartbeat(1,Receiver().stats,0.0)
        rest=[];t=0.0
        while s.depth(): t+=1;rest+=s.next_frames(t)
        r=Receiver();out=r.feed(b''.join(first+rest))
        self.assertEqual(out[0][1],ANGULAR);self.assertEqual((r.stats['heartbeats'],r.stats['lost_frames']),(1,0))
        self.assertEqual(decode_frame(rest[0][:-1])[0],lora.HEARTBEAT)

class TimeSyncTests(unittest.TestCase):
    def bridge(self,role,**kw):
        cfg={'host':'127.0.0.1','port':1,'tls':False,'ca_file':None,'transport':'tcp','websocket_path':'/mqtt',
             'keepalive':15,'reconnect_max':5}
        return Bridge(role=role,device='/dev/null',baud=115200,mqtt_cfg=cfg,node_id='uav-01',**kw)
    def test_offset_and_delay_from_four_timestamps_and_best_sample_wins(self):
        import socket,struct
        from rdf_node.timesync import SAMPLE,SOCK_MAGIC
        path=os.path.join(tempfile.mkdtemp(),'chrony.sock')
        server=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);server.bind(path);self.addCleanup(server.close)
        edge=self.bridge('edge',chrony_sock=path)
        # Ground clock 2.000 s ahead; 60 ms each way, 1 ms in the ground bridge.
        t1=1_000_000_000;t2=t1+60_000+2_000_000;t3=t2+1_000;t4=t1+60_000+1_000+60_000
        edge._on_time(lora.TIME_RESP.pack(2,t1,t2,t3),t4)
        view=edge._time_view()
        self.assertEqual((view['offset_ms'],view['delay_ms'],view['samples']),(2000.0,120.0,1))
        sec,usec,offset,pulse,leap,pad,magic=SAMPLE.unpack(server.recv(64))
        self.assertEqual((round(offset,6),magic),(2.0,SOCK_MAGIC))
        # A slower round trip (asymmetric, 400 ms) does not replace the better sample.
        edge._on_time(lora.TIME_RESP.pack(2,t1,t1+300_000+2_000_000,t1+301_000+2_000_000),t1+400_000+1_000)
        self.assertEqual(edge._time_view()['offset_ms'],2000.0)
        # Nonsense (negative delay) is ignored.
        edge._on_time(lora.TIME_RESP.pack(2,t1,t1,t1+10_000_000),t1+1);self.assertEqual(edge._time_view()['samples'],2)
    def test_ground_answers_a_time_request_first(self):
        ground=self.bridge('ground')
        ground.sender.offer(1,HEALTH)
        ground._on_time(lora.TIME_REQ.pack(1,123),456)
        frames=ground.sender.next_frames(time.monotonic()+1)
        kind,*_,chunk=decode_frame(frames[0][:-1])
        op,t1,t2,t3=lora.TIME_RESP.unpack(chunk)
        self.assertEqual((kind,op,t1,t2),(lora.TIME,2,123,456));self.assertGreater(t3,t2)
    def test_missing_chrony_socket_is_counted_not_raised(self):
        from rdf_node.timesync import ChronySock
        c=ChronySock('/nonexistent/chrony.sock');self.assertFalse(c.send(0.1));self.assertEqual(c.errors,1);c.close()
    def test_gps_rmc_time(self):
        from rdf_node.nav import rmc_utc
        f='102846.00,A,0612.34567,S,10648.60000,E,0.282,,311026,,,D'.split(',')
        self.assertEqual(rmc_utc(f),1793442526.0)
        self.assertIsNone(rmc_utc(['102846.00','V']+f[2:]))

class StatusContractTests(unittest.TestCase):
    def test_status_fields_the_ground_console_reads(self):
        # Ground Console reads lora/ground and lora/edge (v 1). Add fields freely; renaming or changing
        # one of these needs a new `v`.
        cfg={'host':'127.0.0.1','port':1,'tls':False,'ca_file':None,'transport':'tcp','websocket_path':'/mqtt',
             'keepalive':15,'reconnect_max':5}
        b=Bridge(role='ground',device='/dev/null',baud=115200,mqtt_cfg=cfg,node_id='uav-01')
        st=b.status()
        self.assertEqual(st['v'],1)
        self.assertLessEqual({'v','role','t','link','last_rx_ms','port','port_error','mqtt','rx','tx','queue','peer',
                              'mqtt_dropped','ignored','link_changes'},set(st))
        self.assertLessEqual({'frames','crc_errors','bad_frames','lost_frames','messages','duplicates','incomplete',
                              'heartbeats'},set(st['rx']))
        self.assertIn(st['link'],('UP','DOWN'));self.assertIsInstance(st['t'],int)
        b.receiver.peer={'uptime_s':1,'rx_frames':2,'crc_errors':0,'lost_frames':0,'clock_offset_ms':-3,'at':0}
        self.assertEqual(set(b.status()['peer']),{'uptime_s','rx_frames','crc_errors','lost_frames','clock_offset_ms'})

@unittest.skipUnless(hasattr(os,'openpty') and lora.termios is not None,'needs a POSIX pty')
class BridgeTests(unittest.TestCase):
    """Edge broker <- edge bridge <- pty radio (rate-limited, can be cut) -> ground bridge -> Ground broker."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cred=Path(self.tmp.name)/'cred.json';self.cred.write_text(json.dumps({'username':'test','password':'test-password'}))
        self.edge_broker=Broker();self.ground_broker=Broker(websocket=True)
        self.addCleanup(self.edge_broker.stop);self.addCleanup(self.ground_broker.stop)
        m1,s1=os.openpty();m2,s2=os.openpty()
        self.fds=[m1,s1,m2,s2];self.radio_on=threading.Event();self.radio_on.set();self.stop=threading.Event()
        self.addCleanup(self._close)
        self.relay=[threading.Thread(target=self._relay,args=(a,b),daemon=True) for a,b in ((m1,m2),(m2,m1))]
        for t in self.relay: t.start()
        self.edge=self._bridge('edge',os.ttyname(s1),self.edge_broker,'tcp')
        self.ground=self._bridge('ground',os.ttyname(s2),self.ground_broker,'websocket')
        self.edge_pub=self._client(self.edge_broker,'edge-sim')
        self.ground_pub=self._client(self.ground_broker,'console-sim','websocket')
    def _close(self):
        self.stop.set()
        for t in self.relay: t.join(1)
        for fd in self.fds:
            try: os.close(fd)
            except OSError: pass
    def _relay(self,src,dst,rate=1400):
        # A half-duplex-ish radio: paced to ~1.4 KB/s, bytes dropped while the "link" is off.
        while not self.stop.is_set():
            try:
                r,_,_=select.select([src],[],[],.05)
                if not r: continue
                data=os.read(src,256)
            except (OSError,ValueError): return
            if not self.radio_on.is_set(): continue
            time.sleep(len(data)/rate)
            try: os.write(dst,data)
            except OSError: return
    def _bridge(self,role,device,broker,transport):
        cfg={'host':'127.0.0.1','port':broker.port,'tls':False,'ca_file':None,'transport':transport,
             'websocket_path':'/mqtt','keepalive':15,'reconnect_max':5}
        b=Bridge(role=role,device=device,baud=115200,mqtt_cfg=cfg,node_id='uav-01',credentials_file=str(self.cred),
                 heartbeat_s=.5,link_timeout_s=2,angular_every_s=0,status_every_s=.5,time_every_s=.5)
        b.start();self.addCleanup(b.stop)
        self.assertTrue(wait(lambda:b.client.ready,5),role)
        return b
    def _client(self,broker,name,transport='tcp'):
        c=Client(cfg={'host':'127.0.0.1','port':broker.port,'tls':False,'ca_file':None,'transport':transport,
                      'websocket_path':'/mqtt','keepalive':15,'reconnect_max':5},client_id=name,credentials_file=str(self.cred))
        c.start();self.addCleanup(c.stop);self.assertTrue(wait(lambda:c.ready,5));return c
    def published(self,broker,suffix):
        return [m for m in list(broker.messages) if m['topic']=='sdr/v2/uav-01/'+suffix]
    def test_edge_topics_arrive_byte_identical_with_retain(self):
        state=b'{"v":2,"sid":"7a8b9c0d","contract":"1.6","boot":"b","instance":"i","t":1,"run":"RUNNING","daq":true,"cfg":1,"profile":"balanced","clock":"SYNCED"}'
        self.edge_pub.offer('s','sdr/v2/uav-01/state',state,qos=1,retain=True)
        self.edge_pub.offer('h','sdr/v2/uav-01/telemetry/health',HEALTH)
        self.edge_pub.offer('a','sdr/v2/uav-01/telemetry/angular',ANGULAR)
        self.assertTrue(wait(lambda:self.published(self.ground_broker,'telemetry/angular'),10),self.ground.status())
        self.assertTrue(wait(lambda:self.published(self.ground_broker,'state'),5))
        self.assertEqual(self.published(self.ground_broker,'telemetry/angular')[-1]['payload'],ANGULAR)
        self.assertEqual(self.published(self.ground_broker,'telemetry/health')[-1]['payload'],HEALTH)
        st=self.published(self.ground_broker,'state')[-1]
        self.assertEqual((st['payload'],st['retain']),(state,True))
        self.assertEqual(self.ground_broker.retained.get('sdr/v2/uav-01/state'),state)
        self.assertFalse(self.published(self.ground_broker,'telemetry/health')[-1]['retain'])
    def test_command_goes_down_once_and_ack_comes_back(self):
        cmd=b'{"v":2,"id":"c1","op":"config.get","expires_ms":9999999999999}'
        self.ground_pub.offer('c','sdr/v2/uav-01/cmd/config/get',cmd,qos=1)
        self.assertTrue(wait(lambda:self.published(self.edge_broker,'cmd/config/get'),5),self.edge.status())
        time.sleep(1)
        self.assertEqual([m['payload'] for m in self.published(self.edge_broker,'cmd/config/get')],[cmd])
        self.edge_pub.offer('k','sdr/v2/uav-01/ack/config',b'{"v":2,"id":"c1","status":"APPLIED"}',qos=1)
        self.assertTrue(wait(lambda:self.published(self.ground_broker,'ack/config'),5))
        # The bridges never echo their own direction back.
        self.assertFalse(self.published(self.ground_broker,'cmd/config/get')[1:] )
        self.assertFalse(self.published(self.edge_broker,'ack/config')[1:])
    def test_link_loss_is_reported_and_retained_topics_are_resent(self):
        avail=b'{"v":2,"sid":"7a8b9c0d","contract":"1.6","online":true,"t":1}'
        self.edge_pub.offer('v','sdr/v2/uav-01/availability',avail,qos=1,retain=True)
        self.assertTrue(wait(lambda:self.published(self.ground_broker,'availability'),5))
        self.assertTrue(wait(lambda:self.ground.status()['link']=='UP',5))
        self.radio_on.clear()
        self.assertTrue(wait(lambda:self.ground.status()['link']=='DOWN',5))
        status=json.loads(self.ground_broker.retained['sdr/v2/uav-01/lora/ground']) if wait(
            lambda:json.loads(self.ground_broker.retained.get('sdr/v2/uav-01/lora/ground',b'{}') or b'{}').get('link')=='DOWN',3) else {}
        self.assertEqual(status.get('link'),'DOWN')
        before=len(self.published(self.ground_broker,'availability'))
        self.radio_on.set()
        self.assertTrue(wait(lambda:len(self.published(self.ground_broker,'availability'))>before,8),self.edge.status())
        self.assertIn('clock_offset_ms',self.ground.status()['peer'])
        # Over the radio the edge gets NTP-style samples; both clocks are this host's, so ~0 offset.
        self.assertTrue(wait(lambda:self.edge.status()['time'],5))
        t=self.edge.status()['time'];self.assertLess(abs(t['offset_ms']),200);self.assertGreater(t['delay_ms'],0)
    def test_retained_command_on_ground_is_not_replayed(self):
        self.ground_pub.offer('c','sdr/v2/uav-01/cmd/stream/set',b'{"old":1}',qos=1,retain=True)
        time.sleep(1)
        # The bridge subscribes without retained messages, and drops any flagged retained anyway.
        self.assertFalse(self.published(self.edge_broker,'cmd/stream/set'))
        self.ground._from_mqtt('sdr/v2/uav-01/cmd/stream/set',b'{"old":1}',True)
        self.assertGreaterEqual(self.ground.ignored,1)

if __name__=='__main__': unittest.main()
