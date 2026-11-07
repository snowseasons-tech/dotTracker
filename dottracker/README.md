# dotTracker · production-oriented MVP

Development and release gates: see [AGENT_WORKFLOW.md](AGENT_WORKFLOW.md) and the step-by-step [PRODUCTION_WORKFLOW.md](PRODUCTION_WORKFLOW.md). The API refuses to start unless all supported migrations through `0008-safety-checklists` are applied.

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

## Sort a crew route

Dispatch admins can call `POST /api/routes/optimize` with a job list and a directed road travel-time matrix. Row and column 0 represent the starting point. The remaining rows and columns follow job order. Use `null` for a blocked or prohibited leg. All times are whole minutes from the same reference point, such as local midnight on the service date.

```json
{
  "departure_minute": 480,
  "crew_equipment": ["chipper"],
  "jobs": [
    {"id": "job-a", "earliest_start": 540, "latest_finish": 660, "service_minutes": 30, "equipment": ["chipper"]},
    {"id": "job-b", "earliest_start": 480, "latest_finish": 540, "service_minutes": 20}
  ],
  "travel_minutes": [[0, 30, 10], [null, 0, 20], [null, 15, 0]]
}
```

The response lists arrival, service start, and finish times in stop order. It minimizes driving minutes and uses earliest completion to break ties. It handles one crew and up to eight required stops, ending at the last job. An infeasible itinerary returns HTTP 409.

For a closure, call `POST /api/routes/reroute` with the same format, using the current crew location as the matrix origin, a current departure time, and only unfinished jobs. Supply updated travel times or mark unavailable legs `null`. The caller must obtain the matrix from a road routing source configured for the vehicle's height, weight, and restrictions. These endpoints calculate an itinerary without storing or dispatching it.

### Publish and sync a route

Use the same route request body with `PUT /api/assets/{asset_id}/dispatch-route` to save and publish a plan as a dispatcher. The browser's **Dispatch** tab shows the saved stop order for the selected crew asset. The organization-scoped `route_updated` WebSocket event includes the asset ID and revision. After reconnecting, the browser waits for the socket's `sync_ready` acknowledgement and fetches the latest saved plan. This also lets clients recover a plan published while they were disconnected. Existing databases require migration `0003-dispatch-routes` before using Dispatch.

The live event hub runs in the API process. Keep the existing single-worker deployment for live updates until a shared event transport is configured. Native background push registration and delivery require a mobile client and push provider configuration.

### Update job status offline

Open the Dispatch tab while connected and select the crew asset to cache its stop list. In that tab, change a job to pending, completed, or skipped. The browser writes a UUID and UTC timestamp to the IndexedDB outbox before confirming the local change. The status count shows queued changes. The selected route replays changes oldest first after reconnect or when the tab resumes. Each request includes its UUID in the `Idempotency-Key` header. The browser removes only the acknowledged entry. A failed upload stops replay and keeps the remaining entries for retry.

Apply migration `0004-dispatch-progress` before using status sync. The API deduplicates mutation UUIDs and resolves competing status updates by UTC timestamp, using the UUID to break ties. Use a stable job ID per asset when publishing replacement routes so progress follows the same job. This browser implementation caches the selected itinerary and progress. It requires an already loaded dashboard for offline use.

### Report a temporary inspection checkpoint

Choose **Drop observation**, place the pin, then select **Temporary inspection checkpoint** as the pin type. Checkpoint reports queue in browser storage before upload and use the observation layer. They expire two hours after capture, including reports uploaded later. Apply migration `0005-temporary-checkpoints` before using this pin type.

The route sorter uses caller-supplied road travel times. Checkpoint pins do not alter that matrix. Road-level detours require a routing provider that can map a location to an affected road segment and return updated travel times.

Migration `0006-checkpoint-spatial` adds generated PostGIS points and a checkpoint-only GiST index. Clients can query active checkpoint reports in a bounding box with `GET /api/v1/checkpoints?west=-106&south=39&east=-104&north=40`. Writes use the existing `POST /api/observations` endpoint with `kind: "mobile_dot_checkpoint"` and a client UUID in `client_id`. Only a newly inserted, unexpired checkpoint emits a `checkpoint_created` event to the organization. The web dashboard refreshes its observation layer on that event and after reconnecting.

Use representative data to inspect the spatial query plan. PostgreSQL may prefer a sequential or organization index scan for a small fixture.

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id FROM observations
WHERE org_id = 1 AND kind = 'mobile_dot_checkpoint' AND expires_at > now()
  AND geom && ST_MakeEnvelope(-106, 39, -104, 40, 4326);
```

### Draw a polygon geofence

As an admin, choose **Geofences → Draw polygon**. Tap or click the map to add corners. Use **Undo corner** or **Reset** to adjust the draft. After placing at least three corners, choose **Close boundary** to preview the filled area, then **Name and save**. The server validates the boundary with PostGIS before saving it.

Native clients can save a polygon with `POST /api/v1/geofences`, a dispatcher bearer token, and JSON containing `name` and `coordinates`. Send 3 to 500 distinct corners in longitude, latitude order. A repeated closing coordinate is optional. The endpoint returns HTTP 201 with the saved geofence. Apply migration `0007-geofence-spatial` to backfill and index valid polygon boundaries. Circle geofences keep their existing center and radius representation.

### Crew safety checklist

Open **Safety** online once to cache checklist version 1. Complete the five items and add a note for any concern. Submitting while offline saves the completed form to the browser's IndexedDB outbox. The dashboard retries on reconnect or sign-in. Dispatch admins can open **Review submissions** for the latest 100 records in their organization. Completion and receipt times are stored separately. Apply migration `0008-safety-checklists` before using the form.
