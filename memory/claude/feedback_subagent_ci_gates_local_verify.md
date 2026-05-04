---
name: Sub-agent dispatch 必含 local CI 預檢 acceptance（ruff check / format / coverage）
description: 2026-05-04 overnight 6 PR dispatch 5 條 fix-agent；3/5 hit ruff lint failure on CI、1/5 hit critical-path coverage gate。Sub-agent 都跑 pytest 驗自己加的 test 通過 → 沒跑 repo-level lint / coverage。Dispatch prompt acceptance 必明文加「push 前 ruff check . / ruff format --check . / coverage 不退」。
type: feedback
created: 2026-05-04
---

Sub-agent dispatch overnight 6 PR fix（[project_session_2026_05_04_overnight_smoke_followup](project_session_2026_05_04_overnight_smoke_followup.md)）暴露 dispatch prompt 寫法的結構盲區：每個 sub-agent 跑 `pytest tests/<their_test_file>.py` 驗自己加的 test pass → push → CI run repo-level checks → fail。

**Why**：sub-agent 視野 narrow on the file/test they're touching；不會 reflex 跑 repo-wide ruff / coverage gate。CI 才是 ground truth — main context 等 CI report 後再追修，是純 wasted cycle。

**How to apply（dispatch prompt acceptance 必含這 3 條）**：

1. **`ruff check .`** — 不只跑 sub-agent 自己的 file。Sub-agent 加 import / variable 可能撞 unused import (F401) / unused var (F841) / import not sorted (I001) on adjacent files
2. **`ruff format --check .`** — 同上 repo-wide。Sub-agent 寫的新 test file 預設沒 ruff format pass
3. **`python scripts/check_critical_path_coverage.py`** —（or 等價 coverage gate）— 任何 touch 到 critical-path file (`shared/approval_queue.py` / `shared/alerts.py` / `shared/incident_archive.py` / `shared/heartbeat.py` / `shared/kb_writer.py` / `shared/wordpress_client.py` / `thousand_sunny/routers/robin.py` / `thousand_sunny/routers/bridge.py`) 的 PR 都會被 gate 擋。Sub-agent 加新 helper 不寫 edge case test 直接 fail（#374 `_flip_status_to_translating` 的 OSError + count==0 兩條 branch 沒被覆蓋）

**Dispatch prompt 模板片段**（直接貼進 P9 task acceptance 段）：

```
## Pre-push CI gate (acceptance — 缺任一條 = task 不算完成)

Before commit + push, run THESE locally with the venv at `.venv/Scripts/python.exe`:

1. `ruff check .` — must pass with 0 errors. If new lint errors appear, fix them.
2. `ruff format --check .` — must pass. If files would be reformatted, run `ruff format <files>` and commit.
3. If you touched any file under `shared/` (especially the critical-path list) OR `thousand_sunny/routers/robin.py` / `thousand_sunny/routers/bridge.py`, run:
   `python scripts/check_critical_path_coverage.py`
   It must pass. If your new code drops a module below threshold, add tests covering the new branches (especially error / edge paths in any new helper functions).
4. Run the full test file containing your test PLUS the file you modified, e.g.:
   `pytest tests/<your_test>.py tests/<adjacent_test>.py -x -q`

ONLY push after these 4 gates pass. If you push and CI fails, the orchestrator has to manually fix — that wastes the dispatch overhead. Treat CI gate failure as your task's failure.
```

**例外：純 docs / template / copy-only PR** — ruff 不適用、coverage 不變。但 ruff format check 仍要跑（templates 是 jinja，不會被 ruff 動，但 .py 邊角檔可能 carry-over）。

**自我 audit checkpoint when writing dispatch prompts**：

- ❌ 「sub-agent 跑 pytest 通過就好」 → ✓ 加 ruff + coverage gate
- ❌ 「critical-path coverage 是 CI 的事」 → ✓ sub-agent 也要驗，CI fail 才修是浪費 dispatch
- ❌ 「fix 改一行 test 不會掉 coverage」 → ✓ 任何加 helper / branch 都可能掉，必驗

**關連 memory**：

- [feedback_dispatch_everything_minimize_main_context](feedback_dispatch_everything_minimize_main_context.md) — 高指導原則
- [feedback_ci_precheck](feedback_ci_precheck.md) — 自己 commit 前必跑全 repo ruff format check（同主題在 self-direct context 已寫過；這條把它推到 dispatch context）
- [reference_sandcastle](reference_sandcastle.md) — sandcastle 同樣盲區，prompt template 同樣要加
