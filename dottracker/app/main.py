import asyncio, hashlib, hmac, json, os, re, secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
from uuid import UUID
from urllib.parse import parse_qs, urlsplit

import jwt
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Path as ApiPath, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from pydantic import BaseModel, ConfigDict, Field
from app.geometry import inside
from app.routing import RoutePlan, RouteRequest, sort_route

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

SUPPORTED_SCHEMA_VERSIONS=frozenset({
    '0002-hardened-mvp',
    '0003-dispatch-routes',
    '0004-dispatch-progress',
    '0005-temporary-checkpoints',
    '0006-checkpoint-spatial',
    '0007-geofence-spatial',
    '0008-safety-checklists',
    '0009-cdot-stations',
    '0010-inspection-sites',
    '0011-alert-reads',
})

# Base map: OpenFreeMap by default, any HTTPS style (e.g. a self-hosted tile server),
# or 'offline' for the built-in style that needs no outside network.
DEFAULT_MAP_STYLE='https://tiles.openfreemap.org/styles/dark'
OFFLINE_MAP_STYLE='/api/v1/map/offline-style.json'

def map_settings(style=None, extra=None):
    style=(style if style is not None else os.getenv('MAP_STYLE_URL', DEFAULT_MAP_STYLE)).strip() or DEFAULT_MAP_STYLE
    extra=extra if extra is not None else os.getenv('MAP_EXTRA_ORIGINS', '')
    origins=[]
    if style.lower()=='offline':
        style=OFFLINE_MAP_STYLE
    else:
        parsed=urlsplit(style)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
            raise RuntimeError('MAP_STYLE_URL must be offline or an https:// URL without credentials')
        origins.append(f'https://{parsed.netloc}')
        if style==DEFAULT_MAP_STYLE: origins.append('https://*.openfreemap.org')
    for item in filter(None,(value.strip() for value in extra.split(','))):
        parsed=urlsplit(item)
        if parsed.scheme!='https' or not parsed.hostname or parsed.path not in ('','/') or parsed.query or parsed.username:
            raise RuntimeError('MAP_EXTRA_ORIGINS must be a comma-separated list of https:// origins')
        origins.append(f'https://{parsed.netloc}')
    return style, list(dict.fromkeys(origins))

MAP_STYLE, MAP_ORIGINS = map_settings()

def missing_schema_versions(applied_versions):
    return SUPPORTED_SCHEMA_VERSIONS - set(applied_versions)

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
                applied_versions={row[0] for row in c.execute('SELECT version FROM schema_migrations').fetchall()}
                missing=missing_schema_versions(applied_versions)
                if missing:
                    raise RuntimeError('Database schema is incomplete; missing migrations: '+', '.join(sorted(missing)))
            break
        except psycopg.OperationalError:
            await asyncio.sleep(1)
    else: raise RuntimeError('Database unavailable')
    yield
