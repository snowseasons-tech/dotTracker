"""Deterministic point-in-geofence helpers. Coordinates use WGS84 degrees."""
from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000.0


def inside(fence: dict, lat: float, lng: float) -> bool:
    geometry = fence['geometry']
    if fence['shape'] == 'circle':
        dlat = radians(lat - float(geometry['lat']))
        dlng = radians(lng - float(geometry['lng']))
        a = sin(dlat / 2) ** 2 + cos(radians(lat)) * cos(radians(float(geometry['lat']))) * sin(dlng / 2) ** 2
        return EARTH_RADIUS_M * 2 * asin(sqrt(min(1.0, max(0.0, a)))) <= float(geometry['radius_m'])

    # Polygon points are [longitude, latitude]. Boundary points count as inside.
    points = geometry.get('coordinates', [])
    hit = False
    previous = points[-1]
    for current in points:
        x, y = map(float, current)
        px, py = map(float, previous)
        cross = (lng - px) * (y - py) - (lat - py) * (x - px)
        if abs(cross) < 1e-10 and min(px, x) - 1e-10 <= lng <= max(px, x) + 1e-10 and min(py, y) - 1e-10 <= lat <= max(py, y) + 1e-10:
            return True
        if (y > lat) != (py > lat):
            intersection = (px - x) * (lat - y) / (py - y) + x
            if lng < intersection:
                hit = not hit
        previous = current
    return hit
