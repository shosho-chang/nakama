---
name: seo-audit-post
description: >
  On-page SEO audit for a single published blog URL —
  fetches HTML, runs PageSpeed Insights, checks ~25 deterministic rules
  (metadata / headings / image alt / schema / internal links) + ~10 LLM
  semantic rules (E-E-A-T, focus keyword semantics, Taiwan pharma compliance),
  produces a markdown report with fix suggestions. Use this skill when the
  user says "SEO audit <url>", "幫這篇做 SEO 體檢", "檢查 <url> 的 SEO",
  "audit 一下 <url>". Do NOT trigger for keyword research (use
  keyword-research) or draft rewriting (use seo-optimize-draft when Phase 2
  lands).
---

# SEO Audit Post — On-page SEO 體檢

You are the interactive wrapper for the Nakama `seo-audit-post` pipeline
(`scripts/audit.py`). Your job is to take a single URL (a published blog
post) and produce a markdown SEO audit report with deterministic + LLM
semantic findings + optional GSC ranking + optional KB internal-link
suggestions.

You do NOT re-implement the pipeline. You shell out to
`scripts/audit.py` and surface the result back to the user.

## When to Use This Skill

Trigger on intent like:

- "SEO audit <url>"
- "幫這篇做 SEO 體檢 <url>"
- "檢查 <url> 的 SEO"
- "audit 一下 <url>"

Do NOT trigger for:

- Raw keyword research from a topic (use `keyword-research`)
- Enrich a `keyword-research` markdown with GSC data (use `seo-keyword-enrich`)
- Rewriting an existing draft with SEO context (use `seo-optimize-draft` —
  Phase 2)

## Phase 1.5 scope

This Slice D.2 implementation includes:

- 28 deterministic rules (Slice D.1, `shared/seo_audit/*`):
  M1-M5 metadata, O1-O4 OpenGraph/Twitter, H1-H3 headings,
  I1-I3 images, S1-S3 structure, SC1-SC4 schema, P1-P3 PageSpeed.
- 12 LLM semantic rules (`shared/seo_audit/llm_review.py`):
  L1-L4 keyword semantics + intent, L5-L8 E-E-A-T, L9 Taiwan compliance,
  L10 schema-content + internal link, L11 medical references,
  L12 reviewed-date / freshness.
- Optional GSC ranking section (last 28 days) — only when URL host maps
  to `wp_shosho` / `wp_fleet` via `shared/schemas/site_mapping.py`.
- Optional KB internal-link suggestion — calls Robin's `search_kb` with
  `purpose="seo_audit"` so Haiku ranks for SEO-link relevance, not video
  production.

Out-of-scope (Phase 2 backlog): competitor SERP comparison, full-mode
keyword network, cron-driven site audits, GA4 integration.

## Workflow Overview

```
Step 1. Parse input (URL or vault path with `type: source` frontmatter)
Step 2. Resolve focus_keyword + GSC property (frontmatter / SEOPress / user)
Step 3. Confirm scope + cost                                  [CONFIRM]
Step 4. Invoke audit.py (PageSpeed + deterministic + LLM + GSC + KB)
Step 5. Summary + hand-off hint
```

### Step 1: Parse Input

Two valid inputs:

- **URL**: `https://shosho.tw/zone-2-training-guide` — direct.
- **Vault path**: a local markdown file with frontmatter `type: source`
  (Robin Reader output). Read its `url` field and proceed as if user
  passed that URL.

For non-URL prose ("audit my latest post"), ask which URL exactly.

### Step 2: Resolve focus_keyword + gsc_property

Priority order for `focus_keyword`:

1. CLI flag from user (`--focus-keyword "zone 2 訓練"`)
2. Frontmatter `focus_keyword` if input is a vault path
3. SEOPress `meta_keywords` / `_seopress_analysis_target_kw` (Usopp PR #101
   wired this — read-only via WP REST if needed)
4. Ask the user

Priority order for `gsc_property`:

1. CLI flag (`--gsc-property "sc-domain:shosho.tw"`)
2. URL host → `target_site` via `shared/schemas/site_mapping.py` →
   env `GSC_PROPERTY_SHOSHO` / `GSC_PROPERTY_FLEET`
3. If URL host not in `HOST_TO_TARGET_SITE`, GSC section is skipped
   (`gsc_section: skipped (non-self-hosted)`) — that is correct behavior,
   not an error.

### Step 3: Cost + Time Estimate (confirm before running)

| Component | Cost / Time |
|---|---|
| PageSpeed Insights | $0 (free quota) / ~10–30s real Lighthouse run |
| Deterministic checks | $0 / ~1s |
| LLM semantic (Sonnet 4.6, 12-rule batch) | ~$0.025–0.035 / ~5–8s |
| LLM semantic (Haiku) | ~$0.003 / ~3s |
| GSC query | $0 (free, 1 req) / ~3s |
| Robin KB search (Haiku ranker) | ~$0.005 / ~3s |
| **Total wall-clock** | ~25–60s |
| **Total cost** | < $0.10 |

