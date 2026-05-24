**1 — CODE GROUNDING**

`VaultBlobLoader` exists at `shared/blob_loader.py:52`, and the ADR’s line reference is correct. The description is mostly right, but tighten it: the loader is not simply “any vault-relative `.md` / `.epub`”; `data/books/...` is routed to `books_root`, while other paths route to `vault_root` (`shared/blob_loader.py:12-22`, `:99-114`, `:131-139`). That matters because book EPUBs are deliberately outside the vault.

`shared/annotation_store.py` exists and uses `KB/Annotations/{slug}.md`: `_annotations_dir()` returns `get_vault_path() / "KB" / "Annotations"` at `shared/annotation_store.py:98`, with `save()` and `load()` at `:129` and `:137`. But ADR-030’s “parse frontmatter + body” wording is wrong. It parses frontmatter plus a fenced JSON block, not arbitrary markdown body (`shared/annotation_store.py:288-341`).

`shared/brook_synthesize_store.py` is materially misrepresented. ADR-030 says it consumes `Projects/<slug>.synthesize.json` (`docs/decisions/ADR-030-vault-as-substrate-read-strategy.md:47`) and later says ADR-021 already has `Projects/<slug>.synthesize.json` (`:196`). Actual code writes `data/brook_synthesize/{project_slug}.json` (`shared/brook_synthesize_store.py:3`, `:78-95`, `:152-177`). ADR-021 agrees with the code: server-side store at `data/brook_synthesize/{project_slug}.json`, not vault sidecar (`docs/decisions/ADR-021-annotation-substance-store-and-brook-synthesize.md:163-168`, `:188-191`, `:241`).

`shared/digest_indexer.py` exists and matches the digest path claim: `_DIR_FOR` maps `pubmed` to `KB/Wiki/Digests/PubMed` and `ai` to `KB/Wiki/Digests/AI` (`shared/digest_indexer.py:29-31`). It reads latest/list/detail via filesystem, no DB mirror (`:57-113`).

`shared/kb_hybrid_search.py` exists, but ADR-030 gets the table/database names wrong. It is not `state.db` with `kb_chunks + kb_chunks_vec`; code says `kb_index.db`, separate from `state.db`, with `kb_chunks`, `kb_vectors`, and `kb_index_meta` (`shared/kb_hybrid_search.py:4-8`, `:43-49`, `:86-97`). Migration 012 confirms the same (`migrations/012_kb_hybrid.sql:4-9`, `:21-30`). Also, indexing is not all `KB/Wiki/**/*.md`: `shared/kb_indexer.py` scans `KB/Wiki/{Sources,Concepts,Entities}` recursively plus `KB/Annotations/`, not `Digests` (`shared/kb_indexer.py:5-8`, `:54-56`, `:141-168`, `:311-330`).

`shared/digest_ask.py` guardrails exist: `MAX_DAYS = 30`, `MAX_CONTEXT_CHARS = 200_000`, `MAX_OUTPUT_TOKENS = 2048` (`shared/digest_ask.py:26-31`). It enforces days at `:82-83`, char cap at `:116-118`, and empty-scope no-LLM short-circuit at `:123-131`. `shared/markdown_wikilinks.py` has `WikilinkResolver` at `:16`, and Bridge digests uses it (`thousand_sunny/routers/bridge_digests.py:44-45`, `:73-85`, `:209`).

**2 — DRIFT DETECTION**

No fatal contradiction with ADR-017, but ADR-030 oversimplifies Reader. ADR-017 says annotations live at `KB/Annotations/{slug}.md` and source mutation is prohibited (`docs/decisions/ADR-017-annotation-kb-integration.md:26-37`, `:84-93`). Code matches for paper/book annotations (`thousand_sunny/routers/books.py:536-595`; `thousand_sunny/routers/robin.py:301-341`, `:392-407`). But Reader is not purely “vault FS-direct”: book EPUBs and book metadata/progress sit outside vault / in DB (`shared/book_storage.py:12-16`, `:69-88`; `thousand_sunny/routers/books.py:605-638`).

ADR-021 is correctly characterized conceptually only if the path is fixed. Brook synthesize is server-side JSON, not vault markdown. ADR-030’s `Projects/<slug>.synthesize.json` wording is false and should be replaced everywhere with `data/brook_synthesize/{project_slug}.json`.

