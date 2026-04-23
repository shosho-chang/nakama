---
name: Usopp Publisher Slice B — PR #77 審查完畢，等修修授權 merge
description: 2026-04-23 PR #77 四次 commit 跨三台 review（ultrareview + Mac 3-agent + follow-up）解 11 個 blocker；ready for merge
type: project
tags: [usopp, phase-1, pr-77, slice-b, publisher]
---
## 現況（2026-04-23 session end）

PR #77 `feature/usopp-slice-b` — 4 commits on branch，基於 `23790dd`（main with #78-83 merged in）。ready to squash merge，等修修授權。

## 四次 commit 時間線

| Commit | 內容 |
|---|---|
| `1d8f7e9` | 初版 Slice B：publisher.py + compliance + seopress_writer + litespeed_purge + migrations/002 + 44 tests |
| `ca9280e` | 桌機 ultrareview 發現 2 個 score-72 blocker：WP*Error cross-stage propagation + reviewer_compliance_ack escape hatch |
| `cedf9ac` | Mac 3-agent parallel review 挑 3 個 low-risk 安全修：compliance alt-text bypass + `.env.example` WP key drift + `LITESPEED_PURGE_METHOD` 缺 |
| `7841f4e` | 我接 4 個需要 publisher.py 動刀的：orphan probe from `media_ready` state、naive `scheduled_at` tz check、衛福部 vocab 擴充（14 新詞）、簡體中文 mirror 表（38 chars） |

## 最終狀態

- **951 tests pass / 1 skip / 0 regression** (baseline 941 after merge from main)
- ruff check + format clean
- Slice B 總計 **50 tests**（14 publisher + 24 compliance + 8 seopress_writer + 10 litespeed_purge — 全自己的）

## 決策紀錄

- **Bug 1 修法選 (a) probe unconditionally**：orphan probe 從 `state == 'claimed'` gated 改成 advisory lock 內無條件跑，任何 crash→resume 路徑都先探測、再決定 adopt 或 create
- **Bug 5 修法選 mirror approach**：vocab 有限（50+ 詞），mirror 表 38 個字 cover 全部 non-identity 字元，零新 deps；OpenCC 能 cover 完整 TC↔SC 但多一個依賴、語意更大
- **Borderline 不做的**：`self.wp._site_id` 私有 attr（2 call sites，Slice C 加 property 時一起）、`_advance(**fields)` f-string（所有 caller literal，零 runtime 風險）、`wp-nakama-publisher-role.md` 的 delete 權限（ADR-005b §7 明確允許「錯發立即回收」）

## 跟 ADR-005b 的對齊度

全部 §1/§2/§3/§4/§5/§10 實作到位。未完成的延到 Slice C：
- `/healthz` WP 連線檢查 hook
- `agents/usopp/__main__.py` daemon loop
- Docker WP 6.x + SEOPress 9.4.1 staging E2E
- Day 1 LiteSpeed endpoint 實測（runbook 框架已建）

## Merge 後本機接手做

- **Phase 1 foundation borderline #1**（PR #72）：`PRAGMA synchronous=NORMAL + busy_timeout=5000` 移到 `_get_conn()` — 因為要動 `shared/state.py`，PR #77 merge 後 clean 再做
- ~~Slice C daemon + E2E~~（等 LiteSpeed Day 1 實測 + WP staging 環境）
- 6 foundation borderline 的剩 1 個（另 5 個 Mac 已在 PR #81/#82/#83 處理）

## 相關記憶

- [feedback_dual_review_complementarity.md](feedback_dual_review_complementarity.md) — PR #77 實證 ultrareview vs 本地 3-agent 互補
- [feedback_model_construct_bypasses_validators.md](feedback_model_construct_bypasses_validators.md) — Bug 2 的 pydantic lesson
- [project_usopp_slice_a_merged.md](project_usopp_slice_a_merged.md) — Slice A（PR #73）上游
- [project_phase1_foundation_pr.md](project_phase1_foundation_pr.md) — PR #72 foundation
