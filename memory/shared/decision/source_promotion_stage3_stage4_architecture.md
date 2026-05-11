---
type: decision
visibility: shared
agent: shared
confidence: high
created: 2026-05-09
expires: permanent
tags: [source-promotion, reader, kb-ingest, stage-3, stage-4, brook, robin]
name_zh: Source Promotion 與 Stage 4 寫作邊界
name_en: Source Promotion and Stage 4 writing boundary
description_zh: Reader source 可經 Source Promotion 提升為 KB knowledge-grade source；Robin 準備 Reading Context Package，Brook 不代寫 Line 2 原子內容。
description_en: Reader sources may be promoted into knowledge-grade KB sources via Source Promotion; Robin prepares Reading Context Packages, while Brook must not ghostwrite Line 2 atomic content.
---

# Source Promotion Architecture

## Decision

Use **Source Promotion** as the canonical architecture for integrating high-quality Reader sources into the Knowledge Base.

This decision is formalized in `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`.

Canonical vocabulary:

- **Reading Source**: an ebook, web document, or inbox document before it is necessarily promoted into formal KB.
- **Reading Overlay**: Shosho's personal interaction layer for a Reading Source: `KB/Annotations`, `digest.md`, `notes.md`, highlights, annotations, reflections, and reading-session metadata.
- **Source Promotion**: lifting a high-quality Reading Source into a knowledge-grade KB source.
- **Promotion Review**: staging review before formal KB writes, with include / exclude / defer recommendations, reasons, evidence, risks, and actions.
- **Promotion Manifest**: replayable decision record for review, commit, recovery, and future model reruns.
- **Source-local Concept**: a concept useful inside one source but not yet promoted globally.
- **Global KB Concept**: a cross-source, long-term concept in `KB/Wiki/Concepts`.
- **Reading Context Package**: a Stage 3 -> Stage 4 handoff package for Shosho's writing scaffolding, not a draft.
- **Writing Assist Surface**: Stage 4 UI surface that presents the Reading Context Package and supports hand-writing without composing finished prose.

## Rules

- Promotion is triggered by **source quality**, not reading completion.
- Promotion output is a **claim-dense source map**, not a full-text mirror. Full originals may remain private evidence, but `KB/Wiki/Sources/...` should not distribute long verbatim source text.
- Factual authority belongs to original evidence + promoted source map. Personal salience, disagreement, questions, applications, and creative leads belong to the Reading Overlay.
- Full Source Promotion must pass through Promotion Review / staging before writing formal `KB/Wiki`.
- Promotion Review items must force the LLM to give recommendation, reason, evidence, risk, action, and confidence. Missing evidence means defer / needs evidence, not commit.
- Concepts are extracted source-locally first; only selected high-value concepts become Global KB Concepts.
- Bilingual display is for Reader and is not factual evidence. Original-language track is the evidence track; cross-lingual concept matches need explicit confidence and exception handling.
- Rereading updates the Reading Overlay by default. It must not silently rerun full Source Promotion; at most it may suggest a delta promotion review.
- Promotion commits are item-level partial commits recorded in the Promotion Manifest, including touched files, batch status, errors, and recovery metadata.

## Stage Ownership

- Robin/shared owns Source Promotion domain logic: source quality analysis, source-local concept extraction, global Concept matching, Promotion Manifest storage, acceptance gates, and KB commit.
- Thousand Sunny owns presentation and human checkpoint UI for Source Promotion.
- Robin may produce a Reading Context Package from annotations, notes, digest, promoted source map, Concept links, idea clusters, questions, evidence board, and outline skeletons.
- A Brook-owned or shared Writing Assist Surface may present the Reading Context Package and help insert links, references, or prompts.
- Brook must not use the package to ghostwrite Line 2 atomic content. After Shosho writes the atomic content, Brook may use that finished human-authored piece for Stage 5 multi-channel production.

## Documentation Layering

- `agents/robin/CONTEXT.md` owns canonical vocabulary and domain rules.
- `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md` owns the reasons, trade-offs, and alternatives.
- `CONTENT-PIPELINE.md` should own the day-to-day Stage 2/3/4 workflow.
- PRDs and GitHub issues should own implementation delivery slices, not become the only home for domain decisions.
