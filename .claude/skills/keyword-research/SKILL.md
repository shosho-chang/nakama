---
name: keyword-research
description: >
  Bilingual (zh-TW + en) keyword research for Health & Wellness content —
  YouTube + Google Trends + Autocomplete + Reddit + Twitter, synthesized by
  Claude into core keywords, cross-language trend gaps, and title seeds for
  YouTube / Blog. Writes a markdown report with a structured frontmatter block
  consumable by downstream skills (Brook compose, SEO audit). Use this skill
  when the user says "關鍵字研究 / 關鍵字分析 / keyword research / 做個關鍵字
  / 幫我查 <topic> 的關鍵字", or gives a topic and asks for YouTube/blog title
  ideas backed by real data. Also trigger when the user wants to scout whether
  a topic has rising search interest, or to find English-only trends not yet
  covered in Chinese.
---

# Keyword Research — Bilingual Trend & Title Discovery

You are the interactive wrapper for the Nakama Zoro keyword research pipeline
(`scripts/run_keyword_research.py`). Your job is to guide the user from a raw
topic to a markdown report with core keywords, cross-language trend gaps, and
title seeds — making judgment calls on topic clarification, scope, and cost
so the user doesn't have to think about CLI flags.

You do NOT re-implement the pipeline. You shell out to
`scripts/run_keyword_research.py` and surface its results back to the user.

## When to Use This Skill

Trigger on intent like:
- "幫我做 <topic> 的關鍵字研究"
- "關鍵字分析 <topic>"
- "keyword research for <topic>"
- "幫我查 <topic> 的趨勢 / 看有沒有人在搜"
- "找 YouTube 標題 for <topic>"
- "blog SEO 想寫 <topic>，幫我做功課"
- "<topic> 在英文圈紅了嗎？中文圈跟上了嗎？"

