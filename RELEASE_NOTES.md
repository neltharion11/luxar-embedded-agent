# LUXAR development snapshot — 2026-08-30

This snapshot records the current state of the `codex/luxar-full-pipeline`
branch before the next document-parsing and RAG architecture iteration.

## Highlights

### Continuous Agent and Web experience

- Added streaming model commentary, bounded model-facing tool results, durable
  conversation events, tool-call idempotency, approval recovery and clearer
  failure propagation.
- Added structured error cards and progress feedback to the Web UI, including
  model, tool, policy, validation, service and timeout failures.
- Added persisted model configuration for OpenAI-compatible providers, with
  configurable thinking mode and context-window settings.
- Expanded agent verification and recovery coverage across application,
  persistence and Web API layers.

### RAG knowledge pipeline

- Added structured `ParameterAtomDraft` knowledge for registers, commands,
  timing, pin mappings, dimensions and ordered command sequences.
- Added source-excerpt and byte-location gates for parameter extraction,
  including `0xAE` / `AEH` notation normalization.
- Added parameter-aware lexical ranking for hexadecimal and register-like
  tokens alongside vector retrieval.
- Added chip/device hardware entities, deterministic entity identifiers,
  candidate discovery, approval-gated registration, orphan reattachment and
  device-to-chip knowledge aggregation.
- Added reusable evaluation scripts for extraction and recall comparisons.

### Hardware specifications and display tooling

- Added chip-skill schema v2 with generic initialization data and an optional
  display-specific block.
- Added built-in specifications for SH1106, SSD1306, SSD1315, PCD8544, ST7735,
  ST7789, ILI9341, ILI9488 and HD44780.
- Added deterministic font extraction/export, embedded U8g2 bitmap fonts,
  generated C headers and layout-aware display self-checks.
- Added CRC/SHA-anchored display verification and controller-layout checks.

### Driver reuse and device interaction

- Added a local reusable driver library with search, read, write and Web API
  integration.
- Added experimental citation-style driver-byte verification.
- Added serial-terminal sessions and Web APIs for device monitoring and input.

## Validation status

- Focused RAG/entity/driver-verification suite: **50 passed**.
- Full suite on 2026-08-30: **1159 passed, 4 failed, 14 skipped**.
- Three failures are known Continuous Agent event-reducer/checkpoint baselines.
- One SQLite timestamp assertion is timing-sensitive and passes in isolation.
- `pip check` and `git diff --check` pass.

## Known limitations

- Existing knowledge data was extracted with older prompts and must be rebuilt
  before sequence-aware end-to-end behavior can be evaluated.
- Ordered-sequence provenance currently verifies byte membership, not strict
  source order or multiplicity; cross-page sequences remain unresolved.
- `driver.verify` is experimental: references are currently supplied by the
  caller rather than resolved server-side by knowledge ID, and source scanning
  is lexical rather than a complete C semantic analysis.
- Parameter reranking operates on vector-search candidates and therefore does
  not yet guarantee global exact-token recall.
- Scanned manuals still require a dedicated OCR/document-parsing path. MinerU
  integration is under design and is not included in this snapshot.

## Upgrade note

This is a development snapshot rather than a stable release. Preserve the
current knowledge database and perform future extraction experiments in a new,
isolated index before migrating production data.
