---
name: Franky news Slice B merged — Anthropic HTML + GitHub releases
description: PR #176 merged 2026-04-26：Anthropic /news HTML scraper（三種卡片版型）+ 4 個 GitHub releases atom feed；reviewer-flagged hardening 同 PR 補上；VPS deploy 修修手動
type: project
created: 2026-04-26
originSessionId: a13fa7b6-6e0b-45b2-b916-939e6ce57898
---
PR #176 squash merged 2026-04-26 為 commit `a91dc38`。Slice A 留下兩個 source 缺口都補齊。

## What

- `agents/franky/news/anthropic_html.py`（new）— httpx + bs4 抓 https://www.anthropic.com/news；schema 對齊 official_blogs candidate dict
- `config/ai_news_sources.yaml` — 加 4 個 GitHub atom feed（vllm-project/vllm、ggerganov/llama.cpp、huggingface/transformers、langchain-ai/langchain），沿用 RSS path 0 code 改動
- `agents/franky/news_digest.py` — gather_candidates() 後 try/except 包 anthropic_html.gather_candidates()；merge 後跨 source 重新 sort by recency
- `tests/agents/franky/news/test_anthropic_html.py` — 22 tests
- `docs/archive/task-prompts/2026-04-26-franky-news-slice-b.md` — P9 task prompt 凍結

## Why（Slice B = Slice A 缺口補完）

Slice A smoke 已驗 Anthropic 沒官方 RSS（`/news/rss.xml` + 三個 alternate 全 404），修修最在乎的 publisher 必須 HTML scrape。GitHub releases 是 atom feed，沿用 feedparser 0 改動就能加。

## Anthropic /news 三種卡片版型（live 2026-04-26 fetch）

CSS class 全 hashed（CSS Modules），靠 tag/structure 解析：

1. **Hero / FeaturedGrid 主卡**：`<a><h2>Title</h2><time>...</time><p>Body</p></a>`
2. **FeaturedGrid sideLink**：`<a><div><time>...</time></div><h4>Title</h4><p>Body</p></a>`
3. **PublicationList list**：`<a><div><time>...</time><span>Category</span></div><span class="...title">Title</span></a>`

`_extract_title()` 先找任何 `<h1>–<h6>`（覆蓋版型 1, 2），fallback 找最長 substantive `<span>`（>= 20 chars，覆蓋版型 3，避開短 category 標籤）。第一版只認 h2 → dry-run smoke parsed 1 articles instead of 13，被抓到才修。

## 4 個 GitHub repo（避免衝爆 curate）

不收 transformers/diffusers 全套（每週 10+ minor release 會吃爆 curate prompt N→K）。後續若要加減，直接編輯 yaml。

## Reviewer-flagged hardening（同 PR 補上）

`news_digest.py:96` 原本 `anthropic_html.gather_candidates()` 沒包 try/except。Slice A 的 official_blogs per-feed 已自我吞 exception，但 merge layer 沒 wrap → 新 scraper 拋未預期 exception 會帶倒整個 digest（含 RSS path）。

修法：merge layer 包 try/except + log warning + fall through to []，跟 official_blogs per-feed isolation 對齊。同步把 `test_run_anthropic_html_failure_does_not_block_rss` 改成真 raise exception（原本只測 [] return，test 名 overpromise）。

Reviewer 是 general-purpose Agent dispatch（prompt 明確「Do NOT post a comment to GitHub」），結論 APPROVE / no blocker。

## VPS 部署（2026-04-26 17:43 台北 Smoke 完成）

```bash
ssh nakama-vps && cd /home/nakama && git pull  # already up to date
pip3 install beautifulsoup4  # VPS system python 缺，requirements.txt 有寫
python3 -m agents.franky news --dry-run  # 全綠
```

Dry-run 結果：
- 8 RSS + 4 GitHub atom + Anthropic HTML 全部 fetch 成功
- **Anthropic parsed 13 articles**（VPS IP 沒被 CDN 擋，最重要風險點解除）
- candidates 8 條（rss=8, anthropic_html=0）— anthropic 13 篇都 > 24h 被 age filter 掉，正常
- score pick=false 過濾掉 5 條 → 「無精選入選」（純 age + LLM 判斷，dedupe DB 為空）

## 待 verify（明早）

- syslog 顯示 `franky news` cron 從未執行過 — 今天首次部署，下次 06:30 台北是明早。第一次 production 完看 `/var/log/nakama/franky-news.log` 確認 Anthropic parsed > 0、GitHub releases 沒淹沒 curate
- VPS python 是 system /usr/bin/python3.12 不是 venv；新 dep 用 `pip3 install`。requirements.txt 寫了 bs4 但歷史上只 ingest path 用，這次首次走 franky news 才暴露未裝

## 下一步候選

- 若 GitHub releases 太吵（每天 6+ entry 直接吃 LLM curate），加 release minor/patch 過濾邏輯
- Anthropic 真上 RSS（持續查），可從 yaml 直接加，移除 `anthropic_html.py`
- Slice D（X DDG site search 補社群熱度）— 看 A/B 跑一陣子覺得缺再開
