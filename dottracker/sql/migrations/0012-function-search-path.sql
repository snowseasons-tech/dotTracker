-- dotTracker v3.5: pin geofence_polygon()'s search_path. pg_dump/pg_restore run with
-- an empty search_path, so the unqualified GEOMETRY type and ST_* calls failed while
-- restoring the generated geofences.geom column and every geofence was dropped from
-- the restored copy. Found in the v3.5.0 staging restore rehearsal.
ALTER FUNCTION geofence_polygon(JSONB) SET search_path = public, pg_temp;
