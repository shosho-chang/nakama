# Test Coverage — 量測 + critical-path gate

**Owner:** Phase 6 Slice 1（[task prompt](../task-prompts/2026-04-26-phase-6-test-coverage.md)）
**Decisions source:** [Q1=A 採用 pytest-cov + critical-path 80% gate + 整體不擋](../plans/2026-04-26-phase-6-test-coverage-decisions.md)

---

## 量法

### 本地一次性

```bash
pytest --cov=shared --cov=thousand_sunny --cov=agents \
       --cov-report=term-missing --cov-report=html
```

`htmlcov/index.html` 用瀏覽器開可看 line-by-line 哪行沒蓋。

### CI 自動跑

`.github/workflows/ci.yml` 兩步：

1. `pytest --cov=... --cov-report=json:coverage.json --cov-report=term`（整體不設 `--cov-fail-under`，**整體 % 不擋 CI**）
2. `python scripts/check_critical_path_coverage.py`（讀 `coverage.json`，對 critical-path 模組逐一驗 ≥ threshold；缺一個就 exit 1）

### 預設不在 pytest 跑 cov

`pyproject.toml` `[tool.pytest.ini_options]` **沒**加 `--cov`。原因：本地 dev 跑單測加 `--cov` 會慢 ~30%。要量自己 explicit 加 flag。

## 設計哲學：不退步 gate

`scripts/check_critical_path_coverage.py` 的 `THRESHOLDS` dict 是「**不退步 gate**」（regression prevention），不是「**目標 gate**」（aspirational）。

每個 threshold 用該模組 baseline % round-down 至最近 5%/10%。例：

- 模組 baseline 97.08% → threshold 95%（容許小退步、CI 不噪）
- 模組 baseline 91.15% → threshold 90%
- 模組 baseline 100.00% → threshold 100%（已滿，禁退）

PR 退步只要還 ≥ threshold 都 OK；目標是「某次大幅補完後 lock 在新 baseline」，不是逼 contributor 每次 PR 都拉高。

## Critical-path 模組（8 個 starting set）

Phase 6 Slice 1 啟動 set，對齊 plan §Phase 6 「production critical path 模組 ≥ 80%」承諾：

| 模組 | 角色 | Threshold | Baseline 2026-04-26 |
|---|---|---|---|
| `shared/approval_queue.py` | publish FSM single source of truth | 95% | 96.77% |
| `shared/alerts.py` | alert dedupe + dispatch | 100% | 100.00% |
| `shared/incident_archive.py` | Phase 4 archive 自動化 | 90% | 93.13% |
| `shared/heartbeat.py` | per-cron heartbeat | 100% | 100.00% |
| `shared/kb_writer.py` | KB 結構寫入 / aggregator | 90% | 91.15% |
| `shared/wordpress_client.py` | WP REST + media + post CRUD | 90% | 90.48% |
| `thousand_sunny/routers/robin.py` | Robin SSE + 處理頁 | 95% | 97.08% |
| `thousand_sunny/routers/bridge.py` | Bridge UI mutation + cost API | 80% | 77.56% → 86.67%（Slice 1 補 `/api/agents` 測） |

## 怎麼新增 critical-path 模組

1. 跑 baseline：`pytest --cov=... --cov-report=json:coverage.json`
2. 讀數字：`python -c "import json; print(json.load(open('coverage.json'))['files']['<path>']['summary']['percent_covered'])"`
3. round-down 至最近 5% / 10%（不退步 gate 哲學，見上節）
4. 加進 `scripts/check_critical_path_coverage.py` 的 `THRESHOLDS` dict，附 `# baseline X.XX%（理由）` 註解
5. 跑 `python scripts/check_critical_path_coverage.py` 確認該模組過 gate
6. PR description 附 baseline 量法 + 為什麼是 critical-path

## 怎麼調 threshold（往上 raise 才行）

只調往上 raise，不允許 lower（`feedback_no_premature_execution.md`：不退步 gate 的承諾）。raise 時序：

1. 寫了一波測試，coverage 跳到 97% → PR 把 threshold 從 90 → 95（不到 97 也行，留 buffer）
2. 跑 CI 確認過 gate
3. PR description 附 before/after

## 為什麼整體 coverage % 不在 CI 擋？

Plan A bar 寫「整體 ≥ 50%」是長期目標。當下整體已 81%（baseline 2026-04-26）— 遠超 50%。但：

- 整體 % gate 容易誤殺：新 module 寫了 80% test，整體 % 拉低 0.X% 觸發紅
- 真正風險集中在 critical-path 上：publish FSM / alerts / KB writer / router 退步 = 用戶 visible 災難
- Critical-path gate 用「個別 + 高 threshold」捕捉風險，整體 % 留作 dashboard 觀察

如未來想 enforce 整體 ≥ X%（例：plan A bar 50% lock），加進 CI workflow：

```yaml
- name: Run tests with coverage
  run: pytest --cov=... --cov-fail-under=50
```

## Coverage 排除規則

`pyproject.toml` `[tool.coverage.report] exclude_lines`：

- `pragma: no cover` — 顯式標記
- `raise NotImplementedError` — 抽象方法
- `if __name__ == "__main__":` — module entrypoint
- `if TYPE_CHECKING:` — 純 import 給 type checker
- `\.\.\.` — protocol body

不要無故新增；模組真實未測 path 不應藏在 exclude 裡。

## 跟 Phase 6 後續 slice 的關係

| Slice | 跟 coverage 的關係 |
|---|---|
| 1（本 PR） | 工具 + 8 模組 baseline gate（已完成） |
| 2 — FSM property test | hypothesis 加 dep；approval_queue / alert dedupe coverage 自然上升 |
| 3 — Schema round-trip | 10 schema 路徑 coverage 上升；不直接動 critical-path |
| 4 — Agent E2E | agents/* coverage 上升（目前未列 critical-path）；視情況 raise threshold 或加新 critical-path 模組 |

每個 slice PR description 附 before/after critical-path 數字。
