# Zoro — CONTEXT

多領域 agent，兩條互相獨立的能力。入口 `agents/zoro/__main__.py`（argparse；`scout` / `coach-sync`；無 subcommand → exit 2，no-fallback）。

## 對外情報
brainstorm scout（`brainstorm_scout.py`）+ keyword research（`keyword_research.py`）。ADR-012「向外 / 對內」分界（Zoro 向外；Brook 對內加工）。

## 對內健身教練（`coach/`，ADR-053）
Garmin 連動：重訓讀回（`garmin_read.py` → `strength_sets`）→ 漸進負荷（`progression.py`）→ 課表生成（`planner_strength.py`）+ 純程式 guardrail（`guardrail.py`）→ Builder 規格（`builder_spec.py`）→ HITL payload（`hitl.py`）。Owner personal-ops，與內容七層 pipeline 正交，**非醫療建議**。

## Key facts
- ADR：ADR-001（角色）、ADR-012（Zoro/Brook 邊界 + coach carve-out addendum）、ADR-053（coach）、ADR-006（HITL payload 擴充）。
- 資料：`strength_sets`（`shared/strength_sets_store.py`、`migrations/018_strength_sets.sql`）。
- 依賴：`garminconnect`（optional extra `coach`，Python ≥3.12）；token `data/garmin/`（gitignored）。
- 計畫：`docs/research/2026-06-29-zoro-coach-implementation-plan-v2.md`。
- 成本：`set_current_agent("zoro")`（coach MVP 掛 zoro）。
