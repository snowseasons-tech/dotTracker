# dotTracker

**Fleet & asset tracking platform.** Live GPS positions, trip history, geofence alerts, and visual observation pins — for small teams that need to know where their assets are and trust the data.

This README is the MVP playbook: what we're building, how, and in what order.

---

## Table of Contents

1. [Product Overview](#product-overview)
2. [MVP Scope](#mvp-scope)
3. [Tech Stack](#tech-stack)
4. [Architecture](#architecture)
5. [Data Model](#data-model)
6. [Build Playbook (Weeks 1–8)](#build-playbook)
7. [Running Locally](#running-locally)
8. [Observation Pins — Feature Spec](#observation-pins--feature-spec)
9. [Security](#security)
10. [Risks](#risks)
11. [Definition of Done](#definition-of-done)
12. [Roadmap (Post-MVP)](#roadmap-post-mvp)

---

## Product Overview

dotTracker lets a small organization:

- See all assets on a live map (position freshness ≤ 60s)
- Know each asset's status: **moving / stopped / offline**
- Review where an asset has been over the last 30 days
- Get alerted when an asset enters or exits a geofence
- Record **visual observations** by dropping a pin on the map with a timestamped note

**MVP success metric:** 2–3 pilot orgs actively using the product ≥ 3× per week, unsupervised, after 4 weeks.

---

## MVP Scope

### In scope
| Feature | Notes |
|---|---|
| Live map | All org assets, status-colored markers, WS live feed |
| Asset list | Moving / stopped / offline, search |
| Trip history | Breadcrumb trail + scrubber, last 30 days per asset |
| Geofences | Circle/polygon, per-asset entry/exit alerts (push + in-app) |
| Observation pins | Long-press map → pin with timestamp, note, author |
| Auth & roles | Email/password login, password reset, Admin & Viewer |
| Multi-org | Single-tenant per org; org-scoped data isolation |

### Out of scope (v1) — do not build
- Driver behavior analytics, fuel/telematics, ELD compliance
- Custom report builder, public API for third parties
- Billing/subscription flows (onboard manually; sell first)
- Offline-first map rendering, route optimization
- **Photo attachments on observation pins** → v2. Keep a nullable photo ref in the `observations` table so v2 needs no migration.

**Scope-creep rule:** every new idea goes to the v2 board, not the MVP repo.

---

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Mobile | React Native (Expo) *or* Flutter | One codebase → iOS + Android |
| Backend | Node.js (NestJS) *or* Go | Team familiarity decides |
| Database | PostgreSQL + PostGIS | Geo queries, geofence evaluation |
| Realtime | WebSockets (Socket.IO) / MQTT broker | Device ingestion + live app feed |
| Maps | MapLibre + free tile provider | Avoid per-load map licensing at pilot stage |
| Infra | AWS or GCP, Terraform, managed Postgres | Reproducible environments from day 1 |
| Monitoring | Sentry + metrics dashboard | Cheap visibility, early warning |
| CI | Lint + tests on every push | Quality gate from day 1 |

**Devices:** support one well-documented GPS tracker first (e.g., Teltonika or GT-06 class). Build a **device emulator** immediately — never block on hardware. Phone-as-tracker is a stopgap for pilots only.

---

## Architecture

```
GPS devices ──MQTT/socket──▶ Ingestion service ──▶ PostgreSQL (positions)
                                  │
                                  ├──▶ Geofence engine ──▶ geofence_events ──▶ Alerts
                                  └──▶ WS publish ──▶ Mobile app (live map)
Mobile app ──REST/WS──▶ API (auth, org-scoped) ──▶ assets, geofences, observations
```

- Positions are **append-only**; a rollup table serves long history queries.
- Status (`moving/stopped/offline`) is computed from position recency + speed thresholds by a status service.
- All queries are org-scoped at the data-access layer — no exceptions.

---

## Data Model

| Table | Purpose |
|---|---|
| `organizations` | Tenant root |
| `users` | Members; role = admin \| viewer |
| `assets` | Tracked things (vehicle, machine) |
| `devices` | Hardware trackers; auth token per device; links to asset |
| `positions` | Append-only GPS fixes (lat, lng, speed, heading, ts) |
| `geofences` | Circle/polygon per org |
| `geofence_events` | Idempotent enter/exit events |
| `alerts` | Deliverable alert records |
| `observations` | Manual map pins: lat, lng, ts, note, author, nullable asset link, nullable photo ref (v2) |

---

## Build Playbook

### Week 1 — Foundation
- **Days 1–3:** One-page spec (customer, top 3 jobs-to-be-done, success metric). Lock the stack. Decide first device model. Set up repo, CI, staging + prod.
- **Days 4–7:** Schema (above), device ingestion protocol (MQTT topics, payload, per-device token), ingestion service decoding 1–2 real tracker protocols.
- **Exit:** device emulator streams fake positions → they land in the DB.

### Week 2 — Backend API
- JWT access + refresh; org-scoped authorization on every query.
- Endpoints: `GET /assets`, `GET /assets/:id/positions?from=&to=`, `WS /live`, `CRUD /geofences`, `POST /observations`, `GET /observations?bbox=&from=&to=&asset_id=`, `GET /alerts`.
- Geofence engine: evaluate each position, emit idempotent enter/exit events.
- **Exit:** seeded integration tests pass.

### Week 3 — Mobile shell
- Expo/Flutter project, navigation, real auth login.
- Map screen: status-colored markers, WS subscription, auto-fit viewport.
- Asset list: search, tap → focus on map.
- **Exit:** a user sees their org's assets move in (emulated) real time.

### Week 4 — History, geofences, observation pins
- Trip view: asset + date range → polyline + play/pause scrubber.
- Geofence editor: draw circle/polygon, name, per-asset alert toggle.
- Observation pins: see [feature spec](#observation-pins--feature-spec).
- Status computation service.
- **Exit:** full feature set usable end-to-end.

### Week 5 — Hardening
- Reconnect handling; offline detection after N minutes of silence.
- Invite flow (email → set password); Admin vs Viewer permissions.
- Performance: PostGIS indexes, paginated history, cached asset list.
- Security pass: rate limiting, device token rotation, cross-org isolation tests.
- Sentry + metrics + app error boundaries.

### Week 6 — Pilot launch
- TestFlight / Play internal testing.
- Manually onboard 2–3 pilot orgs (create org, pre-register devices, help install).
- Instrument the success metric; shared feedback board; weekly fix cycle.

### Weeks 7–8 — MVP gate & v1.1
- Review: are pilots using it ≥ 3×/week without prompting?
- Ship top-3 pilot fixes as v1.1.
- Store submission; basic onboarding docs.
- Choose v2 priorities from usage data.

**Team:** 2 devs + founder (PM). Solo: double the timeline or cut history view and roles from v1.

---

## Running Locally

```bash
# Backend
git clone <repo> && cd dottracker
cp .env.example .env
docker compose up -d          # Postgres + PostGIS + MQTT broker
npm install && npm run migrate && npm run seed
npm run dev                   # API + ingestion service

# Device emulator (fake GPS stream)
npm run emulate -- --count 5

# Mobile
cd app && npm install
npm run start                 # Expo / Flutter dev server
```

---

## Observation Pins — Feature Spec

Manual annotations made on-site during a visual observation.

**UX**
1. Long-press the map (or tap "Drop pin") → compact form opens.
2. Fields: timestamp (default now), optional note, optional link to a nearby asset. Author is the logged-in user. **No photo capture in v1.**
3. Save → distinct marker style on the map; tap → detail sheet (note, author, time).
4. Observation layer toggle (show/hide all pins); list view filterable by date and asset.

**Behavior**
- Offline-capable: queue locally, sync on reconnect. The capture timestamp is kept regardless of sync time.
- Org-visible to all members.

**API**
- `POST /observations` — lat/lng, ts, note, optional asset link.
- `GET /observations?bbox=&from=&to=&asset_id=` — for layer rendering and lists.

**SLO:** a user drops a pin with a note in under 30 seconds.

---

## Security

- JWT access + refresh tokens; short access-token lifetime.
- Org scoping enforced at the data-access layer, verified by cross-org isolation tests.
- Per-device auth tokens, rotatable.
- Rate limiting on auth and ingestion endpoints.
- No cross-org data leaks — this is a hard test gate.

---

## Risks

| Risk | Mitigation |
|---|---|
| Device protocol complexity | One tracker model first; emulator until real units arrive |
| Map licensing costs | MapLibre + free tiles for pilots; revisit before scale |
| Phone-as-tracker battery/data | Explicit stopgap; real devices for pilots |
| Scope creep | New ideas → v2 board, never the MVP repo |

---

## Definition of Done (MVP)

- [ ] A new org onboarded in under 1 hour (manual)
- [ ] Assets on map with ≤ 60s position freshness
- [ ] Geofence entry/exit alert within 60s
- [ ] 30-day history browsable for any asset
- [ ] Observation pin with note dropped in < 30s, synced org-wide
- [ ] 2–3 pilot orgs actively using it weekly

---

## Roadmap (Post-MVP)

1. **Photo attachments** on observation pins (schema already prepared)
2. Custom report builder & data export
3. Additional device/protocol support
4. Configurable alert rules
5. Self-serve signup + billing
6. Public API for integrations