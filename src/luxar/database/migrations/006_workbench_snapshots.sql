CREATE TABLE IF NOT EXISTS luxar_workbench_snapshots (
    project_key text PRIMARY KEY,
    workflow_family text NOT NULL,
    thread_id text NOT NULL,
    snapshot jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS luxar_workbench_snapshots_family_idx
    ON luxar_workbench_snapshots (workflow_family, updated_at DESC);
