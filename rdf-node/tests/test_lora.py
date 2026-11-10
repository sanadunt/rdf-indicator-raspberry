import hashlib
import json,os,select,tempfile,threading,time,unittest
from unittest import mock
from pathlib import Path
from rdf_node import lora
from rdf_node.lora import (DATA,EDGE_TOPICS,GROUND_TOPICS,TOPIC_IDS,Bridge,Receiver,Sender,cobs_decode,cobs_encode,
                           crc16,decode_frame,encode_frames)
from rdf_node.mqtt import Client
from broker_fixture import Broker,wait

ANGULAR=json.dumps({'v':2,'encoding':'json','sid':'a3da8b3b','q':1,'timestamp_ms':1793446155142,'frequency_hz':433920000,
                    'revision':1,'vfo':0,'convention':1,'flags':63,'raw_doa_deg':137.0,'confidence_native_db':8.27,
                    'values':[round((d*7919%1000)/50,2) for d in range(360)]},separators=(',',':')).encode()
def angular_1_12(values=None):
    if values is None:
        import math
        values=[round(0.6+0.7*math.exp(-(((2*k-137+180)%360-180)/14)**2),2) for k in range(180)]
    return json.dumps({'v':2,'encoding':'json','sid':'a3da8b3b','q':7,'timestamp_ms':1793446155142,
                       'frequency_hz':433920000,'revision':1,'vfo':0,'convention':1,'flags':63,'raw_doa_deg':137.0,
                       'confidence_native_db':8.27,'heading_deg':96.5,'heading_source':'C','step_deg':2,
                       'values':values},separators=(',',':')).encode()
