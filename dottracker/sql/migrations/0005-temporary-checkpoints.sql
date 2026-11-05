ALTER TABLE observations ADD COLUMN kind TEXT NOT NULL DEFAULT 'observation';
ALTER TABLE observations ADD COLUMN expires_at TIMESTAMPTZ;
ALTER TABLE observations ADD CONSTRAINT observations_checkpoint_expiry CHECK (
    (kind = 'observation' AND expires_at IS NULL) OR
    (kind = 'mobile_dot_checkpoint' AND expires_at IS NOT NULL AND expires_at > observed_at)
);
