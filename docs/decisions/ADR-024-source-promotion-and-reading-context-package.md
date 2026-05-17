# ADR-024: Source Promotion and Reading Context Package

**Status:** Accepted
**Date:** 2026-05-09
**Deciders:** shosho-chang, Codex GPT-5
**Related:** ADR-017, ADR-020, ADR-021, CONTENT-PIPELINE.md Stage 2/3/4, `agents/robin/CONTEXT.md`

---

## Context

Nakama now has three related but historically separate reading and ingest paths:

1. Ebook reading with highlights, annotations, `digest.md`, and `notes.md`.
2. Inbox / web document reading, increasingly fed by browser import instead of direct URL ingest.
3. Textbook-style whole-source ingest that produces chapter Source pages, Concept / Entity pages, and `mentioned_in` backlinks.

These paths were converging on the same underlying problem: a source may start as a personal reading object, but some sources deserve to become durable KB knowledge. If this boundary stays implicit, the system risks either under-ingesting valuable books/articles based only on what 修修 happened to highlight, or over-ingesting every annotation as if it were factual knowledge.

There is also a Stage 4 boundary problem. Line 2 book-review content is 修修's own voice. LLMs may reduce blank-page friction by organizing materials, but they must not ghostwrite Line 2 atomic content.

## Decision

Introduce **Source Promotion** as the canonical architecture for lifting a high-quality **Reading Source** into a knowledge-grade KB source.

Canonical vocabulary:

| Term | Meaning |
|---|---|
| **Reading Source** | An ebook, web document, or inbox document before it is necessarily promoted into formal KB. |
| **Reading Overlay** | 修修's personal interaction layer for a Reading Source: `KB/Annotations`, `digest.md`, `notes.md`, highlights, annotations, reflections, and reading-session metadata. |
| **Source Promotion** | Lifting a high-quality Reading Source into a knowledge-grade KB source. |
| **Promotion Review** | Staging review before formal KB writes, with include / exclude / defer recommendations, reasons, evidence, risks, and actions. |
| **Promotion Manifest** | Replayable decision record for review, commit, recovery, and future model reruns. |
| **Source-local Concept** | A concept useful inside one source but not yet promoted globally. |
| **Global KB Concept** | A cross-source, long-term concept in `KB/Wiki/Concepts`. |
| **Reading Context Package** | A Stage 3 -> Stage 4 handoff package for 修修's writing scaffolding, not a draft. |
| **Writing Assist Surface** | A Stage 4 UI surface that presents the Reading Context Package and supports hand-writing without composing finished prose. |

Source Promotion is triggered by **source quality**, not reading completion. Reading completion is a natural time to suggest promotion, but 修修 may also promote a source halfway through reading or immediately after import if the source is clearly high-value.

Promotion output is a **claim-dense source map**, not a full-text mirror. Full originals may remain in private evidence tracks, but `KB/Wiki/Sources/...` should preserve chapter / section structure, claims, key numbers, figures/tables summaries, short quote anchors, Concept / Entity links, and coverage manifests without distributing long verbatim source text.

Reading Overlay and Source Promotion have separate authority:

- Source maps govern factual claims: what the author/source says.
- Reading Overlay governs personal meaning: salience, questions, disagreement, applications, creative leads, and reading history.

Annotations may influence ranking and exception handling during Promotion Review, but they cannot directly create factual claims without evidence.

Full Source Promotion must pass through **Promotion Review** before writing formal `KB/Wiki`. The LLM is the primary recommender; 修修 is the checkpoint / brake. Each review item must include recommendation, reason, evidence, risk, action, and confidence. Missing evidence means defer / needs evidence, not commit.

Concept handling is two-level:

- Extract **Source-local Concepts** first.
- Promote only selected high-value concepts into **Global KB Concepts** when they have cross-source value, long-term output value, enough evidence, clear definitions, useful relations, or recurrence.

Promotion commits are item-level partial commits recorded in a **Promotion Manifest**. The manifest is the decision and recovery record; `KB/Wiki` is the materialized output.

For Stage 4, Robin may produce a **Reading Context Package** from annotations, notes, digest, promoted source map, Concept links, idea clusters, questions, evidence board, and outline skeletons. A Brook-owned or shared **Writing Assist Surface** may present this package and help insert links, references, and prompts. It must not generate finished prose or ghostwrite Line 2 atomic content. After 修修 writes the atomic content, Brook may use that human-authored piece for Stage 5 multi-channel production.

## Considered Options

### Rejected: Only integrate what 修修 highlighted

This overweights reading mood and attention. A high-quality book can contain important claims 修修 did not highlight during a specific session. Reading Overlay is valuable, but it should not be the only source of KB truth.

### Rejected: Promote every Reader source automatically

This creates KB bloat and turns low-value articles into durable concepts. Promotion must be a quality-based action with preflight, review, and manifest records.

### Rejected: Store promoted sources as full-text mirrors

Full originals may exist privately as evidence, but KB Wiki pages should not become long verbatim redistribution. The KB needs dense maps, evidence anchors, claims, and retrieval-friendly structure.

### Rejected: Let Brook write Line 2 reading essays from annotations

This violates the project constitution that Line 2 book-review atomic content is 修修's own voice. LLM assistance is allowed as scaffolding, not authorship.

## Consequences

- Ebook, inbox document, web document, and textbook ingest can share the same promotion language while keeping their import/reading mechanics separate.
- `digest.md` and `notes.md` remain Reading Overlay views, distinct from promoted Source pages.
- Robin/shared owns Source Promotion domain logic: source quality analysis, source-local concept extraction, global Concept matching, Promotion Manifest storage, acceptance gates, and KB commit.
- Thousand Sunny owns presentation and human checkpoint UI for promotion review.
- Brook must not bypass the Reading Context Package boundary to compose Line 2 atomic content.
- **2026-05-17 amendment (ADR-027):** Reading Context Package producer ownership is now formally **Brook**, alongside Brook synthesize, under a unified `agents/brook/scaffold/` sub-pipeline. Robin remains the owner of Source Promotion and KB ingest, but does not produce RCP. This consolidates all Stage 3→4 scaffolding under a single agent and aligns with ADR-012's 向外/對內 framing (Robin = 向外吸收, Brook = 對內加工).
- Future implementation work should first update `CONTENT-PIPELINE.md`, then create a parent PRD issue, then split implementation into vertical slices.