ANGULAR_180=angular_1_12()
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
    def test_commands_go_three_times_acks_twice_and_each_arrives_once(self):
        s=Sender(100000,200);t=1000.0;s.offer(65,b'{"op":"config.patch"}',now=t)
        first=s.next_frames(t);r=Receiver()
        self.assertEqual(len(first),1)
        # The copy waits REPEAT_GAP_S so a single collision cannot take both.
        self.assertEqual(s.next_frames(t+lora.REPEAT_GAP_S/2),[])
        t2=t+lora.REPEAT_GAP_S+.01;again=s.next_frames(t2);self.assertEqual(len(again),1)
        # The third send waits twice as long, so it lands in a later turn of the edge.
        self.assertEqual(s.next_frames(t2+lora.REPEAT_GAP_S*1.5),[])
        third=s.next_frames(t2+lora.REPEAT_GAP_S*2+.01);self.assertEqual(len(third),1)
        self.assertEqual(len({decode_frame(f[:-1])[2] for f in first+again+third}),1)  # one message id
        self.assertEqual(len(r.feed(b''.join(first+again+third))),1);self.assertEqual(r.stats['duplicates'],2)
        self.assertEqual(s.depth(),0)
        # An ACK goes twice.
        s.offer(11,b'{"ack":1}',now=t);sent=[];x=t
        for _ in range(40): x+=.1;sent+=s.next_frames(x)
        self.assertEqual(len(sent),2)
    def test_a_restarted_peer_is_not_taken_for_copies(self):
        # Message ids start again when the other bridge restarts; its lower uptime tells the receiver.
        def beat(up):
            s=Sender(100000,200);s.heartbeat(up,Receiver().stats,0.0);return s.next_frames(1.0)[0]
        r=Receiver();t=100.0
        self.assertEqual(len(r.feed(beat(50)+encode_frames(DATA,0,7,64,b'{"id":"a"}')[0],t)),1)
        self.assertEqual(r.feed(encode_frames(DATA,1,7,64,b'{"id":"a"}')[0],t+1),[])  # a copy
        self.assertEqual(r.feed(beat(55),t+2),[]);self.assertEqual(r.stats['peer_restarts'],0)
        out=r.feed(beat(1)+encode_frames(DATA,0,7,64,b'{"id":"b"}')[0],t+3)
        self.assertEqual(out,[(64,b'{"id":"b"}',False,False)]);self.assertEqual(r.stats['peer_restarts'],1)
        self.assertEqual(r.feed(encode_frames(DATA,1,7,64,b'{"id":"b"}')[0],t+4),[])  # copies still dropped
    def test_a_peer_restarted_within_one_heartbeat_is_seen(self):
        # A crash loop (or a run shorter than heartbeat_s) sends uptime 0 every start: the same uptime a second
        # or more later by the peer's own clock is a new run; the same uptime within that second is not.
        def beat(up,clock_ms):
            s=Sender(100000,200)
            with mock.patch.object(lora,'now_ms',return_value=clock_ms):  # stamped as it leaves
                s.heartbeat(up,Receiver().stats,0.0);return s.next_frames(1.0)[0]
        r=Receiver();t=100.0;c=1_793_000_000_000
        self.assertEqual(len(r.feed(beat(0,c)+encode_frames(DATA,0,7,64,b'{"id":"a"}')[0],t)),1)
        self.assertEqual(r.feed(beat(0,c+400),t+.5),[]);self.assertEqual(r.stats['peer_restarts'],0)
        out=r.feed(beat(0,c+3_400)+encode_frames(DATA,0,7,64,b'{"id":"b"}')[0],t+3)
        self.assertEqual(out,[(64,b'{"id":"b"}',False,False)]);self.assertEqual(r.stats['peer_restarts'],1)
        self.assertEqual(r.feed(beat(5,c+8_400),t+8),[]);self.assertEqual(r.stats['peer_restarts'],1)
    def test_message_ids_start_anywhere(self):
        self.assertGreater(len({Sender(1000,200).msg for _ in range(64)}),8)
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
    def test_quiet_window_counts_from_the_air_not_the_write(self):
        # Frames written just before the turn frame are still on the air when it is written: the other
        # side's turn starts after all of them, so its reply does not collide with the edge.
        s=Sender(100000,200,burst=1000,turn_every_s=1.0,quiet_s=.3);t=1000.0
        s.last_turn=t-.5
        s.offer(4,b''.join(hashlib.sha256(bytes([i])).digest() for i in range(20)),now=t)  # 640 bytes zlib cannot shrink
        burst=s.next_frames(t)
        self.assertGreater(len(burst),1)
        sent=sum(len(f) for f in burst)
        s.offer(11,b'{"ack":1}',now=t+.5)
        turn=s.next_frames(t+.5)
        self.assertEqual(len(turn),1);self.assertTrue(decode_frame(turn[0][:-1])[4]&lora.F_TURN)
        air_end=max(t+sent*lora.AIR_S_PER_BYTE,t+.5)+len(turn[0])*lora.AIR_S_PER_BYTE
        self.assertAlmostEqual(s.next_at,air_end+.3,places=6)
        s.offer(11,b'{"ack":2}',now=t+.5)
        self.assertEqual(s.next_frames(air_end+.29),[])
        self.assertTrue(s.next_frames(air_end+.31))
    def test_quiet_window_outlasts_a_curve_at_the_back_to_back_rate(self):
        # Back to back the radio moves only ~1.2-1.5 KB/s: after a whole curve the edge must still leave the
        # full quiet window from the moment the curve really ends on the air (2026-11-10: 0.5 ms/byte did not).
        s=Sender(100000,200,burst=5000,turn_every_s=1.0,quiet_s=.45);t=1000.0
        s.last_turn=t-.5
        s.offer(4,b''.join(hashlib.sha256(bytes([i])).digest() for i in range(30)),now=t)  # ~1 KB, 5+ frames
        burst=s.next_frames(t)
        s.offer(11,b'{"ack":1}',now=t+.5)
        turn=s.next_frames(t+.5)
        self.assertTrue(decode_frame(turn[0][:-1])[4]&lora.F_TURN)
        on_air=sum(len(f) for f in burst+turn)/1250
        self.assertGreaterEqual(s.next_at,t+on_air+.45)
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

