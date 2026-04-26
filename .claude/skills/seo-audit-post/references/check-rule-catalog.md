# `seo-audit-post` Rule Catalog

40 rules total: 28 deterministic (Slice D.1) + 12 LLM semantic (Slice D.2).
Each rule emits a single `AuditCheck` with `{rule_id, name, category,
severity, status, actual, expected, fix_suggestion}`.

## Severity scale

| Severity | When |
|---|---|
| `critical` | Major SEO breakage — block publish until fixed |
| `warning` | Best-practice deviation — fix soon |
| `info` | Observation — fix opportunistically |

## Status values

| Status | Meaning |
|---|---|
| `pass` | Rule satisfied |
| `warn` | Minor deviation; review and decide |
| `fail` | Rule not satisfied — see `fix_suggestion` |
| `skip` | Data unavailable (e.g., CrUX field missing, KB unreachable) |

---

## 28 Deterministic Rules (Slice D.1)

### Metadata (M1-M5) — `shared/seo_audit/metadata.py`

| ID | Rule | Severity | Notes |
|---|---|---|---|
| M1 | `<title>` length 50-60 chars | warning | Nested-tag aware via `get_text()` (PR #181 fix) |
| M2 | `<meta name="description">` length 150-160 chars | warning | |
| M3 | `<link rel="canonical">` present and self-referential | critical | Case + query + relative urljoin (PR #181 fix) |
| M4 | `<meta name="robots">` does NOT include `noindex` | critical | |
| M5 | `<meta name="viewport">` includes `width=device-width` | warning | Mobile-first index requirement |

### OpenGraph + Twitter Card (O1-O4) — `metadata.py`

| ID | Rule | Severity |
|---|---|---|
| O1 | `og:title` AND `og:description` present | warning |
| O2 | `og:image` meta present | warning |
| O3 | `og:url` matches canonical | warning |
| O4 | `twitter:card` present | info |

### Headings (H1-H3) — `headings.py`

| ID | Rule | Severity |
|---|---|---|
| H1 | Exactly 1 `<h1>` | critical |
| H2 | No skipped levels (H1→H3 invalid) | warning |
| H3 | At least 1 `<h2>` in body | warning |

### Images (I1-I3) — `images.py`

| ID | Rule | Severity |
|---|---|---|
| I1 | All `<img>` tags have `alt` attribute | warning |
| I2 | All `<img>` have explicit `width` + `height` (CLS) | warning |
| I3 | First viewport image has `loading="eager"` (LCP candidate) | info |

### Structure (S1-S3) — `structure.py`

| ID | Rule | Severity | Notes |
|---|---|---|---|
| S1 | Word count ≥ 1500 | warning | CJK-aware (磷酸肌酸系統 = 6 字 not 1) |
| S2 | Internal links ≥ 2 | warning | Same-host = internal |
| S3 | External links ≥ 1 | info | Sub-domain counts as external |

### Schema Markup (SC1-SC4) — `schema_markup.py`

| ID | Rule | Severity |
|---|---|---|
| SC1 | Article schema present (incl. NewsArticle/BlogPosting/MedicalWebPage) | warning |
| SC2 | BreadcrumbList schema present | info |
| SC3 | Article.author = Person + name + url | warning |
| SC4 | All JSON-LD parses without error | critical |

### Performance (P1-P3) — `performance.py` (PageSpeed Insights)

| ID | Rule | Severity | Source |
|---|---|---|---|
| P1 | LCP < 2.5s | critical | Lighthouse lab `numericValue` |
| P2 | INP < 200ms | warning | CrUX field data (skip if low traffic) |
| P3 | CLS < 0.1 | warning | Lighthouse lab `numericValue` |

---

## 12 LLM Semantic Rules (Slice D.2)

All evaluated in a single Sonnet 4.6 batch call (cost ~$0.025-0.035 / audit).
On API error / JSON parse failure / response shape mismatch → all 12 marked
`status="skip"` (no retry, no pipeline break). Implementation:
`shared/seo_audit/llm_review.py`.

| ID | Rule | Severity | Why LLM |
|---|---|---|---|
| L1 | `<h1>` semantically covers focus keyword | warning | Substring not enough — synonym / 詞序 / 中英混排 |
| L2 | First paragraph (≤200 chars) covers focus keyword semantically | warning | Same as L1 |
| L3 | Focus keyword density reasonable (no stuffing) | warning | LLM detects stuffing pattern |
| L4 | Content answers user search intent | critical | Intent vs content fit |
| L5 | E-E-A-T Experience: first-person / case / photo evidence | warning | Subjective judgment |
| L6 | E-E-A-T Expertise: author bio / citations / credentials | warning | Combined author-info + content |
| L7 | E-E-A-T Authoritativeness: external mention hints | info | LLM picks up implicit signals |
| L8 | E-E-A-T Trustworthiness: HTTPS + privacy + contact | warning | Mix structural + LLM |
| L9 | Taiwan pharma / medical compliance | critical | Mandarin legal context; reuses compliance_scan SEED + LLM補抓漏網 |
| L10 | Schema-content consistency + internal-link opportunities | warning | LLM checks Article headline ≈ `<h1>` + KB cross-ref |
| L11 | Medical references (PubMed / DOI / 衛福部 / WHO) ≥ 2-3 | warning | YMYL signal beyond S3 link count |
| L12 | Last-reviewed date / 醫師審稿 / freshness markers | warning | Beyond SC3 author — "reviewed by" layer |

### L9 / L10 caveats

- **L9 SEED limitation**: `compliance_scan.MEDICAL_CLAIM_PATTERNS` has 6
  patterns only (治癒癌症 / 99.9% / 等). LLM補抓 is best-effort; the
  `fix_suggestion` template should mention "Phase 1 SEED — Slice B
  medical vocab upgrade pending".
- **L10 KB context required**: caller must inject `kb_context` from
  `agents.robin.kb_search.search_kb(query, vault, purpose="seo_audit")`.
  Empty KB context → L10 still runs but only checks schema-content side.

---

## Output guarantees

- Total rule count: 40 (28 + 12). The summary table in §1 of the report
  bins these by category.
- Order in `AuditResult.checks`: FETCH (pre-check) → metadata → headings
  → images → structure → schema → performance → semantic (L1-L12).
- Section in markdown report:
  §1 Summary, §2 Critical Fixes, §3 Warnings, §4 Info,
  §5 PageSpeed Summary, §6 GSC (optional), §7 KB Internal Links (optional).
