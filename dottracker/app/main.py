import asyncio, hashlib, hmac, json, os, secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import jwt
import psycopg
from psycopg.rows import dict_row
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from passlib.context import CryptContext
from pydantic import BaseModel, Field

DATABASE_URL=os.getenv('DATABASE_URL','postgresql://dottracker:dottracker_dev@localhost:5432/dottracker')
JWT_SECRET=os.getenv('JWT_SECRET','dev-only-change-this-secret-before-deploying')
DEVICE_TOKEN=os.getenv('DEVICE_TOKEN','demo-device-token')
ACCESS_MINUTES=int(os.getenv('ACCESS_TOKEN_MINUTES','30'))
pwd=CryptContext(schemes=['bcrypt'], deprecated='auto')

@asynccontextmanager
async def lifespan(app):
    for _ in range(30):
        try:
            with psycopg.connect(DATABASE_URL) as c:
                c.execute('SELECT 1')
            break
        except psycopg.OperationalError:
            await asyncio.sleep(1)
    else: raise RuntimeError('Database unavailable')
    yield
app=FastAPI(title='dotTracker API', version='0.1.0', lifespan=lifespan)
app.mount('/static',StaticFiles(directory='/app/app/static'),name='static')

def db():
    try:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as c:
            yield c
    except psycopg.Error as e:
        raise HTTPException(503,'Database request failed') from e

def token_for(user):
    now=datetime.now(timezone.utc)
    return jwt.encode({'sub':str(user['id']),'org':user['org_id'],'role':user['role'],'iat':now,'exp':now+timedelta(minutes=ACCESS_MINUTES)},JWT_SECRET,algorithm='HS256')

def current_user(authorization: str=Header(default=''), c=Depends(db)):
    if not authorization.startswith('Bearer '): raise HTTPException(401,'Sign in required')
    try: p=jwt.decode(authorization[7:],JWT_SECRET,algorithms=['HS256'])
    except jwt.PyJWTError: raise HTTPException(401,'Session expired; sign in again')
    u=c.execute('SELECT id,org_id,email,role FROM users WHERE id=%s AND org_id=%s',(p['sub'],p['org'])).fetchone()
    if not u: raise HTTPException(401,'Invalid session')
    return u

def admin(u=Depends(current_user)):
    if u['role']!='admin': raise HTTPException(403,'Admin role required')
    return u

class Login(BaseModel): email:str; password:str
class ObservationIn(BaseModel): lat:float=Field(ge=-90,le=90); lng:float=Field(ge=-180,le=180); observed_at:Optional[datetime]=None; note:str=Field(default='',max_length=2000); asset_id:Optional[int]=None
class AssetIn(BaseModel): name:str=Field(min_length=1,max_length=120); kind:str='vehicle'
class GeofenceIn(BaseModel): name:str=Field(min_length=1,max_length=120); shape:str; geometry:dict
class PositionIn(BaseModel): device_id:str; token:str; lat:float=Field(ge=-90,le=90); lng:float=Field(ge=-180,le=180); speed:float=Field(default=0,ge=0); heading:Optional[float]=None; recorded_at:Optional[datetime]=None

@app.get('/')
def home(): return FileResponse('/app/app/static/index.html')
@app.get('/health')
def health(c=Depends(db)):
    c.execute('SELECT 1'); return {'status':'ok'}

@app.post('/api/auth/login')
def login(body:Login,c=Depends(db)):
    u=c.execute('SELECT id,org_id,email,role,password_hash FROM users WHERE lower(email)=lower(%s)',(body.email,)).fetchone()
    if not u or not pwd.verify(body.password,u['password_hash']): raise HTTPException(401,'Email or password is incorrect')
    return {'access_token':token_for(u),'token_type':'bearer','user':{'email':u['email'],'role':u['role'],'org_id':u['org_id']}}
@app.get('/api/me')
def me(u=Depends(current_user)): return u

@app.get('/api/assets')
def assets(c=Depends(db),u=Depends(current_user)):
    rows=c.execute('''SELECT a.id,a.name,a.kind,p.lat,p.lng,p.speed,p.heading,p.recorded_at,
      CASE WHEN p.recorded_at IS NULL OR p.recorded_at < now()-interval '60 seconds' THEN 'offline' WHEN p.speed > 2 THEN 'moving' ELSE 'stopped' END status
      FROM assets a LEFT JOIN LATERAL (SELECT * FROM positions WHERE asset_id=a.id AND org_id=a.org_id ORDER BY recorded_at DESC LIMIT 1) p ON true
      WHERE a.org_id=%s ORDER BY a.name''',(u['org_id'],)).fetchall()
    return rows
@app.post('/api/assets')
def add_asset(body:AssetIn,c=Depends(db),u=Depends(admin)):
    row=c.execute('INSERT INTO assets(org_id,name,kind) VALUES(%s,%s,%s) RETURNING id,name,kind',(u['org_id'],body.name.strip(),body.kind)).fetchone(); c.commit(); return row
