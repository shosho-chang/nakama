# `seo-audit-post` Output Contract

The pipeline writes a single markdown file:
`<output-dir>/audit-<url-slug>-<YYYYMMDD>.md` (date in `Asia/Taipei`).
Downstream consumers (future `seo-optimize-draft`, ad-hoc Brook compose
imports, manual review) can rely on the shape below for
`schema_version: 1`.

## Frontmatter

```yaml
---
type: seo-audit-report
schema_version: 1
audit_target: https://shosho.tw/zone-2-training-guide
target_site: wp_shosho                # null when URL is not in HOST_TO_TARGET_SITE
focus_keyword: zone 2 訓練             # null when not provided
fetched_at: 2026-04-26T03:00:00+00:00  # ISO 8601 UTC
phase: "1.5 (deterministic + llm)"
generated_by: seo-audit-post (Slice D.2)
pagespeed_strategy: mobile             # mobile | desktop
llm_level: sonnet                      # sonnet | haiku | none
gsc_section: included                  # included | skipped (...) | error (...)
kb_section: included                   # included | skipped (...) | error (...)
summary:
  total: 40                            # 28 deterministic + 12 LLM semantic
  pass: 25
  warn: 8
  fail: 3
  skip: 4
  overall_grade: B+                    # A / B+ / B / C+ / C / D / F
---
```

### Stable frontmatter guarantees

- Discriminator: `type: seo-audit-report`.
- `schema_version: 1` while Slice D.2 is in production.
- `summary.total / pass / warn / fail / skip / overall_grade` are always
  present (even if all checks skipped).
- `target_site` is `null` for non-self-hosted URLs (NOT a string `"null"`).
- `gsc_section` / `kb_section` strings prefix-match `included` / `skipped` /
  `error` so consumers can branch.

### Not stable

- Frontmatter field order.
- Internal phrasing of `gsc_section` / `kb_section` skip reasons.

---

## Body — 7 sections (5 mandatory + 2 optional)

Order is part of the contract:

```
# SEO Audit — <article title or URL>

## 1. Summary                          # mandatory
## 2. Critical Fixes（必修）            # mandatory
## 3. Warnings（建議修）                # mandatory
## 4. Info（觀察）                       # mandatory
## 5. PageSpeed Insights Summary       # mandatory
## 6. GSC Ranking（last 28 days）      # optional — present always; body says "不適用" when skipped
## 7. Internal Link Suggestions        # optional — present always; body says "跳過：..." when skipped
```

§6 / §7 headings are always emitted; only the body varies. This keeps
the `## N. <heading>` anchor stable for downstream consumers (tools that
extract one section by `## 6.` regex don't break when GSC is skipped).

### §1 Summary

- Markdown table with category rows + Total row + `**Overall grade: X**`.
- Optional "最重要修法" list (only when there are critical fails).

### §2 Critical Fixes

For each critical fail (severity=critical, status=fail):
```
### [<rule_id>] <name>

- **Actual**: ...
- **Expected**: ...
- **Fix**: ... (omitted when fix_suggestion is empty)
```
"（無）" if there are no critical fails.

### §3 Warnings

Same block format as §2. Includes `status="warn"` items + non-critical
fails (`status="fail" AND severity != "critical"`).

### §4 Info

Bullet list (one line per check):
```
- [<rule_id>] <name> — <actual>
```
Excludes skipped items.

### §5 PageSpeed Insights Summary

Hard-coded format:
```
- **Performance**: NN / 100 (mobile|desktop)
- **SEO**: NN / 100
- **Best Practices**: NN / 100
- **Accessibility**: NN / 100

Core Web Vitals:
- LCP: X.Xs
- INP: NNms       # or "— (CrUX 無 field data)"
- CLS: X.XXX
```
Score "—" when missing from PageSpeed response.

### §6 GSC Ranking

When `gsc_section: included`:
- Markdown table headed `| Query | Clicks | Impressions | CTR | Position |`
  (top 15 by impressions).
- Optional "Striking distance opportunities" sub-list (pos 11-20,
  impressions ≥ 50, top 5 by impressions).

When skipped or error: a single explanatory line — text starts with
"不適用" / "跳過：" / "錯誤跳過：".

### §7 Internal Link Suggestions

When `kb_section: included`:
- Numbered list of `[[KB/Wiki/.../page]] — relevance reason`.

When skipped or error: a single explanatory line — text starts with
"跳過：" / "錯誤跳過：".

---

## Filename

`audit-<url-slug>-<YYYYMMDD>.md`, where:

- `<url-slug>` is `urlsplit(url).path.strip("/")` slugified
  (non-word/CJK chars → `-`, lowercase). Falls back to host when path
  is empty.
- `<YYYYMMDD>` uses `ZoneInfo("Asia/Taipei")`.

Example: `audit-zone-2-training-guide-20260426.md`.

---

## Versioning

- `schema_version: 1` throughout Slice D.2 lifetime.
- Phase 2 changes (competitor SERP / cron audits / full mode) will bump
  to `schema_version: 2` with a migration playbook (T3 backlog).
- Breaking changes to body section anchors (`## 1. ... ## 7. ...`) are
  schema_version bumps; new optional sections appended after §7 are NOT
  breaking.
