# Data Sources — Credentials, Limits, Failure Modes

Per-source quirks that affect skill behavior and failure interpretation.

## YouTube Data API v3

- **Credential**: `YOUTUBE_API_KEY` env var (Google Cloud free tier)
- **Quota**: 10,000 units/day. Each `search.list` call = 100 units → **~50
  runs/day max** (2 searches per run, zh + en)
- **Failure mode**: quota exceeded → 403; key missing → skipped with warning
- **In output**: `sources_used=[youtube_zh, youtube_en]` on success

If the user runs this skill heavily (>30 times/day), warn about quota. Upgrade
path: bill the Google Cloud project or request quota increase.

## Google Trends (trendspy)

- **Credential**: none
- **Rate limit**: IP-based, soft ~100 requests/day before Google returns 429 /
  302 redirects. trendspy has built-in retry with exponential backoff.
- **zh-TW caveat**: trendspy truncates `language="zh-TW"` to `"zh"` (library
  limitation). Related queries may include some Simplified Chinese terms.
  Claude compensates via `keyword 用繁體中文` in the prompt.
- **Failure mode**: quota exceeded → `TrendsQuotaExceededError` → collector
  returns `{}` → `sources_failed` includes `trends_zh` / `trends_en`

If both Trends sources fail frequently, suggest: (a) run from a different IP,
(b) wait 1-2 hours, (c) upgrade to Trends MCP / DataForSEO (future work).

## YouTube Autocomplete

- **Credential**: none
- **Method**: direct HTTP to YouTube's autocomplete endpoint
- **Failure mode**: rare; usually connection error
- **Signal value**: high — reflects actual user search behavior

## Twitter (via DuckDuckGo proxy)

- **Credential**: none (no Twitter API v2 key needed)
- **Method**: DuckDuckGo site search for `site:twitter.com <topic>`
- **Failure mode**: DDG rate-limit → collector returns `{}`
- **Signal value**: medium (stale, partial, no engagement metrics for recent
  tweets)

## Reddit Public JSON

- **Credential**: none
- **Method**: `https://www.reddit.com/r/<sub>/search.json?q=<topic>`
- **Health-focused subreddits**: defined in `agents/zoro/reddit_api.py`
- **Failure mode**: 429 (rate-limited) → collector returns `{}`; rare
- **Signal value**: high — detailed community discussion, upvote score as
  quality signal

## When `sources_failed` is Non-Empty

Report the names and degrade confidence signals:

- If `trends_*` failed → search_volume estimates rely on YouTube views only
  (less reliable for niche topics)
- If `youtube_*` failed → title-generation loses style references;
  title_seeds quality drops
- If `reddit_*` failed → less community-language signal; core_keywords may
  miss colloquial terms
- If both `twitter_*` + `reddit_*` failed → no social buzz data; trend_gaps
  may underrepresent emerging topics

Present this as a confidence note, not a failure. The synthesis still
produces usable output with 5-7 of 10 sources.

## Rate-Limit-Aware Scheduling

If Zoro is scheduled to run (cron 06:00 in production), YouTube quota resets
at midnight Pacific Time. Consecutive runs in the same hour increase trendspy
429 probability — space manual invocations by ~10 minutes.
