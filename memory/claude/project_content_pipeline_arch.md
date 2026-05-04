---
name: Content Pipeline 七層架構（CONTENT-PIPELINE.md）
description: 內容工作流七層 stage 凍結 2026-05-04 + Lines/Agents × Stages 兩矩陣 + 4 個結構性觀察 + 3 件結構性優先序；規劃用此架構討論
type: project
created: 2026-05-04
---

修修 2026-05-04 grill 凍結內容流程七層架構，要求未來規劃功能必 anchor 在某 stage 討論，不再 spontaneous 開發。文件落 [CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) repo root（跟 ARCHITECTURE.md / CONTEXT-MAP.md 並列三大 lens）。

## 七階段

1. Discovery 資料收集
2. Reading + Annotation 資料閱讀與註記
3. Synthesis / Ingest 資料整合
4. **Atomic Content** 資料輸出（原子文章 — single source of truth canonical content）
5. Multi-channel Production 內容製作（4 channel：影片 / 部落格 / FB / IG）
6. Publishing 內容發布
7. Monitoring + Optimization 內容監控與優化

**閉環設計**：Stage 7 應 feed 回 Stage 1（內容效能 inform 下次主題選擇）— 但目前迴路斷裂。

## Stage 4 原子文章三種形式

| Line | Atomic content 形式 | 產生方式 |
|---|---|---|
| Line 1 Podcast | 字幕檔（.srt） | Transcribe pipeline 自動產出 |
| Line 2 讀書心得 | **修修在 Project 頁面手寫** | 修修本人手寫（最珍貴的原始輸出，**LLM 不介入**） |
| Line 3 文獻科普 | LLM 輔助 outline + 修修加工 | Robin KB 抽 → outline → Brook compose → 修修審稿 |

**關鍵設計原則**：Line 2 心得是修修自己的聲音，agent 只在 Stage 3（整合素材到 KB）+ Stage 5（拿手寫稿做 channel）服務，不介入手寫過程。修修這週要手跑 Line 2 流程一次，後續再決定要不要加輔助功能。

## Stage 5 多 channel 製作

每條 line 從原子文章 fan out 成 4 channel，各 channel 共用工具：

| Channel | 工具 |
|---|---|
| 影片 | **Script-Driven Video pipeline**（不是獨立 line，是影片 channel 工具） |
| 部落格 | Brook compose + Blog renderer |
| FB post | Brook FB renderer |
| IG carousel | Brook IG renderer |

## Lines 重新整理（2026-05-04 grill v2）

只有三條 line（不是四條）：

- **Line 1 Podcast** — 兩子模式：
  - 1a 一般訪談（直接 transcribe → atomic）
  - 1b 訪問新書作者（先走 Line 2 Stage 2-3 讀書，再 transcribe）
- **Line 2 讀書心得** — 中文書 / 英文書（後者雙語閱讀） + annotation；Stage 4 修修手寫
- **Line 3 文獻科普** — Zoro 主題 → Robin KB → Brook outline → 修修加工

**Script-Driven Video 不是 Line 4**，是 Stage 5 影片 channel 的製作管線，三條 line 都可走它。

## 兩矩陣

- **Lines × Stages**：3 條 line（含 Line 1 兩子模式）× 7 stage 主矩陣 + Stage 5 sub-matrix（每 line × 4 channel）
- **Agents × Stages**：7 agent × 7 stage 對照職責

## 4 個結構性觀察（不是 feature 是架構迴路問題）

1. **Discovery → Production → Insight 是斷的迴路**：SEO 中控台 audit 結果不回灌 Zoro discovery；IG/YT 0 analytics 回收
2. **Brook over-loaded**：Composer + SEO + Repurpose + script_video + style/compliance 5 子領域，13+ 模組，第二大 agent 兩倍。要按 sub-context 切目錄但不急拆 agent
3. **Annotation 沒 owner**：Reader UI 已支援標註語法，但寫到哪 / 誰讀 / 何時用完全沒設計 → Line 2 critical path blocker
4. **發布只有 WP**：Line 1 IG / Script-Driven Video YT / Newsletter / Community 全 0 實作；Line 1 IG carousel 最痛

## 3 件結構性優先序（如果只能挑 3 件）

1. **IG 半自動發布管線**（Stage 6）— Line 1/2/3 IG carousel 都卡這。不用整片 IG API，先 carousel 圖檔 batch + Buffer/Later 排程匯入
2. **Annotation → KB 整合 owner**（Stage 2→3）— Line 2 critical path；具體實作等修修這週手跑流程後決定（**修修明確要求 Stage 4 手寫不介入，但 Stage 3 整合可動**）
3. **SEO 中控台 → Zoro 反向 feed**（Stage 7→1）— 一條 SQL view + Zoro report renderer 加 section

**這三件做完才推 Line 3 Stage 1/4 / Script-Driven Video Slice 2-5**。

## 三條 Line readiness（v2）

- **Line 1a 一般訪談**：Stage 4-5 ✅；卡 Stage 6 IG/YT/FB 自動發 + Slice 10 reviewer
- **Line 1b 訪問新書作者**：Stage 2-3 走 Line 2 流程；Stage 5 影片 channel 在 Slice 2-5
- **Line 2 讀書心得**：Stage 2-3 缺 annotation owner，無法起步；Stage 4 修修這週手跑後再決定
- **Line 3 文獻科普**：Stage 1 缺 Zoro topic discovery、Stage 3 缺主題 retrieval、Stage 4 缺 kb-synthesize skill

## Line 2 手跑流程（修修 2026-05-04 commit）

修修明確要求這週手跑 Line 2 流程一次，痛點浮現再凍結：
- Stage 2 閱讀（中/英書 + annotation）
- Stage 3 整合進 KB
- Stage 4 在 Project 頁面手寫心得
- Stage 5 走 4 channel（影片 + 部落格 + FB + IG）

**我不可在他手跑前 over-design Stage 2-3 annotation 細節**。等他丟痛點來再加。

## 規劃原則（強制 anchor）

詳見 [feedback_pipeline_anchored_planning](feedback_pipeline_anchored_planning.md) — 所有「開發 X / 下一步做什麼」對話必 anchor 七層、對照矩陣、檢視優先序。

## Reference

- [CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) — 完整文件（永久 reference）
- [ARCHITECTURE.md](../../ARCHITECTURE.md) — 元件 lens
- [CONTEXT-MAP.md](../../CONTEXT-MAP.md) — bounded context lens
- [project_three_content_lines.md](project_three_content_lines.md) — 三條 line 凍結（2026-04-30，本架構 superset）
- [project_script_video_phase2a.md](project_script_video_phase2a.md) — Script-Driven Video 軸線
