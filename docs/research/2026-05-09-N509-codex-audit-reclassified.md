# N509 Brief + Plan — Codex Audit Re-classified Against Latest ADR-024 Direction

**Date:** 2026-05-09
**Status:** For 修修 + Codex review
**Owner:** Claude (Opus 4.7, 1M context)
**Worktree:** `E:/nakama-N509-source-registry`
**Branch:** `impl/N509-reading-source-registry` (commit 8bb3f0c)
**Companion artefacts:**
- Brief: `docs/task-prompts/N509-reading-source-registry.md`
- Plan: `docs/plans/2026-05-09-N509-reading-source-registry.md`
- Original Codex audit: see §2 below (preserved verbatim)

---

## 0. Why this doc exists

修修 dispatched a `codex:rescue` push-back audit (GPT-5.5 medium effort) against the #509 Brief + plan. Codex returned **REWORK** with 2 P0 blockers + 7 P1 must-fix + 1 P2 — 10 findings total.

Before reworking, 修修 surfaced two pieces of context that the audit missed:

1. **The latest ADR-024 / Source Promotion direction** finalized 2026-05-08/09 after a full-day grill with Codex GPT-5 + Gemini panel review.
2. **A separate Phase 1 PRD** (`docs/plans/2026-05-09-monolingual-zh-reader-annotation-pilot-prd.md`) on branch `feat/monolingual-zh-pilot-prd-handoff` (un-merged), which deprecates `Book.lang_pair` in favor of a `mode: Mode` field.

This document re-classifies the 10 Codex findings against the latest direction, surfaces 3 findings Codex missed, and lists the 3 open design questions that need 修修's call before the Brief can be reworked.

**Out of scope for this doc:** code changes, new issue creation, label changes, dispatch decisions.

---

## 1. Source materials reviewed (full re-read pass)

### 1.1 Latest direction docs

