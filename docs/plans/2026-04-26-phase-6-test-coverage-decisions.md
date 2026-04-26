---
name: Phase 6 Test Coverage — Decision Questionnaire
description: 凍結 Phase 6 task prompt 前的 6 個拍板題（coverage 工具 / hypothesis dep / alert_state 處理 / E2E 範圍 / mock 策略 / slice 切法）
type: project
created: 2026-04-26
---

# Phase 6 Test Coverage — Decision Questionnaire

**用法**：每題勾選一個選項（用 `[x]` 取代 `[ ]`），nuance 寫 Comments / overrides 區。

**對應提案**：[docs/plans/quality-bar-uplift-2026-04-25.md §Phase 6](quality-bar-uplift-2026-04-25.md)
**背景記憶**：
- [project_quality_uplift_next_2026_04_28.md](../../memory/claude/project_quality_uplift_next_2026_04_28.md)
- [project_quality_uplift_review_2026_04_25.md](../../memory/claude/project_quality_uplift_review_2026_04_25.md)
- [feedback_test_realism.md](../../memory/claude/feedback_test_realism.md)
- [feedback_mock_use_spec.md](../../memory/claude/feedback_mock_use_spec.md)
- [feedback_test_api_isolation.md](../../memory/claude/feedback_test_api_isolation.md)

---

## ⚠️ 跟 plan §Phase 6 文字對不上的兩個點（先說，避免照字面凍 task prompt 出錯）

1. **「alert_state state machine」並不存在 FSM**。`alert_state` 是 SQLite table（`shared/state.py:212`），目前只支援 dedupe（`dedupe_key` + `suppress_until` + `fire_count`）— 沒有 transition 規則、沒有 status enum，property-based stateful test 沒著力點。真正的 FSM 在 `shared/approval_queue.py` 的 `ALLOWED_TRANSITIONS`（7 status × N transitions）+ `transition()` 函式。**Q3 處理**。
2. **「thousand_sunny SSE / robin router」現況**：SSE 在 `thousand_sunny/routers/robin.py:672`（`StreamingResponse` + `text/event-stream`），既有 `tests/test_robin_router_sse.py`（508 行）+ `tests/test_robin_router.py`（820 行），總 1328 行；plan 寫「<60%」是估的、沒實量。**Q1 順帶決定 baseline 量法**。

---

## Q1 — Coverage 工具裝不裝？threshold 怎麼定？

**Tradeoff**：

- **A 裝 pytest-cov + per-module 80% gate（critical-path 模組）**：對齊 plan A bar 寫的「production critical path 模組 ≥ 80%」承諾；CI 會擋；critical-path 清單先收斂在 §補充清單（approval_queue / alerts / incident_archive / heartbeat / kb_writer / wordpress_client / 7 router）；其餘模組只報告不擋。
- **B 裝 pytest-cov，全程只報告（不擋 CI）**：先看數據，避免一上 CI gate 就紅；缺點是「A bar」只是口頭承諾沒落地。
- **C 不裝**：靠 plan 寫的 deliverable 推進 + 人工 review；最簡單但無客觀數據。

**我的建議**：A — Phase 9 已 merge `quality-bar-uplift` 系列，不該停在「無 coverage 數據」。CI gate 收緊在 critical-path 8 個模組，整體不擋。

**選一個**：

- [ ] **A — 裝 + critical-path gate 80% + 全 repo 報告**（建議）
- [ ] **B — 裝但只報告**
- [ ] **C — 不裝**

**Comments / overrides**：

> _（修修自由補充）_

---

## Q2 — Hypothesis 加 dep 嗎？

**Tradeoff**：

- **A 加 hypothesis（`hypothesis>=6.100`），approval_queue FSM 用 `RuleBasedStateMachine`**：plan 明文寫「FSM property-based test」；hypothesis 的 stateful test 抓到的 invariant violation 比窮舉強；新 dep 但 well-maintained、測試用、不上 prod path。
- **B 不加，approval_queue 寫窮舉 transition table test**：21 個 status pair × valid/invalid → ~42 case parametrize 寫得完；少 dep；但抓不到「N 步 random walk 後 state 仍 invariant」這類 bug。
- **C 加 hypothesis 但只用一兩個簡單 `@given`**（不上 stateful test）：折衷，但失去 hypothesis 對 FSM 最大價值。

