CREATE TABLE IF NOT EXISTS luxar_agent_sessions (
    session_id text PRIMARY KEY,
    project_key text NOT NULL,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    active_objective_id text,
    context_summary text NOT NULL DEFAULT '',
    compaction_cursor bigint NOT NULL DEFAULT 0
        CHECK (compaction_cursor >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS luxar_agent_sessions_project_idx
    ON luxar_agent_sessions (project_key, updated_at DESC);

CREATE TABLE IF NOT EXISTS luxar_agent_turns (
    turn_id text PRIMARY KEY,
    session_id text NOT NULL REFERENCES luxar_agent_sessions(session_id),
    client_turn_id text NOT NULL,
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN (
            'running', 'waiting_input', 'waiting_approval',
            'completed', 'failed', 'cancelled'
        )),
    user_message text NOT NULL,
    assistant_message text NOT NULL DEFAULT '',
    failure jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, client_turn_id)
);

CREATE INDEX IF NOT EXISTS luxar_agent_turns_session_idx
    ON luxar_agent_turns (session_id, created_at DESC);
