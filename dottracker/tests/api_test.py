"""PostgreSQL/PostGIS-backed security and API integration tests (CI service required)."""
import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('ALLOW_DEMO_SEED', 'true')
os.environ.setdefault('JWT_SECRET', 'integration-test-secret-not-for-deployment-123456')
os.environ.setdefault('DEVICE_TOKEN', 'demo-device-token')
os.environ.setdefault('DATABASE_URL', 'postgresql://dottracker:dottracker_dev@localhost:5432/dottracker')

from uuid import uuid4 as _uuid4  # noqa: E402

RUN = _uuid4().hex[:8]  # test-created accounts get a per-run suffix so the suite can rerun on one database

from app.main import map_settings, app, pwd, SUPPORTED_SCHEMA_VERSIONS, missing_schema_versions, demo_seed_enabled  # noqa: E402


@pytest.fixture(scope='session')
def client():
    with TestClient(app) as test_client:
        test_client.post('/api/dev/seed')
        yield test_client


def login(client, email='admin@dottracker.local', password='dottracker-demo'):
    response = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert response.status_code == 200, response.text
    return {'Authorization': f"Bearer {response.json()['access_token']}"}


def create_second_org():
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO organizations(name) VALUES('Tenant Isolation Test') RETURNING id")
            org_id = cur.fetchone()[0]
            cur.execute('INSERT INTO users(org_id,email,password_hash,role) VALUES(%s,%s,%s,%s)',
                        (org_id, f'viewer-isolation-{RUN}@test.invalid', pwd.hash('test-viewer-password-long'), 'viewer'))
            cur.execute("INSERT INTO assets(org_id,name,kind) VALUES(%s,'Tenant B Only','vehicle')", (org_id,))
    return org_id


