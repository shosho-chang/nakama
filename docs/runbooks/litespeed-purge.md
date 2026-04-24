# Runbook — LiteSpeed Cache Purge Day 1 研究

**目的**：決定 Usopp 發布後如何觸發 LiteSpeed cache 失效。ADR-005b §5 的硬規則：publish 成功後顯式呼叫 purge，**不依賴 plugin 自動偵測**。

**目前狀態**：**✅ 已決策 2026-04-24**。`LITESPEED_PURGE_METHOD=noop` 為生產正解（不是 fallback）— LiteSpeed plugin 透過 WP `save_post` hook 自動 invalidate cache，Usopp 的 WP REST API 寫入路徑天然觸發，不需要 explicit purge call。方案 A REST endpoint 根本不存在（404 `rest_no_route`）。詳見下方「Day 1 決策紀錄」。

**後續**：見本檔「後續 code follow-up」段，`shared/litespeed_purge.py` 預設值 + ADR-005b §5 需要 code PR 跟進。

**預估時間**：30–60 分鐘（含前置 + 三方案逐一測試 + 套用）

---

## Day 1 執行 checklist（2026-04-25+ 第一次發布日）

**前提**：一篇**非關鍵的測試 post**，內容可以隨便改、hard-refresh 觀察是否立即反映。

### 步驟 0 — 前置確認（5 分鐘）

- [ ] **Usopp daemon 在 VPS 跑中**：`ssh nakama-vps 'systemctl is-active nakama-usopp.service'` → `active`
- [ ] **VPS .env 目前值**：`ssh nakama-vps 'grep LITESPEED_PURGE_METHOD /home/nakama/.env'` → `LITESPEED_PURGE_METHOD=noop`（若非 noop 記下原值以便回滾）
- [ ] **測試 post 存在且可發布**：挑一個 draft post，記下 slug（後續測試用 `https://shosho.tw/<slug>/`）
- [ ] **WP user `bot_usopp` 有 `litespeed_manage_options` capability**：見 [docs/runbooks/wp-nakama-publisher-role.md](wp-nakama-publisher-role.md)；若沒有，方案 A REST 一定炸 WPAuthError
- [ ] **LiteSpeed plugin 啟用中**：WP admin → Plugins → LiteSpeed Cache 是 Active
- [ ] **開 WP admin → LiteSpeed Cache → Toolbox → Purge** 頁面（手動 purge 按鈕位置）— 萬一三方案都炸，這是 fallback 觀察點

### 步驟 1 — 方案 A（REST）實測（10 分鐘）

從**本機**直接打 REST endpoint（還沒動 VPS）：

```bash
# 從 .env 拿 bot_usopp 密碼（別 echo 到對話框或歷史）
WP_PASS=$(grep '^WP_SHOSHO_APP_PASSWORD=' .env | cut -d= -f2-)

curl -s -o /tmp/ls-purge.json -w "HTTP %{http_code}\n" \
  -X POST https://shosho.tw/wp-json/litespeed/v1/purge \
  -u "bot_usopp:$WP_PASS" \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://shosho.tw/<測試 slug>/"}'

cat /tmp/ls-purge.json
```

- [ ] HTTP 200？
- [ ] Body 結構（貼到下方決策表）：_待填_
- [ ] WP admin → LiteSpeed Cache → Reports 看到 purge log？
- [ ] 驗證：把測試 post 內容小改一個字 → 手動打上面 curl → `curl -sI https://shosho.tw/<slug>/` 看 `X-LiteSpeed-Cache: miss` vs `hit` 切換

**若 A 通過 → 直接跳「步驟 4 套用」（不用測 B/C）。**

### 步驟 2 — 方案 B（admin-ajax）實測（10 分鐘，僅 A 失敗時）

`admin-ajax` 需要 wp-admin session nonce。從 Python headless 拿 nonce 複雜度高（要模擬登入 + scrape HTML）。**Phase 1 Slice B 不實作**，這步只驗「未來要不要投資」：

- [ ] 開 browser 登入 WP admin → F12 → 在任一 admin page 跑 `console.log(wpApiSettings?.nonce)` 或看 LiteSpeed 頁面 HTML 的 `_wpnonce` 值
- [ ] 能拿到 nonce 代表路徑存在，但**不代表 Python 能拿到**
- [ ] 結論記到下方決策表即可；**不實作**

### 步驟 3 — 方案 C（WP-CLI SSH）實測（5 分鐘，僅 A/B 都失敗時）