**我的建議**：A — approval_queue 是 publish pipeline 心臟，state.db CHECK constraint 跟 ALLOWED_TRANSITIONS 兩邊手動同步（已寫 `assert` lock），property test 是少數能抓到「兩邊 drift」的方法。

**選一個**：

- [ ] **A — 加 hypothesis + RuleBasedStateMachine for approval_queue**（建議）
- [ ] **B — 不加，窮舉 parametrize**
- [ ] **C — 加但只用 @given simple**

**Comments / overrides**：

> _（修修自由補充）_

---

## Q3 — `alert_state` 怎麼處理？

背景：plan 寫「approval_queue / alert_state state machine property-based test」，但 alert_state 沒 FSM（見上文 ⚠️）。

**Tradeoff**：

- **A alert_state 改寫成 dedupe edge-case test（非 property test）**：window boundary（fire 在 `suppress_until` 前 1 秒 vs 後 1 秒）/ multi-key 不互相影響 / fire_count 累加正確 / TTL expiry — 5-8 個 deterministic case；對齊 alert_state 的真實設計。
- **B 把 alert_state 抽成真正的 FSM（status enum: suppressed / active / expired）再 property test**：scope creep；alerts.py 全要改；偏離原設計。
- **C alert_state 不測**（property-based 範圍只 approval_queue）：plan 兌現程度降；省 0.5 天。

**我的建議**：A — 對 plan 兌現一半（property-based 部分到 approval_queue 為止），dedupe 用 deterministic test 覆蓋 alert_state；不傷現狀、scope 收得住。

**選一個**：

- [ ] **A — alert_state 走 deterministic dedupe test**（建議）
- [ ] **B — alert_state 重構成真 FSM 再測**
- [ ] **C — alert_state 不測**

**Comments / overrides**：

> _（修修自由補充）_

---

## Q4 — 「每 agent E2E golden path」覆蓋哪幾個？

背景：repo 共 7 個 active agent — robin / brook / zoro / usopp / franky / nami / sanji（chopper 還沒開發）。`project_quality_uplift_next_2026_04_28.md` 寫「Robin/Brook/Zoro 三 agent」；plan 寫「每 agent」。

**Tradeoff**：

- **A 三 agent — Robin / Brook / Zoro**（content pipeline 上游 → 下游）：scope 最緊；其他 agent 留 follow-up；對齊 memo 寫的範圍；7 天可完成。
- **B 五 agent — 三 agent + Usopp + Franky**：再加 publish 端（Usopp）+ ops 端（Franky）；publish E2E 已在 `tests/e2e/test_phase1_publish_flow.py`（live_wp marker），可能只要 wire mock LLM 版；7-9 天。
- **C 七 agent 全覆蓋**：包括 Nami（Slack triage）+ Sanji（recipe agent）；plan 字面兌現；但 Nami / Sanji 的 happy path 定義沒先凍結；可能 10+ 天。

**我的建議**：A — 收緊 scope，content pipeline 三 agent 涵蓋最 critical revenue path；Usopp 已有 `live_wp` E2E 不算遺漏；其他 follow-up 走獨立 chunk。

**選一個**：

- [ ] **A — Robin / Brook / Zoro（建議）**
- [ ] **B — 加 Usopp / Franky**
- [ ] **C — 全 7 agent**

**Comments / overrides**：

> _（修修自由補充）_

---

## Q5 — E2E test 的 mock 策略？

**Tradeoff**：

- **A Mock LLM via responses（httpx mock）+ vault via tmp_path + Slack via spec mock**：CI 能跑、零成本、deterministic；conftest autouse fixture 已有先例（`feedback_test_api_isolation.md`）；缺點是「mock 真實性」要看（`feedback_test_realism.md` 教訓）。
- **B Live LLM + tmp vault**：每次跑燒 token（粗估三 agent E2E ~$0.3-0.5/run）；CI 不能跑只能本地；但測到真實 LLM behaviour。
- **C VCR cassette（一次錄、之後 replay）**：deterministic + 接近真實；cassette 過期要 re-record；對 schema-drifting LLM API（Claude tool_use）維護成本不低。

**我的建議**：A — 對齊既有 pattern（`tests/conftest.py` 已 autouse mock 對外 API），CI 跑得動。E2E test 重點是「pipeline 各 stage 串起來不漏」，不是 LLM 行為驗證；後者另寫 live test 帶 `@pytest.mark.real_llm` marker（需新增）。

