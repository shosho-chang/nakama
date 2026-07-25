# Zoro — 劍士（多領域 Agent）

Zoro 是多領域 agent，兩條互相獨立的能力。入口 `agents/zoro/__main__.py`（argparse；無 subcommand → exit 2，不 fallback）。

## 1. 對外情報（Scout / Keyword Research）

- **brainstorm scout**：`python -m agents.zoro scout`（cron 每日）— 從 Trends / SERP / Reddit / YouTube 拉新主題情報，pick 後推 Slack。
- **keyword research**：`keyword-research` skill + `/bridge/zoro` — 雙語關鍵字研究。

ADR-012：Zoro = 向外搜尋；SEO audit / enrich 屬 Brook 對內加工。

## 2. 對內健身教練（Coach，Garmin 連動）

Owner personal-ops 能力（**ADR-053**），與內容 pipeline 正交、**非醫療建議**。`agents/zoro/coach/` 子套件：

| 模組 | 職責 |
|---|---|
| `garmin_read.py` | 讀回 Garmin 重訓 set（reps/weight/rest）+ schema 驗證（失敗即 raise + alert）|
| `progression.py` | volume-load / E1RM(≤10 reps) / 每肌群每週 hard sets / 2-for-2 加重 / deload 訊號 |
| `planner_strength.py` | ACSM 2026 / NSCA LLM 課表生成（輸出經 guardrail）|
| `guardrail.py` | 純程式硬擋（load-rep 雙向 / 容量上限 / 1RM 安全 / concurrent 干擾）|
| `builder_spec.py` | 課表 → Garmin Strength Builder 步驟（含 target weight；Fenix 8 支援）|
| `hitl.py` | guarded 規格 → ADR-006 審批 payload（`WriteGarminWorkoutV1`）|
| `muscle_map.py` / `profile.py` | 肌群對映（版本化）/ 教練 profile（balanced + intermediate + MEV/MAV/MRV）|

**CLI：** `python -m agents.zoro coach-sync --since 8w`（讀回 8 週重訓；heartbeat `zoro-coach-sync`）。

**依賴：** `garminconnect`（optional extra `coach`，需 Python ≥3.12；garth 已死，0.3.x 原生 DI OAuth）。token 在 `data/garmin/`（gitignored），本機 MFA 登入後搬 VPS，非互動 silent refresh。

詳見計畫 `docs/research/2026-06-29-zoro-coach-implementation-plan-v2.md` 與 ADR-053。Phase 2 將加：Tredict 室內單車、恢復（冥想 + CWI 時機）、行事曆排程、週日自動化、Slack 對話 / readiness 調整。
