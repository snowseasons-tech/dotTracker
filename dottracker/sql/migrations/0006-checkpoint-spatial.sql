ALTER TABLE observations ADD COLUMN IF NOT EXISTS geom GEOMETRY(Point, 4326)
    GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lng, lat), 4326)) STORED;
CREATE INDEX IF NOT EXISTS observations_checkpoint_geom_idx ON observations USING GIST (geom)
    WHERE kind = 'mobile_dot_checkpoint';
CREATE INDEX IF NOT EXISTS observations_checkpoint_org_expiry_idx ON observations (org_id, expires_at)
    WHERE kind = 'mobile_dot_checkpoint';
