---
name: seo-keyword-enrich
description: >
  Enrich a keyword-research frontmatter report with on-site GSC data
  (striking-distance keywords, cannibalization warnings), DataForSEO Labs
  difficulty (non-health terms only), and firecrawl top-3 SERP summary.
  Produces a SEOContextV1 block consumable by seo-optimize-draft and
  Brook compose. Use when the user says "enrich 這份關鍵字研究",
  "加上 ranking 數據", "SEO enrich", or hands you a keyword-research
  markdown and asks for SEO context. Do NOT run raw keyword research
  (use keyword-research) or audit a URL (use seo-audit-post).
---

# SEO Keyword Enrich — Enrich keyword-research with GSC ranking data

You are the interactive wrapper for the Nakama `seo-keyword-enrich` pipeline
(`scripts/enrich.py`). Your job is to take a `keyword-research` markdown
report and enrich it with real GSC data (striking-distance opportunities +
cannibalization warnings) so Brook compose / future `seo-optimize-draft` can
write with actual ranking evidence.

You do NOT re-implement the pipeline. You shell out to
`scripts/enrich.py` and surface the result back to the user.

## When to Use This Skill

Trigger on intent like:
- "enrich 這份關鍵字研究 <path>"
- "幫這份 keyword research 加上 ranking 數據"
- "SEO enrich <path>"
- "用 GSC 數據補強 <path>"
- "把關鍵字研究打 SEO 分數"

Do NOT trigger for:
- Raw keyword research from a topic (use `keyword-research`)
- URL-level SEO audit of a published post (use `seo-audit-post` — Phase 1.5)
- Rewriting an existing draft with SEO context (use `seo-optimize-draft` — Phase 2)
- Writing the article itself (use Brook compose / `article-compose`)

## Phase 1.5 Status (GSC + firecrawl SERP)

This Slice F implementation extends Slice B's GSC baseline with a firecrawl
top-3 SERP fetch + Claude Haiku summary chain that fills
`competitor_serp_summary`. DataForSEO difficulty (Slice E) is the remaining
Phase 1.5 item.

Currently active:
- GSC: primary / related / striking-distance / cannibalization (Slice B)
- firecrawl SERP top-3 + Haiku 4.5 summary → `competitor_serp_summary` (Slice F)

Currently stubbed (still `None` / empty in output):
- DataForSEO `keyword_difficulty` / `search_volume` — Slice E, requires
  `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` + $50 credit
- PageSpeed Insights data — belongs to `seo-audit-post` skill

Output frontmatter `phase` field reflects what actually ran:
- `"1.5 (gsc + firecrawl)"`     — both GSC + SERP summary OK
- `"1.5 (gsc + serp-skipped)"`  — GSC OK, firecrawl/Haiku failed/no results
- `"1 (gsc-only)"`              — `--no-serp` flag explicitly opted out

## Workflow Overview

```
Step 1. Parse input frontmatter (type: keyword-research)
Step 2. Resolve target_site (frontmatter hint or default wp_shosho)
Step 3. Confirm scope + cost                            [CONFIRM]
Step 4. Invoke enrich.py (mocked in dry-run; real GSC otherwise)
Step 5. Summary + hand-off hint (Brook compose opt-in)
```

### Step 1: Parse Input

The pipeline reads a markdown file with a `type: keyword-research`
frontmatter produced by the `keyword-research` skill. Expected fields:

- `topic` / `topic_en` — used for logging
- `core_keywords[0].keyword` — becomes `primary_keyword` in GSC queries
- `target_site` (optional) — `"wp_shosho"` or `"wp_fleet"`; defaults to
  `"wp_shosho"` if absent

Missing required fields (no frontmatter block / wrong `type` / no
`core_keywords` / empty first keyword) raise a clear error — do NOT guess.

### Step 2: Resolve target_site

- If frontmatter has `target_site: wp_shosho | wp_fleet` → use it
- Otherwise → default `wp_shosho` + warn in skill summary so修修 can override
  by re-running with `--target-site` once `config/target-keywords.yaml`
  (ADR-008 §6) is populated

### Step 3: Cost + Time Estimate (confirm before running)

- GSC quota: 200 req/day; this skill uses **1 query** (dimensions
  `["query", "page"]`, rowLimit 1000, 28-day window) → ~200 runs/day ceiling
- firecrawl quota: free tier ~500 credits/month; **1 search + 3 scrape ≈ 4 credits**
  per enrich → ~125 enrich/month ceiling on free tier
- LLM cost: **~$0.005-0.011** per enrich (Haiku 4.5 ≤ 3000 in + ≤ 1500 out tokens)
- Wall clock: **~15-25s** (firecrawl scrape dominates; GSC ~3-5s, Haiku ~3-5s)

Present to user:

```
預估：
  時間：~20s
  成本：~$0.01（GSC 免費 + firecrawl free tier + Haiku 4.5 摘要）
  GSC property：<property>  (target_site=<app-name>)
  firecrawl SERP：top-3（about 4 credits）
  輸出：<out_path>
確認開跑？
```

如果 firecrawl quota 用完或想離線跑，加 `--no-serp` flag 跳過 SERP chain。

Accept "go" / "確認" / "ok"; reject → return to scope adjustment.

### Step 4: Invoke enrich.py

Build the command (run from repo root; the script auto-prepends the repo
root to `sys.path` so `shared.*` imports resolve regardless of cwd):

```bash
python .claude/skills/seo-keyword-enrich/scripts/enrich.py \
    --input "<kw_research_path>" \
    --output-dir "<out_dir>" \
    [--dry-run] [--no-serp]
```