class PackedAngularTests(unittest.TestCase):
    def test_pack_angular_round_trip_180_and_360(self):
        p360=json.dumps({**json.loads(ANGULAR),'values':[round(0.5+(d%40)/100,2) for d in range(360)]},
                        separators=(',',':')).encode()
        for p in (ANGULAR_180,p360):
            body=lora.pack_angular(p);self.assertIsNotNone(body)
            self.assertEqual(lora.unpack_angular(body),p)
        self.assertLessEqual(len(lora.compress(lora.pack_angular(ANGULAR_180))[0]),400)
    def test_pack_angular_refuses_lossy(self):
        self.assertIsNone(lora.pack_angular(angular_1_12([0.123]*180)))         # not on the 0.01 grid
        self.assertIsNone(lora.pack_angular(angular_1_12([0.0]*179+[3.0])))     # range over 2.55
        self.assertIsNone(lora.pack_angular(ANGULAR))                           # range over 2.55 (0..19.98)
        last=json.dumps({'values':[0.1]*180,'q':1},separators=(',',':')).encode()
        self.assertIsNone(lora.pack_angular(last))                              # values not the last key
    def test_bridge_sends_packed_angular_in_two_frames(self):
        parts=lora.split_message(DATA,1,TOPIC_IDS['telemetry/angular'],lora.pack_angular(ANGULAR_180),packed=True)
        self.assertLessEqual(len(parts),2)
        self.assertTrue(all(p[3]&lora.F_PACKED for p in parts))
    def test_receiver_unpacks_packed_angular(self):
        s=Sender(100000,200,burst=10000);s.offer(TOPIC_IDS['telemetry/angular'],lora.pack_angular(ANGULAR_180),packed=True,now=1.0)
        frames=s.next_frames(1.0);r=Receiver();out=[]
        for f in frames: out+=r.feed(f)
        self.assertEqual([(t,p) for t,p,_,_ in out],[(TOPIC_IDS['telemetry/angular'],ANGULAR_180)])
    def test_unpacked_angular_still_passes_through(self):
        s=Sender(100000,200,burst=10000);s.offer(TOPIC_IDS['telemetry/angular'],ANGULAR,now=1.0)
        r=Receiver();out=[]
        for f in s.next_frames(1.0): out+=r.feed(f)
        self.assertEqual(out[0][1],ANGULAR)

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

