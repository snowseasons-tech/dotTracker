# Staging rehearsal: v3.5.0 (2026-11-09)

A dress rehearsal of [STAGING_ACCEPTANCE.md](../STAGING_ACCEPTANCE.md) and the production path in [PRODUCTION_WORKFLOW.md](../PRODUCTION_WORKFLOW.md), run in an isolated sandbox with local stand-ins for the managed services. **Status stays NOT PRODUCTION VERIFIED**: the items marked *Not run* and *Partial* below need the real infrastructure.

## Setup

| Part | Stand-in used |
|---|---|
| Database (P1) | PostgreSQL 17 + PostGIS 3.5 container with TLS on (`sslmode=require` in every URL) and WAL archiving for point-in-time recovery |
| Roles (P2) | `dottracker_migrator` (schema owner) and `dottracker_app` (DML only, via default privileges), as in the README |
| Existing data | v3.4.0 installed the documented way (baseline schema, migration runner, CDOT loader as the migrator) in production mode, with 2 orgs, 3 users, 7 vehicles with their own tracker credentials, 517 GPS fixes along real I-25/I-70 mileposts, 5 geofences, 18 alerts (9 read), 10 observations, 2 inspection sites |
| Release (P7) | `dottracker:3.5.0-rc` built from this branch; the sandbox build adds its proxy CA for `pip` only. Ran with the repo's `docker-compose.production.yml` (one-shot `migrate` and `cdot-stations`, then `api`) |
| TLS edge (P3) | nginx 1.27: self-signed certificate, HTTP→HTTPS redirect, 1 MB body limit, login rate limit (10/min, burst 5), `X-Forwarded-For`/`X-Forwarded-Proto` |

## Results

**Bugs found and fixed in this release** (each has a regression test that fails on the old code):

1. **R4 — backups lost every geofence.** `pg_restore` runs with an empty `search_path`; `geofence_polygon()`, which computes `geofences.geom`, used unqualified PostGIS names, so the `geofences` COPY failed and the restored copy had 0 of 5 geofences. Fixed by migration `0012-function-search-path`. Re-run: 5 of 5 restored, all counts match.
2. **F10/F12 — delayed fixes raised false alerts.** An out-of-order fix (25 min old, inside a fence) produced an `enter` alert. Now stored for trip history but not evaluated for fence transitions.
3. **F19 — foreign trip history returned `200 []`** instead of 404. No data was exposed (the query was already org-scoped); now 404.
4. **U3 — base-map outage left a blank map with no message.** The map now falls back to the built-in offline map and says so.

