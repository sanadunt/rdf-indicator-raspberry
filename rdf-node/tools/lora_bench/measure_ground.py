#!/usr/bin/env python3
"""On the groundstation: curve arrivals at the console and the Ground bridge's link counters over N s.
Usage: python3 measure_ground.py [seconds]"""
import sys
import json,time,urllib.request,subprocess
def snap(): return json.load(urllib.request.urlopen("http://127.0.0.1:8787/api/snapshot"))
def st():
  l=subprocess.run(["journalctl","-u","rdf-lora-ground","-n","1","--no-pager","-o","cat"],capture_output=True,text=True).stdout
  return json.loads(l)
s0=st(); t0=time.time(); seen={}
SECONDS=float(sys.argv[1]) if len(sys.argv)>1 else 120.0
while time.time()-t0<SECONDS:
  try:
    a=snap()["mqtt"].get("angular") or {}; r=a.get("received_at_ms")
    if r and r not in seen: seen[r]=(a.get("pairing"),len(a.get("values") or []),a.get("upsampled_from"))
  except Exception: pass
  time.sleep(0.5)
s1=st(); rs=sorted(seen)
print("curves",len(rs),"gaps_s",[round((b-a)/1000,1) for a,b in zip(rs,rs[1:])])
v=list(seen.values());print("peak_moved",sum(1 for x in v if x[0]=="PEAK_MOVED"),"of",len(v),"upsampled_from",sorted(set(x[2] for x in v),key=str))
print("ground_rx frames",s1["rx"]["frames"]-s0["rx"]["frames"],"lost",s1["rx"]["lost_frames"]-s0["rx"]["lost_frames"],"incomplete",s1["rx"]["incomplete"]-s0["rx"]["incomplete"])
print("ground_tx frames",s1["tx"]["sent_frames"]-s0["tx"]["sent_frames"],"bytes",s1["tx"]["sent_bytes"]-s0["tx"]["sent_bytes"])
print("edge_reports_rx",s1["peer"]["rx_frames"]-s0["peer"]["rx_frames"],"edge_reports_lost",s1["peer"]["lost_frames"]-s0["peer"]["lost_frames"],"ground_link",s1["link"])
