---
id: 2026-04-26-drill-state-restore
title: DR drill — state.db 從 R2 restore（半量級）
severity: drill
status: closed
detected_at: 2026-04-26T21:23:19+08:00
mitigated_at: 2026-04-26T21:25:18+08:00
resolved_at: 2026-04-26T21:25:18+08:00
postmortem_due:
trigger: manual
owner: 修修 + Claude
tags:
  - drill
  - dr
  - backup
  - r2
  - quality-uplift-phase-1
---

# DR drill — state.db 從 R2 restore（半量級）

> **本檔不是 incident，是計畫性 drill**（quality-uplift Phase 1 grey-fix）。`severity: drill` 為非標準 tier，用以區分演練與真實事件。
> **流程定義**：[`docs/runbooks/disaster-recovery.md`](../../../../../nakama/docs/runbooks/disaster-recovery.md) §6.1
> **任務凍結**：[`docs/task-prompts/2026-04-27-phase-1-4-grey-fix.md`](../../../../../nakama/docs/task-prompts/2026-04-27-phase-1-4-grey-fix.md) §4.1

## Summary

2026-04-26 21:23–21:25 跑 quality-uplift Phase 1 第一次 DR drill，按 disaster-recovery.md §6.1 半量級 protocol 從 R2 `nakama-backup` bucket 還原最新 state.db snapshot 到隔離路徑（`/tmp/dr-drill-20260426/state.db`），`verify_db()` integrity OK + 25 tables 817 rows 解壓正確。實測 wall-clock 約 ~2 分鐘（含 troubleshooting），happy-path restore-only ~30 秒，遠低於 §1 RTO 30 min 目標（涵蓋停 service + 完整 smoke 的預算）。發現 4 個 runbook gap，需回灌 §6.1 / §3。

## Timeline（Asia/Taipei）

| 時間 | 事件 |
|---|---|
| 21:23:19 | drill 啟動，Mac 端 cwd `/Users/shosho/Documents/nakama` |
| 21:23:26 | 第一次 `python scripts/restore_from_r2.py list` 失敗：`R2Unavailable: missing R2 env: ['R2_ACCOUNT_ID']` |
| 21:23:30 | `dotenv_values('.env')` 確認：Mac `.env` 的 `R2_ACCOUNT_ID` / `NAKAMA_R2_BACKUP_BUCKET` / `R2_BUCKET_NAME` 三 keys 都是空 string（key present, value empty） |
| 21:24:55 | 切到 VPS sandbox 模式（pivot），ssh 進 `nakama-vps` 重跑 list — 4 snapshots OK，最新 4/26 44 KB gz |
| 21:25:04 | VPS `restore --target /tmp/dr-drill-20260426/state.db --apply` 啟動 |
| 21:25:05 | restore 完成：fetched gz=44431, decompressed=368640，integrity OK，applied 到 `/tmp/dr-drill-20260426/state.db` |
| 21:25:08 | 嘗試 runbook §3 line 116 / §6.1 line 275 的 `sqlite3 ... .schema` smoke check — `bash: sqlite3: command not found`（VPS 沒裝 sqlite3 CLI） |
| 21:25:18 | 改用 `python3 -c` + `sqlite3` stdlib 取代，列出 25 tables 完整 row breakdown：alert_state 5 firing / api_calls 406 / scout_seen 259 / agent_events 39 / ... |
| 21:25:18 | drill 結束 |

**抓時間從 ssh + Mac shell `date` 命令、不憑印象**（按 postmortem §4 標準）。

## Detection / Mitigation / Restore steps（哪些 runbook 段沿用、哪些失靈）

### ✅ 沿用無問題

| 步驟 | runbook ref | 結果 |
|---|---|---|
| `python scripts/restore_from_r2.py list --db state` | §6.1 step 2 | 4 snapshots 列出，sort by last_modified desc 正確 |
| `restore --target {path} --apply` 流程 | §3 / §6.1 | gz fetch + gunzip + verify_db 三步串得通；pre-restore backup 邏輯正確（target 不存在時 backup_path=None） |
| `verify_db()` integrity check | `shared/sqlite_integrity.py` | OK，回 24 tables（不含 sqlite_sequence）/ 817 rows |
| TemporaryDirectory cleanup | `restore_from_r2.py` line 181 | `with` block 正常結束，無 leak |

