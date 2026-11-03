"""Heading offset check against a transmitter at a known place (run.py nav-calibrate).

Reads the running Edge's local API once a second, compares the bearing it makes (array heading and
relative DoA, as telemetry/doa b) with the true bearing from the node's GPS position to the transmitter, and prints the
offset change that would make them agree. It never writes the configuration.
"""
from __future__ import annotations
import json
import math
import time
import urllib.request
from .nav import bearing_deg

def true_bearing(lat1,lon1,lat2,lon2):
    """Initial great-circle bearing from point 1 to point 2, degrees clockwise from north."""
    p1,p2=math.radians(lat1),math.radians(lat2); dl=math.radians(lon2-lon1)
    y=math.sin(dl)*math.cos(p2); x=math.cos(p1)*math.sin(p2)-math.sin(p1)*math.cos(p2)*math.cos(dl)
    return math.degrees(math.atan2(y,x))%360

def distance_m(lat1,lon1,lat2,lon2):
    p1,p2=math.radians(lat1),math.radians(lat2)
    h=math.sin((p2-p1)/2)**2+math.cos(p1)*math.cos(p2)*math.sin(math.radians(lon2-lon1)/2)**2
    return 2*6371000*math.asin(math.sqrt(h))

def wrap180(deg):
    return (deg+180)%360-180

def sample(snapshot,tx_lat,tx_lon):
    """One comparison from an Edge snapshot, or (None, reason) when it cannot be used."""
    d=snapshot.get('detection') or {}; nav=snapshot.get('nav') or {}
    heading=nav.get('array_heading'); g=nav.get('gps') or {}
    if not d.get('valid') or d.get('relative_doa_deg') is None: return None,'DOA_NOT_VALID'
    if not heading: return None,'NO_ARRAY_HEADING'
    if g.get('state')!='FIX' or g.get('lat') is None or g.get('lon') is None: return None,'NO_GPS_FIX'
    angle=(snapshot.get('capabilities') or {}).get('angle')
    if angle not in ('theta_mirror','csv_native'): return None,'ANGLE_CONVENTION_UNKNOWN'
    measured=bearing_deg(heading['heading_deg'],d['relative_doa_deg'],angle)
    truth=true_bearing(g['lat'],g['lon'],tx_lat,tx_lon)
    return {'q':d.get('q'),'source':heading['source'],'measured':measured,'true':truth,
            'error':wrap180(truth-measured),'distance_m':distance_m(g['lat'],g['lon'],tx_lat,tx_lon)},None

def summarize(samples):
    """Circular mean and spread of the errors (true - measured), degrees."""
    if not samples: return None
    s=sum(math.sin(math.radians(x['error'])) for x in samples); c=sum(math.cos(math.radians(x['error'])) for x in samples)
    r=min(1.0,math.hypot(s,c)/len(samples))
    spread=math.degrees(math.sqrt(-2*math.log(r))) if r>0 else 180.0
    sources={}
    for x in samples: sources[x['source']]=sources.get(x['source'],0)+1
    return {'n':len(samples),'mean_error':round(wrap180(math.degrees(math.atan2(s,c))),1),'spread':round(spread,1),
            'sources':sources,'distance_m':round(sum(x['distance_m'] for x in samples)/len(samples))}

def run(args):
    url=f'http://127.0.0.1:{args.port}/api/v2/snapshot'
    samples=[]; skipped={}; last_q=None; end=time.monotonic()+args.seconds
    print(f'Membandingkan bearing dengan pemancar di {args.tx_lat:.5f},{args.tx_lon:.5f} selama {args.seconds:.0f} s...',flush=True)
    while time.monotonic()<end:
        try:
            with urllib.request.urlopen(url,timeout=3) as r: snap=json.load(r)
        except (OSError,ValueError) as e:
            skipped['API_'+type(e).__name__.upper()]=skipped.get('API_'+type(e).__name__.upper(),0)+1
            time.sleep(1); continue
        x,why=sample(snap,args.tx_lat,args.tx_lon)
        if x and x['q']==last_q: why='SAME_SAMPLE'; x=None
        if x:
            last_q=x['q']; samples.append(x)
            print(f"  {x['source']:7s} terukur {x['measured']:6.1f}°  benar {x['true']:6.1f}°  selisih {x['error']:+6.1f}°",flush=True)
        else: skipped[why]=skipped.get(why,0)+1
        time.sleep(1)
    result=summarize(samples)
    if skipped: print('Dilewati: '+', '.join(f'{k} {v}' for k,v in sorted(skipped.items())))
    if not result:
        print('Tidak ada sampel yang bisa dipakai (perlu DoA VALID, heading array, dan fix GPS).'); return 1
    main=max(result['sources'],key=result['sources'].get)
    key='nav.compass.offset_deg' if main=='compass' else 'nav.heading.travel_offset_deg'
    print(f"\n{result['n']} sampel, jarak rata-rata ke pemancar {result['distance_m']} m, sumber heading {result['sources']}")
    print(f"Selisih rata-rata (benar - terukur): {result['mean_error']:+.1f}°, sebaran ±{result['spread']:.1f}°")
    if result['spread']>20:
        print('Sebaran besar: multipath, pemancar terlalu dekat, atau tanda sudut terbalik. Ulangi di tempat terbuka '
              'dan putar node: bearing terukur harus tetap, bukan ikut berputar.')
    print(f"Saran: tambahkan {result['mean_error']:+.1f}° ke {key} lalu restart rdf-edge.")
    return 0
