# Cloudflare WAF / SBFM skip rules — for nakama agents

**長期 reference**：所有對 shosho.tw zone 的 CF skip rule 一覽 + 加新規則的標準作業。

**個別規則的 setup task**：用 `docs/runbooks/YYYY-MM-DD-add-{ua}-cf-skip-rule.md` 一份一個 task doc 給修修執行（per `feedback_doc_naming_date_prefix.md` — 要修修跑的一次性 instruction 走日期 prefix）。

---

## 為什麼需要

Cloudflare Super Bot Fight Mode（SBFM）會擋 datacenter / VPS IP，無論 User-Agent。我們的 agents 跑在：

- **VPS**（202.182.107.202 Vultr 香港）— 跑 Robin / Franky / Usopp / SEO audit / external probe
- **GH Actions runners**（Azure datacenter IP，動態）— 跑 external uptime probe / scheduled tests

這些 IP 全被 CF 標 datacenter，default 就擋。每個 agent 用自己的 User-Agent，CF dashboard 加 「if UA contains X then skip SBFM」rule。

---

## 既有 skip rules（依 UA 對應）

| User-Agent | 用途 | 加入時 PR | Setup task doc |
|---|---|---|---|
| `nakama-external-probe/1.0` | GH Actions external uptime probe | PR #115（2026-04-24） | — (PR-inline) |
| `NakamaBot/1.0` | seo-audit-post `fetch_html`（D.1 加） | PR #200（2026-04-27） | [2026-04-27-add-nakamabot-cf-skip-rule.md](2026-04-27-add-nakamabot-cf-skip-rule.md) |
| `nakama-wordpress-client/1.0` | `shared/wordpress_client.py` — Usopp publish + SEO 中控台 wp_post_lister + audit pipeline | PR #252（2026-04-29） | [2026-04-29-add-wp-client-cf-skip-rule.md](2026-04-29-add-wp-client-cf-skip-rule.md) |

加新 agent 要新 UA 時：
1. Append 進這張表（含對應 task doc 連結）
2. 開新 task doc `YYYY-MM-DD-add-{ua-slug}-cf-skip-rule.md` 給修修執行
3. 修修執行完回報 → PR review 帶上 task doc + 表格更新

---

## 加新 skip rule 的標準步驟（task doc 模板）

新 task doc 應該包含：

1. **Why** — 為什麼需要這條 rule（哪個 agent / endpoint）
2. **CF dashboard 步驟** — Custom rule 創建欄位（Rule name / UA value / action: Skip）
3. **驗收 curl** — VPS ssh 跑一條 curl 驗 status 200
4. **跑完之後** — agent 端要跑什麼來確認生效

具體格式參考 [2026-04-27-add-nakamabot-cf-skip-rule.md](2026-04-27-add-nakamabot-cf-skip-rule.md)。

---

## 注意事項

- **不要關整個 SBFM** — 還是要擋未知 bot，只開洞給特定 UA
- **不要用 IP whitelist** 取代 UA whitelist — VPS IP 可能換（Vultr 有 incident 換 IP 的可能），UA 跟 deploy 綁定比較穩
- **每加一條 skip rule 進這份 runbook 表格** — 不在表上的 rule 之後沒人記得為什麼存在
- **firecrawl Google Scholar 路徑不影響 CF** — firecrawl 從自家 IP 出去，不過 shosho.tw zone 的 CF
