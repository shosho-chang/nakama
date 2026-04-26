# Task Prompt — Phase 6 Test Coverage 補齊

**Framework:** P9 六要素（CLAUDE.md §工作方法論）
**Status:** 凍結（Q1-Q6 採 default A 拍板，2026-04-26）
**Plan section:** [docs/plans/quality-bar-uplift-2026-04-25.md §Phase 6](../plans/quality-bar-uplift-2026-04-25.md)
**Decisions:** [docs/plans/2026-04-26-phase-6-test-coverage-decisions.md](../plans/2026-04-26-phase-6-test-coverage-decisions.md)（Q1-Q6 採 A）
**Upstream dependency:** Phase 1-5 + 9 全 merged（PR #146/#147/#152/#154/#157 + Phase 5 六 sub-PR + #187）
**ADR：** 無（純 test 補齊，不需新 ADR）

---

## 1. 目標

把 nakama 從 Test coverage **B+ → A**：
裝 pytest-cov 量化 baseline、critical-path 8 個模組補到 ≥ 80% 並 CI gate 擋；approval_queue FSM 用 hypothesis property-based stateful test 鎖住 invariant；alert_state dedupe 補 deterministic edge case；10 個 critical-path V1 schema 補 round-trip test；Robin / Brook / Zoro 三 agent 各補一條 happy-path E2E。

達成後 plan A bar 兌現（除「整體 ≥ 50%」之外其他 5 點全到位），plan 7/9 → 8/9 ✅。

## 2. 範圍

切 4 slice / 4 PR（stacked，Slice 1 是其他三的 dep）：

### Slice 1 — pytest-cov 工具 + critical-path 80% gate（~2 天）

| 路徑 | 動作 | 內容 |
|---|---|---|
| `pyproject.toml` | 改 | 加 `[tool.coverage.run]` `source = ["shared", "thousand_sunny", "agents"]`；`[tool.coverage.report]` `exclude_lines`；`[tool.pytest.ini_options]` 不預設加 `--cov`（避免本地 dev 變慢）|
| `requirements.txt` | 改 | 加 `pytest-cov>=5.0`（同步 pyproject `[project.optional-dependencies].dev`，`feedback_dep_manifest_sync.md`）|
| `.github/workflows/ci.yml` | 改 | CI 跑 `pytest --cov --cov-fail-under=0`（整體不擋）+ 第二步 `python scripts/check_critical_path_coverage.py` 對 8 模組逐一驗 ≥ 80% |
| `scripts/check_critical_path_coverage.py` | 新增 | 讀 `coverage.json` + per-module threshold dict，缺一個就 exit 1 |
| `docs/runbooks/test-coverage.md` | 新增 | baseline / 怎麼跑 cov local / 怎麼新增 critical-path 模組 / 為什麼整體不擋 |
| `tests/<各 critical-path 模組對應 dir>/test_*.py` | 補 | 跑 baseline 後對 8 模組未覆蓋分支補 test 到 ≥ 80% |

**8 個 critical-path 模組（80% gate 收斂處）**：

1. `shared/approval_queue.py`
2. `shared/alerts.py`
3. `shared/incident_archive.py`
4. `shared/heartbeat.py`
5. `shared/kb_writer.py`
6. `shared/wordpress_client.py`
7. `thousand_sunny/routers/robin.py`
8. `thousand_sunny/routers/bridge.py`

**baseline 量法**（chunk 開頭跑、寫進 PR description）：

```bash
pytest --cov=shared --cov=thousand_sunny --cov=agents --cov-report=term-missing --cov-report=html
```

### Slice 2 — FSM property test（~2 天）

| 路徑 | 動作 | 內容 |
|---|---|---|
| `requirements.txt` + `pyproject.toml` | 改 | 加 `hypothesis>=6.100`（dev dep）|
| `tests/property/__init__.py` | 新增 | empty init |
| `tests/property/test_approval_queue_fsm.py` | 新增 | `RuleBasedStateMachine`：每個 transition rule 對應 `ALLOWED_TRANSITIONS` 一條邊；`@invariant()`：(a) status 一律在 7 enum 內、(b) 無 published→X / rejected→non-archived / archived→X 倒退、(c) claimed 必須先經 approved、(d) state.db CHECK constraint 跟 ALLOWED_TRANSITIONS dict 兩邊一致 |
| `tests/shared/test_alerts_dedupe.py` | 新增（或併入既有 `test_alerts.py`）| 5-8 個 deterministic edge case：window boundary（fire 在 `suppress_until` 前 1ms / 同 1ms / 後 1ms）/ multi-key 互不影響 / fire_count 累加 / TTL expiry / 同 dedup_key 跨 boundary 重新 active |

