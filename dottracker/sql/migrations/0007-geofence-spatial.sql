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
