# Capability Card — `seo-keyword-enrich`

**Status:** Phase 1 Slice B (GSC-only baseline) — live at
`.claude/skills/seo-keyword-enrich/` + `shared/seo_enrich/`
**License:** MIT (planned open-source extraction)
**Scope:** Enrich a `keyword-research` markdown report with on-site Google
Search Console data to produce a `SEOContextV1` block consumable by Brook
compose (Slice C) and future `seo-optimize-draft` (Phase 2).

---

## Capability

Given a `type: keyword-research` markdown report (frontmatter output of the
`keyword-research` skill) and an `output-dir`, query Google Search Console for
the last 28 days, filter striking-distance rows (position 11-20), detect
keyword cannibalization, and write a `SEOContextV1`-serialized markdown
report to `output-dir/enriched-<input-stem>-<YYYYMMDD>.md` (Taipei date).

## Input / Output Contract

**Input** — `keyword-research` markdown:

```yaml
---
type: keyword-research
topic: 晨間咖啡 睡眠
topic_en: morning coffee sleep
content_type: blog
core_keywords:
  - keyword: 晨間咖啡 睡眠       # [0] becomes `primary_keyword`
    opportunity: high
  - keyword: 咖啡因 代謝
target_site: wp_shosho           # optional; defaults to "wp_shosho"
---
```

**Output** — `SEOContextV1` markdown:

````markdown
---
type: seo-context
schema_version: 1
target_site: wp_shosho
phase: "1 (gsc-only)"
generated_at: 2026-04-26T03:00:00+00:00
source_keyword_research_path: …/morning-coffee-sleep.md
---

# SEO enrichment result

## SEOContextV1 (JSON)

```json
{
  "schema_version": 1,
  "target_site": "wp_shosho",
  "primary_keyword": { ... KeywordMetricV1 ... },
  "related_keywords": [ ... ],
  "striking_distance": [ ... ],
  "cannibalization_warnings": [ ... ],
  "competitor_serp_summary": null,
  "generated_at": "2026-04-26T03:00:00+00:00",
  "source_keyword_research_path": "…"
}
```

## 人類可讀摘要
- Primary keyword: ...
- Striking distance: N opportunities (...)
- Cannibalization: K warnings (...)
````

The JSON block is the source of truth for downstream consumers and **MUST**
round-trip through `SEOContextV1.model_validate_json()`.

## Dependencies

- **Runtime**
  - Python 3.10+
  - `pydantic >= 2.0` (schema)
  - `pyyaml` (frontmatter parse/write)
  - `google-api-python-client` + `google-auth` (via `shared.gsc_client`)
- **Internal**
  - `shared/gsc_client.py` — GSC thin wrapper (Slice A)
  - `shared/schemas/publishing.py` — `SEOContextV1` family (Slice A)
  - `shared/schemas/site_mapping.py` — `TargetSite` Literal (Slice A)
  - `shared/seo_enrich/striking_distance.py` — T6 filter (Slice B Phase 1a)
  - `shared/seo_enrich/cannibalization.py` — T9 rules (Slice B Phase 1b)
  - `config/seo-enrich.yaml` — cannibalization thresholds (editable)
- **Credentials**
  - `GSC_SERVICE_ACCOUNT_JSON_PATH` — GCP service account JSON
  - `GSC_PROPERTY_SHOSHO` — e.g. `sc-domain:shosho.tw`
  - `GSC_PROPERTY_FLEET` — e.g. `sc-domain:fleet.shosho.tw`

Setup instructions: see `docs/runbooks/gsc-oauth-setup.md`.

## Cost

- **GSC**: 1 query per enrich (`dimensions=["query","page"]`, `rowLimit=1000`,
  28-day window, end-date lagged 3 days). GSC free quota is ~200 requests/day,
  so ~200 runs/day ceiling.
- **LLM**: $0 — Slice B has zero LLM calls (pure parse + filter + schema build).
- **Wall clock**: ~3-8 s, dominated by the GSC API round-trip.
- **Effective per-run cost**: ~$0.

Phase 1.5 will add firecrawl + Claude Haiku summary (~$0.005/run) and
DataForSEO Labs (~$0.001 per non-health keyword).

## Open-Source Readiness

Parameterized extension points so the skill can be lifted out of Nakama:

1. **Target-site vocabulary** — the `TargetSite` Literal and
   `HOST_TO_TARGET_SITE` dict both live in `shared/schemas/` and are the
   only places to edit for a fork with different sites. The skill itself
   doesn't hardcode either.
2. **GSC property resolution** — `_TARGET_SITE_TO_GSC_PROPERTY_ENV` in
   `enrich.py` is the single env-mapping source; add a new row per new
   target site.
3. **Cannibalization thresholds** — every threshold is in
   `config/seo-enrich.yaml`; re-tuning doesn't require a code change.
4. **Striking-distance band** — `_MIN_POSITION` / `_MAX_POSITION` in
   `shared/seo_enrich/striking_distance.py` can be lifted to config if a
   fork prefers 11-30 (e.g., for higher-traffic sites).
5. **GSC client injection** — `enrich(..., client: GSCClient | None = None)`
   lets an open-source user stub out the real API entirely (see the test
   file for the pattern).
6. **Input contract** — the skill only reads the `type: keyword-research`
   frontmatter fields listed above. Any upstream producer that emits
   equivalent frontmatter (not necessarily the Nakama `keyword-research`
   skill) is a valid input.

## Contract Tests

- Unit / pipeline: `tests/skills/seo_keyword_enrich/test_enrich_pipeline.py`
  (all mocked; no real GSC call).
- Dependency modules: `tests/shared/seo_enrich/test_striking_distance.py`,
  `tests/shared/seo_enrich/test_cannibalization.py`.
- Live GSC smoke: not in CI; run `--dry-run` on the VPS manually after merge
  to verify property / credentials (T1 benchmark).

## Limitations (Phase 1)

- **GSC quota** — 200 req/day ceiling shared with ADR-008 weekly digest.
  No rate-limit middleware yet (T8 backlog).
- **No DataForSEO difficulty** — Phase 1.5 adds optional
  `KeywordMetricV1.difficulty` field; until then, all keywords show
  only GSC-derived metrics.
- **No SERP summary** — `competitor_serp_summary` is always `None` in Slice
  B output. Firecrawl top-3 ingestion + Haiku summary lands in Phase 1.5.
- **Health-vertical keyword difficulty unavailable** — even with Phase 1.5
  DataForSEO, Google Ads policy anonymizes Health SV/CPC; health keywords
  will ship with `difficulty=None`.
- **No cross-language merge** — GSC is queried in its native form; the skill
  doesn't attempt to dedupe "intermittent fasting" vs "間歇性斷食" across the
  property's bilingual traffic.
- **Single GSC property per run** — multi-site aggregation (e.g., cross-blog
  SEO views) is out of scope.
- **No async execution** — T1 benchmark on VPS (post-merge) decides whether
  Phase 2 warrants a job_id + Slack-notification style (T7 backlog).

## Roadmap

- [x] Slice B — GSC-only baseline (this card)
- [ ] Phase 1.5 — DataForSEO Labs + firecrawl SERP + Haiku summary
- [ ] Phase 2 — async job mode; optional `config/target-keywords.yaml` lookup
  for ownership-driven target-site resolution; cross-language keyword merge