| ID | Result | Evidence |
|---|---|---|
| P1 | Partial | TLS + WAL archiving on a container; not a managed, private instance with provider backups |
| P2 | Pass | Separate roles; runtime role has DML on the new `alert_reads` table, no DDL (M4) |
| P3 | Partial | Body limit, login rate limit, forwarded headers on nginx; no ingest rate limit; self-signed cert |
| P4 | Not run | No outside network to port-scan from (API port is bound to 127.0.0.1 by the compose file) |
| P5 | Not run | No log shipping, monitoring or alert owner in the sandbox |
| P6 | Partial | Secrets in a host env file, not a secret manager |
| P7 | Partial | Local image, not pushed by digest |
| B1 | Pass | Full suite 47 passed (three consecutive runs on one database); `pip-audit -r requirements.txt`: no known vulnerabilities |
| B2 | Not run | No container scanner available |
| B3 | Pass | `/app` holds only app, migrate.py, scripts, sql, requirements; no `.env*`, keys or secret-like env vars |
| M1 | Pass | Exit 0; applied only `0011-alert-reads` (then `0012-function-search-path` after the R4 fix) |
| M2 | Pass | `0002` … `0012` recorded; CDOT datasets stations 16, milepoints 9006, patrols 579 |
| M3 | Pass | `MIGRATION_DATABASE_URL` not set in the API container |
| M4 | Pass | `CREATE TABLE` as the runtime role: permission denied for schema public |
| 0011 | Pass | 9 workspace-wide read alerts → 18 per-person rows (9 alerts × 2 members); admin and viewer each see 9 read; a viewer marking one more leaves the admin's view unchanged |
| R1 | Pass | Physical base backup (`pg_basebackup`) and logical dump taken before migrating. **Note:** a full-database `pg_dump` as the migrator fails on the `tiger` schema the PostGIS image installs as superuser; dump schema `public` (`-n public`) or rely on provider snapshots |
| R2 | Pass | Restored into a separate instance, never over staging; 0.3 s for this data size |
| R3 | Pass | Same `schema_migrations` rows as at backup time |
| R4 | Fail → fixed | First run: geofences 0 of 5 (bug 1). After `0012`: orgs 2, users 3, assets 7, positions 517, observations 10, geofences 5, alerts 18: all match |
| R5 | Pass | `/health` ok against the restored copy (v3.4.0 on the pre-migration copy, v3.5.0 on the post-fix copy) |
| R6 | Pass | PITR from the base backup + archived WAL to a point 3 s before a marked test write: recovery stopped before that commit, the marker row is absent, counts equal the pre-write snapshot; promoted in about 1 s |
| H1–H2 | Pass | `/health/live` and `/health` ok over HTTPS |
| H3 | Pass | Database stopped: `/health` 503, `/health/live` 200; back to 200 after recovery, no API restart |
| H4 | Pass | v3.5.0 against a copy missing `0011`: exits 3 with "missing migrations: 0011-alert-reads" |
| H5–H6 | Pass | `/api/dev/seed`, `/docs`, `/openapi.json` all 404 |
| H7 | Pass | Read-only rootfs, `CapDrop=[ALL]`, user 10001, no-new-privileges, 512 MB limit |
| T1 | Pass | HTTP → 301 to HTTPS |
| T2 | Partial | Valid self-signed chain for 30 days; a real CA certificate is still needed |
| T3 | Pass | CSP, `X-Frame-Options`, `X-Content-Type-Options`, HSTS |
| T4 | Pass | 5 MB body → 413 at the edge |
| T5 | Pass | 50 failed logins in a burst: 5 × 401, then 45 × 429 from the edge |
| T6 | Pass | Six spoofed `X-Forwarded-For` values sent straight to the API port were all treated as one client (locked out after 5). **Note:** the API trusts forwarded headers only from 127.0.0.1, so behind a separate proxy it sees the proxy's address for everyone. Set `--forwarded-allow-ips` to the real proxy address and re-run T6 |
| F1 | Pass | Org and admin created; password not printed. Without a TTY, `getpass` warns that input may be echoed, so run `create_org.py` through `docker compose exec` (allocates a TTY) as documented |
| F2–F11 | Pass | Admin session, 401 unauthenticated, invite issued/accepted/replay refused, viewer 403, credential rotation (old rejected), ingest, duplicate stored once, trip history sorted |
| F12 | Fail → fixed | Bug 2; re-run: one enter and one exit, no alert from the delayed fix |
| F13–F18 | Pass | Polygon 201 / bow-tie 422, observation retry idempotent, checkpoint expires after 2.00 h, safety checklist replay + admin-only review, live update in the browser without reload, live ticket refused on reuse (code 4401), refresh token refused after logout |
| F19 | Fail → fixed | Bug 3; re-run: 404 for the foreign asset's history, geofence and dispatch route; no foreign rows in any list |
| F20 | Partial | Route saved and read back via the API; browser reconnect not exercised |
| U1 | Pass | Wrong password shows "Email or password is incorrect" |
| U3 | Fail → fixed | Bug 4; re-run with OpenFreeMap unreachable: offline map shown, all app layers present, status says "Base map unavailable" |
| U7 | **Fail (open)** | At 375 px the page is 685 px wide (the 350 px sidebar plus the map toolbar). Needs a phone layout |
| U2, U4–U6 | Not run | Network-disconnect, API-down, offline checklist and keyboard-only checks need a manual browser session |
| O1–O5 | Not run | No log storage, monitors or alert channel in the sandbox |
| Rollback | Pass | v3.4.0 image over v3.5.0 on the migrated database: healthy in 1.7 s, sign-in works, migration state unchanged; rolled forward again. **Note:** read marks made while rolled back go to the old workspace-wide column and don't carry forward |

## What is left for real infrastructure

1. **Managed PostGIS 17 + 3.5** with private access, TLS, automated backups and PITR (P1). Re-run R1–R6 against provider snapshots and PITR, and record the backup ID, restore duration and data-loss window.
2. **Real TLS edge** with a CA-issued certificate (T2), the login *and ingest* rate limits (P3), and `--forwarded-allow-ips` set to the proxy's address; then re-run T5 and T6 from outside the network, and the external port scan (P4).
3. **Secrets in a secret manager** (P6) and the **image pushed by digest** from CI (P7), then run the organization's **container scanner** (B2) on that digest.
4. **Logs, health monitoring and an alert owner** (P5, O1–O5).
5. **Manual browser checks** U2, U4, U5, U6, and F20's reconnect, on desktop and phone.
6. **Fix U7** (no horizontal scroll at 375 px) before onboarding field crews on phones.
7. Fill in the release record (STAGING_ACCEPTANCE.md section 10) on the real staging host, with an approver.