@app.get('/api/assets/{asset_id}/positions')
def history(asset_id:int,from_:Optional[datetime]=Query(None,alias='from'),to:Optional[datetime]=None,c=Depends(db),u=Depends(current_user)):
    start=from_ or datetime.now(timezone.utc)-timedelta(days=30); end=to or datetime.now(timezone.utc)
    if start < datetime.now(timezone.utc)-timedelta(days=30): start=datetime.now(timezone.utc)-timedelta(days=30)
    rows=c.execute('SELECT id,lat,lng,speed,heading,recorded_at FROM positions WHERE org_id=%s AND asset_id=%s AND recorded_at BETWEEN %s AND %s ORDER BY recorded_at LIMIT 10000',(u['org_id'],asset_id,start,end)).fetchall()
    return rows

@app.get('/api/geofences')
def geofences(c=Depends(db),u=Depends(current_user)):
    return c.execute('SELECT id,name,shape,geometry,enabled FROM geofences WHERE org_id=%s ORDER BY name',(u['org_id'],)).fetchall()
def inside(f,lat,lng):
    g=f['geometry']
    if f['shape']=='circle':
        from math import radians,sin,cos,asin,sqrt
        dlat=radians(lat-g['lat']);dlng=radians(lng-g['lng']); a=sin(dlat/2)**2+cos(radians(lat))*cos(radians(g['lat']))*sin(dlng/2)**2
        return 6371000*2*asin(sqrt(a))<=float(g['radius_m'])
    pts=g.get('coordinates',[]); hit=False; j=len(pts)-1
    for i,(x,y) in enumerate(pts):
        x2,y2=pts[j]
        if ((y>lat)!=(y2>lat)) and lng<(x2-x)*(lat-y)/(y2-y+1e-20)+x: hit=not hit
        j=i
    return hit
@app.post('/api/geofences')
def add_geofence(b:GeofenceIn,c=Depends(db),u=Depends(admin)):
    if b.shape not in ('circle','polygon'): raise HTTPException(422,'shape must be circle or polygon')
    if b.shape=='circle' and not all(k in b.geometry for k in ('lat','lng','radius_m')): raise HTTPException(422,'circle needs lat, lng, radius_m')
    if b.shape=='polygon' and len(b.geometry.get('coordinates',[]))<3: raise HTTPException(422,'polygon needs at least three [lng,lat] points')
    row=c.execute('INSERT INTO geofences(org_id,name,shape,geometry) VALUES(%s,%s,%s,%s) RETURNING id,name,shape,geometry,enabled',(u['org_id'],b.name,b.shape,json.dumps(b.geometry))).fetchone();c.commit();return row
@app.delete('/api/geofences/{fid}')
def delete_geofence(fid:int,c=Depends(db),u=Depends(admin)):
    cur=c.execute('DELETE FROM geofences WHERE id=%s AND org_id=%s',(fid,u['org_id']));c.commit()
    if cur.rowcount==0: raise HTTPException(404,'Geofence not found')
    return {'deleted':True}

@app.post('/api/observations')
def add_observation(b:ObservationIn,c=Depends(db),u=Depends(current_user)):
    ts=b.observed_at or datetime.now(timezone.utc)
    if b.asset_id and not c.execute('SELECT 1 FROM assets WHERE id=%s AND org_id=%s',(b.asset_id,u['org_id'])).fetchone(): raise HTTPException(404,'Asset not found')
    row=c.execute('INSERT INTO observations(org_id,asset_id,author_id,lat,lng,observed_at,note) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id,asset_id,author_id,lat,lng,observed_at,note,photo_ref',(u['org_id'],b.asset_id,u['id'],b.lat,b.lng,ts,b.note.strip())).fetchone();c.commit();return row
@app.get('/api/observations')
def observations(c=Depends(db),u=Depends(current_user),asset_id:Optional[int]=None,from_:Optional[datetime]=Query(None,alias='from'),to:Optional[datetime]=None):
    q='SELECT o.id,o.asset_id,o.lat,o.lng,o.observed_at,o.note,o.author_id,u.email author FROM observations o JOIN users u ON u.id=o.author_id WHERE o.org_id=%s'; args=[u['org_id']]
    if asset_id: q+=' AND o.asset_id=%s';args.append(asset_id)
    if from_: q+=' AND o.observed_at >= %s';args.append(from_)
    if to: q+=' AND o.observed_at <= %s';args.append(to)
    q+=' ORDER BY o.observed_at DESC LIMIT 2000';return c.execute(q,args).fetchall()
@app.get('/api/alerts')
def alerts(c=Depends(db),u=Depends(current_user)):
    return c.execute('SELECT id,asset_id,geofence_id,message,created_at,read_at FROM alerts WHERE org_id=%s ORDER BY created_at DESC LIMIT 100',(u['org_id'],)).fetchall()

