# Output Contract — Frontmatter Schema for Downstream Skills

The skill's output markdown file has two layers. The **frontmatter** is a
machine-readable YAML block intended for downstream consumption (Brook
`article-compose`, future SEO audit skill). The **body** is human-readable
and safe to regenerate from the frontmatter.

## Frontmatter Schema

```yaml
---
type: keyword-research           # discriminator for downstream parsers
topic: str                       # Chinese topic as confirmed with user
topic_en: str                    # English equivalent (auto-translated or user-provided)
content_type: "youtube" | "blog" # optimization target for title seeds
generated_at: ISO8601 UTC        # e.g. "2026-04-18T12:34:56+00:00"

sources_used: list[str]          # e.g. ["youtube_zh", "trends_en", ...]
sources_failed: list[str]        # subset of 10 collector names

core_keywords:
  - keyword: str                 # 繁體中文
    keyword_en: str              # English equivalent
    search_volume: "high" | "medium" | "low"
    competition: "high" | "medium" | "low"
    opportunity: "high" | "medium" | "low"
    source: "zh" | "en" | "both" # which market surfaced this keyword
    reason: str                  # 1-sentence rationale

trend_gaps:                      # 2-5 items
  - topic: str                   # the English-trending subject
    en_signal: str               # evidence from English sources
    zh_status: str               # what Chinese market currently has
    opportunity: str             # why this is an opening

youtube_title_seeds: list[str]   # 10 titles, 55-char limit, curiosity-gap
blog_title_seeds: list[str]      # 10 titles, 60-80-char, SEO-structured
---
```

## Consumption Patterns

### Pattern 1: Brook `article-compose`

Before writing a draft, Brook should load the keyword-research frontmatter to
seed the outline:

```python
import yaml
from pathlib import Path

content = Path(kw_research_path).read_text(encoding="utf-8")
_, fm_raw, _ = content.split("---", 2)
fm = yaml.safe_load(fm_raw)

# Use in compose
high_opp = [k for k in fm["core_keywords"] if k["opportunity"] == "high"]
title_candidates = fm["youtube_title_seeds"] if content_type == "youtube" else fm["blog_title_seeds"]
```

### Pattern 2: Future SEO Audit

An SEO audit skill can cross-reference `core_keywords` (internal judgment) with
external keyword-volume APIs (DataForSEO, Ahrefs) to enrich `search_volume`
from qualitative (high/medium/low) to quantitative (monthly searches).

### Pattern 3: Future Scheduled Ingestion

If Nami builds a "morning brief" skill that aggregates Zoro's daily keyword
research runs, the frontmatter `type: keyword-research` + `generated_at` are
enough to index and dedupe.

## Stability Guarantees

**Stable** (downstream can rely on these):
- Field names in frontmatter
- Enum values (`high`/`medium`/`low`, `zh`/`en`/`both`, `youtube`/`blog`)
- `type: keyword-research` discriminator
- `core_keywords` / `trend_gaps` item shapes

**Not stable** (may evolve):
- Body markdown structure (headings, order, formatting) — regenerate from
  frontmatter if you need consistent body rendering
- Number of items in `core_keywords` / `trend_gaps` / `*_title_seeds` (current
  targets: 8-12 / 2-5 / 10 each, but prompt-dependent)
- `sources_used` collector names (if new collectors are added)

## Breaking Change Policy

If the schema changes incompatibly:
1. Bump `type` to `keyword-research-v2` (new discriminator)
2. Keep one release producing BOTH old and new for downstream migration
3. Document the break in the skill's SKILL.md + this file's changelog

## Minimal Validation Snippet

Downstream consumers should validate the discriminator before trusting fields:

```python
if fm.get("type") != "keyword-research":
    raise ValueError(f"Expected keyword-research frontmatter, got {fm.get('type')!r}")
```
