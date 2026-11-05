import asyncio, hashlib, hmac, json, os, re, secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID
from urllib.parse import parse_qs, urlsplit

import jwt
import psycopg
from psycopg.rows import dict_row
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Path as ApiPath, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from pydantic import BaseModel, ConfigDict, Field
from app.geometry import inside

APP_ENV=os.getenv('APP_ENV','development').lower()
IS_PRODUCTION=APP_ENV=='production'
DATABASE_URL=os.getenv('DATABASE_URL','postgresql://dottracker:dottracker_dev@localhost:5432/dottracker')
JWT_SECRET=os.getenv('JWT_SECRET','local-only-change-this-secret-32-chars')
DEVICE_TOKEN=os.getenv('DEVICE_TOKEN','demo-device-token')
ACCESS_MINUTES=int(os.getenv('ACCESS_TOKEN_MINUTES','30'))
if not 1<=ACCESS_MINUTES<=60:
    raise RuntimeError('ACCESS_TOKEN_MINUTES must be between 1 and 60')
pwd=PasswordHasher()
DUMMY_PASSWORD_HASH=pwd.hash('timing-equalization-value-not-a-real-account')

if IS_PRODUCTION:
    if len(JWT_SECRET)<32 or 'REPLACE' in JWT_SECRET or JWT_SECRET=='local-only-change-this-secret-32-chars':
        raise RuntimeError('Production requires a unique JWT_SECRET of at least 32 characters')
    if os.getenv('ALLOW_DEMO_SEED','false').lower()!='false':
        raise RuntimeError('Production must set ALLOW_DEMO_SEED=false')
    if DATABASE_URL.startswith('postgresql://dottracker:dottracker_dev@'):
        raise RuntimeError('Production must use a managed database credential')
    database_urls=[('DATABASE_URL',DATABASE_URL)]
    if os.getenv('MIGRATION_DATABASE_URL'):
        database_urls.append(('MIGRATION_DATABASE_URL',os.environ['MIGRATION_DATABASE_URL']))
    for label, url in database_urls:
        parsed=urlsplit(url)
        modes=parse_qs(parsed.query).get('sslmode',[])
        if len(modes)!=1 or modes[0] not in ('require','verify-full'):
            raise RuntimeError(f'Production {label} must set sslmode=require or sslmode=verify-full exactly once')
        if 'db.example.net' in url:
            raise RuntimeError(f'Production {label} still contains the example hostname')

@asynccontextmanager
async def lifespan(app):
    for _ in range(30):
        try:
            with psycopg.connect(DATABASE_URL) as c:
                c.execute('SELECT 1')
                migrated=c.execute("SELECT 1 FROM schema_migrations WHERE version='0002-hardened-mvp'").fetchone()
                if not migrated: raise RuntimeError('Database schema migration 0002-hardened-mvp is required')
            break
        except psycopg.OperationalError:
            await asyncio.sleep(1)
    else: raise RuntimeError('Database unavailable')
    yield
app=FastAPI(title='dotTracker API', version='0.2.0', lifespan=lifespan,
            docs_url=None if IS_PRODUCTION else '/docs', redoc_url=None if IS_PRODUCTION else '/redoc',
            openapi_url=None if IS_PRODUCTION else '/openapi.json')
app.mount('/static',StaticFiles(directory=str(Path(__file__).parent/'static')),name='static')

@app.middleware('http')
async def security_headers(request, call_next):
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy']="default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; worker-src blob:; script-src 'self' https://unpkg.com/maplibre-gl@6.16.0/; style-src 'self' 'unsafe-inline' https://unpkg.com/maplibre-gl@6.16.0/; img-src 'self' data: blob:; connect-src 'self' wss: https://tiles.openfreemap.org https://*.openfreemap.org; font-src 'self' data: https://fonts.gstatic.com"
    response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api/') else 'no-cache'
    if request.url.scheme=='https': response.headers['Strict-Transport-Security']='max-age=31536000'
    return response

def db():
    try:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as c:
            yield c
    except psycopg.Error as e:
        raise HTTPException(503,'Database request failed') from e

def require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(422, f'{field_name} must include a timezone offset')
    return value

