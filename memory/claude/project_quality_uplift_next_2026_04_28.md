---
name: Quality Uplift 下一輪起點 — PR #190 LGTM 待 merge + 4 步 pickup checklist
description: PR #190 review LGTM 待 merge；subagent 重新盤點揭露 Phase 9 partial ship；下一輪建議順序：merge #190 → Phase 9 補洞 → Phase 6 Slice 2-4 → Phase 7 拍板
type: project
created: 2026-04-26
updated: 2026-04-27
originSessionId: 2026-04-26-night → 2026-04-27-handoff
---

2026-04-26 night → 2026-04-27 handoff：本 session 凍結 Phase 6 task prompt + 開 Slice 1 PR #190 + 跑 review LGTM + subagent 重新盤點 9-phase。修修「清理對話、下一輪繼續做你建議的順序」→ 不在本 session merge，把 4 步 pickup checklist 寫進 memo 給下次。**取代之前所有 quality_uplift_next memo**。

**Why:** PR #190 CI 全綠 + 6 檔 / 335 lines + threshold 哲學一致 + 4 條 state path 涵蓋；ready to squash merge 但修修要下一輪做。Subagent 重盤揭露 Phase 9 是 partial ship — branch protection / `/bridge/docs` FTS5 / memory pruning 三個 plan deliverable 沒做（PR #157 沒涵蓋）。整體下修為 **6/9 ✅ + 1/9 🟡 + 1/9 ⚠️ partial + 1/9 ❌**。

**How to apply:** 開新對話讀完 `MEMORY.md` → 讀本 memo → 預設下一步 = **squash merge PR #190**，然後依下面 4 步 pickup checklist 推進。

---

## 下一輪 pickup checklist（按性價比）

1. **squash merge PR #190**（立刻可做）— CI 全綠 / review LGTM / minor notes 不 block，跟 `feedback_pr_review_merge_flow.md` 流程
2. **Phase 9 partial ship 補洞**（1-2 天）— branch protection（修修 console 5 分鐘）+ `/bridge/docs` FTS5 search（我做）+ memory pruning（我做）→ 9/9 ✅
3. **Phase 6 Slice 2-4**（5 天）— Slice 2 FSM property（2d）→ Slice 3 schema round-trip（1d）→ Slice 4 agent E2E（2d）→ Phase 6 ✅
4. **Phase 7 staging 拍板**（卡修修）— 要不要花 $5-10/月開新 VPS；沒拍板 Phase 8 auto deploy 不能動

順序原因：步驟 1 收尾本 session 工作；步驟 2 容易快速勝利；步驟 3 是 Phase 6 收尾；步驟 4 卡決策不能搶先。

---

## PR #190 Review Verdict — **LGTM, ready to squash merge**

| 項目 | 評估 |
|---|---|
| CI | 全綠（lint-and-test 2m35s + lint-pr-title 5s）|
| Scope | 6 檔 / 335 lines，純 dev tooling + 補一個 endpoint test，無 production code 改動 |
| Threshold 設計 | 不退步 gate 哲學一致；baseline round-down 5%/10% buffer 合理 |
| Coverage gate script | exit code 標準（0/1/2）+ 邊角 case（missing module / pct=None / below threshold）三條都處理 |
| Runbook | 全面 — 量法、哲學、流程、Phase 6 後 slice 預期 |
| `/api/agents` 兩個新 test | 涵蓋 4 條 state path（online / idle / hold / offline）+ 跨 model sum + None defensive |

### 3 個 Minor notes（不 block，Slice 4 conftest 一起處理）

1. **`lambda **kw: fake_today` 不檢查真實 signature** — Slice 4 conftest autouse mock 應改用 `def fake_get_cost_summary(agent=None, days=7): return fake_today` 顯式對齊 `feedback_test_realism.md`
2. **9 agent set hardcoded in test** — 改 `from thousand_sunny.routers.bridge import AGENT_ROSTER; assert set(agents.keys()) == {a["key"] for a in AGENT_ROSTER}` 防 drift
3. **runbook table 跟 PR after 數字小退步** — `incident_archive` runbook 寫 baseline 93.13%、PR after 寫 93.08% 是 PR 加 2 個 test 後自然漂移（仍 ≥ 90%），不是 bug；runbook 「Baseline 2026-04-26」欄是「初始量」、跟 gate script 一致即可，無需修

---

## 9-phase plan 重新盤點（subagent 2026-04-27 重盤揭露 partial ship）

