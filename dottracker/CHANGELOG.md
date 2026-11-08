# Changelog

## 3.0.0 — CDOT stations

- Added migration `0009-cdot-stations`, with PostGIS tables for CDOT ports of entry, highway milepoints and maintenance patrol sections, plus a `cdot_datasets` load record. The API schema gate now requires it.
- Added `scripts/load_cdot_stations.py`, a validating, transactional, re-runnable loader for the CSVs in `data/`. Milepoints are converted from NAD83 / UTM 13N to WGS 84.
- Docker Compose now runs `db` → `migrate` → `cdot-stations` → `api` locally. The production profile gains a `cdot-stations` one-shot under the `migration` profile.
- Added read-only endpoints: `GET /api/v1/cdot/stations`, `GET /api/v1/cdot/locate`, `GET /api/v1/cdot/summary`. `GET /api/config` reports the app version.
- Dashboard: added the **▲ CDOT stations** map layer, a **Stations** tab, and a highway milepoint / patrol lookup for the selected asset.
- CI loads the real CDOT data and runs the new loader and API tests.
