---
name: Franky Phase 1 monitor 全三 slice 完工（Mac 機執行）
description: 2026-04-23 第二台 Mac 把 ADR-007 三個 slice 全部做完並 merged（PR #74/#75/#76），805 tests / 804 pass
type: project
tags: [franky, phase-1, complete, pr-74, pr-75, pr-76]
originSessionId: d6248f62-150b-43c6-ac05-98394223e172
---
## 完工狀態（2026-04-23）

ADR-007 Franky Phase 1 slim 版全三 slice 上 main：

| PR | Slice | Squash commit | 內容 |
|---|---|---|---|
| #74 | Slice 1 | `b22ecaf` | health_check + /healthz + alert_state/health_probe_state 表 + UptimeRobot runbook |
| #75 | Slice 2 | `c0ce485` | alert_router + slack_bot + r2_backup_verify + r2_client + r2_backup_checks 表 |
| #76 | Slice 3 | `91e08b4` | weekly_digest + /bridge/franky dashboard + capability card |

CI baseline：740 → 805 tests，0 regression。

## 還沒做的事（修修手動 / 之後排）

- **VPS .env 補齊**：`SLACK_SHOSHO_USER_ID`、`R2_ACCOUNT_ID`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`、`R2_BUCKET_NAME`（[feedback_vps_env_drift_check.md](feedback_vps_env_drift_check.md) 硬規則）
- **VPS cron 加三條**：
  - `*/5 * * * * python -m agents.franky health`
  - `30 3 * * * python -m agents.franky backup-verify`
  - `0 10 * * 1 python -m agents.franky digest`
- **UptimeRobot 設定**：跑 [docs/runbooks/uptimerobot-setup.md](../../docs/runbooks/uptimerobot-setup.md)
- **Dashboard 驗看**：`/bridge/franky` 登入實看，確認 4 張 probe 卡 + 24h alert list 美學沒跑掉

## Phase 2 伏筆（Slice 3 的 capability card 有寫）

- `vps_metrics` 時序表 + `vps_monitor.py` sampler → dashboard 圖表
- `cron_runs` 表 + `shared/cron_wrapper.py` → 更準的 cron success rate（現在靠 agent_runs 代）
- `alert_events` append-only log → 真 24h alert timeline（現在 `alert_state` 是 latest-per-dedup_key）
