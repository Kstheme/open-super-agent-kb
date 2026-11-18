# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context repo:

```
/
├── CONTEXT.md
├── docs/
│   ├── adr/
│   │   ├── 0001-sqlite-only-with-store-port.md
│   │   ├── 0002-single-user-local-first.md
│   │   └── 0003-app-first-with-public-ledger-spec.md
│   └── design.md
└── (code lands here per design.md §5: core/, agent/, ingest/, retrieval/, artifacts/, server/, spec/)
```

Multi-context is not in use. If a `CONTEXT-MAP.md` ever appears at the root, switch to reading one `CONTEXT.md` per context plus any `src/<context>/docs/adr/`.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

This repo's glossary is Chinese. Its core terms are 资料 / 声明 / 证据 / 账本 / 产物 / 声明模板 / 触发 / 任务 / 缺口 / 复核 / 差异提案 / 受影响 / 实质影响. Each entry carries an `_Avoid_` list (e.g. 声明 must not be called 结论 or 知识点; 证据 must not be called 来源 or 引用). Follow those avoidances in issue titles and specs too.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0001 (SQLite only, behind a Store port), but worth reopening because…_
