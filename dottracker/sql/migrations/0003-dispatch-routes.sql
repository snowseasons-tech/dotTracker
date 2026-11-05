CREATE TABLE IF NOT EXISTS dispatch_routes (
    asset_id BIGINT PRIMARY KEY,
    org_id BIGINT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1,
    plan JSONB NOT NULL,
    FOREIGN KEY (asset_id, org_id) REFERENCES assets(id, org_id) ON DELETE CASCADE
);
