# Panel integration — /architecture 4-layer proposal

**Models**: Claude (v1 draft) · Codex GPT-5 (audit) · Gemini 2.5-pro (audit)
**Codex verdict**: Approve with modifications
**Gemini verdict**: **Reject** (proposes different organizing principle)

This is the disagreement-laden case. Two audits surfaced almost entirely non-overlapping concerns, with a direct contradiction on the core organizing principle.

## 3-way matrix

| # | Topic | Claude v1 | Codex | Gemini | Pattern | Resolution |
|---|---|---|---|---|---|---|
| 1 | **Inventory accuracy** (49 = 39 + 7 + 3) | claimed | rejects: 39/7/3 doesn't reconcile from text. Confused mix of "shipped vs planned vs in-dev vs speculative vs deprecated" | echoes Codex (Sec 3) | 2-of-3 reject | **Fix inventory before any Phase**. Rebuild with explicit categories + concrete route listings |
| 2 | **`/bridge/draft/{id}` path** | written this way in HTML | actually `/bridge/drafts/{draft_id}` per ADR-006:372-381 + bridge.py:1421-1456 | (didn't check code) | Codex unique catch | **Fix**: actual route is plural `/bridge/drafts/{id}` |
| 3 | **SEO routes** | `/bridge/seo/audit/{id}` + `/history` | actual routes are `/bridge/seo/audits/{job_id}`, `/result`, `/by-id/{audit_id}`, `/posts/{wp_post_id}/audits`, `/audits/{audit_id}/review` (bridge.py:507-622) | (didn't check code) | Codex unique catch | **Fix**: all SEO row paths in HTML wrong |
| 4 | **`/read` rename to `/robin/read/{doc_id}`** | proposed | rejects: current `/read` is query-string (`?file=&base=`), not path (robin.py:207-213). This is data model change, not rename | (didn't check code) | Codex unique catch | **Acknowledge**: this is a data-model migration, not URL alias |
| 5 | **Promotion ownership** (`/brook/promotion/*`) | proposed under Brook | rejects: ADR-024:82-85 says Source Promotion is Robin/shared. agents/robin/CONTEXT.md:21-41 lists Promotion Review as Robin vocabulary. **`/brook/promotion/*` teaches wrong ownership.** | (didn't catch the ownership conflict but flagged "agent-centric prefixes confuse users") | Codex unique catch + Gemini partial | **Fix**: rename to `/robin/promotion/*` OR pull out of agent prefix entirely (Gemini's task-based suggestion) |
| 6 | **Writing-assist ownership** (`/brook/writing/{id}`) | proposed under Brook | rejects: ADR-024:60 + ADR-027:69-83,255-257 — RCP producer is **Robin**, Brook may consume. `/brook/writing` hides Robin ownership | partial | Codex unique catch | **Fix**: `/robin/writing-assist/{source_id}` OR task-based `/write/reading/{source_id}` |
| 7 | **`/brook/context` rename for `/brook/bridge`** | proposed | rejects "context" as too generic. Suggests `/brook/handoff` (names user action) | rejects ALL agent prefixes. Suggests function-first like `/tools/prompt-builder` or `/tools/claude-handoff` | 2-of-3 reject `/brook/context`; **disagreement on alternative** | **User decision**: under-`/brook` rename → `/brook/handoff`. Pull out of agent prefix → `/tools/prompt-builder`. Pick depends on which mental model wins |
| 8 | **`/brook/chat` legacy redirect chain** | not mentioned | warns: `/brook/chat` already 301s to `/brook/bridge` (brook.py:36-43). Adding `/brook/bridge → /brook/context` creates chain | (didn't check) | Codex unique catch | **Fix**: collapse chain — `/brook/chat` should 301 directly to new target, not through `/brook/bridge` |
| 9 | **301 lifespan (30-60 days)** | proposed | rejects: for single operator there's near-zero cost to keep redirects indefinitely. Use 302/307 trial → 301 after logs prove → keep legacy where non-conflicting | (didn't address) | Codex unique catch | **Fix**: change policy to 302 trial → 301 permanent → no auto-removal |
| 10 | **`--nk-*` first-class permanence** | "permanent, not legacy" | "extract tokens first, THEN evaluate merge-vs-separate with actual screens" | (didn't address namespace question) | Codex partial | **Fix**: downgrade `--nk-*` from "first-class permanent" to "first-class today, decision deferred until tokens extracted in Phase 4" |
| 11 | **Promotion + writing-assist mount on VPS** | not mentioned | finds bug: `promotion_review.py:87-94` + `writing_assist.py:140-147` mount on production but services not wired when DISABLE_ROBIN=1 → **authed users hit 503** | (didn't check code) | Codex unique catch | **NEW WORK**: unmount these on VPS OR return deliberate "local-only" page. Affects Phase 1 readiness |
| 12 | **Status subdomain alternative** | not mentioned | strongly suggests `status.shosho.tw` / `progress.shosho.tw` for partner-facing, leave `nakama.shosho.tw` as authenticated shell | (didn't propose) | Codex unique alternative | **User decision**: stay single-host vs split partner-facing to subdomain |
| 13 | **Core mental model: agent-centric vs task-based** | agent-centric (4-layer matches agent owners) | accepts agent-centric, just fixes mistakes | **rejects agent-centric entirely**. Single operator doesn't think "I need Brook now", she thinks "I need to write." Proposes `/app/{research,writing,tools,operations}/*` task-based | **direct contradiction** | **MAJOR USER DECISION** — see fork below |
| 14 | **i18n / zh-TW handling** | English-only URLs | (didn't address) | strong push: reserve `/zh/` `/en/` path segment now or pay later. Chinese-language SEO patterns matter. Partner metadata needs zh-TW Open Graph | Gemini unique catch | **Accept (low-effort hedge)**: reserve i18n path now even if unused; OR explicit "English-only forever" decision documented |
| 15 | **Page titles, breadcrumbs, nav UX** | not mentioned | (didn't address) | "URL is not an island" — rename cascades to `<title>`, breadcrumb, sidebar active state. Plan must update all of these atomically | Gemini unique catch | **Fix**: each rename PR must touch title + breadcrumb + nav label, not just route |
| 16 | **Open Graph for shared `/progress`** | not mentioned | (didn't address) | partner-shared links unfurl via og:tags. Generic "Nakama Progress" is weak vs partner-specific. Add og:title / og:description / og:image plan | Gemini unique catch | **Accept**: add og:* metadata to `/progress` (cheap win) |
| 17 | **Brand identity hidden under agent names** | not mentioned | (didn't address) | "Shosho" is the brand, not "Brook"/"Robin". `/brook/projects` makes it feel like third-party tool, not "Shosho Studio". Use `/app/*` or `/studio/*` to surface brand | Gemini unique frame | **Tied to #13** — agent-centric vs task-based fork |
| 18 | **Path-as-permission lock-in** | not addressed | (didn't address) | future scenario: share one specific writing surface with a guest collaborator. `/brook/writing/{id}` is unnatural permission scope. `/writing/drafts/{id}` is intuitive | Gemini unique catch | **Accept as constraint**: any prefix decision should consider granular permission scoping |
| 19 | **`/api/*` as 5th layer not cross-cutting** | called "cross-cutting" | (didn't address) | "Cross-cutting" understates it. API is own layer with own routing/auth/versioning concerns. Should be named Layer 5 explicitly to prevent dumping ground | Gemini unique frame | **Accept**: name API as Layer 5 in v2 |
| 20 | **Phase 1 (marketing) before Phase 2 (renames)** | proposed | rejects: add Phase 0 (route aliases + redirect tests + template link updates + JS fetch + CSP + login next=) BEFORE marketing | "Phase 1 and Phase 2 create maximum inconsistency" — push for atomic single phase | 2-of-3 reject ordering | **Fix**: atomic phase merging current Phase 1 + 2, OR add explicit Phase 0 for prereqs |
| 21 | **`/architecture` itself as a public URL** | shipped public, noindex | (didn't address) | (didn't address — but Codex's status-subdomain suggestion implicitly applies) | gap | **Consider**: if status-subdomain decided yes, move `/architecture` there too. If no, mark as internal-only via auth |

## Universal agreements (3-way, high-confidence)
None — the panel never reached unanimous agreement on a single change. This itself is a signal: the proposal is contentious enough to warrant user adjudication, not auto-merge.

## 2-of-3 agreements (adopt with note)
- **#1**: Inventory must be fixed (Codex + Gemini both reject 39/7/3 mix)
- **#7 partial**: `/brook/context` is wrong name (both reject, disagree on alternative — #13 fork governs)
- **#20**: Phase ordering needs revision (both reject Phase 1 first)

## Codex-unique catches (high-credibility, code-grounded)
- **#2, #3, #4, #5, #6**: factual route errors (must fix regardless)
- **#8**: `/brook/chat` redirect chain bug
- **#9**: 301 lifespan policy
- **#11**: VPS 503 bug for unwired Robin routes (NEW WORK item)
- **#12**: status subdomain alternative

## Gemini-unique catches (high-credibility, UX-grounded)
- **#14**: i18n reservation
- **#15**: Title/breadcrumb cascade
- **#16**: Open Graph
- **#18**: Permission scoping
- **#19**: API as Layer 5

## THE FORK (item #13): User must decide

### Fork A — Stay agent-centric, fix execution (Codex's path)
Keep `/brook/*` `/robin/*` `/bridge/*`, but:
- Fix inventory errors (#1-3)
- Move `/brook/promotion` → `/robin/promotion` (#5)
- Move `/brook/writing` → `/robin/writing-assist` (#6)
- Rename `/brook/bridge` → `/brook/handoff` not `/brook/context` (#7)
- Fix redirect chains + policy (#8-9)
- Fix VPS unwired-route bug (#11)

**Pros**: Closest to current structure; less migration churn; clear agent ownership in URLs; matches existing ADRs (-006, -021, -024, -027 ownership rules)
**Cons**: Permanent cognitive tax of "remember which agent owns which surface"; brand is "Nakama agents" not "Shosho"; harder to share granular permission

### Fork B — Switch to task-based, abandon agent prefixes (Gemini's path)
Throw out 4-layer agent split. Build new `/app/*` prefix:
- `/app/research/sources` (read EPUBs, ingest)
- `/app/research/concepts` (concept validation)
- `/app/writing/drafts` (synthesize projects)
- `/app/tools/prompt-builder` (Claude.ai handoff)
- `/app/operations/dashboard` (Bridge hub)
- `/app/operations/seo-audits`
- `/app/operations/cost`

**Pros**: User mental model is task-first not agent-first; brand "Shosho Studio" emerges; matches Diátaxis / CMS / pipeline UI conventions; granular permission scopes natural; future i18n trivial
**Cons**: Bigger migration scope (every URL renames, not just 10); ADRs need amendment; need to relearn navigation; "where did Brook's surface go" question for every contributor

### Fork C — Hybrid: task-based public + agent-centric private
- Public: `/`, `/progress`, `/architecture`, `/healthz` (Shosho-branded)
- Authenticated: keep `/bridge/*` `/brook/*` `/robin/*` but rename surface labels (titles/breadcrumbs/nav) to be task-first even though URL stays agent-centric

**Pros**: URL changes minimal; UX gets task-first frame Gemini wants; ADR ownership preserved
**Cons**: URL ≠ UI label is a recipe for confusion; future devs see `/brook/*` and don't know what task it covers

## My recommendation

**Fork A + selected Gemini patches (#14, #15, #16, #18, #19) + fix Codex's NEW WORK (#11)**.

Why:
- Fork B is 6 months of migration work for a single operator who already has muscle memory on current structure. Cost/benefit doesn't pencil out.
- ADR ownership conflicts (#5, #6) are LOAD-BEARING — Brook owning Robin's promotion review breaks the agent boundary that Stage 2-3 vs Stage 4 rests on. Fix Brook/Robin attribution per Codex.
- Gemini's UX patches are cheap and high-value: title cascade (#15), Open Graph (#16), API as Layer 5 framing (#19). Worth adopting independent of fork choice.
- i18n reservation (#14) is hedge — cost ~30 min, future regret ~weeks. Cheap hedge.
- The unwired-route 503 bug (#11) is a current production hazard. Promote to immediate fix regardless of Fork choice.
- `/architecture` should be marked auth-only (or moved to subdomain per #12) so this planning doc isn't leakable to web crawlers indefinitely.

**What I would NOT take from Gemini**: Fork B's full task-based rewrite. The cost is too high for a single operator with existing muscle memory and ADR ownership encoded into routes.

**What I would NOT take from Codex**: status subdomain split (#12). Single-host is fine for the foreseeable; subdomain adds DNS/TLS/Cloudflare config overhead with no current pain.

## Surface to user

Tell修修:
1. Panel found 21 distinct items. Two thumbs-down from Gemini, one approve-with-mods from Codex.
2. The big fork is #13 — agent-centric vs task-based. Recommend Fork A.
3. Codex caught 6+ factual errors that MUST be fixed regardless of fork choice (route paths, ADR ownership, redirect chains, VPS 503 bug).
4. Gemini caught 5+ UX-layer wins worth adopting (title cascade, og:tags, API as L5, i18n hedge, permission scope thinking).
5. v2 should rewrite the /architecture page with corrections + chosen fork direction.
