# ADR-019: Two-file Source Ingest Pattern (Raw + Annotated)

**Status:** Superseded (2026-05-06) — Zotero pipeline removed
**Superseded reason:** 與 ADR-018 一同 superseded。Two-file fan-out (raw `{slug}.md` + annotated `{slug}--annotated.md`) pattern 原本綁在 Zotero ingest path（`agents/robin/zotero_ingest.py` `produce_source_pages`）；Zotero 整合全砍後此模式無實作載體。Annotated source page 概念可能在新 ingest grill（Web Clipper pipeline + Document/EPUB Reader 一致性）後重新挑揀，但要等 grill 結論再寫新 ADR，不直接沿用本 ADR。詳見 `memory/claude/project_zotero_qa_2026_05_06_pivot_to_webclipper.md`.
**Date:** 2026-05-05
**Deciders:** shosho-chang
**Related:** ADR-018, ADR-017 (annotation KB integration), PR #354 (bilingual reader)

---

## Context

When 修修 ingests an academic paper or long-form article, two **epistemically distinct** artifacts are needed in KB:

1. **The raw source as published** — for fact-checking, quotation, future re-translation. Must be **LLM-untouched** (zero-trust pure source).
2. **修修's reading lens on the source** — translation + highlights + annotations capturing personal observations. **First-class personal voice**, not a derivative.

Existing Robin ingest pipeline (URL 軌) produces a single `KB/Wiki/Sources/{slug}.md` — the LLM-summarized source page. This conflates the two roles: it is neither pristine source nor 修修's voice; it's a middle artifact that satisfies neither use case.

CONTENT-PIPELINE.md 觀察 #3 also flagged: "Annotation → 後續使用沒 owner" — Reader UI lets 修修 highlight + annotate (ADR-017), but those notes aren't surfaced anywhere structured in KB.

## Decision

Source ingest produces **two parallel files** in `KB/Wiki/Sources/`:

| File | Filename | Source | Purpose |
|---|---|---|---|
| **Raw source page** | `{slug}.md` | **Re-extract** from `Zotero storage/snapshot.html`（fresh Trafilatura）or PDF（pymupdf4llm） | LLM-untouched 原文，引用 / fact-check / 重翻譯都對它打 |
| **Annotated source page** | `{slug}--annotated.md` | **Weave** `Inbox/kb/{slug}-bilingual.md` + `KB/Annotations/{slug}.md` | 雙語對照 + 修修 annotation inline 編織，個人觀點 first-class |

Frontmatter cross-link：

- **Raw**: `annotated_sibling: "{slug}--annotated.md"` + Zotero / DOI / authors metadata
- **Annotated**: `raw_source: "{slug}.md"` + 同 metadata
- Wiki link `[[slug]]` 預設 resolve 到 raw（primary citation entity）

## Considered Options

- **Single source page** (existing URL ingest 行為) — rejected. Conflates 純原文 + LLM 詮釋；修修 annotation 完全 lost；觀察 #3 無解。
- **Sub-folder structure** `Sources/{slug}/raw.md` + `annotated.md` — rejected. KB/Wiki/Sources/ 既有 flat 慣例（textbook ingest 才有 sub-folder 因「同書多章」是合理 group，單篇 paper 不該）；Obsidian wikilink `[[slug]]` 對 sub-folder 不友善。
- **Strip `==zh==` blocks for raw**（從 inbox bilingual 反推 English） — rejected. 違反「來源沒被 LLM 增加內容」最嚴讀法；inbox file 中間有翻譯按鈕、`==highlight==` markup 殘留風險，不夠純。Re-extract from `snapshot.html` 是 zero-trust 強保證。
- **Annotation woven into raw** — rejected. 違反 raw = 原文 untouched 的純粹定義。

## Consequences

- **MVP scope（grill Q10 凍結）**：Robin concept/entity extraction **暫時不**改吃 annotated 當 input。Wiki Concepts 端的 LLM extraction 行為跟既有 URL ingest 一致（讀原文 source），所以 MVP 階段修修 annotation 只活在 annotated source page，不直接 inform Wiki Concepts。Phase 2 加 input switch（觀察 #3 真正解）— 等修修實跑 3-5 篇 paper 後再 grill。
- **Annotation slug 天然 merge**：raw + bilingual 兩檔 frontmatter 共享同 `title`（PR #354 translator 直接 copy frontmatter），`shared/annotation_store.annotation_slug()` (line 55) 從 title 優先 derive → 同 slug → annotations 落 `KB/Annotations/{slug}.md`，**不需要 amend ADR-017**。
- **Pattern 適用範圍**：Zotero ingest（本 ADR 直接 trigger）+ 將來 EPUB ingest（PR #376 grill 軸 — 修修明確 ack 此 pattern 也適用）+ 其他「修修親自閱讀 + annotate 的 source」類型。Single-file ingest 維持給「無 annotation 場景」（PubMed digest cron auto-batch）。
- **Storage cost**：每篇 paper 在 Sources 從 1 file → 2 file，annotated 比 raw 大（雙語 + annotation callouts），整體 ~2.5x。對個人 KB 規模 negligible。
- **Re-extract 成本**：ingest 時 raw 檔重跑 Trafilatura / pymupdf4llm 一次，毫秒級。為了 zero-trust 純度可接受。