@app.post('/api/ingest')
def ingest(b:PositionIn,c=Depends(db),x_device_token:str=Header(default='')):
    configured=DEVICE_TOKEN
    if not hmac.compare_digest(x_device_token,configured): raise HTTPException(401,'Invalid device credential')
    d=c.execute('SELECT d.org_id,d.asset_id FROM devices d JOIN assets a ON a.id=d.asset_id WHERE a.device_id=%s AND d.enabled=true',(b.device_id,)).fetchone()
    if not d: raise HTTPException(404,'Device not registered')
    if not hmac.compare_digest(hashlib.sha256(b.token.encode()).hexdigest(),c.execute('SELECT device_token_hash FROM devices WHERE asset_id=%s',(d['asset_id'],)).fetchone()['device_token_hash']): raise HTTPException(401,'Invalid tracker token')
    ts=b.recorded_at or datetime.now(timezone.utc)
    c.execute('INSERT INTO positions(org_id,asset_id,lat,lng,speed,heading,recorded_at) VALUES(%s,%s,%s,%s,%s,%s,%s)',(d['org_id'],d['asset_id'],b.lat,b.lng,b.speed,b.heading,ts))
    fences=c.execute('SELECT id,name,shape,geometry FROM geofences WHERE org_id=%s AND enabled=true',(d['org_id'],)).fetchall()
    for f in fences:
        prev=c.execute('SELECT lat,lng FROM positions WHERE asset_id=%s AND recorded_at<%s ORDER BY recorded_at DESC LIMIT 1',(d['asset_id'],ts)).fetchone()
        was=inside(f,prev['lat'],prev['lng']) if prev else False; now=inside(f,b.lat,b.lng)
        if was!=now:
            ev='enter' if now else 'exit'
            e=c.execute('INSERT INTO geofence_events(org_id,geofence_id,asset_id,event,occurred_at) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id',(d['org_id'],f['id'],d['asset_id'],ev,ts)).fetchone()
            if e: c.execute('INSERT INTO alerts(org_id,asset_id,geofence_id,event_id,message) VALUES(%s,%s,%s,%s,%s)',(d['org_id'],d['asset_id'],f['id'],e['id'],f"{f['name']}: {ev}"))
    c.commit(); asyncio.get_running_loop().create_task(hub.publish(d['org_id'],{'type':'position','asset_id':d['asset_id']})); return {'accepted':True}

class Hub:
    def __init__(self): self.clients={}
    async def send(self,ws,org): self.clients.setdefault(org,set()).add(ws)
    def remove(self,ws,org): self.clients.get(org,set()).discard(ws)
    async def publish(self,org,payload):
        for ws in list(self.clients.get(org,set())):
            try: await ws.send_json(payload)
            except Exception: self.remove(ws,org)
hub=Hub()
@app.websocket('/api/live')
async def live(ws:WebSocket,token:str):
    await ws.accept()
    try:
        p=jwt.decode(token,JWT_SECRET,algorithms=['HS256']); org=int(p['org'])
    except Exception:
        await ws.close(code=4401);return
    await hub.send(ws,org)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: hub.remove(ws,org)

@app.post('/api/dev/seed')
def seed(c=Depends(db)):
    # One-time demo bootstrap; disabled outside explicit local development.
    if os.getenv('ALLOW_DEMO_SEED','true').lower()!='true': raise HTTPException(404,'Not found')
    if c.execute('SELECT 1 FROM users LIMIT 1').fetchone(): return {'seeded':False,'message':'Database already has users'}
    org=c.execute("INSERT INTO organizations(name) VALUES('Snow Seasons Demo Fleet') RETURNING id").fetchone()['id']
    user=c.execute('INSERT INTO users(org_id,email,password_hash,role) VALUES(%s,%s,%s,%s) RETURNING id,org_id,email,role',(org,'admin@dottracker.local',pwd.hash('dottracker-demo'), 'admin')).fetchone()
    for n,device in [('Truck 01','truck-01'),('Trailer 02','trailer-02'),('Service Van','van-03')]:
        asset=c.execute('INSERT INTO assets(org_id,name,kind,device_id) VALUES(%s,%s,%s,%s) RETURNING id',(org,n,'vehicle',device)).fetchone()['id']
        c.execute('INSERT INTO devices(org_id,asset_id,device_token_hash) VALUES(%s,%s,%s)',(org,asset,hashlib.sha256(DEVICE_TOKEN.encode()).hexdigest()))
        c.execute('INSERT INTO positions(org_id,asset_id,lat,lng,speed,heading,recorded_at) VALUES(%s,%s,%s,%s,%s,%s,now())',(org,asset,39.7392+(asset-1)*.015,-104.9903+(asset-1)*.02,12 if asset==1 else 0,90))
    c.execute("INSERT INTO geofences(org_id,name,shape,geometry) VALUES(%s,'Denver Yard','circle',%s)",(org,json.dumps({'lat':39.7392,'lng':-104.9903,'radius_m':400})))
    c.commit();return {'seeded':True,'email':user['email'],'password':'dottracker-demo'}
