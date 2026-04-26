# Franky AI News Digest — Slice A（官方 blog only）

P9 六要素 task prompt。凍結於 2026-04-26。Reddit / X / GitHub trending 屬於後續 slice，本 slice 不做。

---

## 1. 目標

幫 Franky 增加每日 06:30 跑的 AI 生態情報 digest：抓 10-12 個 AI 大廠官方 blog RSS、用 LLM curate 出 5-8 條當日值得知道的更新，寫成一份 vault digest 頁，並 Slack DM 推給修修。

讓修修 07:00 看 Nami Morning Brief 之前先掃過 AI 動態。

不做即時 push、不做社群熱度（Reddit/X 留 Slice C，需 Reddit OAuth setup）、不做 GitHub watchlist（Slice B）。

---

## 2. 範圍

| 路徑 | 動作 |
|---|---|
| `config/ai_news_sources.yaml` | **新增** — 10-12 個官方 RSS feed 種子清單 |
| `agents/franky/news/__init__.py` | **新增** — empty 包標記 |
| `agents/franky/news/official_blogs.py` | **新增** — RSS 抓取 + 24h 過濾 + scout_seen 去重 |
| `agents/franky/news_digest.py` | **新增** — `NewsDigestPipeline` 主流程（gather → curate → score → vault write + Slack DM） |
| `prompts/franky/news_curate.md` | **新增** — N → 5-8 篇精選 prompt |
| `prompts/franky/news_score.md` | **新增** — 單篇深度評分 prompt |
| `agents/franky/__main__.py` | **修改** — 加 `news` subcommand（含 `--dry-run` / `--no-publish`） |
| `cron.conf` | **修改** — 加 `30 6 * * * python -m agents.franky news` 一行 |
| `tests/agents/franky/test_news_digest.py` | **新增** — 單元測試（mock feedparser + LLM + Slack） |
| `tests/agents/franky/news/test_official_blogs.py` | **新增** — RSS 解析 / 過濾 / 去重 |
| `.env.example` | 不動 — Slice A 不引入新 env key |

**不碰**：
- `agents/franky/agent.py`（既有週報 entry 不動）
- `agents/franky/weekly_digest.py`（週報路徑不動）
- `agents/franky/slack_bot.py`（直接重用 `FrankySlackBot.from_env().post_plain`）
- `shared/state.py`（重用既有 `is_seen` / `mark_seen`，source key 用 `"ai_news_blog"` 隔離）
- vault 任何既有頁

---

## 3. 輸入（既有 building blocks，直接重用）

- `agents/robin/pubmed_digest.py` — 整套 daily digest pattern（feedparser → dedupe → curate → score → vault write + log + index 更新）。複製結構改 source 即可
- `agents/franky/weekly_digest.py` — `FrankySlackBot.from_env().post_plain(text, context=...)` Slack DM
- `shared/state.is_seen / mark_seen` — `source="ai_news_blog"`，`item_id` 用 entry.id（RSS GUID）或 url SHA-256
- `shared/obsidian_writer.write_page / append_to_file` — vault 寫 + KB/log.md
- `shared/llm.ask` — Claude Sonnet 4.6 預設
- `shared/prompt_loader.load_prompt("franky", "news_curate", **kwargs)` — 自動注入 shared partials
- `shared/log.get_logger`
- `shared/config.get_vault_path` — `KB/Wiki/Digests/AI/{date}.md` 寫入點

---

## 4. 輸出

### 4.1 RSS source 清單（`config/ai_news_sources.yaml`）

```yaml
# Franky AI news digest — 官方 blog RSS sources（Slice A）
#
# 加 source 直接 append。失敗 feed 會 log warning 但不擋整批。
# scout_seen source key = "ai_news_blog"，dedup 用 entry.id (RSS GUID)。

feeds:
  - name: anthropic_news
    url: https://www.anthropic.com/news/rss.xml
    publisher: Anthropic
  - name: openai_news
    url: https://openai.com/news/rss.xml
    publisher: OpenAI
  - name: google_research
    url: https://research.google/blog/rss/
    publisher: Google Research
  - name: deepmind
    url: https://deepmind.google/blog/rss.xml
    publisher: Google DeepMind
  - name: huggingface
    url: https://huggingface.co/blog/feed.xml
    publisher: Hugging Face
  - name: meta_ai
    url: https://ai.meta.com/blog/rss/
    publisher: Meta AI
  - name: vllm
    url: https://blog.vllm.ai/feed.xml
    publisher: vLLM
  - name: langchain
    url: https://blog.langchain.dev/rss/
    publisher: LangChain
  - name: simon_willison
    url: https://simonwillison.net/atom/everything/
    publisher: Simon Willison
  - name: latent_space
    url: https://www.latent.space/feed
    publisher: Latent Space
  - name: stratechery
    url: https://stratechery.com/feed/
    publisher: Stratechery (paywalled, headlines only)
```

URL 失效在 implement 時用 `feedparser.parse(url).bozo` 偵測，console warning 但不抓 throw。

### 4.2 Vault digest 頁

路徑：`KB/Wiki/Digests/AI/2026-04-26.md`（Asia/Taipei TZ；沿用 `pubmed_digest._write_digest_page` pattern）

Frontmatter：

```yaml
---
date: 2026-04-26
created_by: franky
source: ai_news_blog
total_candidates_fresh: 23
selected_count: 7
publishers_covered: [Anthropic, OpenAI, Google DeepMind, Hugging Face]
type: digest
---
```

