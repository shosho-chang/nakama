# Quality Bar Uplift Plan — 拉滿 A

**Date:** 2026-04-25
**Driver:** 修修
**Goal:** 把現況評估中 < A 的 6 個維度都拉到 A 以上，且不犧牲已 A+ 的維度（架構紀律 / 教訓沉澱）。

## 背景

2026-04-25 對話對 nakama 開發流程做了維度盤點，得到：

- **A+**：架構紀律、教訓沉澱、Documentation
- **A**：Code review、Schema/FSM/契約
- **A−**：版本控管紀律、CI/CD
- **B+**：Test coverage、Backup
- **C+**：Incident postmortem
- **C**：Observability
- **C−**：Staging
- **D+**：Disaster recovery

本計劃定義每個維度的 A bar、列出 deliverable + effort、提出 phase 排序。

## A bar 定義

每維度的「A」標準：

| 維度 | A bar |
|------|------|
| Disaster recovery | DR runbook 寫過 + restore drill 做過 ≥ 1 次 + RPO < 24h / RTO < 4h documented + secret rotation 流程 + multi-target deploy capability |
| Backup | daily + weekly + monthly retention + bucket-scoped token（讀寫分離）+ 整合性檢查每週 + secondary off-site target |
| Observability | 結構化 JSON log + per-cron heartbeat + application error 專屬 channel + per-agent latency/cost dashboard + cost/error anomaly detection |
| Incident postmortem | `Incidents/` 制度化結構 + 7 天內 postmortem + Franky 月度 incident review + action item tracking |
| Test coverage | 整體 ≥ 50% + production critical path 模組 ≥ 80% + 每 agent E2E golden path + FSM property-based test |
| Staging | full-stack docker-compose 在桌機可跑 + staging deploy on PR merge + smoke test pipeline + feature flag infra |
| 版本控管 | branch protection（CI green + PR）+ conventional commits lint + semver for shared/* + auto changelog + deploy commit tagging |
| CI/CD | 自動 deploy on main merge + pre-deploy smoke test + rollback automation + deploy notification |
| Documentation A → A+ | memory pruning（過期 project memory）+ doc index search + ADR 完整 cross-link |

## Phase 排序（按 leverage / dependency）

| # | Phase | 維度 | Effort | Status (2026-04-27) |
|---|-------|------|--------|---|
| 1 | DR drill + secret rotation | DR D+→A | 5 天 | ✅ #146 + #187 |
| 2 | Backup A 升級（multi-tier + integrity） | Backup B+→A | 3 天 | ✅ #147 + #154 |
| 3 | Observability foundation | Obs C→B+ | 5 天 | ✅ #152 |
| 4 | Incident postmortem 制度化 | Postmortem C+→A | 3 天 | ✅ #166 + #187 |
| 5 | Observability advanced | Obs B+→A | 7 天 | ✅ 6 sub-PR |
| 6 | Test coverage 補齊 | Test B+→A | 7 天 | ✅ #190/#194/#195/#196 |
| 7 | Staging + feature flags | Staging C−→A | 8 天 | ❎ **Deferred 2026-04-27** |
| 8 | CI/CD auto deploy | CI/CD A−→A | 4 天 | ❎ **Deferred 2026-04-27** |
| 9 | 版本控管 polish + Doc A+ | 版控 / Doc | 3 天 | 🟡 #157 + ops follow-up |

**狀態：6/9 ✅ + 1/9 🟡 + 2/9 ❎ Deferred**

### Phase 7 + 8 deferred 決策（2026-04-27）

**修修拍板 deferred** — 對 solo dev / pre-revenue / 個人 tooling 階段是 over-engineering。

不做的 reasoning：
- 已有 quality net 涵蓋 staging 95% value：CI 2400+ test、critical-path coverage gate、Franky 5min probe、live_wp Docker E2E、git revert ~5 min rollback
- staging setup 5 工作天 + 1-2h/週 維護 → 投入 Chopper community / 內容生產 ROI 高 50-100x
- nakama 沒有 multi-team 協作 / SLA 客戶 / 千萬用戶 — 沒有 staging design intent 對應的場景

**Trigger 升回 active**（同時滿足才考慮）：
1. 自由艦隊月活 100+ 付費會員
2. Production touchpoint > 100/天
3. 出現雲端 / 現有 quality net 解不了的具體 bottleneck

**改做的事**（lightweight production safety net）：
- Manual deploy checklist + rollback runbook（半天工作量、Phase 9 收完後可做）

詳見 [memory/claude/project_quality_uplift_next_2026_04_28.md](../../memory/claude/project_quality_uplift_next_2026_04_28.md) 跟 [memory/claude/feedback_avoid_one_shot_summit.md](../../memory/claude/feedback_avoid_one_shot_summit.md)。

## Deliverable 清單

### Phase 1 — DR drill + secret rotation（5 天）

- `docs/runbooks/disaster-recovery.md`（RPO/RTO + restore steps + smoke check）
- `scripts/restore_from_r2.py`（互動式 restore tool，dry-run 模式）
- DR drill GH issue + outcome 文件（at minimum: 假設 VPS 整台壞，從 R2 起一台新的，記錄真實 wall-clock）
- `docs/runbooks/secret-rotation.md`（每個 secret 的 rotation 流程 + 提醒 cadence）

### Phase 2 — Backup A 升級（3 天）

- `scripts/backup_nakama_state.py` 加 weekly + monthly tier
- R2 bucket policy 拆分：`nakama-backup-write` token（write-only）+ `nakama-backup-read` token（read-only restore 用）
- `scripts/verify_backup_integrity.py`（每週跑：spot check N row + SHA 驗證）
- 第二份 backup target（GCS / B2 / 同 R2 不同 region）

### Phase 3 — Observability foundation（5 天）

- `shared/structured_log.py`（JSON line + rotation + Bridge UI grep page）
- `shared/heartbeat.py`（每 cron job 寫 `data/heartbeats/{job}.last`）
- `nakama-alerts` Slack channel + DM 路由 logic（區分 error / warn / info）
- `/bridge/health` 頁草版（agent × last-success × error count last 24h）

### Phase 4 — Incident postmortem 制度化（3 天）

- vault `Incidents/` schema + template（timeline / detect / mitigate / root cause / action items）
- Franky 月報加 incident roundup（last 30d 統計 + open action items）
- `docs/runbooks/postmortem-process.md`（什麼算 incident、誰負責、deadline）
- alert → Bridge incident timeline 自動歸檔

### Phase 5 — Observability advanced（7 天）

- Per-agent / per-LLM-call latency p50/p95/p99（蒐集進 SQLite + Bridge cost dashboard 擴展）
- Anomaly daemon（每 15 min 跑：cost / error rate / latency std-dev > 3σ → alert）
- 結構化 log search UI（基於 SQLite + FTS5）
- WP / GSC / Slack / Gmail / R2 等外部 dependency 健康指標

### Phase 6 — Test coverage 補齊（7 天）

- thousand_sunny SSE / robin router 補到 80%（目前 <60%）
- 每 agent E2E golden path（接 mock LLM + mock vault → 走完一條真實流程）
- approval_queue FSM property-based test（hypothesis）
- Critical-path schema round-trip test

### Phase 7 — Staging + feature flags（8 天）

- `infra/staging/docker-compose.yml`（thousand_sunny + nakama-gateway + sqlite + 模擬 vault dir）
- `scripts/deploy_staging.sh`（桌機本地 boot 全 stack）
- `tests/smoke/`（每 agent 一條 golden path，可 boot staging 後自動跑）
- `shared/feature_flags.py`（yaml-driven，DB-backed，Bridge UI 切換）

### Phase 8 — CI/CD auto deploy（4 天）

- `.github/workflows/deploy.yml`（main merge → staging smoke → VPS pull + restart）
- `scripts/rollback.sh`（git revert HEAD + service restart + smoke verify）
- Deploy Slack notification（commit / 改動 service / smoke 結果）
- Deploy commit tagging（`vps-deploy-{sha}-{timestamp}`）

### Phase 9 — 版本控管 polish + Doc A+（3 天）

- GH branch protection rule（require CI green，single-person 設 1 review approval = self-review token 或 admin override 紀錄）
- `.github/workflows/conventional-commits-lint.yml`
- `scripts/release.py`（semver + changelog 自動產生）
- Memory pruning（每月過期 project memory；feedback memory 永久留）
- Doc index search（`/bridge/docs` 簡單 FTS5 搜尋頁）

## Trade-offs / 風險

**值不值得做**：對「自用工具、無付費客戶、單人開發」的當下，這計劃整個做完是 over-investment（多數 hobby 專案到 B− 就停）。但下列任一條件成立，做完整 plan 就值得：

1. nakama 走向開源（feedback memory + ADR 系統會被社群直接用）
2. 上線 paying customer（即使只是少數 alpha tester，observability + DR 立刻成 must-have）
3. 引入第二位 contributor（CI/CD + staging 是合作 baseline）
4. 修修個人「練 quality muscle」價值高（已知這條成立）

**最大風險**：

- **Over-doc 加劇**：本計劃會再增 ~10 ADR/runbook + ~3-5k LOC。需要配套 doc pruning 制度（Phase 9 包含），否則 22k markdown 變 35k。
- **Single-person SPOF 沒解**：所有自動化都依賴修修腦袋。Phase 9 branch protection 對 1 人 team 是儀式。真要解 SPOF 要靠 onboarding doc 完整度（不在本計劃內）。
- **Phase 5 / 7 effort 估算最不確定**：observability dashboard 容易 scope creep，feature flag infra 設計選擇多。建議到時再凍結 design。

**最有把握快贏**：Phase 1（DR drill）。一個下午就能演練「VPS 整台炸 → 新機從 R2 起來 → smoke 通過」，立刻把 D+ 拉到 B+，runbook 的副產品永久留。

## 推薦執行順序

1. **本週**：Phase 1 + Phase 2 配對（8 天）— 最大快贏，DR drill 強迫驗 backup
2. **下週**：Phase 3 + Phase 4 配對（8 天）— observability foundation 同時鋪 incident 制度
3. **第 3-4 週**：Phase 5（advanced obs）+ Phase 6（test coverage 補齊）
4. **第 5-6 週**：Phase 7（staging）+ Phase 8（CI/CD auto deploy）
5. **第 7 週**：Phase 9（polish + doc A+）

執行時每 phase 開獨立 PR，沿用 ultrareview + 本地 3-agent review 既有 flow。