ADR-022 carve-out is directionally right but overclaimed. The implementation is a rebuildable hybrid index, but ADR-022 itself is about making BGE-M3 the default embedding and dimensional alignment (`docs/decisions/ADR-022-multilingual-embedding-default.md:19-29`, `:37-39`), not a full policy proof for “when mirrors are allowed.” D5 is a good new rule, but do not pretend ADR-022 already contained that whole rule.

ADR-024 aligns with `VaultBlobLoader` usage in implementation: promotion wiring constructs `VaultBlobLoader` and injects it into `PromotionPreflight` and `SourceMapBuilder` (`thousand_sunny/promotion_wiring.py:138-177`), while those services accept injected blob loaders (`shared/promotion_preflight.py:53-59`, `:219-295`; `shared/source_map_builder.py:61-67`, `:325-400`). ADR-024 itself talks at domain level, not code detail.

ADR-028 strongly aligns with D1: `docs/VAULT-LAYOUT.md` declares `KB/Wiki/Digests/*` and `KB/Annotations/*` as vault contracts (`docs/VAULT-LAYOUT.md:185-188`) and says Brook synthesize store is not in vault (`:409`). It also supports the repo-vs-vault heuristic (`:293-304`).

`thousand_sunny/CONTEXT.md` mostly matches ADR-030 (`:7-24`, `:43-55`), but it has one path drift: it says PubMed digest uses `{YYYYMMDD}.md` (`thousand_sunny/CONTEXT.md:30`), while code and Vault Layout use `{YYYY-MM-DD}.md` (`shared/digest_indexer.py:8-9`; `docs/VAULT-LAYOUT.md:185-186`). Fix the context doc.

Issue #231: I could not verify the GitHub issue body because `gh` cannot read its config in this environment, and the public web result was not accessible. Local evidence supports the constraint: `thousand_sunny/routers/bridge_zoro.py:12-13`, `tests/test_bridge_zoro.py:384-404`, and `thousand_sunny/CONTEXT.md:48-54` all cite “Bridge no vault writes.” But ADR-030 should not say “Issue #231 says X” unless the issue text is checked in or linked in a reviewer-accessible way.

**3 — NUMERICAL / FACTUAL CLAIMS**

Current Sonnet 4.6 pricing is verified: Anthropic states $3/M input and $15/M output, with 1M context available in API beta (`claude-sonnet-4-6`) . The “1M-context Sonnet is already shipped” claim is therefore acceptable if ADR-030 says API beta, not universally available.

The `$0.18/query` math is wrong as written. ADR-030 says “200k char (~70k token) digest concat + 2k output ≈ $0.18” (`ADR-030:62`). At $3/M input and $15/M output, 70k input tokens cost $0.21 and 2k output costs $0.03, total $0.24. `$0.18` implies about 50k input tokens plus 2k output. Worse, code caps Python string length, not UTF-8 bytes (`shared/digest_ask.py:28`, `:116`). Code’s own comment says 200,000 chars is “~150k input tokens worst case” (`shared/digest_ask.py:28`), which would cost about $0.48 with 2k output. Fix this.

The “200KB chars ≈ 70k tokens” claim is also sloppy. If “KB” means bytes, Chinese UTF-8 changes the character count; if “chars” means Python `len()`, token count can be much higher. The implementation is char-count based, so ADR-030 must talk in chars and treat token cost as estimated, not verified.

The “~50% lower per 12 months across last 3 years” cost trajectory is hand-wavy. Anthropic’s 2024 Claude 3.5 Sonnet announcement already had $3/M input and $15/M output with 200K context ; Sonnet 4.6 is still $3/$15 in 2026 . Capability per dollar has improved, but nominal per-token Sonnet pricing has not halved annually across that observable window.

The PubMed digest size claim is plausible. In the local Windows vault, `E:\Shosho LifeOS\KB\Wiki\Digests\PubMed` has 34 files, 409,058 bytes total, average 11.75 KB/file, min 5.13 KB, max 13.6 KB. `~10KB/day` and `~3.6MB/year` are defensible as a round estimate.

The KB Wiki claim is false against the accessible vault. `E:\Shosho LifeOS\KB\Wiki` has 3,124 markdown files totaling 20,538,954 bytes, about 19.6 MB. Breakdown: `Concepts` 2,762 files / 7.3 MB, `Sources` 304 files / 11.8 MB, `Digests` 56 files / 0.44 MB. Replace “200+ pages, all under 5MB total.”

“Question volume estimated <10/day across all surfaces” is not verifiable here. Local `state.db` is zero bytes, and no usage telemetry was available. Mark it as an owner estimate, not a verified fact.

**4 — ASSUMPTION PUSH-BACK**