Present:

```
預估：
  時間：~30-60s
  成本：~$0.05（Sonnet semantic + Haiku KB ranker）
  PageSpeed strategy：mobile
  GSC：<property or "skipped (non-self-hosted)">
  KB：<enabled / --no-kb>
  輸出：<out_path>
確認開跑？
```

Accept "go" / "確認" / "ok"; reject → return to scope adjustment.

### Step 4: Invoke audit.py

Run from repo root (script auto-prepends repo root to `sys.path`):

```bash
python .claude/skills/seo-audit-post/scripts/audit.py \
    --url "<url>" \
    --output-dir "<out_dir>" \
    [--focus-keyword "<kw>"] \
    [--gsc-property "sc-domain:shosho.tw"] \
    [--no-kb] \
    [--strategy desktop] \
    [--llm-level haiku|none]
```

`--llm-level=none` → skips L1-L12 (純 deterministic + PageSpeed + 可選
GSC/KB)，給 debug / quick check / 沒 ANTHROPIC_API_KEY 時用。

`--no-kb` → skips KB query + report §7。當 audit URL 與 KB 主題不重疊
（例如 audit 競品文章）時用。

### Step 5: Summary + Hand-off

After a successful run, present:

```
完成！耗時 X.Xs

輸出：<out_path>

Audit summary:
  Overall grade: <A/B+/B/C+/C/D/F>
  Pass / Warn / Fail / Skip: P/W/F/S
  Critical fails: <list rule_ids>
  GSC: <included / skipped reason>
  KB: <included with N suggestions / skipped reason>

下一步建議：
  → 把 critical 條目逐一修了再 re-audit
  → striking-distance opportunities → 補內文 + internal link
  → 內容嚴重不足 / 偏題 → 走 article-compose 重寫
  → Phase 2 上線後可走 seo-optimize-draft 自動重寫稿
```

---

## Output Contract (downstream consumers)

See `references/output-contract.md` for the frozen shape (frontmatter +
section order). The 7-section body has 5 mandatory + 2 optional sections;
section ordering is part of the schema_version=1 contract.

## Caveats (important)

1. **`agents/brook/compliance_scan.py` is SEED (6 patterns only)**. The
   audit reuses `scan_publish_gate()` as a quick signal for L9 LLM
   context. The LLM still does its own judgment — but L9 is known to
   give false-negatives on terms outside the 6-pattern vocab. Each L9
   `fix_suggestion` should mention the SEED status. Slice B medical
   vocab upgrade will lift this.
2. **`agents/robin/kb_search.search_kb` requires `purpose="seo_audit"`**
   to escape the YouTube prompt baked in for Zoro's pipeline. The
   pipeline calls it correctly — do NOT bypass.
3. **PageSpeed Insights has no API key** → audit raises
   `PageSpeedCredentialsError`. Fix: add `PAGESPEED_INSIGHTS_API_KEY`
   to `.env` per `docs/runbooks/setup-wp-integration-credentials.md` §2e.
4. **GSC quota is shared with `seo-keyword-enrich` (200 req/day)**. Each
   audit consumes 1 query. > 200 audits/day → adjust scheduling.
5. **CrUX field data (INP)** can be missing for low-traffic URLs →
   P2 returns `status="skip"`, not fail. Expected behavior.

---

## Cost

- **PageSpeed**: $0 (free quota)
- **GSC**: $0 (free quota, 1 query per audit)
- **LLM semantic**: ~$0.025–0.035 (Sonnet 4.6, 12-rule batch); ~$0.003
  (Haiku); $0 (`--llm-level=none`)
- **KB ranker**: ~$0.005 (Haiku, single call)
- **Total per audit**: < $0.10

---

## Open-Source Friendliness

This skill is part of the Nakama repo and intended to be extractable.
See `docs/capabilities/seo-audit-post.md` for the capability card.

Parameterized extension points:

- All side-effecting collaborators (`pagespeed_runner`, `gsc_querier`,
  `kb_searcher`, `compliance_scanner`, `llm_reviewer`) inject via
  `audit(...)` kwargs — open-source forks can swap modules without
  touching the orchestrator.
- `shared/schemas/site_mapping.py` is the single source for host →
  target-site lookup.
- LLM prompts live in `shared/seo_audit/llm_review.py` — fork can
  re-tune without modifying the pipeline.

## References

| When | Read |
|---|---|
| Output contract | `references/output-contract.md` |
| Rule catalog | `references/check-rule-catalog.md` |
| Deterministic rules | `shared/seo_audit/*.py` |
| LLM semantic | `shared/seo_audit/llm_review.py` |
| PageSpeed thin client | `shared/pagespeed_client.py` |
| GSC thin client | `shared/gsc_client.py` |
| KB ranker (purpose dispatch) | `agents/robin/kb_search.py` |
| Compliance SEED | `agents/brook/compliance_scan.py` |
| Capability card | `docs/capabilities/seo-audit-post.md` |
