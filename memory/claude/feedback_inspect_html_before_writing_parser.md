---
name: 寫 HTML scraper 前先實際 fetch raw HTML 看結構
description: 任何網站 scraper / parser 動手前先 httpx.get 看真實 HTML，別靠假設或記憶；同站常多版型卡片混合 + CSS Modules hashed class 不可靠
type: feedback
created: 2026-04-26
originSessionId: a13fa7b6-6e0b-45b2-b916-939e6ce57898
---
**規則：寫 HTML parser 前必須先實際 fetch raw HTML，並至少抽 3 個不同位置（hero / list / footer 之類）的 anchor 看結構。靠假設寫 parser 高機率漏掉版型。**

**Why:** 2026-04-26 寫 anthropic.com/news scraper 第一版只認 `<a><h2>...` 的 hero 卡，dry-run smoke 顯示 `parsed 1 articles` 而 raw regex 找到 11 unique slugs。實際看 HTML 才發現同頁面三種卡片版型混合：

1. **Hero / FeaturedGrid 主卡**：`<a><h2>Title</h2><time>...</time><p>Body</p></a>`
2. **FeaturedGrid sideLink**：`<a><div><time>...</time></div><h4>Title</h4><p>Body</p></a>`
3. **PublicationList list**：`<a><div><time>...</time><span>Category</span></div><span class="...title">Real Title</span></a>`

CSS class 全 hashed（CSS Modules `FeaturedGrid-module-scss-module__W1FydW__content`），刷新部署就會變 — 靠 class 解析必崩。

**How to apply:**

1. **先 raw fetch**：`httpx.get(url, headers={"User-Agent": chrome_ua}, timeout=30, follow_redirects=True)`
2. **看 link pattern**：`re.findall(r'href="([^"]+)"', html)` 看 anchor 規律
3. **看每種 link 的 surrounding 600 chars**：抽至少 3 個不同位置的 link（用 `re.finditer` + `html[start-30:end+600]`）
4. **靠 tag/structure 解析，不靠 class**（hashed class 必崩）
5. **Title 多版型 fallback**：先找 `<h1>–<h6>`，fallback 找最長 substantive `<span>`（避開短 category 標籤；threshold ~20 chars 經驗值 OK）
6. **Date parsing 不能假設 ISO**：`<time datetime="...">` 屬性可能不存在（Anthropic 沒），只有 visible text "Apr 16, 2026" → 用 `dateutil.parser.parse`，無 TZ 補 UTC
7. **dry-run smoke 必跑**：parsed N 跟 raw regex 找到的 link 數要對得上；不對就再看結構

## 反例（這次踩到）

寫成只認 `<a.find("h2")`：
- live HTML 13 anchors → `parsed 1`，dropped 12（PublicationList 全炸）
- unit test 用我自己造的 hero-only fixture → 全綠，但對不上現實
- 直到 dry-run smoke 看到 1 vs 13 才抓到

## 同類風險

- vLLM blog / Meta AI blog 等未來 HTML scrape candidate 都要先看
- WP / WooCommerce / 其他 site scraper 先看 raw HTML（cf publisher OA 全文模式）
- **不要相信 LLM 對「典型網站結構」的描述** — 都是現編，跟實際差距大
