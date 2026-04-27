# Capability Card — `seo-audit-post`

**Status:** Phase 1.5 Slice D.2 — live at `.claude/skills/seo-audit-post/`
+ `shared/seo_audit/` (D.1 deterministic + D.2 LLM semantic).
**License:** MIT (planned open-source extraction).
**Scope:** On-page SEO audit for a single published blog URL — combines
28 deterministic rules + 12 LLM semantic rules (E-E-A-T, focus keyword
semantics, Taiwan pharma compliance) + optional GSC + optional Robin KB
internal-link suggestions, into a single markdown report.

---

## Capability

Given a URL (and optional `--focus-keyword`, `--gsc-property`,
`--vault-path`), runs:

1. HTML fetch with retry (`shared/seo_audit/html_fetcher.py`)
2. PageSpeed Insights (`shared/pagespeed_client.py`)
3. 28 deterministic rules (`shared/seo_audit/{metadata,headings,images,
   structure,schema_markup,performance}.py`)
4. 12 LLM semantic rules in a single batched Sonnet 4.6 call
   (`shared/seo_audit/llm_review.py`)
5. Optional GSC ranking query (last 28 days, lagged 3 days) — only if
   URL host is in `HOST_TO_TARGET_SITE`
6. Optional Robin KB ranker for internal-link suggestions
   (`agents/robin/kb_search.search_kb(query, vault, purpose="seo_audit")`)
7. Renders a markdown report with frontmatter (`type: seo-audit-report`,
   `schema_version: 1`) + 7 body sections (5 mandatory + 2 optional).

## Input / Output Contract

**Input** — CLI:

```bash
python .claude/skills/seo-audit-post/scripts/audit.py \
    --url "https://shosho.tw/zone-2-training-guide" \
    --output-dir "vault/KB/Audits/" \
    [--focus-keyword "zone 2 訓練"] \
    [--gsc-property "sc-domain:shosho.tw"] \
    [--no-kb] \
    [--vault-path "/Users/shosho/.../Shosho LifeOS"] \
    [--strategy desktop] \
    [--llm-level sonnet|haiku|none]
```

**Output** — markdown:

```markdown
---
type: seo-audit-report
schema_version: 1
audit_target: https://shosho.tw/zone-2-training-guide
target_site: wp_shosho
focus_keyword: zone 2 訓練
fetched_at: 2026-04-26T03:00:00+00:00
phase: "1.5 (deterministic + llm)"
generated_by: seo-audit-post (Slice D.2)
pagespeed_strategy: mobile
llm_level: sonnet
gsc_section: included
kb_section: included
summary:
  total: 40
  pass: 25
  warn: 8
  fail: 3
  skip: 4
  overall_grade: B+
---

# SEO Audit — <title>

## 1. Summary
[category × pass/warn/fail/skip table + grade]

## 2. Critical Fixes（必修）
[blocks of {actual / expected / fix}]

## 3. Warnings（建議修）
[blocks]

## 4. Info（觀察）
[bullet list]

## 5. PageSpeed Insights Summary
[scores + Core Web Vitals]

## 6. GSC Ranking（last 28 days）
[table + striking distance — or "不適用" line]

## 7. Internal Link Suggestions
[KB page list — or "跳過：..." line]
```

Full contract in
`.claude/skills/seo-audit-post/references/output-contract.md`.

## Dependencies

- **Runtime**
  - Python 3.10+
  - `pydantic >= 2.0` (schema)
  - `pyyaml` (frontmatter)
  - `httpx` (PageSpeed + HTML fetch)
  - `beautifulsoup4` (HTML parse)
  - `anthropic` (LLM semantic)
  - `google-api-python-client` + `google-auth` (GSC, indirect)
- **Internal**
  - `shared/seo_audit/*` — deterministic rule modules + LLM review
  - `shared/pagespeed_client.py` — PageSpeed thin wrapper
  - `shared/gsc_client.py` — GSC thin wrapper
  - `shared/schemas/site_mapping.py` — host → target_site
  - `shared/anthropic_client.py` — LLM client + cost tracking
  - `agents/robin/kb_search.py` — KB ranker (`purpose="seo_audit"` opt-in)
  - `shared/compliance/` — full medical-claim vocab + disclaimer (L9 input)
- **Credentials**
  - `PAGESPEED_INSIGHTS_API_KEY` — required
  - `ANTHROPIC_API_KEY` — required (unless `--llm-level=none`)
  - `GSC_SERVICE_ACCOUNT_JSON_PATH` — only when URL is self-hosted
  - `GSC_PROPERTY_SHOSHO` / `GSC_PROPERTY_FLEET` — env vars per target site
  - `VAULT_PATH` — Obsidian vault root (only when KB section enabled)

