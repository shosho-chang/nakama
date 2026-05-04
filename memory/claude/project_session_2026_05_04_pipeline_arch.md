---
name: 收工 — 2026-05-04 內容流程七層架構凍結 + Line 2 起手準備
description: CONTENT-PIPELINE.md repo root + 三條 line × 七 stage 兩矩陣 + 4 結構性觀察 + anchor planning rule；Nami #332 + Franky #333 deploy verified；下個 session 開 Line 2 讀書心得手跑流程
type: project
created: 2026-05-04
---

修修 5/4 早問代辦 → 七層架構成形對話 → 凍結 CONTENT-PIPELINE.md + memory rule + Line 2 手跑流程準備。

## 1. 七層內容流程架構凍結

新檔 [CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) repo root（跟 ARCHITECTURE.md / CONTEXT-MAP.md 並列三大架構 lens — 元件 / bounded context / 內容工作流）。

七階段：1 收集 / 2 閱讀註記 / 3 整合 / 4 原子文章 / 5 多 channel 製作 / 6 發布 / 7 監控。

兩矩陣：
- **Lines × Stages**：3 條 line（Line 1 拆 1a 一般訪談 / 1b 訪問新書作者）× 6 stage 主矩陣 + Stage 5 sub-matrix（每 line × 4 channel）
- **Agents × Stages**：7 agent × 7 stage 對照職責

**4 個結構性觀察**：
1. Discovery → Production → Insight 是斷迴路
2. Brook over-loaded（13+ 模組 5 子領域）
3. Annotation 沒 owner（Line 2 critical path）
4. 發布只有 WP（Stage 6 唯一缺口）

**3 件結構性優先序**：IG 半自動發布 / Annotation → KB 整合 / SEO → Zoro 反向 feed。

## 2. v2 重要 delta（vs v1 三條 line 2026-04-30）

- **Script-Driven Video 不是 Line 4** — 是 Stage 5 影片 channel 工具，三條 line 都可走
- **Line 1 拆兩子模式** — 1a 一般訪談 / 1b 訪問新書作者（後者走 Line 2 Stage 2-3）
- **Stage 4「原子文章」三形式** — Line 2 修修手寫（agent 不介入）/ Line 1 transcribe / Line 3 LLM 輔助
- **Stage 5 多 channel** — 4 channel：影片 / 部落格 / FB / IG
- 標 [project_three_content_lines.md](project_three_content_lines.md) v1 為 SUPERSEDED 保留歷史

## 3. Anchor planning rule 立規

[feedback_pipeline_anchored_planning.md](feedback_pipeline_anchored_planning.md) — 「開發 X / 下一步做什麼」對話前必 anchor stage + 對照矩陣 + 檢視優先序，禁止散著挑。

CLAUDE.md 加 reference 行 + 規劃原則。

## 4. Deploy verify（5/4 凌晨 ship）

- ✅ `nakama-gateway` active 1h 25min，3 bot (nami/sanji/zoro) Socket Mode connected — PR #332 Nami round 3 生效
- ✅ `.env` `FRANKY_R2_PREFIXES=shosho/,fleet/` — PR #333 per-prefix env 就位，明早 cron 跑 per-prefix verify

## 5. Line 2 起手準備（下個 session）

修修 5/4 確定下個 session 開 Line 2 讀書心得，先做：閱讀器 + Ingest + Annotation。

**起手前 sanity check 三件 5 分鐘事**（下個 session 第一步）：
- Robin Reader 開一本中文 EPUB 看能讀嗎（**未實測過中文書**）
- `/project-bootstrap` 開「讀書心得」project 看 template 還對嗎
- EPUB 雙語 reader（之前實測 PubMed PDF）能讀英文書嗎

**Line 2 流程**：選書 → Reader 讀（中/英）→ 邊讀邊 annotation（`==highlight==` + `> [!annotation]`）→ Robin ingest 書 + annotation → 修修在 Project 頁面手寫心得（**Stage 4 LLM 不介入**）→ Stage 5 影片+部落格+FB+IG 多 channel

**禁止 over-design**：
- 不在修修手跑前設計 annotation schema
- 不先 build「reading session 統整」功能
- 不先想 Stage 5 怎麼接
- 等修修手跑痛點浮現再 grill 凍結需求

**修修會記下痛點**（用 Slack DM Nami 或 vault Inbox），手跑完一本後一起 grill → anchor Stage 2/3 凍結需求。

## Reference

- [CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) — 七層架構永久 reference
- [project_content_pipeline_arch.md](project_content_pipeline_arch.md) — 架構摘要 + 4 觀察 + 優先序
- [feedback_pipeline_anchored_planning.md](feedback_pipeline_anchored_planning.md) — anchor 規劃 rule
- [project_three_content_lines.md](project_three_content_lines.md) — v1（SUPERSEDED 2026-05-04）