```bash
ssh nakama-vps 'which wp && wp --path=/var/www/shosho.tw --allow-root litespeed-purge url https://shosho.tw/<slug>/'
```

- [ ] `wp` CLI 有裝？
- [ ] `litespeed-purge` subcommand 可用？（LiteSpeed WP-CLI plugin）
- [ ] **若能用但需要 `--allow-root`** → Python 從 agent 殼呼叫 SSH 會權限放大，**不建議**。此路作廢，走 noop。

### 步驟 4 — 套用決策到 VPS（5 分鐘）

依步驟 1–3 結果，填下方「Day 1 決策紀錄」表格，然後：

```bash
# 1. 備份 .env
ssh nakama-vps 'cp /home/nakama/.env /home/nakama/.env.bak.$(date +%Y%m%d_%H%M%S)'

# 2. in-place 改值（不要 scp 整份覆蓋，會殺掉 VPS-only keys）
ssh nakama-vps 'sed -i "s/^LITESPEED_PURGE_METHOD=.*/LITESPEED_PURGE_METHOD=<rest|noop>/" /home/nakama/.env'

# 3. 驗證寫進去了
ssh nakama-vps 'grep LITESPEED_PURGE_METHOD /home/nakama/.env'

# 4. restart daemon 讓新 env 生效
ssh nakama-vps 'systemctl restart nakama-usopp.service && systemctl status nakama-usopp.service --no-pager | head -10'
```

- [ ] `active (running)` 確認
- [ ] Graceful restart（看 journal 有 `SIGTERM received` + 新 PID）：`ssh nakama-vps 'journalctl -u nakama-usopp.service -n 20 --no-pager'`

### 步驟 5 — 端到端驗證（10 分鐘）

透過正式 publish 路徑驗（不是手動 curl）：

- [ ] 用 Usopp 正式流程 enqueue 一篇新 post（或 update 測試 post）
- [ ] daemon 處理完後，`ssh nakama-vps 'journalctl -u nakama-usopp.service -n 50 --no-pager | grep -i litespeed'` 看到 `litespeed purge ok url=...`（若 method=rest）或 `litespeed purge skipped (method=noop)`
- [ ] 瀏覽器訪問文章 URL hard-refresh（Cmd+Shift+R / Ctrl+F5），內容即時更新
- [ ] `curl -sI https://shosho.tw/<slug>/ | grep -i x-litespeed-cache` 看 cache header 狀態

### 步驟 6 — 收尾

- [ ] 更新本檔 §「Day 1 決策紀錄」表
- [ ] 更新 `memory/claude/project_usopp_vps_deployed.md` 的 Unblock 清單（勾 C2b）
- [ ] 更新 `memory/claude/project_pending_tasks.md` 的 Phase 1 Wave 2 C2b 行
- [ ] 若選 `noop`：在 `.env.example` 的 `LITESPEED_PURGE_METHOD` 註解補「noop is production default until Phase 2 revisits」
- [ ] 若選 `rest`：補 runbook「如果 bot_usopp 失去 litespeed_manage_options 會怎樣」的 alert 線索（journal grep `litespeed purge auth failure`）

---

## 待實測三方案

### 方案 A — REST endpoint（Python 首選）

```bash
curl -X POST https://shosho.tw/wp-json/litespeed/v1/purge \
  -u bot_usopp:<app_password> \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://shosho.tw/<任一已發表文章 slug>/"}'
```

- [ ] HTTP 200？
- [ ] 回 body 結構（貼出來）：
- [ ] LiteSpeed plugin 有看到 purge 動作？（WP admin → LiteSpeed Cache → Reports）

若成功 → `.env` 設 `LITESPEED_PURGE_METHOD=rest`，本 runbook 結案。

### 方案 B — admin-ajax + nonce

```
POST https://shosho.tw/wp-admin/admin-ajax.php
  action=litespeed_purge
  _wpnonce=<從 wp-admin page 拿到的 one-time token>
Body: purge_type=url, url=<permalink>
```

- [ ] 從 /wp-admin 首頁拿到的 nonce 能否從 Python headless 拿到？（通常不行，要登入 session）
- [ ] 若能：貼出來，並記錄 nonce 如何 refresh

若成功 → Phase 2 task：為此寫 nonce fetcher + session plumbing。Slice B **不實作**。

### 方案 C — WP-CLI via SSH

```bash
ssh nakama-vps
wp --path=/var/www/shosho.tw litespeed-purge url https://shosho.tw/<slug>/
```

