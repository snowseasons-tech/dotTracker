-- dotTracker v3.7: account management. A disabled user (disabled_at set) cannot sign in or
-- refresh a session, and their existing access tokens stop working on the next request.
ALTER TABLE users ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMPTZ;

-- Admin-issued, one-use password reset links. Only the SHA-256 of the token is stored.
CREATE TABLE IF NOT EXISTS password_resets (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    requested_by BIGINT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY(user_id,org_id) REFERENCES users(id,org_id) ON DELETE CASCADE,
    FOREIGN KEY(requested_by,org_id) REFERENCES users(id,org_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS password_resets_pending_idx ON password_resets (user_id) WHERE used_at IS NULL;
