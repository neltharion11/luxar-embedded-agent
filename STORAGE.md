# LUXAR local storage

LUXAR defaults to an embedded Windows-friendly storage profile. It starts no
database server and requires no Docker runtime.

## Ownership

| Data | Backend | Default location |
| --- | --- | --- |
| Conversations, workflow runs, approvals, project memory, workbench snapshots | SQLite | `.luxar-data/luxar.sqlite3` |
| LangGraph checkpoints | SQLite `SqliteSaver` | `.luxar-data/checkpoints.sqlite3` |
| Knowledge documents, chunks, metadata, vectors | LanceDB | `.luxar-data/knowledge.lance/` |
| Original ESP-IDF source files | Filesystem | configured project roots |

Set `LUXAR_STORAGE_DIRECTORY` to move all embedded data together. A relative
path is resolved from the process working directory.

```env
LUXAR_STORAGE_DIRECTORY=.luxar-data
```

The Web gateway creates SQLite tables and LangGraph checkpoint tables on first
startup. There is no separate migration command for a new installation.

## Health and durability

```powershell
luxar storage health
luxar storage runtime-audit
luxar storage runtime-migration-plan
```

`runtime-audit` checks the persisted observation window, workflow-family
counts, pending recovery dependencies, and checkpoint thread inventory.
`runtime-migration-plan` is a zero-write dry run for historical records that
predate `workflow_family`. Deterministic evidence such as a knowledge approval
kind, inspection trace, firmware trace, or explicit Supervisor runtime may be
proposed for migration; ambiguous records remain unchanged and block removal.

The Web endpoint returns the active application store:

```text
GET /api/health/storage
```

Expected local response:

```json
{"status":"ok","database":"sqlite","durable":true}
```

`GET /api/health/database` remains as a compatibility alias.

SQLite uses WAL mode and a separate connection per application operation.
LangGraph owns its checkpoint schema in a separate SQLite file so its internal
tables are not treated as LUXAR application records.

All checkpoint backends use LUXAR's explicit MessagePack type allowlist.
Existing checkpoints containing approved LUXAR workflow models remain
readable, while unregistered Python types are not reconstructed. Add every new
Pydantic model stored in workflow state to `luxar.checkpoint_serde` together
with a checkpoint round-trip test.

`luxar_workbench_snapshots` stores the latest bounded monitoring view for each
project independently from long-lived Supervisor objectives. Knowledge-task
snapshots contain operation metadata, progress, approval state, and bounded
result summaries; document bodies and model execution context are excluded.

## Knowledge/RAG

LanceDB is initialized only when `LUXAR_EMBEDDING_API_KEY` is configured.
Embedding settings remain independent from the DeepSeek chat model:

```env
LUXAR_EMBEDDING_API_KEY=
LUXAR_EMBEDDING_BASE_URL=https://api.openai.com/v1
LUXAR_EMBEDDING_MODEL=text-embedding-3-small
LUXAR_EMBEDDING_DIMENSIONS=1536
```

Knowledge is project-scoped. Search first retrieves vector candidates from
LanceDB, then applies a small lexical reranking contribution. Every returned
chunk retains its document title and source URI.

## Backup and recovery

Stop LUXAR before copying the storage directory. Back up the entire configured
directory rather than one file, because application records, checkpoints, and
the LanceDB index must stay consistent.

To reset only a project's visible conversation, use `/clear` in the Web UI or
the conversation reset endpoint. Do not delete SQLite or LanceDB files while
the gateway is running.

The audit and migration-plan commands are read-only. They are safe to run while
the gateway is stopped or active, but no metadata cleanup or checkpoint
creation/deletion is performed automatically.

## Optional PostgreSQL compatibility

The previous PostgreSQL implementation and migrations remain in the source
tree as an optional compatibility adapter, with dependencies available through
`.[postgres]`. It is no longer selected by the default Web or CLI startup path.
New local installations should use SQLite + LanceDB.
