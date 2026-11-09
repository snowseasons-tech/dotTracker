# dotTracker development and production release workflow

This runbook takes dotTracker from a change request through a controlled pilot release. Use it with [AGENT_WORKFLOW.md](AGENT_WORKFLOW.md) for task ownership and reviews. Run the phases serially when working alone.

## Release boundary

The app repository contains the application and deployment templates. A production service still needs an operator-provisioned host, managed PostgreSQL/PostGIS, TLS ingress, backups, monitoring, and secrets. Do not mark a release production-verified until the staging rehearsal and restore check below have passed.

The snowStore entry currently sells a subscription but does not point to this app or enforce app access. Before offering hosted access, configure an authenticated app URL and a verified entitlement handoff. The offline storefront demo is not the production app.

## 1. Start a scoped change

1. Read [AGENTS.md](AGENTS.md), this runbook, the app [README.md](README.md), and the relevant API, schema, and UI code.
2. Write the user problem, acceptance criteria, role/tenant impact, data changes, and recovery plan in the issue or pull request.
3. Create a feature branch from `main`. Keep API, UI, and schema changes in one reviewable contract; split work when independently testable.
4. Never use production data or credentials in local development. Use fictional data and the development-only seed path.

## 2. Develop locally

From `dottracker/`:

```bash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:8000`, initialize the demo workspace, and use the local GPS emulator only with the development stack:

```bash
python3 emulator.py --count 3 --interval 5
```

For every API or schema change, preserve tenant scoping and role checks, use an explicit numbered migration, update the API/UI contract, and add a regression test. Update `sql/schema.sql` for fresh installs as well as the incremental migration path.

## 3. Verify the change

Run the same checks as CI from `dottracker/` with a PostgreSQL 17 + PostGIS 3.5 service available:

```bash
python -m pip install -r requirements-test.txt
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/migrations/0001-initial.sql
python migrate.py
python -m py_compile app/main.py app/geometry.py emulator.py migrate.py scripts/create_org.py scripts/load_cdot_stations.py tests/api_test.py tests/test_cdot_loader.py
node --input-type=module --check < app/static/app.js
python -m pytest -q
pip-audit -r requirements.txt
docker build -t dottracker:review .
```

The integration tests require a disposable database. Do not point them at a customer or production database. The CI workflow also runs these checks for pull requests. Fix failures before merge; report checks that could not run instead of treating them as passed.

Review the diff for secrets, unscoped database queries, unsafe role changes, unbounded inputs, and frontend states that hide API failures. Require an independent review of authentication, tenant isolation, migrations, and offline/reconnect behavior.

## 4. Prepare staging

Provision a staging environment that matches production:

- PostgreSQL 17 with PostGIS 3.5, private access, TLS, automated backup, and point-in-time recovery.
- Separate least-privilege runtime and migration roles. Keep `MIGRATION_DATABASE_URL` out of the API service.
- TLS reverse proxy or managed edge with request-size limits, trusted forwarded headers, and distributed login/ingest rate limits.
- Restricted service ingress, persistent backup retention, centralized logs, health monitoring, and an alert owner.
- Unique staging secrets stored on the host or in a secret manager. Never commit `.env.production` or paste credentials into an issue or chat.

For a new database, apply the current baseline once with the migration role:

```bash
psql "$MIGRATION_DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/schema.sql
```

For an existing supported MVP database, do not reapply the baseline. Take and verify a backup, then let the migration runner apply the pending numbered migrations. It applies 0002 through 0012 in order and records each version.

Build and run the production migration service, then start the API:

```bash
docker compose -f docker-compose.production.yml build api migrate
docker compose -f docker-compose.production.yml --profile migration run --rm migrate
docker compose -f docker-compose.production.yml --profile migration run --rm cdot-stations
docker compose -f docker-compose.production.yml up -d api
```

The API refuses to start unless every supported schema version through `0012-function-search-path` is recorded. The `cdot-stations` one-shot loads the CDOT reference data from `../data` using the migration credential. Do not bypass this gate to make a deployment appear healthy.

## 5. Staging acceptance

Run the step-by-step checks in [STAGING_ACCEPTANCE.md](STAGING_ACCEPTANCE.md) and record the results in its release record. The summary below is the required scope:

Exercise the release through the same private ingress that production will use:

- `/health` returns success only after database connection and schema validation.
- Create the first organization and admin using `scripts/create_org.py`; confirm the password is not echoed and demo seeding is disabled.
- Verify admin/viewer permissions, invite single-use behavior, cross-tenant isolation, tracker credential rotation, duplicate/out-of-order fixes, trip history, geofence transitions, observation retry, offline safety checklist sync, and logout/session revocation.
- Confirm the browser displays truthful connection state and useful errors when the API, map tiles, or database is unavailable.
- Confirm TLS, request limits, ingress restrictions, logs, monitoring, and alert delivery.
- Restore a recent backup into an isolated staging database and verify the restored app and migration state.
- Record the image digest, commit SHA, test report, migration result, restore result, and any accepted medium-risk issues.

Any failed item blocks production release. A CI pass alone is not production verification.

## 6. Production release and rollback

Before release, confirm the production database has a fresh recoverable backup and the staging report is approved. Use the same image digest that passed staging. Apply migrations with the one-shot migration service, then start the API behind the private TLS ingress. Provision the production organization and admin through the operator shell; deliver invites and device credentials through an approved secure channel.

Smoke-test sign-in, asset list, tracker ingest, live updates, geofences, observations, and `/health`. Watch errors, response latency, database health, and ingest freshness during the pilot.

Rollback to the prior app image if the smoke test fails or errors/data freshness exceed the agreed pilot threshold. Prefer application rollback for additive migrations. Restore the database only from the rehearsed backup when data integrity requires it; record the restore point and lost-write window. Do not attempt an unreviewed destructive schema rollback.

## Current release status

The repository provides CI, a production Docker Compose template, and an ordered migration runner. Production hosting and release credentials are not configured in this repository. Until staging is provisioned and the acceptance/restore checks are recorded, release status is **NOT PRODUCTION VERIFIED**.
