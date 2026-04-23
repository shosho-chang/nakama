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

## VPS 上線（2026-04-24 完成）

- ✅ `.env` 全部 R2_* 補齊 + `SLACK_USER_ID_SHOSHO` 統一命名（PR #86）
- ✅ `load_config()` 顯式在 `__main__.py` 呼叫，env 不再漏 load（PR #87）
- ✅ 3 條 cron 全裝：health (`*/5`)、backup-verify (03:30)、digest (週一 10:00)
- ✅ Smoke test：`alert --test` → Slack DM 成功 `ts=1776949007.977169`；`backup-verify` → R2 連線 OK；`health` → 4 個 probe 全 ok

## 還沒做的事（修修手動 / 之後排）

- **UptimeRobot 設定**：跑 [docs/runbooks/uptimerobot-setup.md](../../docs/runbooks/uptimerobot-setup.md)（~20 分鐘）
- **Dashboard 驗看**：`/bridge/franky` 登入實看，確認 4 張 probe 卡 + 24h alert list 美學沒跑掉
- **Fleet `.tar.gz` 缺口**：xCloud 對 fleet 只有 DB dump，沒有整站 tarball；修修要去 xCloud console 檢查 fleet 的 Full Backup 有沒有開 file scope

## Phase 2 伏筆（Slice 3 的 capability card 有寫）

- `vps_metrics` 時序表 + `vps_monitor.py` sampler → dashboard 圖表
- `cron_runs` 表 + `shared/cron_wrapper.py` → 更準的 cron success rate（現在靠 agent_runs 代）
- `alert_events` append-only log → 真 24h alert timeline（現在 `alert_state` 是 latest-per-dedup_key）