| # | Phase | 狀態 | 對應 PR / commit | 還欠什麼 |
|---|---|:---:|---|---|
| 1 | DR drill + secret rotation | ✅ | #146 + #187 | — |
| 2 | Backup A 升級 | ✅ | #147 + #154 | — |
| 3 | Observability foundation | ✅ | #152 | — |
| 4 | Incident postmortem | ✅ | #166 + #187 | — |
| 5 | Observability advanced | ✅ | 6 sub-PR (#168/#170/#175/#182/#184/#177) | — |
| **6** | **Test coverage** | 🟡 | d58ff51 (#190 待 merge) | **Slice 2-4 task prompt 已凍結未動工** |
| 7 | Staging + feature flags | ❌ | — | 整 phase 沒開（要錢/新 VPS） |
| 8 | CI/CD auto deploy | ❌ | — | blocked by 7 |
| **9** | **版控 polish + Doc A+** | ⚠️ **partial** | #157 (部分) | **branch protection 沒設 + `/bridge/docs` FTS5 沒做 + memory pruning 沒跑** |

整體：**6/9 ✅ + 1/9 🟡 + 1/9 ⚠️ + 1/9 ❌**（之前算 7/9 ✅ 是高估）。

---

## 細目

### 🟡 Phase 6 Slice 2-4（task prompt 已凍結，開新 branch 即動工）

- **Slice 2 — FSM property test**（hypothesis + `RuleBasedStateMachine` for approval_queue + alert dedupe edge cases）~ 2 天
- **Slice 3 — Schema round-trip test**（10 個 V1 schema parametrize）~ 1 天
- **Slice 4 — Agent E2E**（Robin/Brook/Zoro mock LLM + tmp_path vault）~ 2 天

Slice 4 要併處理 PR #190 review 3 個 minor notes（lambda signature / AGENT_ROSTER import / 不需動 runbook）。

### ⚠️ Phase 9 partial ship — 三個 plan 寫了沒做的小品

| 項目 | 工作量 | 誰做 |
|---|---|---|
| GH branch protection rule | console 5 分鐘 | 修修手動（runbook 在 #157） |
| `/bridge/docs` FTS5 search | ~1 天（可參照 Phase 5C logs FTS5 pattern） | 我 |
| Memory pruning（過期 project memory 月度清） | ~0.5 天 | 我 |

### ❌ Phase 7 + 8 — 卡修修決策

- 開新 staging VPS（約 $5-10/月）vs 復用桌機 docker-compose（免費但桌機開機才能 staging deploy）
- 沒 staging → Phase 8 auto deploy 不能做（沒 staging smoke step）

### Plan 外但已做（額外加值，不算 9-phase 進度）

- Phase 1.5 SEO audit + enrich（PR #173/#183/#185/#191/#192）
- Ingest v2 walker + Vision（PR #169/#178/#186/#188/#189）

---

## Open follow-up（保留，不在下一輪 4 步範圍）

### PR #187 留下（不阻塞）

- **A5**（drill outcome）：現役 VPS `apt install sqlite3` — 修修手動 ssh
- **A6**（drill outcome）：`verify_db()` table count -1（沒算 sqlite_sequence）— low pri
- **incident archive #3**：`list_pending_incidents` mtime → 改讀 frontmatter `detected_at`
- **incident archive #4**：`_archive_alert` 用 dispatch `now` → 改 `alert.fired_at`（一行）
- **Mac vault sync hook**：repo `data/incidents-pending/` → vault `Incidents/YYYY/MM/`
- **Bridge UI `/bridge/incidents`**：候選看頻率決定

### 5B-3 anomaly daemon

- **1-2 週後寫 `feedback_anomaly_3sigma_pattern.md`**：誤報多寡 / 真實抓到 issue 機率

### PR #187 service restart 決策（修修選）

- nakama-usopp alert path 改了 `_archive` hook，systemd service 沒重啟 = sticky import；下次 publish 失敗 alert 不走 archive。修修決定立刻 restart 還是等下次 deploy 順手帶。

---

## 不要自己決定的事

- **Phase 7 staging** — 規模大、要錢、要新 VPS，必先問
- **PR #190 squash merge** — 修修明確說「下一輪繼續做你建議的順序」，本 session 不 merge
- **ultrareview 連續 free quota 滿後** 是否 abandon → 走 self-review only — 修修決定（PR #187 / PR #190 都走 self-review pattern OK）

---

## 開始之前一定要先看

- 本 memo
- 原 9-phase plan：`docs/plans/quality-bar-uplift-2026-04-25.md`
- Phase 6 task prompt：`docs/task-prompts/2026-04-26-phase-6-test-coverage.md`（採 Q1-Q6 全 A）
- Phase 6 decisions：`docs/plans/2026-04-26-phase-6-test-coverage-decisions.md`
- [feedback_measure_before_freeze.md](feedback_measure_before_freeze.md) — 凍結前先量 baseline
- [feedback_no_regression_gate.md](feedback_no_regression_gate.md) — gate threshold 哲學
- [feedback_aesthetic_first_class.md](feedback_aesthetic_first_class.md) — 美學要求（適用 `/bridge/docs` UI）
