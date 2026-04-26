---
name: Quality Uplift 下一輪起點（2026-04-27+ 接手 — Slice 1 PR #190 開了，等修修拍板 threshold）
description: Phase 6 task prompt 凍結 + Slice 1 tooling PR #190 開了 + baseline 整體 81% / 8 模組全綠；等修修醒來 review + merge → Slice 2
type: project
created: 2026-04-26
updated: 2026-04-26
originSessionId: 2026-04-26-night
---
2026-04-26 深夜：修修「放手讓你執行、我先去睡了」+ auto mode → 我凍結 Phase 6 task prompt + 開 Slice 1 PR #190。**取代 `project_quality_uplift_next_2026_04_27.md` 跟更早所有同名 memo**。

**Why:** Phase 6 task prompt 採 decisions doc 全 A 拍板（self-决，修修授權範圍內）；Slice 1 是其他 3 slice 的 dep（pytest-cov + critical-path gate）必先做；baseline 量出來 8/8 ✅ 大幅好過 plan 估計，threshold 走「不退步 gate」哲學鎖 baseline。

**How to apply:** 開新對話讀完 `MEMORY.md` → 讀本 memo → 預設下一步 = **修修 review PR #190 → 拍板 threshold 是否合理 → squash merge → 我開 Slice 2 PR（FSM property test）**。

## 本 session 進度（2026-04-26 night）

### 已完成

| 項目 | 結果 |
|---|---|
| Decisions doc | `docs/plans/2026-04-26-phase-6-test-coverage-decisions.md`（6 題 + 建議答案）— commit edf94c8 |
| Task prompt 凍結 | `docs/task-prompts/2026-04-26-phase-6-test-coverage.md`（採 Q1-Q6 全 A）— commit edf94c8 |
| Slice 1 PR | **#190 `feature/phase-6-slice-1-coverage-tooling`** — 待 review |

### Slice 1 PR #190 內容

- pytest-cov dev dep + `[tool.coverage.*]` config
- `scripts/check_critical_path_coverage.py`（不退步 gate, 8 模組 threshold dict）
- `.github/workflows/ci.yml` 加 cov gate step
- 補 `/api/agents` 兩條 test 把 `bridge.py` 77.56% → 86.67%
- `docs/runbooks/test-coverage.md` runbook
- `.gitignore` 加 `coverage.json`
- 6 files / +335 lines

### Baseline 2026-04-26（揭露 plan §Phase 6 三點對不上）

整體 **81.0%**（11857 stmts / 2240 missed）— **遠超 plan A bar 整體 ≥ 50%**。

| 模組 | Threshold | After |
|---|---|---|
| `shared/approval_queue.py` | 95% | 96.77% ✓ |
| `shared/alerts.py` | 100% | 100.00% ✓ |
| `shared/incident_archive.py` | 90% | 93.08% ✓ |
| `shared/heartbeat.py` | 100% | 100.00% ✓ |
| `shared/kb_writer.py` | 90% | 91.15% ✓ |
| `shared/wordpress_client.py` | 90% | 90.34% ✓ |
| `thousand_sunny/routers/robin.py` | 95% | 96.56% ✓ |
| `thousand_sunny/routers/bridge.py` | 80% | 86.67% ✓（+9.11% 從 baseline） |

**Plan §Phase 6 vs 現狀三點**（已寫進 PR description + decisions doc）：

1. plan 寫「目前 <60%」實際 **81%** — 大幅好過估計，baseline gate 收斂在「不退步」而非「補洞」
2. **alert_state 不是 FSM**（只有 dedupe），Slice 2 走 deterministic dedupe 而非 property-based stateful
3. **SSE 在 `thousand_sunny/routers/robin.py:672`**（既有 1328 行 router test），plan 寫的 path 對

### Tests + lint
- 2363 passed, 2 skipped（baseline 2361 → +2）
- ruff check + format clean
- 本地 `python scripts/check_critical_path_coverage.py` → 8/8 ✓
- CI 在跑（GH Actions runs/24959818297）— 我沒等綠就推；修修明早看

## 下一步（修修醒來）