### ❌ 失靈 / 文件不齊

見 [§Findings](#findings) 詳列。

## Findings

### Finding 1 — runbook §6.1 line 272 範例缺 `--apply` flag（HIGH）

**現況**：runbook §6.1 line 272 寫
```
python scripts/restore_from_r2.py restore --db state --target /tmp/dr-drill-$(date +%Y%m%d)/state.db
```
但 `restore_from_r2.py` line 176-179：dry-run（沒 `--apply`）的話 target 用 TemporaryDirectory，with-block 結束就清掉。runbook 接下來 line 275 的 `sqlite3 /tmp/dr-drill-*/state.db ...` 必然 file-not-found。

**Why hidden**：runbook 沒人按字面跑過。drill protocol 寫了 §6.1 但從未演練（這次是首次）。

**Action**：[A1] runbook §6.1 line 272 補 `--apply`；同段 commentary 解釋 dry-run vs apply 對 `--target` 的不同行為。

### Finding 2 — Mac 端 `.env` R2 keys 是空 string，§6.1 假設失靈（HIGH）

**現況**：runbook §6.1 預設「桌機/Mac 端」跑 drill，但 R2 backup credentials 只在 VPS `.env`。Mac `.env` 雖有 key 但 value 為空，Mac 跑 restore 直接 `R2Unavailable`。

**Why hidden**：寫 §6.1 時假設 backup credentials 已在所有開發機上同步，實際只 VPS 在跑 backup（`backup_nakama_state.py` 是 cron 作業）。

**Action**：
- [A2] runbook §6.1 改 default 模式為 **VPS sandbox**（隔離 `--target /tmp/dr-drill-{date}/`，不影響 prod `/home/nakama/data/state.db`）。
- [A3] §6.1 補 prerequisite section：「Mac 端跑須先 ssh 抄 R2 read-only credentials 進 Mac `.env`，或用 `mode='read'` scoped key（VPS 端有 `NAKAMA_R2_READ_*` 可選 fallback）」。

### Finding 3 — VPS 沒裝 `sqlite3` CLI，runbook 多處 smoke check 會 command-not-found（MID）

**現況**：runbook §3 line 116 / §6.1 line 275-277 多處用 `sqlite3 data/state.db '...'` 做 manual smoke。實測 VPS 沒裝 `sqlite3` 套件（`apt install sqlite3` 缺）；只有 python 內建 `sqlite3` module 可用。

**Why hidden**：VPS 設定流程沒記入手動安裝過 sqlite3 CLI；restore script 內部用 stdlib 不需要 CLI，所以這 gap 從沒在自動路徑暴露。

**Action**：
- [A4] runbook §3 / §6.1 把 sqlite3 CLI 例改成 python `python3 -c "import sqlite3; ..."` 一行替代（避免 install dep）。
- [A5] disaster-recovery.md §B-3「基礎安裝」line 153 `apt install` 清單追加 `sqlite3`（新 VPS 重建時就裝）。

### Finding 4 — restore script `verify_db` 回 24 tables 但 sqlite_master 列 25（LOW）

**現況**：restore report 印 `Tables 24`，但實際 `SELECT COUNT(*) FROM sqlite_master WHERE type='table'` 回 25（差 1 個是 `sqlite_sequence` 系統表）。

**Why**：`shared/sqlite_integrity.verify_db()` 應該過濾系統表。差 1 不影響 integrity 結論，純報表數字一致性。

**Action**：[A6] 低優先，先不修。下次 verify_db sweep 時對齊。記在 `project_quality_uplift_next_*.md` follow-up。

## Root cause（半量 drill 一行帶過）

非真實 incident，不適用 5-why。Findings 1-3 的共同 root cause：**runbook 從未實際 dry-run 演練過**，文件 → 真實環境的差異只能靠 drill 暴露。Phase 1 grey-fix 的存在意義就是這個。

## Action items

| ID | 動作 | Owner | Due | 狀態 |
|---|---|---|---|---|
| A1 | runbook §6.1 line 272 補 `--apply` | Claude（本 PR） | 2026-04-27 | 進行中 |
| A2 | runbook §6.1 default 模式改 VPS sandbox（保留 Mac 模式為 alt） | Claude（本 PR） | 2026-04-27 | 進行中 |
| A3 | runbook §6.1 補 prerequisites（Mac 模式須同步 R2 credentials） | Claude（本 PR） | 2026-04-27 | 進行中 |
| A4 | runbook §3 / §6.1 sqlite3 CLI 例改 python stdlib | Claude（本 PR） | 2026-04-27 | 進行中 |
| A5 | 現役 VPS `apt install sqlite3` 補裝（runbook §B-3 line 153 已含 sqlite3，新建 path 已涵蓋；本 finding 是現役機未裝） | 修修（手動） | 2026-04-27 | 待修修 ssh 執行 |
| A6 | `verify_db()` table count 排除 sqlite_sequence | 修修（next sweep） | — | open（low） |
| A7 | 把本 outcome 文件存進 vault `Incidents/2026/04/`（首份 incident-class 文件） | Claude（本 turn） | 2026-04-26 | ✅ 完成 |
| A8 | RTO 目標檢視：實測 restore <30s vs §1 30 min RTO（含完整 stop-service + smoke 預算） | 修修 + Claude | 2026-04-27 | 進行中（runbook §1 加 caveat） |

A1-A5 + A8 在本次 grey-fix PR 解，A6 留 follow-up。

## Measured RTO vs target

| 步驟 | 實測 wall-clock | runbook 假設 |
|---|---:|---|
| `list` snapshots | <2 sec（VPS） | — |
| `restore --apply` (gz fetch + gunzip + verify) | ~1 sec（44 KB） | <30 min |
| python sqlite verify | <2 sec | <30 min |
| **總計（VPS sandbox happy path）** | **<10 sec** | **30 min** |
| 含本次 troubleshooting + Mac → VPS pivot | ~2 min | — |

**Verdict**：state.db restore-only step 充分快，30 min RTO 預算 99% 都在 stop-service / smoke / Slack 通知 / 修修人為反應時間。
**未驗證**：full Scenario A（停 service → mv corrupt → restore over `/home/nakama/data/state.db` → start service → smoke）下一次 drill 補（建議下半年再做一次，模式 = full Scenario A，需 ~5-10 min maintenance window）。

## Lessons learned

1. **「runbook 文件齊」≠「runbook 跑得通」** — 本 drill 4 個 finding 全是「沒人實際跑過」的 gap。半年一次 drill 是 SLA，不是 nice-to-have。
2. **drill 自身就是 deliverable** — outcome 文件 + runbook diff 才是 grey 洗綠的證據，光 drill 不寫文件等於沒做。
3. **drill 暴露的 schema drift 也是 finding** — Mac 端 R2 credential gap 反映「workflow inventory drift」（修修桌機/Mac/VPS 三機開發環境不對等），值得進 `feedback_*` 教訓。

## 相關文件

- [`docs/runbooks/disaster-recovery.md`](../../../../../nakama/docs/runbooks/disaster-recovery.md) — 本次 drill 的 source runbook（§6.1）
- [`docs/runbooks/postmortem-process.md`](../../../../../nakama/docs/runbooks/postmortem-process.md) §3 — incident schema 來源（drill 沿用）
- [`docs/plans/quality-bar-uplift-2026-04-25.md`](../../../../../nakama/docs/plans/quality-bar-uplift-2026-04-25.md) §Phase 1 — drill 在 9-phase 計畫的位置
- [`docs/task-prompts/2026-04-27-phase-1-4-grey-fix.md`](../../../../../nakama/docs/task-prompts/2026-04-27-phase-1-4-grey-fix.md) — 本次 chunk 凍結文件
