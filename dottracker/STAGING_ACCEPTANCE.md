# Staging acceptance runbook

This runbook is the executable form of section 5 of [PRODUCTION_WORKFLOW.md](PRODUCTION_WORKFLOW.md). An operator runs it against a provisioned staging environment and records every result in the release record (section 10). A release is production-eligible only when every required item below is **PASS** on the same image digest and commit SHA.

Use placeholders in every command. Do not paste real credentials, tracker tokens, or invitation tokens into chat, tickets, or this document.

## 0. Rules for the run

- Run against staging only. Never point these steps at a production database or customer data.
- Use fictional names and addresses. Enter the administrator password only at the `create_org.py` prompt in section 7.
- Record `NOT RUN` with the reason when a prerequisite is missing. Do not mark it `PASS`.
- Any failed **required** item blocks production. A failed item may be accepted only with a named owner and a written risk decision in section 10.
- Stop and escalate immediately if you see cross-tenant data, a demo credential accepted outside development, or a secret in logs.

## 1. Variables and prerequisites

Set these in the operator shell. Keep secret values out of shell history by reading them from the secret store.

```bash
export STAGING_URL="https://staging.example.internal"   # private TLS ingress hostname
export STAGING_HOST_DB_URL="<runtime DATABASE_URL from the secret store>"
export MIGRATION_DATABASE_URL="<owner-level URL from the secret store; host-side only>"
export RELEASE_IMAGE="<registry>/dottracker@sha256:<digest>"
export RELEASE_SHA="<git commit SHA>"
```

Prerequisites (all required):

| ID | Prerequisite | Evidence to record |
|---|---|---|
| P1 | PostgreSQL 17 + PostGIS 3.5 with private access, TLS, automated backups, and PITR | Provider, instance ID, PITR window |
| P2 | Separate `dottracker_app` (DML only) and `dottracker_migrator` roles per section 2 of the README | Role grants output |
| P3 | TLS edge with request size limits, trusted forwarded headers, and login/ingest rate limits | Edge config export or screenshot ID |
| P4 | Service reachable only from private ingress; port 8000 not public | Port scan result from outside the network |
| P5 | Log shipping, health monitoring, and an alert channel with a named owner | Alert owner name and test alert ID |
| P6 | Staging secrets stored in the host or a secret manager | Secret names only, never values |
| P7 | Image built from `RELEASE_SHA` and pushed by digest | `RELEASE_IMAGE` value |

## 2. Baseline checks

Record the release identifiers first.

```bash
git -C dottracker rev-parse HEAD            # must equal RELEASE_SHA
docker image inspect "$RELEASE_IMAGE" --format '{{.Id}}'
```

| ID | Check | Command or method | Pass criteria |
|---|---|---|---|
| B1 | Repo checks on the release SHA | Run the CI job, or the commands in section 3 of [PRODUCTION_WORKFLOW.md](PRODUCTION_WORKFLOW.md) | All pass, pip-audit has no unresolved critical/high |
| B2 | Image scan | Run the organization's container scanner on `RELEASE_IMAGE` | No unresolved critical/high findings |
| B3 | Secrets not in image | `docker run --rm --entrypoint sh "$RELEASE_IMAGE" -c 'env; ls -la /app'` | No `.env*`, keys, or demo tokens outside development values |

## 3. Schema and migrations

Run as the migration role, on the staging database, after a verified backup (section 4).

```bash
docker compose -f docker-compose.production.yml --profile migration run --rm migrate
```

| ID | Check | Pass criteria |
|---|---|---|
| M1 | Migration runner completes | Exit 0. Output lists each pending version once, or "already applied" for each. |
| M2 | Schema versions recorded | `psql "$MIGRATION_DATABASE_URL" -c "SELECT version FROM schema_migrations ORDER BY version"` returns `0002-hardened-mvp` through `0008-safety-checklists` |
| M3 | Migrator cannot run inside the API container | `docker compose -f docker-compose.production.yml exec api env \| grep -c MIGRATION_DATABASE_URL` returns `0` |
| M4 | Runtime role has no DDL | `psql "$STAGING_HOST_DB_URL" -c "CREATE TABLE zz_probe(id int)"` fails with permission denied |

Record the migration output and the `schema_migrations` listing in the release record.

## 4. Backup and restore rehearsal

Take a backup before any migration in section 3.

