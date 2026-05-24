# ADR-030: Vault-as-Substrate Read Strategy

**Date:** 2026-05-24
**Status:** Proposed (pending panel triangulation)
**Deciders:** shosho-chang, Claude Opus 4.7
**Related:** ADR-017 (Reader annotation store), ADR-021 (Brook synthesize HITL), ADR-022 (KB hybrid retrieval), ADR-024 (Promotion / Sources of Truth), ADR-028 (Vault layout consolidation), ADR-029 (Bridge IA restructure), CONTEXT-MAP.md, `thousand_sunny/CONTEXT.md`
**Tier A implementation reference:** PR #689 (`shared/markdown.py` + wikilink resolver) · PR #690 (`/bridge/digests` + detail) · PR #692 (`/bridge/digests/ask` LLM-over-vault)

---

## Context

Nakama runs two parallel data substrates today, and the boundary between them was never written down:

- **Vault** (`KB/`, `Projects/`, `Daily/`, `Digests/` under the Obsidian root) — markdown artefacts that humans read directly. Synced cross-device via Syncthing (VPS + Windows + Mac). Mutation: low frequency (agent cron writes a digest, 修修 edits a project note).
- **`state.db`** (SQLite) — operational state: `api_calls`, `drafts`, `user_memories`, `audit_results`, `approval_queue`. Machine reads/writes intensively, structured shape, high mutation rate.

Until 2026-05, every new feature triggered a 30-minute "where should this live?" debate. The drift accelerated as the Bridge UI added surfaces that consume vault content:

