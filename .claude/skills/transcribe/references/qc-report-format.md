# QC Report Format

The pipeline writes a `.qc.md` file next to the SRT. Parse it in Step 7 to
give the user a structured summary of what needs human review.

## File Structure

Header:
```markdown
# QC 報告 — 需人工確認
```

Per-item block (two formats, auto-detected by the writer):

### Format A: With arbitration verdict (new)

```markdown
## [{RISK} | {verdict} | conf {0.XX}] Line {N}
- **ASR 原文**：{original ASR text}
- **Opus 建議**：{Opus suggestion}
- **Opus 理由**：{Opus reason for flagging}
- **Gemini 仲裁**：{final text after arbitration}
- **Gemini 理由**：{Gemini reasoning}
```

Where:
- `{RISK}` = `HIGH` / `MEDIUM` / `LOW` (uppercase)
- `{verdict}` = `keep_original` / `accept_suggestion` / `custom` / `refused`
- `conf` = 0.00-1.00 float (Gemini's confidence)

### Format B: No arbitration (fallback)

```markdown
## [{RISK}] Line {N}
- **原文**：{original}
- **建議**：{suggestion}
- **理由**：{reason}
```

## Parsing Strategy

Read the full file (usually <10KB) and split on `^## ` to get per-item blocks.
For each block:

1. Parse header with regex:
   ```
   ^## \[([A-Z]+)(?:\s\|\s(\w+)\s\|\sconf\s([\d.]+))?\]\sLine\s(\d+)$
   ```
   Groups: risk, verdict (optional), confidence (optional), line_no.

2. Collect bullet fields:
   - `- **ASR 原文**：` → original
   - `- **Opus 建議**：` → suggestion
   - `- **Gemini 仲裁**：` → final_text (if present)
   - `- **Gemini 理由**：` → gemini_reasoning (if present)

## Summary Template

Aggregate for the user:

```
QC 摘要：
  總項目: {total} 處
  - Refused (Gemini 拒答): {refused_count} 片段
  - High risk (建議複查): {high_count} 處
  - Medium risk: {medium_count} 處
  - Low risk: {low_count} 處

Corrections applied: {accept_suggestion_count + custom_count} 處

建議人工複查 (High + Refused)：
  Line {N}: "{original}" → "{final}" ({verdict}, conf {0.XX})
    Gemini 理由: {reasoning_short}
  ...（最多列 10 筆，超過則顯示 "+N more"）
```

## What to Highlight

- **Refused**: Always list these. They are cases where Gemini explicitly
  declined to judge (audio vs text mismatch). The ASR original is kept.
  These need human review most.
- **High + not accept_suggestion**: Opus flagged as uncertain, arbitration
  did not cleanly resolve. Review.
- **Medium + custom verdict**: Gemini produced a third option different
  from both ASR and Opus. Worth spot-checking.
- **Low**: Usually safe to skip in summary unless user asks for full list.

## Cost Line (if present in the run log)

The pipeline prints total cost to stdout at the end (from
`shared/anthropic_client` / `shared/gemini_client` cost tracking). Capture
it from the Bash output and include in the summary. If not found, say
"成本未回傳，請查 token usage log"。

## When the QC File is Missing

If Step 7 can't find `.qc.md`, it means either:
1. There were zero uncertain segments (perfect transcription — rare but
   possible for clean short clips). The SRT alone is the output.
2. The pipeline crashed after writing SRT but before QC. Check stdout
   for errors and suggest partial re-run.
