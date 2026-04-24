---
name: Usopp daemon VPS 部署完成
description: 2026-04-24 nakama-usopp.service 上線；過程中修了兩個 .env legacy typo + 補 LITESPEED_PURGE_METHOD=noop；Slice C2b unblocked
type: project
tags: [usopp, vps, deployment, phase-1, slice-c1, slice-c2b]
---

## 部署狀態（2026-04-24）

VPS 第三個 systemd service：

| Service | 工作負載 |
|---|---|
| `thousand-sunny.service` | FastAPI web (Bridge UI / Robin REST) |
| `nakama-gateway.service` | Slack Socket Mode (Nami / Sanji / Zoro) |
| **`nakama-usopp.service`** | Publisher poll loop（new） |

```
Active: active (running) since Fri 2026-04-24 13:55:01 CST
Main PID: 373300 (python3)
Memory: ~48M stable
worker_id: usopp-shoshotw
poll_interval: 30s, batch: 5, site: wp_shosho
```

Graceful restart 驗證：SIGTERM → daemon shutdown 1 秒完成（remote `TimeoutStopSec=120` 預算充裕），無 SIGKILL。

## 路上發現的非預期問題（全修）

| # | 問題 | 根因 | 修法 |
|---|------|------|------|
| 1 | 本機 + VPS `.env` 都是 `WP_SHOSHO_USER=` | code 讀 `WP_SHOSHO_USERNAME=`（[shared/wordpress_client.py:134](../../shared/wordpress_client.py#L134)），這是 PR #77 Slice B 之前的 legacy；mocked tests 看不到 | sed 改 key 名（不動 value） |
| 2 | 本機 + VPS `.env` 都是 `WP_SHOSHO_BASE_URL=https://shosho.tw/wp-json` | PR #101 改了 client convention（`_request()` 自己 append `/wp-json/`），但 `.env.example` + 兩端 `.env` 沒 sync | regex 去掉結尾 `/wp-json/?` |
| 3 | VPS 沒 `LITESPEED_PURGE_METHOD=` | code default `"rest"`（[shared/litespeed_purge.py:50](../../shared/litespeed_purge.py#L50)），會在 publish 後真的打 LiteSpeed endpoint | append `LITESPEED_PURGE_METHOD=noop`（C2b 實測前的安全值） |

VPS `.env` 備份在 `/home/nakama/.env.bak.20260424_135123`。

## 操作流程（給未來自己參考）

不能 `scp .env` 整份覆蓋 — 會殺掉 VPS-only keys（[feedback_env_push_diff_before_overwrite.md](feedback_env_push_diff_before_overwrite.md)）。改在 VPS 端 `ssh + sed -i` in-place edit，搭配 `cp .env .env.bak.$(date +...)` 預備回滾。

不要在對話框 echo `.env` value（[feedback_no_secrets_in_chat.md](feedback_no_secrets_in_chat.md)）— 用 `awk` 印 `key=<set>/<empty>` flag、或 grep + sed 抽 host/path 部份就好。

## Unblock 清單

- ✅ Slice C2b — LiteSpeed Day 1 實測完成（2026-04-24）：`LITESPEED_PURGE_METHOD=noop` 為**生產正解不是 fallback**。發現 `POST /wp-json/litespeed/v1/purge` endpoint 根本不存在（404 `rest_no_route`），`shared/litespeed_purge.py` 的 rest method 一直在打空氣；真正的 purge 機制是 LiteSpeed plugin hook `save_post`，WP REST API 寫入天然觸發 auto-invalidate（實測 hit → update → miss → hit，2 秒內完成）。決策表 + 後續 code follow-up 清單在 [docs/runbooks/litespeed-purge.md](../../docs/runbooks/litespeed-purge.md)。

## Follow-up（可選）

- **`shared/litespeed_purge.py` 清理**（code PR）：(1) 預設從 `"rest"` 改 `"noop"` (2) `_purge_via_rest()` 可刪（endpoint 不存在）(3) docstring 反映 WP hook 實際機制
- **ADR-005b §5 更新**（docs PR）：「publish 成功後顯式呼叫 purge」的硬規則要放寬為「WP REST 寫入路徑已由 LiteSpeed plugin hook 處理，不需 explicit call」；硬規則只對非 WP-REST 寫入路徑適用（目前無）
- 寫個 preflight script 對齊 `.env` key names vs `.env.example`，下次 onboard 新機器自動發現 typo（這次 mocked tests 蓋不到的兩個 typo 都是這類）

## 相關

- [project_usopp_slice_c1_merged.md](project_usopp_slice_c1_merged.md) — daemon code（PR #97）
- [project_usopp_slice_c2a_merged.md](project_usopp_slice_c2a_merged.md) — Docker E2E（PR #101）
- [docs/runbooks/deploy-usopp-vps.md](../../docs/runbooks/deploy-usopp-vps.md) — 部署 runbook（已驗證跑得通；建議補一段「.env legacy 三項檢查」放在前置檢查）
- [feedback_wp_base_url_convention.md](feedback_wp_base_url_convention.md) — `/wp-json` convention 正典
- [feedback_env_push_diff_before_overwrite.md](feedback_env_push_diff_before_overwrite.md) — diff 不要 scp
- [feedback_vps_two_services.md](feedback_vps_two_services.md) — 多 service 部署原則（這次變三個了，memory 待更新）
