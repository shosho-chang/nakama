# Cost Estimation

## Per-Run Cost Formula

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
| With auto-translate | ~$0.06 |
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
| **Total** | **~30-60s** |

## Budget Planning

- **Single run**: negligible cost; present as "~$0.05, 30-60s"
- **Heavy user** (10 runs/day): ~$0.50-0.80/day, well within any monthly
  Anthropic budget
- **Scheduled agent** (Zoro cron 06:00 × 7 days × 1 topic): ~$0.40/week

## When to Re-Estimate

The numbers above assume Sonnet 4.x pricing ($3 input / $15 output per 1M
tokens). If the API pricing changes or the skill switches to a different
model, update this file.

To get a real-time cost readout, run the pipeline once with
`--json-out debug.json` and inspect the Claude usage records in server logs.
