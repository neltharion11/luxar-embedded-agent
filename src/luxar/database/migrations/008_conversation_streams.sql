CREATE TABLE IF NOT EXISTS luxar_conversation_streams (
    thread_id text PRIMARY KEY,
    task_key text NOT NULL,
    user_message text NOT NULL,
    assistant_content text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN (
            'running', 'pending_approval', 'completed', 'failed', 'interrupted'
        )),
    last_sequence bigint NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
    last_event text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS luxar_conversation_streams_task_idx
    ON luxar_conversation_streams (task_key, updated_at DESC);

CREATE TABLE IF NOT EXISTS luxar_conversation_stream_events (
    thread_id text NOT NULL REFERENCES luxar_conversation_streams(thread_id)
        ON DELETE CASCADE,
    sequence bigint NOT NULL CHECK (sequence > 0),
    event text NOT NULL,
    data jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (thread_id, sequence)
);