def token_for(user):
    now=datetime.now(timezone.utc)
    return jwt.encode({'sub':str(user['id']),'org':user['org_id'],'role':user['role'],'iat':now,'exp':now+timedelta(minutes=ACCESS_MINUTES)},JWT_SECRET,algorithm='HS256')

def current_user(authorization: str=Header(default=''), c=Depends(db)):
    if not authorization.startswith('Bearer '): raise HTTPException(401,'Sign in required')
    try: p=jwt.decode(authorization[7:],JWT_SECRET,algorithms=['HS256'])
    except jwt.PyJWTError: raise HTTPException(401,'Session expired; sign in again')
    try: subject=int(p['sub']); org_id=int(p['org'])
    except (KeyError,TypeError,ValueError): raise HTTPException(401,'Invalid session')
    u=c.execute('SELECT id,org_id,email,role FROM users WHERE id=%s AND org_id=%s',(subject,org_id)).fetchone()
    if not u: raise HTTPException(401,'Invalid session')
    return u

def admin(u=Depends(current_user)):
    if u['role']!='admin': raise HTTPException(403,'Admin role required')
    return u

class StrictModel(BaseModel): model_config=ConfigDict(allow_inf_nan=False,extra='forbid',str_strip_whitespace=True)
class Login(StrictModel): email:str=Field(min_length=3,max_length=320); password:str=Field(min_length=1,max_length=1024)
class ObservationIn(StrictModel): lat:float=Field(ge=-90,le=90); lng:float=Field(ge=-180,le=180); observed_at:Optional[datetime]=None; note:str=Field(default='',max_length=2000); asset_id:Optional[int]=Field(default=None,gt=0); client_id:Optional[UUID]=None
class AssetIn(StrictModel): name:str=Field(min_length=1,max_length=120); kind:str=Field(default='vehicle',min_length=1,max_length=40); device_id:Optional[str]=Field(default=None,min_length=1,max_length=120)
class GeofenceIn(StrictModel): name:str=Field(min_length=1,max_length=120); shape:str; geometry:dict
class PositionIn(StrictModel): device_id:str=Field(min_length=1,max_length=120); token:str=Field(min_length=16,max_length=256); lat:float=Field(ge=-90,le=90); lng:float=Field(ge=-180,le=180); speed:float=Field(default=0,ge=0,le=1000); heading:Optional[float]=Field(default=None,ge=0,lt=360); recorded_at:Optional[datetime]=None
class InviteIn(StrictModel): email:str=Field(min_length=3,max_length=320); role:str=Field(default='viewer',pattern='^(admin|viewer)$')
class InviteAccept(StrictModel): token:str=Field(min_length=32,max_length=128); password:str=Field(min_length=14,max_length=1024)
class RefreshIn(StrictModel): refresh_token:str=Field(min_length=32,max_length=128)

def issue_session(c,user):
    refresh=secrets.token_urlsafe(48)
    digest=hashlib.sha256(refresh.encode()).hexdigest()
    c.execute('INSERT INTO user_sessions(session_hash,user_id,org_id,expires_at) VALUES(%s,%s,%s,now()+interval \'30 days\')',
              (digest,user['id'],user['org_id']))
    return {'access_token':token_for(user),'refresh_token':refresh,'token_type':'bearer'}

@app.get('/')
def home(): return FileResponse(Path(__file__).parent/'static'/'index.html')
@app.get('/health')
def health(c=Depends(db)):
    c.execute('SELECT 1'); return {'status':'ok'}

@app.get('/health/live')
def live_health(): return {'status':'ok'}

