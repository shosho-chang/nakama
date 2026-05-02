---
name: Script-Driven Video Production Phase 2a closed → Phase 2b in-flight
description: 修修最高價值 workflow 自動化專案 — Phase 2a (PR #311 merged 進 main) closed loop；Phase 2b to-issues quiz 已發給修修待回 4 個確認問題後 gh issue create 5 slice
type: project
created: 2026-05-02
updated: 2026-05-02
---

修修 2026-05-02 grill 凍結「腳本式 YouTube 影片自動化」workflow。Phase 0 + Phase 1 + Phase 2a 全 closed，Phase 2b in-flight 等修修 quiz 回。

## Phase 進度（嚴格遵 feedback_dev_workflow 6 phase）

- ✅ **Phase 0 grill** — `/grill-with-docs` 走 7 分岔（Q1/Q3/Q4-1234/Q5/Q7）
- ✅ **Phase 1 PRD** — `/to-prd` 提交 #310，修修 approved 2026-05-02
- ✅ **Phase 2a** — PR #311 merged 進 main `86a5775` 2026-05-02：ADR-015 Accepted + Plan + CONTEXT-MAP + memory
- 🔄 **Phase 2b** — `/to-issues` quiz 已送修修，**等他回 4 個確認問題**：(1) granularity / (2) 依賴鏈 / (3) Slice 1 是否拆 1a/1b/1c / (4) HITL/AFK 標記
- ⏸ **Phase 2c** — `/github-triage` 每 slice 標 `ready-for-agent` + agent brief + `sandcastle` label
- ⏸ **Phase 3** — sandcastle dispatch AFK 解 issue（4/4 戰績 unblock，Mac AFK 副機 also ready）
- ⏸ **Phase 4** — multi-agent review（替代 ultrareview）
- ⏸ **Phase 5** — squash merge + memory + CHANGELOG

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

1. **Qwen3-Embedding-0.6B vs BGE-M3** — Qwen3 在 MMTEB benchmark +7.9% 超越 BGE-M3。Phase 1 仍選 BGE-M3（ecosystem 成熟），Phase 2 swap 接口預留
2. **DaVinci FCPXML 支援邊界** — Resolve 18+ 支援 1.10，**1.12 不支援 opening**；1.11 仍有 quirk。FCPXML 1.10 是 conservative 最佳選擇
3. **sqlite-vec v0.1.0 stable** (Aug 2024) — pure C，pypi binding 穩定
4. **PyMuPDF4llm 1.27+** — `extract_words=True` + `add_highlight_annot` 全套，nakama 已 import
5. **Remotion** — 4.x production-ready；Anthropic 自家 Remotion Agent Skills 可參考

## Phase 2b 待修修回的 4 個 quiz 問題

下次 session 接手第一件事 = 等修修回，然後 `gh issue create` 5 slice。

1. **Granularity 對嗎？** ~10 工程天 / 5 slice / 平均 2 天 / 每 slice demoable + 跨層完整
2. **依賴鏈對嗎？** Slice 1 → 2 → 3 → 4 → 5 嚴格 sequential（特別 Slice 4 fuzzy match 是擴展 Slice 3 的 `pdf_quote.py`）
3. **Slice 1 是否拆 1a/1b/1c？** 骨幹含 4 件事（parser / mistake removal / FCPXML / pipeline orchestration），互依強，建議**不拆**保 context 一致；但工程量 ~3 天偏長
4. **HITL/AFK 標記對嗎？** 4/5 HITL（Slice 1/2/3/5 美學或驗收）+ 1/5 AFK（Slice 4 Embedding 純技術整合）— 因 workflow 美學依賴度高，4 HITL 合理

## 5 Slice 拆分（修修 quiz 回後落 gh issue）

| Slice | Title | Type | Blocked by | User stories（PRD #310） |
|---|---|---|---|---|
| 1 | 骨幹 — DSL parser + WhisperX align + Mistake removal + FCPXML | HITL | None | US 1 / 4 / 5 / 12 / 15 |
| 2 | 6 場景 Remotion components + Studio preview | HITL | #1 | US 8 / 13 / 14 |
| 3 | 引用 PDF — PyMuPDF + bbox + DocumentQuote 渲染（exact match only） | HITL | #2 | US 2 / 9 |
| 4 | Embedding — BGE-M3 + sqlite-vec + cross-lingual fuzzy match | AFK | #3 | US 3 / 7 / 10 |
| 5 | 端到端 dry-run + 對照人工剪 + 寫 dry-run 報告 | HITL | #4 | US 1 (整體) / 6 |

## 工具 / 基礎設施 gotcha 留存

- **Auto-merge 不可用**：nakama repo (private + free tier) `gh pr merge --auto` 回 `Auto merge is not allowed for this repository (enablePullRequestAutoMerge)`。要 paid feature。**workaround**：background `gh pr checks <PR> --watch && gh pr merge --squash --delete-branch && git checkout main && git pull --ff-only` chain，watch CI pass 後自動 merge
- **Branch protection**：merge 條件「base branch policy prohibits the merge」可能在 CI in_progress 時觸發；merge state status `BLOCKED` 但 mergeable `MERGEABLE` = 等 CI pass 即可
- **PR 與 main divergence**：PR 開後 main 進新 commit（如 sandcastle background auto-merge）會造成 MEMORY.md conflict（兩邊都加 entry）；解法 = `git merge origin/main` 手動編輯 conflict markers + commit

## 文件 / artifacts

- PRD：[#310](https://github.com/shosho-chang/nakama/issues/310)（approved 2026-05-02）
- ADR：[docs/decisions/ADR-015-script-driven-video-production.md](../../docs/decisions/ADR-015-script-driven-video-production.md)（Accepted）
- Plan：[docs/plans/2026-05-02-script-driven-video-production.md](../../docs/plans/2026-05-02-script-driven-video-production.md)（Final）
- CONTEXT-MAP：加 video module + 6 個新 glossary 詞 + Flagged ambiguities 對「Line N」澄清
- PR：#311 merged 進 main `86a5775` 2026-05-02

## 跟既有專案的 cross-ref

- 跟 `project_podcast_theme_video_repurpose.md` 不同 — 那條「訪談抽亮點」，這條「腳本式照稿 + 自動 B-roll」
- 跟 `project_three_content_lines.md` Line 1/2/3 不同 — 那是 RepurposeEngine fan-out；這條 sequential pipeline，**不是 Line 4**
- 跟 ADR-014 RepurposeEngine — sibling 不繼承不擴展
- 跟 ADR-001 Brook = Composer — 仍合理
- 跟 ADR-013 transcribe — Stage 1 直接重用 WhisperX

## How to apply

下次 session 接手起手：

1. 讀本記憶 + Plan + ADR-015 確認凍結結論
2. 看修修是否回 Phase 2b quiz 4 問題；若回 → 立刻 `gh issue create` 5 slice（Parent #310 + Blocked by 真實 issue 編號 in dependency order）
3. 進 Phase 2c：`/github-triage` 每 issue 標 `ready-for-agent` + agent brief + `sandcastle` label
4. 進 Phase 3：sandcastle AFK dispatch