**hypothesis 設定**：`max_examples=100`，`stateful_step_count=20`，`derandomize=True`（CI deterministic）。

### Slice 3 — Schema round-trip test（~1 天）

| 路徑 | 動作 | 內容 |
|---|---|---|
| `tests/shared/test_schema_roundtrip.py` | 新增 | parametrize 10 個 V1 schema，每個跑 `model_dump_json()` → `Schema.model_validate_json()` → field-by-field compare；nested 用 `model_dump()` 比 dict |

**10 個 critical-path V1 schema（round-trip 範圍）**：

- `shared/schemas/publishing.py`：DraftV1 / DraftComplianceV1 / GutenbergHTMLV1 / PublishRequestV1 / PublishResultV1 / PublishComplianceGateV1 / SEOContextV1
- `shared/schemas/approval.py`：PublishWpPostV1 / UpdateWpPostV1
- `shared/schemas/external/wordpress.py`：WpPostV1（驗 anti-corruption layer）

其餘 schema（`franky.py` / `kb.py` / `external/seopress.py` / `site_mapping.py`）若 Slice 1 帶到就夠，本 slice 不補。

### Slice 4 — Agent E2E golden path（~2 天）

| 路徑 | 動作 | 內容 |
|---|---|---|
| `tests/e2e/conftest.py` | 改（或新增）| autouse fixture：(a) httpx mock LLM endpoints（Anthropic / OpenAI / Gemini） (b) `tmp_path` vault root with `vault_rules.py` enforcement (c) `spec=SlackBot` mock for Slack send (d) `_isolated_state_db` |
| `tests/e2e/test_agent_robin_e2e.py` | 新增 | Robin happy path：給合法 raw URL → trigger Robin map step → KB Wiki page 寫入（concept aggregator 對齊 `feedback_kb_concept_aggregator_principle.md`）→ KB index.md 更新 |
| `tests/e2e/test_agent_brook_e2e.py` | 新增 | Brook happy path：給 topic（DraftRequestV1）→ compose → DraftV1 落入 approval_queue（status=pending） → assert payload schema |
| `tests/e2e/test_agent_zoro_e2e.py` | 新增 | Zoro happy path：trigger trend digest → 寫 vault digest page → mock Slack DM 收到 |

**E2E test 規格**（每個）：

- 入口：呼叫 agent 主函式（`agents.{robin,brook,zoro}.run()` 或同等）
- LLM call：autouse fixture mock（payload 形狀對齊真實 API，`feedback_test_realism.md`）
- vault：tmp_path，`vault_rules.py` 規則照 enforce
- assert：terminal state（vault file exist + frontmatter parsed + approval_queue row）+ 中途 stage 至少 2 個（如 KB write + index update）
- 跑時間：< 3s/test
- marker：無（autouse mock 全攔，CI 跑得動）

## 3. 輸入

| 來源 | 內容 | 已 ready？ |
|---|---|---|
| Plan §Phase 6 deliverable | 4 條 + A bar 定義 | ✅ |
| Decisions doc Q1-Q6 採 A | tooling / dep / 範圍 / mock 策略 / slice 切法 | ✅ |
| `shared/approval_queue.py` `ALLOWED_TRANSITIONS` | FSM single source of truth（state.py CHECK 同步 lock） | ✅ |
| `shared/state.py:212` `alert_state` table | dedupe schema（無 FSM） | ✅ |
| `tests/conftest.py` autouse mock pattern | `_isolated_incidents_pending` 可參照 | ✅（PR #187） |
| 既有 markers `live_wp` / `real_extractor` / `real_slack` | mock vs live 分流範本 | ✅ |
| `feedback_test_realism.md` / `feedback_mock_use_spec.md` / `feedback_test_api_isolation.md` / `feedback_pytest_monkeypatch_where_used.md` | mock 紀律 | ✅（必讀）|
| baseline coverage 數據 | 8 模組目前各 % | ❌ Slice 1 第一步產出 |