1. **review PR #190**：看 baseline 數字 + threshold 設定是否同意（80/90/95/100）
2. **CI green** + 修修一句話 → 我 squash merge + 開 Slice 2
3. 或修修想調 threshold（例：要求 bridge.py 90% 而非 80%）→ 同 PR 補 test 達標

## 9-phase plan 對照（不變，等 Slice 1 merge 後再更新）

| # | Phase | 狀態 |
|---|---|:---:|
| 1-5, 9 | 全 merged | ✅ |
| **6** | **Slice 1 PR #190 待 review；Slice 2-4 task prompt 已凍結** | 🟡 |
| 7 | Staging — 要錢/新 VPS，必先問 | ❌ |
| 8 | CI/CD auto deploy — blocked by 7 | ❌ |

整體：7/9 ✅ + 1/9 🟡（Slice 1 in flight）+ 2/9 ❌。

## PR #187 merged + VPS deployed

| 項目 | 結果 |
|---|---|
| PR #187 | merged `224dd91` 2026-04-26 14:10 UTC |
| Branch | `feat/phase-1-4-grey-fix` 已 deleted |
| Commits（squashed 自 3 個原本 commit） | docs(runbooks) drill 回灌 + feat(incidents) archive + fix(incidents) YAML safety |
| Tests | 2242 → 2281（+39 包含 self-review 加的 yaml.safe_load + title-newline test） |
| ruff | 全綠 |
| VPS pull | HEAD 224dd91，3 services（thousand-sunny/gateway/usopp）all active |
| VPS smoke | `archive_incident()` 寫 `/home/nakama/data/incidents-pending/` 通了；trigger 已 quote、Taipei tz、slug tag 切對 |

## Phase 1 + 4 grey → ✅ 證據

### Phase 1 — DR drill 實證

- ✅ 首次半量 drill 跑完（21:24:55 → 21:25:18 ≈ 23 sec）
- ✅ vault `Incidents/2026/04/drill-2026-04-26-state-restore.md` 寫了（postmortem schema 對齊，severity 用非標準 tier `drill`）
- ✅ runbook 4 findings 回灌：§1 RTO caveat / §3 sqlite3 → python / §6.1 default 改 VPS sandbox + Mac alt + `--apply` flag + sqlite3 → python

### Phase 4 — incident postmortem 自動化

- ✅ `shared/incident_archive.py` archive_incident() + list_pending_incidents() + 22 tests
- ✅ shared.alerts.alert error path / franky.alert_router AlertV1 critical path 兩條 hook
- ✅ Franky weekly_digest §6 incidents（過去 30d，SEV tier breakdown + open count + top 3 recurring）
- ✅ tests/conftest.py autouse `_isolated_incidents_pending` fixture
- ✅ YAML safety：trigger quoted scalar / title 換行 collapse / category_tag 用 slug prefix（self-review 抓到的 3 個 footgun）

## 9-phase plan 對照（更新到 2026-04-26 22:10）

| # | Phase | 狀態 | 證據 |
|---|---|:---:|---|
| 1 | DR drill + secret rotation | ✅ | runbook 三段回灌 + vault drill outcome 文件 + RTO 校準 |
| 2 | Backup A 升級 | ✅ | PR #147 (2A) + #154 (2B) |
| 3 | Observability foundation | ✅ | PR #152 |
| 4 | Incident postmortem | ✅ | PR #166 process + template + **PR #187 archive 自動化** |
| 5 | Observability advanced | ✅（拆 6 sub-PR） | 5A #168 · 5B-1 #170 · 5B-2 #175 · 5B-3 #184 · 5C #182 · 5D #177 |
| 6 | Test coverage | ❌ 未開始 | task prompt 待凍結，plan §Phase 6 有 4 個 deliverable |
| 7 | Staging + feature flags | ❌ 未開始（要錢/新 VPS，必先問） | — |
| 8 | CI/CD auto deploy | ❌ blocked by 7 | — |
| 9 | 版控 polish + Doc A+ | ✅ | PR #157 |

**整體：7/9 ✅ + 0/9 🟡 + 3/9 ❌**。

## 下一個 chunk — Phase 6 test coverage 補齊

**Plan source**：`docs/plans/quality-bar-uplift-2026-04-25.md` §Phase 6
**Task prompt 狀態**：未凍結（待寫）

