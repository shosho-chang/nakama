# Runbook — LiteSpeed Cache Purge Day 1 研究

**目的**：決定 Usopp 發布後如何觸發 LiteSpeed cache 失效。ADR-005b §5 的硬規則：publish 成功後顯式呼叫 purge，**不依賴 plugin 自動偵測**。

**目前狀態**：**未決**。Phase 1 Slice B 的 `shared/litespeed_purge.py` 支援三個 method，env `LITESPEED_PURGE_METHOD` 切換。預設 `rest`，若 Day 1 實測失敗改 `noop` 等 TTL。

**預估時間**：30–60 分鐘（三方案逐一測試）

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

**測試日期**：_待填_
**測試人**：_待填_

| 方案 | 結果 | 說明 |
|---|---|---|
| A REST | _待填_ | |
| B admin-ajax | _待填_ | |
| C WP-CLI SSH | _待填_ | |

**選擇**：_A / B / C / noop_
**理由**：_待填_

**VPS `.env` 更新**：
```
LITESPEED_PURGE_METHOD=<rest|admin_ajax|noop>
```

若三方案全不可行 → 選 `noop`，文章發布後最多延遲 LiteSpeed TTL（預設 600 秒）才呈現最新內容。修修手動 hard-refresh 驗證。

---

## 相關

- 程式碼：[shared/litespeed_purge.py](../../shared/litespeed_purge.py)
- ADR：[ADR-005b §5](../decisions/ADR-005b-usopp-wp-publishing.md)
- Phase 2 優化：cron_runs 追蹤 purge 成功率、自架 purge micro-service
