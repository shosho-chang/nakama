# Cost Estimation

> v1.1 onwards (2026-04-25, dffa1e7), the CLI emits a **measured** cost block
> at the end of every run. The formula and ranges below are kept for reference
> only — trust the live readout for real-world decisions.

## Measured (capability card N=X)

The skill conforms to the open-source-ready capability-card principle in
`memory/claude/feedback_open_source_ready.md` §8 — the costs below are
recorded from real CLI runs, not derived from a priori formulas.

### v1.1 — `dffa1e7`, 2026-04-25, **N=1**

Run: `深度睡眠`, content_type=youtube, en_topic auto-translated.
Sources active: **5/10** (Mac dev machine — no `YOUTUBE_API_KEY`, Google
Trends quota exceeded). A run with full sources will have larger input
prompts and somewhat higher synthesis cost.

| Field | Value |
|-------|------:|
| Claude API calls | 2 (translate + synthesis) |
| input tokens | 2,573 |
| output tokens | 2,606 |
| thinking tokens | (billed inside output for Claude Sonnet 4.x) |
| cache_read tokens | 0 |
| cache_write tokens | 0 |
| **$ USD** | **$0.0468** |
| wall time | 45.6 s |

When more samples accumulate, append rows above and quote the running mean
in the table below.

| Date | Sources active | Calls | input | output | $ USD | wall (s) |
|------|---------------:|------:|------:|-------:|------:|---------:|
| 2026-04-25 | 5/10 | 2 | 2,573 | 2,606 | 0.0468 | 45.6 |

## Per-Run Cost Formula (a priori, sanity check)

```
Total ≈ translate_cost + synthesis_cost + (0 from data sources)
```

### Translate Cost (~$0.001, skipped if --en-topic provided)

- Model: Claude Sonnet (via `ask_claude` default)
- Prompt: ~30 input tokens
- Response: ~10 output tokens
- Formula: `input × $3/1M + output × $15/1M` ≈ **$0.0002**
- Actual ceiling: `max_tokens=100` → worst case $0.001

### Synthesis Cost (~$0.03-0.08, main driver)

- Model: Claude Sonnet (via `ask_claude` default)
- Prompt: `prompts/zoro/keyword_research.md` interpolated with 10 data blocks
  - Typical size: ~3,000-8,000 input tokens (depends on collected data volume)
- Response: JSON with 8-12 keywords + 2-5 gaps + 20 title seeds + summary
  - Typical size: ~2,000-4,000 output tokens (`max_tokens=4096`)
- Formula:
  ```
  synthesis = 5000 × $3/1M + 3000 × $15/1M
            = $0.015 + $0.045
            = $0.06
  ```

### Data Source Cost

All free / free-tier:
- YouTube Data API: free tier (10K units/day, search=100 units/call)
- trendspy (Google Trends): free (IP-limited)
- YouTube autocomplete: free
- DuckDuckGo for Twitter: free
- Reddit JSON: free

## Per-Run Total

| Scenario | Cost |
|----------|------|
| With auto-translate, sparse sources (Mac dev N=1) | $0.05 |
| With auto-translate, all sources active (a priori) | ~$0.06 |
| With `--en-topic` (skip translate) | ~$0.06 |
| Worst case (large data blocks + 4K output) | ~$0.10 |
| Best case (sparse data, small output) | ~$0.03 |

## Time Estimate

| Phase | Time |
|-------|------|
| Auto-translate | ~2s |
| Parallel collection (10 sources, timeout 15s each) | ~10-20s |
| Claude synthesis | ~10-15s |
| Markdown write | <1s |
| **Total** | **~30-60s** (N=1: 45.6s) |

## Budget Planning

- **Single run**: ~$0.05, 30-60s (matches v1.1 N=1 measurement above)
- **Heavy user** (10 runs/day): ~$0.50/day, well within any monthly
  Anthropic budget
- **Scheduled agent** (Zoro cron 06:00 × 7 days × 1 topic): ~$0.35/week

## When to Re-Estimate

The numbers above assume Sonnet 4.x pricing ($3 input / $15 output per 1M
tokens). If the API pricing changes or the skill switches to a different
model, update both this file and the rate-card in
`scripts/run_keyword_research.py` (`_CLAUDE_RATE_USD_PER_1M`).

The CLI's measured block is authoritative — re-run once and append a row to
the measurement table above whenever a contributor wants a fresh baseline.