D3 and the rationale lean too hard on “LLM cost crashes.” Do not build the decision on nominal per-token price decline. The evidence supports “model capability and context are improving at roughly stable Sonnet price,” not “50% cheaper every year.” Rewrite the bet as: “The maintenance cost of per-surface retrieval exceeds expected single-user LLM spend under explicit caps.” That is defensible.

“Single-user, no concurrent query load” is true for Bridge now (`thousand_sunny/CONTEXT.md:36-37`), but it is not a property of Nakama forever. Sanji/community and any partner-facing surface must be excluded from this ADR by default. Add a D3 boundary: LLM-over-vault is allowed only for authenticated owner-only surfaces unless a separate ADR budgets multi-user load.

“Markdown is human-readable across all devices including offline” is half true. Reading local markdown is offline; having the right markdown on the device depends on Syncthing health. ADR-030 mentions Syncthing but does not make sync failure a first-class risk. Add a consequence: FS-direct consumers must surface missing-path errors as “vault not synced / path contract drift,” not generic 404.

The D5 carve-out conditions are too subjective. “Corpus exceeds what a 1M-context LLM can chew,” “sub-second retrieval,” and “deterministic rebuild” are good, but reviewers need numbers. Require PRs to report measured corpus bytes/tokens, p95 latency target, rebuild command, and freshness SLA. Without that, every future engineer can claim condition (a) or (b).

The cross-over point “>1MB and >100/day” is unsupported (`ADR-030:140`). Local current 30-day digest scope is already 269,943 chars / 405 KB before cap and truncates at 195,280 chars; 1 MB is close, not architectural destiny. Either remove thresholds or derive them from cost math and latency budgets.

D4’s routing rule is right but incomplete for mixed records. A long-form analysis with structured metadata should be split: markdown body in vault, queryable metadata/progress in state or a derived index, with an explicit pointer key. The existing book design already does this: EPUB files outside vault, book metadata/progress in DB, annotations in vault.

**5 — ALTERNATIVES NOT CONSIDERED**

Claude’s “mirror vs no mirror” framing is too binary. The missing alternative is FS-direct for known paths plus lightweight FTS-only lookup for “which file?” discovery. That is not the same as full RAG. It avoids embeddings, avoids vector rebuilds, and can still return whole files to the LLM. ADR-030 should evaluate this before rejecting all index support for digest/Q&A surfaces.

Cache-warmed concat is also missing. For daily digests, the writer could emit a rolling `Digests/_bundles/pubmed-ai-14d.md` or metadata manifest at write time. `/digests/ask` would read one bundle and check mtime, instead of walking N files. Trade-off: derived artefact and sync drift versus faster reads and predictable context. Given the current code already truncates at 21 days of the 30-day scope in my vault measurement, precomputed bundles could also declare exactly what is included.

Streaming context growth would improve UX. Today the UI only shows `已截斷` and char count (`thousand_sunny/templates/bridge/digest_ask.html:200-201`). It does not tell the user which dates were dropped. D3 should require surfacing the oldest included date and dropped count. Better: let the user choose “newest first / PubMed only / include older with lower cap.”

Finally, ADR-030 is too global. Reader, Brook, and Digest have different read semantics: Reader is whole-file plus annotation store; Brook is hybrid retrieval plus server-side review JSON; Digest is time-window concat. A global default is useful, but the ADR should explicitly say “default policy,” not “this explains all surfaces equally.” Otherwise future reviewers will use Digest assumptions to judge Reader or Brook incorrectly.

**6 — FINAL VERDICT**

Approve with modifications. The core decision is sound: vault markdown as canonical knowledge substrate, FS-direct for known paths, LLM-over-vault for bounded single-user ad-hoc questions, and ADR-022 hybrid retrieval as a derived-index carve-out. But ADR-030 currently contains enough false implementation and cost claims that approving as-is would bake drift into the architecture record.

Top required changes:

1. Fix Codebase Inventory: `brook_synthesize_store` path, annotation parsing, `kb_vectors`/`kb_index.db`, and `kb_indexer` scope.
2. Rewrite Context cost math: remove `$0.18` unless recomputed; distinguish chars, bytes, and tokens; remove “50%/year” or mark it as a non-binding optimism.
3. Replace KB corpus counts with measured current values or label them as stale examples.
4. Strengthen D5 with measurable carve-out criteria and remove unsupported `1MB / 100/day` thresholds.
5. Amend D3 UX guardrails to require explicit truncation disclosure: oldest included date, dropped files/days, and no silent scope loss.

VERDICT: approve-with-mods
