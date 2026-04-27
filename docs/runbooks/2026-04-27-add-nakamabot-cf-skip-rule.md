# 2026-04-27 — 加 `NakamaBot/1.0` Cloudflare WAF skip rule（修修手動）

**Owner**: 修修（CF dashboard 需要 owner 權限）
**Trigger**: SEO Phase 1.5 Acceptance follow-up F5-B
**預估時間**: 5 分鐘

---

## 為什麼

VPS（202.182.107.202 Vultr 香港 datacenter IP）打 `https://shosho.tw/` 會被 Cloudflare Super Bot Fight Mode（SBFM）擋全 403，**無論 User-Agent 設什麼**——CF 標 datacenter IP 為 bot risk。

`seo-audit-post` skill 的 default fetcher 從 VPS 跑會 403 → audit 早收 → fake grade=D。

PR #200 加了 `--via-firecrawl` flag 為 fallback（每次 +1 firecrawl credit），但更乾淨的解法是 CF 加一條 skip rule：**只要 UA 含 `NakamaBot/1.0` 就跳過 SBFM**——讓 default fetcher 從 VPS 直接通，不燒 firecrawl credit。

跟 PR #115 既有 `nakama-external-probe/1.0` whitelist 是同 pattern。

---

## 步驟

### 1. 開 Cloudflare dashboard

→ https://dash.cloudflare.com/

選 **shosho.tw** zone（nakama.shosho.tw 跟 fleet.shosho.tw 都掛同 zone）

### 2. 進 Custom Rules

左側 **Security → WAF → Custom Rules**（不是 Managed Rules）

### 3. 點「Create rule」

填這些：

| 欄位 | 值 |
|---|---|
| **Rule name** | `Skip SBFM for NakamaBot agent` |
| **If incoming requests match** | Field: `User Agent`<br>Operator: `contains`<br>Value: `NakamaBot/1.0` |
| **Then take action** | `Skip` |
| **Skip the remaining custom rules** | ☑ 勾 |
| **Then continue evaluation against** | ☑ Super Bot Fight Mode |
| **Order / Priority** | 拉到所有 challenge rule 之前 |

### 4. Deploy

點 Save。約 30 秒生效。

---

## 驗收（你做完 ssh 跑這條，回我結果）

從 VPS ssh 跑：

```bash
ssh nakama-vps "curl -A 'Mozilla/5.0 (compatible; NakamaBot/1.0; +https://shosho.tw/about) seo-audit/1.0' -s -o /dev/null -w '%{http_code}\n' https://shosho.tw/blog/zone-2-common-questions/"
```

| 結果 | 意義 |
|---|---|
| `200` ✅ | rule 生效 — 跟我說一聲，我從 VPS 跑 audit 驗真實 grade |
| `403` ❌ | rule 沒生效 — 可能 UA value 拼錯、order 不對、或還沒 propagate（等 1 分鐘再試） |

---

## 為什麼是 UA whitelist 不是 IP whitelist

- Vultr 可能換 VPS IP（incident / migration），UA 跟 deploy 綁定比較穩
- UA 由我們 self-issue（`shared/seo_audit/html_fetcher.py:_USER_AGENT`），抗 third-party 偽造能力夠（這只是擋 CF SBFM 不是擋惡意 bot）
- 跟 PR #115 既有 `nakama-external-probe/1.0` 走同 pattern，未來 Robin/Brook 加 self-fetch 也照這個套路

---

## 跑完之後

我會：
1. 從 VPS 直接跑 `python3 .claude/skills/seo-audit-post/scripts/audit.py --url https://shosho.tw/blog/zone-2-common-questions/` **不加** `--via-firecrawl`
2. 看新 grade（之前 firecrawl 抓的 grade=D 是因為 firecrawl `formats=html` 沒回 head metadata，這次 default httpx 抓 raw HTML 應該升到 A/B）
3. 把實測 grade 寫進 `docs/plans/2026-04-27-seo-phase15-acceptance-results.md`（更新原本 follow-up 段）
4. PR #200 squash merge

如果 audit 抓回來真的有完整 head meta + grade 升上去 → SEO Phase 1.5 真正 100% 完成。
