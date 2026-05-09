---
name: N511 Promotion Preflight v1 Brief — required v2 corrections
description: Two specific Codex-flagged design errors in the #511 v1 Brief that must be fixed in v2 before relabeling ready-for-agent
type: project
---

The N511 v1 Brief at `docs/task-prompts/N511-promotion-preflight.md` (drafted 2026-05-09; **not yet committed** — sits as untracked on `docs/n510-n511-n512-briefs` branch) ships with two design errors that 修修 has flagged for v2 revision. Codex review surfaced these on 2026-05-09 alongside the #512 v2 micro-rework session. **Do NOT relabel #511 `ready-for-agent` until v2 lands.**

### Correction 1 — `has_evidence_track=False` default policy

v1 §4.2 maps `has_evidence_track=False` (with moderate-large content) to `partial_promotion_only` as the happy path. **This is wrong** per ADR-024 + `agents/robin/CONTEXT.md` § Source Promotion: "missing evidence means **defer** / needs evidence, **not commit**" and "Annotation 中無 evidence 的延伸想法進 personal insight，不直接 create factual Concept".

The default for `has_evidence_track=False` MUST be `defer` (or `annotation_only_sync` for short / non-promotable content) — never `partial_promotion_only` as the standard recommendation. v2 must invert the §4.2 mapping table:

- `has_evidence_track=False` (any size, any reason) → `defer` or `annotation_only_sync`. NEVER auto-route to `partial_promotion_only`.
- `partial_promotion_only` becomes a narrow exception state — only valid when an explicit human override / waiver is recorded upstream (e.g. by #516 Review UI), not the deterministic preflight output.
- The hard invariant `recommended_action == "proceed_full_promotion" ⇒ has_evidence_track == True` (already in v1) stays. Add a parallel invariant: `recommended_action == "partial_promotion_only" ⇒ explicit_human_override is True` (or remove the action enum value entirely from #511 schema and defer to #515 Commit Gate).

### Correction 2 — EPUB inspector path discipline

v1 §3 says "EPUB inspector: load via `book_storage.read_book_blob(book_id, lang=...)`". This re-derives `book_id` from `ReadingSource.source_id` parsing, which **violates #509 N3 contract**: `source_id` is logical identity, not a filesystem lookup key (per `shared/schemas/reading_source.py:84-87`).

v2 inspector MUST do one of:

- **(a)** Read directly from `ReadingSource.variants[*].path`. The variant path is the authoritative file location (`data/books/{book_id}/original.epub` or `Inbox/kb/foo.md` per `shared/reading_source_registry.py:181-200`). Open the file via `vault_root / path` (or for ebooks the equivalent).
- **(b)** Accept an injected `blob_loader: Callable[[str], bytes]` so callers (production + tests) provide the loader. Preflight stays read-only at the schema-described variants and never reaches into `book_storage` by parsing namespaces.

**Why both corrections matter:**

1. **Correction 1**: prevents silent factual-claim drift when evidence is missing. ADR-024 is explicit that promotion without evidence corrupts KB factuality. Default-to-defer is the conservative posture; default-to-partial-promotion is a foot-gun.
2. **Correction 2**: keeps preflight aligned with #509's logical-identity contract (`source_id` is not a file path). Re-parsing namespaces would require preflight to know the `ebook:` prefix convention and would re-introduce identity-derivation logic that #509 deliberately centralized in the registry.

### How to apply (when reworking N511 to v2)

1. Re-write §4.2 mapping table around `defer` / `annotation_only_sync` defaults for `has_evidence_track=False`. Drop or constrain `partial_promotion_only`.
2. Re-write §3 EPUB inspector input contract: variant path or injected loader; never `book_id` parsing.
3. Re-write §4.3 service API to make the loader injection explicit (or document the variant-path read pattern).
4. Add T-test asserting that `partial_promotion_only` is unreachable from a deterministic preflight (or reachable only via the explicit-override field).
5. Add T-test asserting EPUB inspector is callable without `book_storage` import (e.g. via injected loader); subprocess assertion or import-check.
6. Other v1 sections likely unchanged (T1-T13 mostly OK pending the policy table re-write; §0 enumeration scope decision OK; §6 boundaries OK). Review carefully but expect localized changes in §3 / §4.2 / §4.3 / §5.

### Context

- N512 v2 micro-rework already committed at `c73e44d` on `docs/n510-n511-n512-briefs` branch (2026-05-09 same session).
- N510 read-only investigation note committed at `4106194` on the same branch (2026-05-09).
- N511 v1 Brief still untracked. v2 rework is deferred to the next session.
- ADR-024 axis status: S1 (#509) shipped to main `0f2742f`; S2 (#510), S3 (#511), S4 (#512) all have v1+ Briefs drafted but not yet implemented.
