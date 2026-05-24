# ADR-030: Vault-as-Substrate Read Strategy

**Date:** 2026-05-24 (v1) · 2026-05-24 (v2 post-panel)
**Status:** Accepted
**Deciders:** shosho-chang, Claude Opus 4.7
**Related:** ADR-017 (Reader annotation store), ADR-021 (Brook synthesize HITL), ADR-022 (KB hybrid retrieval — BGE-M3 default), ADR-024 (Promotion / Sources of Truth), ADR-028 (Vault layout consolidation), ADR-029 (Bridge IA restructure), CONTEXT-MAP.md, `thousand_sunny/CONTEXT.md`
**Tier A implementation reference:** PR #689 (`shared/markdown.py` + wikilink resolver) · PR #690 (`/bridge/digests` + detail) · PR #692 (`/bridge/digests/ask` LLM-over-vault)

> **v2 audit trail (2026-05-24):** Multi-agent panel review (Claude + Codex GPT-5 + Gemini 2.5 Pro) ran on v1.
>
> - **Codex** caught 6 factual errors in v1 codebase inventory (`brook_synthesize_store` path, KB hybrid DB name + table names, annotation_store body shape, Reader's mixed substrate, ADR-022 overclaim, `thousand_sunny/CONTEXT.md` `{YYYYMMDD}` drift) + 5 cost-math errors (`$0.18/query` wrong even for English; "50%/year cost decline" unsupported; KB Wiki size wrong by 4×) + 4 architectural pushbacks (multi-user boundary missing, Syncthing as first-class risk, D5 conditions too subjective, mixed-record routing rule gap).
> - **Gemini** caught Tokenization economics for CJK (200KB chars ≈ 100–130k tokens not 70k → real cost ≈ $0.39/query, **2× v1's claim**), Syncthing `*.sync-conflict-*` files silently ignored by `digest_indexer` (**real data-loss bug**), archival query gap (D3's `MAX_DAYS=30` makes "what did I think about X in 2027" impossible), and reframed observability gap as architectural flaw not UX papercut.
> - **Direct verifications** (panel both pointed at code; I confirmed against repo): all 6 Codex factual errors verified, all 4 Gemini measurements verified.
>
> Owner adjudicated 22 distinct push-back items: 17 adopted (15 verbatim + 2 modified), 3 escalated to "documented as known gap, not committed to fix in this ADR" (vault-as-graph, multi-modal future, provider diversity), 2 rejected with rationale (D4 reframe to transactional-guarantees, archival query promotion to D5 default).
>
> Audits preserved at `docs/research/2026-05-24-codex-adr030-audit.md` and `docs/research/2026-05-24-gemini-adr030-audit.md`.

---

## Context

Nakama runs two parallel data substrates today, and the boundary between them was never written down:

- **Vault** (`KB/`, `Projects/`, `Daily/`, `Digests/` under the Obsidian root) — markdown artefacts that humans read directly. Synced cross-device via Syncthing (VPS + Windows + Mac). Mutation: low frequency.
- **`state.db`** (SQLite) — operational state: `api_calls`, `drafts`, `user_memories`, `audit_results`, `approval_queue`. Machine-read-write intensive, structured, high mutation rate.

Until 2026-05, every new feature triggered a "where should this live?" debate. The drift accelerated as the Bridge UI added surfaces consuming vault content:

- **Reader** (ADR-017) reads `KB/Annotations/{slug}.md` *plus* `data/books/` EPUBs *plus* book metadata/progress from `state.db` — hybrid substrate, not pure-vault.
- **Brook synthesize review** (ADR-021) reads `KB/Wiki/Sources/*.md` and writes a server-side JSON store at `data/brook_synthesize/{project_slug}.json` (NOT a vault sidecar — corrected from v1 which had it under `Projects/`).
- **Promotion preflight / source-map builder** (ADR-024) reads variant bytes via `shared/blob_loader.py:52` `VaultBlobLoader`, with explicit dual root: `data/books/...` → `books_root`, everything else → `vault_root`.
- **Digest viewer** (Tier A PR #690, this ADR's motivating implementation) reads `KB/Wiki/Digests/{PubMed,AI}/{YYYY-MM-DD}.md`.

修修's framing during the 2026-05-24 grilling session:

> 「LLM 一定會越來越強、越來越便宜，而 Obsidian 又是一個很適合 LLM 去操作的工具。」

The implication: betting against future LLM capability is the wrong bet. The read layer should treat "more LLM" as a free upgrade, not a re-architecture. **v2 caveat (Codex): nominal per-token Sonnet pricing has been flat from 3.5 Sonnet (2024) to 4.6 (2026) — the bet is on capability-per-dollar growth and context-window growth, not headline price decline.**

### Codebase inventory (verified 2026-05-24 against repo)

**Vault read pathways in production:**

| Module | Vault path consumed | Body shape | Read mechanism | Caller surfaces |
|---|---|---|---|---|
| `shared/blob_loader.py:52` `VaultBlobLoader` | vault-relative paths (`KB/...`, `Inbox/...`) OR `data/books/...` (separate `books_root`) | bytes | sandboxed FS read | promotion preflight, source-map builder, reader |
| `shared/annotation_store.py:98,129,137` | `KB/Annotations/{slug}.md` | frontmatter + **fenced JSON block** (not arbitrary markdown body) | parse | `/books/{id}` reader |
| `shared/brook_synthesize_store.py:79-92` | **NOT vault** — `data/brook_synthesize/{slug}.json` (env `NAKAMA_DATA_DIR` override, repo-relative fallback) | structured JSON | file IO | `/brook/projects/{slug}` |
| `shared/digest_indexer.py:29-31` (PR #690) | `KB/Wiki/Digests/PubMed/{YYYY-MM-DD}.md` + `KB/Wiki/Digests/AI/{YYYY-MM-DD}.md` | frontmatter + markdown body | parse | `/bridge/digests*` |
| `shared/kb_hybrid_search.py:48-49,86,97` (ADR-022) | indexed copy of `KB/Wiki/{Sources,Concepts,Entities}/` + `KB/Annotations/` (per `shared/kb_indexer.py:5-8,141-168`) into **`kb_index.db`** with `kb_chunks` (FTS5) + `kb_vectors` (vec0 1024-dim) | derived index, not source | rebuildable mirror | Robin keyword research, Brook synthesize |

**Outlier: ADR-022 KB hybrid retrieval *does* mirror vault content** — but into a separate `kb_index.db` (not `state.db`, corrected from v1), only over the `Sources / Concepts / Entities / Annotations` subset (not all `KB/Wiki/**`, corrected from v1). This is the principled carve-out (see D5). It is **not** the default.

**Vault corpus measurements (Asia/Taipei vault, `E:/Shosho LifeOS/`, 2026-05-24):**

- `KB/Wiki/`: **3,124 markdown files / ~20MB** (Concepts 2,762 files / 7.3MB · Sources 304 files / 11.8MB · Digests 56 files / 0.44MB). v1's "200+ pages, <5MB" was wrong by ~4× — replaced.
- `KB/Wiki/Digests/PubMed/`: 34 files / 409KB / avg 11.75KB/file. v1's "~10KB/day" estimate holds.

**Concurrent writers to vault:**

- **Agent-side**: Robin (PubMed digest), Franky (AI digest), Reader (annotation pages), Brook indirectly via Usopp (web inbox).
- **Human-side**: 修修 in Obsidian desktop on Windows / Mac.
- **Forbidden writers**: Bridge UI — local evidence confirms (`thousand_sunny/routers/bridge_zoro.py:12-13`, `tests/test_bridge_zoro.py:384-404`, `thousand_sunny/CONTEXT.md:48-54` all cite "Bridge no vault writes"). Originally framed as Issue #231; the constraint stands regardless of issue-tracker availability.

### LLM cost trajectory the design bets on (v2 corrected)

- **Sonnet 4.6 today (2026)**: $3/M input, $15/M output (Anthropic API). 1M context in API beta.
- **Single `/digests/ask` call cost** — v1 claimed $0.18, **wrong**:
  - 200KB Python `len()` chars of mixed Traditional Chinese + English at typical CJK-heavy ratio (Gemini measurement: 1 char ≈ 1.5–2 tokens for primary-Chinese content) ≈ **100,000–130,000 input tokens**.
  - Worst-case 130k input × $3/M + 2k output × $15/M = **$0.42/query**. Typical case 100k input ≈ **$0.33/query**.
  - **Use $0.30–0.45/query for budget forecasting**, not the v1 figure. Monthly cost projection at ~10 queries/day = **$90–135/month**, not v1's $20–50.
- **Trajectory the bet rests on** (v2 reframed): not nominal per-token price decline (which has been flat 2024→2026), but **capability-per-dollar growth + context-window growth + cheaper-tier diversity**. By 2027 the same call may be servable by a cheaper Haiku-class model at 30% the cost, OR by a 4M-context Sonnet that obviates manual scope cap.
- **Defensible reframing of the bet** (per Codex push-back): "**The maintenance cost of per-surface retrieval infrastructure exceeds expected single-user LLM spend under explicit caps**." This is the bet, not "tokens will get cheaper."

---

## Decision

### D1: Vault is the **canonical knowledge substrate**

All durable knowledge artefacts that humans also read (`KB/`, `Projects/`, `Daily/`, `Digests/`) live as markdown in the Obsidian vault. There is **no parallel structured copy** of vault content unless explicitly scoped by D5.

The vault is the source of truth, not a staging surface. Any agent / Bridge / external consumer reads from vault directly; nobody waits for sync to a DB before consuming.

### D2: Read pattern A — **FS-direct** for deterministic access

When the caller knows the path it wants, read directly via `shared.blob_loader.VaultBlobLoader` or a domain-specific reader (`shared.digest_indexer`, `shared.annotation_store`).

**No DB mirror, no index lookup, no cache by default.** Add caching only when a measured hot path warrants it.

### D3: Read pattern B — **LLM-over-vault** for *recent* ad-hoc query

When the caller has a natural-language question that spans multiple files within a known **recency-bounded** scope (date range, type filter, path glob), concat the in-scope files and feed them to the LLM in one shot. Reference: `shared.digest_ask` (PR #692).

**Scope boundary (v2 added per Gemini archival-query push-back):** D3 is a strategy for **recent** ad-hoc queries. It is **unsuitable for archival queries** ("summarize my thoughts on intermittent fasting from 2027") because:

- `MAX_DAYS` cap makes querying older content impossible.
- Even without the cap, archive corpus eventually exceeds 1M context.

Archival queries are a known gap. They will require a D5-style indexed solution if the use case emerges. **Do not extend D3's `MAX_DAYS` past a small constant to "make it work" for archival.**

**Required guardrails** (every D3 surface must apply all four):

- **Scope cap**: maximum concatenated input bounded by character count (PR #692: 200KB). Older / less relevant items drop out first; never silently truncate mid-file.
- **Cost cap**: bound the input dimension (days, glob breadth) before concat. PR #692: `MAX_DAYS=30`.
- **Empty-scope short-circuit**: if no files match, return synthesised "nothing in range" without calling the LLM.
- **Explicit truncation disclosure** (v2 added per Codex+Gemini): UI must surface oldest included date and count of dropped items (e.g., "Answer based on 21 digests from 2026-05-04 to 2026-05-24. 9 older digests were excluded to meet the context limit."). Silent `已截斷` flag is insufficient.

**Multi-user boundary (v2 added per Codex):** D3 is allowed only for **authenticated owner-only** surfaces (single-user Bridge). Any future partner-facing surface (Sanji community, public-facing tools) must NOT use D3 without a separate ADR budgeting multi-user load and per-tenant cost caps.

**Provider note (v2 added per Gemini):** Tier A implementation hard-codes `claude-sonnet-4-6`. This is intentional for the first surface — provider diversity is deferred to a future ADR. The lock-in is acknowledged; reviewers should flag if a second surface follows the pattern without re-evaluating.

### D4: **Knowledge layer vs State layer — parallel substrates, not hierarchy**

The two substrates are **not a pyramid** with vault at the bottom; they are **siblings** with disjoint mutation profiles:

| Layer | Substrate | Content shape | Mutation freq | Reader |
|---|---|---|---|---|
| **Knowledge** | vault (markdown) | long-form, human-readable, wikilink-cross-referenced | low (agent cron / human edits) | humans + LLM |
| **State** | `state.db` (SQLite) | structured records, FSM transitions, audit trails | high (per-request) | machines |

**Routing rule for new features:**

- High-frequency mutation OR machine-only consumer OR structured query needed → **state.db**
- Long-form OR human reads it directly in Obsidian → **vault**
- **Mixed records** (long-form body + queryable metadata) → **split** with explicit pointer key. The book reader is the canonical example: EPUB bytes in `data/books/`, metadata + progress in `state.db`, annotations in vault `KB/Annotations/`. Vault frontmatter is fine for low-mutation metadata (tags, dates) but NOT for FSM state or high-mutation flags.

**v2 nuance**: a digest's frontmatter `status: draft, confidence: 0.8` lives in vault frontmatter (low-mutation, versioned with content). A draft's `approval_queue_state: pending_review` lives in `state.db` (FSM, high-mutation, atomic transitions required). When in doubt, default to `state.db`.

### D5: **Carve-out: derived indexes allowed under measured conditions**

A derived mirror (FTS / embedding / structured index rebuilt from vault) is allowed when **all three** measured conditions hold:

1. **Corpus size**: measured bytes/tokens exceed what a 1M-context LLM can ingest in one shot, OR LLM latency exceeds user tolerance for the use case.
2. **Latency requirement**: documented p95 latency target sub-second OR sub-LLM-call (a number, not "fast").
3. **Rebuild determinism**: rebuild command exists, runs in <1h on dev box, produces byte-identical output from same vault state. Freshness SLA documented.

**ADR-022 KB hybrid retrieval** (`kb_index.db` with `kb_chunks` FTS5 + `kb_vectors` vec0 1024-dim) is the existing carve-out: 3,124 files × ~20MB corpus over `Sources/Concepts/Entities/Annotations`, sub-second hybrid retrieval required for Brook synthesize's interactive evidence review, rebuild via `shared.kb_indexer` is deterministic. ADR-022 itself documents the embedding default; **the D5 carve-out rule is new in this ADR — do not claim ADR-022 contained it**.

**Carve-out PR requirements** (v2 strengthened per Codex+Gemini): any new carve-out proposal must report:

- Measured corpus bytes/tokens
- p95 latency target
- Rebuild command + measured wall time
- Freshness SLA (how stale can the index be relative to vault?)
- Why D2 or D3 wouldn't fit (with numbers)

Without these, the carve-out is rejected. The bar is high by design.

### D6: **Wikilink semantics rendered, not stripped**

Vault markdown contains `[[target]]` and `[[target|display]]` syntax. Bridge surfaces render via `shared/markdown_wikilinks.py:16` `WikilinkResolver` (PR #689) with per-surface URL mapping:

- Resolvable targets (e.g., `pubmed-{id}` → external PubMed) render as `<a class="wikilink">`
- Unresolved targets render as `<span class="wikilink-broken">` (no dead `href`, gray dotted styling)

**CJK robustness gap (v2 added per Gemini)**: resolver behavior on CJK-bearing targets (`[[健康-2024]]`) is **not yet tested**. Specific risks:

- URL encoding of CJK chars in `href`
- Filename normalization mismatch (macOS NFD vs Linux/Windows NFC) producing apparent-broken-but-actually-existing links
- Case sensitivity differences across filesystems

A follow-up test pass is needed before the resolver is used on CJK-heavy vault subdirs. Flagged, not blocking.

---

## Rationale

### Why vault is SoT (vs DB mirror)

**For**: Markdown is human-readable across all devices including offline. Obsidian's plugin ecosystem (Dataview / Templater / Tasks) gives 修修 query power on the desktop without engineering work. Syncthing tri-sync ensures no single-host dependency. The mirror would add: cron sync drift, two-source-of-truth bugs, ingestion failure modes.

**Against (rejected)**: A DB mirror enables sub-100ms FTS / filter / sort queries. But (a) current corpus size is small even after v2 correction (digests: ~3.6MB/year; KB Wiki: ~20MB total), (b) Bridge surfaces are inherently single-user, no concurrent query load, (c) the D5 carve-out already exists for the one workload that needed it.

### Why LLM-over-vault (vs RAG / embedding retrieval) for recent ad-hoc query

**For** `/digests/ask`-style **recent** queries: corpus fits in context (PR #692 caps at 200KB ≈ 100–130k CJK tokens of 1M available). LLM sees full digest body, not chunked snippets, so it can quote and cite naturally. No embedding index to keep in sync. No retrieval recall/precision tuning. Zero infrastructure beyond an HTTP call.

**Cross-lingual benefit (v2 added per Gemini)**: D3 sidesteps the cross-lingual embedding-alignment problem inherent in CJK+English RAG. The LLM sees mixed-language text directly and uses internal multilingual representations. This is an unstated but real strength of the pattern.

**Against (rejected for default)**: At ~$0.30–0.45/query (v2 corrected), ad-hoc questions are noticeably more expensive than a local FTS hit (~$0). But the question volume is low (owner estimate: <10/day across surfaces — **not verified by telemetry**, flagged as estimate not fact), and the engineering cost of building/maintaining FTS for each new surface is 2–3 weeks. Cross-over point is captured in D5's measurement criteria, not as a hardcoded threshold.

### Why the carve-out (D5) is principled, not ad-hoc

ADR-022's KB hybrid retrieval was built because Brook synthesize needs sub-second retrieval across thousands of pages, called interactively while 修修 reviews evidence. All three conditions (corpus size, latency requirement, deterministic rebuild) are satisfied with measurements.

**Floodgate risk (v2 added per Gemini)**: the conditions are objective but the burden of proof is on the proposer. The archival query use case (queries beyond `MAX_DAYS=30`) will eventually press on condition (a). When it does, expect to either expand D5's coverage (with measurements) OR build a second carve-out with its own ADR. Either is fine — the rule is "carve-outs require numbers", not "no carve-outs ever."

### Four limitations (restated from v1, all still apply)

1. **Obsidian CLI is desktop-only** — `obsidian eval` (Dataview / Tasks / Templater runtime) requires Obsidian app running. VPS-side Bridge cannot use it. Future Tier B (LifeOS Dashboard mirror) must either Python-rewrite aggregations OR adopt desktop-resident agent topology.
2. **Vault is not the only substrate** — D4 is normative. Don't bend it because vault is "easier."
3. **"Feed it everything" is bounded** — D3 guardrails are non-negotiable. CJK tokenization (v2) makes the cap *more* important than v1 implied.
4. **Read path is clean, write path is hard** — Bridge does not write vault. Future Bridge→vault must go through agent path (Usopp draft → vault) or `obsidian create` CLI on a desktop-resident agent.

---

## Consequences

### Positive

- **One mental model for "where does this data live?"** — D4 routing rule resolves the recurring debate.
- **Bridge surfaces ship faster** — Tier A's three PRs (689/690/692) shipped read + Q&A in ~1 working day because patterns are template-able.
- **Future-proof against capability growth** — when context windows expand or cheaper-tier models cover the use case, raising `MAX_CONTEXT_CHARS` / `MAX_DAYS` is a one-line change.
- **No sync infrastructure to maintain** — Syncthing handles cross-device; consumers read the same files.
- **Cross-lingual queries work natively** — D3 avoids embedding-alignment failure modes.

### Negative

- **D3 queries cost real money** — v2 corrected: **$0.30–0.45/call**. At ~10 queries/day this could reach **$90–135/month**, materially higher than v1's $20–50 estimate. Mitigation: D3 guardrails (especially scope cap); if it overshoots, fall back to tighter `MAX_DAYS` or a per-surface carve-out.
- **No sub-100ms ad-hoc query** — every D3 hit has multi-second latency. Acceptable for async "ask the digest" flows; unacceptable for inline-as-you-type. Latter must use D2 or D5.
- **Vault path drift breaks consumers silently** — Robin/Franky path change ⇒ every Bridge reader 404s. Mitigation: ADR-028 names paths as a versioned contract; this ADR reinforces it. FS-direct consumers should surface "vault not synced / path contract drift" rather than generic 404.
- **Syncthing `*.sync-conflict-*` files are silently ignored** (v2 — Gemini find): current `digest_indexer._DATE_RE = ^\d{4}-\d{2}-\d{2}\.md$` excludes conflict files (`2026-05-24.sync-conflict-...md`). User edits made during sync drift won't surface in the viewer or Q&A. **Real data-loss scenario, not theoretical.** Mitigation deferred to a follow-up issue; reviewers must check this when extending D2 to other vault subdirs.
- **CJK tokenization makes cost cap tighter than v1 implied** — same 200KB cap covers fewer "logical" papers than English-tokenization assumptions suggest. Re-evaluate cap if Q&A coverage feels inadequate.
- **D5 invites future carve-out creep** — measurement burden is the line. Each new mirror proposal must defend it with numbers.
- **Lock-in to Anthropic** — single-provider Tier A. Acknowledged, not addressed in this ADR.
- **Archival query gap** — D3 doesn't serve queries older than `MAX_DAYS`. No path defined.
- **Multi-modal future not addressed** — D3/D5 are text-only; future image/audio/video knowledge will need parallel decisions.
- **Observability gap** — current implementation logs to `api_calls` but doesn't surface to user which files were included/excluded in their query. Gemini reframed this as architectural debt, not UX papercut. Future iteration should plumb "scope used" through to UI.

### Neutral / observed

- **修修 stays on Obsidian for direct edits** — desktop UX > web for long-form authoring. Bridge is read + ops surface, not editor.
- **Agents continue writing vault** — they own write paths to their subdirectories per ADR-028 + Vault Writing Rules.

---

## Alternatives considered

### A1: Mirror vault to Postgres / SQLite for FTS (rejected)

Build a watcher ingesting every vault `.md` change into a search index. Bridge surfaces query the index.

**Rejected**: 2–3 weeks engineering per surface, ongoing sync-drift bugs, two sources of truth, no improvement for D3's "feed body to LLM" use case (index just speeds up "which file"). D5 covers the one workload where this actually helps.

### A2: Single retrieval API for all reads (rejected)

Force every consumer through `shared.vault_search(query) -> list[chunk]`.

**Rejected**: premature abstraction. Current consumers have heterogeneous needs (Reader: whole files; digest viewer: frontmatter + body; Brook synthesize: chunked retrieval). Hiding behind one API forces lowest common denominator. Shared pieces (`WikilinkResolver`, `MarkdownIt + bleach`) are already extracted (PR #689).

### A3: Move state.db content into vault as JSON sidecars (rejected)

"One substrate" appeal. ADR-021 already had one sidecar (server-side JSON, but in `data/` not vault — so this would be "everything in vault" version).

**Rejected**: high-mutation state (`api_calls`, `approval_queue` FSM) in markdown/JSON would (a) flood Syncthing with diffs, (b) lose transactional semantics, (c) make atomic multi-row updates impossible.

### A4: RAG with embeddings as the default ad-hoc query mechanism (rejected for default)

Use embeddings for every cross-day question instead of full concat.

**Rejected as default**: at current corpus sizes, full concat fits and yields better answers (no retrieval recall loss, native citation). Embeddings attractive when corpus crosses D5's measured thresholds — that's the carve-out, not the default. We will re-evaluate per-surface when D5 conditions actually hit.

### A5: FS-direct + lightweight FTS-only "which-file" lookup (v2 added — considered, deferred)

Per Codex: not all-or-nothing between D2 and D5. A middle ground exists: keep D2 for known paths, build a no-embedding SQLite FTS5 index for "which file matches keyword X" lookup, then read full files via D2.

**Status: deferred, not rejected.** This would help when D2's "know the path" assumption breaks (e.g., 修修 wants "the digest where I noted GLP-1 trial"). For now D3 handles it by concat-and-ask, which is more expensive but zero infrastructure. Re-evaluate if D3 monthly cost exceeds $200/month sustainably.

### A6: Cache-warmed concat (rolling bundles) (v2 added — considered, deferred)

Per Codex: at digest write time, emit `Digests/_bundles/pubmed-ai-14d.md` containing the rolling concat. `/digests/ask` reads one bundle, checks mtime.

**Status: deferred.** Trade-off favors current design at single-digit QPS: one extra write per day vs cheaper read per query. Cost asymmetry doesn't justify the complexity at current usage. Re-evaluate when query volume grows.

### A7: Per-surface ADR instead of global default (v2 added — rejected for now)

Per Codex: Reader, Brook, Digest have different read semantics. Maybe 3 separate ADRs instead of one global default.

**Rejected**: the value of ADR-030 is **naming the shared pattern** so the next surface doesn't re-debate it. Per-surface ADRs would still need this one as the framing layer. Re-evaluate if a 4th surface's read pattern materially diverges from D2/D3/D5.

---

## Open questions resolved

- **Is Tier B (LifeOS Dashboard mirror) blocked by this ADR?** → No. Tier B explicitly may require a desktop-resident agent (limitation #1). When it ships, its ADR can either Python-rewrite vault aggregations OR call Obsidian CLI from a desktop agent.
- **Does this affect ADR-022?** → No. ADR-022 is the existing D5 carve-out. This ADR's D5 rule is **new** (do not retroactively attribute it to ADR-022).
- **Does this affect Reader (ADR-017)?** → Partially. Annotation reads are D2 (FS-direct from `KB/Annotations/{slug}.md`). EPUB binaries are D2 via `VaultBlobLoader` against `books_root`. Book metadata + progress live in `state.db` (D4 split — book is the canonical mixed-record example).
- **Does this affect Brook synthesize review (ADR-021)?** → Partially. The review page reads `data/brook_synthesize/{slug}.json` (server-side store, **not vault**). The evidence pool comes from ADR-022 KB hybrid retrieval (D5 carve-out). Both stay.
- **Multi-modal future?** → Out of scope for this ADR. Acknowledged as a gap. When images/audio/video become first-class knowledge artefacts, a separate ADR will decide their substrate.
- **Provider diversity?** → Out of scope. Acknowledged as Tier A simplification. When a second surface demands non-Anthropic capability, a separate ADR will introduce provider routing.
- **Vault-as-graph reasoning?** → Out of scope. D3 flattens the graph by design (concat); future graph-traversal LLM capability would re-open this. Not committed.

---

## Migration plan

This ADR documents an existing pattern made explicit; no migration of existing code is required.

**Forward enforcement** (every new vault-consuming feature):

1. PR description names which read pattern (D2 / D3 / D5).
2. If D3, the PR shows all four guardrails (scope cap / cost cap / empty-scope short-circuit / explicit truncation disclosure).
3. If D5, the PR reports the four measurements (corpus / latency / rebuild / freshness).
4. CONTEXT-MAP "vault" glossary or surface-specific `CONTEXT.md` updated when read patterns change.

**Follow-up issues opened by this ADR** (not blocking acceptance):

- **Fix `thousand_sunny/CONTEXT.md` path drift**: line 30 says `{YYYYMMDD}.md` for PubMed; actual is `{YYYY-MM-DD}.md` (Codex find).
- **Syncthing conflict file handling**: `digest_indexer` and any future D2 reader must decide policy for `*.sync-conflict-*.md` (silently ignored today — Gemini find).
- **CJK wikilink resolver test pass**: verify URL encoding + filesystem normalization + case-sensitivity on CJK-bearing targets (Gemini find).
- **Truncation UX**: `/digests/ask` template must surface oldest included date + dropped count (Codex + Gemini find).
- **Markdown render for `/digests/ask` answers**: LLM answers currently render as `pre-wrap`; piping through `render_markdown` would let LLM use lists / bold / tables for better readability (Gemini find).
- **Observability plumbing**: log which scope was used per `/digests/ask` call to `state.db api_calls` or a sibling table, surface a "view scope" link from result (Gemini find).

---

## Out of scope

- **Write-path policy** (Bridge → vault) — covered by vault writing rules in `CLAUDE.md` and ADR-028. Future Bridge→vault write needs its own ADR.
- **Cross-vault scenarios** (other users' vaults, shared KBs) — not on roadmap.
- **Per-surface LLM cost dashboard** — operational, not architectural. Tracks via `state.db api_calls`.
- **Time-based "when do we revisit"** — captured via per-decision measurement conditions, not committed milestones.
- **Multi-modal substrate (images / audio / video)** — separate ADR when first surface emerges.
- **Provider diversity (non-Anthropic LLM)** — separate ADR when first surface demands it.
- **Vault graph-traversal reasoning** — speculative; revisit if a graph-native LLM materially changes the calculus.