@app.post('/api/auth/login')
def login(body:Login,request:Request,c=Depends(db)):
    normalized=body.email.strip().lower()
    throttle_key=hashlib.sha256(f"{request.client.host if request.client else 'unknown'}|{normalized}".encode()).hexdigest()
    limit=c.execute('SELECT failures,blocked_until FROM login_attempts WHERE throttle_key=%s',(throttle_key,)).fetchone()
    if limit and limit['blocked_until'] and limit['blocked_until']>datetime.now(timezone.utc):
        raise HTTPException(429,'Too many sign-in attempts. Try again later.',headers={'Retry-After':'900'})
    u=c.execute('SELECT id,org_id,email,role,password_hash FROM users WHERE lower(email)=lower(%s)',(normalized,)).fetchone()
    valid=False
    if u:
        try: valid=pwd.verify(u['password_hash'],body.password)
        except (VerifyMismatchError,VerificationError): valid=False
    else:
        try: pwd.verify(DUMMY_PASSWORD_HASH,body.password)
        except VerifyMismatchError: pass
    if not valid:
        c.execute('''INSERT INTO login_attempts(throttle_key,failures,blocked_until,updated_at)
          VALUES(%s,1,NULL,now()) ON CONFLICT(throttle_key) DO UPDATE SET
          failures=CASE WHEN login_attempts.updated_at < now()-interval '15 minutes' THEN 1 ELSE login_attempts.failures+1 END,
          blocked_until=CASE WHEN (CASE WHEN login_attempts.updated_at < now()-interval '15 minutes' THEN 1 ELSE login_attempts.failures+1 END)>=5 THEN now()+interval '15 minutes' ELSE NULL END,
          updated_at=now()''',(throttle_key,));c.commit()
        raise HTTPException(401,'Email or password is incorrect')
    c.execute('DELETE FROM login_attempts WHERE throttle_key=%s',(throttle_key,))
    tokens=issue_session(c,u)
    c.commit()
    return {**tokens,'user':{'email':u['email'],'role':u['role'],'org_id':u['org_id']}}
@app.get('/api/me')
def me(u=Depends(current_user)): return u

@app.post('/api/invites',status_code=201)
def create_invite(body:InviteIn,c=Depends(db),u=Depends(admin)):
    email=body.email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email): raise HTTPException(422,'A valid email address is required')
    if c.execute('SELECT 1 FROM users WHERE lower(email)=%s',(email,)).fetchone(): raise HTTPException(409,'That email already has an account')
    raw=secrets.token_urlsafe(32)
    digest=hashlib.sha256(raw.encode()).hexdigest()
    row=c.execute('''INSERT INTO invites(org_id,email,role,token_hash,invited_by,expires_at)
      VALUES(%s,%s,%s,%s,%s,now()+interval '48 hours')
      ON CONFLICT(org_id,email) DO UPDATE SET role=EXCLUDED.role,token_hash=EXCLUDED.token_hash,
      invited_by=EXCLUDED.invited_by,expires_at=EXCLUDED.expires_at,accepted_at=NULL,created_at=now()
      RETURNING email,role,expires_at''',(u['org_id'],email,body.role,digest,u['id'])).fetchone()
    c.commit()
    return {**row,'invite_token':raw,'delivery':'Copy this token and send it to the invited user through a trusted channel.'}

@app.post('/api/invites/accept',status_code=201)
def accept_invite(body:InviteAccept,c=Depends(db)):
    digest=hashlib.sha256(body.token.encode()).hexdigest()
    invite=c.execute('SELECT id,org_id,email,role FROM invites WHERE token_hash=%s AND accepted_at IS NULL AND expires_at>now() FOR UPDATE',(digest,)).fetchone()
    if not invite: raise HTTPException(400,'Invitation is invalid, expired, or already used')
    try:
        user=c.execute('INSERT INTO users(org_id,email,password_hash,role) VALUES(%s,%s,%s,%s) RETURNING id,org_id,email,role',
                       (invite['org_id'],invite['email'],pwd.hash(body.password),invite['role'])).fetchone()
        c.execute('UPDATE invites SET accepted_at=now() WHERE id=%s',(invite['id'],))
        tokens=issue_session(c,user)
        c.commit()
    except psycopg.errors.UniqueViolation:
        c.rollback(); raise HTTPException(409,'That email already has an account')
    return {'user':user,**tokens}

@app.post('/api/auth/refresh')
def refresh_session(body:RefreshIn,c=Depends(db)):
    digest=hashlib.sha256(body.refresh_token.encode()).hexdigest()
    old=c.execute('DELETE FROM user_sessions WHERE session_hash=%s AND expires_at>now() RETURNING user_id,org_id',(digest,)).fetchone()
    if not old: raise HTTPException(401,'Refresh session is expired or already used')
    user=c.execute('SELECT id,org_id,email,role FROM users WHERE id=%s AND org_id=%s',(old['user_id'],old['org_id'])).fetchone()
    if not user:
        c.rollback(); raise HTTPException(401,'Account is no longer active')
    tokens=issue_session(c,user)
    c.commit()
    return tokens

