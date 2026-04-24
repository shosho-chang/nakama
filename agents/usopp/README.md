# Usopp — 狙擊手（Publisher Agent）

精準將已審核的 Brook `DraftV1` 發布到 WordPress（shosho.tw / fleet.shosho.tw），含 SEOPress meta、LiteSpeed cache 自動 invalidation（WP `save_post` hook 處理）、台灣藥事法/醫療法詞彙攔截、crash-safe state machine。

**排程：** Daemon poll `approval_queue`（預設每 30 秒），批量 claim + publish
**狀態：** Phase 1 Slice C2b 完成（daemon 上線 VPS、Docker WP staging E2E 黃金路徑可跑、LiteSpeed Day 1 實測 2026-04-24 完成 — `LITESPEED_PURGE_METHOD=noop` 為生產正解）

---

## 能力

詳見 [docs/capabilities/wordpress-publisher.md](../../docs/capabilities/wordpress-publisher.md)。要點：

- Crash-safe 8 階段 state machine（`publish_jobs` 表持久化；重啟續跑）
- 雙層 idempotency（Nakama `draft_id` UNIQUE + WP `nakama_draft_id` post meta）
- SEOPress 三層 fallback（REST → post meta → skip + Critical alert）
- LiteSpeed cache 自動失效（透過 WP `save_post` hook，`shared/litespeed_purge.py` 是 anchor point；Day 1 實測 2026-04-24 確認不需 explicit purge call）
- 合規詞彙 Gate（`shared.compliance.scan`，Brook + Usopp 雙次防禦）

## 執行

```bash
# 本機（dry-run）
python -m agents.usopp

# VPS：systemd 拉起 — 見 docs/runbooks/deploy-usopp-vps.md
systemctl start nakama-usopp
```

## 環境變數

| Env | Default | 用途 |
|---|---|---|
| `WP_SHOSHO_BASE_URL` / `WP_SHOSHO_USERNAME` / `WP_SHOSHO_APP_PASSWORD` | — | WordPress REST v2 憑證（`nakama_publisher` role app password） |
| `USOPP_TARGET_SITE` | `wp_shosho` | `WordPressClient.from_env` prefix（`wp_shosho` / `wp_fleet`） |
| `USOPP_WORKER_ID` | `usopp-<hostname>` | claim 時寫入 `approval_queue.worker_id` |
| `USOPP_POLL_INTERVAL_S` | `30` | 每 cycle sleep 秒數（interruptible by SIGTERM） |
| `USOPP_BATCH_SIZE` | `5` | 單次 `claim_approved_drafts` 批量 |
| `LITESPEED_PURGE_METHOD` | `noop` | 只接受 `noop`；Day 1 實測 2026-04-24 確認 WP save_post hook 已處理 cache invalidation |

## E2E test（本機 Docker WP staging）

Slice C2a 產出的 opt-in local 測試 — 真 WP + SEOPress 9.4.1，狀態機跑完整。
`cache_purged=False` 是生產正解（noop method）— Day 1 實測 2026-04-24 確認 WP `save_post` hook 已自動 invalidate cache，explicit purge call 不需要。

```bash
# 1. 一次性 boot + seed + 產 .env.test
bash tests/fixtures/wp_staging/run.sh

# 2. 載入 creds + 跑測試
set -a && source .env.test && set +a
pytest -m live_wp tests/e2e/

# 3. 收工
docker compose -f tests/fixtures/wp_staging/docker-compose.yml down -v
```

`PYTEST_WP_BASE_URL` 未設時 `live_wp` marker 會自動 skip（不 fail），所以平常的 `pytest` 不會踩到。

詳見 [docs/task-prompts/phase-1-usopp-slice-c2a.md](../../docs/task-prompts/phase-1-usopp-slice-c2a.md)。

## 不做的事（Phase 1 scope）

- ❌ `UpdateWpPostV1` — daemon 收到直接 `mark_failed`（Phase 2 再加 update flow）
- ❌ FluentCRM newsletter / FluentCommunity 貼文（Phase 2–3）
- ❌ 多 worker 併發（Phase 1 單 worker + SQLite advisory lock）
- ❌ 自動建 WP category / tag
- ❌ `/healthz` 加 WP 連線檢查 — ADR-007 `agents/franky/health_check.probe_wp_site` out-of-band cron 已 cover（ADR-005b line 417 superseded by ADR-007 §4）

## 相關文件

- [ADR-005b](../../docs/decisions/ADR-005b-usopp-wp-publishing.md) — 正典設計
- [ADR-006](../../docs/decisions/ADR-006-approval-queue.md) — approval_queue FSM
- [docs/runbooks/deploy-usopp-vps.md](../../docs/runbooks/deploy-usopp-vps.md) — VPS 部署（systemd + `.env` diff）
- [docs/runbooks/litespeed-purge.md](../../docs/runbooks/litespeed-purge.md) — LiteSpeed purge Day 1 決策（2026-04-24 定稿：WP `save_post` hook 處理）
- [docs/runbooks/rotate-wp-app-password.md](../../docs/runbooks/rotate-wp-app-password.md) — 憑證輪替
- [docs/runbooks/wp-nakama-publisher-role.md](../../docs/runbooks/wp-nakama-publisher-role.md) — WP 自訂角色白名單
