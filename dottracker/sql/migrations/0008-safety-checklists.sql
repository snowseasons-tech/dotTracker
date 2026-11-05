CREATE TABLE IF NOT EXISTS safety_checklists (
    org_id BIGINT NOT NULL,
    author_id BIGINT NOT NULL,
    submission_id UUID NOT NULL,
    template_version INTEGER NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    answers JSONB NOT NULL,
    notes TEXT NOT NULL,
    PRIMARY KEY (org_id, author_id, submission_id),
    FOREIGN KEY (author_id, org_id) REFERENCES users(id, org_id)
);
CREATE INDEX IF NOT EXISTS safety_checklists_org_received_idx
    ON safety_checklists (org_id, received_at DESC);
