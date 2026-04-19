# Error Recovery

Common failure modes and suggested re-run commands.

## Exit Code 2: All Data Sources Failed

```
[keyword-research] ERROR: 所有資料來源都失敗，無法進行關鍵字研究
```

Likely causes (in order of probability):

1. **Network down** — check connectivity first
2. **YouTube API key missing/invalid** — verify `YOUTUBE_API_KEY` env var
3. **All external services rate-limited simultaneously** — wait 10-15 min
4. **ANTHROPIC_API_KEY missing** — auto-translate fails before collectors
   start (rare edge; check this if topic contains only Chinese)

Recovery steps:
- `echo $YOUTUBE_API_KEY` / `echo $ANTHROPIC_API_KEY` — non-empty?
- `ping 8.8.8.8` — network reachable?
- Retry after 10 minutes; if still failing, run with verbose logging:
  ```bash
  python -c "from agents.zoro.keyword_research import research_keywords; print(research_keywords('test'))"
  ```

## Partial Failures (Exit Code 0, but sources_failed non-empty)

Pipeline succeeded but some sources dropped out. Check the frontmatter's
`sources_failed` list.

| Failed source | Likely cause | Recovery |
|---------------|--------------|----------|
| `trends_zh` / `trends_en` | trendspy IP rate limit (429) | Wait 1-2 hours; Claude synthesis still works on remaining sources |
| `youtube_zh` / `youtube_en` | YouTube Data API quota | Wait until midnight Pacific Time for reset |
| `reddit_zh` / `reddit_en` | Reddit rate limit | Usually recovers in minutes |
| `twitter_zh` / `twitter_en` | DuckDuckGo rate limit | Less critical; social signal is noisy anyway |
| `autocomplete_*` | Connection error | Usually transient; retry |

Decision rule: if <5 of 10 sources succeeded, warn the user the synthesis
may be lower-confidence and offer to re-run. If ≥5, proceed with the report
but mention affected signals in the summary.

## Claude API Errors

### 401 Authentication Failed
- `ANTHROPIC_API_KEY` missing or invalid
- Action: fix env var, re-run

### 429 Rate Limited
- Account-level or org-level throttle
- Action: wait 1 minute (exponential backoff via `shared/retry.py` built-in)

### 529 Overloaded / 500 Internal
- Transient Anthropic-side issues
- Action: re-run; if persistent (>10 min), check https://status.anthropic.com

### Timeout
- Model took too long (rare for this prompt size)
- Action: re-run; if repeats, the prompt may be too large — check data source
  blocks sizes in `keyword_research.py::_format_*` functions

## Translation Produces Wrong English Term

Symptom: `topic_en` in frontmatter is off (e.g., "冷水跳水" for "cold plunge"),
English-side collectors return poor data.

Recovery: re-run with explicit `--en-topic`:
```bash
python scripts/run_keyword_research.py "冷水浸泡" --en-topic "cold plunge"
```

## Output File Permission Error

Symptom: `PermissionError` writing to `--out` path.

Recovery:
- Check file isn't open in another app (Obsidian editor, etc.)
- Check parent directory is writable
- Try a fresh path: `--out /tmp/kw-test.md`

## trendspy Specific: Google Trends Quota Exceeded

Symptom: both `trends_zh` and `trends_en` appear in `sources_failed`; log
shows `TrendsQuotaExceededError`.

Recovery options:
1. **Wait** — quota resets in 1-2 hours
2. **Different IP** — run from VPS if you were local (or vice versa)
3. **Proxy** — `tr = Trends(proxy={'http': '...', 'https': '...'})`
   (requires editing `agents/zoro/trends_api.py`)
4. **Accept degradation** — Claude synthesis still produces keywords from
   YouTube + Autocomplete + Reddit + Twitter (4 of 5 signal sources)

## Pipeline Hangs (No Output for > 2 min)

ThreadPoolExecutor timeout is 15s per source, so total should be ~30s max
for parallel collection + 15s for synthesis. A hang suggests:

- Claude API stuck (check Anthropic status)
- Infinite retry loop in a collector (check logs for repeated warnings)

Recovery: Ctrl+C to abort, then run with `--json-out` to capture partial state
for debugging.
