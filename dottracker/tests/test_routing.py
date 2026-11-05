import pytest
from pydantic import ValidationError

from app.routing import RouteRequest, sort_route


def test_route_minimizes_driving_and_waits_for_window():
    request = RouteRequest(
        crew_equipment=['chipper'],
        jobs=[
            {'id': 'late', 'earliest_start': 30, 'service_minutes': 10, 'equipment': ['chipper']},
            {'id': 'early', 'latest_finish': 20, 'service_minutes': 5},
        ],
        travel_minutes=[[0, 20, 5], [None, 0, 15], [None, 8, 0]],
    )
    result = sort_route(request)
    assert [stop.job_id for stop in result.stops] == ['early', 'late']
    assert result.driving_minutes == 13
    assert result.stops[1].arrival_minute == 18
    assert result.stops[1].service_start_minute == 30
    assert result.finish_minute == 40


def test_reroute_avoids_blocked_direct_leg():
    request = RouteRequest(
        jobs=[{'id': 'a'}, {'id': 'b'}],
        travel_minutes=[[0, None, 5], [None, 0, 5], [None, 12, 0]],
    )
    assert [stop.job_id for stop in sort_route(request).stops] == ['b', 'a']


def test_impossible_equipment_and_time_window():
    request = RouteRequest(
        jobs=[{'id': 'a', 'equipment': ['crane'], 'latest_finish': 5}],
        travel_minutes=[[0, 5], [None, 0]],
    )
    assert sort_route(request) is None
    request.crew_equipment = ['crane']
    assert sort_route(request).finish_minute == 5
    request.travel_minutes[0][1] = 6
    assert sort_route(request) is None


def test_rejects_wrong_matrix_size():
    with pytest.raises(ValidationError, match='Matrix must have'):
        RouteRequest(jobs=[{'id': 'a'}], travel_minutes=[[0]])