Body：editor note + N 條精選，每條 verdict / why_it_matters / key_finding / link。

### 4.3 Slack DM

走 `FrankySlackBot.from_env().post_plain(text, context="news_digest")`，內容：

```
🤖 Franky AI Daily — 2026-04-26

候選 23 / 精選 7

1. [Anthropic] Claude 4.7 1M context 釋出 — ...
2. [OpenAI] GPT-5.2 推 ...
...

→ 完整 digest：[KB/Wiki/Digests/AI/2026-04-26](obsidian://...)
op=op_xxxxxxxx
```

### 4.4 KB log + index

- `KB/log.md` append 一行 `franky: AI news digest written → 2026-04-26.md (7 picks)`
- `KB/index.md` append 一行 `[[Digests/AI/2026-04-26|AI 動態 2026-04-26]] — 7 條精選`（fallback 略過如 index.md 不存在）

---

## 5. 驗收（Definition of Done）

- [ ] `python -m agents.franky news --dry-run` 在本機跑通：log 印出每個 feed 抓到幾筆、curate 入選幾筆、估 LLM tokens；不寫 vault 不送 Slack
- [ ] `python -m agents.franky news --no-publish` 寫 vault 但不送 Slack（給開發者驗 vault 寫入正確）
- [ ] `python -m agents.franky news` full path：vault 頁存在、Slack DM 收到（or stub log）
- [ ] 連跑兩次 — 第二次 dedupe 後 selected_count 顯著減少（fresh 數降為 0 時走 graceful path 不 crash）
- [ ] 一個 feed URL 故意失效時，log warning 但其他 feed 照跑
- [ ] LLM 回非 JSON 時 graceful：log error 跳該篇 score（不 crash 整批）— 沿用 `_parse_json` 容忍 markdown fence
- [ ] `pytest tests/agents/franky/test_news_digest.py tests/agents/franky/news/` 全綠
- [ ] `pytest tests/` 不退步
- [ ] `ruff check` + `ruff format --check` 綠
- [ ] cron.conf 加好一行（VPS 套用是修修手動 — 列在 PR test plan）

---

## 6. 邊界（明確不做）

- ❌ Reddit / X 社群熱度 — Slice C，需 Reddit OAuth setup
- ❌ GitHub watchlist / trending — Slice B
- ❌ Trafilatura 抓 blog 全文 — Slice A 只用 RSS abstract（feedparser 既有 `summary` 欄位足夠 LLM curate）
- ❌ 即時 push（突發新聞偵測）— 永遠不做（會吵）
- ❌ Nami Morning Brief 整合 — 等 Slice B/C 完成再考慮
- ❌ Bridge UI 顯示 — Slice A 純 vault + Slack；UI 是 follow-up
- ❌ 改 `weekly_digest.py` — 週報跟 daily news 分開，不做整合
- ❌ 引入新 env key — 全部走既有的 `ANTHROPIC_API_KEY` + `SLACK_FRANKY_BOT_TOKEN` + `SLACK_USER_ID_SHOSHO`
- ❌ 改 cron.conf 之外的 deploy artifact（systemd service 不需要，walk on cron）

---

## 7. 發現 issue（執行過程補寫）

### 2026-04-26 本機 dry-run smoke test 發現

10 個 seed feed 中 4 個無 RSS / RSS broken：

| Publisher | 原 URL | 結果 | 處理 |
|---|---|---|---|
| Anthropic | `/news/rss.xml` | 404 | **沒官方 RSS**（試了 `/feed`, `/research/rss.xml` 都 404）— 拿掉。Slice B follow-up 用 HTML scrape `/news` 頁解 |
| Meta AI | `/blog/rss/` | malformed XML（回 Facebook 反爬 HTML）| 拿掉 |
| vLLM | `/feed.xml` | text/html 不是 XML — vLLM blog 純 HTML 無 RSS | 拿掉 |
| LangChain blog | `/rss/` | malformed XML | 換成 `changelog.langchain.com/feed.xml`（changelog 比 blog 更實用）|

加入替代 source 補回容量：
- `together_ai` — `https://www.together.ai/blog/rss.xml` ✅ 有效
- （考慮過 arxiv cs.AI RSS 補 paper coverage，最後不加 — 每日 200+ paper 會把 curate prompt 衝爆；PubMed 既有流已涵蓋）

最終 8 feeds：openai / google_research / deepmind / huggingface / simon_willison / latent_space / langchain_changelog / together_ai

### Anthropic RSS 缺口 — Slice B follow-up

Anthropic 沒官方 RSS 是這個方案最大缺口（修修最在乎 Anthropic update）。
Slice B 加 GitHub watchlist 時，**順便加一個 HTML scrape source**：
- 用 `shared/web_scraper.py` 三層 fallback 抓 `https://www.anthropic.com/news` 主頁
- parse `<a href="/news/...">` 連結 + 各篇 `<time>` 取 published date
- 24h cutoff + scout_seen 去重，schema 對齊既有 candidate dict

工程量：~50 行新 module `agents/franky/news/anthropic_html.py`，配個 unit test。

### 2026-04-26 本機 stdout fix

Windows cp1252 stdout 印中文 log crash（feedback_windows_stdout_utf8）。
`agents/franky/__main__.py` 頂層加 stdout/stderr UTF-8 reconfigure 解決。
VPS Linux 不受影響，但本機 dev 必須。