Do NOT trigger for:
- Pure translation tasks ("translate X to English")
- Writing the actual article (that's `article-compose`)
- Ingesting an existing article into KB (that's `kb-ingest`)
- SEO audit of existing published posts (that's a future skill, not this one)

## Workflow Overview

The pipeline has 6 steps and 2 mandatory confirmation points (topic
clarification + final cost go/no-go). Middle steps can be skipped in **fast
mode** when the user says "full pipeline" / "用 default" / "快速跑" / "go".

```
Step 1. Parse intent + extract topic
Step 2. Topic clarification                            [CONFIRM #1]
Step 3. Scope selection (content_type + en_topic hint)
Step 4. Cost + time estimate                           [CONFIRM #2]
Step 5. Invoke run_keyword_research.py (stream output)
Step 6. Summary + hand-off hint for downstream skills
```

---

## Step 1: Parse Intent

From the user's message, extract:
- `topic` — the Chinese topic (e.g. "間歇性斷食", "HRV 訓練", "鎂補充劑")
- `content_type` (optional) — "youtube" or "blog" (default: "youtube")
- `en_topic` (optional) — user-provided English hint (auto-translated if absent)
- `out_path` (optional) — where to write the report
- Fast-mode signals ("用 default" / "full pipeline" / "go")

If `topic` is not clear or empty, ask the user explicitly. Do NOT guess.

## Step 2: Topic Clarification (CONFIRM #1, never skip)

Health & Wellness topics often have ambiguity that changes the entire result.
Before invoking the pipeline, confirm:

**Scope check:**
- "間歇性斷食" — broad overview, or a specific protocol (16:8 / 5:2 / OMAD)?
- "睡眠" — sleep quality generally, or a specific issue (insomnia / shift work)?
- "鎂" — general supplementation, or a specific use case (sleep / muscle)?

**Audience check:**
- 一般大眾（科普）vs. 深度讀者（研究/臨床）
- 台灣本地 vs. 華語通用

Present a compact confirmation:

```
關鍵字研究：「<topic>」
  英文對譯：<en_topic or "自動翻譯">
  內容類型：<content_type> 標題優化
  範圍：<broad / narrow + specific angle>

確認嗎？或想收窄到某個角度？
```

Even in fast mode, show this and wait for a brief confirmation ("ok" / "go" /
"對" counts). If the user narrows the scope, incorporate it into `topic`
(e.g., "間歇性斷食" → "間歇性斷食 16:8").

## Step 3: Scope Selection

Two knobs:

**content_type** — determines title-generation prompt:
- `youtube` (default) — 55-char titles with curiosity gap + emotional triggers
- `blog` — 60-80-char titles with long-tail keywords + SEO structure

In fast mode: default to `youtube` unless the user mentioned "blog" / "部落格" /
"文章" / "SEO" explicitly.

**en_topic** — English equivalent for parallel English data collection:
- If user provided it, use as-is
- If omitted, the pipeline auto-translates via Claude (adds ~$0.001, ~2s)
- If topic is already English, pass it as `en_topic` and set the zh topic to a
  Taiwan-localized translation (don't send English as the Chinese query — it
  pollutes Chinese-side Google Trends data)

Skip presenting this step in fast mode; just use the resolved values.

## Step 4: Cost + Time Estimate (CONFIRM #2, never skip)

Read `references/cost-estimation.md` for the measured baseline + formula.

Quick summary (from v1.1 N=1 measurement, `dffa1e7`, 2026-04-25):
- Parallel data collection: ~15-30s (10 concurrent sources, 15s timeout each)
- Claude synthesis: ~10-15s (Sonnet-level, ~3-4K output tokens)
- Wall time: **~45s measured** (range 30-60s)
- API cost: **$0.05 measured** (input 2.6K + output 2.6K → $0.0468 at Sonnet rates)
- Range envelope: $0.03-0.08 depending on data volume

External API calls (rate-limit awareness — not dollar cost):
- YouTube Data API v3 (quota: 10K units/day, each search costs 100 units → ~100 runs/day ceiling)
- Google Trends via trendspy (IP-rate-limited, ~100 req/day soft limit)
- Reddit public JSON (no key, gentle)
- DuckDuckGo for Twitter proxy (no key)

Present to the user:

```
預估：
  時間：~45s（量測自 v1.1 N=1，範圍 30-60s）
  成本：$0.05（量測自 v1.1 N=1，CLI 結尾會印實際數字）
  輸出：<out_path>
確認開跑？
```

Accept: "確認" / "go" / "跑吧" / "yes" / "ok" / "對".
Reject: "不" / "改一下" / "取消" → return to Step 3 for scope adjustment.

## Step 5: Invoke run_keyword_research.py

Build the command:

```bash
python scripts/run_keyword_research.py "<topic>" \
    [--en-topic "<en_topic>"] \
    [--content-type youtube|blog] \
    [--out "<out_path>"] \
    [--json-out "<json_path>"]
```

Run with `Bash`. Default timeout (120s) is usually fine; bump to 180000ms if
you see historical runs near the ceiling.

While running, do NOT narrate — stdout/stderr will stream progress lines
(`[keyword-research] 執行中…` etc.). Only interrupt on exceptions.

On failure (non-zero exit), read `references/error-recovery.md` and present
the error context + suggested re-run. Do NOT auto-retry; the user decides.

Common failures:
- All data sources fail (rare; usually network) → RuntimeError from
  `research_keywords`, exit code 2
- Claude API error (quota / key missing) → propagates as exception
- One or two source timeouts → pipeline proceeds with remaining sources
  (check `sources_failed` in frontmatter post-hoc)

## Step 6: Summary + Hand-off Hint

After successful run, read the output markdown file and extract:
- Sources used / failed (from frontmatter `sources_used` / `sources_failed`)
- Core keywords count + top 3 highest-opportunity ones
- Trend gaps count + the most interesting gap
- YouTube/Blog title count

Present a structured summary. The CLI itself prints `完成！耗時 XX.Xs` plus a
**measured cost block** (input/output tokens + $) — keep that block visible
to the user; do not paraphrase it. Wrap it with the qualitative summary
below:

```
完成！耗時 XX.Xs   ← from CLI

輸出：<out_path>

資料來源：N/10 成功 (失敗：<names>)

Top 機會關鍵字：
  1. <keyword> — <reason (short)>
  2. <keyword> — <reason>
  3. <keyword> — <reason>

跨語言趨勢缺口 (<count>)：
  - <first_gap_topic>：<opportunity (short)>

標題種子：YouTube <M> 個 / Blog <K> 個（看報告 <out_path>）

成本（實測）：    ← printed by CLI; surface verbatim
  Claude API call(s)：N 次
    input tokens   : ...
    output tokens  : ...  (Claude 4.x 把 extended thinking 計入 output)
  $ 換算：$0.0XXX

下一步建議：
  → 寫文章：用 Brook `article-compose` skill（會自動吃這份 frontmatter）
  → 發佈前 SEO 體檢：（未來 SEO skill，尚未實作）
  → 想再研究相關主題：再跑一次 `keyword-research`
```

If any sources failed, mention which ones and whether the shortfall affected
confidence (e.g., "Google Trends 失敗，core keywords 仍有 YouTube + Autocomplete
支撐，但 search_volume 估算信心略降").

---

## Fast Mode Behavior

Triggered by phrases: "用 default" / "full pipeline" / "快速跑" /
"go with defaults" / "just run it".

- Step 2 (topic clarification) → **still shown, still requires confirmation**
  (cheap insurance against garbage-in)
- Step 3 (scope) → skipped, defaults resolved from context
- Step 4 (cost) → **still shown, still requires confirmation**
  (the never-skip gate)

The two "never skip" gates are: topic clarity + final cost go/no-go.

## Output Contract (Downstream Composability)

The markdown report has two layers:

**Frontmatter** (machine-readable, YAML):
```yaml
---
type: keyword-research
topic: 間歇性斷食
topic_en: intermittent fasting
content_type: youtube
generated_at: 2026-04-18T12:34:56+00:00
sources_used: [youtube_zh, youtube_en, trends_zh, ...]
sources_failed: []
core_keywords:
  - keyword: 16:8 斷食
    keyword_en: 16:8 fasting
    search_volume: high
    competition: medium
    opportunity: high
    source: both
    reason: ...
trend_gaps:
  - topic: Fasting mimicking diet
    en_signal: ...
    zh_status: ...
    opportunity: ...
youtube_title_seeds: [...]
blog_title_seeds: [...]
---
```

**Body** (human-readable, Markdown): strategy summary, keyword table, trend
gaps, title seeds, reference videos/social posts, source status.

Downstream skills should parse the frontmatter to reuse structured data
without re-invoking the pipeline. See `references/output-contract.md` for
the full schema.

## Open-Source Friendliness

This skill is part of the Nakama repo and intended to be extractable. Design
constraints:

- No hardcoded personal paths. Default output is `./keyword-research-<topic>-<ts>.md`
  in CWD; user can override with `--out`.
- Pipeline data sources are in `agents/zoro/*_api.py`. An open-source user
  needs `YOUTUBE_API_KEY` (Google Cloud free tier) and `ANTHROPIC_API_KEY` to
  run — other sources (Trends, Autocomplete, Reddit, Twitter-via-DDG) are
  keyless.
- Taiwan-specific terminology lives in `references/taiwan-health-terminology.md`
  — other users can replace it with their locale's glossary.
- The skill has no LifeOS / Obsidian dependency. Writing into a vault is the
  user's choice of `--out` path.

## References

All reference files in `references/` should be read when you need the detailed
template for each step:

| File | When to read |
|------|--------------|
| `pipeline-overview.md` | Before Step 5 (explain the 10-source collection) |
| `data-sources.md` | Step 4 (rate limits, credentials, failure modes) |
| `output-contract.md` | Step 6 (frontmatter schema for hand-off) |
| `cost-estimation.md` | Step 4 (token + API cost formula) |
| `taiwan-health-terminology.md` | Step 2-3 (topic disambiguation in zh-TW) |
| `error-recovery.md` | Step 5 on failure (error → suggested re-run) |
