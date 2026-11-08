#!/usr/bin/env python3
"""Load CDOT reference data (ports of entry, milepoints, maintenance patrols) into PostGIS.

The loader is idempotent: each run validates every CSV first, then replaces the
reference tables in one transaction, so a failed or partial file never leaves
half-loaded data behind. Run it after migrate.py, for example through the
`cdot-stations` Docker Compose service.
"""
import argparse
import csv
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg

# Milepoint and port-of-entry x/y columns are NAD83 / UTM zone 13N.
SOURCE_SRID = 26913
ROUTE_RE = re.compile(r'^[0-9]{3}[A-Z]$')
# Colorado's state boundary plus a small margin for stations on the line.
CO_BOUNDS = (-109.2, 36.9, -101.9, 41.1)

FILES = {
    'stations': 'POELocations_679693616010166512.csv',
    'milepoints': 'Milepoints.csv',
    'patrols': 'Maintenance_Patrols.csv',
}


class DataError(ValueError):
    pass


@dataclass(frozen=True)
class Dataset:
    name: str
    source_file: str
    sha256: str
    rows: list


def _read(path: Path):
    raw = path.read_bytes()
    text = raw.decode('utf-8-sig')
    return hashlib.sha256(raw).hexdigest(), list(csv.DictReader(text.splitlines()))


def _number(row, column, line, *, minimum=None):
    value = (row.get(column) or '').strip()
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise DataError(f'line {line}: {column} must be a number, got {value!r}') from None
    if not number.is_finite() or (minimum is not None and number < minimum):
        raise DataError(f'line {line}: {column} is out of range ({value})')
    return number


def _integer(row, column, line):
    number = _number(row, column, line, minimum=0)
    if number != number.to_integral_value():
        raise DataError(f'line {line}: {column} must be a whole number')
    return int(number)


def _text(row, column, line, *, required=True):
    value = (row.get(column) or '').strip()
    if required and not value:
        raise DataError(f'line {line}: {column} is required')
    return value


def _route(value, line):
    route = value.strip().upper()
    if not ROUTE_RE.fullmatch(route):
        raise DataError(f'line {line}: route {value!r} is not a CDOT route code such as 025A')
    return route


def highway_route(highway_number: str):
    """US-85 / I-25 highway number -> CDOT primary route code (085A / 025A)."""
    digits = highway_number.strip()
    if not digits.isdigit() or not 0 < int(digits) < 1000:
        return None
    return f'{int(digits):03d}A'


def parse_stations(rows):
    parsed, seen = [], set()
    for line, row in enumerate(rows, start=2):
        station_id = _integer(row, 'OBJECTID', line)
        if station_id in seen:
            raise DataError(f'line {line}: duplicate OBJECTID {station_id}')
        seen.add(station_id)
        lat = float(_number(row, 'Y-LAT', line))
        lng = float(_number(row, 'X-LONG', line))
        west, south, east, north = CO_BOUNDS
        if not (west <= lng <= east and south <= lat <= north):
            raise DataError(f'line {line}: station {station_id} is outside Colorado ({lat}, {lng})')
        phone = _text(row, 'Phone', line, required=False)
        planned = not re.search(r'\d{3}-\d{3}-\d{4}', phone)
        parsed.append({
            'id': station_id,
            'kind': 'port_of_entry',
            'name': _text(row, 'Port Name', line).replace('Future SIte', 'Future Site'),
            'highway': _text(row, 'Highway', line),
            'route': highway_route(_text(row, 'Highway Number', line)),
            'milepost': _number(row, 'Mile Post', line, minimum=0),
            'direction': _text(row, 'Direction', line),
            'phone': None if planned else phone,
            'status': 'planned' if planned else 'open',
            'lat': lat,
            'lng': lng,
        })
    if not parsed:
        raise DataError('no stations found')
    return parsed


def parse_milepoints(rows):
    parsed, seen = [], set()
    for line, row in enumerate(rows, start=2):
        point_id = _integer(row, 'OBJECTID', line)
        if point_id in seen:
            raise DataError(f'line {line}: duplicate OBJECTID {point_id}')
        seen.add(point_id)
        x = float(_number(row, 'X', line, minimum=0))
        y = float(_number(row, 'Y', line, minimum=0))
        if not (100_000 <= x <= 900_000 and 4_000_000 <= y <= 4_600_000):
            raise DataError(f'line {line}: milepoint {point_id} is not a Colorado UTM 13N coordinate')
        parsed.append({'id': point_id, 'route': _route(row.get('ROUTE', ''), line),
                       'refpt': _number(row, 'REFPT', line, minimum=0), 'x': x, 'y': y})
    if not parsed:
        raise DataError('no milepoints found')
    return parsed


