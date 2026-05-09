---
type: decision
visibility: shared
agent: shared
confidence: high
created: 2026-05-09
expires: permanent
tags: [textbook-ingest, reader-digest, annotations, adr-020, adr-021, adr-022]
name_zh: Textbook ingest 與 Reader digest 邊界
name_en: Boundary between textbook ingest and Reader digest
description_zh: Reader / Ebook 的 digest.md 是 annotation-derived view；textbook ingest 不產 digest.md，也不應把 annotation_merger 或 book_digest_writer 納入 ADR-020 P0 修復範圍。
description_en: Reader/Ebook digest.md is an annotation-derived view; textbook ingest does not produce digest.md and must not pull annotation_merger or book_digest_writer into ADR-020 P0 repair scope.
---

# Textbook Ingest vs Reader Digest Boundary

## Decision

Treat Reader / Ebook `digest.md` and ADR-020 textbook ingest as separate pipelines.

- Reader / Ebook `digest.md` is a derived human-facing view generated from `KB/Annotations/{book_id}.md` after Reader annotation save.
- Textbook ingest canonical outputs are Raw markdown, staged chapter source pages, staged Concept pages, dispatch logs, coverage manifests, and Book Entity pages.
- Textbooks are English-only sources for this project. 修修 does not personally annotate textbooks, so textbook ingest never triggers Reader annotation save and never produces `KB/Wiki/Sources/Books/{book_id}/digest.md`.
- Do not include `book_digest_writer.py` or `annotation_merger.py` changes in ADR-020 textbook-ingest P0 repair unless a later decision explicitly changes the textbook pipeline boundary.

## Why This Matters

The 2026-05-09 cross-session findings file clarified that the word "digest" was overloaded:

- `book_digest_writer.py` belongs to Reader / Ebook annotation workflow.
- ADR-020 textbook source pages and concept appendices are ingest artifacts, not digest files.
- Mixing these paths would expand textbook P0 into Reader / Ebook cross-lingual annotation work and risk fixing the wrong system.

## Cross-Pipeline Findings To Preserve

- `annotation_merger` currently lists only concept slugs and does not pass aliases, definitions, or source counts into the LLM matching prompt. This affects Reader / Ebook cross-lingual reverse-surfaced wikilinks, not current textbook P0.
- Monolingual zh-Hant Reader pilot intentionally does not run cross-lingual `annotation_merger` sync. Its `digest.md` can still render H/A/R items and Reader deep links, but reverse wikilinks may stay empty.
- ADR-022 BGE-M3 production rebuild verification is a shared infrastructure risk. It affects Reader digest KB hits, Brook synthesize, and future cross-lingual KB search quality, but it should be handled as a separate retrieval/index verification task rather than folded into textbook P0.
- If future textbook ingest work adds grounding context, use label-only candidate metadata such as slug, display title, aliases, `en_source_terms`, status, and source count. Do not copy body excerpts into the prompt as a large grounding pool.

## Current Next-Step Implication

For the active ADR-020 repair thread:

1. Finish and commit textbook P0 repair first.
2. Keep scope limited to canonicalization, dispatch-log-derived source appendix/frontmatter, L1 plain-text demotion, `mentioned_in` reconciliation, file-based acceptance gate, golden fixture, and aggregate `verify_staging`.
3. Do not run LLM batch until P0 code and targeted tests are settled.
4. Track ADR-022 production index verification and Reader / Ebook `annotation_merger` candidate-context expansion as separate follow-up work.
