# dotTracker MVP

A runnable pilot foundation for fleet and asset tracking. It includes a responsive MapLibre dashboard, FastAPI backend, PostgreSQL/PostGIS schema, email/password sign-in, organization-scoped endpoints, tracker ingestion, live WebSocket notifications, trip history, circular/polygon geofences, alerts, and observation pins. Authentication roles are `admin` and `viewer`; the UI limits asset and geofence creation to admins.

## Run locally

Requirements: Docker Compose and Python 3.12+ (only needed for the emulator).

```bash
cp .env.example .env
# Replace JWT_SECRET with a unique random value before using non-demo data.
docker compose up --build
```

Open http://localhost:8000. On first run, choose **Initialize demo workspace** or sign in with `admin@dottracker.local` / `dottracker-demo`. Demo initialization is available only when `ALLOW_DEMO_SEED=true` (the local default); set false for deployment. The demo includes three assets and Denver-area sample positions.

To stream simulated GPS updates:

```bash
python3 emulator.py --count 3 --interval 5
```

The emulator sends `truck-01`, `trailer-02`, and `van-03` with a demo tracker token. For local development these match `.env` defaults. Keep the device credential secret; set a strong `DEVICE_TOKEN` before any shared deployment.

## API overview

- `POST /api/auth/login`, `GET /api/me`
- `GET /api/assets`, `POST /api/assets` (admin)
- `GET /api/assets/{id}/positions?from=&to=` (last 30 days maximum)
- `GET|POST /api/geofences`, `DELETE /api/geofences/{id}` (writes admin-only)
- `POST|GET /api/observations`
- `GET /api/alerts`
- `POST /api/ingest` (device credential in `X-Device-Token`, per-tracker token in body)
- `WS /api/live?token=<access-token>`
- `GET /health`

Circle geofence geometry is `{ "lat": 39.7, "lng": -105.0, "radius_m": 400 }`. Polygon geometry uses `{ "coordinates": [[lng,lat], ...] }`. The device emulator is an HTTP ingestion stub to keep the pilot hardware-independent; MQTT protocol adapters, password reset email, mobile apps, push notifications, and durable offline observation sync remain follow-on work.

## Security and pilot notes

This is a local pilot starter, not production-ready deployment. Use TLS, a strong `JWT_SECRET` and device credentials, set `ALLOW_DEMO_SEED=false`, back up Postgres, add rate limiting and audited invite/password-reset flows, and rotate credentials before onboarding real trackers. API queries scope user data by organization. Position rows are append-only. Position freshness currently treats fixes older than 60 seconds as offline; change that threshold to 60 seconds if the pilot operational target requires it. The UI refreshes over REST and receives WebSocket ingestion notifications. Observation capture timestamps are saved at submission; offline queueing is not implemented in this first runnable slice.
