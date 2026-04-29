---
name: CF Bot Fight Mode 偽裝成 HTTP 403 auth fail
description: client 看 HTTP 403 + body 是 HTML（含 `<title>Just a moment...</title>`）= CF SBFM challenge 不是真 auth；datacenter IP 必帶穩定 UA + CF zone skip rule
type: feedback
originSessionId: ae6ace4c-6a20-4ab2-bc01-ec47c0980825
---
**規則：client 收到 HTTP 403 但 response body 是 HTML（特別是含 `<title>Just a moment...</title>` 或 `cf-mitigated` / `cf-chl-bypass` CSS class）= **Cloudflare Bot Fight Mode / Super Bot Fight Mode challenge**，不是真的 auth/權限失敗。**

**Why:** PR #252 抓到的 SEO 中控台 `wp_post_lister fetch_failed` 根因 — `WordPressClient` 從 VPS（202.182.107.202 Vultr datacenter IP）打 `https://shosho.tw/wp-json/wp/v2/posts` 走 httpx default UA `python-httpx/x.y`，CF SBFM 把 datacenter IP 視為 bot，回 HTTP 403 + 5KB HTML interstitial。`WordPressClient._raise_for_status()` 把任何 4xx 包 `WPAuthError`，error message 還夾 HTML 開頭片段「<!DOCTYPE html><title>Just a moment...</title>」。`/bridge/seo` Section 1 顯示「共 0 篇」+ log 寫 `WP auth error 403` — 字面誤導，浪費 30 分鐘 diagnose。

**真實 status code 機制**：CF challenge 故意回 403 而非 401/418/429，因為 403 「最像」站點主動拒絕 bot；client 不會 retry（避免 amplify），也不會 prompt for credentials（避免 user 重複輸入）。HTML body 是給 browser challenge UI；非瀏覽器 client 看到 HTML 而非 JSON 就該知道是 CF。

**How to apply:**

1. **Diagnose 訊號**：API client 收 4xx + body 起頭是 `<!DOCTYPE html` 或 `<html` → **直覺直接懷疑 CF interstitial**，不是 auth/route 問題。grep response body 含 `Just a moment` / `cf-mitigated` / `cf-chl-bypass` / `Ray ID` 直接確診。
2. **預防 — 凡 datacenter IP outbound 打 CF zone 都需要**：
   - client 帶**穩定 self-issue UA**（`nakama-{component}/1.0` 系列）
   - CF zone WAF Custom Rule：`UA equals X` AND `URI starts with Y` AND `IP equals Z` → Skip Bot Fight Mode + Super Bot Fight Mode
   - 詳見 [docs/runbooks/cf-waf-skip-rules.md](../../docs/runbooks/cf-waf-skip-rules.md) 表格 + per-rule 日期前綴 task doc
3. **既有同坑教訓**：PR #115 nakama-external-probe（GH Actions）+ PR #200 NakamaBot（seo-audit-post fetcher）+ PR #252 nakama-wordpress-client。下個踩同坑的多半是 Robin/Brook/Franky 加 self-fetch path — 加 deps 前先檢查 outbound destination 是不是 CF zone。

**對齊既有 memory**：[feedback_uptimerobot_cost_benefit.md](feedback_uptimerobot_cost_benefit.md) 講過 SBFM 擋 GH runner，本條補的是 **client-side diagnostic 訊號**（看 status code + body shape 直接判斷）+ datacenter IP 必補 UA whitelist 的工程慣例。
