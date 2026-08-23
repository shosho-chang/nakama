# Fleet Gamification（笨層 plugin）

自由艦隊自研遊戲化系統的 WordPress 端。職責只有三件：**hook 捕捉 → 本地事件表**、
**持有 ledger（append-only）**、**暴露窄 REST API**。所有規則（分數、streak、判定、驚喜）
都在 nakama 端的 Sanji——本 plugin 內出現任何規則邏輯即為違規。

- 設計裁決：`agents/sanji/CONTEXT.md`（nakama repo）
- 營運方案：`docs/plans/fleet-gamification-master-plan.md`
- 版本基準：FluentCommunity 2.7.5 / Pro 2.7.6（hook 驗證日 2026-08-23）

## 檔案地圖

```
fleet-gamification.php        主檔：常數、require、boot 掛載
includes/
  class-plugin.php            boot 序（migrations → roles → REST → capture）
  class-settings.php          gam_enabled 止血開關、space allowlist
  class-migrations.php        版本號驅動 migration runner
  migrations/001-*.php        events / grants / balances 三表
  class-ledger.php            事件與帳本唯一寫入口（INSERT IGNORE 冪等）
  class-capture.php           8 個 vendor hook 監聽（每個 handler 註記 file:line 出處）
  class-rest.php              nakama-gam/v1 端點
  class-fc-bridge.php         對 FluentCommunity 的全部接觸點（probe 的驗證面）
tools/contract-probe.php      vendor 依賴驗證（唯讀，一分鐘）
```

## 資料表

| 表 | 性質 | 說明 |
|---|---|---|
| `{p}nakama_gam_events` | append 流 | 捕捉層事件；`id` 是 Sanji 的 cursor；`dedupe_key` unique 去噪 |
| `{p}nakama_gam_grants` | **帳本（append-only）** | 一筆授予/沖正一列；`idempotency_key` unique 是防重最後防線；永不 UPDATE/DELETE |
| `{p}nakama_gam_balances` | 投影 | 可隨時砍掉由 grants 重算（`Ledger::rebuild_balance`） |

## REST（namespace `nakama-gam/v1`）

認證：HTTPS basic auth（WP Application Password）＋ capability `nakama_gam_api`。
`gam_enabled=0` 時業務端點回 503，只有 `/health` 照常。

| 端點 | 用途 |
|---|---|
| `GET /health` | 煙霧測試：enabled / db_version / fc_available |
| `GET /events?after_id=&limit=&types=` | 事件 cursor 增量拉（id 升冪，max 500/批） |
| `GET /reactions?after_id=&types=bookmark` | vendor reactions 增量掃描（收藏無 hook 的補洞） |
| `GET /feeds/{id}` | 單篇貼文＋媒體（判定用） |
| `POST /comments` `{feed_id, comment}` | 以 sanji 身分留言（站內 dispatch vendor route，原生通知） |
| `POST /grants` `{grants:[...]}` | 批次入帳；回報 created / duplicate / invalid（duplicate=冪等成功） |
| `GET /balances/{id}?rebuild=1` | 投影查詢／由帳本重算 |

## Ops runbook

**服務帳號（一次性設置）**

1. 建 WP user `sanji`（也是社群成員：xprofile active、加入打卡 space——留言權限需要）
2. 指派角色 `Gamification Service`（plugin boot 自動建立；只有 `read` + `nakama_gam_api`，不是 admin）
3. 產 Application Password，填進 nakama 端 `.env`（`GAM_WP_USER` / `GAM_WP_APP_PASSWORD`）

**開關與白名單**

```bash
wp option update nakama_gam_enabled 1          # 開（預設 0；UAT 通過才開）
wp option update nakama_gam_enabled 0          # 一鍵止血
wp eval 'update_option("nakama_gam_space_allowlist", [123]);'   # 打卡 space 白名單
```

**部署後煙霧測試**

```bash
wp eval-file wp-content/plugins/fleet-gamification/tools/contract-probe.php   # 要 GREEN
curl -su 'sanji:APP_PASS' https://fleet.shosho.tw/wp-json/nakama-gam/v1/health
```

**vendor 更新紀律**：FluentCommunity 關閉自動更新。新版釋出 → 對新檔案跑 probe →
綠燈才更新 → 更新後 probe＋煙霧測試再跑一次。紅燈＝先修 Capture/FcBridge 再更新。

## 已知縫隙（原始碼證實，勿「修正」掉對策）

1. **收藏無 hook**：vendor 的 react hooks 被 `if ($type == 'like')` 包住——bookmark 靜默寫入。
   對策＝Sanji 每日 `GET /reactions` 增量掃描補入（承諾「最晚隔日入帳」內）。
2. **`react_removed` 只帶 `$feed`**：不知道誰移除了什麼——即時沖正不可能。
   對策＝事件記 marker，每日對帳 recount。
3. **`wp_login` 不可作每日登入訊號**：持久 session 不重登。
   對策＝portal ticker 的 `track_activity` action（45–75s/次）＋ dedupe_key 一天一次。
