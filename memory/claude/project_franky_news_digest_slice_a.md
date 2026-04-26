---
name: Franky AI 生態情報 daily digest — Slice A
description: PR #171 merged 2026-04-26 + VPS deployed (cron 06:30) — 8 個官方 RSS + Anthropic 沒 RSS Slice B 補；Slice B/C/D backlog
type: project
created: 2026-04-26
originSessionId: 393bd1a7-5ed2-4fb4-af4f-818b26131b3a
---
Franky 加每日 06:30 台北 AI 生態情報 digest。Slice A 已上線：抓官方 blog RSS → LLM curate 5-8 條精選 → score 過 pick filter → 寫 vault `KB/Wiki/Digests/AI/{date}.md` + Slack DM 推給修修。

PR #171 squash merged `4c12abd` 2026-04-26。VPS git pull + cron 加好。

**Why:** 修修要每天掌握 AI 生態（Anthropic / OpenAI / Google blog + 工具 update），但不想自己刷 Twitter / Reddit。Franky 角色「船的進化」延伸到「AI 工具情報」，跟既有「系統健康」職責並行（不衝突）。

**How to apply:**
- 修 prompt 在 `prompts/franky/news_curate.md` + `news_score.md`
- 加 source 在 `config/ai_news_sources.yaml`（feedparser 解析，bozo / 404 graceful skip）
- 結構沿用 `agents/robin/pubmed_digest.py` pattern
- 設計 doc：`docs/task-prompts/2026-04-26-franky-news-slice-a.md`

## 8 個 active RSS（VPS 跑通驗證）

| Feed | URL | Daily entries 量 |
|---|---|---|
| openai_news | openai.com/news/rss.xml | 918 historical |
| google_research | research.google/blog/rss/ | 100 |
| deepmind | deepmind.google/blog/rss.xml | 100 |
| huggingface | huggingface.co/blog/feed.xml | 770 |
| simon_willison | simonwillison.net/atom/everything/ | 30 |
| latent_space | www.latent.space/feed | 20 |
| langchain_changelog | changelog.langchain.com/feed.xml | 178 |
| together_ai | www.together.ai/blog/rss.xml | 100 |

## 試過 broken 的（task prompt §7 全紀錄）

- `anthropic.com/news/rss.xml` → **404** + `/feed`、`/research/rss.xml` 也 404 → **Anthropic 沒官方 RSS**，必須 HTML scrape `/news` 解 → Slice B backlog
- `ai.meta.com/blog/rss/` → malformed（回 Facebook 反爬 HTML）
- `blog.vllm.ai/feed.xml` → text/html，vLLM 沒 RSS
- `blog.langchain.dev/rss/` → malformed → 換 changelog feed 替代

## VPS 部署

```
crontab line: 30 6 * * *  cd /home/nakama && /usr/bin/python3 -m agents.franky news >> /var/log/nakama/franky-news.log 2>&1
```

cron backup at `/tmp/cron.bak.20260426-*` on VPS（自動 timestamped）。

不引入新 env key — 全部沿用既有 `ANTHROPIC_API_KEY` / `SLACK_FRANKY_BOT_TOKEN` / `SLACK_USER_ID_SHOSHO`。

## 三個 mode

```bash
python -m agents.franky news               # full path：vault + Slack DM + state.db
python -m agents.franky news --no-publish  # 寫 vault 不送 Slack（dev 驗 vault）
python -m agents.franky news --dry-run     # 不寫不送（純 log，state.db 也不污染）
```

## 後續 Slice 規劃

| Slice | 內容 | 狀態 |
|---|---|---|
| A | 官方 blog RSS 8 source | ✅ merged + VPS deployed |
| B | Anthropic `/news` HTML scrape + 4 個 GitHub releases atom feed | ✅ merged (PR #176)；VPS pull + smoke 修修手動 |
| ~~C~~ | ~~Reddit + X~~ | **砍**（Reddit VPS IP 封 + dev portal 變動；X 訊號弱）|
| D（如需）| X DDG site search 補社群熱度 | 看 A/B 跑一陣子覺得缺再開 |

## 已知 finding

- **VPS LLM score 比本機嚴**：同樣 3 candidates，本機 Sonnet 給 pick=true 兩條，VPS 給 pick=false 全部。Temperature randomness，不是 bug。production 上 8 feed 每天會有 5-30 candidates，picks 自然出現。
- **24h cutoff 配 cron 06:30**：VPS scout_seen 按 source key `ai_news_blog` 隔離，不會跟 PubMed 衝突。
