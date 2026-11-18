#!/usr/bin/env python3
"""Compare the two ways a database gets its schema.

A new install runs sql/schema.sql. An existing install runs sql/migrations/0001-initial.sql and then
migrate.py. Both must produce the same schema. Names, column order and migration history are ignored;
columns, types, constraints, indexes, functions and triggers are compared.

Every remaining difference must be listed in scripts/schema_path_allowlist.txt, each with a reason.
A difference that is not listed fails the check, and so does a listed difference that no longer
exists, so the allowlist cannot go stale without someone noticing.
"""
import argparse
import sys
from pathlib import Path

import psycopg

DEFAULT_ALLOWLIST = Path(__file__).with_name('schema_path_allowlist.txt')

CATALOG_SQL = r"""
SELECT 'COLUMN|' || c.relname || '|' || a.attname || '|' || format_type(a.atttypid, a.atttypmod)
       || '|notnull=' || a.attnotnull || '|default=' || coalesce(pg_get_expr(d.adbin, d.adrelid), '')
       || '|generated=' || a.attgenerated::text
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
UNION ALL
SELECT 'CONSTRAINT|' || c.relname || '|' || con.contype::text || '|' || pg_get_constraintdef(con.oid)
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
UNION ALL
SELECT 'INDEX|' || t.relname || '|' || regexp_replace(pg_get_indexdef(i.indexrelid), 'INDEX \S+ ON ', 'INDEX ON ')
FROM pg_index i
JOIN pg_class t ON t.oid = i.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
UNION ALL
SELECT 'FUNCTION|' || p.proname || '|config=' || coalesce(array_to_string(p.proconfig, ','), '')
       || '|md5=' || md5(pg_get_functiondef(p.oid))
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' AND p.prokind = 'f'
UNION ALL
SELECT 'TRIGGER|' || tgrelid::regclass::text || '|' || regexp_replace(pg_get_triggerdef(oid), 'TRIGGER \S+ ', 'TRIGGER ')
FROM pg_trigger
WHERE NOT tgisinternal
"""


def catalog(url):
    """The normalized schema of one database, as a set of comparable lines."""
    with psycopg.connect(url) as conn:
        return {row[0] for row in conn.execute(CATALOG_SQL)}


def load_allowlist(path):
    lines = path.read_text().splitlines()
    return {line for line in lines if line.strip() and not line.startswith('#')}


def compare(fresh, upgrade, allowlist):
    """Return (unexpected, stale). Lines are prefixed with the path they belong to."""
    found = {f'fresh-only: {line}' for line in fresh - upgrade}
    found |= {f'upgrade-only: {line}' for line in upgrade - fresh}
    return sorted(found - allowlist), sorted(allowlist - found)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--fresh-url', required=True, help='database built from sql/schema.sql')
    parser.add_argument('--upgrade-url', required=True, help='database built from 0001 plus migrate.py')
    parser.add_argument('--allowlist', type=Path, default=DEFAULT_ALLOWLIST)
    args = parser.parse_args()

    allowlist = load_allowlist(args.allowlist)
    unexpected, stale = compare(catalog(args.fresh_url), catalog(args.upgrade_url), allowlist)
    for line in unexpected:
        print(f'NEW DIFFERENCE (add a reason to {args.allowlist.name}, or fix it): {line}')
    for line in stale:
        print(f'STALE ALLOWLIST ENTRY (the two paths now match here; remove it): {line}')
    if unexpected or stale:
        return 1
    print(f'Fresh install and upgrade paths match, except {len(allowlist)} allowlisted difference(s).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
