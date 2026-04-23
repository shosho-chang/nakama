---
name: Franky Phase 1 monitor 交給另一台 Claude Code 並行開發
description: 2026-04-23 handover 給第二台機器執行 Franky 健康檢查 + 告警 + R2 備份 + 週報三 slice；主機器不碰 agents/franky/
type: project
tags: [franky, phase-1, parallel-dev, handover]
---

## 現況（2026-04-23）

主機器跑 Qwen bench 同時 handover Franky monitor 給另一台開發機（也是 Claude Code）。

## 交給對方的範圍

依 ADR-007 slim 版，三個獨立 PR：

1. **Slice 1 — 健康檢查核心**（`feature/franky-slice-1-health`）
   - `agents/franky/health_check.py`（systemd timer / VPS+WP+Nakama service probe / `health_probe_state` 表）
   - `shared/schemas/franky.py`（HealthProbeV1 / AlertV1）
   - `migrations/003_franky_tables.sql`
   - `thousand_sunny/routers/franky.py` — `GET /healthz`（UptimeRobot 探測）
   - `docs/runbooks/uptimerobot-setup.md`

2. **Slice 2 — 告警 + R2 備份**（`feature/franky-slice-2-alert-backup`）
   - `agents/franky/alert_router.py`（三級分派 + `alert_state` 表 15-min 去重）
   - `agents/franky/r2_backup_verify.py`（daily cron + state.db snapshot push）

3. **Slice 3 — 週報 + 儀表板**（`feature/franky-slice-3-digest-dashboard`）
   - `agents/franky/weekly_digest.py`（週一早 10:00 Slack DM）
   - `thousand_sunny/routers/franky.py` `GET /bridge/franky` 儀表板
   - Capability card

Paste 用的完整六要素 prompt 在本次對話記錄裡（未寫進 repo 的 `docs/task-prompts/`；若對方要，主機器這邊可以補寫）。

## 主機器的邊界

不碰 `agents/franky/`、`shared/schemas/franky.py`、`thousand_sunny/routers/franky.py`、`migrations/003_*.sql`、`docs/runbooks/uptimerobot-setup.md`、`docs/capabilities/franky-monitor.md`。若另一台卡住，可以 pair 但不要 push 到同 branch。

## 憑證狀態（對方直接用）

全在 `.env`（見 [project_phase1_infra_checkpoint.md](project_phase1_infra_checkpoint.md)）：
- `SLACK_FRANKY_BOT_TOKEN` + `SLACK_FRANKY_APP_TOKEN`
- `/home/nakama/secrets/gcp-nakama-franky.json`（VPS 上）
- `CLOUDFLARE_API_TOKEN` + `R2_ACCESS_KEY_ID/SECRET_ACCESS_KEY/BUCKET_NAME`
- WP health_check 走剛 merged 的 `shared/wordpress_client.WordPressClient.health_check()`

## How to apply

- 主機器若接下 Phase 1 其他任務（Usopp Slice B、Brook compose、Bridge drafts UI）可以平行做
- 對方開 PR 時主機器這邊照 `feedback_pr_review_merge_flow.md` 跑 code-review 給意見
- 收工後寫 capability card + 下一輪整合測試（Usopp Slice C E2E 跑通之後一起驗 Franky `/bridge/franky` 顯示）
