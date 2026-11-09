# Changelog

## 3.2.1 — MapLibre served by the app

- MapLibre GL JS 6.16.0 is now vendored under `app/static/vendor/maplibre-gl-6.16.0/`: unmodified files from the npm package, with their license and recorded SHA-256 hashes. The map no longer loads code from unpkg at runtime, so a CDN outage or change can't break or alter it.
- The Content Security Policy no longer allows any third-party script, style or worker source: `script-src 'self'`, `style-src 'self' 'unsafe-inline'`, `worker-src 'self' blob:`. That reverses the unpkg allowance 3.1.1 had to add. Base-map tiles still load from OpenFreeMap.
- Tests check that the vendored files match their recorded hashes, that the app references them, that they are served with the right types, and that the policy names no CDN.

## 3.2.0 — Saved geofences on the map

- Every saved geofence is now outlined on the map. Polygons use their corners, and circles are drawn at their true ground radius. Before this, only a fence you were drawing was visible.
- Click a geofence to see its name, shape and size, and whether alerts are on. Admins can **Delete geofence** from the popup; the fence's past alerts are deleted with it. Points and symbols above a fence keep their own popups.
- New **▢ Geofence areas** layer toggle. **Find existing** now zooms to fit every geofence instead of flying to one, and a newly saved fence appears right away. The list also refreshes every minute.
- Added an API test for listing and deleting geofences: workspace-scoped, and delete is admin-only.

## 3.1.1 — Map loads under the Content Security Policy

- Fixed: in a browser, the map never finished loading, so nothing added after load appeared. That includes checkpoint symbols, the CDOT stations layer and inspection sites. MapLibre 6 starts a `blob:` module worker that imports its worker code from `https://unpkg.com/maplibre-gl@6.16.0/`, and the CSP's `worker-src` allowed only `blob:`. `worker-src` now also allows that exact MapLibre path, and a test checks the header.

## 3.1.0 — Non-permanent safety inspection stations

- Added migration `0010-inspection-sites`: workspace-scoped inspection sites with an active window of up to 30 days. The API schema gate now requires it.
- Added `GET /api/v1/inspection-sites`, plus admin-only `POST /api/v1/inspection-sites` and `POST /api/v1/inspection-sites/{id}/end`. Changes are broadcast over the live connection.
- Map: blue octagon symbols with a popup (name, agency, highway and milepost, active window, notes, and End now for admins), an **⬢ Inspection sites** layer toggle, and a list in the **Stations** tab. Admins add sites from the Drop observation form, with highway and milepost prefilled from the nearest CDOT milepoint.

## 3.0.0 — CDOT stations

- Added migration `0009-cdot-stations`, with PostGIS tables for CDOT ports of entry, highway milepoints and maintenance patrol sections, plus a `cdot_datasets` load record. The API schema gate now requires it.
- Added `scripts/load_cdot_stations.py`, a validating, transactional, re-runnable loader for the CSVs in `data/`. Milepoints are converted from NAD83 / UTM 13N to WGS 84.
- Docker Compose now runs `db` → `migrate` → `cdot-stations` → `api` locally. The production profile gains a `cdot-stations` one-shot under the `migration` profile.
- Added read-only endpoints: `GET /api/v1/cdot/stations`, `GET /api/v1/cdot/locate`, `GET /api/v1/cdot/summary`. `GET /api/config` reports the app version.
- Dashboard: added the **▲ CDOT stations** map layer, a **Stations** tab, and a highway milepoint / patrol lookup for the selected asset.
- CI loads the real CDOT data and runs the new loader and API tests.
