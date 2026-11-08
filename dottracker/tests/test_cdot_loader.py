"""Validation tests for the CDOT stations loader (no database required)."""
import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    'load_cdot_stations', Path(__file__).resolve().parents[1] / 'scripts' / 'load_cdot_stations.py')
loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loader)

STATION = {'OBJECTID': '14', 'Y-LAT': '39.10055', 'X-LONG': '-104.863961', 'Highway Number': '25',
           'Port Name': 'Monument', 'Direction': 'NB', 'Phone': '719-481-2281', 'Highway': 'I-25',
           'Mile Post': '161.2'}
PATROL = {'OBJECTID': '1', 'ROUTE': '025a', 'region': '2', 'refpt': '155', 'endrefpt': '170.5',
          'Cost_Center': '4102', 'Maintenance_Section': '4', 'Maintenance_Patrol': '19', 'SUPERVISOR_AREA': 'John'}


def test_station_parsing_marks_open_and_planned_ports():
    planned = {**STATION, 'OBJECTID': '1', 'Port Name': 'Platteville Future SIte', 'Phone': 'Not Open Yet',
               'Highway Number': '85', 'Highway': 'US-85'}
    open_port, future = loader.parse_stations([STATION, planned])
    assert open_port['status'] == 'open' and open_port['phone'] == '719-481-2281'
    assert open_port['route'] == '025A' and open_port['milepost'] == Decimal('161.2')
    assert future['status'] == 'planned' and future['phone'] is None
    assert future['name'] == 'Platteville Future Site' and future['route'] == '085A'


def test_station_outside_colorado_is_rejected():
    with pytest.raises(loader.DataError, match='outside Colorado'):
        loader.parse_stations([{**STATION, 'Y-LAT': '45.0'}])


def test_duplicate_ids_and_bad_numbers_are_rejected():
    with pytest.raises(loader.DataError, match='duplicate OBJECTID'):
        loader.parse_stations([STATION, STATION])
    with pytest.raises(loader.DataError, match='must be a number'):
        loader.parse_stations([{**STATION, 'Mile Post': 'n/a'}])
    with pytest.raises(loader.DataError, match='out of range'):
        loader.parse_stations([{**STATION, 'Mile Post': 'NaN'}])


def test_patrol_parsing_normalizes_route_and_checks_range():
    (patrol,) = loader.parse_patrols([PATROL])
    assert patrol['route'] == '025A' and patrol['patrol'] == 19 and patrol['region'] == 2
    with pytest.raises(loader.DataError, match='before refpt'):
        loader.parse_patrols([{**PATROL, 'endrefpt': '150'}])
    with pytest.raises(loader.DataError, match='not a CDOT route code'):
        loader.parse_patrols([{**PATROL, 'ROUTE': 'I-25'}])


def test_milepoints_must_be_colorado_utm_coordinates():
    row = {'OBJECTID': '1', 'ROUTE': '025A', 'REFPT': '161', 'X': '511764.03', 'Y': '4327942.93'}
    assert loader.parse_milepoints([row])[0]['refpt'] == Decimal('161')
    with pytest.raises(loader.DataError, match='UTM 13N'):
        loader.parse_milepoints([{**row, 'X': '104.86', 'Y': '39.10'}])


def test_missing_file_rejects_whole_load(tmp_path):
    with pytest.raises(loader.DataError, match='is missing'):
        loader.read_datasets(tmp_path)


@pytest.mark.skipif(not (Path(__file__).resolve().parents[2] / 'data').is_dir(), reason='repository data/ not mounted')
def test_repository_cdot_data_validates():
    datasets = {d.name: d for d in loader.read_datasets(Path(__file__).resolve().parents[2] / 'data')}
    assert len(datasets['stations'].rows) == 16
    assert len(datasets['patrols'].rows) == 579
    assert len(datasets['milepoints'].rows) == 9006
