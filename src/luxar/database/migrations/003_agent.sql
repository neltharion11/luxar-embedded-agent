CREATE TABLE IF NOT EXISTS luxar_agent_objectives (
    project_key text PRIMARY KEY,
    objective_id text NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    objective jsonb NOT NULL,
    change_set jsonb NOT NULL,
    snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS luxar_agent_capabilities (
    project_key text NOT NULL REFERENCES luxar_agent_objectives(project_key)
        ON DELETE CASCADE,
    capability_id text NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    capability jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_key, capability_id)
);

CREATE INDEX IF NOT EXISTS luxar_agent_capabilities_revision_idx
    ON luxar_agent_capabilities (project_key, revision, capability_id);

CREATE TABLE IF NOT EXISTS luxar_agent_interactions (
    interaction_id text PRIMARY KEY,
    project_key text NOT NULL REFERENCES luxar_agent_objectives(project_key)
        ON DELETE CASCADE,
    objective_id text,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS luxar_agent_interactions_project_idx
    ON luxar_agent_interactions (project_key, created_at, interaction_id);
