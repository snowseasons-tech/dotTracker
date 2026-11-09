# Changelog

## 3.5.0 — Configurable base map and per-person alert reads

- **Base map:** new `MAP_STYLE_URL` setting. The default is OpenFreeMap; `offline` serves a built-in map (Colorado outline and CDOT highways drawn from the milepoints) that needs no outside network; or use any `https://` style from your own tile server. `MAP_EXTRA_ORIGINS` lists any other tile, glyph or sprite hosts. The CSP's `connect-src` now allows only the configured origins, and the API refuses to start on a non-HTTPS value. New `GET /api/v1/map/offline-style.json` and `GET /api/v1/map/highways.geojson` (signed in).
- **Per-person alert reads:** migration `0011-alert-reads` adds `alert_reads`. `POST /api/alerts/read` and `GET /api/alerts` now use the signed-in person's own read state. Alerts already read workspace-wide stay read for everyone who was a member.
- Tests for per-person reads, map settings validation, the offline style and highways, and the CSP.

## 3.4.0 — Readable popups, alert read state, live geofence updates, automatic release tags

- Fixed: the vehicle popup showed light text on a white background, inheriting the page's dark-theme colour. Every map popup now uses dark text.
- **Alert read state:** new `POST /api/alerts/read` (`{"ids": [...]}`, or `{}` for all unread). The Alerts tab shows unread first with a count and **Mark all read**, and selecting an alert flies to it and marks it read. Read state is workspace-wide, matching `alerts.read_at`.
- **Live geofence updates:** creating, pausing, resuming or deleting a geofence broadcasts `geofence_changed`, and open maps refresh their geofence layer immediately instead of within a minute.
- **Automatic release tags:** `.github/workflows/release-tag.yml` runs after **dotTracker CI** passes on a push to `main` and tags the commit `v<APP_VERSION>` if that tag is new.
- Tests for the broadcasts and alert read state (workspace-scoped, sign-in required).

## 3.3.0 — Alerts on the map, pausing geofences, who placed a site

- **Alerts on the map:** the new **⚠ Alerts** layer shows the last 24 hours of geofence alerts at the fix that crossed the fence, with a popup for the alert, asset and time. `GET /api/alerts` now also returns `asset_name`, `event`, `occurred_at`, `lat` and `lng`.
- **Pause geofence alerts:** new admin-only `PATCH /api/geofences/{id}` (`{"enabled": bool}`), with **Pause alerts** and **Resume alerts** in the geofence popup. Ingest already skipped disabled fences, so a paused fence raises no alerts.
- **Who placed an inspection site:** the API returns `placed_by` (email) and `placed_at`, and the popup shows them.
- **Re-runnable tests:** accounts the tests create get a per-run suffix, so the suite passes repeatedly on one database. Before, a second run failed three tests on duplicate users.

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
