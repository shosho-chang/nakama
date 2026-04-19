# Pipeline Overview

The keyword-research pipeline (`agents/zoro/keyword_research.py::research_keywords`)
runs 3 phases in sequence: translate → parallel collect → synthesize.

## Phase 1: Auto-Translate (optional, ~2s, ~$0.001)

If `en_topic` is not provided, Claude translates the Chinese topic to an
English equivalent (`max_tokens=100, temperature=0.0`). This runs BEFORE the
parallel collection so English-side sources can start with the right term.

Skip this phase by passing `--en-topic` explicitly.

## Phase 2: Parallel Collection (~15-30s)

Runs 10 concurrent data collectors via `ThreadPoolExecutor(max_workers=10)`
with a 15s timeout per source. Failures are tolerated — the pipeline
continues as long as at least one source returns data.

| Collector | Function | Source | Output shape |
|-----------|----------|--------|--------------|
| `youtube_zh` / `youtube_en` | `search_top_videos` | YouTube Data API v3 | `{top_videos, avg_views, common_words}` |
| `trends_zh` / `trends_en` | `get_trends` (trendspy) | Google Trends | `{related_top, related_rising, trend_direction}` |
| `autocomplete_zh` / `autocomplete_en` | `get_suggestions` | YouTube autocomplete | `{suggestions: [...]}` |
| `twitter_zh` / `twitter_en` | `search_recent_tweets` | DuckDuckGo → Twitter | `{tweets: [...]}` |
| `reddit_zh` / `reddit_en` | `search_reddit_posts` | Reddit public JSON | `{posts: [...]}` |

**Collectors return `{}` on failure** — the orchestrator treats this as
"source failed" and continues. Post-run `sources_failed` is populated with
failed collector names.

## Phase 3: Claude Synthesis (~10-15s, ~$0.03-0.07)

Formatted per-source text blocks are interpolated into
`prompts/zoro/keyword_research.md` and sent to Claude. Output is strict JSON
with:

- `core_keywords`: 8-12 keywords with search_volume / competition /
  opportunity / source / reason
- `trend_gaps`: 2-5 English-leading trends not yet in Chinese market
- `youtube_titles`: 10 titles (55-char, curiosity gap)
- `blog_titles`: 10 titles (60-80-char, long-tail SEO)
- `analysis_summary`: 2-3 sentences cross-language insight

## Failure Tolerance

- All 10 sources fail → `RuntimeError` raised → exit code 2 from CLI
- Some sources fail → pipeline proceeds; missing signals weaken synthesis but
  don't block output
- Claude API error → propagates as exception; no partial result

## Cost Dominance

~90% of per-run cost is Phase 3 (Claude synthesis). Data sources are free or
use free-tier API keys. See `cost-estimation.md` for the exact formula.
