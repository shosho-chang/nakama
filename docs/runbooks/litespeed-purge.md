# Runbook — LiteSpeed Cache Purge Day 1 研究

**目的**：決定 Usopp 發布後如何觸發 LiteSpeed cache 失效。ADR-005b §5 的硬規則：publish 成功後顯式呼叫 purge，**不依賴 plugin 自動偵測**。

**目前狀態**：**✅ 已決策 2026-04-24**。`LITESPEED_PURGE_METHOD=noop` 為生產正解（不是 fallback）— LiteSpeed plugin 透過 WP `save_post` hook 自動 invalidate cache，Usopp 的 WP REST API 寫入路徑天然觸發，不需要 explicit purge call。方案 A REST endpoint 根本不存在（404 `rest_no_route`）。詳見下方「Day 1 決策紀錄」。

**後續**：見本檔「後續 code follow-up」段，`shared/litespeed_purge.py` 預設值 + ADR-005b §5 需要 code PR 跟進。

**預估時間**：30–60 分鐘（含前置 + 三方案逐一測試 + 套用）

---

## Day 1 實測紀錄（2026-04-24 retrospective）

**執行人**：修修 + Claude（桌機）
**測試 post**：id=9930 `day-1-litespeed-test`（測完立即 trash）
**本節是當天實際跑了什麼 + 觀察到什麼**；對應結論見下方「Day 1 決策紀錄」表。若未來 cache 行為出狀況要重新 debug，此段可當 playbook template（把「觀察到」那欄的結果當成「預期」，出現 diff 就往不同方向查）。

### 步驟 0 — 前置確認（5 分鐘）

cwd = `f:\nakama`（桌機 repo 根目錄；所有 `grep .env` 指本機；`ssh nakama-vps` 跳 VPS）

- ✅ **Usopp daemon active**：`ssh nakama-vps 'systemctl is-active nakama-usopp.service'` → `active` since 2026-04-24 13:55
- ✅ **VPS `.env` 現值**：`LITESPEED_PURGE_METHOD=noop`（部署時 append 的過渡值）
- ⚠️ **WP user `bot_usopp` role = `editor`**（不是 `nakama_publisher`），**無 `manage_options` 也無任何 `litespeed_*` capability**。這預告方案 A 會 403 — 但實際跑出更本質的 404（見步驟 1）。
- ✅ **LiteSpeed plugin 啟用中**（從後續出現 `x-litespeed-cache` header 間接確認）

### 步驟 1 — 方案 A（REST）實測 → ❌ 404 `rest_no_route`

從桌機（cwd = `f:\nakama`）打 REST endpoint：

```bash
WP_PASS=$(grep '^WP_SHOSHO_APP_PASSWORD=' .env | cut -d= -f2-)
WP_USER=$(grep '^WP_SHOSHO_USERNAME=' .env | cut -d= -f2-)
WP_BASE=$(grep '^WP_SHOSHO_BASE_URL=' .env | cut -d= -f2-)

curl -s -o /tmp/ls-purge.json -w "HTTP %{http_code}\n" \
  -X POST "$WP_BASE/wp-json/litespeed/v1/purge" \
  -u "$WP_USER:$WP_PASS" \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://shosho.tw/blog/day-1-litespeed-test/"}'
```

**結果**：HTTP 404，body `{"code":"rest_no_route","message":"找不到與網址及要求方法相符的路由。"}`

列 plugin 暴露的 namespace 確認：

```bash
curl -s -u "$WP_USER:$WP_PASS" "$WP_BASE/wp-json" | jq -r '.namespaces | sort[]'
# → 出現 litespeed/v1 + litespeed/v3；均**無** purge route

curl -s -u "$WP_USER:$WP_PASS" "$WP_BASE/wp-json/litespeed/v3" | jq -r '.routes | keys[]'
# → cdn_status, err_domains, ip_validate, ping, wp_rest_echo（全是 QUIC.cloud CDN 管理，不是 purge）
```

**結論**：`POST /wp-json/litespeed/v1/purge` **endpoint 根本不存在**。原 runbook 寫的 URL 是虛構的，`shared/litespeed_purge.py` 的 rest method 一直在打 404（mocked tests 看不到）。

### 步驟 2 — 方案 B（admin-ajax）⏭️ 跳過

原計畫在方案 A 失敗後評估 browser nonce 路徑。跳過理由：下方「意外發現（方案 D）」已覆蓋所有 purge 時機；admin-ajax 只剩理論價值。

### 步驟 3 — 方案 C（WP-CLI SSH）⏭️ 快速確認存在性

```bash
ssh nakama-vps 'which wp && ls -la /var/www/shosho.tw 2>&1 | head -3'
# → /usr/local/bin/wp 存在；site 在 /var/www/shosho.tw（owner: u1_shosho_tw）
```

