# Changelog

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