| ID | Check | Pass criteria |
|---|---|---|
| R1 | Provider backup created before migration | Backup ID and timestamp recorded, retention set |
| R2 | Restore into an isolated database | Restore completes into a new instance, never over staging |
| R3 | Restored schema versions | Same `schema_migrations` rows as M2 |
| R4 | Restored data checks | Row counts for `organizations`, `users`, `assets`, `positions`, `observations`, and `geofences` match the pre-restore snapshot taken at the time of backup |
| R5 | App starts against restored database | API `/health` returns `{"status":"ok"}` when pointed at the restored database, then the restored instance is deleted |
| R6 | Point-in-time recovery | A PITR restore to a time 10 minutes before a test write contains the pre-write state and not the write |

Record the restore point, the restore duration, and any measured data loss window.

## 5. Service start and health

```bash
docker compose -f docker-compose.production.yml up -d api
```

| ID | Check | Command | Pass criteria |
|---|---|---|---|
| H1 | Liveness through private ingress | `curl -sS "$STAGING_URL/health/live"` | `{"status":"ok"}` over HTTPS |
| H2 | Readiness requires the database | `curl -sS "$STAGING_URL/health"` | `{"status":"ok"}` |
| H3 | Readiness fails when the database is down | Stop the database briefly (staging only) then `curl -sS -o /dev/null -w '%{http_code}' "$STAGING_URL/health"` | Non-200 while the database is down, 200 after it recovers |
| H4 | Schema gate refuses to start | Start the API against a database missing `0008-safety-checklists` in a throwaway copy | Container exits; logs name the missing version |
| H5 | Demo seeding disabled | `curl -sS -X POST "$STAGING_URL/api/dev/seed" -o /dev/null -w '%{http_code}'` | 404 or 403, never 2xx |
| H6 | Interactive API docs disabled | `curl -sS -o /dev/null -w '%{http_code}' "$STAGING_URL/docs"` | Not 200 |
| H7 | Container hardening | `docker inspect` for the API container | `ReadonlyRootfs: true`, `CapDrop` includes `ALL`, `User` is `10001` |

## 6. TLS, ingress, and headers

| ID | Check | Command or method | Pass criteria |
|---|---|---|---|
| T1 | TLS only | `curl -sS -I "http://${STAGING_URL#https://}/"` | Redirect to HTTPS or refused; no plaintext API |
| T2 | Certificate valid | `openssl s_client -connect <host>:443 -servername <host> </dev/null 2>/dev/null \| openssl x509 -noout -dates -subject` | Valid chain, not expiring within 14 days |
| T3 | Security headers | `curl -sSI "$STAGING_URL/"` | Content-Security-Policy present; no framing; no MIME sniffing |
| T4 | Request size limit | POST a 5 MB body to `$STAGING_URL/api/observations` | Rejected at the edge or the API with 413 or 422 |
| T5 | Login rate limit at the edge | Send 50 failed logins from one source in one minute | Rate limiting response before the API accepts more attempts |
| T6 | Forwarded headers trusted only from the proxy | Send `X-Forwarded-For: 203.0.113.9` directly to the API port from outside the proxy | Client address seen by the app is not the spoofed value |

## 7. Functional acceptance

Create the first organization and administrator from the operator shell. The script prompts for the password without echoing it.

```bash
docker compose -f docker-compose.production.yml exec api \
  python scripts/create_org.py --name "Staging Acceptance Org" --admin-email admin-acceptance@example.com
```

Export the login for the checks below. Use the password entered above.

```bash
ACCEPT_TOKEN=$(curl -sS -X POST "$STAGING_URL/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin-acceptance@example.com","password":"<password from prompt>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer $ACCEPT_TOKEN"
```

