---
name: Script-Driven Video Production Phase 2b/2c closed → Phase 3 sandcastle Slice 1 in-flight
description: 修修最高價值 workflow 自動化專案 — Phase 2a/2b/2c 全 closed（5 slice issue 建好 + triaged）+ Phase 3 sandcastle dispatch Slice 1 #313 in-flight；下次 session 接手 = 看 sandcastle PR 出來 review + DaVinci import smoke + merge
type: project
created: 2026-05-02
updated: 2026-05-02
---

修修 2026-05-02 grill 凍結「腳本式 YouTube 影片自動化」workflow。Phase 0/1/2a/2b/2c 全 closed，Phase 3 sandcastle dispatch Slice 1 (#313) in-flight。

## Phase 進度（嚴格遵 feedback_dev_workflow 6 phase）

- ✅ **Phase 0 grill** — `/grill-with-docs` 走 7 分岔
- ✅ **Phase 1 PRD** — `/to-prd` 提交 #310，修修 approved
- ✅ **Phase 2a** — PR #311 merged 進 main `86a5775`：ADR-015 Accepted + Plan + CONTEXT-MAP + memory
- ✅ **Phase 2b** — `/to-issues` 5 slice issue 建好（#313/#314/#315/#316/#317）+ blocked-by chain 接通；quiz 4 問題自答（granularity ✅ / 依賴鏈 ✅ + Slice 3 留 fuzzy stub note / Slice 1 不拆 ✅ / 1 AFK + 4 HITL ✅）
- ✅ **Phase 2c** — triage 完成：#313/#316 加 sandcastle label + ready-for-agent / #314/#315/#317 加 ready-for-human；PRD #310 留 tracking comment
- 🔄 **Phase 3** — sandcastle dispatch Slice 1 (#313) in-flight（Mac sandcastle-test 跑 docker，agent 跑完 PR 自動 merge-back-HEAD；下次 session pickup）
- ⏸ **Phase 4** — multi-agent review（替代 ultrareview）
- ⏸ **Phase 5** — squash merge + memory + CHANGELOG

## 5 Slice 拆分凍結（Phase 2b 落 issue 對應）

| Slice | Issue | Title | Type | Blocked by | Sandcastle 適用 |
|---|---|---|---|---|---|
| 1 | #313 | 骨幹 — DSL parser + WhisperX align + Mistake removal + FCPXML 1.10 emit | HITL | None | ✅（4/5 acceptance 自動驗 + DaVinci smoke 修修 review 階段做） |
| 2 | #314 | 6 場景 Remotion components + Studio preview + DSL parser 5 directive | HITL | #313 | ❌（美學 first-class，hands-on dispatch） |
| 3 | #315 | 引用 PDF — PyMuPDF + bbox + DocumentQuote（exact match only） | HITL | #314 | ❌（highlight 動畫品質 review） |
| 4 | #316 | Embedding — BGE-M3 + sqlite-vec + cross-lingual fuzzy match | AFK | #315 | ✅（**最佳 sandcastle 候選**） |
| 5 | #317 | 端到端 dry-run + 對照人工剪 + 寫 dry-run 報告 | HITL | #316 | ❌（修修主導全程） |

## Quiz 額外 note（落實在 issue 內）

- **Slice 3 fuzzy 接口 stub**（#315 acceptance 明確要求）— `pdf_quote.py` 留 fuzzy abstract 接口 raise `NotImplementedError("fuzzy match: implemented in Slice 4")`；Slice 4 擴展不重寫。**防 Slice 4 dispatch 要重寫 Slice 3 已 merged code**
- **Slice 1 不拆**（#313 acceptance 5 條：CLI / fixture cut points / xmllint --schema / DaVinci import smoke / CI 全綠）— 互依強，4 件事拆了 sandcastle agent 要跨 PR 跑、context 切碎
- **Slice 1 HITL bit 延後**（#313 type 標 HITL 但 sandcastle eligible）— 4/5 acceptance 自動驗（CLI / fixture / xmllint / CI），唯一 HITL bit 是 DaVinci import smoke，延後到 PR review 階段做即可，不阻擋 dispatch

## Grill 7 分岔凍結結論（不變）

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
| 副產品 | 中文 SRT 從乾淨 timeline 直出 |
| 架構反轉 | Remotion **不 render 整支影片**，只 render B-roll segments 為個別 mp4 |

## Sandcastle dispatch 紀錄

- **Mac sandcastle**（PR #306+#307+#308 chain merged 2026-05-02）+ Docker image `sandcastle:nakama` (2.51GB) ready
- **Round 4** dispatch task ID `b4zqhfvzv`（process running in `/private/tmp/claude-502/.../tasks/b4zqhfvzv.output`）
- 跑 `cd ~/Documents/sandcastle-test && npx tsx --env-file=.sandcastle/.env .sandcastle/main.mts`
- agent: `claude-sonnet-4-6`，maxIterations: 5，branchStrategy: merge-to-head
- 預期：抓 #313（first non-blocked sandcastle issue）→ TDD red-green → ONE commit → merge back to nakama main HEAD
- #316 blocked by #315 agent 應跳過 → output `<promise>COMPLETE</promise>`

## 下次 session 接手起手點

1. 讀本記憶 + Plan + ADR-015 確認凍結結論
2. **看 sandcastle Slice 1 dispatch 結果**：
   - `gh pr list --state all --limit 5`（看 sandcastle 是否 PR opened + merged）
   - `gh issue view 313`（看是否 closed）
   - 看 `/private/tmp/.../tasks/b4zqhfvzv.output` 看 dispatch log（如還沒結束）
   - 如 PR 已 merged → Slice 1 done，dispatch Slice 2 hands-on（with Claude Design 視覺探索 + Claude Code 落地）
   - 如 PR 卡住 / escalated needs-info → 修修接手手動或 grill issue spec
   - 如 dispatch 還在跑 → wait
3. **DaVinci import smoke**（修修 Slice 1 PR review 階段做）
4. Slice 2 → 3 sequential dispatch（hands-on，不走 sandcastle）
5. Slice 4 sandcastle dispatch（最佳 AFK 候選）
6. Slice 5 dry-run（修修主導）

## 工具 / 基礎設施 gotcha 留存

- **Auto-merge 不可用**：nakama repo (private + free tier) `gh pr merge --auto` 回 enablePullRequestAutoMerge 要 paid feature。**workaround**：background `gh pr checks <PR> --watch && gh pr merge --squash --delete-branch && git checkout main && git pull --ff-only` chain
- **Sandcastle 跑時 nakama working tree 必須乾淨**：dispatch 前 `git status --short` 確認；dispatch 期間不要在 main session 改 nakama 程式碼（避免 conflict）
- **Branch protection**：merge 條件「base branch policy prohibits the merge」可能在 CI in_progress 時觸發；merge state status `BLOCKED` 但 mergeable `MERGEABLE` = 等 CI pass 即可
- **PR 與 main divergence**：PR 開後 main 進新 commit會造成 MEMORY.md conflict（兩邊都加 entry）；解法 = `git merge origin/main` 手動編輯 conflict markers + commit

## 文件 / artifacts

- PRD：[#310](https://github.com/shosho-chang/nakama/issues/310)（approved 2026-05-02）
- ADR：[docs/decisions/ADR-015-script-driven-video-production.md](../../docs/decisions/ADR-015-script-driven-video-production.md)（Accepted）
- Plan：[docs/plans/2026-05-02-script-driven-video-production.md](../../docs/plans/2026-05-02-script-driven-video-production.md)（Final）
- 5 slice issue：#313 / #314 / #315 / #316 / #317
- PR #311 merged 進 main `86a5775`（Phase 2a：ADR + Plan + memory）
- PR #312 merged 進 main `b0dde94`（memory update）

## 跟既有專案的 cross-ref

- 跟 `project_podcast_theme_video_repurpose.md` 不同 — 那條「訪談抽亮點」，這條「腳本式照稿 + 自動 B-roll」
- 跟 `project_three_content_lines.md` Line 1/2/3 不同 — 那是 RepurposeEngine fan-out；這條 sequential pipeline，**不是 Line 4**
- 跟 ADR-014 RepurposeEngine — sibling 不繼承不擴展
- 跟 ADR-001 Brook = Composer — 仍合理
- 跟 ADR-013 transcribe — Stage 1 直接重用 WhisperX

## How to apply

下次 session 接手按上面「下次 session 接手起手點」5 步走。Sandcastle PR 出來 = pickup time。
