CREATE TABLE IF NOT EXISTS luxar_runtime_observation_baseline (
    singleton smallint PRIMARY KEY CHECK (singleton = 1),
    started_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO luxar_runtime_observation_baseline (singleton)
VALUES (1)
ON CONFLICT (singleton) DO NOTHING;
