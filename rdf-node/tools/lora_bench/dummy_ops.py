#!/usr/bin/env python3
"""Send harmless operation.get commands from Ground every N s and count the ACKs that come back.

Run on the groundstation from an rdf-node tree: python3 tools/lora_bench/dummy_ops.py [seconds] [every_s]
operation.get only reads the journal (an unknown target_id answers NOT_FOUND), so it changes nothing on the
Edge and does not pause Angular sending; its ACK proves the command went down and the reply came back."""
import json,os,sys,threading,time,uuid
sys.path[:0]=[os.path.join(os.path.dirname(__file__),'..','..','src'),os.path.join(os.path.dirname(__file__),'..','..','vendor')]
from rdf_node.mqtt import Client

PREFIX='sdr/v2/uav-01'
seconds=float(sys.argv[1]) if len(sys.argv)>1 else 180.0
every=float(sys.argv[2]) if len(sys.argv)>2 else 5.0
state={}; sent={}; acked={}; lock=threading.Lock()

def on_message(topic,payload,retained):
    try: body=json.loads(payload)
    except ValueError: return
    if topic==PREFIX+'/state': state.update(body)
    elif topic==PREFIX+'/ack/operation':
        with lock:
            if body.get('id') in sent and body['id'] not in acked: acked[body['id']]=time.monotonic()

c=Client(cfg={'host':'127.0.0.1','port':1883,'tls':False,'ca_file':None,'transport':'tcp','websocket_path':'/mqtt',
              'keepalive':15,'reconnect_max':5},client_id='lora-bench-ops',
         subscriptions=[(PREFIX+'/state',1,False),(PREFIX+'/ack/operation',1,False)],on_message=on_message,rate=100000)
c.start()
deadline=time.monotonic()+10
while not (c.ready and state.get('sid')) and time.monotonic()<deadline: time.sleep(.1)
if not state.get('sid'): sys.exit('no retained state from the Edge on the Ground broker')
start=time.monotonic()
while time.monotonic()-start<seconds:
    now_ms=int(time.time()*1000); rid='bench-'+uuid.uuid4().hex[:12]
    req={'v':2,'id':rid,'sid':state['sid'],'boot':state['boot'],'issued_ms':now_ms,'expires_ms':now_ms+15000,
         'base_rev':state.get('cfg'),'op':'operation.get','target_id':'bench-none'}
    with lock: sent[rid]=time.monotonic()
    c.offer(rid,PREFIX+'/cmd/operation/get',json.dumps(req,separators=(',',':')).encode(),qos=1)
    time.sleep(every)
time.sleep(20)  # late ACKs (commands live 15 s)
c.stop()
rtt=sorted(acked[i]-sent[i] for i in acked)
print(json.dumps({'sent':len(sent),'acked':len(acked),'ack_rate':round(len(acked)/max(1,len(sent)),3),
                  'rtt_p50_s':round(rtt[len(rtt)//2],2) if rtt else None,'rtt_max_s':round(rtt[-1],2) if rtt else None}))
