# 2026-04-29 — 加 `nakama-wordpress-client/1.0` Cloudflare WAF skip rule（修修手動）

**Owner**: 修修（CF dashboard 需要 owner 權限）
**Trigger**: SEO 中控台 v1 end-to-end QA — `/bridge/seo` Section 1「沒有抓到文章」根因
**預估時間**: 5 分鐘

---

## 為什麼

`shared/wordpress_client.py` 從 VPS（202.182.107.202 Vultr 香港）打 `https://shosho.tw/wp-json/...`，預設 httpx UA `python-httpx/x.y` 被 Cloudflare Bot Fight Mode 擋全 403 + `<title>Just a moment...</title>` interstitial HTML。client 端誤判為 WP auth 403，graceful degrade 成空 list。

實際 log 證據（2026-04-29 15:12 從 VPS journalctl 抓）：

```
WARNING wp_post_lister fetch_failed target_site=wp_shosho
err=WP auth error 403 on GET wp/v2/posts:
<!DOCTYPE html><title>Just a moment...</title>
```

PR #252 在 `WordPressClient._headers()` 加了穩定 UA `nakama-wordpress-client/1.0`。CF 加一條 skip rule 為這個 UA 放行 `/wp-json/*` 後，VPS → WP REST 才能通。

跟 PR #115 `nakama-external-probe/1.0` + PR #200 `NakamaBot/1.0` 是同 pattern。

---

## 步驟

### 1. 開 Cloudflare dashboard

→ https://dash.cloudflare.com/

選 **shosho.tw** zone（fleet.shosho.tw 是同 zone subdomain，rule 在 zone level 設一次涵蓋兩 site）

### 2. 進 Custom Rules

左側 **Security → WAF → Custom Rules**（不是 Managed Rules）

### 3. 點「Create rule」

填這些：

| 欄位 | 值 |
|---|---|
| **Rule name** | `Skip SBFM for WordPressClient agent` |
| **If incoming requests match** | Field: `User Agent` · Operator: `equals` · Value: `nakama-wordpress-client/1.0`<br>**AND** Field: `URI Path` · Operator: `starts with` · Value: `/wp-json/`<br>**AND** Field: `IP Source Address` · Operator: `equals` · Value: `202.182.107.202`（VPS IP — 三條 AND 收緊濫用面） |
| **Then take action** | `Skip` |
| **Skip the remaining custom rules** | ☑ 勾 |
| **Then continue evaluation against** | ☑ Super Bot Fight Mode（zone 啟用 BFM 也勾） |
| **Order / Priority** | 拉到所有 challenge rule 之前（**Top**） |

### 4. Deploy

點 Save。約 30 秒生效。

---

## 驗收（你做完 ssh 跑這條，回我結果）

從 VPS ssh 跑：

```bash
ssh nakama-vps "curl -A 'nakama-wordpress-client/1.0' \
  -u \"\$WP_SHOSHO_USERNAME:\$WP_SHOSHO_APP_PASSWORD\" \
  -s -o /dev/null -w '%{http_code}\n' \
  https://shosho.tw/wp-json/wp/v2/posts?per_page=1"
```

| 結果 | 意義 |
|---|---|
| `200` ✅ | rule 生效 — 跟我說一聲，PR #252 squash merge + VPS deploy |
| `403` ❌ | rule 沒生效 — 可能 UA value 拼錯、order 不對、IP 條件對不上、或還沒 propagate（等 1 分鐘再試） |

---

## PR #252 merge 後 VPS deploy

```bash
ssh nakama-vps
cd /home/nakama && git pull
systemctl restart thousand-sunny nakama-usopp
```

重啟前 service 跑舊版 wordpress_client.py（無 UA），CF rule 即使設好也接不到流量。

---

## 跑完之後驗證

瀏覽器 hard reload `https://nakama.shosho.tw/bridge/seo` → Section 1「文章列表」應該出 shosho.tw + fleet.shosho.tw 文章。

VPS log 看 `cache_miss count>0`：

```bash
ssh nakama-vps "journalctl -u thousand-sunny --since '2 min ago' | grep wp_post_lister"
```

預期看到：

```
INFO  wp_post_lister cache_miss target_site=wp_shosho count=N
INFO  wp_post_lister cache_miss target_site=wp_fleet count=M
```
