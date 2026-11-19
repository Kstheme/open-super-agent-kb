# openkb

**A local-first knowledge base for keeping checklists and reports up to date when their sources change.**

[中文](./README.zh-CN.md)

openkb is designed to run on one machine. Its first use case is **maintaining policy and compliance checklists**. Give it a set of policy documents and a goal. An agent extracts claims backed by evidence and assembles them into a checklist or report. Each claim traces back to a specific version and passage. When a policy changes, openkb identifies affected claims and artifact items, then proposes revisions for you to review.

**Example** Build a reimbursement checklist from a travel policy. If the lodging cap changes, openkb identifies the affected checklist item, shows the updated clause, and suggests a revision you can check against the evidence.

## Why

Traditional enterprise knowledge bases fail in four predictable ways:

| Failure | Typical setup | openkb |
|---|---|---|
| Retrieval is the destination | returns snippets, you assemble the answer | returns the artifact (checklist, report) with evidence attached |
| Answers are unattributable | cites "a document", version lost | every claim points to a document version + span |
| Knowledge rots | depends on someone remembering to update | source change → affected claims → review only those |
| Setup has a threshold | build folders, tags, taxonomies | state a goal in plain language |

## What makes it different

1. **Build a checklist or report from a goal.** The system uses an artifact outline to identify gaps in coverage. The agent finds evidence to fill those gaps and assembles the artifact.
2. **Trace every claim to the source text.** For example, a checklist item can point to section 2.3 of the June 2024 version of a policy.
3. **Review affected items when sources change.** The system locates potentially affected claims. The agent judges which changes are material and proposes revisions to the relevant artifact items.

## How it works

- Documents are parsed into structure-aware blocks with stable cross-version keys; an agent extracts claims, each bound to a span. **No span, no claim** — the ledger rejects it.
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

The project is **pre-alpha** and preparing for M0. M0 uses synthetic policy documents to measure recall, false-positive rate, latency, and cost for a baseline that regenerates the artifact and runs a semantic diff. The sections above describe the product design. `pipx` installation and a local web demo are planned for M4. [System design](./docs/design.md) · [M0 evaluation spec](./docs/specs/0001-m0-eval-harness.md)

## Non-goals

No accounts, no multi-tenancy, no web crawling, no auto-applying revisions, no multi-agent chatter. Each refusal is recorded with its reason in [docs/rejected.md](./docs/rejected.md).

## License

Apache-2.0. See [LICENSE](./LICENSE). Copyright 2026 Kstheme.
