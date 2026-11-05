#!/usr/bin/env python3
"""Apply the next transactional PostgreSQL schema migration, if needed."""
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent
VERSION = '0002-hardened-mvp'


def main() -> None:
    migration_url = os.getenv('MIGRATION_DATABASE_URL')
    if os.getenv('APP_ENV', 'development').lower() == 'production' and not migration_url:
        raise SystemExit('Production requires MIGRATION_DATABASE_URL; the app database role cannot run migrations')
    database_url = migration_url or os.getenv('DATABASE_URL')
    if not database_url:
        raise SystemExit('Set MIGRATION_DATABASE_URL (preferred) or DATABASE_URL')
    if os.getenv('APP_ENV', 'development').lower() == 'production':
        modes=parse_qs(urlsplit(database_url).query).get('sslmode',[])
        if len(modes)!=1 or modes[0] not in ('require','verify-full'):
            raise SystemExit('Production MIGRATION_DATABASE_URL must set sslmode=require or verify-full exactly once')

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        conn.execute('SELECT pg_advisory_xact_lock(841257301)')
        _apply_next(conn)
        for version in ('0003-dispatch-routes', '0004-dispatch-progress', '0005-temporary-checkpoints', '0006-checkpoint-spatial', '0007-geofence-spatial', '0008-safety-checklists'):
            if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=%s', (version,)).fetchone():
                conn.execute((ROOT / 'sql' / 'migrations' / f'{version}.sql').read_text())
                conn.execute('INSERT INTO schema_migrations(version) VALUES(%s)', (version,))
                print(f'Applied {version}')


def _apply_next(conn) -> None:
    conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())')
    exists = conn.execute('SELECT 1 FROM schema_migrations WHERE version=%s', (VERSION,)).fetchone()
    if exists:
        print(f'{VERSION} is already applied')
        return

    # A fresh install applies the current baseline from sql/schema.sql.
    current = conn.execute("""SELECT 1 FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name='positions' AND column_name='ingest_key'""").fetchone()
    if current:
        conn.execute('INSERT INTO schema_migrations(version) VALUES(%s)', (VERSION,))
        print(f'Recorded existing current schema as {VERSION}')
        return

    script = (ROOT / 'sql' / 'migrations' / f'{VERSION}.sql').read_text()
    try:
        conn.execute(script)
        conn.execute('INSERT INTO schema_migrations(version) VALUES(%s)', (VERSION,))
    except psycopg.Error as exc:
        conn.rollback()
        raise SystemExit(f'Migration {VERSION} failed and was rolled back: {exc.__class__.__name__}') from exc
    print(f'Applied {VERSION}')


if __name__ == '__main__':
    main()