`--dry-run` skips both GSC and SERP calls and prints the query payload for
debugging — use when diagnosing auth / property issues.

`--no-serp` skips the firecrawl + Haiku SERP summary chain (phase falls back
to `"1 (gsc-only)"`); use for offline runs or when firecrawl quota is
exhausted.

### Step 5: Summary + Hand-off

After a successful run, present:

```
完成！耗時 X.Xs

輸出：<out_path>
phase: <"1.5 (gsc + firecrawl)" | "1.5 (gsc + serp-skipped)" | "1 (gsc-only)">

SEOContextV1 summary:
  target_site: <app-name>
  primary_keyword: <kw> (clicks X, impressions Y, pos Z.Z)
  related_keywords: N
  striking_distance: M opportunities
  cannibalization_warnings: K
  competitor_serp_summary: <chars> 字 / 已跳過 / 失敗降級

下一步建議：
  → Brook compose：把這份 path 傳給 article-compose / Brook，
    `compose_and_enqueue(..., seo_context=<parse SEOContextV1 from this file>)`
  → Slice E (pending)：DataForSEO difficulty 補齊
```

---

## Output Contract (for downstream consumers)

The pipeline writes a markdown file with two layers:

**Frontmatter** (machine-readable, YAML):

```yaml
---
type: seo-context
schema_version: 1
target_site: wp_shosho        # "wp_shosho" | "wp_fleet"
phase: "1.5 (gsc + firecrawl)"  # also: "1.5 (gsc + serp-skipped)" / "1 (gsc-only)"
generated_at: 2026-04-26T03:00:00+00:00
source_keyword_research_path: KB/Research/keywords/morning-coffee-sleep.md
---
```

**Body** (two sections):

1. **`## SEOContextV1 (JSON)`** — a single `` ```json `` code fence containing
   the exact `SEOContextV1.model_dump_json(indent=2)` output. Downstream
   consumers **MUST** parse this block via `SEOContextV1.model_validate_json()`
   and not the frontmatter.

2. **`## 人類可讀摘要`** — Chinese-language bullet summary (primary keyword
   metrics / striking-distance count / cannibalization count). Not machine
   readable; regenerate from the JSON block if you need structured data.

### Stable guarantees

Downstream consumers can rely on:
- Frontmatter `type: seo-context` discriminator
- `schema_version: 1` invariant
- JSON block round-trips through `SEOContextV1.model_validate_json()`

Not stable (may evolve between Phase 1 / 1.5 / 2):
- Body markdown structure (headings, order)
- Exact frontmatter field order
- `phase` value (added new variants when more enrich sources land — caller
  should treat any `phase: "1.5 ..."` as Slice F output and parse the JSON
  block for what's actually present)

### Filename convention

`enriched-<input-stem>-<YYYYMMDD>.md` where the date uses
`ZoneInfo("Asia/Taipei")` (per `feedback_date_filename_review_checklist.md`).
Example: `enriched-morning-coffee-sleep-20260426.md`.

---

## Cost

- **GSC**: 1 query per enrich (dimensions `["query", "page"]`, rowLimit 1000,
  28-day window). At 200 queries/day GSC quota → ~200 enrich runs/day ceiling.
- **firecrawl**: 1 search + 3 scrape ≈ 4 credits per enrich. Free tier 500
  credits/month → ~125 enrich/month ceiling. Exceeded → graceful fallback
  to `phase: "1.5 (gsc + serp-skipped)"`.
- **LLM** (Haiku 4.5): ~$0.005-0.011 per enrich (3000 in + 1500 out tokens).
- **Wall clock**: ~15-25s dominated by firecrawl scrape (3 × ~5s).
  `--no-serp` brings it back to ~3-8s.
- **Effective per-run cost**: ~$0.01 with SERP, ~$0 without.

Slice E will add ~$0.001 per non-health keyword (DataForSEO Labs).

---

## Open-Source Friendliness

This skill is part of the Nakama repo and intended to be extractable. Design
constraints:

- No hardcoded paths. `--output-dir` is required; default filename format is
  `enriched-<input-stem>-<YYYYMMDD>.md`.
- `GSCClient` is injected via constructor in `enrich()` — unit tests pass a
  mock client instead of touching real credentials.
- Phase 1 schema (`SEOContextV1`) is frozen via `schema_version: Literal[1]`;
  an open-source fork can vend its own target-site Literal by changing
  `shared/schemas/publishing.py`'s `TargetSite` + `shared/schemas/site_mapping.py`
  and re-running the exhaustiveness test.
- No LifeOS / Obsidian dependency beyond output path (user choice).

See `docs/capabilities/seo-keyword-enrich.md` for capability card format.

## References

| When | Read |
|---|---|
| Schema shape | `shared/schemas/publishing.py` (`SEOContextV1` family) |
| GSC query / auth | `shared/gsc_client.py`, `docs/runbooks/gsc-oauth-setup.md` |
| Striking-distance filter | `shared/seo_enrich/striking_distance.py` (T6 contract) |
| Cannibalization threshold | `shared/seo_enrich/cannibalization.py`, `config/seo-enrich.yaml` |
| firecrawl SERP fetch | `shared/firecrawl_serp.py` (Slice F) |
| SERP summary | `shared/seo_enrich/serp_summarizer.py` (Slice F, Haiku 4.5) |
| Brook downstream sanitize | `agents/brook/seo_block.py` (`_INJECTION_PATTERNS`, `_MAX_SERP_CHARS=1200`) |
| Input contract | `.claude/skills/keyword-research/references/output-contract.md` |
| Brook compose integration (Slice C) | ADR-009 §D5 |
