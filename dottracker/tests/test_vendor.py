"""The vendored MapLibre files must stay byte-identical to the published npm package."""
import hashlib
import re
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / 'app' / 'static' / 'vendor' / 'maplibre-gl-6.16.0'


def test_vendored_maplibre_matches_recorded_hashes():
    table = dict(re.findall(r'^\| (\S+) \| ([0-9a-f]{64}) \|$', (VENDOR / 'README.md').read_text(), re.M))
    assert set(table) == {'maplibre-gl.mjs', 'maplibre-gl-worker.mjs', 'maplibre-gl.css'}
    for name, digest in table.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, name


def test_app_loads_maplibre_from_the_vendored_copy():
    static = VENDOR.parents[1]
    assert "from '/static/vendor/maplibre-gl-6.16.0/maplibre-gl.mjs'" in (static / 'app.js').read_text()
    html = (static / 'index.html').read_text()
    assert '/static/vendor/maplibre-gl-6.16.0/maplibre-gl.css' in html and 'unpkg' not in html