- [ ] WP-CLI 有裝？（`which wp` on VPS）
- [ ] LiteSpeed WP-CLI plugin 有啟用？
- [ ] Python 從 agent 殼呼叫 SSH 是否合理？（不建議——權限放大）

若方案 A/B 都失敗才考慮。建議 Phase 2 自架 purge service 取代。

---

## Day 1 決策紀錄

**測試日期**：2026-04-24
**測試人**：修修 + Claude（桌機）
**測試 post**：id=9930 `day-1-litespeed-test`（測完 trash）

| 方案 | 結果 | 說明 |
|---|---|---|
| A REST | ❌ **endpoint 不存在** | `POST /wp-json/litespeed/v1/purge` 回 HTTP 404 `rest_no_route`。列 `/wp-json/litespeed/v1` namespace 只有 `toggle_crawler_state`；`/wp-json/litespeed/v3` 是 QUIC.cloud / CDN 管理（`cdn_status`/`ping`/`ip_validate`/`err_domains`/`wp_rest_echo`）— **沒有 purge endpoint**。runbook 原本寫的 `litespeed/v1/purge` 是虛構的，`shared/litespeed_purge.py` 的 rest method 實際一直在打 404（mocked tests 看不到）。 |
| B admin-ajax | ⏭️ 跳過 | Phase 1 本來就不實作；下方 WP hook auto-purge 已覆蓋 update flow，admin-ajax 不再需要。 |
| C WP-CLI SSH | ⏭️ 跳過 | `wp` 在 VPS 裝了（`/usr/local/bin/wp`）、site 在 `/var/www/shosho.tw`，但下方 WP hook auto-purge 已覆蓋，不需要走 SSH 權限放大路徑。 |
| **D（意外發現）**：**WP hook auto-purge** | ✅ **天然生效** | LiteSpeed plugin hook 到 `save_post` 等 WP action，透過 WP REST API 的 `POST /wp/v2/posts/<id>` 更新文章時，plugin **自動 invalidate 該 URL + 相關 archive/homepage/feed**。實測：update 前 `x-litespeed-cache: hit` → update 後立即 `miss` → 2s 後 `hit`（re-populated）。Usopp `create_post` / `update_post` 皆走 WP REST，**無需 explicit purge call**。 |

**選擇**：`noop`（production-correct，不是 fallback）

**理由**：WP `save_post` hook 已自動處理 cache invalidation，所有 purge method 都是多餘。VPS `.env` 2026-04-24 部署時 append 的 `LITESPEED_PURGE_METHOD=noop` 已是最終生產值。

**VPS `.env` 狀態**：
```
LITESPEED_PURGE_METHOD=noop    # 維持不變；已驗證為正確生產值
```

---

## 後續 code follow-up（非本次 scope）

基於 Day 1 發現，`shared/litespeed_purge.py` 有兩個值得清理的點（開新 PR 處理）：

1. **預設值改 `noop`**（[shared/litespeed_purge.py:50](../../shared/litespeed_purge.py#L50)）— 現值 `"rest"` 會讓沒設 env 的環境打到不存在的 endpoint 拿 404，造成 journal 充斥無用 warning。
2. **方案 A REST method 可刪**（[shared/litespeed_purge.py:104-146](../../shared/litespeed_purge.py#L104-L146)）— endpoint 不存在，`_purge_via_rest()` 死程式碼。同時更新 docstring 反映「noop 是生產預設，因 WP hook 已處理」。
3. **ADR-005b §5 更新**（[ADR-005b-usopp-wp-publishing.md](../decisions/ADR-005b-usopp-wp-publishing.md)）— 原本說「publish 成功後顯式呼叫 purge，不依賴 plugin 自動偵測」的硬規則要放寬：WP save_post hook 是 LiteSpeed plugin 的**內建**行為（不是「偵測」），可信度等同 plugin 本身；explicit purge 只在「繞過 WP REST 的寫入路徑」才需要（我們目前沒有這類路徑）。

---

## 相關

- 程式碼：[shared/litespeed_purge.py](../../shared/litespeed_purge.py)
- ADR：[ADR-005b §5](../decisions/ADR-005b-usopp-wp-publishing.md)
- Memory 更新：[project_usopp_vps_deployed.md](../../memory/claude/project_usopp_vps_deployed.md)（Unblock 清單勾 C2b）
- Phase 2 優化：若未來有非 WP-REST 的寫入路徑（e.g. 直接改 DB / wp-cli batch 腳本），才需要補 admin-ajax 或 wp-cli purge