APP_VERSION='3.5.0'
app=FastAPI(title='dotTracker API', version=APP_VERSION, lifespan=lifespan,
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
    response.headers['Content-Security-Policy']="default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; worker-src 'self' blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' wss:"+''.join(' '+origin for origin in MAP_ORIGINS)+"; font-src 'self' data: https://fonts.gstatic.com"
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
class ObservationIn(StrictModel): lat:float=Field(ge=-90,le=90); lng:float=Field(ge=-180,le=180); observed_at:Optional[datetime]=None; note:str=Field(default='',max_length=2000); asset_id:Optional[int]=Field(default=None,gt=0); client_id:Optional[UUID]=None; kind:Literal['observation','mobile_dot_checkpoint']='observation'
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

@app.post('/api/routes/optimize')
@app.post('/api/routes/reroute')
def optimize_route(body: RouteRequest, dispatcher=Depends(admin)) -> RoutePlan:
    return require_route_plan(body)


def require_route_plan(body: RouteRequest) -> RoutePlan:
    plan = sort_route(body)
    if plan is None:
        raise HTTPException(409, 'No route satisfies the equipment, travel and time-window constraints')
    return plan


def read_dispatch_route(c, asset_id: int, org_id: int):
    return c.execute("""SELECT revision, plan, COALESCE((
        SELECT jsonb_object_agg(job_id, status) FROM dispatch_progress WHERE asset_id=dispatch_routes.asset_id
        ), '{}'::jsonb) AS progress FROM dispatch_routes
        WHERE asset_id=%s AND org_id=%s""", (asset_id, org_id)).fetchone()


@app.get('/api/assets/{asset_id}/dispatch-route')
def get_dispatch_route(asset_id: int, c=Depends(db), u=Depends(current_user)):
    route = read_dispatch_route(c, asset_id, u['org_id'])
    if route is None:
        raise HTTPException(404, 'No dispatch route found')
    return route


class DispatchStatusMutation(BaseModel):
    mutation_id: UUID
    job_id: str = Field(min_length=1, max_length=80)
    status: Literal['pending', 'completed', 'skipped']
    changed_at: datetime


@app.post('/api/assets/{asset_id}/dispatch-route/status')
def update_dispatch_status(asset_id: int, body: DispatchStatusMutation, background_tasks: BackgroundTasks,
                           idempotency_key: UUID = Header(alias='Idempotency-Key'),
                           c=Depends(db), u=Depends(current_user)):
    if idempotency_key != body.mutation_id:
        raise HTTPException(422, 'Idempotency-Key must match mutation_id')
    changed_at = require_aware(body.changed_at, 'changed_at')
    if changed_at > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise HTTPException(422, 'Change timestamp is too far in the future')
    route = c.execute("""SELECT revision, plan FROM dispatch_routes
        WHERE asset_id=%s AND org_id=%s FOR UPDATE""", (asset_id, u['org_id'])).fetchone()
    if route is None:
        raise HTTPException(404, 'No dispatch route found')
    accepted = c.execute("""INSERT INTO dispatch_mutations (asset_id, mutation_id) VALUES (%s, %s)
        ON CONFLICT DO NOTHING RETURNING mutation_id""", (asset_id, body.mutation_id)).fetchone()
    if not accepted:
        return {'synced': True, 'route': read_dispatch_route(c, asset_id, u['org_id'])}
    if not any(stop['job_id'] == body.job_id for stop in route['plan']['stops']):
        raise HTTPException(409, 'Job is no longer on this route')
    updated = c.execute("""INSERT INTO dispatch_progress (asset_id, job_id, status, changed_at, mutation_id)
        VALUES (%s, %s, %s, %s, %s) ON CONFLICT (asset_id, job_id) DO UPDATE SET
        status=excluded.status, changed_at=excluded.changed_at, mutation_id=excluded.mutation_id
        WHERE (excluded.changed_at, excluded.mutation_id) >
              (dispatch_progress.changed_at, dispatch_progress.mutation_id)
        RETURNING job_id""", (asset_id, body.job_id, body.status, changed_at, body.mutation_id)).fetchone()
    if updated:
        c.execute('UPDATE dispatch_routes SET revision=revision+1 WHERE asset_id=%s', (asset_id,))
    snapshot = read_dispatch_route(c, asset_id, u['org_id'])
    c.commit()
    if updated:
        background_tasks.add_task(hub.publish, u['org_id'],
                                  {'type': 'route_updated', 'asset_id': asset_id, 'revision': snapshot['revision']})
    return {'synced': True, 'route': snapshot}


@app.put('/api/assets/{asset_id}/dispatch-route')
def publish_dispatch_route(asset_id: int, body: RouteRequest, background_tasks: BackgroundTasks,
                           c=Depends(db), u=Depends(admin)):
    if not c.execute('SELECT 1 FROM assets WHERE id=%s AND org_id=%s FOR KEY SHARE',
                     (asset_id, u['org_id'])).fetchone():
        raise HTTPException(404, 'Asset not found')
    plan = require_route_plan(body)
    route = c.execute("""INSERT INTO dispatch_routes (asset_id, org_id, plan) VALUES (%s, %s, %s)
        ON CONFLICT (asset_id) DO UPDATE SET plan=excluded.plan, revision=dispatch_routes.revision+1
        RETURNING revision, plan""", (asset_id, u['org_id'], Jsonb(plan.model_dump()))).fetchone()
    c.commit()
    background_tasks.add_task(hub.publish, u['org_id'],
                              {'type': 'route_updated', 'asset_id': asset_id, 'revision': route['revision']})
    return route


@app.get('/api/geofences')
def geofences(c=Depends(db),u=Depends(current_user)):
    return c.execute('SELECT id,name,shape,geometry,enabled FROM geofences WHERE org_id=%s ORDER BY name',(u['org_id'],)).fetchall()
@app.post('/api/geofences')
def add_geofence(b:GeofenceIn,background_tasks:BackgroundTasks,c=Depends(db),u=Depends(admin)):
    row = save_geofence(b, c, u['org_id'])
    publish_geofence_change(background_tasks, u['org_id'], row['id'])
    return row


class PolygonGeofenceIn(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    coordinates: list[tuple[float, float]] = Field(min_length=3, max_length=501)


@app.post('/api/v1/geofences', status_code=201)
def add_polygon_geofence(body: PolygonGeofenceIn, background_tasks: BackgroundTasks, c=Depends(db), u=Depends(admin)):
    geometry = {'coordinates': [list(point) for point in body.coordinates]}
    row = save_geofence(GeofenceIn(name=body.name, shape='polygon', geometry=geometry), c, u['org_id'])
    publish_geofence_change(background_tasks, u['org_id'], row['id'])
    return row


def publish_geofence_change(background_tasks: BackgroundTasks, org_id: int, fence_id: int):
    # Open maps in the workspace refresh their geofence layer.
    background_tasks.add_task(hub.publish, org_id, {'type': 'geofence_changed', 'data': {'id': fence_id}})


def save_geofence(b: GeofenceIn, c, org_id: int):
    if b.shape not in ('circle','polygon'): raise HTTPException(422,'shape must be circle or polygon')
    if b.shape=='circle':
        if not all(k in b.geometry for k in ('lat','lng','radius_m')): raise HTTPException(422,'circle needs lat, lng, radius_m')
        try: lat,lng,r=float(b.geometry['lat']),float(b.geometry['lng']),float(b.geometry['radius_m'])
        except (TypeError,ValueError): raise HTTPException(422,'circle coordinates and radius must be numbers')
        if not (-90<=lat<=90 and -180<=lng<=180 and 20<=r<=100000): raise HTTPException(422,'circle coordinates or radius are out of range')
    if b.shape=='polygon':
        pts = b.geometry.get('coordinates')
        if not isinstance(pts, list):
            raise HTTPException(422, 'polygon needs 3 to 500 [lng,lat] vertices')
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if not 3 <= len(pts) <= 500:
            raise HTTPException(422, 'polygon needs 3 to 500 [lng,lat] vertices')
        try:
            if any(not isinstance(point, (tuple, list)) for point in pts):
                raise ValueError
            pts = [[float(lng), float(lat)] for lng, lat in pts]
        except (TypeError, ValueError):
            raise HTTPException(422, 'polygon points must be valid [lng,lat] coordinates')
        if not all(-180 <= lng <= 180 and -90 <= lat <= 90 for lng, lat in pts):
            raise HTTPException(422, 'polygon points must be valid [lng,lat] coordinates')
        valid = c.execute('SELECT geofence_polygon(%s) IS NOT NULL AS valid', (Jsonb(pts),)).fetchone()['valid']
        if not valid:
            raise HTTPException(422, 'polygon must be a valid non-self-intersecting shape')
        b.geometry['coordinates'] = pts
    row=c.execute('INSERT INTO geofences(org_id,name,shape,geometry) VALUES(%s,%s,%s,%s) RETURNING id,name,shape,geometry,enabled',(org_id,b.name,b.shape,json.dumps(b.geometry))).fetchone();c.commit();return row
class GeofenceUpdate(StrictModel):
    enabled: bool


@app.patch('/api/geofences/{fid}')
def update_geofence(body: GeofenceUpdate, background_tasks: BackgroundTasks, fid: int = ApiPath(gt=0), c=Depends(db), u=Depends(admin)):
    row = c.execute('UPDATE geofences SET enabled=%s WHERE id=%s AND org_id=%s RETURNING id,name,shape,geometry,enabled',
                    (body.enabled, fid, u['org_id'])).fetchone()
    if not row: raise HTTPException(404, 'Geofence not found')
    c.commit()
    publish_geofence_change(background_tasks, u['org_id'], fid)
    return row


@app.delete('/api/geofences/{fid}')
def delete_geofence(fid:int,background_tasks:BackgroundTasks,c=Depends(db),u=Depends(admin)):
    cur=c.execute('DELETE FROM geofences WHERE id=%s AND org_id=%s',(fid,u['org_id']));c.commit()
    if cur.rowcount==0: raise HTTPException(404,'Geofence not found')
    publish_geofence_change(background_tasks, u['org_id'], fid)
    return {'deleted':True}

@app.post('/api/observations')
def add_observation(b:ObservationIn, background_tasks:BackgroundTasks, c=Depends(db),u=Depends(current_user)):
    ts=require_aware(b.observed_at,'observed_at') if b.observed_at else datetime.now(timezone.utc)
    if ts>datetime.now(timezone.utc)+timedelta(minutes=5): raise HTTPException(422,'observed_at cannot be more than five minutes in the future')
    if b.client_id:
        existing=c.execute('''SELECT id,asset_id,author_id,client_id,lat,lng,observed_at,note,photo_ref,kind,expires_at
          FROM observations WHERE org_id=%s AND author_id=%s AND client_id=%s''',(u['org_id'],u['id'],b.client_id)).fetchone()
        if existing: return existing
    if b.asset_id and not c.execute('SELECT 1 FROM assets WHERE id=%s AND org_id=%s',(b.asset_id,u['org_id'])).fetchone(): raise HTTPException(404,'Asset not found')
    expires_at = ts + timedelta(hours=2) if b.kind == 'mobile_dot_checkpoint' else None
    row=c.execute("""INSERT INTO observations(org_id,asset_id,author_id,client_id,lat,lng,observed_at,note,kind,expires_at)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
      ON CONFLICT(org_id,author_id,client_id) DO NOTHING
      RETURNING id,asset_id,author_id,client_id,lat,lng,observed_at,note,photo_ref,kind,expires_at""",
      (u['org_id'],b.asset_id,u['id'],b.client_id,b.lat,b.lng,ts,b.note.strip(),b.kind,expires_at)).fetchone()
    if row is None:
        return c.execute("""SELECT id,asset_id,author_id,client_id,lat,lng,observed_at,note,photo_ref,kind,expires_at
          FROM observations WHERE org_id=%s AND author_id=%s AND client_id=%s""",
          (u['org_id'],u['id'],b.client_id)).fetchone()
    c.commit()
    if expires_at and expires_at > datetime.now(timezone.utc):
        background_tasks.add_task(hub.publish, u['org_id'], {
            'type': 'checkpoint_created',
            'data': {'id': row['id'], 'latitude': row['lat'], 'longitude': row['lng'],
                     'expires_at': expires_at.isoformat()}
        })
    return row


@app.get('/api/v1/checkpoints')
def checkpoints(west: float = Query(ge=-180, le=180), south: float = Query(ge=-90, le=90),
                east: float = Query(ge=-180, le=180), north: float = Query(ge=-90, le=90),
                c=Depends(db), u=Depends(current_user)):
    if west >= east or south >= north:
        raise HTTPException(422, 'Bounding box must have west < east and south < north')
    return c.execute("""SELECT id, lat, lng, kind, note, observed_at, expires_at
        FROM observations WHERE org_id=%s AND kind='mobile_dot_checkpoint' AND expires_at>now()
        AND geom && ST_MakeEnvelope(%s,%s,%s,%s,4326)
        ORDER BY observed_at DESC LIMIT 2000""", (u['org_id'], west, south, east, north)).fetchall()


# --- v3: CDOT stations (shared public reference data, read-only for every signed-in user) ---
CDOT_STATION_COLUMNS = ('id, kind, name, highway, route, milepost::float AS milepost, direction, phone, status, lat, lng')
MILEPOINT_SEARCH_METERS = 2000


@app.get('/api/v1/cdot/stations')
def cdot_stations(status: Optional[Literal['open', 'planned']] = None, c=Depends(db), u=Depends(current_user)):
    return c.execute(f"""SELECT {CDOT_STATION_COLUMNS} FROM cdot_stations
        WHERE (%(status)s::text IS NULL OR status=%(status)s) ORDER BY name, direction""",
        {'status': status}).fetchall()


@app.get('/api/v1/cdot/summary')
def cdot_summary(c=Depends(db), u=Depends(current_user)):
    datasets = c.execute('SELECT name, row_count, loaded_at FROM cdot_datasets ORDER BY name').fetchall()
    return {'loaded': bool(datasets), 'datasets': datasets}


@app.get('/api/v1/cdot/locate')
def cdot_locate(lat: float = Query(ge=-90, le=90, allow_inf_nan=False), lng: float = Query(ge=-180, le=180, allow_inf_nan=False),
                c=Depends(db), u=Depends(current_user)):
    """Nearest highway milepoint (within 2 km), its maintenance patrol, and the nearest port of entry."""
    here = {'lat': lat, 'lng': lng, 'radius': MILEPOINT_SEARCH_METERS}
    milepoint = c.execute("""WITH here AS (SELECT ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326) AS g)
        SELECT m.route, m.refpt::float AS refpt,
               round(ST_Distance(m.geom::geography, here.g::geography))::int AS distance_m
        FROM cdot_milepoints m, here
        WHERE ST_DWithin(m.geom::geography, here.g::geography, %(radius)s)
        ORDER BY m.geom <-> here.g LIMIT 1""", here).fetchone()
    patrol = None
    if milepoint:
        patrol = c.execute("""SELECT route, region, begin_refpt::float AS begin_refpt, end_refpt::float AS end_refpt,
                   maintenance_section, patrol, supervisor_area
            FROM cdot_patrols WHERE route=%s AND %s BETWEEN begin_refpt AND end_refpt
            ORDER BY end_refpt - begin_refpt LIMIT 1""", (milepoint['route'], milepoint['refpt'])).fetchone()
    station = c.execute(f"""WITH here AS (SELECT ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326) AS g)
        SELECT {CDOT_STATION_COLUMNS}, round(ST_Distance(s.geom::geography, here.g::geography))::int AS distance_m
        FROM cdot_stations s, here ORDER BY s.geom <-> here.g LIMIT 1""", here).fetchone()
    return {'milepoint': milepoint, 'patrol': patrol, 'nearest_station': station}


# --- v3.5: built-in offline base map (Colorado outline + CDOT highways from milepoints) ---
INTERSTATE_ROUTES = ['025A', '070A', '076A', '225A', '270A']


@app.get('/api/v1/map/offline-style.json')
def offline_map_style():
    # Static style; the highway lines it references need a signed-in user.
    colorado = [[-109.05, 37.0], [-109.05, 41.0], [-102.04, 41.0], [-102.04, 37.0], [-109.05, 37.0]]
    return {
        'version': 8, 'name': 'dotTracker offline',
        'sources': {
            'colorado': {'type': 'geojson', 'data': {'type': 'Feature', 'properties': {}, 'geometry': {'type': 'Polygon', 'coordinates': [colorado]}}},
            'highways': {'type': 'geojson', 'data': '/api/v1/map/highways.geojson'},
        },
        'layers': [
            {'id': 'background', 'type': 'background', 'paint': {'background-color': '#0d1620'}},
            {'id': 'colorado-fill', 'type': 'fill', 'source': 'colorado', 'paint': {'fill-color': '#111d29'}},
            {'id': 'colorado-outline', 'type': 'line', 'source': 'colorado', 'paint': {'line-color': '#2c4154', 'line-width': 1.5}},
            {'id': 'highways', 'type': 'line', 'source': 'highways', 'layout': {'line-join': 'round', 'line-cap': 'round'},
             'paint': {'line-color': ['match', ['get', 'route'], INTERSTATE_ROUTES, '#5d7f99', '#3b5468'],
                       'line-width': ['interpolate', ['linear'], ['zoom'], 6, ['match', ['get', 'route'], INTERSTATE_ROUTES, 1.6, 0.8],
                                      12, ['match', ['get', 'route'], INTERSTATE_ROUTES, 5, 2.5]]}},
        ],
    }


@app.get('/api/v1/map/highways.geojson')
def offline_highways(c=Depends(db), u=Depends(current_user)):
    # Join each route's milepoints in order; a gap over 1.5 miles starts a new line
    # so discontinuous routes are not drawn across the map.
    row = c.execute('''WITH m AS (
            SELECT route, refpt, geom,
                   CASE WHEN refpt - lag(refpt) OVER (PARTITION BY route ORDER BY refpt) > 1.5 THEN 1 ELSE 0 END AS brk
            FROM cdot_milepoints),
        g AS (SELECT route, refpt, geom, sum(brk) OVER (PARTITION BY route ORDER BY refpt) AS seg FROM m),
        l AS (SELECT route, ST_MakeLine(geom ORDER BY refpt) AS line FROM g GROUP BY route, seg HAVING count(*) > 1)
        SELECT json_build_object('type', 'FeatureCollection', 'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature', 'properties', json_build_object('route', route),
            'geometry', ST_AsGeoJSON(line, 5)::json)), '[]'::json)) AS fc FROM l''').fetchone()
    return row['fc']


# --- v3.1: non-permanent safety inspection stations (org-scoped; admins place and end them) ---
INSPECTION_SITE_COLUMNS = ("""id, name, agency, highway, milepost::float AS milepost, direction, notes, lat, lng,
    starts_at, ends_at, CASE WHEN starts_at > clock_timestamp() THEN 'scheduled' ELSE 'active' END AS status,
    created_at AS placed_at, (SELECT email FROM users WHERE users.id = inspection_sites.created_by) AS placed_by""")
MAX_INSPECTION_WINDOW = timedelta(days=30)


class InspectionSiteIn(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    agency: str = Field(min_length=1, max_length=80)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    highway: Optional[str] = Field(default=None, max_length=40)
    milepost: Optional[float] = Field(default=None, ge=0, le=9999)
    direction: Optional[str] = Field(default=None, max_length=20)
    notes: str = Field(default='', max_length=1000)
    starts_at: Optional[datetime] = None
    ends_at: datetime


@app.get('/api/v1/inspection-sites')
def inspection_sites(c=Depends(db), u=Depends(current_user)):
    return c.execute(f"""SELECT {INSPECTION_SITE_COLUMNS} FROM inspection_sites
        WHERE org_id=%s AND ended_at IS NULL AND ends_at > now()
        ORDER BY starts_at, id LIMIT 500""", (u['org_id'],)).fetchall()


@app.post('/api/v1/inspection-sites', status_code=201)
def add_inspection_site(body: InspectionSiteIn, background_tasks: BackgroundTasks, c=Depends(db), u=Depends(admin)):
    now = datetime.now(timezone.utc)
    starts = require_aware(body.starts_at, 'starts_at') if body.starts_at else now
    ends = require_aware(body.ends_at, 'ends_at')
    if ends <= starts: raise HTTPException(422, 'ends_at must be after starts_at')
    if ends <= now: raise HTTPException(422, 'ends_at must be in the future')
    if ends - starts > MAX_INSPECTION_WINDOW: raise HTTPException(422, 'A non-permanent site can stay up for at most 30 days')
    if starts > now + MAX_INSPECTION_WINDOW: raise HTTPException(422, 'starts_at can be at most 30 days ahead')
    optional = lambda value: (value or '').strip() or None
    row = c.execute(f"""INSERT INTO inspection_sites
        (org_id,name,agency,highway,milepost,direction,notes,lat,lng,starts_at,ends_at,created_by)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {INSPECTION_SITE_COLUMNS}""",
        (u['org_id'], body.name, body.agency, optional(body.highway), body.milepost, optional(body.direction),
         body.notes.strip(), body.lat, body.lng, starts, ends, u['id'])).fetchone()
    c.commit()
    background_tasks.add_task(hub.publish, u['org_id'], {'type': 'inspection_site_changed', 'data': {'id': row['id']}})
    return row


@app.post('/api/v1/inspection-sites/{site_id}/end')
def end_inspection_site(background_tasks: BackgroundTasks, site_id: int = ApiPath(gt=0), c=Depends(db), u=Depends(admin)):
    row = c.execute("""UPDATE inspection_sites SET ended_at=now()
        WHERE id=%s AND org_id=%s AND ended_at IS NULL AND ends_at > now() RETURNING id""",
        (site_id, u['org_id'])).fetchone()
    if not row: raise HTTPException(404, 'Inspection site not found or already ended')
    c.commit()
    background_tasks.add_task(hub.publish, u['org_id'], {'type': 'inspection_site_changed', 'data': {'id': site_id}})
    return {'id': site_id, 'ended': True}


@app.get('/api/observations')
def observations(c=Depends(db),u=Depends(current_user),asset_id:Optional[int]=Query(default=None,gt=0),from_:Optional[datetime]=Query(None,alias='from'),to:Optional[datetime]=None):
    if from_: from_=require_aware(from_,'from')
    if to: to=require_aware(to,'to')
    if from_ and to and to<from_: raise HTTPException(422,'to must be at or after from')
    q='SELECT o.id,o.client_id,o.kind,o.expires_at,o.asset_id,o.lat,o.lng,o.observed_at,o.note,o.author_id,u.email author FROM observations o JOIN users u ON u.id=o.author_id WHERE o.org_id=%s AND (o.expires_at IS NULL OR o.expires_at > now())'; args=[u['org_id']]
    if asset_id: q+=' AND o.asset_id=%s';args.append(asset_id)
    if from_: q+=' AND o.observed_at >= %s';args.append(from_)
    if to: q+=' AND o.observed_at <= %s';args.append(to)
    q+=' ORDER BY o.observed_at DESC LIMIT 2000';return c.execute(q,args).fetchall()
SAFETY_TEMPLATE = json.loads((Path(__file__).parent / 'static' / 'safety-v1.json').read_text())


class SafetySubmission(StrictModel):
    submission_id: UUID
    template_version: Literal[1]
    completed_at: datetime
    answers: dict[str, Literal['checked', 'concern', 'not_applicable']]
    notes: str = Field(default='', max_length=2000)


@app.post('/api/safety/submissions')
def submit_safety_checklist(body: SafetySubmission, c=Depends(db), u=Depends(current_user)):
    completed = require_aware(body.completed_at, 'completed_at')
    if completed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise HTTPException(422, 'Completion timestamp is too far in the future')
    if body.answers.keys() != SAFETY_TEMPLATE['items'].keys():
        raise HTTPException(422, 'Answer every checklist item')
    if 'concern' in body.answers.values() and not body.notes.strip():
        raise HTTPException(422, 'Add a note for any concern')
    c.execute("""INSERT INTO safety_checklists
        (org_id, author_id, submission_id, template_version, completed_at, answers, notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (u['org_id'], u['id'], body.submission_id, body.template_version, completed,
         Jsonb(body.answers), body.notes.strip()))
    c.commit()
    return {'synced': True}


@app.get('/api/safety/submissions')
def safety_history(c=Depends(db), u=Depends(admin)):
    return c.execute("""SELECT s.submission_id, s.template_version, s.completed_at, s.received_at,
        s.answers, s.notes, u.email AS author FROM safety_checklists s
        JOIN users u ON (u.id,u.org_id)=(s.author_id,s.org_id)
        WHERE s.org_id=%s ORDER BY s.received_at DESC LIMIT 100""", (u['org_id'],)).fetchall()


@app.get('/api/alerts')
def alerts(c=Depends(db),u=Depends(current_user)):
    # lat/lng/occurred_at come from the fix that crossed the fence, so alerts can be placed on the map.
    # read_at is the signed-in person's own read time for each alert.
    return c.execute('''SELECT a.id, a.asset_id, a.geofence_id, a.message, a.created_at, r.read_at,
            s.name AS asset_name, e.event, e.occurred_at, p.lat, p.lng
        FROM alerts a
        LEFT JOIN alert_reads r ON r.alert_id = a.id AND r.user_id = %(user)s
        LEFT JOIN assets s ON s.id = a.asset_id AND s.org_id = a.org_id
        LEFT JOIN geofence_events e ON e.id = a.event_id AND e.org_id = a.org_id
        LEFT JOIN positions p ON p.id = e.position_id AND p.org_id = a.org_id
        WHERE a.org_id=%(org)s ORDER BY a.created_at DESC LIMIT 100''',{'org':u['org_id'],'user':u['id']}).fetchall()

class AlertsRead(StrictModel):
    ids: Optional[list[int]] = Field(default=None, min_length=1, max_length=500)


@app.post('/api/alerts/read')
def mark_alerts_read(body: AlertsRead, c=Depends(db), u=Depends(current_user)):
    # Marks alerts read for the signed-in person only. Without ids, every alert in
    # the workspace that this person has not read yet.
    cur = c.execute('''INSERT INTO alert_reads (alert_id, user_id, org_id)
        SELECT a.id, %(user)s, %(org)s FROM alerts a
        WHERE a.org_id = %(org)s AND (%(ids)s::bigint[] IS NULL OR a.id = ANY(%(ids)s::bigint[]))
        ON CONFLICT DO NOTHING''', {'user': u['id'], 'org': u['org_id'], 'ids': body.ids})
    c.commit()
    return {'marked_read': cur.rowcount}


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
        while True:
            if await ws.receive_text() == 'sync':
                await ws.send_json({'type': 'sync_ready'})
    except WebSocketDisconnect: hub.remove(ws,org)

def demo_seed_enabled(app_env=APP_ENV, setting=None):
    enabled = os.getenv('ALLOW_DEMO_SEED', 'false') if setting is None else setting
    return app_env.lower() != 'production' and enabled.lower() == 'true'

@app.get('/api/config')
def client_config():
    return {'demo_seed_enabled': demo_seed_enabled(), 'version': APP_VERSION, 'map_style': MAP_STYLE}

@app.post('/api/dev/seed')
def seed(c=Depends(db)):
    # One-time demo bootstrap; disabled outside explicit local development.
    if not demo_seed_enabled(): raise HTTPException(404,'Not found')
    if c.execute('SELECT 1 FROM users LIMIT 1').fetchone(): return {'seeded':False,'message':'Database already has users'}
    org=c.execute("INSERT INTO organizations(name) VALUES('Snow Seasons Demo Fleet') RETURNING id").fetchone()['id']
    user=c.execute('INSERT INTO users(org_id,email,password_hash,role) VALUES(%s,%s,%s,%s) RETURNING id,org_id,email,role',(org,'admin@dottracker.local',pwd.hash('dottracker-demo'), 'admin')).fetchone()
    for n,device in [('Truck 01','truck-01'),('Trailer 02','trailer-02'),('Service Van','van-03')]:
        asset=c.execute('INSERT INTO assets(org_id,name,kind,device_id) VALUES(%s,%s,%s,%s) RETURNING id',(org,n,'vehicle',device)).fetchone()['id']
        c.execute('INSERT INTO devices(org_id,asset_id,device_token_hash) VALUES(%s,%s,%s)',(org,asset,hashlib.sha256(DEVICE_TOKEN.encode()).hexdigest()))
        c.execute('INSERT INTO positions(org_id,asset_id,ingest_key,lat,lng,speed,heading,recorded_at) VALUES(%s,%s,%s,%s,%s,%s,%s,now())',(org,asset,f'demo-seed-{asset}',39.7392+(asset-1)*.015,-104.9903+(asset-1)*.02,12 if asset==1 else 0,90))
    c.execute("INSERT INTO geofences(org_id,name,shape,geometry) VALUES(%s,'Denver Yard','circle',%s)",(org,json.dumps({'lat':39.7392,'lng':-104.9903,'radius_m':400})))
    c.commit();return {'seeded':True,'email':user['email'],'password':'dottracker-demo'}
