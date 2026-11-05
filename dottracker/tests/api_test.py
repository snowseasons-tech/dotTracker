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

from app.main import app, pwd  # noqa: E402


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
                        (org_id, 'viewer-isolation@test.invalid', pwd.hash('test-viewer-password-long'), 'viewer'))
            cur.execute("INSERT INTO assets(org_id,name,kind) VALUES(%s,'Tenant B Only','vehicle')", (org_id,))
    return org_id


def test_readiness_and_security_headers(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']


def test_org_scope_and_viewer_permissions(client):
    org_b = create_second_org()
    admin_headers = login(client)
    asset_a = client.post('/api/assets', headers=admin_headers, json={'name': 'Tenant A Private Asset'}).json()['id']
    viewer_headers = login(client, 'viewer-isolation@test.invalid', 'test-viewer-password-long')
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
        assert conn.execute('SELECT org_id FROM users WHERE email=%s', ('viewer-isolation@test.invalid',)).fetchone()[0] == org_b


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
                          json={'email': 'New.Viewer@test.invalid', 'role': 'viewer'})
    assert invited.status_code == 201
    payload = invited.json()
    assert payload['email'] == 'new.viewer@test.invalid'
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
