-- dotTracker v3.5: alert read state per person instead of per workspace.
CREATE TABLE IF NOT EXISTS alert_reads (
    alert_id BIGINT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    org_id BIGINT NOT NULL,
    read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, alert_id),
    FOREIGN KEY (user_id, org_id) REFERENCES users(id, org_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS alert_reads_alert_idx ON alert_reads (alert_id);
-- Carry over the old workspace-wide read state: an alert someone had already
-- marked read stays read for everyone who was in the workspace at the time.
INSERT INTO alert_reads (alert_id, user_id, org_id, read_at)
SELECT a.id, u.id, a.org_id, a.read_at
FROM alerts a JOIN users u ON u.org_id = a.org_id
WHERE a.read_at IS NOT NULL
ON CONFLICT DO NOTHING;
