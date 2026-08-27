ALTER TABLE luxar_agent_objectives
    ADD COLUMN IF NOT EXISTS snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;
