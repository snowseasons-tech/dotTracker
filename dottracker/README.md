# dotTracker · production-oriented MVP

A pilot fleet-tracking application with a responsive MapLibre dashboard, FastAPI API, PostgreSQL/PostGIS schema, Argon2id password hashing, rotating opaque refresh sessions, org-scoped access, individual tracker credentials, short-lived one-use WebSocket tickets, duplicate-fix and observation idempotency, offline observation queueing, trip history, circle/polygon geofences, alerts, and observation pins.

## Local development

Requires Docker Compose and Python 3.12+ (for the GPS emulator).

```bash
cp .env.example .env
docker compose up --build
```

Open http://localhost:8000 and choose **Initialize demo workspace**. Demo login: `admin@dottracker.local` / `dottracker-demo`.

Run the emulator:

```bash
python3 emulator.py --count 3 --interval 5
```

The demo tracker IDs and credential are seeded only in development. Do not reuse them. Demo seeding is disabled unless `APP_ENV=development` and `ALLOW_DEMO_SEED=true`; it is hard-disabled in production.

## Replay a trip

Open **History**, choose an asset and a local date range, and select **Load trip**. Use **Play** or the position slider to move through the recorded GPS fixes on the map. History covers the last 30 days and returns up to 10,000 fixes per request.

## Drop an observation pin

Choose **Drop observation**, then tap or click the map. On desktop, you can also right-click the map. The pin keeps the timestamp from when you chose its location, including when an offline submission syncs later. Add an optional note or link the pin to an asset. Use **Observations** on the map to show or hide the pins.

## Production deployment path

This project is a production-oriented MVP for a controlled pilot. It has not received an independent penetration test or a live infrastructure rehearsal; use the release gates below before onboarding customers.

1. Provision PostgreSQL 17 with PostGIS 3.5 on a managed service, private network access, automated backups, point-in-time recovery, and TLS. For a new database, apply `sql/schema.sql` with a database owner/migration role. For the existing first MVP database, do not reapply the baseline; run the one-shot `migrate.py` service with `MIGRATION_DATABASE_URL` to apply the transactional 0002 upgrade and record its version. Grant the runtime role only DML and sequence privileges. Review and rehearse migrations with a verified backup before release.
2. Create separate `dottracker_app` and `dottracker_migrator` roles. After the schema owner applies the baseline, grant the app role only what the API needs:

   ```sql
   GRANT CONNECT ON DATABASE dottracker TO dottracker_app;
   GRANT USAGE ON SCHEMA public TO dottracker_app;
   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO dottracker_app;
   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO dottracker_app;
   ALTER DEFAULT PRIVILEGES FOR ROLE dottracker_migrator IN SCHEMA public
     GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dottracker_app;
   ALTER DEFAULT PRIVILEGES FOR ROLE dottracker_migrator IN SCHEMA public
     GRANT USAGE, SELECT ON SEQUENCES TO dottracker_app;
   ```

   Copy `.env.production.example` to `.env.production`; set a unique `JWT_SECRET` with at least 32 random bytes, a runtime `DATABASE_URL`, and a separate owner-level `MIGRATION_DATABASE_URL`; both database URLs must use `sslmode=require` or `sslmode=verify-full`. Run the one-shot `migrate` service before starting the API; the API container does not receive the migration credential. Keep the file out of source control and restrict access to the deploy operator.
3. Build the image and apply the migration in its short-lived container. The one-shot service is removed when it exits; the API container does not receive the owner credential.

   ```bash
   docker compose -f docker-compose.production.yml build api migrate
   docker compose -f docker-compose.production.yml --profile migration run --rm migrate
   ```

4. Put a TLS reverse proxy or managed edge in front of the service. Configure request size limits, login/ingest rate limits, and trusted proxy forwarding there. Restrict the service port to localhost/private ingress. Then start one API worker:

   ```bash
   docker compose -f docker-compose.production.yml up -d api
   ```

   One worker is required for the in-process WebSocket fan-out. Add a shared pub/sub transport before horizontal scaling.
