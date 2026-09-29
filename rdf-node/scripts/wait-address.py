#!/usr/bin/env python3
"""Wait for an exact local IP before binding the isolated Ground MQTT listener."""
import json,subprocess,sys,time
ip=sys.argv[1]; timeout=int(sys.argv[2]) if len(sys.argv)>2 else 60
for _ in range(timeout):
    try:
        r=subprocess.run(['ip','-j','addr','show'],capture_output=True,text=True,timeout=2)
        for iface in json.loads(r.stdout):
            if any(a.get('local')==ip for a in iface.get('addr_info',[])): sys.exit(0)
    except Exception: pass
    time.sleep(1)
print('PPP local address not available; service manager will retry.',file=sys.stderr)
sys.exit(1)
