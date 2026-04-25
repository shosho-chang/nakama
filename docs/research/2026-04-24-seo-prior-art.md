---
title: SEO Solution Prior-Art Research
date: 2026-04-24
author: Mac lane (Claude)
status: research-only — 不寫 code、不開 PR、不改 agent 接線
purpose: 為「SEO 內容建議 + 部落格體檢 + Brook compose 整合」三大用途的架構決策提供工具地景與選項
related_memory:
  - memory/claude/project_seo_solution_scope.md
  - memory/claude/feedback_search_skills_first.md
  - memory/claude/feedback_open_source_ready.md
  - memory/claude/project_brook_compose_merged.md
---

# SEO Solution — Prior-Art Research（2026-04-24）

## 0. Executive Summary

修修的 SEO 方案要同時支援三件事：(1) 寫什麼（內容創作建議）、(2) 既有部落格體檢、(3) Brook compose 寫稿時把關鍵字資料一起餵進 LLM。本研究在實作前盤點工具地景，找出可複用點與決策點。

**三個關鍵發現：**

1. **Health & Wellness 是 Google Ads 受限類別** — DataForSEO 的 search_volume / CPC 對醫療/保健類關鍵字會被 Google 隱藏（DataForSEO 自己在 help-center 註明）。這直接改變數據源策略：**不能把 DataForSEO 當主數據源**，要以 Google Search Console（修修自己的真實流量）為核心，DataForSEO 補非醫療類關鍵字 + SERP 結構。

2. **修修的 stack 已經有 SEO 寫入路徑** — Usopp 已寫 SEOPress meta（focus_keyword + meta_description），Brook DraftV1 已有對應欄位，缺的是「**生成這些欄位時的數據驅動邏輯**」與「**發佈後的體檢回饋環**」。整合不是從零開始接線。

3. **claude-skill 生態已有 SEO 套件** — AgriciDaniel/claude-seo（19 sub-skills, MIT）、JeffLi1993/seo-audit-skill（Python+LLM 兩層）、aaron-he-zhu/seo-geo-claude-skills（20 skills）都是現成 prior art。**抄架構不抄實作** — 他們的 skill 邊界切法可以借，但體檢邏輯要結合修修的 SEOPress + 中文 + Health vertical 重做。

**推薦組合**（詳見 §5）：
- **數據層**：GSC API（免費，自己流量）+ PageSpeed Insights API（免費，CWV）+ DataForSEO Labs（$50 起 pay-as-you-go，補非醫療關鍵字 / SERP 結構）+ 既有 keyword-research skill（YouTube/Trends/Reddit）
- **體檢層**：自寫 Python on-page checker（fetch + lxml + Claude semantic review，two-layer pattern）+ PageSpeed Insights for CWV
- **整合層**：3 個 skill — `seo-audit-post`（單篇體檢）、`seo-keyword-enrich`（keyword-research 結果 → DataForSEO/GSC enrich）、`seo-optimize-draft`（compose 階段把 enriched keyword 餵 Brook）
- **明確不走**：Ahrefs（$129/mo）、Semrush（$499/mo）、SurferSEO API（看似 fit 但 sticker shock）、site-wide crawler（Unlighthouse / SiteOne 對 single-blog use-case overkill）

---

## 1. 既有工具生態調研

### 1.1 付費 SEO 數據平台

#### DataForSEO API + 官方 MCP Server

**Capability card**

| 欄位 | 內容 |
|---|---|
| 能力 | 全 SERP 數據：keyword volume / CPC / difficulty / ranked keywords / competitor analysis / on-page audit / Lighthouse |
| Scope / Non-goals | 不做即時 ranking 追蹤訂閱（要自己排程）；不做 AI overview 監測（要走 Trends/Labs 組合） |
| 輸入 | API user/password；POST JSON requests；可選 location_code / language_code |
| 輸出 | JSON；Standard 模式批次，Live 模式即時 |
| 授權模式 | Pay-as-you-go，**$50 最低儲值**，credits 不過期 |
| 成本 | Standard SERP $0.0006/req、Live $0.002/req、Priority $0.0012/req；OnPage Basic $0.000125/page、JS render 10× = $0.00125/page；Lighthouse $0.0040/page；Keywords Data search_volume 單次 request 上限 1000 keywords |
| MCP 整合 | Official typescript MCP server，free to install，按 API call 計費 |
| 對修修風險 | **Health & Wellness 屬 Google Ads 受限類別 — search_volume 與 CPC 可能被 hide**（官方 help-center 明確說明） |

**API 契約坑** → 寫進 [reference_seo_tools_landscape.md](../../memory/claude/reference_seo_tools_landscape.md)：
- search_volume 一次 request 收一次費，無論帶 1 或 1000 keywords，**要批次處理才划算**
- Live 比 Standard 貴 ~3.3×，daemon/ad-hoc audit 用 Standard，互動式 Slack 才用 Live
- Google Ads Live endpoints **每分鐘 12 req 上限**（per account）

