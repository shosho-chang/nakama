# Cloudflare WAF / SBFM skip rules — for nakama agents

**Owner**: 修修（手動透過 CF dashboard）
**用途**: 讓 nakama agents 從 VPS / GH Actions IP 出去打 shosho.tw / fleet.shosho.tw / nakama.shosho.tw 不被 CF SBFM 擋 403。

---

## 為什麼需要

Cloudflare Super Bot Fight Mode（SBFM）會擋 datacenter / VPS IP，無論 User-Agent。我們的 agents 跑在：

- **VPS**（202.182.107.202 Vultr 香港）— 跑 Robin / Franky / Usopp / SEO audit / external probe
- **GH Actions runners**（Azure datacenter IP，動態）— 跑 external uptime probe / scheduled tests

這些 IP 全被 CF 標 datacenter，default 就擋。每個 agent 用自己的 User-Agent，CF dashboard 加 「if UA contains X then skip SBFM」rule。

---

## 既有 skip rules（依 PR / agent 對應）

| User-Agent | 用途 | 加入時 PR |
|---|---|---|
| `nakama-external-probe/1.0` | GH Actions external uptime probe | PR #115（2026-04-24） |
| `NakamaBot/1.0` | seo-audit-post `fetch_html`（D.1 加） | **F5-B 2026-04-27 待加** |

加新 agent 要新 UA 時 append 進這張表。

---

## F5-B 2026-04-27 — `NakamaBot/1.0` skip rule（修修做）

### 為什麼

PR #200 加了 `--via-firecrawl` flag 讓 audit 從 VPS 跑時可以繞 CF（每次 +1 firecrawl credit）。但更乾淨的做法是讓 default httpx fetcher 從 VPS 直接通 — 加 CF skip rule by UA。這樣：
- VPS 跑 production audit 不用 firecrawl credit
- `--via-firecrawl` 保留為「caller IP 真的進不來」的最終 fallback

### 步驟

1. 開 [Cloudflare dashboard](https://dash.cloudflare.com/)
2. 選 **shosho.tw** zone（nakama.shosho.tw 跟 fleet.shosho.tw 都掛在這個 zone）
3. 左側 **Security → WAF → Custom Rules**（不是 Managed Rules）
4. 點「Create rule」
5. 設定：
   - **Rule name**: `Skip SBFM for NakamaBot agent`
   - **If incoming requests match**：
     - Field: `User Agent`
     - Operator: `contains`
     - Value: `NakamaBot/1.0`
   - **Then take action**: `Skip`
   - **Skip the remaining custom rules**: 勾
   - **Then continue evaluation against**: 勾「Super Bot Fight Mode」
6. **Order**: 拉到所有 challenge rule 之前（或設 priority 1）
7. **Deploy**

### 驗收

從 VPS ssh：

```bash
ssh nakama-vps "curl -A 'Mozilla/5.0 (compatible; NakamaBot/1.0; +https://shosho.tw/about) seo-audit/1.0' -s -o /dev/null -w '%{http_code}\n' https://shosho.tw/blog/zone-2-common-questions/"
```

預期：`200`（之前是 `403`）

### 跑完後

跟我說「CF rule 加好了」，我會跑 audit 從 VPS 直接打 shosho.tw（不加 `--via-firecrawl`）驗證 grade 真實狀態（之前 grade=D 是因為 firecrawl 抓的 HTML 沒含 `<head>`，CF 擋之後 audit 連 fetch 都 fail）。

---

## 其他可能要加的 skip rule（先記，沒急）

- 未來 Robin pubmed scrape 自家 KB / source 頁時可能需要
- Brook 寫稿 fact-check 走 firecrawl Google Scholar 路徑不影響（firecrawl 從 firecrawl 自家 IP 出去，不過 CF）

---

## 注意事項

- **不要關整個 SBFM** — 還是要擋未知 bot，只開洞給特定 UA
- **不要用 IP whitelist** 取代 UA whitelist — VPS IP 可能換（Vultr 有 incident 換 IP 的可能），UA 跟 deploy 綁定比較穩
- **每加一條 skip rule 進這份 runbook 表格** — 不在表上的 rule 之後沒人記得為什麼存在
