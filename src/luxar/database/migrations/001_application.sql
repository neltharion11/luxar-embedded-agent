CREATE TABLE IF NOT EXISTS luxar_workflow_runs (
    thread_id text PRIMARY KEY,
    task_key text NOT NULL,
    project_name text NOT NULL,
    root_index integer NOT NULL CHECK (root_index >= 0),
    task_text text NOT NULL,
    status text NOT NULL,
    runtime_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS luxar_workflow_runs_task_idx
    ON luxar_workflow_runs (task_key, created_at DESC);

CREATE TABLE IF NOT EXISTS luxar_conversation_messages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_key text NOT NULL,
    role text NOT NULL CHECK (role IN ('user', 'assistant')),
    content text NOT NULL,
    thread_id text REFERENCES luxar_workflow_runs(thread_id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS luxar_messages_task_idx
    ON luxar_conversation_messages (task_key, id);

CREATE TABLE IF NOT EXISTS luxar_approval_requests (
    task_key text PRIMARY KEY,
    project_name text NOT NULL,
    root_index integer NOT NULL CHECK (root_index >= 0),
    thread_id text NOT NULL REFERENCES luxar_workflow_runs(thread_id) ON DELETE CASCADE,
    request jsonb NOT NULL,
    runtime_config jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'decided', 'completed', 'failed')),
    decision boolean,
    created_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS luxar_approvals_status_idx
    ON luxar_approval_requests (status, created_at);

CREATE TABLE IF NOT EXISTS luxar_project_memories (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_key text NOT NULL,
    memory_key text NOT NULL,
    memory_type text NOT NULL,
    value jsonb NOT NULL,
    source_thread_id text REFERENCES luxar_workflow_runs(thread_id) ON DELETE SET NULL,
    confidence double precision NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_key, memory_key)
);

CREATE INDEX IF NOT EXISTS luxar_memories_lookup_idx
    ON luxar_project_memories (project_key, memory_type, updated_at DESC);
