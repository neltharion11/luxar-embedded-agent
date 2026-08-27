ALTER TABLE luxar_knowledge_chunks
    ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS luxar_chunks_knowledge_id_idx
    ON luxar_knowledge_chunks ((metadata ->> 'knowledge_id'));