def new_tracker(client, headers, label):
    """A fresh asset with its own tracker, so each test's fixes are in time order."""
    asset = client.post('/api/assets', headers=headers, json={'name': f'{label} {RUN}', 'device_id': f'{label}-{RUN}'}).json()
    def send(lat, lng, seconds_ago):
        response = client.post('/api/ingest', json={'device_id': asset['device_id'], 'token': asset['device_token'], 'lat': lat, 'lng': lng,
            'speed': 5, 'recorded_at': (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()})
        assert response.status_code == 200, response.text
        return response.json()
    return asset, send


def test_schema_gate_requires_every_supported_migration():
    assert missing_schema_versions(SUPPORTED_SCHEMA_VERSIONS) == set()
    prior_schema = {'0002-hardened-mvp'}
    assert missing_schema_versions(prior_schema) == SUPPORTED_SCHEMA_VERSIONS - prior_schema


def test_demo_seed_is_hidden_in_production(client):
    assert client.get('/api/config').json() == {'demo_seed_enabled': True, 'version': '3.6.0', 'map_style': 'https://tiles.openfreemap.org/styles/dark'}
    assert demo_seed_enabled('development', 'true') is True
    assert demo_seed_enabled('development', 'false') is False
    assert demo_seed_enabled('production', 'true') is False


def test_readiness_and_security_headers(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']
    # MapLibre is served by the app itself: no third-party script, style or worker source is allowed.
    csp = response.headers['content-security-policy']
    assert "script-src 'self';" in csp and "worker-src 'self' blob:;" in csp and 'unpkg' not in csp
    for name, kind in [('maplibre-gl.mjs', 'javascript'), ('maplibre-gl-worker.mjs', 'javascript'), ('maplibre-gl.css', 'css')]:
        asset = client.get(f'/static/vendor/maplibre-gl-6.16.0/{name}')
        assert asset.status_code == 200 and kind in asset.headers['content-type'], name


def test_org_scope_and_viewer_permissions(client):
    org_b = create_second_org()
    admin_headers = login(client)
    asset_a = client.post('/api/assets', headers=admin_headers, json={'name': 'Tenant A Private Asset'}).json()['id']
    viewer_headers = login(client, f'viewer-isolation-{RUN}@test.invalid', 'test-viewer-password-long')
    visible = client.get('/api/assets', headers=viewer_headers)
    assert visible.status_code == 200
    assert all(row['name'] == 'Tenant B Only' for row in visible.json())
    assert all(row['name'] != 'Tenant A Private Asset' for row in visible.json())
    denied = client.post('/api/assets', headers=viewer_headers, json={'name': 'Unauthorized Write'})
    assert denied.status_code == 403
    cross_tenant_note = client.post('/api/observations', headers=viewer_headers,
                                    json={'lat': 39.7, 'lng': -105, 'asset_id': asset_a})
    assert cross_tenant_note.status_code == 404
    route = {'jobs': [{'id': 'pruning'}], 'travel_minutes': [[0, 12], [None, 0]]}
    assert client.put(f'/api/assets/{asset_a}/dispatch-route', headers=admin_headers, json=route).status_code == 200
    assert client.get(f'/api/assets/{asset_a}/dispatch-route', headers=viewer_headers).status_code == 404
    assert client.put(f'/api/assets/{asset_a}/dispatch-route', headers=viewer_headers, json=route).status_code == 403
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        assert conn.execute('SELECT org_id FROM users WHERE email=%s', (f'viewer-isolation-{RUN}@test.invalid',)).fetchone()[0] == org_b


def test_tracker_auth_idempotency_and_geofence_alerts(client):
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        asset_id = conn.execute("SELECT id FROM assets WHERE device_id='truck-01'").fetchone()[0]
        # Keep both fixes after the seed position and within the history window.
        conn.execute("UPDATE positions SET recorded_at = now() - interval '1 minute' WHERE asset_id=%s",
                     (asset_id,))
    headers = {'Content-Type': 'application/json'}
    now = datetime.now(timezone.utc) - timedelta(seconds=10)
    fix = {'device_id': 'truck-01', 'token': 'wrong-device-credential', 'lat': 39.75,
           'lng': -104.9903, 'speed': 0, 'recorded_at': now.isoformat()}
    assert client.post('/api/ingest', headers=headers, json=fix).status_code == 401
    fix['token'] = 'demo-device-token'
    fix['recorded_at'] = (now + timedelta(seconds=1)).isoformat()
    first = client.post('/api/ingest', headers=headers, json=fix)
    assert first.status_code == 200 and first.json()['accepted'] is True
    duplicate = client.post('/api/ingest', headers=headers, json=fix)
    assert duplicate.status_code == 200 and duplicate.json()['duplicate'] is True
    fix['lat'] = 39.7392
    fix['recorded_at'] = (now + timedelta(seconds=2)).isoformat()
    assert client.post('/api/ingest', headers=headers, json=fix).status_code == 200
    owner = login(client)
    alert_messages = [a['message'] for a in client.get('/api/alerts', headers=owner).json()]
    assert any('exit' in m for m in alert_messages)
    assert any('enter' in m for m in alert_messages)
    history = client.get(f'/api/assets/{asset_id}/positions', headers=owner)
    assert history.status_code == 200 and len(history.json()) >= 3


def test_timezone_required_and_authentication_errors(client):
    invalid = client.post('/api/auth/login', json={'email': 'admin@dottracker.local', 'password': 'bad'})
    assert invalid.status_code == 401
    headers = login(client)
    missing_tz = client.post('/api/observations', headers=headers,
                             json={'lat': 39.7, 'lng': -105, 'observed_at': '2026-11-03T12:00:00'})
    assert missing_tz.status_code == 422
    assert client.get('/api/assets').status_code == 401


def test_invitation_is_single_use_and_respects_role(client):
    headers = login(client)
    invited = client.post('/api/invites', headers=headers,
                          json={'email': f'New.Viewer-{RUN}@test.invalid', 'role': 'viewer'})
    assert invited.status_code == 201
    payload = invited.json()
    assert payload['email'] == f'new.viewer-{RUN}@test.invalid'
    accepted = client.post('/api/invites/accept', json={
        'token': payload['invite_token'], 'password': 'new-viewer-password-long'
    })
    assert accepted.status_code == 201
    replay = client.post('/api/invites/accept', json={
        'token': payload['invite_token'], 'password': 'new-viewer-password-long'
    })
    assert replay.status_code == 400
    viewer_headers = {'Authorization': f"Bearer {accepted.json()['access_token']}"}
    assert client.post('/api/assets', headers=viewer_headers,
                       json={'name': 'Should not create'}).status_code == 403


def test_invitation_authorization_replacement_and_expiry(client):
    headers = login(client)
    # Duplicate of an existing account is rejected before a token is issued.
    duplicate = client.post('/api/invites', headers=headers,
                            json={'email': 'admin@dottracker.local', 'role': 'viewer'})
    assert duplicate.status_code == 409

    # A replacement invitation for the same email invalidates the earlier link.
    first = client.post('/api/invites', headers=headers,
                        json={'email': f'replaced.invite-{RUN}@test.invalid', 'role': 'viewer'})
    assert first.status_code == 201
    second = client.post('/api/invites', headers=headers,
                         json={'email': f'replaced.invite-{RUN}@test.invalid', 'role': 'admin'})
    assert second.status_code == 201
    stale = client.post('/api/invites/accept', json={
        'token': first.json()['invite_token'], 'password': 'replacement-password-long'})
    assert stale.status_code == 400
    current = client.post('/api/invites/accept', json={
        'token': second.json()['invite_token'], 'password': 'replacement-password-long'})
    assert current.status_code == 201
    admin_headers = {'Authorization': f"Bearer {current.json()['access_token']}"}
    assert client.get('/api/me', headers=admin_headers).json()['role'] == 'admin'

    # Only administrators may create invitations.
    viewer = client.post('/api/invites', headers=headers,
                         json={'email': f'viewer.cannot.invite-{RUN}@test.invalid', 'role': 'viewer'})
    viewer_token = client.post('/api/invites/accept', json={
        'token': viewer.json()['invite_token'], 'password': 'viewer-cannot-invite-long'}).json()['access_token']
    viewer_headers = {'Authorization': f'Bearer {viewer_token}'}
    assert client.post('/api/invites', headers=viewer_headers,
                       json={'email': f'escalation-{RUN}@test.invalid', 'role': 'admin'}).status_code == 403

    # An expired invitation is refused even when its token is otherwise correct.
    expiring = client.post('/api/invites', headers=headers,
                           json={'email': f'expired.invite-{RUN}@test.invalid', 'role': 'viewer'})
    assert expiring.status_code == 201
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        conn.execute("UPDATE invites SET expires_at = now() - interval '1 minute' WHERE email=%s",
                     (f'expired.invite-{RUN}@test.invalid',))
        conn.commit()
    expired = client.post('/api/invites/accept', json={
        'token': expiring.json()['invite_token'], 'password': 'expired-invite-password-long'})
    assert expired.status_code == 400


def test_refresh_rotation_and_logout_revoke_session(client):
    initial = client.post('/api/auth/login', json={
        'email': 'admin@dottracker.local', 'password': 'dottracker-demo'
    }).json()
    rotated = client.post('/api/auth/refresh', json={'refresh_token': initial['refresh_token']})
    assert rotated.status_code == 200
    assert rotated.json()['refresh_token'] != initial['refresh_token']
    replay = client.post('/api/auth/refresh', json={'refresh_token': initial['refresh_token']})
    assert replay.status_code == 401
    revoked = client.post('/api/auth/logout', json={'refresh_token': rotated.json()['refresh_token']})
    assert revoked.status_code == 200
    after_logout = client.post('/api/auth/refresh', json={'refresh_token': rotated.json()['refresh_token']})
    assert after_logout.status_code == 401


def test_websocket_ticket_is_short_lived_and_single_use(client):
    headers = login(client)
    ticket = client.post('/api/live-ticket', headers=headers)
    assert ticket.status_code == 200
    value = ticket.json()['ticket']
    with client.websocket_connect("/api/live", subprotocols=["dottracker", f"ticket.{value}"]) as socket:
        socket.send_text('heartbeat')
    with client.websocket_connect("/api/live", subprotocols=["dottracker", f"ticket.{value}"]) as socket:
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            socket.receive_text()


def test_observation_retry_is_idempotent(client):
    import uuid
    headers = login(client)
    record = {
        'client_id': str(uuid.uuid4()), 'lat': 39.7392, 'lng': -104.9903,
        'note': 'Offline note preserved', 'observed_at': datetime.now(timezone.utc).isoformat()
    }
    first = client.post('/api/observations', headers=headers, json=record)
    second = client.post('/api/observations', headers=headers, json=record)
    assert first.status_code == second.status_code == 200
    assert first.json()['id'] == second.json()['id']


def test_dispatch_route_updates_and_reconnect_catchup(client):
    headers = login(client)
    asset = client.post('/api/assets', headers=headers, json={'name': 'Dispatch Sync Test'})
    assert asset.status_code == 200
    asset_id = asset.json()['id']
    path = f'/api/assets/{asset_id}/dispatch-route'
    payload = {'jobs': [{'id': 'job-1'}], 'travel_minutes': [[0, 12], [None, 0]]}
    ticket = client.post('/api/live-ticket', headers=headers).json()['ticket']
    with client.websocket_connect('/api/live', subprotocols=['dottracker', f'ticket.{ticket}']) as socket:
        socket.send_text('sync')
        assert socket.receive_json() == {'type': 'sync_ready'}
        assert client.put(path, headers=headers, json=payload).json()['revision'] == 1
        assert socket.receive_json() == {'type': 'route_updated', 'asset_id': asset_id, 'revision': 1}
    payload['travel_minutes'][0][1] = 20
    assert client.put(path, headers=headers, json=payload).json()['revision'] == 2
    saved = client.get(path, headers=headers)
    assert saved.status_code == 200
    assert saved.json()['revision'] == 2
    assert saved.json()['plan']['driving_minutes'] == 20


def test_dispatch_status_replay_and_timestamp_conflict(client):
    headers = login(client)
    asset = client.post('/api/assets', headers=headers, json={'name': 'Offline Dispatch Test'})
    path = f"/api/assets/{asset.json()['id']}/dispatch-route"
    payload = {'jobs': [{'id': 'job-1'}], 'travel_minutes': [[0, 12], [None, 0]]}
    assert client.put(path, headers=headers, json=payload).status_code == 200
    now = datetime.now(timezone.utc)
    completed = {'mutation_id': 'fc5ddce8-91c6-472d-bda8-f5848225c173',
                 'job_id': 'job-1', 'status': 'completed', 'changed_at': now.isoformat()}
    stale = {**completed, 'mutation_id': 'a7320458-c410-4a92-88b2-5d84f3471cf7',
             'status': 'skipped', 'changed_at': (now - timedelta(minutes=1)).isoformat()}
    assert client.post(path + '/status', headers=headers, json=completed).status_code == 422
    mismatch = {**headers, 'Idempotency-Key': stale['mutation_id']}
    assert client.post(path + '/status', headers=mismatch, json=completed).status_code == 422
    for mutation in (completed, completed, stale):
        replay_headers = {**headers, 'Idempotency-Key': mutation['mutation_id']}
        response = client.post(path + '/status', headers=replay_headers, json=mutation)
        assert response.status_code == 200
        assert response.json()['route']['revision'] == 2
        assert response.json()['route']['progress'] == {'job-1': 'completed'}
    assert client.get(path, headers=headers).json()['progress'] == {'job-1': 'completed'}


def test_temporary_checkpoint_expiry(client):
    headers = login(client)
    now = datetime.now(timezone.utc)
    report = {'kind': 'mobile_dot_checkpoint', 'lat': 39.7, 'lng': -105}
    active = client.post('/api/observations', headers=headers,
                         json={**report, 'observed_at': now.isoformat()})
    expired = client.post('/api/observations', headers=headers,
                          json={**report, 'observed_at': (now - timedelta(hours=3)).isoformat()})
    assert active.status_code == expired.status_code == 200
    assert datetime.fromisoformat(active.json()['expires_at']) == now + timedelta(hours=2)
    visible = {row['id'] for row in client.get('/api/observations', headers=headers).json()}
    assert active.json()['id'] in visible
    assert expired.json()['id'] not in visible


def test_checkpoint_spatial_search_and_live_event(client):
    from uuid import uuid4

    headers = login(client)
    params = {'west': -106, 'south': 39, 'east': -104, 'north': 40}
    payload = {'kind': 'mobile_dot_checkpoint', 'lat': 39.72, 'lng': -105.12,
               'client_id': str(uuid4())}
    ticket = client.post('/api/live-ticket', headers=headers).json()['ticket']
    with client.websocket_connect('/api/live', subprotocols=['dottracker', f'ticket.{ticket}']) as socket:
        socket.send_text('sync')
        assert socket.receive_json() == {'type': 'sync_ready'}
        created = client.post('/api/observations', headers=headers, json=payload)
        assert created.status_code == 200
        marker = created.json()
        assert socket.receive_json() == {
            'type': 'checkpoint_created',
            'data': {'id': marker['id'], 'latitude': payload['lat'], 'longitude': payload['lng'],
                     'expires_at': datetime.fromisoformat(marker['expires_at']).isoformat()}
        }
        replay = client.post('/api/observations', headers=headers, json=payload)
        assert replay.json()['id'] == marker['id']
        socket.send_text('sync')
        assert socket.receive_json() == {'type': 'sync_ready'}
    response = client.get('/api/v1/checkpoints', headers=headers, params=params)
    assert response.status_code == 200
    assert marker['id'] in {row['id'] for row in response.json()}
    outside = client.get('/api/v1/checkpoints', headers=headers, params={**params, 'east': -105.5})
    assert outside.status_code == 200
    assert marker['id'] not in {row['id'] for row in outside.json()}


def test_mobile_polygon_geofence_stores_postgis_geometry(client):
    headers = login(client)
    coordinates = [[-105.1, 39.7], [-105, 39.7], [-105, 39.8], [-105.1, 39.7]]
    payload = {'name': 'Mobile property boundary', 'coordinates': coordinates}
    assert client.post('/api/v1/geofences', json=payload).status_code == 401
    response = client.post('/api/v1/geofences', headers=headers, json=payload)
    assert response.status_code == 201
    fence = response.json()
    assert fence['geometry']['coordinates'] == coordinates[:-1]
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        geometry = conn.execute("""SELECT ST_SRID(geom), ST_IsValid(geom),
            ST_Covers(geom, ST_SetSRID(ST_MakePoint(-105.02,39.72),4326))
            FROM geofences WHERE id=%s""", (fence['id'],)).fetchone()
    assert geometry == (4326, True, True)
    bow_tie = [[-105.1, 39.7], [-105, 39.8], [-105, 39.7], [-105.1, 39.8]]
    assert client.post('/api/v1/geofences', headers=headers,
                       json={**payload, 'coordinates': bow_tie}).status_code == 422


def test_safety_submission_replay_and_review(client):
    from uuid import uuid4

    headers = login(client)
    template = client.get('/static/safety-v1.json').json()
    submission = {'submission_id': str(uuid4()), 'template_version': template['version'],
                  'completed_at': datetime.now(timezone.utc).isoformat(),
                  'answers': dict.fromkeys(template['items'], 'checked'), 'notes': ''}
    path = '/api/safety/submissions'
    concern = {**submission, 'answers': {**submission['answers'], 'ppe': 'concern'}}
    assert client.post(path, headers=headers, json=concern).status_code == 422
    for _ in range(2):
        assert client.post(path, headers=headers, json=submission).json() == {'synced': True}
    history = client.get(path, headers=headers)
    assert history.status_code == 200
    rows = [row for row in history.json() if row['submission_id'] == submission['submission_id']]
    assert len(rows) == 1
    assert rows[0]['answers'] == submission['answers']
    assert datetime.fromisoformat(rows[0]['completed_at']) == datetime.fromisoformat(submission['completed_at'])


def test_cdot_stations_load_and_locate(client):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        'load_cdot_stations', Path(__file__).resolve().parents[1] / 'scripts' / 'load_cdot_stations.py')
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    stations = loader.parse_stations([
        {'OBJECTID': '14', 'Y-LAT': '39.10055', 'X-LONG': '-104.863961', 'Highway Number': '25', 'Port Name': 'Monument',
         'Direction': 'NB', 'Phone': '719-481-2281', 'Highway': 'I-25', 'Mile Post': '161.2'},
        {'OBJECTID': '1', 'Y-LAT': '40.193154', 'X-LONG': '-104.820824', 'Highway Number': '85',
         'Port Name': 'Platteville Future SIte', 'Direction': 'Center', 'Phone': 'Not Open Yet', 'Highway': 'US-85', 'Mile Post': '249.6'},
    ])
    milepoints = loader.parse_milepoints([{'OBJECTID': '1', 'ROUTE': '025A', 'REFPT': '161', 'X': '511764.03', 'Y': '4327942.93'}])
    patrols = loader.parse_patrols([{'OBJECTID': '1', 'ROUTE': '025A', 'region': '2', 'refpt': '155', 'endrefpt': '170.5',
                                     'Cost_Center': '4102', 'Maintenance_Section': '4', 'Maintenance_Patrol': '19',
                                     'SUPERVISOR_AREA': 'John'}])
    datasets = [loader.Dataset('stations', 'stations.csv', 'a' * 64, stations),
                loader.Dataset('milepoints', 'milepoints.csv', 'b' * 64, milepoints),
                loader.Dataset('patrols', 'patrols.csv', 'c' * 64, patrols)]
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        assert loader.load(conn, datasets) == {'stations': 2, 'milepoints': 1, 'patrols': 1}
        # A second run replaces rather than duplicates the reference data.
        assert loader.load(conn, datasets) == {'stations': 2, 'milepoints': 1, 'patrols': 1}
        assert conn.execute('SELECT count(*) FROM cdot_stations').fetchone()[0] == 2

    assert client.get('/api/v1/cdot/stations').status_code == 401
    headers = login(client)
    listed = client.get('/api/v1/cdot/stations', headers=headers)
    assert listed.status_code == 200
    assert [(row['name'], row['status']) for row in listed.json()] == [('Monument', 'open'), ('Platteville Future Site', 'planned')]
    planned = client.get('/api/v1/cdot/stations?status=planned', headers=headers).json()
    assert [row['id'] for row in planned] == [1]
    assert client.get('/api/v1/cdot/stations?status=closed', headers=headers).status_code == 422

    summary = client.get('/api/v1/cdot/summary', headers=headers).json()
    assert summary['loaded'] is True
    assert {row['name']: row['row_count'] for row in summary['datasets']} == {'milepoints': 1, 'patrols': 1, 'stations': 2}

    located = client.get('/api/v1/cdot/locate?lat=39.10056&lng=-104.86396', headers=headers)
    assert located.status_code == 200, located.text
    body = located.json()
    assert body['milepoint']['route'] == '025A' and body['milepoint']['refpt'] == 161
    assert body['milepoint']['distance_m'] < 25
    assert body['patrol']['patrol'] == 19 and body['patrol']['supervisor_area'] == 'John'
    assert body['nearest_station']['name'] == 'Monument' and body['nearest_station']['distance_m'] < 25

    off_highway = client.get('/api/v1/cdot/locate?lat=39.7392&lng=-104.9903', headers=headers).json()
    assert off_highway['milepoint'] is None and off_highway['patrol'] is None
    assert off_highway['nearest_station']['name'] == 'Platteville Future Site'
    assert client.get('/api/v1/cdot/locate?lat=91&lng=0', headers=headers).status_code == 422


def test_inspection_sites_are_admin_placed_org_scoped_and_expire(client):
    admin_headers = login(client)
    now = datetime.now(timezone.utc)
    site = {'name': 'I-70 Silverthorne pull-off', 'agency': 'Colorado State Patrol', 'lat': 39.6303, 'lng': -106.0717,
            'highway': 'I-70', 'milepost': 205.2, 'direction': 'WB', 'notes': 'Level 1 inspections',
            'ends_at': (now + timedelta(hours=4)).isoformat()}
    path = '/api/v1/inspection-sites'
    assert client.post(path, json=site).status_code == 401
    created = client.post(path, headers=admin_headers, json=site)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body['status'] == 'active' and body['milepost'] == 205.2 and body['agency'] == 'Colorado State Patrol'

    scheduled = client.post(path, headers=admin_headers, json={**site, 'name': 'Scheduled US-50 site', 'highway': None,
        'milepost': None, 'starts_at': (now + timedelta(days=1)).isoformat(), 'ends_at': (now + timedelta(days=1, hours=6)).isoformat()})
    assert scheduled.status_code == 201 and scheduled.json()['status'] == 'scheduled'

    for bad, message in [
        ({**site, 'ends_at': '2026-11-03T12:00:00'}, 'timezone'),
        ({**site, 'starts_at': (now + timedelta(hours=5)).isoformat()}, 'after starts_at'),
        ({**site, 'starts_at': (now - timedelta(days=2)).isoformat(), 'ends_at': (now - timedelta(days=1)).isoformat()}, 'future'),
        ({**site, 'ends_at': (now + timedelta(days=31)).isoformat()}, '30 days'),
    ]:
        rejected = client.post(path, headers=admin_headers, json=bad)
        assert rejected.status_code == 422 and message in rejected.text, rejected.text
    assert client.post(path, headers=admin_headers, json={**site, 'name': ''}).status_code == 422

    listed = {row['name']: row for row in client.get(path, headers=admin_headers).json()}
    assert {'I-70 Silverthorne pull-off', 'Scheduled US-50 site'} <= set(listed)

    # Viewers in the same workspace see sites but cannot place or end them; other workspaces see nothing.
    invite = client.post('/api/invites', headers=admin_headers, json={'email': f'site-viewer-{RUN}@test.invalid', 'role': 'viewer'}).json()
    accepted = client.post('/api/invites/accept', json={'token': invite['invite_token'], 'password': 'site-viewer-password-long'})
    assert accepted.status_code == 201, accepted.text
    viewer = login(client, f'site-viewer-{RUN}@test.invalid', 'site-viewer-password-long')
    assert any(row['id'] == body['id'] for row in client.get(path, headers=viewer).json())
    assert client.post(path, headers=viewer, json=site).status_code == 403
    assert client.post(f"{path}/{body['id']}/end", headers=viewer).status_code == 403
    outsider = login(client, f'viewer-isolation-{RUN}@test.invalid', 'test-viewer-password-long')
    assert all(row['id'] != body['id'] for row in client.get(path, headers=outsider).json())

    ended = client.post(f"{path}/{body['id']}/end", headers=admin_headers)
    assert ended.status_code == 200 and ended.json() == {'id': body['id'], 'ended': True}
    assert client.post(f"{path}/{body['id']}/end", headers=admin_headers).status_code == 404
    assert all(row['id'] != body['id'] for row in client.get(path, headers=admin_headers).json())

    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        conn.execute("UPDATE inspection_sites SET starts_at=now()-interval '2 hours', ends_at=now()-interval '1 minute' WHERE id=%s",
                     (scheduled.json()['id'],))
    assert all(row['id'] != scheduled.json()['id'] for row in client.get(path, headers=admin_headers).json())


def test_geofence_list_and_delete_are_org_scoped_and_admin_only(client):
    admin_headers = login(client)
    fence = client.post('/api/geofences', headers=admin_headers, json={
        'name': 'Delete me', 'shape': 'circle', 'geometry': {'lat': 39.74, 'lng': -104.99, 'radius_m': 250}})
    assert fence.status_code == 200, fence.text
    fence_id = fence.json()['id']
    listed = client.get('/api/geofences', headers=admin_headers).json()
    assert any(row['id'] == fence_id and row['shape'] == 'circle' and row['geometry']['radius_m'] == 250 for row in listed)

    outsider = login(client, f'viewer-isolation-{RUN}@test.invalid', 'test-viewer-password-long')
    assert all(row['id'] != fence_id for row in client.get('/api/geofences', headers=outsider).json())
    assert client.delete(f'/api/geofences/{fence_id}', headers=outsider).status_code == 403

    assert client.delete(f'/api/geofences/{fence_id}', headers=admin_headers).json() == {'deleted': True}
    assert client.delete(f'/api/geofences/{fence_id}', headers=admin_headers).status_code == 404
    assert all(row['id'] != fence_id for row in client.get('/api/geofences', headers=admin_headers).json())


def test_geofence_alerts_can_be_paused_and_alerts_carry_their_location(client):
    admin_headers = login(client)
    tracker, send = new_tracker(client, admin_headers, 'pause')
    asset_id = tracker['id']
    fence = client.post('/api/geofences', headers=admin_headers, json={
        'name': f'Pause test {RUN}', 'shape': 'circle', 'geometry': {'lat': 38.5, 'lng': -106.5, 'radius_m': 500}}).json()
    def fix(lat, seconds_ago):
        send(lat, -106.5, seconds_ago)
    def fence_alerts():
        return [a for a in client.get('/api/alerts', headers=admin_headers).json() if a['geofence_id'] == fence['id']]

    fix(38.6, 6)
    fix(38.5, 5)  # inside: enter
    alerts = fence_alerts()
    assert [a['event'] for a in alerts] == ['enter']
    assert alerts[0]['lat'] == 38.5 and alerts[0]['lng'] == -106.5 and alerts[0]['asset_id'] == asset_id
    assert alerts[0]['asset_name'] and alerts[0]['occurred_at']

    viewer = login(client, f'viewer-isolation-{RUN}@test.invalid', 'test-viewer-password-long')
    assert client.patch(f"/api/geofences/{fence['id']}", headers=viewer, json={'enabled': False}).status_code == 403
    assert client.patch(f"/api/geofences/{fence['id']}", headers=admin_headers, json={'enabled': 'nope'}).status_code == 422
    paused = client.patch(f"/api/geofences/{fence['id']}", headers=admin_headers, json={'enabled': False})
    assert paused.status_code == 200 and paused.json()['enabled'] is False
    fix(38.6, 4)  # leaves while paused: no alert
    assert len(fence_alerts()) == 1

    resumed = client.patch(f"/api/geofences/{fence['id']}", headers=admin_headers, json={'enabled': True}).json()
    assert resumed['enabled'] is True
    fix(38.5, 3)  # enters again with alerts on
    assert [a['event'] for a in fence_alerts()] == ['enter', 'enter']
    assert client.patch('/api/geofences/999999999', headers=admin_headers, json={'enabled': True}).status_code == 404


def test_inspection_site_reports_who_placed_it(client):
    admin_headers = login(client)
    site = client.post('/api/v1/inspection-sites', headers=admin_headers, json={
        'name': f'Placed-by test {RUN}', 'agency': 'CSP', 'lat': 39.7, 'lng': -105.0,
        'ends_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}).json()
    assert site['placed_by'] == 'admin@dottracker.local' and site['placed_at']
    listed = [s for s in client.get('/api/v1/inspection-sites', headers=admin_headers).json() if s['id'] == site['id']]
    assert listed[0]['placed_by'] == 'admin@dottracker.local'


def test_geofence_changes_are_broadcast_to_open_maps(client):
    headers = login(client)
    ticket = client.post('/api/live-ticket', headers=headers).json()['ticket']
    with client.websocket_connect('/api/live', subprotocols=['dottracker', f'ticket.{ticket}']) as socket:
        fence = client.post('/api/geofences', headers=headers, json={
            'name': f'Broadcast {RUN}', 'shape': 'circle', 'geometry': {'lat': 39.0, 'lng': -105.5, 'radius_m': 300}}).json()
        assert socket.receive_json() == {'type': 'geofence_changed', 'data': {'id': fence['id']}}
        client.patch(f"/api/geofences/{fence['id']}", headers=headers, json={'enabled': False})
        assert socket.receive_json() == {'type': 'geofence_changed', 'data': {'id': fence['id']}}
        client.delete(f"/api/geofences/{fence['id']}", headers=headers)
        assert socket.receive_json() == {'type': 'geofence_changed', 'data': {'id': fence['id']}}


def test_alerts_can_be_marked_read_within_the_workspace(client):
    headers = login(client)
    fence = client.post('/api/geofences', headers=headers, json={
        'name': f'Read test {RUN}', 'shape': 'circle', 'geometry': {'lat': 37.9, 'lng': -107.9, 'radius_m': 400}}).json()
    _, send = new_tracker(client, headers, 'reads')
    def fix(lat, seconds_ago):
        send(lat, -107.9, seconds_ago)
    fix(38.0, 3); fix(37.9, 2); fix(38.0, 1)  # enter, then exit
    mine = lambda: [a for a in client.get('/api/alerts', headers=headers).json() if a['geofence_id'] == fence['id']]
    first, second = mine()
    assert first['read_at'] is None and second['read_at'] is None

    outsider = login(client, f'viewer-isolation-{RUN}@test.invalid', 'test-viewer-password-long')
    assert client.post('/api/alerts/read', headers=outsider, json={'ids': [first['id']]}).json() == {'marked_read': 0}
    assert client.post('/api/alerts/read', headers=headers, json={'ids': []}).status_code == 422
    assert client.post('/api/alerts/read', json={}).status_code == 401

    assert client.post('/api/alerts/read', headers=headers, json={'ids': [first['id']]}).json() == {'marked_read': 1}
    by_id = {a['id']: a for a in mine()}
    assert by_id[first['id']]['read_at'] and by_id[second['id']]['read_at'] is None
    assert client.post('/api/alerts/read', headers=headers, json={'ids': [first['id']]}).json() == {'marked_read': 0}
    assert client.post('/api/alerts/read', headers=headers, json={}).json()['marked_read'] >= 1
    assert all(a['read_at'] for a in mine())


def test_alert_read_state_is_per_person(client):
    admin_headers = login(client)
    invite = client.post('/api/invites', headers=admin_headers, json={'email': f'reader-{RUN}@test.invalid', 'role': 'viewer'}).json()
    assert client.post('/api/invites/accept', json={'token': invite['invite_token'], 'password': 'reader-password-long-1'}).status_code == 201
    reader = login(client, f'reader-{RUN}@test.invalid', 'reader-password-long-1')
    fence = client.post('/api/geofences', headers=admin_headers, json={
        'name': f'Per person {RUN}', 'shape': 'circle', 'geometry': {'lat': 37.5, 'lng': -105.5, 'radius_m': 400}}).json()
    _, send = new_tracker(client, admin_headers, 'perperson')
    for lat, ago in [(37.6, 3), (37.5, 2)]:
        send(lat, -105.5, ago)
    pick = lambda headers: [a for a in client.get('/api/alerts', headers=headers).json() if a['geofence_id'] == fence['id']]
    (alert,) = pick(admin_headers)
    assert client.post('/api/alerts/read', headers=admin_headers, json={}).json()['marked_read'] >= 1
    assert pick(admin_headers)[0]['read_at'] is not None
    assert pick(reader)[0]['read_at'] is None  # the admin reading it does not mark it read for anyone else
    assert client.post('/api/alerts/read', headers=reader, json={'ids': [alert['id']]}).json() == {'marked_read': 1}
    assert pick(reader)[0]['read_at'] is not None


def test_map_style_settings_validate_and_drive_the_csp():
    assert map_settings('', '') == ('https://tiles.openfreemap.org/styles/dark', ['https://tiles.openfreemap.org', 'https://*.openfreemap.org'])
    assert map_settings('offline', '') == ('/api/v1/map/offline-style.json', [])
    style, origins = map_settings('https://maps.example.org/styles/basic.json', 'https://tiles.example.org, https://fonts.example.org/')
    assert style == 'https://maps.example.org/styles/basic.json'
    assert origins == ['https://maps.example.org', 'https://tiles.example.org', 'https://fonts.example.org']
    for bad_style, bad_extra in [('http://maps.example.org/style.json', ''), ('https://user:pw@maps.example.org/s.json', ''),
                                 ('javascript:alert(1)', ''), ('offline', 'http://tiles.example.org'), ('offline', 'https://tiles.example.org/path')]:
        with pytest.raises(RuntimeError):
            map_settings(bad_style, bad_extra)


def test_offline_map_style_and_highways(client):
    style = client.get('/api/v1/map/offline-style.json')
    assert style.status_code == 200
    body = style.json()
    assert body['version'] == 8 and body['sources']['highways']['data'] == '/api/v1/map/highways.geojson'
    assert {layer['id'] for layer in body['layers']} >= {'background', 'colorado-outline', 'highways'}
    assert client.get('/api/v1/map/highways.geojson').status_code == 401
    highways = client.get('/api/v1/map/highways.geojson', headers=login(client)).json()
    assert highways['type'] == 'FeatureCollection'
    assert all(f['geometry']['type'] == 'LineString' and len(f['geometry']['coordinates']) >= 2 for f in highways['features'])
    csp = client.get('/health').headers['content-security-policy']
    assert "connect-src 'self' wss: https://tiles.openfreemap.org https://*.openfreemap.org;" in csp


def test_geofence_polygon_works_with_an_empty_search_path():
    # pg_restore runs with search_path='' while it recomputes geofences.geom; the
    # function must still resolve PostGIS types, or restored backups lose every geofence.
    ring = '[[-105.1,39.7],[-105.0,39.7],[-105.0,39.8]]'
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        conn.execute("SET search_path = ''")
        valid = conn.execute('SELECT public.ST_IsValid(public.geofence_polygon(%s::jsonb))', (ring,)).fetchone()[0]
        config = conn.execute("SELECT proconfig FROM pg_catalog.pg_proc WHERE proname = 'geofence_polygon'").fetchone()[0]
    assert valid is True
    assert 'search_path=public, pg_temp' in config


def test_out_of_order_fix_is_stored_but_raises_no_fence_alert(client):
    headers = login(client)
    fence = client.post('/api/geofences', headers=headers, json={
        'name': f'Out of order {RUN}', 'shape': 'circle', 'geometry': {'lat': 37.2, 'lng': -104.2, 'radius_m': 400}}).json()
    tracker, send = new_tracker(client, headers, 'late')
    assert send(37.3, -104.2, 2)['accepted'] is True    # newest fix, outside
    assert send(37.2, -104.2, 40)['accepted'] is True   # delayed fix from earlier, inside the fence
    events = [a['event'] for a in client.get('/api/alerts', headers=headers).json() if a['geofence_id'] == fence['id']]
    assert events == []
    trip = client.get(f"/api/assets/{tracker['id']}/positions", headers=headers).json()
    assert [p['lat'] for p in trip] == [37.2, 37.3]  # the delayed fix is kept, in time order
    send(37.2, -104.2, 1)                                # a new, in-order fix inside does raise the alert
    events = [a['event'] for a in client.get('/api/alerts', headers=headers).json() if a['geofence_id'] == fence['id']]
    assert events == ['enter']


def test_trip_history_for_another_workspace_asset_is_not_found(client):
    admin_headers = login(client)
    asset_id = client.post('/api/assets', headers=admin_headers, json={'name': f'History owner {RUN}'}).json()['id']
    assert client.get(f'/api/assets/{asset_id}/positions', headers=admin_headers).status_code == 200
    outsider = login(client, f'viewer-isolation-{RUN}@test.invalid', 'test-viewer-password-long')
    assert client.get(f'/api/assets/{asset_id}/positions', headers=outsider).status_code == 404
    assert client.get('/api/assets/999999999/positions', headers=admin_headers).status_code == 404