@app.post('/api/auth/logout')
def logout_session(body:RefreshIn,c=Depends(db)):
    digest=hashlib.sha256(body.refresh_token.encode()).hexdigest()
    c.execute('DELETE FROM user_sessions WHERE session_hash=%s',(digest,))
    c.commit()
    return {'revoked':True}

@app.get('/api/assets')
def assets(c=Depends(db),u=Depends(current_user)):
    rows=c.execute('''SELECT a.id,a.name,a.kind,p.lat,p.lng,p.speed,p.heading,p.recorded_at,
      CASE WHEN p.recorded_at IS NULL OR p.recorded_at < now()-interval '60 seconds' THEN 'offline' WHEN p.speed > 2 THEN 'moving' ELSE 'stopped' END status
      FROM assets a LEFT JOIN LATERAL (SELECT * FROM positions WHERE asset_id=a.id AND org_id=a.org_id ORDER BY recorded_at DESC LIMIT 1) p ON true
      WHERE a.org_id=%s ORDER BY a.name''',(u['org_id'],)).fetchall()
    return rows
@app.post('/api/assets')
def add_asset(body:AssetIn,c=Depends(db),u=Depends(admin)):
    try:
        row=c.execute('INSERT INTO assets(org_id,name,kind,device_id) VALUES(%s,%s,%s,%s) RETURNING id,name,kind,device_id',(u['org_id'],body.name.strip(),body.kind,body.device_id)).fetchone()
        result=dict(row)
        if body.device_id:
            credential=secrets.token_urlsafe(32)
            c.execute('INSERT INTO devices(org_id,asset_id,device_token_hash) VALUES(%s,%s,%s)',(u['org_id'],row['id'],hashlib.sha256(credential.encode()).hexdigest()))
            result['device_token']=credential
        c.commit()
        return result
    except psycopg.errors.UniqueViolation:
        c.rollback(); raise HTTPException(409,'That tracker ID is already registered')

@app.post('/api/assets/{asset_id}/device')
def provision_device(asset_id:int,device_id:str,c=Depends(db),u=Depends(admin)):
    device_id=device_id.strip()
    if not device_id or len(device_id)>120: raise HTTPException(422,'device_id must be 1 to 120 characters')
    asset=c.execute('SELECT id,device_id FROM assets WHERE id=%s AND org_id=%s',(asset_id,u['org_id'])).fetchone()
    if not asset: raise HTTPException(404,'Asset not found')
    if asset['device_id']: raise HTTPException(409,'Asset already has a tracker; rotate its credential instead')
    secret=secrets.token_urlsafe(32)
    try:
        c.execute('UPDATE assets SET device_id=%s WHERE id=%s AND org_id=%s',(device_id,asset_id,u['org_id']))
        c.execute('INSERT INTO devices(org_id,asset_id,device_token_hash) VALUES(%s,%s,%s)',(u['org_id'],asset_id,hashlib.sha256(secret.encode()).hexdigest()))
        c.commit()
    except psycopg.errors.UniqueViolation:
        c.rollback(); raise HTTPException(409,'That tracker ID is already registered')
    return {'device_id':device_id,'device_token':secret,'warning':'Copy this token now. It cannot be read again.'}

@app.post('/api/assets/{asset_id}/device/rotate')
def rotate_device(asset_id:int,c=Depends(db),u=Depends(admin)):
    row=c.execute('SELECT d.id,a.device_id FROM devices d JOIN assets a ON a.id=d.asset_id AND a.org_id=d.org_id WHERE d.asset_id=%s AND d.org_id=%s AND d.enabled=true',(asset_id,u['org_id'])).fetchone()
    if not row: raise HTTPException(404,'Active tracker not found')
    secret=secrets.token_urlsafe(32)
    c.execute('UPDATE devices SET device_token_hash=%s WHERE id=%s',(hashlib.sha256(secret.encode()).hexdigest(),row['id']))
    c.commit()
    return {'device_id':row['device_id'],'device_token':secret,'warning':'Copy this token now. The previous token is revoked.'}
