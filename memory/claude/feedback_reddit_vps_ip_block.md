---
name: Reddit 封 VPS datacenter IP（匿名 API 全 403）
description: Reddit 匿名 JSON API 從 Vultr 等 datacenter IP 全回 403，無論 UA 格式怎麼調；RSS 可通但訊號廢一半；走 OAuth 是唯一可靠解
type: feedback
originSessionId: 08f9ecf1-0d35-4311-a34c-34bca66b0731
---
Reddit 匿名 JSON API 從 VPS（Vultr datacenter IP）回 **403 Blocked**，實測涵蓋所有 UA 組合都沒用：
- Reddit 官方建議格式 `<platform>:<appID>:<version> (by /u/<user>)` — 403
- 偽裝 Firefox UA — 403
- 短 UA `nakama/1.0` — 403
- Bare curl 無 UA — 403

**Why**：Reddit anti-bot 對 datacenter IP 段直接封，不是 UA 檢查。Mac 本地（Anthropic 代理端）也 403 — 幾乎任何非住宅 IP 都被封。

**How to apply**：
1. **VPS 做 Reddit 匿名 API call 直接不要寫** — 不是「要調 UA」，是根本不通
2. 只有 **Reddit OAuth（authenticated）** 能繞過 — 要修修到 https://www.reddit.com/prefs/apps 建 Reddit app 取 `client_id + client_secret`，走 `<username>:<password>` password grant 或 script app flow
3. `.rss` endpoint 是唯一匿名還通的路，但 XML 只有 title/link/date，**沒 score / num_comments**，velocity 訊號廢掉 — 不建議當主 source
4. 本地開發（住宅 IP）能跑不代表 VPS 能跑 — 推 VPS 前一定要 `curl -o /dev/null -w "%{http_code}\n"` 實測

同類風險：**所有內容平台的匿名 API 從 VPS 都要先實測 HTTP 碼再寫**（Instagram / Twitter public scrape / Pinterest 都可能同樣 IP block）。Google 系（Trends、YouTube Data API）目前不受影響。

## Zoro scout 的具體 fallout

Slice C 原設計 Reddit hot 為 primary source，deploy VPS 後 scout 每 tick 收 0 signals。Slice C1（PR #107）切 Google Trends trending_now — 從 VPS 跑得通。Reddit OAuth 列 Slice D backlog。