來源：[DataForSEO Pricing](https://dataforseo.com/pricing)、[OnPage API Pricing](https://dataforseo.com/pricing/on-page/onpage-api)、[Help — SV/CPC restrictions](https://dataforseo.com/help-center/sv-cpc-cmp-with-dataforseo-api)、[Official MCP Server](https://github.com/dataforseo/mcp-server-typescript)

---

#### Ahrefs（含 official MCP）

**Capability card**

| 欄位 | 內容 |
|---|---|
| 能力 | 主流 SEO 數據（backlinks 是強項）+ remote MCP server（無額外費用） |
| Scope / Non-goals | MCP **只在 Lite plan ($129/mo) 以上開放**；MCP 用量計入 plan 內 Integration API units |
| 輸入 | Ahrefs subscription + API key（v3 keys 給 local server，MCP keys 給 remote） |
| 輸出 | MCP 工具透過 Claude/Cursor 對話介面 |
| 授權模式 | 月訂閱：Lite $129、Standard $249、Advanced $449 |
| 成本 | 最低 $129/月固定（沒有 pay-per-call 選項） |
| 對修修風險 | **個人創作者 sticker shock** — $1,548/年只用 backlinks/SERP 對 single-blog use-case 性價比差 |

來源：[Ahrefs MCP Docs](https://docs.ahrefs.com/mcp/docs/introduction)、[Ahrefs Pricing](https://ahrefs.com/blog/ahrefs-pricing/)

**結論**：除非修修決定要做大量競品 backlink 分析，否則跳過。

---

#### Semrush（含 official MCP）

**Capability card**

| 欄位 | 內容 |
|---|---|
| 能力 | 同 Ahrefs，外加 trends/PPC 數據；MCP 整合 50K units 內含於 Pro 以上 plan |
| Scope / Non-goals | API 訂閱要 Business plan ($499.95/mo) 才有完整 access |
| 輸入 | Semrush subscription + API key |
| 輸出 | MCP tools |
| 授權模式 | 月訂閱：Pro $139、Guru $249、Business $499.95 |
| 成本 | API 額外 unit 約 $50/百萬 unit；單次 endpoint 1-100 units |
| 對修修風險 | **更嚴重的 sticker shock** — Business plan 必要才有 API 完整 access |

來源：[Semrush MCP Knowledge Base](https://www.semrush.com/kb/1618-mcp)、[Semrush API Pricing](https://thatmarketingbuddy.com/blog/semrush-api-pricing)

**結論**：價格與我們的 use-case 不匹配，跳過。

---

#### SurferSEO API

**Capability card**

| 欄位 | 內容 |
|---|---|
| 能力 | Content Score（與目標關鍵字 top 20-50 SERP 比對）、SERP Analyzer、Audit endpoint |
| Scope / Non-goals | 不提供 standalone keyword data；主要用來「優化已有草稿到 80+ score」 |
| 輸入 | Surfer subscription + REST API |
| 輸出 | JSON content score + 改進建議 |
| 授權模式 | 月訂閱（Essential $99、Advanced $179、Max $299）+ API access |
| 對修修風險 | 看似 fit Brook compose 整合，但中文支援存疑、$99/月對 single-blog use-case 偏高 |

來源：[Surfer API Docs](https://docs.surferseo.com/en/articles/8201326-surfer-api-examples-of-use)

**結論**：架構上 fit Brook compose（給 score 反饋），但成本與中文支援不確定，**列為 phase 2 才評估**。

---

#### SerpApi

**Capability card**

| 欄位 | 內容 |
|---|---|
| 能力 | SERP scraping（Google / Bing / Yandex / 40+ engines），Trends API 包裝 |
| Scope / Non-goals | **不做 keyword volume / difficulty**（純 SERP scraping） |
| 輸入 | API key |
| 輸出 | JSON SERP 結構 |
| 授權模式 | Free tier 100/月；$50/月 5000 calls；$15/1000 calls 偏貴 |
| 對修修風險 | 與我們已有的 keyword-research skill (Trends + DDG twitter proxy) 重疊 |

來源：[SerpApi](https://serpapi.com/)、[Best SERP APIs 2026](https://scrapfly.io/blog/posts/google-serp-api-and-alternatives)

**結論**：與既有 keyword-research skill 重疊，跳過。

---

#### Mangools / Keywords Everywhere（平價組）

| 工具 | 價格 | 適用 |
|---|---|---|
| Mangools | $29.90/月起（年繳）| 小團隊全套 SEO；API 內含但 quota 受限 |
| Keywords Everywhere | $21/年 | 純 keyword volume overlay，不適合 programmatic |

來源：[Affordable SEO Tools 2026](https://webseotrends.com/blog/affordable-seo-tools/)、[Mangools API](https://mangools.com/api)

**結論**：Mangools 是「修修若不要 DataForSEO，就用這個」的後備選項，但 API quota 偏緊。

---

### 1.2 免費官方數據源

#### Google Search Console API ⭐

**Capability card**

| 欄位 | 內容 |
|---|---|
| 能力 | 自己網站的真實 search performance：query × page × clicks × impressions × CTR × position |
| Scope / Non-goals | **只看自己網站**（無競品數據）；非醫療關鍵字也會被 GSC anonymize（< 10 impressions） |
| 輸入 | OAuth 2.0 / Service Account；Search Console property verified |
| 輸出 | JSON（searchanalytics.query()），單次最多 25,000 rows |
| 授權模式 | **完全免費**，無 quota 限制（理論上 1200 req/min/user，實務無壓力） |
| 成本 | $0 |
| 對修修風險 | **對 Health vertical 是最佳選擇** — DataForSEO/Ahrefs 看不到的 health keyword，GSC 看得到（因為是自己網站的真實數據，不受 Google Ads policy 影響）|

**為什麼這對修修是核心**：
- shoshostudio.com 已有流量歷史 → GSC 有真實 query × URL pair 數據
- Keyword cannibalization 偵測（同一 keyword 多個 URL 競爭）只能靠 GSC，付費工具看不到
- Striking-distance（position 11-20）keyword 是最快的 SEO win，GSC 是唯一準確來源

來源：[GSC API Guide](https://developers.google.com/webmaster-tools/v1/searchanalytics/query)、[Free GSC MCP Servers](https://github.com/AminForou/mcp-gsc)、[Cannibalization detection with Python](https://www.jcchouinard.com/keyword-cannibalization-tool-with-python/)

---

#### PageSpeed Insights API ⭐

**Capability card**

| 欄位 | 內容 |
|---|---|
| 能力 | Lighthouse audit 完整輸出（Performance / SEO / Accessibility / Best Practices / PWA），含 Core Web Vitals lab + field data |
| Scope / Non-goals | 只 audit 單一 URL；不做 site-wide crawl（要自己 sitemap iterate）|
| 輸入 | URL；optional API key（無 key 試用 OK，programmatic 要 key）|
| 輸出 | JSON `lighthouseResult` + `loadingExperience` |
| 授權模式 | **完全免費** |
| 成本 | $0；無嚴格 quota（實務上 25K queries/day per project 安全） |
| 對修修風險 | 無 |

來源：[PageSpeed Insights API](https://developers.google.com/speed/docs/insights/v5/get-started)、[Python lib pyspeedinsights](https://pypi.org/project/pyspeedinsights/)

---

#### Google Search Console MCP（社群）

| Server | 工具數 | 重點 |
|---|---|---|
| [AminForou/mcp-gsc](https://github.com/AminForou/mcp-gsc) | 基礎 | uvx 安裝免 venv |
| [suganthan-gsc-mcp](https://suganthan.com/blog/google-search-console-mcp-server/) | 20 tools | quick wins / content decay / cannibalisation / CTR benchmarks |

**修修可選擇**：直接裝現成 MCP（互動式分析）或自寫 thin wrapper（programmatic skill 用）。Skill 場景建議自寫，互動式 Slack 場景可裝 MCP。

---

### 1.3 開源 audit 工具

#### SiteOne Crawler（Rust + JSON output, MIT）

**Capability card**

| 欄位 | 內容 |
|---|---|
| 能力 | 全站爬蟲 + 五大評分（Performance / SEO / Security / Accessibility / Best Practices）；單檔 binary < 20MB |
| Scope / Non-goals | SEO 檢查偏基本（H1 / title uniqueness / meta description / 404）；無 schema 驗證、無 E-E-A-T 評估 |
| 輸入 | URL；CLI flags `--single-page` / `--workers` / `--include-regex` |
| 輸出 | JSON 含 `ciGate` object（passed / exitCode 10 / checks array）+ HTML report |
| 授權模式 | MIT，全免費 |
| 成本 | $0，本機運算 |
| 對修修風險 | SEO check 深度不夠（只 covers 基本 hygiene） |

來源：[github.com/janreges/siteone-crawler](https://github.com/janreges/siteone-crawler) v2.3.0 (2026-03-30)

**用法**：適合「整站快速 hygiene check」或 CI gate（exit code 10 = fail）。**不適合**深度單篇 audit。

---

#### Unlighthouse（Node.js, 全站 Lighthouse）

**Capability card**

| 欄位 | 內容 |
|---|---|
| 能力 | 並行跑 Lighthouse over sitemap，single dashboard |
| Scope / Non-goals | 全站 perf-focused；非 on-page SEO checker |
| 輸入 | sitemap URL |
| 輸出 | HTML dashboard + per-URL Lighthouse JSON |
| 授權模式 | MIT |
| 成本 | $0 + 本機 CPU |

來源：[unlighthouse.dev](https://unlighthouse.dev/)

**用法**：定期全站 perf 體檢；對單篇 audit 是 overkill。

---

#### python-seo-analyzer（aka pyseoanalyzer, MIT）

**Capability card**

| 欄位 | 內容 |
|---|---|
| 能力 | 全站爬蟲 + heading 分析 + word count + 基本技術 SEO；最近版本加了 Claude AI 對 expertise signals 的評估 |
| Scope / Non-goals | 全站偏向；單頁深度不夠 |
| 輸入 | URL；`analyze_headings=True, analyze_extra_tags=True` flag |
| 輸出 | JSON / HTML |
| 授權模式 | MIT |
| 成本 | $0；Claude integration 走 API key |
| 活躍度 | 1.4k stars，2025-04 仍有 release |

來源：[github.com/sethblack/python-seo-analyzer](https://github.com/sethblack/python-seo-analyzer)

**用法**：可參考它的 check rule set，**不一定要 import**（依賴大）。

---

#### SEOmator / seo-audit-skill（Node.js, MIT）

**Capability card**

| 欄位 | 內容 |
|---|---|
| 能力 | 251 條 audit rules / 20 個 categories；Console / JSON / HTML / Markdown / LLM-XML 五種輸出 |
| 輸入 | URL + 可選 crawl 參數 |
| 輸出 | 多格式；包含 CWV / 結構化資料 / JS rendering / security headers |
| 依賴 | Node.js 18+, Playwright |
| 授權模式 | MIT |
| 對修修風險 | Node.js 依賴（修修主 stack 是 Python；agent 用 subprocess shell out OK） |

來源：[github.com/seo-skills/seo-audit-skill](https://github.com/seo-skills/seo-audit-skill)

**用法**：可作為 baseline rule reference；亦可直接 subprocess 呼叫 + 取 JSON。

---

### 1.4 Claude Code Skill / Plugin 生態

#### AgriciDaniel/claude-seo（MIT, 19 sub-skills）

**結構**：
- 19 sub-skills（technical audit / E-E-A-T / schema / GEO / local SEO / programmatic SEO / 等）
- 12 subagents
- 3 extensions：DataForSEO / Firecrawl / Banana（圖片）

**安裝**：plugin marketplace（Claude Code 1.0.33+）/ git clone / curl one-liner

**對修修價值**：
- ✅ **架構參考**：sub-skills 邊界切法、與 DataForSEO/Firecrawl 整合方式
- ❌ **不直接用**：泛用設計、無 SEOPress 整合、無中文/Taiwan 在地化、E-E-A-T check 偏 generic

來源：[github.com/AgriciDaniel/claude-seo](https://github.com/AgriciDaniel/claude-seo)

---

#### JeffLi1993/seo-audit-skill（兩層架構）

**架構**（這個 pattern 對我們很有用）：
- **Script 層**：Python 做 deterministic check（HTTP status / XML parsing / string matching）
- **LLM 層**：Claude 做 semantic judgment（keyword intent / content quality / page type）
- `llm_review_required` flag 決定哪些 check 走 LLM

**輸入/輸出**：單一 URL → `reports/<hostname>-audit.html`

**依賴**：`pip install requests`（極輕）

**兩層**：`seo-audit`（20+ checks 基本）/ `seo-audit-full`（加 CWV / GSC / 競品）

來源：[github.com/JeffLi1993/seo-audit-skill](https://github.com/JeffLi1993/seo-audit-skill)

**對修修價值**：
- ✅ **直接借 architecture**：`Script + LLM` 兩層 pattern 是業界 best practice，跟我們 [feedback_skill_design_principle.md] 三層架構完全 aligned
- ✅ `llm_review_required` flag 設計可抄
- ❌ check 規則要重做（要中文、要 SEOPress、要 Health vertical compliance）

---

#### aaron-he-zhu/seo-geo-claude-skills（20 skills bundle）

**重點**：
- CORE-EEAT + CITE 框架（GEO/AEO 優化）
- 跨平台支援（Cursor/Codex/Gemini CLI）

**對修修價值**：
- ✅ 框架參考（CORE-EEAT 的 prompt template）
- ❌ 太大，20 skills 太多 surface area

來源：[github.com/aaron-he-zhu/seo-geo-claude-skills](https://github.com/aaron-he-zhu/seo-geo-claude-skills)

---

### 1.5 其他 prior art

| 工具 | 一句話 | 為什麼跳過 |
|---|---|---|
| LibreCrawl | 開源 Screaming Frog 替代（Python Flask web app） | 全站 crawler，single-blog use-case overkill |
| Screaming Frog | 業界標竿付費 desktop crawler | 桌面工具，不適合 agent embedding |
| viasite/site-audit-seo | Lighthouse + console/json/csv 多輸出 | 與 Unlighthouse 重疊 |
| Glimpse | Trends 加強版 + 絕對 search volume | $$$，且 keyword-research skill 已含 Trends |

---

## 2. 既有 Nakama Skill / MCP 能複用的

### 2.1 keyword-research skill — 研究階段已 covered

**看 [.claude/skills/keyword-research/SKILL.md](../../.claude/skills/keyword-research/SKILL.md)：**

**已產出**（frontmatter schema）：
```yaml
type: keyword-research
topic / topic_en / content_type
sources_used: [youtube_zh, youtube_en, trends_zh, trends_en,
               autocomplete_zh, autocomplete_en, reddit, twitter, ...]
core_keywords:
  - keyword / keyword_en / search_volume / competition / opportunity / source / reason
trend_gaps:
  - topic / en_signal / zh_status / opportunity
youtube_title_seeds: [...]
blog_title_seeds: [...]
```

**用了哪些數據源**：YouTube Data API + Google Trends（trendspy）+ Autocomplete + Reddit + Twitter via DDG。

**SEO solution 不能重做的**：
- ❌ keyword 探索（是 keyword-research 的本職）
- ❌ trend gap 分析
- ❌ title seeds 生成

**SEO solution 應該擴充的**：
- ✅ 把 `core_keywords` 餵進 GSC 對照（這個 keyword 我們網站 ranking 在哪？striking distance？）
- ✅ 把 `core_keywords` 餵進 DataForSEO 拿 difficulty score（如果不是 health restricted）
- ✅ 把 `blog_title_seeds` 餵進 Brook compose 變成 draft

**結論**：keyword-research **已凍結，不動它**；SEO solution 是它的 **下游消費者**。

---

### 2.2 firecrawl plugin — 競品爬取

已裝（[project_plugins_installed.md](../../memory/claude/project_plugins_installed.md)）：
- 能力：scrape / crawl / search / map（含 JS render + 反偵測）
- 適用 SEO use-case：競品 top-10 SERP 頁面爬取（看他們在標題/H2/結構化資料用了什麼）
- 免費額度：有限，但 single-blog use-case 夠

**SEO solution 應該借**：competitor SERP 爬取（給 compose skill 看「目前排前 10 的長什麼樣」）。

---

### 2.3 playwright plugin — Headless browser

已裝。適用 SEO use-case：
- Screenshot for SEOPress preview
- E2E 驗證 SEOPress meta 寫入後的渲染

**SEO solution 用法**：選用，**phase 2 再考慮**。

---

### 2.4 Brook compose — 已有 SEO 欄位

看 [agents/brook/compose.py:307-319](../../agents/brook/compose.py#L307-L319)：

```json
{
  "title": "...",
  "slug_candidates": [...],
  "excerpt": "...",
  "focus_keyword": "<2-60 字主關鍵字>",
  "meta_description": "<50-155 字 SEO meta>",
  ...
}
```

**現狀**：LLM 自己生 `focus_keyword` 和 `meta_description`，**沒有任何外部關鍵字數據驗證**。

**SEO solution 應該插入的點**：在 `_build_compose_system_prompt` 之前，把 enriched keyword data（搜索量、難度、SERP gap、striking distance）作為 context 餵進 system prompt。

**整合介面（凍結中）**：DraftV1 → PublishWpPostV1 → approval_queue → Usopp claim → SEOPress + WP REST。`focus_keyword` / `meta_description` 直接寫到 SEOPress（[agents/usopp/publisher.py:262](../../agents/usopp/publisher.py#L262)）。

---

### 2.5 Robin KB search — 知識背景

看 [agents/robin/kb_search.py:13](../../agents/robin/kb_search.py#L13)：

`search_kb(query, vault_path, top_k=8)` → 由 Claude Haiku 排序 KB pages，回傳 type / title / path / relevance_reason。

**SEO solution 應該借**：當 SEO research 需要「修修自己對這個主題已經寫過/知道什麼」時，先 query Robin KB 拿 context（避免 SEO suggestion 與既有 voice 衝突）。

---

### 2.6 SEOPress 寫入路徑（Usopp）

看 [agents/usopp/publisher.py:262](../../agents/usopp/publisher.py#L262) + [shared/seopress_writer.py](../../shared/seopress_writer.py)：

- 已有 SEOPress REST API 寫入（`focus_keyword` / `meta_description`）
- 三層 fallback：REST → post meta → skip + Critical alert
- E2E 測試已通過（PR #101 Slice C2a）

**SEO solution 不需要重做寫入**，只需要**生成更好的 `focus_keyword` / `meta_description`** 供 Brook 寫進 DraftV1。

---

## 3. 部落格 audit workflow 實務

### 3.1 業界標準 on-page SEO checklist（2026 edition）

整合多份 2026 checklist（[Chillybin](https://www.chillybin.co/on-page-seo-checklist/)、[CrawlWP](https://crawlwp.com/on-page-seo-checklist/)、[Pentagon](https://www.pentame.com/blog/on-page-seo-checklist-2026-top-10-proven-ranking-fixes/)）：

#### 3.1.1 Metadata（deterministic checks，Script 層處理）

- [ ] `<title>` 50-60 字符、含 focus keyword、unique
- [ ] `<meta name="description">` 150-160 字符、含 focus keyword 自然出現
- [ ] `<link rel="canonical">` 存在且指向自己（除非有 syndication）
- [ ] `<meta name="robots">` 不誤設 noindex
- [ ] OpenGraph：`og:title` / `og:description` / `og:image` / `og:url`
- [ ] Twitter Card：`twitter:card` / `twitter:image`

#### 3.1.2 Content structure（混合 Script + LLM）

- [ ] H1 唯一、含 focus keyword 或語義近似（**LLM 判斷語義**）
- [ ] H2/H3 階層合理、不跳級（Script 檢查）
- [ ] 第一段含 focus keyword（**LLM 判斷自然度**）
- [ ] Word count 達閾值（Health 類建議 1500-2500，Script 計）
- [ ] Internal links 至少 2-3 個（Script 計）
- [ ] External links 引用權威源（**LLM 判斷權威性**）

#### 3.1.3 Images（deterministic）

- [ ] 所有 `<img>` 有 `alt`（< 125 字符）
- [ ] Featured image 存在
- [ ] 圖片 lazy loading
- [ ] WebP/AVIF 優先

#### 3.1.4 Schema markup（Script + LLM 混合）

- [ ] Article schema（Health 類加 MedicalWebPage 或自訂）
- [ ] FAQPage schema（如果有 FAQ section）
- [ ] BreadcrumbList schema
- [ ] Author schema（E-E-A-T 強化）
- [ ] **LLM 驗證**：schema 是否與可見內容一致（Google 2026 反 schema spam）

#### 3.1.5 Core Web Vitals（PageSpeed Insights API）

- [ ] LCP < 2.5s
- [ ] INP < 200ms（取代 FID 自 2024-03）
- [ ] CLS < 0.1

#### 3.1.6 E-E-A-T（純 LLM judgment）

- [ ] **Experience**：第一人稱經驗、案例、照片證據
- [ ] **Expertise**：作者 bio、引用論文、professional credentials
- [ ] **Authoritativeness**：被引用、外部 mentions
- [ ] **Trustworthiness**：HTTPS、隱私政策、聯絡資訊、台灣藥事法/醫療法 compliance（與 Brook compliance_scan 連動）

---

### 3.2 開源工具能 cover 的 vs. 自寫

| Check 類別 | 開源工具能 cover | 我們要自寫 |
|---|---|---|
| Metadata（title/desc/canonical/OG）| ✅ SEOmator, SiteOne, python-seo-analyzer | — |
| H1/heading 結構 | ✅ 全部 | — |
| Image alt | ✅ 全部 | — |
| Schema 存在 | ✅ SEOmator, SiteOne | — |
| Schema **與內容一致性** | ❌ | ✅ LLM judgment |
| Core Web Vitals | ✅ PageSpeed Insights API | — |
| Internal links 結構 | ✅ 全部 | — |
| Internal links **語義相關性** | ❌ | ✅ LLM 對 KB 比對 |
| Focus keyword 在 H1/第一段 | ⚠️ 偏字面比對 | ✅ LLM 語義 |
| E-E-A-T signals | ❌ | ✅ LLM + 修修 author profile |
| 中文/台灣在地化 check | ❌ | ✅ 自訂規則 |
| 藥事法/醫療法 compliance | ❌ | ✅ 已有 [compliance_scan.py](../../agents/brook/compliance_scan.py)（Slice B 補強中） |
| Striking distance / cannibalization | ❌（要 GSC API） | ✅ |

**結論**：「Script + LLM 兩層」是必走架構（與 [feedback_skill_design_principle.md] 完全 aligned）。Script 層可以選擇 (a) 自寫純 Python（依賴 `requests` + `lxml`/`beautifulsoup4`）或 (b) subprocess 呼叫 SEOmator/SiteOne 取 JSON 後 reshape。

**建議走 (a) 自寫純 Python**：理由：
- 規則不多（~20-30 條 deterministic check）
- 對 Health vertical / SEOPress / 中文要客製，subprocess 反而綁手
- 開源時零 Node.js 依賴更乾淨（[feedback_open_source_ready.md] 第 7 點）
- 可借 SEOmator 251 rules 作為 reference checklist，挑出 single-blog 真正需要的 ~25 條

---

### 3.3 Workflow 範本（給 SEO audit skill）

```
Step 1. Input: URL（可選 GSC property 對照）
Step 2. Fetch + parse HTML（requests + lxml）→ structured data
Step 3. PageSpeed Insights API 跑 SEO + Performance category
Step 4. （optional）GSC API 取最近 28 天 query × URL 數據
Step 5. Script 層 deterministic checks（~25 條）
Step 6. LLM 層 semantic checks（~10 條，用 llm_review_required flag）
Step 7. 產出 markdown report：
        - Summary table（pass/warn/fail count）
        - 每條 check 的 actual / expected / fix suggestion
        - Striking distance opportunities（如有 GSC）
        - Cannibalization warnings（如有 GSC）
        - Featured image / schema 修正建議
```

---

## 4. 整合點分析

### 4.1 三大用途的 data flow

```
┌─────────────────────────────────────────────────────────────┐
│ 用途 1: 內容創作建議（已 covered，keyword-research）        │
│                                                              │
│ topic → keyword-research skill → frontmatter 報告           │
│         (YouTube/Trends/Reddit + Claude synth)              │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼ frontmatter
┌─────────────────────────────────────────────────────────────┐
│ 用途 3: Brook compose 整合（new — seo-keyword-enrich）      │
│                                                              │
│ keyword-research 報告 → seo-keyword-enrich skill            │
│   ├─ GSC API 對照（自己的 ranking / striking distance）     │
│   ├─ DataForSEO Labs（非 health 類加 difficulty）           │
│   └─ firecrawl 爬 top-3 SERP（看競品結構）                  │
│ → enriched keyword data                                      │
│ → Brook compose（修改 _build_compose_system_prompt 餵進）   │
│ → DraftV1 with 數據驅動的 focus_keyword + meta_description  │
│ → approval_queue → Usopp → SEOPress 寫入                    │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ 用途 2: 既有部落格體檢（new — seo-audit-post）              │
│                                                              │
│ URL → seo-audit-post skill                                   │
│   ├─ requests + lxml 拉 HTML                                │
│   ├─ PageSpeed Insights API（SEO + Perf + CWV）             │
│   ├─ GSC API 拉最近 28 天 query × URL（可選）               │
│   ├─ Script 層 ~25 deterministic checks                     │
│   └─ LLM 層 ~10 semantic checks（含 E-E-A-T、藥事法）       │
│ → markdown report（含 fix suggestions）                      │
│ → 可選：再餵 seo-optimize-draft 重寫該篇                    │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Brook compose 修改點（精準路徑）

要動 [agents/brook/compose.py:476](../../agents/brook/compose.py#L476)：

**現狀**：
```python
def compose_and_enqueue(*, topic, category, kb_context="", source_content="", ...):
    profile = load_style_profile(resolved_category)
    system_prompt = _build_compose_system_prompt(profile)
    user_msg = _build_user_request(topic, kb_context, source_content)
```

**SEO 整合後（提案）**：
```python
def compose_and_enqueue(*, topic, category, kb_context="",
                       source_content="", seo_context: SEOContextV1 | None = None, ...):
    profile = load_style_profile(resolved_category)
    system_prompt = _build_compose_system_prompt(profile, seo_context)  # 新增 SEO block
    user_msg = _build_user_request(topic, kb_context, source_content)
```

`SEOContextV1` 新 schema（在 `shared/schemas/publishing.py`）：
```python
class SEOContextV1(BaseModel):
    target_focus_keyword: str
    related_keywords: list[KeywordWithMetricsV1]  # vol / difficulty / opportunity
    competitor_serp_summary: str  # firecrawl top-3 摘要
    striking_distance_keywords: list[str]  # GSC 來的（可選）
    title_seed_hints: list[str]  # 從 keyword-research frontmatter 來
```

**為什麼這樣切**：保持 compose 對 SEO context 的依賴是 **opt-in**（None = fallback 到目前行為），不破壞既有對話式 flow。

---

### 4.3 Robin KB 作為 SEO research context

當 seo-audit-post skill 跑到 LLM semantic check 時：

```python
# 偽 code
existing_kb_context = robin.kb_search.search_kb(
    query=focus_keyword, vault_path=vault, top_k=5
)
llm_review_prompt = f"""
作者 KB 已有的相關內容：
{existing_kb_context}

請判斷這篇文章與作者既有觀點是否一致，並建議 internal link 機會。
"""
```

**價值**：
- 避免 SEO suggestion 與作者既有觀點衝突
- 自動產生 internal link 機會（指向 KB 相關 page）

---

### 4.4 Skill 家族切法 — 三個選項

#### Option A：3 個 skill（推薦）

| Skill | 職責 | 觸發詞 |
|---|---|---|
| `seo-audit-post` | 單篇 URL 體檢，產 markdown report | "audit 一下 https://..."、"幫這篇做 SEO 體檢" |
| `seo-keyword-enrich` | keyword-research 結果 → +GSC/+DataForSEO/+SERP enrichment | "把這份關鍵字研究 enrich 一下"、"加上 ranking 數據" |
| `seo-optimize-draft` | 既有 draft + enriched keyword → 改寫建議 / 重新生稿 | "用這份 SEO 數據重寫"、"優化這篇草稿" |

**Pros**：
- 邊界清楚，每個 skill 一個動作
- 與 keyword-research 解耦（enrich 是後置 enhancer）
- 整合 Brook compose 時，`seo-optimize-draft` 是天然 wrapper（內部呼叫 compose）

**Cons**：
- 修修要記三個 skill
- 跨 skill 串接靠 frontmatter contract，要小心 schema drift

#### Option B：1 個大 skill `seo`

```
/seo audit https://...
/seo enrich <keyword-research-report.md>
/seo optimize <draft.md> --keywords <enriched.md>
```

**Pros**：表面少一個觸發點。**Cons**：違反 [feedback_skill_design_principle.md] 「skill 粒度扁平」原則。

#### Option C：2 個 skill（折衷）

- `seo-audit`（單篇 URL 體檢，covers 用途 2）
- `seo-compose`（吃 keyword-research → 跑 enrichment → 內部 call Brook compose，covers 用途 1+3）

**Pros**：用途 1+3 緊密耦合，少一個跨 skill 串接點。
**Cons**：`seo-compose` 變很重（enrichment + composing 混一起）；單純想 enrich 但不寫稿時沒地方去。

#### 建議

**Option A（3 個 skill）**，理由：
1. `seo-keyword-enrich` 獨立有複用價值（修修可能想看 enrichment 結果再決定要不要寫）
2. 三個 skill 都符合 single-responsibility，testing/debugging 簡單
3. 開源時可分別抽出（[feedback_open_source_ready.md]）

**但這是 architecture 決策，最後要修修拍板**。

---

### 4.5 整合風險清單

| 風險 | 緩解 |
|---|---|
| GSC API quota 對 daily audit 是否足夠 | 1200 req/min/user 上限，daily audit 一篇 < 5 req，無壓力 |
| DataForSEO health restriction → search_volume 缺失 | 主數據走 GSC（自己網站），DataForSEO 只 enrich 非 health terms |
| keyword-research frontmatter schema 變動 → enrich skill 壞 | 凍結 keyword-research output schema 為合約；enrich skill 對 missing key fall back gracefully |
| Brook compose 加 `seo_context` 參數 → 對話式 flow 不破 | 設 default None，現有 chat flow 完全不感知 |
| SEOPress 寫入失敗（已有 fallback） | 既有 `_alert_seopress_skipped` 路徑覆蓋 |
| 中文 SEO check 規則不齊全 | phase 1 抓 80% 業界 rule + 5-8 條中文/在地化規則；不追完美 |
| Health vertical 法規 compliance（藥事法/醫療法）| 既有 [compliance_scan.py] 已 cover 大部分；audit skill 加「合規 SEO 不衝突」單獨 check |

---

## 5. 結論與建議

### 5.1 推薦工具組合（含成本估算）

| 層 | 工具 | 成本 | 理由 |
|---|---|---|---|
| Keyword research | 既有 keyword-research skill | $0.05/run | 已 covered，不動 |
| GSC 數據 | Google Search Console API（自寫 thin wrapper）| $0 | Health vertical 唯一可靠數據源 |
| Keyword enrichment | DataForSEO Labs `keyword_difficulty` + `bulk_keyword_difficulty` | $50 起儲值，~$0.01/req（1000 keywords/req）| 補非 health 類 difficulty；單次 audit 約 $0.005 |
| 競品 SERP 結構 | firecrawl plugin（已裝）| 免費 quota 內 | 已 covered |
| Page audit + CWV | PageSpeed Insights API + 自寫 Python checker | $0 + Claude semantic LLM ~$0.01-0.03/audit | 雙層架構，PR-friendly |
| WP 寫入 | Usopp + SEOPress（已 wired）| $0 | 已 covered |

**單次 audit 預估**：~$0.01-0.05（PageSpeed 免費 + Claude review）
**單次 enrich 預估**：~$0.01-0.03（DataForSEO + Claude synth）
**單次 optimize-draft 預估**：~$0.05-0.15（含 Brook compose Sonnet call）

**月成本估算**（修修一週寫 2 篇 + 體檢 5 篇 + enrich 3 次）：
- DataForSEO：~$0.15/月
- Claude API：~$2/月
- 總計：< $3/月 + 一次性 $50 DataForSEO 儲值（用 6+ 個月）

vs. 付費平台對照：
- Ahrefs Lite：$129/月
- Semrush Pro：$139/月
- SurferSEO Essential：$99/月

**省 95%+，且更貼合 Health vertical**。

---

### 5.2 Skill 家族切法 tradeoff（給修修選）

#### 選項 1（推薦）：3 skill — `seo-audit-post` + `seo-keyword-enrich` + `seo-optimize-draft`

- ✅ Single responsibility，testing 簡單
- ✅ `seo-keyword-enrich` 獨立有 standalone 價值
- ✅ 開源時可單獨抽出
- ❌ 三個觸發點要記
- ❌ 串接靠 frontmatter contract（要 lock schema）

#### 選項 2：2 skill — `seo-audit` + `seo-compose-with-data`

- ✅ 少一個 skill，少一個串接點
- ❌ `seo-compose-with-data` 變得很重
- ❌ 純 enrich（不寫稿）沒地方去

#### 選項 3：1 skill — `seo`（subcommand 風格）

- ✅ 表面只一個入口
- ❌ 違反 nakama 「skill 粒度扁平」原則
- ❌ 內部 logic 混雜，testing 難

---

### 5.3 明確不建議走的路線

| 路線 | 為什麼不走 |
|---|---|
| 訂 Ahrefs / Semrush / SurferSEO subscription | $99-499/月對 single-blog use-case CP 值極差，且大部分功能我們不需要 |
| 把 SEOmator / SiteOne 整顆 subprocess 包起來當 audit 引擎 | 中文 / SEOPress / Health 客製需求高，subprocess 反而綁手；自寫 ~25 條 Python check 更乾淨 |
| 用 Surfer Content Score 作為 Brook compose 的優化 metric | 黑箱、$$$、中文支援存疑；未來 phase 2 再評估 |
| 直接安裝 AgriciDaniel/claude-seo plugin 跑 | 泛用設計，不 fit 我們 Health vertical / SEOPress / 中文需求；**可借架構不能直接用** |
| 把 keyword-research 重做成 SEO research 一體化 | keyword-research 已凍結且 production，重做 = scope creep；SEO solution 應為 keyword-research 的下游消費者 |
| 在 phase 1 就接 SurferSEO API 給 Brook score 反饋 | 增加 dependency 但不解問題核心；phase 2 有需求再評估 |
| 全站 site-wide crawler（Unlighthouse / SiteOne 全量）| 修修 use-case 是「單篇 audit」+「定期看 GSC」；全站 crawl overkill |

---

## 6. Open Questions for修修

實作前要先 align：

1. **Skill 家族切法選 A / B / C？**（推薦 A）
2. **DataForSEO 要不要儲值 $50 起步？**（建議要 — Labs API 最便宜的 difficulty 數據源）
3. **GSC API OAuth 設定要不要先做？**（強烈建議 — Health vertical 最關鍵數據源）
4. **`seo-audit-post` 的 LLM semantic check 用 Sonnet 還是 Haiku？**（推薦 Sonnet — semantic judgment quality 重要；單次 ~$0.02 成本可接受）
5. **`seo-optimize-draft` 是 standalone skill 還是 Brook compose 的 mode？**（推薦 standalone，但內部 call compose）
6. **`SEOContextV1` schema 要不要 phase 1 就凍結？**（建議要 — 一旦 enrich → compose 串起來，schema 變更代價高）
7. **要不要做 cron-driven 整站 GSC 體檢報告？**（推薦 phase 2 — 先把單篇 audit 做穩）
8. **Phase 1 要不要含 cannibalization 偵測？**（建議含 — 是 GSC 最高 ROI 的 insight，~50 行 Python）

---

## 7. 對 Plan agent / Architect 的 hand-off

下一步若進入 Plan / Architect phase：

- 此 doc **不算 ADR**，只是工具地景與選項枚舉
- ADR-00X (SEO solution architecture) 應該由桌機（已 align 後）撰寫
- ADR 的核心決策點：
  - Skill 家族切法（§5.2）
  - 數據源組合（§5.1）
  - `SEOContextV1` schema（§4.2）
  - Phase 1 / Phase 2 boundary（§6 第 7、8 點）
- 此 doc 的 §3.1 checklist 可作為 ADR appendix 的 audit rule baseline

---

## Sources（按 Section 順序）

### Section 1 — Tool ecosystem
- [DataForSEO Pricing](https://dataforseo.com/pricing)
- [DataForSEO OnPage API Pricing](https://dataforseo.com/pricing/on-page/onpage-api)
- [DataForSEO MCP Server (TS)](https://github.com/dataforseo/mcp-server-typescript)
- [DataForSEO Health/Wellness restriction](https://dataforseo.com/help-center/sv-cpc-cmp-with-dataforseo-api)
- [Ahrefs MCP Docs](https://docs.ahrefs.com/mcp/docs/introduction)
- [Ahrefs Pricing](https://ahrefs.com/blog/ahrefs-pricing/)
- [Semrush MCP KB](https://www.semrush.com/kb/1618-mcp)
- [Semrush API Pricing](https://thatmarketingbuddy.com/blog/semrush-api-pricing)
- [Surfer API Examples](https://docs.surferseo.com/en/articles/8201326-surfer-api-examples-of-use)
- [SerpApi](https://serpapi.com/)
- [Best SERP APIs 2026](https://scrapfly.io/blog/posts/google-serp-api-and-alternatives)
- [Mangools API](https://mangools.com/api)
- [Affordable SEO Tools 2026](https://webseotrends.com/blog/affordable-seo-tools/)

### Section 1.2 — Free official sources
- [GSC API Reference](https://developers.google.com/webmaster-tools/v1/searchanalytics/query)
- [PageSpeed Insights API](https://developers.google.com/speed/docs/insights/v5/get-started)
- [pyspeedinsights](https://pypi.org/project/pyspeedinsights/)
- [mcp-gsc (free)](https://github.com/AminForou/mcp-gsc)
- [suganthan-gsc-mcp 20 tools](https://suganthan.com/blog/google-search-console-mcp-server/)
- [Cannibalization detection w/ Python](https://www.jcchouinard.com/keyword-cannibalization-tool-with-python/)

### Section 1.3 — Open-source audit tools
- [SiteOne Crawler](https://github.com/janreges/siteone-crawler)
- [Unlighthouse](https://unlighthouse.dev/)
- [python-seo-analyzer](https://github.com/sethblack/python-seo-analyzer)
- [SEOmator (seo-skills/seo-audit-skill)](https://github.com/seo-skills/seo-audit-skill)
- [LibreCrawl](https://github.com/PhialsBasement/LibreCrawl)

### Section 1.4 — Claude Code skill prior art
- [AgriciDaniel/claude-seo](https://github.com/AgriciDaniel/claude-seo)
- [JeffLi1993/seo-audit-skill](https://github.com/JeffLi1993/seo-audit-skill)
- [aaron-he-zhu/seo-geo-claude-skills](https://github.com/aaron-he-zhu/seo-geo-claude-skills)
- [zubair-trabzada/geo-seo-claude](https://github.com/zubair-trabzada/geo-seo-claude)
- [VoltAgent/awesome-agent-skills](https://github.com/VoltAgent/awesome-agent-skills)

### Section 3 — On-page SEO checklist
- [Chillybin 2026 Checklist](https://www.chillybin.co/on-page-seo-checklist/)
- [CrawlWP 2026 Checklist](https://crawlwp.com/on-page-seo-checklist/)
- [Pentagon Top 10 Fixes 2026](https://www.pentame.com/blog/on-page-seo-checklist-2026-top-10-proven-ranking-fixes/)
- [SEOlogist Beginner's Guide](https://www.seologist.com/knowledge-sharing/beginners-guide-on-page-seo-2026/)

### Internal references
- [memory/claude/project_seo_solution_scope.md](../../memory/claude/project_seo_solution_scope.md)
- [memory/claude/feedback_search_skills_first.md](../../memory/claude/feedback_search_skills_first.md)
- [memory/claude/feedback_open_source_ready.md](../../memory/claude/feedback_open_source_ready.md)
- [memory/claude/project_brook_compose_merged.md](../../memory/claude/project_brook_compose_merged.md)
- [.claude/skills/keyword-research/SKILL.md](../../.claude/skills/keyword-research/SKILL.md)
- [agents/brook/compose.py](../../agents/brook/compose.py)
- [agents/usopp/publisher.py](../../agents/usopp/publisher.py)
- [agents/robin/kb_search.py](../../agents/robin/kb_search.py)
