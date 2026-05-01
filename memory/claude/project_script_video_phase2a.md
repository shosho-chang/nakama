---
name: Script-Driven Video Production Phase 2a — PRD #310 + ADR-015 + Plan
description: 修修最高價值 workflow 自動化專案 Phase 2a 進度凍結 — grill 7 分岔結論 + Phase 1 PRD approved + ADR-015 Accepted + Plan 5 slice 拆好待 to-issues
type: project
created: 2026-05-02
---

修修 2026-05-02 grill 凍結「腳本式 YouTube 影片自動化」workflow。Phase 0 grill closed，Phase 1 PRD approved，Phase 2a artifacts 全寫完，等 commit + PR 後進 Phase 2b（to-issues 拆 vertical slice）。

## Phase 進度（嚴格遵 feedback_dev_workflow 6 phase）

- ✅ Phase 0 grill — `/grill-with-docs` 走 7 分岔（Q1/Q3/Q4-1234/Q5/Q7）
- ✅ Phase 1 PRD — `/to-prd` 提交 #310，修修 approved 2026-05-02
- 🔄 Phase 2a — ADR-015 Accepted + Plan 寫完 + CONTEXT-MAP 更新（待 commit + PR）
- ⏸ Phase 2b — `/to-issues` 拆 vertical slice issues（待 Phase 2a PR merge）
- ⏸ Phase 2c — `/github-triage` 每 slice 標 `ready-for-agent` + agent brief + `sandcastle` label
- ⏸ Phase 3 — sandcastle dispatch AFK 解 issue（4/4 戰績 unblock，Mac AFK 副機 also ready）
- ⏸ Phase 4 — multi-agent review（替代 ultrareview）
- ⏸ Phase 5 — squash merge + memory + CHANGELOG

## Grill 7 分岔凍結結論

| Q | 凍結結論 |
|---|---|
| Q1 架構 | 獨立 video module（`video/` Node.js + Remotion + TS）+ Brook orchestrator（`agents/brook/script_video/` Python） |
| Q3 mistake removal | Marker-based α 為主（拍掌 audio spike）+ Alignment-based β fallback；修修整段重唸習慣 |
| Q4-1 PDF library | per-episode `refs/` + 全局 `_cache/embeddings/<sha256>.npy` |
| Q4-2 Robin metadata | 接（read-only metadata），不接 chunk text |
| Q4-3 Embedding | BGE-M3 本地（cross-lingual） + Qwen3-Embedding-0.6B Phase 2 swap 接口預留 |
| Q4-4 Quote 索引 | state.db + sqlite-vec virtual table（3 新 table） |
| Q5 Phase 1 component | 6 個（ARollFull / TransitionTitle / ARollPip / DocumentQuote / QuoteCard / BigStat） |
| Q7 Output 路徑 | DaVinci timeline (FCPXML 1.10) — Phase 1 不直出 mp4 |
| 副產品 | 中文 SRT 從乾淨 timeline 直出（不另跑 LLM 翻譯，narration 全程中文） |
| 架構反轉 | Remotion **不 render 整支影片**，只 render B-roll segments 為個別 mp4 |

## 2026-05-02 技術選型調研重要發現

1. **Qwen3-Embedding-0.6B vs BGE-M3** — Qwen3 在 MMTEB benchmark +7.9% 超越 BGE-M3（Aryan Kumar 2026 Medium 比較）。Phase 1 仍選 BGE-M3（ecosystem 成熟），Phase 2 swap 接口預留
2. **DaVinci FCPXML 支援邊界** — Resolve 18+ 支援 1.10，**1.12 不支援 opening**；1.11 仍有 quirk。FCPXML 1.10 是 conservative 最佳選擇
3. **sqlite-vec v0.1.0 stable** (Aug 2024) — pure C，pypi binding 穩定，LangChain integrated，作者 v1.0 maintenance roadmap
4. **PyMuPDF4llm 1.27+** — 已支援 `extract_words=True` + `page.search_for` + `page.add_highlight_annot` 全套，nakama requirements.txt 已 import
5. **Remotion** — 4.x production-ready；2027 預測 45% motion graphics 來自 AI-assisted code generation；Anthropic 自家 Remotion Agent Skills 可參考 prompt patterns

## Plan 5 Slice 拆分（待 to-issues 凍結）

| Slice | 端到端 demo | 工程天 | Type | Blocked by |
|---|---|---|---|---|
| 1. 骨幹 | 最小 `[aroll-full]` script → FCPXML → DaVinci import smoke | ~3 | HITL | — |
| 2. 6 場景 | 6 Remotion components Studio preview + 渲染 mp4 segment | ~2 | HITL | Slice 1 |
| 3. 引用 PDF | `[quote ... page=87]` exact match → highlight 動畫 mp4 | ~2 | HITL | Slice 2 |
| 4. Embedding | `[quote ... auto]` cross-lingual fuzzy match | ~2 | AFK | Slice 3 |
| 5. 端到端 | 修修舊片 dry-run + 對比人工剪 | ~1 | HITL | Slice 4 |

合計 ~10 工程天（agent 工時）。Sandcastle AFK 並行可顯著壓縮 wall-clock。

## 文件 / artifacts

- PRD：[#310](https://github.com/shosho-chang/nakama/issues/310)（approved 2026-05-02）
- ADR：[docs/decisions/ADR-015-script-driven-video-production.md](../../docs/decisions/ADR-015-script-driven-video-production.md)（Accepted）
- Plan：[docs/plans/2026-05-02-script-driven-video-production.md](../../docs/plans/2026-05-02-script-driven-video-production.md)
- CONTEXT-MAP：加 video module + 6 個新 glossary 詞 + Flagged ambiguities 對「Line N」澄清
- branch：`docs/script-driven-video-production-prd`（待 Phase 2a 全 commit + push + 開 PR）

## 跟既有專案的 cross-ref

- 跟 `project_podcast_theme_video_repurpose.md` 不同 — 那條是「訪談錄音抽亮點剪 10-20 min YT 影片」，這條是「腳本式照稿錄製 + 自動 B-roll」。Sibling project，input 完全不同
- 跟 `project_three_content_lines.md` Line 1/2/3 不同 — 那是 RepurposeEngine fan-out parallel；這條 sequential pipeline，**不是 Line 4**
- 跟 ADR-014 RepurposeEngine — 不繼承不擴展，sibling
- 跟 ADR-001 Brook = Composer — 仍合理（script + B-roll 標記是 compose specialist）
- 跟 ADR-013 transcribe — Stage 1 直接重用 WhisperX

## How to apply

修修確認 Phase 2a PR merged → 立刻進 Phase 2b：跑 `/to-issues` skill 把 plan 5 slice quiz 修修確認 → 各 slice gh issue create → 進 Phase 2c。
