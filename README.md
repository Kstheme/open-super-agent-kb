# openkb

**A local-first knowledge base that says what needs changing when a source document changes.**

[中文](./README.zh-CN.md)

Most knowledge bases stop at retrieval: you get ten snippets and do the rest yourself. openkb stores *claims* with evidence, builds *artifacts* from those claims, and when a source document changes it tells you which conclusions — and which artifacts — are affected.

```
pipx install openkb
openkb init
openkb serve          # http://127.0.0.1:8765
```

One command to install. One file to back up. No services to run.

## Why

Traditional enterprise knowledge bases fail in four predictable ways:

| Failure | Typical setup | openkb |
|---|---|---|
| Retrieval is the destination | returns snippets, you assemble the answer | returns the artifact (checklist, report) with evidence attached |
| Answers are unattributable | cites "a document", version lost | every claim points to a document version + span |
| Knowledge rots | depends on someone remembering to update | source change → affected claims → review only those |
| Setup has a threshold | build folders, tags, taxonomies | state a goal in plain language |

## What makes it different

1. **It delivers artifacts, not search results.** Give it a goal; it decides what to read, fills its own evidence gaps, and produces a usable checklist or report.
2. **Every conclusion traces back to a source.** Not "the AI says so" — *this clause, in this section, of this version*.
3. **It knows what your change breaks.** Edit a policy; it names the affected conclusions, the clause that changed, and the artifact that needs revising.

The three are a ladder: capability → trust → durability. Together they form the demo: ask → get an artifact → change one document → see exactly what needs revision.

## How it works

- Documents are parsed into structure-aware blocks; an agent extracts claims, each bound to a span. **No span, no claim** — the ledger rejects it.
- Dependencies are not stored separately. "Which claims depend on this document" is derived from `evidence → block → version → document`, so it can never drift out of sync.
- When a file hash changes, the system computes the affected set, and the agent judges whether the change is *material*. The system owns bookkeeping; the agent owns judgment.
- Tasks are durable: every step is checkpointed, every side effect is queued, and hitting a budget cap leaves a resumable task with a gap list rather than an exception.

## Retrieval

Hybrid by default: FTS5 (trigram for Chinese) + vector search, fused with RRF, reranked. Reranking is on by default because this workload is about *few and correct*, not *many and maybe*. If the vector extension fails to load, it degrades to FTS5 + exact numpy search instead of breaking.

## Backends

| Backend | Requires | Status |
|---|---|---|
| SQLite + sqlite-vec | nothing (pipx is enough) | v0.1 default |
| SQLite + FTS5 only | nothing | v0.1 fallback, automatic |
| Postgres + pgvector | your own PG 16+ with pgvector | roadmap |
| Milvus | — | not planned, see [rejected](./docs/rejected.md) |

## Models

OpenAI-compatible endpoints only. Set `base_url` and a key — OpenAI, DeepSeek, vLLM, Ollama all work. Structured output uses JSON Schema validation with retries, not provider-specific function calling. Embeddings default to the same endpoint; a local `sentence-transformers` backend can be selected during `openkb init`.

## Data

Everything lives in `~/.openkb/`:

```
kb.db          # ledger, tasks, artifacts (single file)
originals/     # source documents, every version
config.toml
```

Backup is a directory copy. Migration is a directory copy.

## Status

Pre-alpha. The design is settled; implementation starts at M0 (evaluation harness) before the core — see [docs/design.md](./docs/design.md).

## Non-goals

No accounts, no multi-tenancy, no web crawling, no auto-applying revisions, no multi-agent chatter. Each refusal is recorded with its reason in [docs/rejected.md](./docs/rejected.md).

## License

Apache-2.0
