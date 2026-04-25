---
name: SEO 工具地景與 API 契約陷阱
description: SEO 工具/API 選型快速 reference — 價格、契約坑、Health vertical 限制；詳見 docs/research/2026-04-24-seo-prior-art.md
type: reference
created: 2026-04-24
originSessionId: 0688a720-7789-447a-b7a2-7d60c84698d1
---
詳細研究見 [docs/research/2026-04-24-seo-prior-art.md](../../docs/research/2026-04-24-seo-prior-art.md)。此處記錄選型時最常踩到的契約坑與紅線。

## 紅線：Health vertical 數據源限制

**DataForSEO 自己在 help-center 註明**：health / financial / gambling 類 keyword 的 search_volume 與 CPC 可能被 Google Ads policy hide。
- 對修修（Health & Wellness 創作者）影響：**不能把 DataForSEO 當主 keyword volume 數據源**
- 替代方案：**Google Search Console API（免費）**取自己網站真實 query × URL 數據
- DataForSEO 只用來補非醫療類關鍵字的 difficulty / SERP 結構

## 價格地景（2026-04-24）

| 工具 | 起跳成本 | 模式 |
|---|---|---|
| Google Search Console API | $0 | 完全免費，1200 req/min |
| PageSpeed Insights API | $0 | 完全免費 |
| DataForSEO | $50 起儲值 | pay-as-you-go，credits 不過期 |
| SerpApi | $50/月起 | 100/月 free tier |
| Mangools | $29.90/月（年繳）| 含 API quota |
| Surfer | $99/月起 | API 內含 |
| Ahrefs | $129/月起 | MCP 內含於 plan |
| Semrush | $499.95/月（API plan）| MCP 內含 |

對 single-blog use-case：**GSC + PageSpeed + DataForSEO Labs（按需）= 月成本 < $3**，付費平台 sticker shock。

## API 契約坑

### DataForSEO

- **search_volume 一次 request 收一次費，無論帶 1 或 1000 keywords** → 必須批次
- Live mode 比 Standard 貴 ~3.3×（$0.002 vs $0.0006/req）
- Google Ads Live endpoints **每分鐘 12 req per account**
- OnPage JS render 比 Basic 貴 10×（$0.00125 vs $0.000125/page）
- bulk_keyword_difficulty 上限 1000 keywords/req
- $50 最低儲值

### Google Search Console API

- 單次 query 最多 25,000 rows（vs UI 的 1000）
- query × page 兩個 dimension 一起拉才能看 cannibalization
- < 10 impressions 的 query 會被 anonymize
- OAuth 2.0 setup 比 service account 麻煩，個人用 OAuth 即可

### PageSpeed Insights API

- 無 key 試用 OK，programmatic 要 key
- 無嚴格 quota，實務上 25K queries/day 安全
- category 參數要明列：`['PERFORMANCE','ACCESSIBILITY','BEST_PRACTICES','SEO']`

### Ahrefs

- Local MCP server **archived，已不維護**（要用 remote MCP）
- Local 用 v3 API key、Remote 用 MCP key（不通用）

## 開源 audit 工具一句話

| 工具 | License | 一句話 |
|---|---|---|
| SiteOne Crawler | MIT | Rust 寫，single-page mode + JSON CI gate（exit 10）|
| Unlighthouse | MIT | Node.js，全站 Lighthouse |
| python-seo-analyzer | MIT | 1.4k stars，全站爬蟲 + Claude integration |
| SEOmator | MIT | 251 rules，Node.js + Playwright |
| LibreCrawl | open | Python Flask web app，Screaming Frog 替代 |

**對修修最 fit**：自寫 Python ~25 條 deterministic check（可借 SEOmator rules 當 reference），不 subprocess 包這些工具（中文/SEOPress/Health 客製需求高）。

## Claude Code Skill 生態（已存在 prior art）

| Repo | 內容 | 對我們價值 |
|---|---|---|
| AgriciDaniel/claude-seo | 19 sub-skills + 12 subagents + DataForSEO/Firecrawl ext | 架構參考，不直接用 |
| JeffLi1993/seo-audit-skill | Script + LLM 兩層 pattern，極輕依賴 | **Architecture pattern 直接借** |
| aaron-he-zhu/seo-geo-claude-skills | 20 skills + CORE-EEAT 框架 | E-E-A-T prompt template 參考 |
| MadAppGang/claude-code/plugins/seo | technical-audit skill | 可看 SKILL.md 對齊度 |

## How to apply

- 規劃 SEO skill 時，先打開此 reference 看價格與契約坑
- 規劃前先確認是 Health vertical 還是 generic（Health 走 GSC 為主）
- 不主動推薦修修付費 SEO 平台訂閱（Mangools 例外，是平價 fallback）
- 任何「要花錢的數據源」決策都要先看此 doc 的價格表
