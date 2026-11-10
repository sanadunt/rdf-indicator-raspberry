#!/bin/sh
# On the Pi: Edge bridge send rate and link counters from its status lines over the last N s (default 150).
journalctl -u rdf-lora-edge --since "-${1:-150}s" --no-pager -o cat | grep "^{" | python3 -c "
import sys,json
L=[json.loads(l) for l in sys.stdin]; a,b=L[0],L[-1]; dt=(b['t']-a['t'])/1000
print('edge_status_n',len(L),'dt',dt,'tx_Bps',round((b['tx']['sent_bytes']-a['tx']['sent_bytes'])/dt),'tx_fps',round((b['tx']['sent_frames']-a['tx']['sent_frames'])/dt,2),'rx_frames',b['rx']['frames']-a['rx']['frames'],'rx_lost',b['rx']['lost_frames']-a['rx']['lost_frames'],'link',b['link'],'link_changes',b.get('link_changes'))"