Plan §Phase 6 寫的 4 個 deliverable：
1. **thousand_sunny SSE coverage**（Bridge realtime endpoints 沒測）
2. **Agent E2E golden path**（Robin/Brook/Zoro 三 agent 各一個 happy path E2E test）
3. **FSM property test**（approval_queue / alert_state state machine property-based test，hypothesis）
4. **Schema round-trip test**（Pydantic V1 model JSON serialize + parse 回來等價性）

預計 chunk 大小：1-1.5 天。Pre-chunk 要先：
- 讀 plan §Phase 6 細節
- 跟 修修 對齊 Q（hypothesis 是否要加 dep？SSE test client 用什麼？coverage threshold？）
- 凍結 task prompt 到 `docs/task-prompts/2026-04-XX-phase-6-test-coverage.md`

## Open follow-up（不在下個 chunk 範圍）

### PR #187 自身留下

- **A5（drill outcome）**：現役 VPS `apt install sqlite3`（runbook §B-3 已含，新建 path 已涵蓋；現役機未裝）— 修修手動 ssh 補裝
- **A6（drill outcome）**：`verify_db()` table count -1（沒算 sqlite_sequence）— low pri，下次 verify_db sweep 對齊
- **incident archive #3**：`list_pending_incidents` 用 mtime 過濾，跨 30d 邊界 re-fired 檔會誤入。未來改讀 frontmatter `detected_at`
- **incident archive #4**：`_archive_alert` 用 dispatch `now` 而非 `alert.fired_at` — 罕見 queue lag 跨午夜會錯日。一行修
- **Mac vault sync hook**：把 repo `data/incidents-pending/` move 進 vault `Incidents/YYYY/MM/`（Q2 答案，sync 機制本身沒做；目前 VPS pending dir 累積，digest §6 看得到）
- **Bridge UI `/bridge/incidents`**：Phase 4+1 候選，看頻率決定要不要做

### 5B-3 anomaly daemon

- **1-2 週後寫 `feedback_anomaly_3sigma_pattern.md`**：誤報多寡 / 真實抓到 issue 機率（task prompt §10）。現在沒實證資料

## Service restart（VPS）— 修修決定

PR #187 alert path 改了 `_archive` hook。但 deploy 只 `git pull` 沒 restart 三個 systemd services。影響：
- **Cron jobs**（health_check / backup / r2_backup_verify / weekly_digest 等）：每次 fork 新 process import 新 code，**自動生效** ✓
- **Systemd services**（thousand-sunny / nakama-gateway / nakama-usopp）：sticky import，**不重啟不會載新 _archive hook**

實務上：
- Web/gateway 不發 error alerts → 不重啟也沒影響
- nakama-usopp 會發 publish 失敗 alert → 下次 publish 失敗 alert 不會走 archive path（Slack DM 仍正常），等 service 自然 restart 才接上

**修修決定**：要立刻 ssh restart nakama-usopp（30s daemon 中斷可接受），還是等下次 deploy 順手帶上。

## 別軸線（不在本 chunk 範圍）

| 工作 | 狀態 |
|---|---|
| **D.2 SEO audit** | PR #183 開了（別 session） |
| **F SEO firecrawl** | PR #185 merged（已上線） |
| **ingest v2 PR C** | merged d7ed413（Wiley ch1 v2 re-ingest + 11 concept actions） |

## 不要自己決定的事

- Phase 7 staging — 規模大、要錢、要新 VPS，必先問
- ultrareview 連續 free quota 滿後是否要 abandon → 走 self-review only — 修修決定（本次 ultrareview 額度耗盡，PR #187 走 self-review 抓到 3 個 blocker 並修，pattern OK）
- Service restart 決策（見上節）

## 開始之前一定要先看

- 本 memo
- [feedback_aesthetic_first_class.md](feedback_aesthetic_first_class.md) — 美學要求（不適用於本 chunk 但常被 ignore）
- 原 9-phase plan：`docs/plans/quality-bar-uplift-2026-04-25.md`（特別 §Phase 6）
- vault drill outcome：`Incidents/2026/04/drill-2026-04-26-state-restore.md`（drill 真實 wall-clock + findings）
