#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
project_dir=$PWD
scratch=$(mktemp -d)
project="dottracker-test-${scratch##*.}"
project=${project,,}
compose=(docker compose --project-name "$project" --file "$scratch/compose.yml")

cleanup() {
    "${compose[@]}" down --volumes --remove-orphans || true
    rm -f -- "$scratch/compose.yml"
    rmdir -- "$scratch"
}
trap cleanup EXIT

cat > "$scratch/compose.yml" <<'YAML'
services:
  db:
    image: postgis/postgis:17-3.5
    environment:
      POSTGRES_DB: dottracker
      POSTGRES_USER: dottracker
      POSTGRES_PASSWORD: dottracker_dev
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "dottracker", "-d", "dottracker"]
      interval: 2s
      timeout: 5s
      retries: 30
  tests:
    image: python:3.12.14-slim-bookworm
    working_dir: /src
    volumes:
      - type: bind
        source: ${DOTTRACKER_TEST_SOURCE:?}
        target: /src
        read_only: true
      - type: bind
        source: ${DOTTRACKER_DATA_SOURCE:?}
        target: /data
        read_only: true
    environment:
      APP_ENV: development
      ALLOW_DEMO_SEED: "true"
      DATABASE_URL: postgresql://dottracker:dottracker_dev@db:5432/dottracker
      MIGRATION_DATABASE_URL: postgresql://dottracker:dottracker_dev@db:5432/dottracker
      JWT_SECRET: integration-test-secret-not-for-deployment-123456
      DEVICE_TOKEN: demo-device-token
      PYTHONDONTWRITEBYTECODE: "1"
YAML

export DOTTRACKER_TEST_SOURCE="$project_dir"
export DOTTRACKER_DATA_SOURCE="$(cd "$project_dir/../data" && pwd)"
"${compose[@]}" up --wait db
"${compose[@]}" exec -T db psql -U dottracker -d dottracker -v ON_ERROR_STOP=1 < sql/migrations/0001-initial.sql
"${compose[@]}" run --rm --no-deps tests sh -ec '
    python -m pip install -r requirements-test.txt
    python migrate.py
    python scripts/load_cdot_stations.py --data-dir /data
    python -m pytest -q -p no:cacheprovider
'
