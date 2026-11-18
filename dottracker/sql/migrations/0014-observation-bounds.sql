-- dotTracker v3.7: coordinate bounds on observations. sql/schema.sql has always had these checks for
-- fresh installs, but no migration created them, so upgraded databases lacked them. The API already
-- rejects out-of-range coordinates; this adds the same rule in the database. Adding a CHECK checks
-- every existing row, so the migration stops and rolls back if any stored coordinate is out of range.
ALTER TABLE observations ADD CONSTRAINT observations_lat_check CHECK(lat BETWEEN -90 AND 90);
ALTER TABLE observations ADD CONSTRAINT observations_lng_check CHECK(lng BETWEEN -180 AND 180);
