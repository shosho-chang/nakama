---
name: 三條內容生產線需求（2026-04-30 凍結 / 2026-05-04 superseded by CONTENT-PIPELINE.md）
description: 修修最緊急的內容生產 Line 1（Podcast→訪談+FB+IG）/ Line 2（讀書心得→blog+YT+IG）/ Line 3（文獻→科普文章→IG）；Line 1 優先；2026-05-04 v2 grill 已細化為七層架構
type: project
created: 2026-04-30
updated: 2026-05-04
---

> ⚠️ **2026-05-04 SUPERSEDED**：本檔內容已被 [CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) + [project_content_pipeline_arch.md](project_content_pipeline_arch.md) 取代並細化。下方 v1 內容保留為歷史紀錄，**規劃時請以 CONTENT-PIPELINE.md 為準**。
>
> v2 重要 delta：
> - **Script-Driven Video 不是 Line 4**，是 Stage 5 影片 channel 的工具（三條 line 都可走）
> - **Line 1 拆兩子模式**：1a 一般訪談 / 1b 訪問新書作者（後者走 Line 2 Stage 2-3）
> - **Stage 4「原子文章」概念**：Line 2 是修修手寫（agent 不介入）/ Line 1 是 transcribe / Line 3 是 LLM 輔助
> - **Line 2 流程修修這週手跑**，痛點浮現再決定 Stage 2-3 annotation 細節，不可 over-design

---

## v1 原始內容（2026-04-30）

修修 2026-04-30 凍結三條內容生產線，**Line 1 是當前最緊急功能**。SEO 部落格體檢功能延後。

## Line 1 — Podcast → 訪談多 channel（**最緊急**）

**Source**：1 小時左右人物訪談錄音

**Pipeline**：
1. 錄音 → SRT（既有 `transcribe` skill production）
2. 校正後 SRT → 人物訪談 blog 文章
3. Blog → FB 社群媒體貼文
4. Blog → IG Carousel 文字序列（6-8 張卡的文字內容）

**已有**：
- `transcribe` skill production（FunASR + Auphonic + Claude Opus 校正 + Gemini 2.5 Pro 多模態仲裁）

**缺**：
- SRT → narrative cleanup（verbatim vs editorial 未決）
- Blog → 三 channel repurpose engine
- Speaker diarization 接法
- Approval queue 整合粒度

## Line 2 — 讀書心得 → 多 channel

**Source**：Robin Reader 一邊閱讀一邊註解劃線的電子書

**Pipeline**：
1. Robin Reader 讀電子書 + 註解 + 劃線
2. Robin ingest 整本書（DB + Wiki）+ 統整 reading session
3. 協助寫一篇完整讀書心得
4. 心得 → blog / YouTube 講稿 / IG 知識圖卡 Carousel

**已有**：
- Robin Reader 雙語（PR #71）
- Textbook ingest v2 ADR-011 凍結，Step 1/2 merged

**缺**：
- Step 3 實作（in flight）
- Annotation → source page append（P3 backlog）
- Reading session 統整 → 心得 outline
- 心得 compose（Brook）
- Repurpose engine（同 Line 1）

## Line 3 — 文獻 → 科普文章 → 多 channel

**Source**：身心健康主題的學術文獻

**Pipeline**：
1. **Zoro topic discovery**（搜尋熱議主題 + 建議候選）
2. 修修在 Project 開頁面（如「肌酸的妙用」前例）
3. **Robin** 從 KB 抽該主題相關已 ingest 文獻 → source summary + 標題候選 + 內容素材
4. **Brook** 接 outline 寫 final article
5. 文章 → Line 1 同樣的 repurpose engine（IG 知識圖卡 + 社群貼文）

**已有**：
- Zoro keyword research production（PR #102-108）
- Robin PubMed digest + ingest
- Project bootstrap skill（Nami）
- Brook compose pipeline production（PR #78）

**缺**：
- **Zoro topic discovery mode**（≠ keyword research，是「告訴我寫什麼」不是「給定主題給關鍵字」）
- 「給主題 → 從 KB 抽相關文獻 → outline + 標題」editorial 整合（Robin job）
- Brook 接 Robin outline 寫文章的整合介面
- Repurpose engine（同 Line 1）

## 共通 Building Block

**Repurpose engine** 是三條 line 的公因式：
- 三 channel：blog / FB post / IG carousel 文字
- 應該蓋一次共用，不每條 line 各做
- 三條 source agent 對它輸出 standardized markdown（含 frontmatter 標 `intent: interview / book-reflection / health-explainer`），repurpose 看 intent 走不同 template
- 詳見 [project_repurpose_flow.md](project_repurpose_flow.md) backlog

## 起手順序拍板

| # | 動作 | 理由 |
|---|---|---|
| 1 | **Line 1 grill + PRD**（當前 task） | 最快收 ROI、能順便驗 repurpose engine、訪談是真實業務需求 |
| 2 | Line 1 落地 | repurpose engine 蓋一次三 line 共用 |
| 3 | Line 3 接 repurpose | building block 全在、editorial 編排相對輕量 |
| 4 | Line 2 落地 | 距 production 最遠（textbook v2 + annotation flow 兩件未完） |

## Brook 角色已釐清（ADR-012）

- **Zoro** = 對外搜尋（topic discovery + keyword research + trends）
- **Robin** = 對內知識庫（ingest + summary + outline + 文獻 retrieval）
- **Brook** = compose specialist（接 outline 寫 final article + 三 channel repurpose）

Line 3 不需要為 Brook 找位置 — 他天生就是「最後一棒」。