## 4. 輸出

每 slice 開獨立 feature branch + PR：

- `feature/phase-6-slice-1-coverage-tooling`
- `feature/phase-6-slice-2-fsm-property`
- `feature/phase-6-slice-3-schema-roundtrip`
- `feature/phase-6-slice-4-agent-e2e`

PR description 必含：

- baseline coverage 數據（Slice 1）/ before-after coverage diff（Slice 2-4）
- hypothesis examples 數 + duration（Slice 2）
- 跑通的 schema 清單 + 任何 round-trip mismatch 處理（Slice 3）
- 三 agent E2E 行為摘要（Slice 4）
- 走 `feedback_pr_review_merge_flow.md`：自動 review → 修修授權 → squash merge

文件：

- `docs/runbooks/test-coverage.md`（Slice 1）
- `docs/capabilities/`：本 chunk 不補新 capability card（test infra 不開源獨立用）

memory：

- chunk 結束更新 `memory/claude/project_quality_uplift_next_2026_04_28.md`：Phase 6 ✅，下一個 chunk = Phase 7（要修修授權，因 staging 要錢/新 VPS）

## 5. 驗收（Definition of Done）

**全 chunk 必達**：

- [ ] 全 repo `pytest` pass（baseline 2281 → 預期 ~2400+）
- [ ] `ruff check` + `ruff format` clean（`feedback_ci_precheck.md`）
- [ ] CI green（含新 coverage gate）

**Slice 1**：

- [ ] `pytest-cov` 安裝在 requirements.txt + pyproject.toml
- [ ] `pytest --cov` 在 local 可跑、輸出 `coverage.json` + html
- [ ] `scripts/check_critical_path_coverage.py` 對 8 模組逐一驗 ≥ 80%
- [ ] 8 個模組各自 ≥ 80%（baseline 量過、缺的補 test）
- [ ] CI workflow 加 critical-path coverage gate 步驟、green
- [ ] `docs/runbooks/test-coverage.md` 寫完

**Slice 2**：

- [ ] `hypothesis` 安裝在 requirements.txt + pyproject.toml
- [ ] `tests/property/test_approval_queue_fsm.py` 跑 `max_examples=100` × stateful 全綠
- [ ] 4 個 invariant（status enum / 不可倒退 / claimed 必經 approved / state.db CHECK 同步）全部驗到
- [ ] `tests/shared/test_alerts_dedupe.py` 5-8 deterministic edge case 全綠
- [ ] 若 hypothesis 抓到 invariant violation：開 issue + 修進同 PR + 加 regression test

**Slice 3**：

- [ ] `tests/shared/test_schema_roundtrip.py` 10 schema 全綠
- [ ] round-trip 對齊：`model.dump_json() → validate_json()` 等價（含 `Optional[None]` / nested model / `Decimal` 等 corner case）
- [ ] 若有 schema 不能 round-trip：flag 出來、加 regression test、評估是 schema bug 還是 test 期待錯

**Slice 4**：

- [ ] `tests/e2e/conftest.py` autouse fixture 不漏 mock 任何對外 API（grep 過 `httpx.AsyncClient` / `slack_sdk` / `anthropic` / `openai` / `google.genai` import 全攔）
- [ ] 三 agent E2E 各 < 3s 跑時、不打真 API
- [ ] vault tmp_path 內 `vault_rules.py` 一樣 enforce（不能寫 Journals/、Raw 不可改寫）
- [ ] mock 用 `spec=`/`autospec=True`（`feedback_mock_use_spec.md`）

**VPS 部署 gate**：

- [ ] 本 chunk 不影響 VPS 部署（純 test 改動，systemd service code path 不動）
- [ ] CI 通過 = deploy 通過，不需 ssh restart

## 6. 邊界（明確不碰）

**不在 Phase 6 做**（scope creep 守門）：

