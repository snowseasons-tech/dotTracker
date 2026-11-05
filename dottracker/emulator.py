#!/usr/bin/env python3
"""Send demo GPS positions to a local dotTracker instance."""
import argparse, json, math, time, urllib.error, urllib.request

def main():
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://localhost:8000');p.add_argument('--count',type=int,default=3);p.add_argument('--interval',type=float,default=3);p.add_argument('--once',action='store_true');p.add_argument('--tracker-token',default='demo-device-token');a=p.parse_args()
    points=[(39.7392,-104.9903),(39.7542,-104.9703),(39.7242,-105.0103)]
    try:
      while True:
        for i in range(max(1,a.count)):
          lat,lng=points[i%len(points)];t=time.time();lat+=math.sin(t/18+i)*.004;lng+=math.cos(t/20+i)*.004
          body=json.dumps({'device_id':['truck-01','trailer-02','van-03'][i%3],'token':a.tracker_token,'lat':lat,'lng':lng,'speed':18 if i==0 else 0,'heading':90,'recorded_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}).encode()
          req=urllib.request.Request(a.url.rstrip('/')+'/api/ingest',data=body,headers={'Content-Type':'application/json'},method='POST')
          try:
            with urllib.request.urlopen(req,timeout=10) as r: print(r.status, r.read().decode())
          except urllib.error.HTTPError as e: print('ingestion failed:',e.code,e.read().decode())
        if a.once:break
        time.sleep(max(.5,a.interval))
    except KeyboardInterrupt: print('\nEmulator stopped')
if __name__=='__main__': main()