Setup: see `docs/runbooks/setup-wp-integration-credentials.md` §2e
(PageSpeed) + `docs/runbooks/gsc-oauth-setup.md` (GSC).

## Cost

- **PageSpeed**: $0 (free quota; ~10–30s)
- **GSC**: $0 (free quota; 1 query / audit; shared 200 req/day budget
  with `seo-keyword-enrich`)
- **LLM semantic** (Sonnet 4.6, 12-rule batch): ~$0.025–0.035 per audit
- **LLM semantic** (Haiku): ~$0.003 per audit
- **KB ranker** (Haiku, single call): ~$0.005 per audit
- **Wall clock**: ~25–60s (PageSpeed dominates)
- **Total per audit**: < $0.10 (Sonnet level)

`--llm-level=none` reduces cost to $0 + GSC + KB; `--no-kb` removes KB
ranker call.

## Open-Source Readiness

Parameterized extension points:

1. **All side-effecting collaborators are injectable.**
   `audit(url, output_dir, *, pagespeed_runner=None, gsc_querier=None,
   kb_searcher=None, compliance_scanner=None, llm_reviewer=None, ...)` —
   tests / forks supply fakes; production wiring is the default.
2. **Target-site vocabulary** lives in
   `shared/schemas/site_mapping.py` — single edit point per fork.
3. **Rule catalog** is purely module-driven: each `check_*` function in
   `shared/seo_audit/` returns a `list[AuditCheck]`. Add a rule by
   appending a check; remove by deleting it.
4. **LLM prompt + rule wording** is in `shared/seo_audit/llm_review.py`
   `_RULES` tuple — fork can re-tune phrasing without touching pipeline.
5. **Markdown rendering** lives in `audit.py` `_render_*` helpers — open
   to template forks (e.g., HTML output) by replacing `render_markdown`.
6. **No LifeOS / Obsidian dependency** beyond `--vault-path` (optional);
   skill works without KB section.
7. **`kb_search` `purpose` parameter** lets new use cases (`youtube`,
   `seo_audit`, `blog_compose`, `general`) share KB scan + Haiku ranker
   without prompt drift.

## Contract Tests

- LLM review: `tests/shared/seo_audit/test_llm_review.py`
  (Anthropic fully mocked; 19 cases covering happy path, prompt
  assembly, JSON parse variants, model selection, all failure modes).
- Audit pipeline: `tests/skills/seo_audit_post/test_audit_pipeline.py`
  (E2E mock — fixture HTML + mock PageSpeed + mock LLM + mock GSC →
  verify markdown structure, frontmatter shape, all sections present).
- No-GSC mode: `test_audit_no_gsc.py` (URL not in HOST_TO_TARGET_SITE
  → §6 says "不適用" without raising).
- No-KB mode: `test_audit_no_kb.py` (KB search exception swallowed,
  §7 says "錯誤跳過").
- Smoke: `test_audit_smoke.py` (subprocess `python audit.py
  --url <fixture-server>` end-to-end).
- Live smoke: T1-style benchmark on
  `https://shosho.tw/zone-2-training-guide` — output in PR description.

## Limitations (Phase 1.5)

- **No competitor SERP comparison** — Phase 2 backlog (would require
  firecrawl + competitor analysis).
- **No cross-page keyword network** — single-page audit only.
- **L9 compliance is SEED**: `compliance_scan.MEDICAL_CLAIM_PATTERNS` has
  6 patterns. False-negatives expected for terms outside the seed vocab.
  Slice B medical vocab will lift this.
- **CrUX field data sometimes missing** for low-traffic URLs → P2 (INP)
  returns `status="skip"` (correct behavior, not an error).
- **PageSpeed wall clock dominates** — typical 10–30s per audit. No
  asyncio parallelization (intentional simplicity per §D.2.6 边界).
- **GSC quota** shared with `seo-keyword-enrich` (200 req/day).
- **Single URL per run** — no batch / sitemap-driven mode (Phase 2).

## Roadmap

- [x] Slice D.1 — deterministic checks + PageSpeed (PR #173 + #181)
- [x] Slice D.2 — `seo-audit-post` skill + LLM semantic + markdown report
- [ ] Phase 2 — competitor SERP, full-mode keyword network, sitemap-driven
  cron audits, `seo-optimize-draft` integration
- [ ] Slice B medical vocab — replace SEED compliance_scan, lifts L9
  false-negative rate