5. Provision each pilot organization using an operator shell with the production database URL:

   ```bash
   docker compose -f docker-compose.production.yml exec api \
     python scripts/create_org.py --name "Pilot Org" --admin-email admin@example.com
   ```

   The script prompts for the password without echoing it. The first administrator can invite org-scoped admins/viewers with a 48-hour one-use token to deliver through a trusted channel. Admins can register an asset with its tracker ID; the server generates an individual high-entropy token, stores only its SHA-256 hash, and returns the credential once. Rotate it with `POST /api/assets/{id}/device/rotate`.
6. Confirm `/health` succeeds through the private service path, validate TLS and restore a database backup before production use.

The production compose profile runs as a non-root user, drops Linux capabilities, uses a read-only root filesystem, disables demo seeding and interactive API docs, and expects an external managed database. Backups, monitoring, external push alert delivery, invite email delivery, tracker protocol adapters, and a production migration rehearsal still need operational work before a broad launch.

## Security controls in this MVP

- Tenant ownership is checked in application queries and enforced by composite foreign keys across tenant-owned rows.
- Passwords use Argon2id. Failed logins are rate-limited per hashed client-address/email key in Postgres; the edge proxy must also apply a distributed source-IP limit. Opaque refresh sessions rotate on use and can be revoked at logout; access JWTs expire within 60 minutes.
- Device credentials are unique, high entropy, hashed at rest, rotated by replacement, and never recoverable from the database.
- WebSocket access uses short-lived one-use tickets; access JWTs are not placed in WebSocket URLs.
- Tracker fix writes and browser-queued observations are idempotent for duplicate submissions. Out-of-order delayed fixes are retained as positions but must not be treated as reliable real-time fence transitions.
- API input bounds reject malformed coordinates, speed, heading, timestamps, and oversized notes; standard response hardening headers and a restrictive Content Security Policy are set.
- The proxy/edge must enforce TLS, request size controls, trusted forwarded headers, and distributed rate limiting. Do not expose the app directly to the public internet.

## API

- `POST /api/auth/login`, `POST /api/auth/refresh`, `POST /api/auth/logout`, `GET /api/me`
- `POST /api/invites` (admin; returns a one-time 48-hour token for secure out-of-band delivery), `POST /api/invites/accept`
- `GET /api/assets`, `POST /api/assets` (admin; optional `device_id` provisions a tracker)
- `POST /api/assets/{id}/device/rotate` (admin)
- `GET /api/assets/{id}/positions?from=&to=` (last 30 days)
- `GET|POST /api/geofences`, `DELETE /api/geofences/{id}` (writes admin-only)
- `POST|GET /api/observations`
- `GET /api/alerts`
- `POST /api/ingest` (per-device token in request body over TLS)
- `POST /api/live-ticket`, `WS /api/live` (one-use ticket via subprotocol)
- `GET /health` (database readiness), `GET /health/live` (process liveness)

Circle geometry: `{ "lat": 39.7, "lng": -105.0, "radius_m": 400 }`. Polygon geometry uses `[longitude, latitude]` pairs: `{ "coordinates": [[-105,39],[-104,39],[-104,40]] }`.

## Verification

Run local checks:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile app/main.py app/geometry.py emulator.py migrate.py scripts/create_org.py tests/api_test.py
```

GitHub Actions also runs PostgreSQL/PostGIS API integration tests against the previous-schema migration, JavaScript syntax checking, dependency auditing, and a container build on each push and pull request. A full release gate must additionally scan the container image, verify backup restore, and exercise deployment behind the actual TLS proxy. The database migration and API integration gates have been defined but could not be executed in this workspace because Docker, PostgreSQL, and the application dependencies are unavailable.

## Run integration tests with Docker

Run `bash scripts/test_with_docker.sh` from this directory. The runner starts a temporary PostGIS database without publishing ports, upgrades the prior MVP schema, runs pytest, and removes the test containers and volumes on exit.
