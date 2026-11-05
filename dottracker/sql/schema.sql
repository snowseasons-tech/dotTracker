CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TABLE IF NOT EXISTS organizations (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS users (id BIGSERIAL PRIMARY KEY, org_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','viewer')), created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(id,org_id));
CREATE TABLE IF NOT EXISTS assets (id BIGSERIAL PRIMARY KEY, org_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'vehicle', device_id TEXT UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(id,org_id));
CREATE INDEX IF NOT EXISTS assets_org_idx ON assets(org_id);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_idx ON users(lower(email));
CREATE TABLE IF NOT EXISTS devices (id BIGSERIAL PRIMARY KEY, org_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE, asset_id BIGINT NOT NULL, device_token_hash TEXT NOT NULL, enabled BOOLEAN NOT NULL DEFAULT true, FOREIGN KEY(asset_id,org_id) REFERENCES assets(id,org_id) ON DELETE CASCADE, UNIQUE(asset_id,org_id));
CREATE TABLE IF NOT EXISTS positions (id BIGSERIAL PRIMARY KEY, org_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE, asset_id BIGINT NOT NULL, ingest_key TEXT NOT NULL, lat DOUBLE PRECISION NOT NULL CHECK(lat BETWEEN -90 AND 90), lng DOUBLE PRECISION NOT NULL CHECK(lng BETWEEN -180 AND 180), speed DOUBLE PRECISION NOT NULL DEFAULT 0, heading DOUBLE PRECISION, recorded_at TIMESTAMPTZ NOT NULL, point GEOGRAPHY(POINT,4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lng,lat),4326)::geography) STORED, FOREIGN KEY(asset_id,org_id) REFERENCES assets(id,org_id) ON DELETE CASCADE, UNIQUE(asset_id,ingest_key), UNIQUE(id,org_id));
CREATE INDEX IF NOT EXISTS positions_asset_time_idx ON positions(asset_id,recorded_at DESC);
CREATE INDEX IF NOT EXISTS positions_point_idx ON positions USING GIST(point);
CREATE TABLE IF NOT EXISTS geofences (id BIGSERIAL PRIMARY KEY, org_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE, name TEXT NOT NULL, shape TEXT NOT NULL CHECK(shape IN ('circle','polygon')), geometry JSONB NOT NULL, enabled BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(id,org_id));
CREATE TABLE IF NOT EXISTS geofence_events (id BIGSERIAL PRIMARY KEY, org_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE, geofence_id BIGINT NOT NULL, asset_id BIGINT NOT NULL, position_id BIGINT NOT NULL, event TEXT NOT NULL CHECK(event IN ('enter','exit')), occurred_at TIMESTAMPTZ NOT NULL, FOREIGN KEY(geofence_id,org_id) REFERENCES geofences(id,org_id) ON DELETE CASCADE, FOREIGN KEY(asset_id,org_id) REFERENCES assets(id,org_id) ON DELETE CASCADE, FOREIGN KEY(position_id,org_id) REFERENCES positions(id,org_id) ON DELETE CASCADE, UNIQUE(id,org_id));
CREATE TABLE IF NOT EXISTS observations (id BIGSERIAL PRIMARY KEY, org_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE, asset_id BIGINT, author_id BIGINT NOT NULL, client_id UUID, lat DOUBLE PRECISION NOT NULL CHECK(lat BETWEEN -90 AND 90), lng DOUBLE PRECISION NOT NULL CHECK(lng BETWEEN -180 AND 180), observed_at TIMESTAMPTZ NOT NULL, note TEXT NOT NULL DEFAULT '', photo_ref TEXT, kind TEXT NOT NULL DEFAULT 'observation', expires_at TIMESTAMPTZ, CONSTRAINT observations_checkpoint_expiry CHECK ((kind = 'observation' AND expires_at IS NULL) OR (kind = 'mobile_dot_checkpoint' AND expires_at IS NOT NULL AND expires_at > observed_at)), created_at TIMESTAMPTZ NOT NULL DEFAULT now(), FOREIGN KEY(asset_id,org_id) REFERENCES assets(id,org_id) ON DELETE SET NULL (asset_id), FOREIGN KEY(author_id,org_id) REFERENCES users(id,org_id), UNIQUE(org_id,author_id,client_id));
CREATE INDEX IF NOT EXISTS observations_org_time_idx ON observations(org_id,observed_at DESC);
CREATE TABLE IF NOT EXISTS alerts (id BIGSERIAL PRIMARY KEY, org_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE, asset_id BIGINT, geofence_id BIGINT, event_id BIGINT, message TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), read_at TIMESTAMPTZ, FOREIGN KEY(asset_id,org_id) REFERENCES assets(id,org_id) ON DELETE CASCADE, FOREIGN KEY(geofence_id,org_id) REFERENCES geofences(id,org_id) ON DELETE CASCADE, FOREIGN KEY(event_id,org_id) REFERENCES geofence_events(id,org_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS login_attempts (throttle_key TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0, blocked_until TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS live_tickets (ticket_hash TEXT PRIMARY KEY, user_id BIGINT NOT NULL, org_id BIGINT NOT NULL, expires_at TIMESTAMPTZ NOT NULL, FOREIGN KEY(user_id,org_id) REFERENCES users(id,org_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS invites (id BIGSERIAL PRIMARY KEY, org_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE, email TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','viewer')), token_hash TEXT NOT NULL UNIQUE, invited_by BIGINT NOT NULL, expires_at TIMESTAMPTZ NOT NULL, accepted_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), FOREIGN KEY(invited_by,org_id) REFERENCES users(id,org_id) ON DELETE CASCADE, UNIQUE(org_id,email));
CREATE TABLE IF NOT EXISTS user_sessions (session_hash TEXT PRIMARY KEY, user_id BIGINT NOT NULL, org_id BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL, FOREIGN KEY(user_id,org_id) REFERENCES users(id,org_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now());
INSERT INTO schema_migrations(version) VALUES('0002-hardened-mvp') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS dispatch_routes (
    asset_id BIGINT PRIMARY KEY,
    org_id BIGINT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1,
    plan JSONB NOT NULL,
    FOREIGN KEY (asset_id, org_id) REFERENCES assets(id, org_id) ON DELETE CASCADE
);
INSERT INTO schema_migrations(version) VALUES('0003-dispatch-routes') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS dispatch_progress (
    asset_id BIGINT NOT NULL REFERENCES dispatch_routes(asset_id) ON DELETE CASCADE,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'skipped')),
    changed_at TIMESTAMPTZ NOT NULL,
    mutation_id UUID NOT NULL,
    PRIMARY KEY (asset_id, job_id)
);
CREATE TABLE IF NOT EXISTS dispatch_mutations (
    asset_id BIGINT NOT NULL REFERENCES dispatch_routes(asset_id) ON DELETE CASCADE,
    mutation_id UUID NOT NULL,
    PRIMARY KEY (asset_id, mutation_id)
);
INSERT INTO schema_migrations(version) VALUES('0004-dispatch-progress') ON CONFLICT DO NOTHING;
INSERT INTO schema_migrations(version) VALUES('0005-temporary-checkpoints') ON CONFLICT DO NOTHING;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS geom GEOMETRY(Point, 4326)
    GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lng, lat), 4326)) STORED;
CREATE INDEX IF NOT EXISTS observations_checkpoint_geom_idx ON observations USING GIST (geom)
    WHERE kind = 'mobile_dot_checkpoint';
CREATE INDEX IF NOT EXISTS observations_checkpoint_org_expiry_idx ON observations (org_id, expires_at)
    WHERE kind = 'mobile_dot_checkpoint';
INSERT INTO schema_migrations(version) VALUES('0006-checkpoint-spatial') ON CONFLICT DO NOTHING;
CREATE OR REPLACE FUNCTION geofence_polygon(coordinates JSONB)
RETURNS GEOMETRY LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE
    ring JSONB := coordinates;
    polygon GEOMETRY;
BEGIN
    IF jsonb_typeof(ring) <> 'array' OR jsonb_array_length(ring) < 3 THEN
        RETURN NULL;
    END IF;
    IF ring->0 <> ring->-1 THEN
        ring := ring || jsonb_build_array(ring->0);
    END IF;
    polygon := ST_SetSRID(ST_GeomFromGeoJSON(jsonb_build_object(
        'type', 'Polygon', 'coordinates', jsonb_build_array(ring))), 4326);
    IF ST_IsValid(polygon) THEN
        RETURN ST_Force2D(polygon);
    END IF;
    RETURN NULL;
EXCEPTION WHEN data_exception OR internal_error THEN
    -- Early MVP records may contain malformed vertices.
    RETURN NULL;
END;
$$;

ALTER TABLE geofences ADD COLUMN IF NOT EXISTS geom GEOMETRY(Polygon, 4326)
    GENERATED ALWAYS AS (CASE WHEN shape = 'polygon' THEN geofence_polygon(geometry->'coordinates') END) STORED;
CREATE INDEX IF NOT EXISTS geofences_polygon_geom_idx ON geofences USING GIST (geom)
    WHERE geom IS NOT NULL;
INSERT INTO schema_migrations(version) VALUES('0007-geofence-spatial') ON CONFLICT DO NOTHING;
