-- Upgrade the first dotTracker MVP schema (0001) to the hardened MVP schema.
-- Apply atomically through migrate.py after taking a verified database backup.
ALTER TABLE users ADD CONSTRAINT users_id_org_unique UNIQUE(id,org_id);
ALTER TABLE assets ADD CONSTRAINT assets_id_org_unique UNIQUE(id,org_id);
ALTER TABLE devices ADD CONSTRAINT devices_asset_org_fk FOREIGN KEY(asset_id,org_id) REFERENCES assets(id,org_id) ON DELETE CASCADE;
ALTER TABLE devices ADD CONSTRAINT devices_one_per_asset UNIQUE(asset_id,org_id);

ALTER TABLE positions ADD COLUMN ingest_key TEXT;
UPDATE positions SET ingest_key='legacy:'||id::text WHERE ingest_key IS NULL;
ALTER TABLE positions ALTER COLUMN ingest_key SET NOT NULL;
ALTER TABLE positions ADD CONSTRAINT positions_asset_org_fk FOREIGN KEY(asset_id,org_id) REFERENCES assets(id,org_id) ON DELETE CASCADE;
ALTER TABLE positions ADD CONSTRAINT positions_asset_ingest_unique UNIQUE(asset_id,ingest_key);
ALTER TABLE positions ADD CONSTRAINT positions_id_org_unique UNIQUE(id,org_id);

ALTER TABLE geofences ADD CONSTRAINT geofences_id_org_unique UNIQUE(id,org_id);
ALTER TABLE geofence_events ADD COLUMN position_id BIGINT;
UPDATE geofence_events e SET position_id=(
    SELECT p.id FROM positions p WHERE p.asset_id=e.asset_id
    ORDER BY abs(extract(epoch FROM (p.recorded_at-e.occurred_at))), p.id LIMIT 1
) WHERE position_id IS NULL;
ALTER TABLE geofence_events ALTER COLUMN position_id SET NOT NULL;
ALTER TABLE geofence_events ADD CONSTRAINT geofence_events_fence_org_fk FOREIGN KEY(geofence_id,org_id) REFERENCES geofences(id,org_id) ON DELETE CASCADE;
ALTER TABLE geofence_events ADD CONSTRAINT geofence_events_asset_org_fk FOREIGN KEY(asset_id,org_id) REFERENCES assets(id,org_id) ON DELETE CASCADE;
ALTER TABLE geofence_events ADD CONSTRAINT geofence_events_position_org_fk FOREIGN KEY(position_id,org_id) REFERENCES positions(id,org_id) ON DELETE CASCADE;
ALTER TABLE geofence_events ADD CONSTRAINT geofence_events_id_org_unique UNIQUE(id,org_id);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM users GROUP BY lower(email) HAVING count(*)>1) THEN
        RAISE EXCEPTION 'Resolve duplicate user emails differing only by case before migration';
    END IF;
END $$;
CREATE UNIQUE INDEX users_email_lower_idx ON users(lower(email));
ALTER TABLE observations ADD CONSTRAINT observations_asset_org_fk FOREIGN KEY(asset_id,org_id) REFERENCES assets(id,org_id) ON DELETE SET NULL (asset_id);
ALTER TABLE observations ADD CONSTRAINT observations_author_org_fk FOREIGN KEY(author_id,org_id) REFERENCES users(id,org_id);
ALTER TABLE observations ADD COLUMN client_id UUID;
ALTER TABLE observations ADD CONSTRAINT observations_client_idempotency_unique UNIQUE(org_id,author_id,client_id);
ALTER TABLE alerts ADD CONSTRAINT alerts_asset_org_fk FOREIGN KEY(asset_id,org_id) REFERENCES assets(id,org_id) ON DELETE CASCADE;
ALTER TABLE alerts ADD CONSTRAINT alerts_geofence_org_fk FOREIGN KEY(geofence_id,org_id) REFERENCES geofences(id,org_id) ON DELETE CASCADE;
ALTER TABLE alerts ADD CONSTRAINT alerts_event_org_fk FOREIGN KEY(event_id,org_id) REFERENCES geofence_events(id,org_id) ON DELETE CASCADE;

CREATE TABLE login_attempts (throttle_key TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0, blocked_until TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE live_tickets (ticket_hash TEXT PRIMARY KEY, user_id BIGINT NOT NULL, org_id BIGINT NOT NULL, expires_at TIMESTAMPTZ NOT NULL, FOREIGN KEY(user_id,org_id) REFERENCES users(id,org_id) ON DELETE CASCADE);
CREATE TABLE invites (id BIGSERIAL PRIMARY KEY, org_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE, email TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','viewer')), token_hash TEXT NOT NULL UNIQUE, invited_by BIGINT NOT NULL, expires_at TIMESTAMPTZ NOT NULL, accepted_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), FOREIGN KEY(invited_by,org_id) REFERENCES users(id,org_id) ON DELETE CASCADE, UNIQUE(org_id,email));
CREATE TABLE user_sessions (session_hash TEXT PRIMARY KEY, user_id BIGINT NOT NULL, org_id BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL, FOREIGN KEY(user_id,org_id) REFERENCES users(id,org_id) ON DELETE CASCADE);
