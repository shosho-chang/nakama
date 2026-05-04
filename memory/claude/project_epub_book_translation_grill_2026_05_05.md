---
name: epub_book_translation_grill_2026_05_05
description: EPUB 整本翻譯升級 grill prep（Stage 2 閱讀 / Line 2 critical path）；3-level 升級候選 + 12 grill 議題凍結
type: project
---

**EPUB 整本翻譯升級 — pre-grill 凍結 2026-05-05**

修修今天問翻譯英文 EPUB 流程，提兩個痛點：(1) 跨章節術語/人名飄移，(2) 書類型多樣需 genre routing。研究完社群主流解法 + 寫好 grill prep doc 在 [`docs/plans/2026-05-05-epub-book-translation-grill-prep.md`](../../docs/plans/2026-05-05-epub-book-translation-grill-prep.md)。下個 session 直接 grill。

**關鍵發現**：
- Sample-then-extract glossary 是個人工具圈共識（[`deusyu/translate-book`](https://github.com/deusyu/translate-book) Claude Code skill 完美對位 nakama stack）
- Genre routing 是社群空白（KazKozDev README 列了沒實作，bilingual_book_maker / Immersive Translate 都沒）— 修修方向是補空白不是重複造輪子
- 多 agent / RAG / 1M whole-book / TM 都是 over-engineering，已凍結不做

**3-level 升級候選**（plan doc §4）：
- 級別 1（1 day）：genre slot + glossary 動態注入 + Anthropic prompt cache → 解痛點 2 + cost ↓ 90%
- 級別 2（2-3 day）：sample-extract first-pass + sliding window prev_excerpt → 解痛點 1
- 級別 3 暫不做：等真實翻 3-5 本書再評估

**12 grill 議題待拍板**（plan doc §6）：genre 來源 / glossary 多層合併 / 取樣策略 / cache TTL / 自動偵測 vs 手動 / 短書走哪條 / character bible / 與 textbook-ingest 整合 / EPUB upload UI / #367 解耦 / 成本天花板 / 驗收標準

**Why**：Line 2 critical path（讀書心得）卡 Stage 2 翻譯品質，修修要看英文書必經這裡。
**How to apply**：下個 session grill 起手讀 plan doc，從 Q1 逐題拍板；grill 完開 ADR + PRD GH issue + task prompt。**現有 [`shared/translator.py`](../../shared/translator.py) 寫死學術 prompt + 189 yaml 全塞 + 無 cache，三大 gap 都在 plan doc §1.3 記錄**。
