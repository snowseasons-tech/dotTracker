-- dotTracker v3: CDOT reference stations (ports of entry), highway milepoints
-- and maintenance patrol sections. These tables hold public Colorado DOT
-- reference data shared by every organization; they carry no tenant rows.
CREATE TABLE IF NOT EXISTS cdot_stations (
    id BIGINT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('port_of_entry')),
    name TEXT NOT NULL,
    highway TEXT NOT NULL,
    route TEXT CHECK (route ~ '^[0-9]{3}[A-Z]$'),
    milepost NUMERIC(7,3) NOT NULL CHECK (milepost >= 0),
    direction TEXT NOT NULL,
    phone TEXT,
    status TEXT NOT NULL CHECK (status IN ('open', 'planned')),
    lat DOUBLE PRECISION NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lng DOUBLE PRECISION NOT NULL CHECK (lng BETWEEN -180 AND 180),
    geom GEOMETRY(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lng, lat), 4326)) STORED
);
CREATE INDEX IF NOT EXISTS cdot_stations_geom_idx ON cdot_stations USING GIST (geom);

CREATE TABLE IF NOT EXISTS cdot_milepoints (
    id BIGINT PRIMARY KEY,
    route TEXT NOT NULL CHECK (route ~ '^[0-9]{3}[A-Z]$'),
    refpt NUMERIC(7,3) NOT NULL CHECK (refpt >= 0),
    geom GEOMETRY(Point, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS cdot_milepoints_geom_idx ON cdot_milepoints USING GIST (geom);
CREATE INDEX IF NOT EXISTS cdot_milepoints_route_idx ON cdot_milepoints (route, refpt);

CREATE TABLE IF NOT EXISTS cdot_patrols (
    id BIGINT PRIMARY KEY,
    route TEXT NOT NULL CHECK (route ~ '^[0-9]{3}[A-Z]$'),
    region SMALLINT NOT NULL CHECK (region BETWEEN 1 AND 9),
    begin_refpt NUMERIC(7,3) NOT NULL CHECK (begin_refpt >= 0),
    end_refpt NUMERIC(7,3) NOT NULL CHECK (end_refpt >= begin_refpt),
    cost_center TEXT NOT NULL,
    maintenance_section SMALLINT NOT NULL,
    patrol INTEGER NOT NULL,
    supervisor_area TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cdot_patrols_route_idx ON cdot_patrols (route, begin_refpt, end_refpt);

CREATE TABLE IF NOT EXISTS cdot_datasets (
    name TEXT PRIMARY KEY CHECK (name IN ('stations', 'milepoints', 'patrols')),
    source_file TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
