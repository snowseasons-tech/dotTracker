-- dotTracker v3.1: non-permanent safety inspection stations. Workspace admins
-- place them on the map for a set window; they drop off once the window ends
-- or an admin ends them early. Rows belong to one organization.
CREATE TABLE IF NOT EXISTS inspection_sites (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    agency TEXT NOT NULL,
    highway TEXT,
    milepost NUMERIC(7,3) CHECK (milepost >= 0),
    direction TEXT,
    notes TEXT NOT NULL DEFAULT '',
    lat DOUBLE PRECISION NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lng DOUBLE PRECISION NOT NULL CHECK (lng BETWEEN -180 AND 180),
    geom GEOMETRY(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lng, lat), 4326)) STORED,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT inspection_sites_window CHECK (ends_at > starts_at AND ends_at <= starts_at + interval '30 days'),
    FOREIGN KEY (created_by, org_id) REFERENCES users(id, org_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS inspection_sites_org_active_idx ON inspection_sites (org_id, ends_at) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS inspection_sites_geom_idx ON inspection_sites USING GIST (geom);