class EdgePanelLoraTests(unittest.TestCase):
    """rdf-edge reads the bridge's retained lora/edge status and shows LoRa instead of PPP."""
    def setUp(self):
        from test_system import make_agent
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.a=make_agent(Path(os.path.realpath(self.tmp.name)));self.addCleanup(self.a.journal.close)
    def status(self,t,link='UP',sent=1000,msgs=10,rx=5,frames=50,lost=1,peer_frames=200,peer_lost=4):
        return json.dumps({'v':1,'role':'edge','t':t,'link':link,'last_rx_ms':300,'port':'OPEN','port_error':None,
            'mqtt':'CONNECTED','rx':{'frames':frames,'crc_errors':0,'bad_frames':0,'lost_frames':lost,'messages':rx,
            'duplicates':0,'incomplete':0,'heartbeats':10},'tx':{'queued':12,'superseded':0,'expired':1,
            'sent_messages':msgs,'sent_frames':20,'sent_bytes':sent,'too_large':0},'queue':0,
            'peer':{'uptime_s':10,'rx_frames':peer_frames,'crc_errors':0,'lost_frames':peer_lost,'clock_offset_ms':-12},
            'mqtt_dropped':0,'ignored':0,'link_changes':1,'time':{'offset_ms':16.4,'delay_ms':62.3}}).encode()
    def test_lora_status_drives_transport_rates_and_loss(self):
        a=self.a;topic=a.prefix+'/lora/edge'
        a._snapshot();self.assertEqual(a.snapshot()['link']['transport'],'PPP');self.assertIsNone(a.snapshot()['link']['lora'])
        a._incoming(topic,self.status(1_000_000),True)
        a._incoming(topic,self.status(1_005_000,sent=6000,msgs=20,rx=15),False)
        a._snapshot();l=a.snapshot()['link']
        self.assertEqual(l['transport'],'LORA');lo=l['lora']
        self.assertEqual((lo['link'],lo['tx_kbit_s'],lo['tx_msg_s'],lo['rx_msg_s']),('UP',8.0,2.0,2.0))
        self.assertEqual((lo['uplink_loss_pct'],lo['downlink_loss_pct'],lo['clock_offset_ms']),(2.0,2.0,16.4))
        # The status never reaches the command path.
        self.assertTrue(a.events.empty())
    def test_link_down_and_stale_bridge_raise_alerts(self):
        a=self.a;topic=a.prefix+'/lora/edge'
        a._incoming(topic,self.status(1_000_000,link='DOWN'),False);a._snapshot()
        self.assertIn('LORA_LINK_DOWN',[x['code'] for x in a.snapshot()['active_alerts']])
        a.lora_seen-=60;a._snapshot();snap=a.snapshot()
        self.assertEqual((snap['link']['transport'],snap['link']['lora']['link']),('PPP','STALE'))
        self.assertIn('LORA_BRIDGE_STALE',[x['code'] for x in snap['active_alerts']])
    def test_retained_status_is_as_old_as_its_stamp(self):
        # After an rdf-edge restart the broker replays the bridge's last status: a dead bridge stays stale.
        from rdf_node.util import now_ms
        a=self.a;topic=a.prefix+'/lora/edge'
        a._incoming(topic,self.status(now_ms()-90_000),True);a._snapshot()
        self.assertEqual(a.snapshot()['link']['lora']['link'],'STALE')
        self.assertIn('LORA_BRIDGE_STALE',[x['code'] for x in a.snapshot()['active_alerts']])
        a._incoming(topic,self.status(1_000_000),True);a._snapshot()   # no usable stamp: stale too
        self.assertEqual(a.snapshot()['link']['lora']['link'],'STALE')
        a._incoming(topic,self.status(now_ms()-2_000),True);a._snapshot()
        lo=a.snapshot()['link']['lora'];self.assertEqual(lo['link'],'UP');self.assertGreaterEqual(lo['status_age_ms'],1500)
    def test_bad_status_is_ignored(self):
        a=self.a
        for bad in (b'not json',b'[]',b'{"v":2,"role":"edge"}',b'{"v":1,"role":"ground"}'):
            a._incoming(a.prefix+'/lora/edge',bad,True)
        self.assertIsNone(a.lora);self.assertTrue(a.events.empty())
    def test_subscribed_on_the_primary_control_client_only(self):
        self.a.cfg['mqtt']['enabled']=True
        clients=self.a._new_mqtt_clients(dict(self.a.mqtt_desired,enabled=True))
        # Without Retain As Published: only the broker's replay arrives flagged retained.
        self.assertIn((self.a.prefix+'/lora/edge',0,False,False),clients['control'].subscriptions)
        self.assertNotIn(self.a.prefix+'/lora/edge',[s[0] for s in self.a._command_subscriptions()])
        self.assertTrue(all(len(s)==3 for s in self.a._command_subscriptions()))  # commands keep RAP
    def test_live_status_is_fresh_even_when_the_clock_stepped(self):
        # chrony may step the Pi clock between the bridge's stamp and now. Through rdf-edge's own subscription
        # (no Retain As Published) a retained status published while it listens arrives unflagged, so its
        # stamp does not age it; only the broker's replay on subscribe is aged by `t`.
        from rdf_node.util import now_ms
        a=self.a;topic=a.prefix+'/lora/edge';broker=Broker();self.addCleanup(broker.stop)
        creds=Path(self.tmp.name)/'creds.json';creds.write_text(json.dumps({'username':'test','password':'test-password'}))
        broker.retained[topic]=self.status(now_ms()-90_000)   # left by a bridge that has since stopped
        a.cfg['mqtt'].update(enabled=True,host='127.0.0.1',port=broker.port,tls=False,allow_insecure_loopback=True,
                             control_credentials_file=str(creds),keepalive=5,reconnect_max=5)
        flags=[];incoming=a._incoming
        def record(topic,payload,retained,**kw):
            if topic.endswith('/lora/edge'):flags.append(retained)
            return incoming(topic,payload,retained,**kw)
        a._incoming=record
        control=a._new_mqtt_clients(dict(a.mqtt_desired,enabled=True))['control'];self.addCleanup(control.stop);control.start()
        self.assertTrue(wait(lambda:control.ready and flags,5),(control.status(),broker.errors))
        a._snapshot();self.assertEqual((flags,a.snapshot()['link']['lora']['link']),([True],'STALE'))
        bridge=Client(cfg=dict(a.cfg['mqtt'],control_credentials_file=None),client_id='bridge',credentials_file=str(creds))
        self.addCleanup(bridge.stop);bridge.start();self.assertTrue(wait(lambda:bridge.ready,5))
        for n,step in enumerate((700_000,-25_000),2):
            self.assertTrue(bridge.offer('s',topic,self.status(now_ms()+step),qos=1,retain=True))
            self.assertTrue(wait(lambda:len(flags)==n,5),broker.errors);a._snapshot()
            self.assertEqual((flags[-1],a.snapshot()['link']['lora']['link']),(False,'UP'),step)

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
    def test_packed_180_angular_arrives_byte_identical(self):
        self.edge_pub.offer('a','sdr/v2/uav-01/telemetry/angular',ANGULAR_180)
        self.assertTrue(wait(lambda:self.published(self.ground_broker,'telemetry/angular'),10),self.ground.status())
        self.assertEqual(self.published(self.ground_broker,'telemetry/angular')[-1]['payload'],ANGULAR_180)
        self.assertLessEqual(self.edge.sender.stats['sent_frames'],6)  # curve + a heartbeat or two
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
    def test_retained_topics_are_refreshed_while_the_link_stays_up(self):
        # Edge publishes capabilities once; a lost fragment must not lose it until the next link change.
        from unittest import mock
        from rdf_node import lora
        caps=b'{"v":2,"sid":"7a8b9c0d","version":"1.8.0","contract":"1.8"}'
        self.edge_pub.offer('c','sdr/v2/uav-01/capabilities',caps,qos=1,retain=True)
        self.assertTrue(wait(lambda:self.published(self.ground_broker,'capabilities'),5),self.edge.status())
        before=len(self.published(self.ground_broker,'capabilities'))
        with mock.patch.object(lora,'RETAINED_REFRESH_S',0.5):
            self.assertTrue(wait(lambda:len(self.published(self.ground_broker,'capabilities'))>before,8),self.edge.status())
        self.assertEqual(self.ground.status()['link_changes'],1)
        self.assertEqual(self.published(self.ground_broker,'capabilities')[-1]['payload'],caps)
    def test_retained_command_on_ground_is_not_replayed(self):
        self.ground_pub.offer('c','sdr/v2/uav-01/cmd/stream/set',b'{"old":1}',qos=1,retain=True)
        time.sleep(1)
        # The bridge subscribes without retained messages, and drops any flagged retained anyway.
        self.assertFalse(self.published(self.edge_broker,'cmd/stream/set'))
        self.ground._from_mqtt('sdr/v2/uav-01/cmd/stream/set',b'{"old":1}',True)
        self.assertGreaterEqual(self.ground.ignored,1)

if __name__=='__main__': unittest.main()
