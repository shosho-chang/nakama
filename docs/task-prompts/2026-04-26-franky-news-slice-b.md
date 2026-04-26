# Franky AI News Digest — Slice B（Anthropic HTML scrape + GitHub releases）

P9 六要素 task prompt。凍結於 2026-04-26（Slice A 已 merged + VPS deployed）。

---

## 1. 目標

補齊 Slice A 兩個 source 缺口：

1. **Anthropic /news**：Slice A smoke 已驗證 `/news/rss.xml` + 三個 alternate 全 404，必須 HTML scrape
2. **GitHub releases watchlist**：4 個 high-signal AI repo 的 release 串流（vLLM / llama.cpp / transformers / langchain）

合併進既有 daily digest 流（cron 06:30 台北已上線），不改 cron 行為，不改 prompt。

---

## 2. 範圍

| 路徑 | 動作 |
|---|---|
| `config/ai_news_sources.yaml` | **修改** — 加 4 個 GitHub atom feed（沿用既有 RSS path，0 code 改動） |
| `agents/franky/news/anthropic_html.py` | **新增** — Anthropic /news HTML scraper（httpx + bs4） |
| `agents/franky/news_digest.py` | **修改** — gather_candidates 後 merge `anthropic_html.gather_candidates()` |
| `tests/agents/franky/news/test_anthropic_html.py` | **新增** — fixture HTML + parse + 24h cutoff + dedupe + age filter |
| `tests/agents/franky/test_news_digest.py` | **修改** — 加 multi-source merge test |

**不碰**：
- `FeedConfig` schema（保持 RSS 純粹；HTML source 走獨立 module 不混 yaml type 多型）
- `prompts/franky/news_curate.md` / `news_score.md`（schema 對齊既有 candidate dict 即可）
- `agents/franky/__main__.py`（subcommand 已就位）
- `cron.conf`（已上線）
- `.env.example`（不引入新 env key）

---

## 3. 輸入（既有 building blocks）

- `agents/franky/news/official_blogs.gather_candidates` — schema reference（candidate dict 形狀）
- `shared/state.is_seen / mark_seen` — `SOURCE_KEY="ai_news_blog"` 共用（同個頁從不同 source 抓也算同一筆）
- `bs4`（pyproject.toml 已有 `beautifulsoup4>=4.12`）+ `dateutil`（`python-dateutil>=2.9`）+ `httpx`（已有）
- feedparser 透過 atom feed 自動解 GitHub releases，無需新 code

---

## 4. 輸出

### 4.1 yaml 加 4 個 GitHub atom feed

```yaml
  # GitHub releases — Slice B；用 atom feed，feedparser 直接吃
  - name: github_vllm
    url: https://github.com/vllm-project/vllm/releases.atom
    publisher: GitHub vllm-project/vllm
  - name: github_llama_cpp
    url: https://github.com/ggerganov/llama.cpp/releases.atom
    publisher: GitHub ggerganov/llama.cpp
  - name: github_transformers
    url: https://github.com/huggingface/transformers/releases.atom
    publisher: GitHub huggingface/transformers
  - name: github_langchain
    url: https://github.com/langchain-ai/langchain/releases.atom
    publisher: GitHub langchain-ai/langchain
```

選 4 個高信號 repo（不收 transformers/diffusers 全套，避免每週 10+ minor release 把 LLM curate prompt 衝爆）。後續若要加減，直接編輯 yaml。

### 4.2 `anthropic_html.gather_candidates`

簽名：

```python
def gather_candidates(
    *,
    now: datetime | None = None,
    max_age_hours: float = 24.0,
    skip_seen: bool = True,
    html_override: str | None = None,
) -> list[dict]:
```