**選一個**：

- [ ] **A — Mock 全（httpx + tmp_path + spec）**（建議）
- [ ] **B — Live LLM + tmp vault**
- [ ] **C — VCR cassette**

**Comments / overrides**：

> _（修修自由補充）_

---

## Q6 — Slice 怎麼切？

背景：4 個 deliverable × 7 天，太大不切會踩 `feedback_dual_review_complementarity.md` 的「review 規模 limit」+ stacked PR conflict 風險。

**Tradeoff**：

- **A 4 slice / 4 PR**（一 deliverable 一 PR）：① 工具 + critical-path coverage（pytest-cov + 8 模組補到 80%）→ ② FSM property test（approval_queue + alert_state dedupe）→ ③ Schema round-trip → ④ Agent E2E（三 agent）。每 PR 1.5-2 天、可獨立 review；① 是其他三個的 dep；可 stacked。
- **B 2 slice**：（① + ② 工具 + property） / （③ + ④ schema + E2E）；review 比較重但少 PR overhead。
- **C 1 個大 PR**：對齊既有 ingest v2 PR C 模式（big bang）；review 痛苦；revert 不細緻。

**我的建議**：A — Phase 5 拆 6 sub-PR 已實證乾淨；Phase 6 同樣切。每 slice 走 `feedback_pr_review_merge_flow.md`（自動 review → 修修 squash merge → 下個 slice rebase）。

**選一個**：

- [ ] **A — 4 slice / 4 PR**（建議）
- [ ] **B — 2 slice / 2 PR**
- [ ] **C — 1 個大 PR**

**Comments / overrides**：

> _（修修自由補充）_

---

## 補充清單（拍板後寫進 task prompt）

### Critical-path 模組（Q1=A 時 80% gate 收斂在這 8 個）

| 模組 | 角色 | 現有 test 行數 |
|---|---|---|
| `shared/approval_queue.py` | publish FSM | 大量（PR #72 / #77 / #97 / #101 等） |
| `shared/alerts.py` | alert dedupe + dispatch | 中（PR #152） |
| `shared/incident_archive.py` | postmortem 自動歸檔 | 22 tests（PR #187） |
| `shared/heartbeat.py` | per-cron 心跳 | 中（PR #152） |
| `shared/kb_writer.py` | KB 結構寫入（aggregator） | 大量（PR #169 / #178 / d7ed413） |
| `shared/wordpress_client.py` | WP REST + media | 大量（PR #73） |
| `thousand_sunny/routers/robin.py` | Robin SSE + 處理 | 1328 行 |
| `thousand_sunny/routers/bridge.py` | Bridge UI mutation | 中（PR #140） |

### Critical-path schema（Q1 + Q6③ 用，round-trip test 範圍）

`shared/schemas/`：30+ V1 model — round-trip test 不全做，收斂在 publish path 10 個：

- `publishing.py`：DraftV1 / DraftComplianceV1 / GutenbergHTMLV1 / PublishRequestV1 / PublishResultV1 / PublishComplianceGateV1 / SEOContextV1
- `approval.py`：PublishWpPostV1 / UpdateWpPostV1
- `external/wordpress.py`：WpPostV1（驗 anti-corruption layer）

其餘 schema（franky / kb / external/seopress / site_mapping）若 Q1=A 模組 coverage 帶到就夠，不另寫 round-trip。

### Coverage baseline 量法（Q1=A 或 B）

chunk 開頭跑：

```bash
pytest --cov=shared --cov=thousand_sunny --cov=agents --cov-report=term-missing --cov-report=html
```

寫進 task prompt §3 Inputs 的 baseline，每個 slice PR description 附 before/after diff。

---

## 補充自由提問區

> _（修修自由 input — 想到我沒列到的題）_

---

## 拍板後我做什麼

1. 把本 questionnaire 答案寫進 task prompt 的 §1-6 P9 六要素
2. 凍結 task prompt 到 `docs/task-prompts/2026-04-XX-phase-6-test-coverage.md`（檔名日期填拍板當天）
3. 更新 `memory/claude/project_quality_uplift_next_2026_04_28.md` 的「下一個 chunk」段，把 Q1-Q6 答案 + critical-path 清單 link 過去
4. 視 Q6 答案準備 slice 1 的 feature branch + 開工
