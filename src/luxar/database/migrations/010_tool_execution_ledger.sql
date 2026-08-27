CREATE TABLE IF NOT EXISTS luxar_tool_executions (
    idempotency_key text PRIMARY KEY,
    session_id text NOT NULL REFERENCES luxar_agent_sessions(session_id),
    turn_id text NOT NULL REFERENCES luxar_agent_turns(turn_id),
    call_id text NOT NULL,
    tool_name text NOT NULL,
    arguments_fingerprint text NOT NULL,
    status text NOT NULL
        CHECK (status IN (
            'running', 'succeeded', 'failed', 'rejected', 'indeterminate'
        )),
    result jsonb,
    failure jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS luxar_tool_executions_turn_idx
    ON luxar_tool_executions (turn_id, created_at);