行為：
1. `httpx.get(NEWS_URL)` 用 Chrome UA、follow_redirects、timeout=30；fail → log warning + 回 `[]`
2. BeautifulSoup parse HTML，找所有 `<a href="/news/SLUG">` anchor（regex `/news/([^/?#]+)` match）
3. 每個 anchor 取 inner `<h2>`（title）、`<time>`（visible text e.g. "Apr 16, 2026"）、`<p>`（summary）
4. 用 `dateutil.parser.parse` 解 date string；無 TZ 時補 UTC（Anthropic 沒寫 datetime= 屬性）
5. 24h cutoff（同 official_blogs）+ scout_seen dedupe（same `SOURCE_KEY="ai_news_blog"`）
6. `item_id = f"anthropic-news-{slug}"`（用 slug 而非 entry.id 因為沒 RSS GUID）
7. `feed_name = "anthropic_news_html"`，`publisher = "Anthropic"`
8. `html_override` 給 test 注入 fixture HTML 用

candidate dict schema 對齊 official_blogs（同 8 個 key），讓 `news_digest.py` merge 不需要轉換層。

### 4.3 `news_digest.py` merge

```python
# In NewsDigestPipeline.run, after RSS gather_candidates:
from agents.franky.news import anthropic_html
candidates = gather_candidates(self.feeds, skip_seen=not self.dry_run)
candidates += anthropic_html.gather_candidates(skip_seen=not self.dry_run)
```

排序由既有 `candidates.sort(...)` 處理（Anthropic merge 完一起 sort）。但 official_blogs 內也 sort 過 — 為了避免重複 sort 邏輯，merge 後 main pipeline 再排一次（small overhead，N < 100）。

---

## 5. 驗收（Definition of Done）

- [ ] `python -m agents.franky news --dry-run` 跑通：log 印出 RSS feed × 8 + anthropic_html + GitHub releases × 4 各抓到幾筆
- [ ] Anthropic 的 fresh entry（24h 內）在 candidates 出現
- [ ] GitHub releases atom feed 過 feedparser，candidate schema 不破
- [ ] 一個 source 失敗（network / parse）log warning 但其他 source 照跑
- [ ] `pytest tests/agents/franky/` 全綠（既有 + 新測試）
- [ ] full suite 不退步
- [ ] `ruff check` + `ruff format --check` 綠

---

## 6. 邊界（明確不做）

- ❌ FeedConfig 加 type 欄位 — 為一個 HTML source 抽多型 over-engineer，Slice C/D 真要加 vLLM/Meta HTML 再改
- ❌ `shared/web_scraper.py` 改 public API（`_fetch_html` 保持 module-private）— Anthropic scraper 自己 httpx
- ❌ Trafilatura 抓 Anthropic 全文 — list view 只取 title/date/summary，足夠 LLM curate
- ❌ GitHub release notes 全文抓 — atom feed `<content>` 已含 markdown summary
- ❌ 新增 env key — 不需要 GH_TOKEN（GitHub releases atom 是 public 端點）
- ❌ Reddit / X 社群熱度 — Slice C（已砍，需 OAuth）
- ❌ 改 prompt — schema 對齊就夠

---

## 7. 已知 finding（凍結時記錄）

### Anthropic /news HTML 結構（2026-04-26 fetch）

- **NextJS SSR**：HTML 含完整 server-rendered article list（不需 JS render）
- **Class names hashed**（CSS Modules）：不依賴 class，用 tag/structure 解析
- **沒 datetime= 屬性**：`<time>` 只有 visible text "Apr 16, 2026"，需 dateutil parse
- **anchor 唯一性**：同一篇 article 在 hero + list 重複出現 → 用 slug `set` dedupe
- **首頁 HTML ~358KB**，11 unique slugs，15 time elements

### GitHub releases atom feed schema（feedparser 已驗）

- entry.id = `tag:github.com,2008:Repository/.../v1.2.3` （unique，可當 dedupe key）
- entry.title = "v1.2.3"（不含 repo name）— 顯示時靠 `feed.publisher` 補
- entry.summary = release notes markdown（feedparser 已 strip 部分 HTML）
- entry.published_parsed = struct_time UTC

---

## 8. 部署（Slice A 已上線，Slice B 不需要）

- VPS cron 不變（`30 6 * * * python -m agents.franky news` 已就位）
- merge 後修修 VPS `git pull` + 手動 `python -m agents.franky news --dry-run` smoke 驗 log