| Doc | What it establishes |
|---|---|
| `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md` (main, PR #441) | Reading Source = ebook + inbox/web document; Source Promotion is Stage 3→KB lift; textbook-grade is a promotion mode; Robin owns domain logic; Brook does not ghostwrite Line 2 |
| `agents/robin/CONTEXT.md` § Source Promotion | Canonical vocabulary; "Promotion 的觸發門檻是 source quality 不是 reading completion"; "Promotion 的輸出是 claim-dense source map 不是 full-text mirror" |
| `memory/shared/decision/source_promotion_stage3_stage4_architecture.md` | Same vocabulary + Robin / Thousand Sunny / Brook ownership boundary |
| `memory/shared/decision/textbook_ingest_digest_boundary.md` | Textbook ingest 與 Reader digest 是分開 pipeline; textbook 永遠英文; 修修 不親自 annotate 教科書 |
| `memory/shared/decision/web_reading_import_main_content_extraction.md` | Toast 是 web import 主控; 借用 Obsidian Clipper / Defuddle 抽 main content; Reader UX 雙語 display 不是 factual evidence |
| `docs/research/2026-05-09-digest-md-cross-session-findings.md` | 修修 confirms 永遠 zh-Hant + en 兩語言; 教科書永遠英文; ADR-022 production rebuild verify pending |

### 1.2 Phase 1 PRD (un-merged, parallel axis)

| Doc | Key facts |
|---|---|
| `docs/plans/2026-05-09-monolingual-zh-reader-annotation-pilot-prd.md` (qa-adr021 branch) | Reversible pilot for monolingual-zh reader + annotation; **introduces `Book.mode = Literal["monolingual-zh", "bilingual-en-zh"]`**; deprecates `Book.lang_pair`; uses Phase 1 PRD scope is reader+annotate only, NO ingest, NO concept page touches |
| `docs/plans/2026-05-08-monolingual-zh-source-grill.md` (qa-adr021 branch) | 8 grill decisions; mode field rationale; lang_pair deprecation rationale |
| `docs/research/2026-05-08-codex-adr-024-audit.md` + `gemini-adr-024-audit.md` (qa-adr021 branch) | Panel REJECT of an alternative ADR-024 (cross-lingual-concept-alignment, now superseded) |

### 1.3 Issue #508 (parent PRD) and #509 (slice 1)

- PRD #508 explicitly references "ebook, Inbox document, web document, and textbook-grade source" — but this language predates the latest "URL cancelled / textbook is a mode / web→Inbox" direction.
- Issue #509 inherits the four-kind framing.

---

## 2. Original Codex audit — verdict + findings (verbatim)

**Verdict:** REWORK

| ID | Severity | Location | What is wrong | Fix |
|---|---|---|---|---|
| F1 | P0 blocker | Brief:57; Plan:90-95; ADR-024:30 | `SourceKind = ebook / inbox_document / kb_source` does not match ADR vocabulary (ebook / web document / inbox document). Replaces `web_document` with `kb_source`. | Use `ebook` / `inbox_document` / `web_document` or fold web into inbox metadata. Move `kb_source` out. |
| F2 | P0 blocker | Plan:94; ADR-024:42; shared-memory:38 | `kb_source` is "already-materialized page in KB/Wiki/Sources" — but ADR says that path holds promotion **output**, not Reading Source input. Mixes Stage 2 raw input with Stage 3 promoted output. | Remove `KBSourceKey`, or define separate `PromotedSourceMapKey`. |
| F3 | P1 must-fix | Plan:125-127; annotation_store.py:70-78; Brief:45 | `annotation_slug` priorities frontmatter title > filename. Title can change → `source_id` mutates. #510 overlay join becomes brittle. | Split: `source_id` = stable namespace key (e.g. `inbox:Inbox/kb/foo.md`); `annotation_key` = current annotation_slug for overlay compat. |
| F4 | P1 must-fix | thousand_sunny/routers/robin.py:141-155; Plan:217-220 | Inbox route collapses siblings (`-bilingual.md` shown, `.md` hidden) but registry doesn't canonicalize the pair. `InboxKey("foo.md")` vs `InboxKey("foo-bilingual.md")` give different IDs for one logical source. | Canonicalize sibling pair so both inputs resolve to one stable `source_id`. |
| F5 | P1 must-fix | Brief:103; Plan:206,295; books.py:44; book_storage.py | `Book.lang_pair` is unconstrained string; real values not grounded. `split("-")[0]` + default `"en"` is wrong for zh-heavy system. | Add explicit `parse_primary_lang(lang_pair)` helper with accepted cases + tests. Don't default to `en`. |
| F6 | P1 must-fix | Plan:136-138; CONTEXT:75; shared-memory:43 | `has_evidence_track=False` is described, but downstream policy (block / defer / degraded) is undecided. | Brief must define: promotion preflight blocks #513/#514 unless human-approved degraded mode. |
| F7 | P1 must-fix | Plan:285,327; book_storage.py:167-171; routers/robin.py:136-194 | "No enumeration API" declared out of scope, but #511 Preflight needs it. Risk: #511 duplicates route logic or violates reusable-shared-service boundary. | Add minimal `list_reading_source_candidates()` now, OR scope #511's first task as extraction. |
| F8 | P1 must-fix | Brief:103; Plan:209-210,300-302 | Plan §4.3 says ebook variant path is `books:{book_id}:en/bilingual`, but Brief §4.1 + Plan §7 Q3 use `data/books/{book_id}/original.epub`. Same plan, contradictory contracts. | Rewrite plan §4.3 with single canonical path syntax. |
| F9 | P1 must-fix | Plan:221-223,242; Brief:133 | Inbox single-file rule: plan says "only one exists = original with evidence", but `test_resolve_inbox_bilingual_only` expects display-only with `has_evidence_track=False`. Internal contradiction. | Split single-file cases: plain-only = original/evidence; bilingual-only = display/no evidence. |
| F10 | P2 nice-to-have | Plan:237-245; Brief:128-136; annotation_store.py:83-89 | Test matrix misses: orphan book blob (no DB row), vault root not configured, kb_source path escape, empty annotation_slug. | Add the four tests; especially path normalization rejecting `..` and controlled error for empty slug. |

---

## 3. Re-classification under latest direction

### 3.1 🔴 Valid blockers — still apply

| # | Finding | Why still valid | Required fix |
|---|---|---|---|
| **F2** | `kb_source` mixes Stage 2/3 layers | ADR-024 §Decision: `KB/Wiki/Sources/...` is **claim-dense source map** = promotion output, not Reading Source. "Textbook-grade is a promotion mode" reinforces this boundary. | Drop `kb_source` from `SourceKind`. If a future slice needs to resolve already-promoted artefacts, define a separate `SourceMapReference` value object — not part of `ReadingSource`. |
| **F3** | `source_id == annotation_slug(...)` mutates with frontmatter title | Neither ADR-024 nor Phase 1 PRD changes `annotation_slug` semantics. Title-priority is still in `shared/annotation_store.py:70-78`. | Two-field model: `source_id` = stable namespace-qualified ID (e.g. `inbox:Inbox/kb/foo.md` or `ebook:{book_id}`); `annotation_key` = current `annotation_slug(filename, frontmatter)` for overlay join. Document mutation semantics for each. |
| **F4** | Sibling canonicalization missing | `web_reading_import_main_content_extraction.md` and existing `_get_inbox_files` in `routers/robin.py` keep the bilingual sibling pattern. Latest direction does not change this. | Define canonical pair: when both `foo.md` and `foo-bilingual.md` exist, both `InboxKey` inputs resolve to the same `source_id`; original sibling is canonical owner. |
| **F8** | Plan §4.3 path contradiction | Pure doc bug — independent of architecture. | Single canonical path syntax across Brief + plan: `data/books/{book_id}/original.epub` / `bilingual.epub` (matches `book_storage.read_book_blob`). |
| **F9** | Inbox single-file rule contradicts test matrix | Pure logic bug — independent of architecture. | Three cases explicitly: (a) plain-only → 1 original variant, has_evidence=True; (b) bilingual-only → 1 display variant, has_evidence=False; (c) both → 2 variants. |

### 3.2 🟡 Superseded by latest ADR-024 direction

| # | Finding | Why superseded |
|---|---|---|
| **F1 (web_document split)** | Codex argued for `ebook` / `web_document` / `inbox_document` 3-kind enum. | Latest direction: "URL ingest 已取消; web capture 交給 browser/Obsidian plugin (Toast/Defuddle)". Web docs **physically land in `Inbox/kb/`** as markdown. Therefore web documents ARE inbox documents in this system. **Reading Source has only 2 kinds in latest architecture: `ebook` + `inbox_document`.** Origin (web vs hand-dropped vs scrape) is metadata, not a kind. |
| **F1 (kb_source part)** | `kb_source` doesn't match ADR vocabulary. | Subsumed by F2 — covered in §3.1. |
| **F10 (kb_source path escape test)** | Edge case test for `KBSourceKey` path escape outside `KB/Wiki/Sources`. | Once F2 removes `kb_source` from `SourceKind`, this edge case is moot. The other F10 cases (orphan book blob, missing vault root, empty annotation_slug) remain valid as P2 — see §3.3. |

### 3.3 🟠 Open design questions (need 修修 decision)

These are not bugs. They are scope/boundary calls that the Brief must commit to before rework.

| Q | Question | Options | Claude lean |
|---|---|---|---|
| **Q1** (F5 + N1 combined) | Phase 1 PRD deprecates `Book.lang_pair` and introduces `Book.mode: Mode = Literal["monolingual-zh", "bilingual-en-zh"]`. Phase 1 is on `feat/monolingual-zh-pilot-prd-handoff` (not merged). #509 from main can only see `lang_pair`. How should #509 derive `primary_lang`? | **(a)** Use `lang_pair.split("-")[0]` (main current state) — simple but doomed to refactor when Phase 1 lands<br>**(b)** Use `BookMetadata.lang` from `extract_metadata` (already captured per `shared/schemas/books.py:28`) — survives Phase 1<br>**(c)** Block #509 until Phase 1 S1 (mode field schema) merges, then use `Book.mode` directly | **(b)** — most stable; `metadata.lang` is the upstream truth that both `lang_pair` and `mode` should derive from anyway. Avoids both deprecation collision and dependency stall. |
| **Q2** (F6) | `has_evidence_track=False` (bilingual-only inbox) — should #509 commit to a downstream policy (block / defer / degrade), or expose the flag and let #511 / #513 / #514 each decide? | **(a)** #509 defines policy: bilingual-only = block at preflight<br>**(b)** #509 only exposes flag; downstream slices each decide<br>**(c)** Defer entirely; out of scope for #509 | **(b)** — #509 is a registry, not a policy module. Policy belongs in `#511` Preflight (block) and `#513`/`#514` (degraded modes if 修修 explicitly approves). #509 must guarantee the flag is **precise** (exact rule for what makes it false), not enforce it. |
| **Q3** (F7) | No enumeration API in #509 → how does `#511` Promotion Preflight scan inbox + books table? | **(a)** Add `list_reading_source_candidates()` to #509 now (speculative)<br>**(b)** `#511` extends the registry when needed (clean dependency)<br>**(c)** `#511` reuses `book_storage.list_books()` + walks `Inbox/kb/` directly (no registry extension) | **(b)** — registry surface should grow with concrete demand. `#511` can spec the enumeration shape it needs (filters? laziness? pagination?) when it knows what Preflight requires. **Brief must explicitly note this as deferred contract**, not silently absent. |

---

## 4. Findings Codex missed (latest-direction-only)

### 4.1 🔴 N1 — Brief silent on Phase 1 PRD axis collision

**Severity:** P0
**Location:** Brief §3 (Inputs) + §6 (Boundaries)
**Issue:** Brief uses `Book.lang_pair` per main schema. Phase 1 PRD on `feat/monolingual-zh-pilot-prd-handoff` deprecates `lang_pair` and adds `Book.mode`. If #509 ships before Phase 1 S1 merges, the `primary_lang` derivation in `_resolve_book` will need refactoring within weeks.

**Why Codex missed it:** Codex was given main-branch context only; Phase 1 PRD lives on a parallel un-merged branch.

**Fix path:** Brief must (a) acknowledge Phase 1 axis exists, (b) commit to one of Q1's three options, (c) call out the schema migration that #509 will require if (a) is chosen.

### 4.2 🟡 N2 — Brief inherits stale 4-kind framing from issue body

**Severity:** P1
**Location:** Brief §1 (目標) + §2 (範圍) + §4.1 (schema)
**Issue:** Issue #509 body says "ebook, Inbox document, web document, and textbook-grade source" (4 kinds). Latest direction reduces this to **2 kinds**:
- `ebook` (book reader target)
- `inbox_document` (web docs land here via Toast; hand-dropped articles also land here)
- Textbook is a **promotion mode applied to ebook source**, not a Reading Source kind
- Web is **physically inbox**, not a separate kind

**Why Codex missed it:** Codex pushed back on my 3-kind enum (suggesting a different 3-kind enum with `web_document` separate); did not reduce to 2.

**Fix path:** Brief should explicitly quote ADR-024 + 修修's verbal direction, justify the 2-kind reduction, and acknowledge it diverges from issue #509 body. Optional: post comment on #509 explaining the divergence so future readers don't get confused.

### 4.3 🟡 N3 — Onboarding gap for next agent picking up #509

**Severity:** P1
**Location:** Brief §7 (References)
**Issue:** Brief references main-branch artefacts only. An agent picking up #509 from main will not see Phase 1 PRD or the grill artefacts on `feat/monolingual-zh-pilot-prd-handoff`. They will recapitulate the question of `lang_pair` vs `mode` from scratch.

**Fix path:** Brief should explicitly list the qa-adr021 branch artefacts (Phase 1 PRD + grill summary + panel audits) under "References / parallel axis" with a note: "These are not yet in main; clone qa-adr021 worktree or read directly from `feat/monolingual-zh-pilot-prd-handoff` branch."

---

## 5. Recommended path forward

### 5.1 What 修修 must decide

Three open design questions (§3.3 Q1-Q3). Without these, Brief cannot be reworked coherently.

### 5.2 What is mechanical (no decision needed)

After Q1-Q3 are answered, the Brief + plan rework is mechanical:

1. Drop `kb_source` kind, `KBSourceKey`, related tests (F2)
2. Add stable `source_id` + `annotation_key` two-field model + document mutation semantics (F3)
3. Add canonical sibling pair handling for inbox (F4)
4. Single canonical path syntax (F8)
5. Three-case inbox single-file rule (F9)
6. 4 missing edge case tests minus the kb_source-dependent one (F10 → 3 tests: orphan book blob, missing vault root, empty annotation_slug)
7. Add Phase 1 PRD references to Brief (N3)
8. Quote ADR-024 + verbal direction to justify 2-kind reduction (N2)
9. Apply Q1 decision (lang_pair / metadata.lang / wait-for-Phase1)
10. Apply Q2 decision (policy in #509 or downstream)
11. Apply Q3 decision (enumeration in #509 or in #511)

Estimated rework: half a session, no LLM batch, no code yet.

### 5.3 Suggested next-step order

1. 修修 reviews this doc + decides Q1 / Q2 / Q3
2. (Optional) Re-dispatch Codex with this doc + 修修's decisions for second-pass audit
3. Claude reworks Brief + plan per §5.2
4. 修修 final review → relabel `ready-for-agent` (or stop and queue with other slices)
5. Implementation runs in this worktree (local, ~200 LOC + tests; no Sandcastle)

---

## 6. References

### Primary docs reviewed in this re-classification

- `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md` (main)
- `docs/plans/2026-05-09-monolingual-zh-reader-annotation-pilot-prd.md` (qa-adr021 branch)
- `docs/plans/2026-05-08-monolingual-zh-source-grill.md` (qa-adr021 branch)
- `docs/research/2026-05-09-digest-md-cross-session-findings.md` (main)
- `docs/research/2026-05-08-adr-024-panel-integration.md` (qa-adr021 branch)
- `docs/research/2026-05-08-codex-adr-024-audit.md` (qa-adr021 branch)
- `docs/research/2026-05-08-gemini-adr-024-audit.md` (qa-adr021 branch)
- `agents/robin/CONTEXT.md` § Source Promotion (main)
- `memory/shared/decision/source_promotion_stage3_stage4_architecture.md` (main)
- `memory/shared/decision/textbook_ingest_digest_boundary.md` (main)
- `memory/shared/decision/web_reading_import_main_content_extraction.md` (main)
- `CONTENT-PIPELINE.md` (main)
- `AGENTS.md` (main)

### Code surface inspected

- `shared/schemas/books.py:35-50` (Book schema, lang_pair / has_original)
- `shared/schemas/ingest_result.py` (inbox frontmatter contract)
- `shared/schemas/annotations.py` (v3 schema preserved by Phase 1)
- `shared/annotation_store.py:64-90` (annotation_slug rule)
- `shared/book_storage.py:84-180` (book identity, blob paths)
- `agents/robin/inbox_writer.py:1-80` (inbox write rules)
- `thousand_sunny/routers/books.py:118-243` (upload + ingest gate)
- `thousand_sunny/routers/robin.py:140-260` (inbox routes, sibling collapse)

### Companion artefacts under audit

- `docs/task-prompts/N509-reading-source-registry.md` (Brief, commit 8bb3f0c)
- `docs/plans/2026-05-09-N509-reading-source-registry.md` (Plan, commit 8bb3f0c)

### Issue + PR context

- Issue #509: `[ADR-024 S1] Reading Source Registry` (needs-triage, no labels yet)
- Issue #508: `PRD: Source Promotion and Reading Context Package` (parent)
- PR #441: source-promotion ADR + textbook P0 repair (merged to main, commit 28ec9ee)
- Branch `feat/monolingual-zh-pilot-prd-handoff`: Phase 1 PRD source (not merged)
- Branch `impl/N509-reading-source-registry`: this Brief + plan (commit 8bb3f0c, pushed)
