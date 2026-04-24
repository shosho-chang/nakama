# Usopp — 狙擊手（Publisher Agent）

精準將已審核的 Brook `DraftV1` 發布到 WordPress（shosho.tw / fleet.shosho.tw），含 SEOPress meta、LiteSpeed cache purge、台灣藥事法/醫療法詞彙攔截、crash-safe state machine。

**排程：** Daemon poll `approval_queue`（預設每 30 秒），批量 claim + publish
**狀態：** Phase 1 Slice C1（daemon + unit tests 完備，staging E2E 未跑）

---

## 能力

詳見 [docs/capabilities/wordpress-publisher.md](../../docs/capabilities/wordpress-publisher.md)。要點：

- Crash-safe 8 階段 state machine（`publish_jobs` 表持久化；重啟續跑）
- 雙層 idempotency（Nakama `draft_id` UNIQUE + WP `nakama_draft_id` post meta）
- SEOPress 三層 fallback（REST → post meta → skip + Critical alert）
- LiteSpeed cache purge（Slice C2 Day 1 實測後定 endpoint）
- 合規詞彙 Gate（`shared.compliance.scan`，Brook + Usopp 雙次防禦）

## 執行

```bash
# VPS：systemd 拉起（部署時再加 unit file）
python -m agents.usopp
```

## 環境變數

| Env | Default | 用途 |
|---|---|---|
| `WP_SHOSHO_BASE_URL` / `WP_SHOSHO_USERNAME` / `WP_SHOSHO_APP_PASSWORD` | — | WordPress REST v2 憑證（`nakama_publisher` role app password） |
| `USOPP_TARGET_SITE` | `wp_shosho` | `WordPressClient.from_env` prefix（`wp_shosho` / `wp_fleet`） |
| `USOPP_WORKER_ID` | `usopp-<hostname>` | claim 時寫入 `approval_queue.worker_id` |
| `USOPP_POLL_INTERVAL_S` | `30` | 每 cycle sleep 秒數（interruptible by SIGTERM） |
| `USOPP_BATCH_SIZE` | `5` | 單次 `claim_approved_drafts` 批量 |
| `LITESPEED_PURGE_METHOD` | — | Day 1 決定後設；Slice C2 定稿 |

## 不做的事（Phase 1 scope）

- ❌ `UpdateWpPostV1` — daemon 收到直接 `mark_failed`（Phase 2 再加 update flow）
- ❌ FluentCRM newsletter / FluentCommunity 貼文（Phase 2–3）
- ❌ 多 worker 併發（Phase 1 單 worker + SQLite advisory lock）
- ❌ 自動建 WP category / tag
- ❌ `/healthz` 加 WP 連線檢查 — ADR-007 `agents/franky/health_check.probe_wp_site` out-of-band cron 已 cover（ADR-005b line 417 superseded by ADR-007 §4）

## 相關文件

- [ADR-005b](../../docs/decisions/ADR-005b-usopp-wp-publishing.md) — 正典設計
- [ADR-006](../../docs/decisions/ADR-006-approval-queue.md) — approval_queue FSM
- [docs/runbooks/litespeed-purge.md](../../docs/runbooks/litespeed-purge.md) — LiteSpeed purge 決策（Slice C2 定稿）
- [docs/runbooks/rotate-wp-app-password.md](../../docs/runbooks/rotate-wp-app-password.md) — 憑證輪替
- [docs/runbooks/wp-nakama-publisher-role.md](../../docs/runbooks/wp-nakama-publisher-role.md) — WP 自訂角色白名單