| ID | Requirement | How to verify | Pass criteria |
|---|---|---|---|
| F1 | Org and admin created, password not echoed | Output of `create_org.py` | Success message; password absent from output and shell history |
| F2 | Admin session works | `curl -sS "$STAGING_URL/api/me" -H "$AUTH"` | `role` is `admin` |
| F3 | Unauthenticated access rejected | `curl -sS -o /dev/null -w '%{http_code}' "$STAGING_URL/api/assets"` | `401` |
| F4 | Invitation issued | `POST /api/invites` with `{"email":"viewer-acceptance@example.com","role":"viewer"}` | `201`; token returned once |
| F5 | Invitation accepted once | `POST /api/invites/accept` with the token and a 14+ character password | `201` with access token |
| F6 | Invitation replay refused | Repeat the same accept call | `400` |
| F7 | Viewer cannot administer | Viewer token calls `POST /api/invites` and `POST /api/assets` | `403` on both |
| F8 | Asset created with tracker | `POST /api/assets` with `device_id`, then rotate with `POST /api/assets/{id}/device/rotate` | Credential shown once; old credential rejected after rotation |
| F9 | Tracker ingest | Run `python3 emulator.py --url "$STAGING_URL" --tracker-token <rotated token> --once` or equivalent | `200` response; a position appears in `GET /api/assets` |
| F10 | Duplicate and out-of-order fixes | Re-send one fix exactly, then one fix with an older `recorded_at` | Duplicate stored once; out-of-order fix kept as a position but not treated as a live fence transition |
| F11 | Trip history | `GET /api/assets/{id}/positions?from=...&to=...` for the last 24 hours | Returns the fixes sent in F9 and F10, sorted by time |
| F12 | Circle geofence transition | Create a circle geofence around a test fix, then send a fix inside and outside | One enter and one exit alert from `GET /api/alerts`, no duplicates on replay |
| F13 | Polygon geofence | `POST /api/v1/geofences` with a 3-corner polygon | `201`; invalid self-intersecting polygon refused |
| F14 | Observation retry is idempotent | `POST /api/observations` twice with the same `client_id` | Second call returns the first record; one row stored |
| F15 | Temporary checkpoint expiry | Create `mobile_dot_checkpoint`; query `GET /api/v1/checkpoints` in its bounding box | Visible now; `expires_at` about two hours after capture |
| F16 | Safety checklist sync | `POST /api/safety/submissions` once, then replay with the same identifier | One stored record; replay returns the original; visible in `GET /api/safety/submissions` to admins only |
| F17 | Live updates | Open the dashboard in a browser, then send a fix | Map updates without reload; `POST /api/live-ticket` ticket is one-use |
| F18 | Logout and session revocation | `POST /api/auth/logout`, then reuse the refresh token | Refresh refused with `401` |
| F19 | Tenant isolation | Create a second org with `create_org.py`; using its token, request the first org's asset, geofence, and observation IDs | `404` for every foreign ID; no data from the other org in any list |
| F20 | Dispatch route save and reconnect | `PUT /api/assets/{id}/dispatch-route` as admin, then reconnect the browser | Saved route reloaded after `sync_ready` |

## 8. Browser and failure-state checks

Test on a phone-width viewport and a desktop viewport.

| ID | Requirement | Pass criteria |
|---|---|---|
| U1 | Sign-in and invitation acceptance screens show errors from the API | An invalid password shows the server message |
| U2 | Connection state is truthful | Disconnect the network: the status shows offline, not live |
| U3 | Map tile outage | Map shows a visible error, asset list still loads |
| U4 | API unavailable | Actions show an error; nothing claims success |
| U5 | Offline safety checklist | Submit while offline, reconnect, confirm the record is sent once |
| U6 | Keyboard and focus | Sign-in, invite, and geofence forms are usable with keyboard only |
| U7 | No horizontal scroll at 375 px width | No horizontal page scroll |

## 9. Observability and alerting

| ID | Check | Pass criteria |
|---|---|---|
| O1 | Logs reach central storage | A test request appears within 5 minutes |
| O2 | Logs contain no secrets | Search logs for the test tracker token, the test password, and `Bearer ` values; none found |
| O3 | Health monitor configured | Monitor fires when `/health` fails for 2 minutes (test by stopping the API in staging) |
| O4 | Alert reaches the named owner | Test alert received; owner acknowledges |
| O5 | Metrics for latency and ingest freshness | Dashboard shows the test ingest and request latency |

## 10. Release record

Copy this table into the release record and fill every row.

| Field | Value |
|---|---|
| Release commit SHA | |
| Image digest | |
| Staging host and database instance ID | |
| Operator name | |
| Start and end time (UTC) | |
| Migration output (M1–M4) | |
| Backup ID and restore result (R1–R6) | |
| Health and TLS results (H1–H7, T1–T6) | |
| Functional results (F1–F20) | |
| Browser results (U1–U7) | |
| Observability results (O1–O5) | |
| Failed or NOT RUN items, with owner and decision | |
| Accepted medium-risk issues, with owner and expiry | |
| Approver (required before production) | |

The release is **PRODUCTION ELIGIBLE** only when every required row is PASS, no critical or high finding is open, and the approver signs off. Otherwise it stays **NOT PRODUCTION VERIFIED**.

## 11. Rollback drill

Run once in staging before the first production release.

1. Deploy the prior image digest over the current one.
2. Confirm `/health` and a sign-in with the prior image.
3. Confirm the migration state is unchanged (additive migrations are not rolled back).
4. Record elapsed time from decision to healthy service. Compare with the agreed pilot threshold.
5. Only if data integrity requires it, restore from the rehearsed backup in R2 and record the restore point and lost-write window. Do not run an unreviewed destructive schema rollback.
