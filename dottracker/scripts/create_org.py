#!/usr/bin/env python3
"""Provision a tenant and its first administrator without exposing a password in argv."""
import argparse
import getpass
import os
import re
import sys

import psycopg
from argon2 import PasswordHasher


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True, help='Organization display name')
    parser.add_argument('--admin-email', required=True, help='Initial administrator email address')
    args = parser.parse_args()
    name = args.name.strip()
    email = args.admin_email.strip().lower()
    if not name or len(name) > 200:
        parser.error('organization name must be 1–200 characters')
    if len(email) > 320 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        parser.error('provide a valid administrator email address')
    first = getpass.getpass('Set administrator password (minimum 14 characters): ')
    second = getpass.getpass('Repeat password: ')
    if first != second:
        parser.error('password entries do not match')
    if len(first) < 14 or len(first) > 1024:
        parser.error('password must be between 14 and 1024 characters')
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        parser.error('DATABASE_URL must be set')
    try:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute('INSERT INTO organizations(name) VALUES(%s) RETURNING id', (name,))
                org_id = cur.fetchone()[0]
                cur.execute('INSERT INTO users(org_id,email,password_hash,role) VALUES(%s,%s,%s,%s)',
                            (org_id, email, PasswordHasher().hash(first), 'admin'))
        print(f'Created organization {name!r} (id={org_id}) with admin {email}.')
        return 0
    except psycopg.errors.UniqueViolation:
        print('That email is already registered; no organization was created.', file=sys.stderr)
        return 2
    except psycopg.Error as exc:
        print(f'Database provisioning failed: {exc.__class__.__name__}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
