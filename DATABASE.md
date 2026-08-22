# LUXAR legacy PostgreSQL compatibility guide

> The default runtime now uses SQLite + LanceDB. See `STORAGE.md` for the
> active local architecture. This document describes the optional PostgreSQL
> adapter retained for compatibility and migration work.

## Boundaries

LUXAR deliberately keeps four kinds of data separate even though they share
one PostgreSQL cluster:

| Data | Owner | Purpose |
| --- | --- | --- |
| LangGraph checkpoints | `PostgresSaver` | Exact graph state, interrupts, resume |
| Workflow/application records | LUXAR migrations | Runs, messages, approvals, queryable status |
| Project memory | LUXAR migrations | Small, structured, explicit long-term facts |
| Knowledge chunks | LUXAR + pgvector | Source-labelled RAG retrieval |

Checkpoint tables are an implementation detail of LangGraph. Application code
must not query them as conversation tables or manually migrate their schema.

## Local setup

The repository includes `compose.yaml` using the pgvector PostgreSQL image.

```powershell
docker compose up -d postgres
Copy-Item .env.example .env
# Fill DEEPSEEK_API_KEY and, for RAG, LUXAR_EMBEDDING_API_KEY.
luxar db migrate
luxar db health
luxar
```

The example DSN is local-development-only. Use a secret manager and TLS DSN
outside a developer machine. Do not commit `.env`.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `LUXAR_DATABASE_URL` | unset | Enables durable PostgreSQL mode |
| `LUXAR_DATABASE_MIN_POOL_SIZE` | `1` | Minimum application/checkpoint pool size |
| `LUXAR_DATABASE_MAX_POOL_SIZE` | `5` | Maximum application/checkpoint pool size |
| `LUXAR_DATABASE_TIMEOUT_SECONDS` | `10` | Pool acquisition/start timeout |
| `LUXAR_DATABASE_AUTO_MIGRATE` | `true` | Apply LUXAR migrations on Web startup |
| `LUXAR_DATABASE_REQUIRE_VECTOR` | `true` | Apply pgvector knowledge migration |
| `LUXAR_EMBEDDING_API_KEY` | unset | Enables knowledge ingestion and RAG |
| `LUXAR_EMBEDDING_BASE_URL` | OpenAI API | Independent compatible embedding endpoint |
| `LUXAR_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model |
| `LUXAR_EMBEDDING_DIMENSIONS` | `1536` | Must match the migration vector dimension |

## Migration lifecycle

`luxar db migrate` takes a PostgreSQL transaction-scoped advisory lock, applies
packaged `NNN_name.sql` files in lexical order, and records each version in
`luxar_schema_migrations`. It then invokes the idempotent LangGraph
`PostgresSaver.setup()` for checkpoint-owned tables.

For production deployment, run migrations as a release step and set
`LUXAR_DATABASE_AUTO_MIGRATE=false` on ordinary application replicas.

Never edit an applied migration. Add the next numbered migration instead.

## Restart recovery

Before a Web workflow starts, LUXAR writes `luxar_workflow_runs` with the same
stable `thread_id` passed to LangGraph. At an interrupt it stores the safe
approval request and the minimum runtime configuration in
`luxar_approval_requests`. The approval endpoint follows one of two paths:

1. If the original SSE worker is alive, update the durable decision and wake
   its process-local event.
2. After a restart, rebuild RuntimeContext from the durable record and resume
   the PostgreSQL checkpoint directly with `Command(resume=...)`.

The Python event is only a delivery optimization; it is not a source of truth.

## Structured memory policy

Memory keys are project-scoped and upserted by `(project_key, memory_key)`.
Records contain a type, JSON value, confidence, source workflow, optional
expiry, and timestamps. Retrieval is deterministic SQL ordered by confidence
and recency. Only explicit target-chip and serial-port selections are learned
automatically. Other memories require the memory API.

Avoid storing raw secrets, full source trees, arbitrary model conclusions, or
entire conversations as memory.

## Knowledge and RAG

Document ingestion enforces a 2 MiB UTF-8 limit, normalizes and overlaps text
chunks, generates 1536-dimensional embeddings, and transactionally replaces
the document's chunks. Search combines cosine similarity (70%) with PostgreSQL
full-text rank (30%). HNSW and GIN indexes support the two retrieval paths.

Retrieved text is passed to requirement analysis as source-labelled,
untrusted reference data. The system prompt explicitly denies instruction
authority to retrieved content to reduce indirect prompt injection risk.

## Verification

The default test suite uses fake persistence and embeddings. Real integration
tests require a disposable pgvector database:

```powershell
$env:LUXAR_TEST_DATABASE_URL = 'postgresql://luxar:luxar-local@127.0.0.1:5432/luxar'
python -m pytest -q tests/database/test_postgres_integration.py
```

The integration suite verifies application round trips, structured memory,
vector search, and checkpoint interrupt/resume across two independently opened
database runtimes.

## Operations checklist

- Use TLS and least-privilege credentials outside localhost.
- Back up both LUXAR and LangGraph-owned tables together.
- Monitor pool saturation, transaction latency, database size, and HNSW index
  growth.
- Define retention for completed checkpoints, workflow results, messages, and
  superseded knowledge documents.
- Test restore procedures; a backup that has never been restored is not proven.