@app.get('/api/assets/{asset_id}/positions')
def history(asset_id:int=ApiPath(gt=0),from_:Optional[datetime]=Query(None,alias='from'),to:Optional[datetime]=None,c=Depends(db),u=Depends(current_user)):
    now=datetime.now(timezone.utc)
    start=require_aware(from_,'from') if from_ else now-timedelta(days=30)
    end=require_aware(to,'to') if to else now
    if start < now-timedelta(days=30): start=now-timedelta(days=30)
    if end>now: end=now
    if end<start: raise HTTPException(422,'to must be at or after from')
    rows=c.execute('SELECT id,lat,lng,speed,heading,recorded_at FROM positions WHERE org_id=%s AND asset_id=%s AND recorded_at BETWEEN %s AND %s ORDER BY recorded_at LIMIT 10000',(u['org_id'],asset_id,start,end)).fetchall()
    return rows

@app.get('/api/geofences')
def geofences(c=Depends(db),u=Depends(current_user)):
    return c.execute('SELECT id,name,shape,geometry,enabled FROM geofences WHERE org_id=%s ORDER BY name',(u['org_id'],)).fetchall()
@app.post('/api/geofences')
def add_geofence(b:GeofenceIn,c=Depends(db),u=Depends(admin)):
    if b.shape not in ('circle','polygon'): raise HTTPException(422,'shape must be circle or polygon')
    if b.shape=='circle':
        if not all(k in b.geometry for k in ('lat','lng','radius_m')): raise HTTPException(422,'circle needs lat, lng, radius_m')
        try: lat,lng,r=float(b.geometry['lat']),float(b.geometry['lng']),float(b.geometry['radius_m'])
        except (TypeError,ValueError): raise HTTPException(422,'circle coordinates and radius must be numbers')
        if not (-90<=lat<=90 and -180<=lng<=180 and 20<=r<=100000): raise HTTPException(422,'circle coordinates or radius are out of range')
    if b.shape=='polygon':
        pts=b.geometry.get('coordinates',[])
        if len(pts)<3 or len(pts)>500: raise HTTPException(422,'polygon needs 3 to 500 [lng,lat] points')
        try: valid=all(len(p)==2 and -180<=float(p[0])<=180 and -90<=float(p[1])<=90 for p in pts)
        except (TypeError,ValueError): valid=False
        if not valid: raise HTTPException(422,'polygon points must be valid [lng,lat] coordinates')
        ring=pts if pts[0]==pts[-1] else [*pts,pts[0]]
        try:
            shape={'type':'Polygon','coordinates':[ring]}
            valid_shape=c.execute('SELECT ST_IsValid(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)) valid',(json.dumps(shape),)).fetchone()['valid']
        except psycopg.Error:
            raise HTTPException(422,'polygon geometry is invalid')
        if not valid_shape: raise HTTPException(422,'polygon must be a valid non-self-intersecting shape')
    row=c.execute('INSERT INTO geofences(org_id,name,shape,geometry) VALUES(%s,%s,%s,%s) RETURNING id,name,shape,geometry,enabled',(u['org_id'],b.name,b.shape,json.dumps(b.geometry))).fetchone();c.commit();return row
@app.delete('/api/geofences/{fid}')
def delete_geofence(fid:int,c=Depends(db),u=Depends(admin)):
    cur=c.execute('DELETE FROM geofences WHERE id=%s AND org_id=%s',(fid,u['org_id']));c.commit()
    if cur.rowcount==0: raise HTTPException(404,'Geofence not found')
    return {'deleted':True}

@app.post('/api/observations')
def add_observation(b:ObservationIn,c=Depends(db),u=Depends(current_user)):
    ts=require_aware(b.observed_at,'observed_at') if b.observed_at else datetime.now(timezone.utc)
    if ts>datetime.now(timezone.utc)+timedelta(minutes=5): raise HTTPException(422,'observed_at cannot be more than five minutes in the future')
    if b.client_id:
        existing=c.execute('''SELECT id,asset_id,author_id,client_id,lat,lng,observed_at,note,photo_ref
          FROM observations WHERE org_id=%s AND author_id=%s AND client_id=%s''',(u['org_id'],u['id'],b.client_id)).fetchone()
        if existing: return existing
    if b.asset_id and not c.execute('SELECT 1 FROM assets WHERE id=%s AND org_id=%s',(b.asset_id,u['org_id'])).fetchone(): raise HTTPException(404,'Asset not found')
    row=c.execute('''INSERT INTO observations(org_id,asset_id,author_id,client_id,lat,lng,observed_at,note)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
      ON CONFLICT(org_id,author_id,client_id) DO UPDATE SET client_id=EXCLUDED.client_id
      RETURNING id,asset_id,author_id,client_id,lat,lng,observed_at,note,photo_ref''',
      (u['org_id'],b.asset_id,u['id'],b.client_id,b.lat,b.lng,ts,b.note.strip())).fetchone();c.commit();return row
