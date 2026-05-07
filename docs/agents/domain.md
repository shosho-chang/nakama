# Domain Docs

This file tells engineering skills (`improve-codebase-architecture`, `diagnose`, `tdd`, `zoom-out`, `to-issues`, `to-prd`, `grill-with-docs`) where to find this repo's domain documentation and how to consume it.

## Layout: multi-context

This is a **multi-context** repo. The map lives at the root:

- **`CONTEXT-MAP.md`** (root) — lists every bounded context (each agent + cross-cutting kernel + presentation layer), the relationships between them, and a small set of frozen cross-context terms (e.g. "SEO solution", "approval queue", "surface", "audit review session", "slice"). **Read this first.**

Per-context vocabulary is **lazy-created**. Only when a skill is grilling or refactoring a specific context does it write that context's `CONTEXT.md`:

- `agents/<name>/CONTEXT.md` — domain language internal to one agent (Robin / Nami / Zoro / Sanji / Brook / Franky / Usopp). Most do not exist yet; create lazily when first needed.
- `thousand_sunny/CONTEXT.md` — domain language for the web presentation layer (Bridge dashboard, agent routers). Lazy.
- `shared/CONTEXT.md` — kernel vocabulary (Agent, Run, Memory, Event, API call, Token cost). Lazy.

If a skill needs a term that isn't in `CONTEXT-MAP.md` or any existing per-context `CONTEXT.md`, the right move is **add it to the relevant context's `CONTEXT.md` inline** (creating the file if needed), not to invent a synonym.

## ADR location: `docs/decisions/` (not `docs/adr/`)

**Important deviation from the mattpocock skills' default.** ADRs in this repo live at `docs/decisions/ADR-NNN-<topic>.md`, frozen by `CONTEXT-MAP.md` line 49.

When a skill instruction says "look in `docs/adr/`," substitute `docs/decisions/`. Examples of what's there:

- `ADR-001-agent-role-assignments.md` — agent-by-agent responsibility freeze
- `ADR-006-*` — Usopp publish HITL approval gate
- `ADR-008` / `ADR-009` — SEO solution architecture (Zoro for outward search, Brook for inward processing)
- `ADR-011-textbook-ingest-v2.md` — Robin ingest v2 (5 sub-decisions)

Context-scoped ADRs (e.g. `agents/<name>/docs/decisions/`) are **lazy** — none exist yet. When a decision is local to one agent's internals and would clutter the system-level ADR list, write it there instead.

## Retrieval defaults

- **Embedder**: `BGE-M3` (1024-dim, cross-lingual) is the KB retrieval default per [ADR-022](../decisions/ADR-022-multilingual-embedding-default.md). The legacy `potion-base-8M` (256-dim, English-only) is opt-in via `NAKAMA_EMBED_BACKEND=potion`.
- **Vector store**: `data/kb_index.db` `kb_vectors` is `vec0(embedding float[1024])`. Dim mismatch between the loaded model and the table fails loudly at `kb_hybrid_search.get_kb_conn()` time — re-run `python -m shared.kb_indexer --rebuild` when the embedder dim changes.

## Other domain artefacts

Skills should also know about these neighbouring directories — they're not consumed by skill prompts directly, but linking out to them is often the right move:

- **`docs/plans/YYYY-MM-DD-<topic>.md`** — time-stamped implementation plans (e.g. `2026-04-29-seo-control-center-prd.md`). Use when proposing or referencing in-flight work.
- **`docs/research/YYYY-MM-DD-<topic>.md`** — prior-art audits, codebase audits, capability landscapes. Cross-link when grounding a recommendation.
- **`docs/runbooks/<name>.md`** — operational procedures (postmortem-process, add-agent-slack-bot). Reference when the work touches deploy/ops.
- **`docs/principles/`** — three foundational principle docs (schemas, reliability, observability) that every ADR cites.

Use the date prefix for time-bound docs (plans / research / task-prompts), no prefix for evergreen docs (runbooks / principles / ADRs). See `feedback_doc_naming_date_prefix.md`.

## Vocabulary discipline

- **Use `CONTEXT-MAP.md` glossary terms exactly** when naming things in issues, PRDs, plans, ADRs, tests. If `CONTEXT-MAP.md` says "SEO 中控台" or "audit review session," use those — don't invent synonyms.
- **Surface ADR conflicts transparently**. If a proposal contradicts an existing ADR in `docs/decisions/`, name the ADR by number and call out the contradiction explicitly. Don't proceed silently.
- **Treat missing terminology as a signal**. If a concept needs a name and there isn't one in any `CONTEXT*.md` file, that's either an unnecessary invention (rename to an existing term) or a real gap (add the term to the right `CONTEXT.md`).
