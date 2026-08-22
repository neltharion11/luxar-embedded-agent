CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS luxar_knowledge_documents (
    id uuid PRIMARY KEY,
    project_key text NOT NULL,
    source_uri text NOT NULL,
    title text NOT NULL,
    content_hash text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_key, source_uri, content_hash)
);

CREATE TABLE IF NOT EXISTS luxar_knowledge_chunks (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES luxar_knowledge_documents(id) ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    content text NOT NULL,
    token_count integer NOT NULL CHECK (token_count >= 0),
    embedding vector(1536) NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', content)
    ) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, ordinal)
);

CREATE INDEX IF NOT EXISTS luxar_chunks_text_idx
    ON luxar_knowledge_chunks USING gin (search_vector);

CREATE INDEX IF NOT EXISTS luxar_chunks_embedding_idx
    ON luxar_knowledge_chunks USING hnsw (embedding vector_cosine_ops);