def parse_patrols(rows):
    parsed, seen = [], set()
    for line, row in enumerate(rows, start=2):
        patrol_id = _integer(row, 'OBJECTID', line)
        if patrol_id in seen:
            raise DataError(f'line {line}: duplicate OBJECTID {patrol_id}')
        seen.add(patrol_id)
        begin = _number(row, 'refpt', line, minimum=0)
        end = _number(row, 'endrefpt', line, minimum=0)
        if end < begin:
            raise DataError(f'line {line}: endrefpt {end} is before refpt {begin}')
        region = _integer(row, 'region', line)
        if not 1 <= region <= 9:
            raise DataError(f'line {line}: region {region} is out of range')
        parsed.append({
            'id': patrol_id,
            'route': _route(row.get('ROUTE', ''), line),
            'region': region,
            'begin_refpt': begin,
            'end_refpt': end,
            'cost_center': _text(row, 'Cost_Center', line),
            'maintenance_section': _integer(row, 'Maintenance_Section', line),
            'patrol': _integer(row, 'Maintenance_Patrol', line),
            'supervisor_area': _text(row, 'SUPERVISOR_AREA', line),
        })
    if not parsed:
        raise DataError('no patrols found')
    return parsed


PARSERS = {'stations': parse_stations, 'milepoints': parse_milepoints, 'patrols': parse_patrols}


def read_datasets(data_dir: Path):
    datasets = []
    for name, filename in FILES.items():
        path = data_dir / filename
        if not path.is_file():
            raise DataError(f'{path} is missing')
        digest, rows = _read(path)
        try:
            datasets.append(Dataset(name, filename, digest, PARSERS[name](rows)))
        except DataError as exc:
            raise DataError(f'{filename}: {exc}') from None
    return datasets


def load(conn, datasets):
    """Replace the CDOT reference tables with the validated datasets in one transaction."""
    by_name = {d.name: d for d in datasets}
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(841257309)')
        conn.execute('TRUNCATE cdot_stations, cdot_milepoints, cdot_patrols')
        with conn.cursor() as cur:
            cur.executemany(
                '''INSERT INTO cdot_stations(id,kind,name,highway,route,milepost,direction,phone,status,lat,lng)
                   VALUES(%(id)s,%(kind)s,%(name)s,%(highway)s,%(route)s,%(milepost)s,%(direction)s,%(phone)s,%(status)s,%(lat)s,%(lng)s)''',
                by_name['stations'].rows)
            cur.execute('''CREATE TEMP TABLE cdot_milepoints_staging
                           (id BIGINT, route TEXT, refpt NUMERIC(7,3), x DOUBLE PRECISION, y DOUBLE PRECISION)
                           ON COMMIT DROP''')
            with cur.copy('COPY cdot_milepoints_staging(id,route,refpt,x,y) FROM STDIN') as copy:
                for row in by_name['milepoints'].rows:
                    copy.write_row((row['id'], row['route'], row['refpt'], row['x'], row['y']))
            # Source coordinates are UTM 13N; store WGS 84 like every other dotTracker geometry.
            cur.execute('''INSERT INTO cdot_milepoints(id,route,refpt,geom)
                           SELECT id, route, refpt, ST_Transform(ST_SetSRID(ST_MakePoint(x, y), %s), 4326)
                           FROM cdot_milepoints_staging''', (SOURCE_SRID,))
            cur.executemany(
                '''INSERT INTO cdot_patrols(id,route,region,begin_refpt,end_refpt,cost_center,maintenance_section,patrol,supervisor_area)
                   VALUES(%(id)s,%(route)s,%(region)s,%(begin_refpt)s,%(end_refpt)s,%(cost_center)s,%(maintenance_section)s,%(patrol)s,%(supervisor_area)s)''',
                by_name['patrols'].rows)
            for d in datasets:
                cur.execute('''INSERT INTO cdot_datasets(name,source_file,sha256,row_count,loaded_at)
                               VALUES(%s,%s,%s,%s,now())
                               ON CONFLICT(name) DO UPDATE SET source_file=EXCLUDED.source_file,
                               sha256=EXCLUDED.sha256,row_count=EXCLUDED.row_count,loaded_at=now()''',
                            (d.name, d.source_file, d.sha256, len(d.rows)))
    return {d.name: len(d.rows) for d in datasets}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--data-dir', default=os.getenv('CDOT_DATA_DIR', '/data'), type=Path)
    args = parser.parse_args(argv)
    database_url = os.getenv('MIGRATION_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not database_url:
        print('Set MIGRATION_DATABASE_URL (preferred) or DATABASE_URL', file=sys.stderr)
        return 2
    try:
        datasets = read_datasets(args.data_dir)
    except DataError as exc:
        print(f'CDOT data rejected, nothing loaded: {exc}', file=sys.stderr)
        return 1
    with psycopg.connect(database_url) as conn:
        if not conn.execute("SELECT 1 FROM schema_migrations WHERE version='0009-cdot-stations'").fetchone():
            print('Run migrate.py first: 0009-cdot-stations is not applied', file=sys.stderr)
            return 1
        counts = load(conn, datasets)
    print('Loaded CDOT stations: ' + ', '.join(f'{name}={count}' for name, count in counts.items()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
