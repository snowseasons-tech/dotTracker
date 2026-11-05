CREATE TABLE dispatch_progress (
    asset_id BIGINT NOT NULL REFERENCES dispatch_routes(asset_id) ON DELETE CASCADE,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'skipped')),
    changed_at TIMESTAMPTZ NOT NULL,
    mutation_id UUID NOT NULL,
    PRIMARY KEY (asset_id, job_id)
);
CREATE TABLE dispatch_mutations (
    asset_id BIGINT NOT NULL REFERENCES dispatch_routes(asset_id) ON DELETE CASCADE,
    mutation_id UUID NOT NULL,
    PRIMARY KEY (asset_id, mutation_id)
);