@app.get('/api/observations')
def observations(c=Depends(db),u=Depends(current_user),asset_id:Optional[int]=Query(default=None,gt=0),from_:Optional[datetime]=Query(None,alias='from'),to:Optional[datetime]=None):
    if from_: from_=require_aware(from_,'from')
    if to: to=require_aware(to,'to')
    if from_ and to and to<from_: raise HTTPException(422,'to must be at or after from')
    q='SELECT o.id,o.asset_id,o.lat,o.lng,o.observed_at,o.note,o.author_id,u.email author FROM observations o JOIN users u ON u.id=o.author_id WHERE o.org_id=%s'; args=[u['org_id']]
    if asset_id: q+=' AND o.asset_id=%s';args.append(asset_id)
    if from_: q+=' AND o.observed_at >= %s';args.append(from_)
    if to: q+=' AND o.observed_at <= %s';args.append(to)
    q+=' ORDER BY o.observed_at DESC LIMIT 2000';return c.execute(q,args).fetchall()
@app.get('/api/alerts')
def alerts(c=Depends(db),u=Depends(current_user)):
    return c.execute('SELECT id,asset_id,geofence_id,message,created_at,read_at FROM alerts WHERE org_id=%s ORDER BY created_at DESC LIMIT 100',(u['org_id'],)).fetchall()

@app.post('/api/live-ticket')
def live_ticket(c=Depends(db),u=Depends(current_user)):
    ticket=secrets.token_urlsafe(32)
    digest=hashlib.sha256(ticket.encode()).hexdigest()
    c.execute('DELETE FROM live_tickets WHERE expires_at<now()')
    c.execute('INSERT INTO live_tickets(ticket_hash,user_id,org_id,expires_at) VALUES(%s,%s,%s,now()+interval \'60 seconds\')',(digest,u['id'],u['org_id']))
    c.commit()
    return {'ticket':ticket,'expires_in':60}