**結論**：`wp` CLI 有裝。但目錄 owner 不是 `nakama`，要 `sudo -u u1_shosho_tw wp ...` 或 `wp --allow-root`，都屬權限放大。Daemon 從 Python 殼 `ssh` 到 VPS 再跑 wp-cli 是 anti-pattern。**跳過**。

### 步驟 4 — 意外發現：WP `save_post` hook auto-purge（方案 D）

測試 cache 生命週期：Prime（瀏覽器 UA）→ update post → 觀察 header 變化

```bash
URL="https://shosho.tw/blog/day-1-litespeed-test/"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128.0.0.0"

# Prime cache
for i in 1 2 3 4; do curl -sI "$URL" -H "User-Agent: $UA" | grep -i "x-litespeed-cache:"; done
# → hit, hit, hit, hit ✅

# Update post via WP REST
curl -s -u "$WP_USER:$WP_PASS" -X POST "$WP_BASE/wp-json/wp/v2/posts/9930" \
  -H 'Content-Type: application/json' \
  -d '{"content":"<p>Updated at 2026-04-24T18:30+08:00</p>'}
# → HTTP 200

# 立即再 probe cache
curl -sI "$URL" -H "User-Agent: $UA" | grep -i "x-litespeed-cache:"
# → miss（cache invalidated）

# 2 秒後
sleep 2; curl -sI "$URL" -H "User-Agent: $UA" | grep -i "x-litespeed-cache:"
# → hit（cache re-populated）
```

**結論**：LiteSpeed plugin hook 到 WP `save_post`，**WP REST API 的寫入路徑天然觸發 auto-invalidate + auto re-populate**。explicit purge call 完全不需要。Usopp 的 `create_post` / `update_post` 都走 WP REST，因此 `LITESPEED_PURGE_METHOD=noop` 是生產正解而非 fallback。

**觀察 tip**：LiteSpeed 的 `x-litespeed-cache` header 只在「看起來像 browser 的 UA + `Accept: text/html`」請求才出現；裸 curl（預設 UA）會讓 LiteSpeed bypass cache 完全不設此 header。debug 時記得帶 realistic UA。

### 步驟 5 — 套用結論：VPS `.env` 維持不變

```bash
ssh nakama-vps 'grep LITESPEED_PURGE_METHOD /home/nakama/.env'
# → LITESPEED_PURGE_METHOD=noop（維持不變；此為生產正解）
```

無須 daemon restart。

### 步驟 6 — 收尾（已完成）

- ✅ 本檔「Day 1 決策紀錄」表填完
- ✅ `memory/claude/project_usopp_vps_deployed.md` Unblock 清單勾 C2b
- ✅ `memory/claude/project_pending_tasks.md` Phase 1 Wave 2 C2b 行勾 ✅
- ✅ `.env.example` `LITESPEED_PURGE_METHOD` 註解改述「只接受 noop」（見 PR #112）
- ✅ code follow-up 開 PR #112：`shared/litespeed_purge.py` 刪死 code + ADR-005b §5 放寬硬規則

測試 post `id=9930` 已 `DELETE /wp/v2/posts/9930` → trash。

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

## 後續 code follow-up

基於 Day 1 發現，`shared/litespeed_purge.py` 的清理已在 **PR #112（`chore/litespeed-purge-cleanup`，open）** 處理：

1. `shared/litespeed_purge.py` 預設 `"rest"` → `"noop"` + 刪 `_purge_via_rest()` + docstring 反映 WP hook 實際機制
2. ADR-005b §5 放寬硬規則 — 原「publish 成功後顯式呼叫 purge」作廢；`save_post` 是 WP core hook 而非 LiteSpeed 自偵測行為，可信度等同 WP 本身；explicit purge 僅對「繞過 WP REST 的寫入路徑」適用（目前無）
3. 受影響 schema / state machine 皆不動；`cache_purged=False` 語意從「失敗/未嘗試」改為「WP hook 已處理」

建議 merge 順序：本 PR（#110）先 merge → PR #112 其次，讓 ADR §5 修訂版引用的 runbook 決策紀錄就位。

---

## 相關

- 程式碼：[shared/litespeed_purge.py](../../shared/litespeed_purge.py)
- ADR：[ADR-005b §5](../decisions/ADR-005b-usopp-wp-publishing.md)
- Memory 更新：[project_usopp_vps_deployed.md](../../memory/claude/project_usopp_vps_deployed.md)（Unblock 清單勾 C2b）
- Phase 2 優化：若未來有非 WP-REST 的寫入路徑（e.g. 直接改 DB / wp-cli batch 腳本），才需要補 admin-ajax 或 wp-cli purge