- **Reader** (ADR-017) reads `KB/Annotations/*.md` and book EPUBs.
- **Brook synthesize review** (ADR-021) reads `KB/Wiki/Sources/*.md` and writes its own JSON sidecar store.
- **Promotion preflight / source-map builder** (ADR-024) reads variant bytes via `VaultBlobLoader`.
- **Digest viewer** (Tier A PR #690, this ADR's motivating implementation) reads `KB/Wiki/Digests/{PubMed,AI}/*.md`.

Each of these arrived as a bespoke read path. Three implicit choices kept being re-made:

1. **Do we mirror vault into a DB to query it?** (FTS, embedding index, structured table)
2. **Do we expose `[[wikilink]]` semantics in the web UI?** (resolver, or strip them, or leave as text?)
3. **What's the upper bound of "feed the LLM the relevant context"?** (one file, ten files, the whole vault?)

The 2026-05-24 grilling session that produced this ADR forced the meta-question: **is the vault the source of truth for knowledge, or is it a staging area that we'll eventually replace with a proper store?**

修修's framing during grilling:

> 「LLM 一定會越來越強、越來越便宜，而 Obsidian 又是一個很適合 LLM 去操作的工具。」

The implication: betting against future LLM capability is the wrong bet. We should design the read layer so that "more LLM" is a free upgrade, not a re-architecture.

### Codebase inventory (verified 2026-05-24)

**Vault read pathways in production** (`shared/`):

| Module | Vault path consumed | Read mechanism | Caller surfaces |
|---|---|---|---|
| `shared/blob_loader.py:52` `VaultBlobLoader` | any vault-relative `.md` / `.epub` | sandboxed FS read | promotion preflight, source-map builder, reader |
| `shared/annotation_store.py` | `KB/Annotations/*.md` | parse frontmatter + body | `/books/{id}` reader |
| `shared/brook_synthesize_store.py` | `Projects/<slug>.synthesize.json` (JSON sidecar, not strictly vault md) | file IO | `/brook/projects/{slug}` |
| `shared/digest_indexer.py` (PR #690) | `KB/Wiki/Digests/{PubMed,AI}/*.md` | parse frontmatter + body | `/bridge/digests*` |
| `shared/kb_hybrid_search.py` (ADR-022) | `KB/Wiki/**/*.md` indexed into `state.db` `kb_chunks` + `kb_chunks_vec` | **mirror**: indexed copy in DB | Robin keyword research, Brook synthesize |

**Outlier: ADR-022 KB hybrid retrieval *does* mirror the vault** — `kb_chunks` + `kb_chunks_vec` are derived FTS+embedding tables. This is intentionally scoped to **deep retrieval for Brook synthesize** (200+ KB Wiki pages, semantic similarity required). It is not the default; it is the carve-out.

**Concurrent writers to vault**:

- **Agent-side**: Robin (PubMed digest), Franky (AI digest), Reader (annotation pages), Brook indirectly via Usopp (web inbox).
- **Human-side**: 修修 in Obsidian desktop on Windows / Mac.
- **Forbidden writers**: Bridge UI (Issue #231 — Bridge POST routes mutate `state.db`, never vault).

### LLM cost trajectory the design bets on

- Claude Sonnet 4.6 today: **$3/M input, $15/M output**.
- 200k char (~70k token) digest concat + 2k output ≈ **$0.18 per `/digests/ask` call** (verified during PR #692).
- Trajectory: per-token cost ~50% lower per 12 months across last 3 years. By 2027, the same call drops to ~$0.05–0.09. By 2028, ~$0.02–0.04.
- Throughput trajectory: 1M-context Sonnet is already shipped; 修修's full PubMed digest corpus (1 year × ~10KB/day = 3.6MB) fits comfortably.

The decision below treats "we can afford to feed the LLM more context next year" as a default assumption, not a leap.

---

## Decision

### D1: Vault is the **canonical knowledge substrate**

All durable knowledge artefacts that humans also read (`KB/`, `Projects/`, `Daily/`, `Digests/`) live as markdown in the Obsidian vault. There is **no parallel structured copy** of vault content unless explicitly scoped (see D5).

The vault is the **source of truth**, not a staging surface. Any agent / Bridge / external consumer reads from vault directly; nobody waits for a sync to a DB before consuming.

### D2: Read pattern A — **FS-direct** for deterministic access

When the caller knows the path it wants (`KB/Wiki/Digests/PubMed/2026-05-24.md`, `KB/Annotations/{slug}.md`), read the file directly via `shared.blob_loader.VaultBlobLoader` or a domain-specific reader (`shared.digest_indexer`, `shared.annotation_store`).

**No DB mirror, no index lookup, no cache by default** — file IO on the dev box and VPS is sub-ms. Add caching only when a measured hot path warrants it.

### D3: Read pattern B — **LLM-over-vault** for ad-hoc query

When the caller has a natural-language question that spans multiple files within a known scope (date range, type filter, path glob), concat the in-scope files and feed them to the LLM in one shot. Reference: `shared.digest_ask` (PR #692).

**Required guardrails** (every LLM-over-vault surface must apply all three):

- **Scope cap**: maximum concatenated input bounded by character count (PR #692: 200KB). Older / less relevant items drop out first; never silently truncate mid-file.
- **Cost cap**: bound the input dimension (days, glob breadth) before concat. PR #692: `MAX_DAYS=30`.
- **Empty-scope short-circuit**: if no files match the scope, return a synthesised "nothing in range" answer without calling the LLM.

### D4: **Knowledge layer vs State layer — parallel substrates, not hierarchy**

The two substrates are **not a pyramid** with vault at the bottom; they are **siblings** with disjoint mutation profiles:

| Layer | Substrate | Content shape | Mutation freq | Reader |
|---|---|---|---|---|
| **Knowledge** | vault (markdown) | long-form, human-readable, wikilink-cross-referenced | low (agent cron / human edits) | humans + LLM |
| **State** | `state.db` (SQLite) | structured records, FSM transitions, audit trails | high (per-request) | machines |

Routing rule for new features:

- High-frequency mutation OR machine-only consumer OR structured query needed → **state.db**
- Long-form OR human reads it directly in Obsidian → **vault**
- If unsure → state.db (vault is for things you'd open in Obsidian)

### D5: **Carve-out: ADR-022 hybrid retrieval stays**

KB hybrid retrieval (`kb_chunks` FTS + `kb_chunks_vec` embedding) is a **scoped mirror**, not a violation of D1. It exists for Brook synthesize's deep retrieval over 200+ KB Wiki pages where path-scoped concat doesn't fit in any reasonable context window. The mirror is **derived** (rebuildable from vault) and **read-only relative to vault** (rebuild ingests vault, never reverse).

**Generalisation rule** for future indexes: a derived mirror is allowed when (a) the corpus exceeds what a 1M-context LLM can chew in one shot, (b) the use case demands sub-second retrieval, AND (c) the rebuild path from vault is deterministic. Any one of those missing → use D2 or D3 instead.

### D6: **Wikilink semantics rendered, not stripped**

Vault markdown contains `[[target]]` and `[[target|display]]` syntax. Bridge surfaces render these via `shared.markdown_wikilinks.WikilinkResolver` (PR #689) with per-surface URL mapping:

- Resolvable targets (e.g., `pubmed-{id}` → external PubMed) render as `<a class="wikilink">`
- Unresolved targets render as `<span class="wikilink-broken">` (no dead `href`, gray dotted styling)

This preserves the cross-reference signal without requiring every link target to exist as a Bridge surface.

---

## Rationale

### Why vault is SoT (vs DB mirror)

**For**: Markdown is human-readable across all devices including offline. Obsidian's plugin ecosystem (Dataview / Templater / Tasks) gives 修修 query power on the desktop without engineering work. Syncthing tri-sync ensures no single-host dependency. The mirror would add: cron sync drift, two-source-of-truth bugs, ingestion failure modes.

**Against (considered, rejected)**: A DB mirror enables sub-100ms FTS / filter / sort queries. But (a) corpus size is small (digests: ~3.6MB/year; KB Wiki: 200+ pages, all under 5MB total), (b) Bridge surfaces are inherently single-user, no concurrent query load, (c) the carve-out (D5) already exists for the one workload that needed it.

### Why LLM-over-vault (vs RAG / embedding retrieval) for ad-hoc query

**For** `/digests/ask`-style queries: corpus is small enough to fit in context (PR #692 caps at 200KB ≈ 70k tokens of 1M available). LLM sees the full digest body, not chunked snippets, so it can quote and cite naturally. No embedding index to keep in sync. No "retrieval recall vs precision" tuning. Zero infrastructure beyond an HTTP call.

**Against (considered, rejected)**: At ~$0.18/query, ad-hoc questions are noticeably more expensive than a local FTS hit (~$0). But the question volume is low (estimated <10/day across all surfaces), the cost trajectory is downward (see Context), and the engineering cost of building/maintaining FTS+embedding for each new surface is 2–3 weeks per surface — far exceeding any plausible LLM bill in the next 2 years.

**The cross-over point**: When the corpus to query exceeds ~1MB and the query rate exceeds ~100/day per surface. ADR-022 already proved this exists for one workload. Other surfaces are nowhere close.

### Why the carve-out (D5) is principled, not ad-hoc

ADR-022's KB hybrid retrieval was built because Brook synthesize needs sub-second retrieval across hundreds of KB Wiki pages, called interactively while 修修 is reviewing evidence. Two of the three conditions (corpus size, latency requirement) are bound to that specific surface; the third (deterministic rebuild) is enforced by the indexer. The carve-out is therefore self-justifying and bounded — it does not start a slippery slope.

### Why the four limitations were called out explicitly

`thousand_sunny/CONTEXT.md` already documents four limitations of LLM-over-vault. They are restated here as **design constraints, not future fixes**:

1. **Obsidian CLI is desktop-only** — `obsidian eval` (Dataview / Tasks / Templater runtime) requires Obsidian app running. VPS-side Bridge cannot use it. Future Tier B (LifeOS Dashboard mirror) must either Python-rewrite the aggregations OR adopt a desktop-resident agent topology.
2. **Vault is not the only substrate** — D4 is normative. Don't bend it because vault is "easier."
3. **"Feed it everything" is bounded** — D3 guardrails are non-negotiable.
4. **Read path is clean, write path is hard** — Issue #231 is upheld. Bridge does not write vault. Any future Bridge → vault write must go through an agent path (e.g., Usopp draft → vault, or `obsidian create` CLI on a desktop-resident agent).

---

## Consequences

### Positive

- **One mental model for "where does this data live?"** — D4 routing rule resolves the recurring debate.
- **Bridge surfaces ship faster** — Tier A's three PRs (689/690/692) implemented full read + Q&A in ~1 working day because the FS-direct + LLM-over-vault patterns are template-able.
- **Future-proof against LLM cost crashes** — when Sonnet drops to $0.05/M, raising `MAX_CONTEXT_CHARS` and `MAX_DAYS` is a one-line change, not a re-architecture.
- **No sync infrastructure to maintain** — Syncthing handles cross-device; agents write to whichever filesystem they run on; consumers read the same files.

### Negative

- **`/digests/ask`-class queries cost real money** — at $0.18/call × 修修 usage, this could reach $20–50/month within a year. Cost cap design (D3 guardrails) is the mitigation; if it overshoots, fall back to a tighter `MAX_DAYS` or hand-roll a cheaper retrieval pre-filter for that one surface.
- **No sub-100ms ad-hoc query** — every LLM-over-vault hit has multi-second latency. Acceptable for asynchronous "ask the digest" flows; unacceptable for inline-as-you-type. The latter must use D2 (FS-direct, known path) or D5 (carve-out).
- **Vault path drift breaks consumers silently** — if Robin's digest writer changes the path from `KB/Wiki/Digests/PubMed/` to elsewhere, every Bridge reader 404s. Mitigation: ADR-028 vault layout consolidation already names paths as a versioned contract; this ADR reinforces it.
- **D5 invites future "carve-out creep"** — the generalisation rule is explicit, but reviewers must defend the line. Each new mirror proposal should cite the three conditions and provide measurements.

### Neutral / observed

- **修修 stays on Obsidian for direct edits** — desktop UX > web for long-form authoring. Bridge is the read + ops surface, not the editor.
- **Agents continue writing vault** — they own write paths to their respective subdirectories per ADR-028 + Vault Writing Rules.

---

## Alternatives considered

### A1: Mirror vault to Postgres / SQLite for FTS

Build a watcher that ingests every vault `.md` change into a search index. Bridge surfaces query the index.

**Why rejected**: 2–3 weeks engineering per surface, ongoing sync-drift bugs, two sources of truth, no improvement for the LLM-over-vault use case (still need to feed the LLM the body, the index just speeds up "which file"). The carve-out (D5) covers the only workload where this would actually help.

### A2: Build a single retrieval API and route all reads through it

Force every consumer through `shared.vault_search(query) -> list[chunk]`. Surfaces become thin shells.

**Why rejected**: premature abstraction. The current consumers have heterogeneous needs (Reader wants whole annotation files; digest viewer wants frontmatter + body; Brook synthesize wants chunked retrieval). Hiding them behind one API would force the lowest common denominator. The actual shared piece (the `WikilinkResolver`, the `MarkdownIt + bleach` pipeline) is already extracted (PR #689).

### A3: Move state.db content into vault as JSON sidecars

"One substrate" appeal: everything in vault. ADR-021 already has one such sidecar (`Projects/<slug>.synthesize.json`).

**Why rejected**: high-mutation state (api_calls, approval_queue FSM transitions) in markdown / JSON files would (a) flood Syncthing with diffs, (b) lose transactional semantics, (c) make atomic multi-row updates impossible. D4's parallel-substrates frame is load-bearing.

### A4: Adopt RAG with embeddings as the default ad-hoc query mechanism

Use embeddings for every cross-day question instead of full concat.

**Why rejected**: at current corpus sizes, full concat fits in context and yields better answers (no retrieval recall loss, native citation). Embeddings become attractive when the corpus crosses the cross-over point named above. We will re-evaluate when (a) PubMed digest archive exceeds 12 months / 5MB, OR (b) we add a Reader-annotation Q&A surface where corpus is annotations × years.

---

## Open questions resolved

- **Is Tier B (LifeOS Dashboard mirror) blocked by this ADR?** → No. Tier B explicitly may require a desktop-resident agent (limitation #1). When it ships, it can either re-aggregate vault in Python OR call Obsidian CLI from a desktop agent. The decision will live in its own ADR if it crosses substrates.
- **Does this affect ADR-022?** → No. ADR-022 is explicitly the carve-out (D5). This ADR documents the rule that carved it out.
- **Does this affect Reader (ADR-017)?** → No. Reader is FS-direct (D2) — it knows the annotation path it wants.
- **Does this affect Brook synthesize review (ADR-021)?** → Partially. The review page itself is FS-direct (sidecar JSON). The evidence_pool inside it comes from ADR-022 KB hybrid retrieval (D5 carve-out). Both stay.

---

## Migration plan

This ADR documents an existing pattern made explicit; no migration of existing code is required.

**Forward enforcement** (apply to every new vault-consuming feature):

1. PR description must name which read pattern the feature uses (D2 / D3 / D5).
2. If D3, the PR must show all three guardrails (scope cap / cost cap / empty-scope short-circuit).
3. If D5 is proposed, the PR must cite the three conditions with measurements (corpus size, latency requirement, rebuild path).
4. CONTEXT-MAP "vault" glossary entry (or surface-specific `CONTEXT.md`) must be updated when read patterns change.

---

## Out of scope

- **Write-path policy** (Bridge → vault) is governed by Issue #231 and the vault writing rules in `CLAUDE.md`. This ADR documents read; write semantics need their own decision when they become an active question.
- **Cross-vault scenarios** (other users' vaults, shared knowledge bases) — not on the roadmap.
- **Cost / observability for LLM-over-vault calls** — covered by ADR-008 ranking telemetry pattern + `state.db api_calls` table. Adding per-surface dashboards is operational, not architectural.
- **Specific evolution triggers** ("when do we revisit?") — captured in A4's cross-over conditions, not committed as time-based milestones.