@app.post('/api/ingest')
def ingest(b:PositionIn,background_tasks:BackgroundTasks,c=Depends(db)):
    d=c.execute('SELECT d.org_id,d.asset_id FROM devices d JOIN assets a ON a.id=d.asset_id AND a.org_id=d.org_id WHERE a.device_id=%s AND d.enabled=true',(b.device_id,)).fetchone()
    if not d: raise HTTPException(404,'Device not registered')
    stored=c.execute('SELECT device_token_hash FROM devices WHERE asset_id=%s AND org_id=%s',(d['asset_id'],d['org_id'])).fetchone()
    if not stored or not hmac.compare_digest(hashlib.sha256(b.token.encode()).hexdigest(),stored['device_token_hash']): raise HTTPException(401,'Invalid tracker token')
    ts=require_aware(b.recorded_at,'recorded_at') if b.recorded_at else datetime.now(timezone.utc)
    now=datetime.now(timezone.utc)
    if ts>now+timedelta(minutes=5) or ts<now-timedelta(days=30): raise HTTPException(422,'recorded_at must be within the last 30 days and no more than five minutes ahead')
    digest=hashlib.sha256(f"{b.device_id}|{ts.isoformat()}|{b.lat:.7f}|{b.lng:.7f}|{b.speed:.2f}|{b.heading}".encode()).hexdigest()
    position=c.execute('''INSERT INTO positions(org_id,asset_id,ingest_key,lat,lng,speed,heading,recorded_at)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(asset_id,ingest_key) DO NOTHING RETURNING id''',
      (d['org_id'],d['asset_id'],digest,b.lat,b.lng,b.speed,b.heading,ts)).fetchone()
    if not position:
        c.commit()
        return {'accepted':True,'duplicate':True}
    previous=c.execute('SELECT lat,lng FROM positions WHERE asset_id=%s AND id<>%s AND recorded_at<%s ORDER BY recorded_at DESC LIMIT 1',(d['asset_id'],position['id'],ts)).fetchone()
    fences=c.execute('SELECT id,name,shape,geometry FROM geofences WHERE org_id=%s AND enabled=true',(d['org_id'],)).fetchall()
    for f in fences:
        was=inside(f,previous['lat'],previous['lng']) if previous else False; is_inside=inside(f,b.lat,b.lng)
        if was!=is_inside:
            ev='enter' if is_inside else 'exit'
            e=c.execute('''INSERT INTO geofence_events(org_id,geofence_id,asset_id,position_id,event,occurred_at)
              VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id''',(d['org_id'],f['id'],d['asset_id'],position['id'],ev,ts)).fetchone()
            if e: c.execute('INSERT INTO alerts(org_id,asset_id,geofence_id,event_id,message) VALUES(%s,%s,%s,%s,%s)',(d['org_id'],d['asset_id'],f['id'],e['id'],f"{f['name']}: {ev}"))
    c.commit()
    background_tasks.add_task(hub.publish,d['org_id'],{'type':'position','asset_id':d['asset_id']})
    return {'accepted':True}

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
async def live(ws:WebSocket):
    await ws.accept(subprotocol='dottracker')
    try:
        ticket=next((value[7:] for value in ws.scope.get('subprotocols',[]) if value.startswith('ticket.')),None)
        if not ticket: raise ValueError('missing ticket')
        with psycopg.connect(DATABASE_URL,row_factory=dict_row) as c:
            row=c.execute('''DELETE FROM live_tickets WHERE ticket_hash=%s AND expires_at>now()
              RETURNING user_id,org_id''',(hashlib.sha256(ticket.encode()).hexdigest(),)).fetchone()
            c.commit()
            if not row: raise ValueError('expired or consumed ticket')
            org=int(row['org_id'])
            if not c.execute('SELECT 1 FROM users WHERE id=%s AND org_id=%s',(int(row['user_id']),org)).fetchone(): raise ValueError('inactive user')
    except Exception:
        await ws.close(code=4401);return
    await hub.send(ws,org)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: hub.remove(ws,org)

@app.post('/api/dev/seed')
def seed(c=Depends(db)):
    # One-time demo bootstrap; disabled outside explicit local development.
    if IS_PRODUCTION or os.getenv('ALLOW_DEMO_SEED','false').lower()!='true': raise HTTPException(404,'Not found')
    if c.execute('SELECT 1 FROM users LIMIT 1').fetchone(): return {'seeded':False,'message':'Database already has users'}
    org=c.execute("INSERT INTO organizations(name) VALUES('Snow Seasons Demo Fleet') RETURNING id").fetchone()['id']
    user=c.execute('INSERT INTO users(org_id,email,password_hash,role) VALUES(%s,%s,%s,%s) RETURNING id,org_id,email,role',(org,'admin@dottracker.local',pwd.hash('dottracker-demo'), 'admin')).fetchone()
    for n,device in [('Truck 01','truck-01'),('Trailer 02','trailer-02'),('Service Van','van-03')]:
        asset=c.execute('INSERT INTO assets(org_id,name,kind,device_id) VALUES(%s,%s,%s,%s) RETURNING id',(org,n,'vehicle',device)).fetchone()['id']
        c.execute('INSERT INTO devices(org_id,asset_id,device_token_hash) VALUES(%s,%s,%s)',(org,asset,hashlib.sha256(DEVICE_TOKEN.encode()).hexdigest()))
        c.execute('INSERT INTO positions(org_id,asset_id,ingest_key,lat,lng,speed,heading,recorded_at) VALUES(%s,%s,%s,%s,%s,%s,%s,now())',(org,asset,f'demo-seed-{asset}',39.7392+(asset-1)*.015,-104.9903+(asset-1)*.02,12 if asset==1 else 0,90))
    c.execute("INSERT INTO geofences(org_id,name,shape,geometry) VALUES(%s,'Denver Yard','circle',%s)",(org,json.dumps({'lat':39.7392,'lng':-104.9903,'radius_m':400})))
    c.commit();return {'seeded':True,'email':user['email'],'password':'dottracker-demo'}