- ❌ **重構 production code**：除非 coverage gate 強迫（先把 logic 抽出可測），否則只補 test、不動 src
- ❌ **`ALLOWED_TRANSITIONS` 改邏輯**：property test 是「發現 invariant violation」，發現了才修；不為了好寫 test 改 FSM
- ❌ **alert_state 重構成真 FSM**：Decisions Q3=A 已決定走 dedupe deterministic，不重設計 alerts.py
- ❌ **Nami / Sanji / Usopp / Franky / Chopper E2E**：Decisions Q4=A 限縮三 agent；Usopp 已有 `live_wp` E2E，其他留 follow-up
- ❌ **Live LLM E2E**：Decisions Q5=A 走 mock；想驗真實 LLM 行為另開 chunk + 加 `@pytest.mark.real_llm` marker
- ❌ **VCR cassette**：Decisions Q5=A 否決，本 chunk 不引入新 mock infra
- ❌ **整體 coverage 50% gate**：本 chunk 只 critical-path 8 模組 80% gate；整體 50% 留給後續（plan A bar 第一條）
- ❌ **新 ADR**：純 test 補齊，不需 ADR
- ❌ **Phase 7 staging / Phase 8 CI auto-deploy**：明確告知修修「Phase 7 要錢/新 VPS 必先問」（next memo 已記）

**不能碰的既有檔案**（避免副作用）：

- `shared/approval_queue.py` 的 `ALLOWED_TRANSITIONS` / `transition()` 邏輯**不改**（property test 只讀）；如真有 bug 開 issue + 獨立 PR
- `shared/alerts.py` dedupe 邏輯不改
- 既有 V1 schema 形狀**不改**；如 round-trip 抓到 bug 在 dump/validate side fix
- `tests/conftest.py` 既有 autouse fixture 不刪不改 signature；只 append
- `pyproject.toml` `[tool.pytest.ini_options]` 既有 markers 不刪
- `.github/workflows/*.yml` 既有 step 順序不動，只 append coverage gate step

**架構決策需要回來問**（`feedback_ask_on_architecture.md`）：

- 若 Slice 1 baseline 顯示某模組已遠超 80%（如 90%+）但缺 critical branch coverage（line 蓋到但行為沒 assert）→ 報告修修決定要不要把 threshold 拉到 90%
- 若 Slice 2 hypothesis 抓到 ≥ 1 個真 invariant violation 在 production → 停下來報修修決定要不要先暫停 prod publish 流程（保守）還是 hotfix 同 PR 走（aggressive）
- 若 Slice 4 mock LLM payload 對齊真實 API 時發現某 agent 在 prod 用了未文件化的 API 形狀（`feedback_test_realism.md`）→ flag deviation、issue tag、不默默對齊 mock 形狀

---

## 實施順序建議（非強制，但 stacked PR dependency 強制 1 在前）

**順序**：Slice 1 → 2 → 3 → 4，stacked PR（每個 rebase 上前一個 merged main）。

| Slice | 預估 | 解 unblock |
|---|---|---|
| 1 — coverage 工具 + 8 模組 80% | 2 天 | 2/3/4 都需要 pytest-cov 量 before/after diff |
| 2 — FSM property test | 2 天 | 可 parallel with 3，但跟 1 一樣建議序貫避免 conftest 衝突 |
| 3 — Schema round-trip | 1 天 | 純新檔，最小衝突 |
| 4 — Agent E2E | 2 天 | conftest fixture 改動最大，最後做 |

**總計**：~7 天 part-time，對齊 plan §Phase 6 effort 估計。

## 交付方式

每個 slice 開 feature branch + PR，PR 走 `feedback_pr_review_merge_flow.md`：自動跑 ultrareview（如修修授權 + 額度有）+ 本地 3-agent code-review → 報告 → 等修修授權 → squash merge → pull + 刪 branch。

每個 PR 交付時附 [P7 完工格式](../../CLAUDE.md)（What changed / Impact / Self-review / Remaining）+ 上述 §4 PR description 必含項。

chunk 收尾更新 `memory/claude/project_quality_uplift_next_2026_04_28.md`：Phase 6 ✅、整體 8/9 ✅、下一個 chunk Phase 7 標明「要修修授權」（plan A bar staging C−→A 要錢 + 新 VPS）。
