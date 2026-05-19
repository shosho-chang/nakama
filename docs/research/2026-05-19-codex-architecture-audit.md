# Codex Audit of Nakama URL Architecture Proposal v1

> Multi-agent panel step 2/5. Dispatched 2026-05-19 via `codex exec` (GPT-5) against the v1 proposal (PR #610).
> Final verdict: **Approve with modifications** (5 specific changes; cannot execute Phase 1 as written).

***

**1 — URL Structure Grounding**

The proposal’s count is not reproducible from its own text. The live planning page says “39 surfaces + 7 planned + 3 speculative” at `thousand_sunny/static/architecture/index.html:58`, but the rows only expose one clearly speculative URL: `/bridge/zoro/discovery` at `index.html:256-258`. To get seven “planned” items, Claude appears to mix categories: new `/`, five Stage 6 publisher URLs, and possibly deprecated `/bridge/health` or in-dev `/bridge/script_video/{run_id}`. That is not a clean taxonomy.

The root overload diagnosis is correct. With `DISABLE_ROBIN=1`, `/` is a 302 to `/brook/bridge` (`thousand_sunny/app.py:121-125`); without it, `/` is Robin’s upload hub (`thousand_sunny/routers/robin.py:199-204`). `/progress`, `/architecture`, and `/healthz` are mounted unconditionally (`app.py:83-90`), and the public route implementations are real (`progress.py:45`, `architecture.py:42`, `franky.py:64-70`).

The proposal also lists several shipped URLs that do not match code. `/bridge/draft/{id}` should be `/bridge/drafts/{draft_id}` (`bridge.py:1421-1456`) and ADR-006 also freezes the plural form (`docs/decisions/ADR-006-hitl-approval-queue.md:372-381`). `/bridge/seo/audit/{id}` and `/bridge/seo/history` are not current routes. Actual SEO routes are `/bridge/seo/audits/{job_id}`, `/result`, `/by-id/{audit_id}`, `/posts/{wp_post_id}/audits`, and `/audits/{audit_id}/review` (`bridge.py:507-622`, `bridge.py:759`). Those proposal rows would 404 if treated literally.

The Robin rename is under-specified. Current `/read` is query-string based: `file` plus `base`, not a path identifier (`robin.py:207-213`). So `/robin/read/{doc_id}` is a new data model, not a mechanical rename. The proposal also omits Robin operational URLs used by the UI: `/files/{path}`, `/events/{session_id}`, `/save-annotations`, `/sync-annotations/{slug}`, `/mark-read`, `/discard-info`, `/discard`, `/translate`, and `/pubmed-to-reader` (`robin.py:268-343`, `robin.py:543-618`, `robin.py:806`). Book APIs under `/api/books/*` are also part of the reader surface and are hardcoded in JS (`static/book_reader.js`) while CSP is scoped to `/books` and `/api/books` (`middleware/csp.py:31`). Moving `/books/*` without moving or consciously preserving those API/CSP paths will create partial breakage.

Redirects need more care. Existing `/brook/chat` already 301s to `/brook/bridge` (`brook.py:36-43`). Adding `/brook/bridge -> /brook/context` creates a redirect chain. That is acceptable temporarily, but removing `/brook/bridge` after 60 days while leaving `/brook/chat` untouched breaks the older redirect.

**2 — Drift Detection**

ADR-006 directly contradicts the proposal’s singular draft URL. The accepted route structure is `/bridge/drafts/{id}`, plus content edit, approve, reject, claim, and metrics endpoints (`ADR-006:372-381`). The proposal should not publish `/bridge/draft/{id}` even as shorthand.

ADR-012 supports keeping `/bridge/seo` topic-rooted. It explicitly says the SEO control center should not hang under `/bridge/brook` or `/bridge/zoro` because it mixes Brook audit and Zoro keyword research (`ADR-012:37-38`). Claude is right to keep `/bridge/seo`, but the proposal’s simplified `/bridge/seo/audit/{id}` / `/history` URLs drift from actual implementation.

ADR-021 supports moving `/projects/{slug}` only if the ADR is amended. The ADR explicitly names `GET /projects/{slug}` as the Web UI route (`ADR-021:184-186`, `ADR-021:241-242`). A rename to `/brook/projects/{slug}` is semantically fine, but the proposal must update ADR-021 and tests, not just add redirects.

The biggest semantic drift is `/promotion-review/* -> /brook/promotion/*`. ADR-024 says Source Promotion domain logic is Robin/shared, while Thousand Sunny owns the presentation checkpoint (`ADR-024:82-85`). `agents/robin/CONTEXT.md` is even clearer: Source Promotion, Promotion Review, and Reading Context Package are Robin vocabulary (`agents/robin/CONTEXT.md:21-41`, `:73-83`). Putting Promotion Review under `/brook` makes Brook look like the owner of a Robin knowledge promotion workflow. Don’t do that.

Writing Assist is more ambiguous, but `/brook/writing/{source_id}` still hides the RCP producer. ADR-024 allows a Brook-owned or shared Writing Assist Surface, but the package itself is Robin’s Stage 3 -> 4 handoff (`ADR-024:60`). ADR-027 explicitly rejected moving RCP to Brook and says Robin remains producer while Brook may consume it (`ADR-027:69-83`, `:255-257`). If the URL is meant to teach ownership, `/brook/writing` teaches the wrong thing.

ADR-027 does support renaming `/brook/bridge`. It calls the surface a “context bridge” and describes the new behavior as preparing context for Claude.ai handoff (`ADR-027:143-151`). I would not use `/brook/context`; it is too generic. Use `/brook/handoff` or `/brook/context-bridge`. My pick is `/brook/handoff`: it names the user action and avoids reusing “bridge”.

**3 — Numerical / Scope Claims**

“39 shipped + 7 planned + 3 speculative” fails audit. From the proposal table, I can identify 5 planned Stage 6 publisher URLs (`/bridge/ig`, `/youtube`, `/newsletter`, `/community`, `/annotations`) and 1 speculative URL (`/bridge/zoro/discovery`) at `index.html:230-258`. Add the new marketing `/` and you get 6 planned plus 1 speculative. Add in-dev `/bridge/script_video/{run_id}` and deprecated `/bridge/health`, and you can force “7 planned”, but that pollutes the count.

The Bridge “17 surfaces” line is also off. The proposal text lists 18 if `/bridge/script_video/{run_id}` is included. Actual shipped Bridge UI routes are more than that because SEO is not one audit page and one history page; it has progress, result, by-id result, per-post history, and review pages (`bridge.py:460-622`, `bridge.py:759-1080`).

Phase 2’s “4 renames” reconciles only as four workflow groups, not four routes. `/promotion-review/*` includes GET list, GET source, POST decide, POST commit, and POST start (`promotion_review.py:139-328`). `/brook/bridge` also has the legacy `/brook/chat` redirect (`brook.py:36-43`). The migration plan should say “4 route groups” and enumerate all handlers.

Phase 3’s “6 renames” has the same problem. As route groups, yes: root, read, processing, done, review pair, books. As actual migration work, no: Robin has SSE, reader asset serving, annotation writes, discard, translate, PubMed handoff, and book APIs. Treating this as local-only does not make it “no impact”; local-only is where the author’s reading workflow actually lives.

**4 — Assumption Push-Back**

The 30-60 day 301 lifespan is asserted without evidence. For a single operator, there is almost no cost to keeping old redirects indefinitely unless they block a new semantic route. Also, 301s are sticky in browsers and caches. Use 302/307 during the trial period, switch to 301 only after logs prove the new route is stable, and keep lightweight legacy routes where they do not conflict.

The partner behavior story is also unproven. `/progress` is partner-facing; root marketing might help if partners strip paths, but that should be checked against Cloudflare or app access logs. Do not let a cosmetic public landing page jump ahead of route correctness.

The “naming collision” framing is overstated. `/brook/bridge` and `/bridge` are not a routing collision. They are visually similar and semantically overloaded. That is still worth fixing, but not because users will hit the wrong route accidentally; it is because internal navigation already labels Brook as `'/brook/bridge'` from the Bridge hub (`templates/bridge/index.html:597-600`), which reinforces the overloaded term.

I also push back on preserving `--nk-*` as first-class by assertion. `docs/design-system.md` says all three namespaces are first-class (`docs/design-system.md:14-30`), but current reality is 19 Bridge templates with inline `--nk-*` styles, not “20+” Bridge templates. That is technical debt, not proof of a durable design system. Extract `thousand_sunny/static/bridge/tokens.css` first, then decide whether Bridge is a mode of Shosho tokens or a separate namespace. For a single-operator internal ops UI, a permanent third brand needs evidence beyond “workshop floor” language.

**5 — Alternatives Not Considered**

Alternative 1: organize by workflow stage. Keep `/bridge/*` for ops, but move author work into `/read/*` for Stage 2, `/kb/*` or `/knowledge/*` for Stage 3, `/write/*` for Stage 4, `/produce/*` for Stage 5, `/publish/*` for Stage 6, and `/monitor/*` for Stage 7. This matches `CONTENT-PIPELINE.md:14-22` better than agent branding.

Alternative 2: organize by auth/exposure. Public routes live at `/progress`, `/healthz`, and maybe `/about`. Authenticated app routes live under `/app/*` or `/bridge/*`. Local-only Robin routes do not mount on production at all. This avoids publicly documenting internal architecture and makes auth behavior predictable.

Alternative 3: organize by agent owner. `/robin/*`, `/brook/*`, `/zoro/*`, `/franky/*`, `/usopp/*`, with `/bridge` as a dashboard shell. This is simpler, but ADR-012 shows the downside: cross-agent topics like SEO become misleading if forced under one agent.

I would seriously consider putting the partner-facing layer on a different host, such as `status.shosho.tw` or `progress.shosho.tw`, and leaving `nakama.shosho.tw` as the authenticated product shell. If the root becomes public marketing, it should be intentionally public and sparse, not a doorway into internal roadmaps.

Robin local-only surfaces should not remain half-present in production. Today promotion and writing routes are mounted on VPS but their services are not wired when `DISABLE_ROBIN=1`, so authenticated users can hit 503 (`app.py:51-67`, `promotion_review.py:87-94`, `writing_assist.py:140-147`). Either hide and unmount the whole family in production, or return a deliberate “local-only” page.

**6 — Final Verdict**

Approve with modifications. Do not execute Phase 1 as written.

Top changes:

1. Fix the inventory first. Replace the 39/7/3 claim with a route-group table and a concrete route table. Correct `/bridge/drafts/{id}` and all SEO paths.

2. Change the Brook renames. Use `/brook/projects/{slug}` and `/brook/handoff`. Do not move Source Promotion to `/brook/promotion`; use `/robin/promotion/*` or a neutral `/knowledge/promotion/*`. Do not use `/brook/writing`; use `/robin/writing-assist/{source_id}` or `/write/reading/{source_id}`.

3. Add a Phase 0 before marketing: route aliases, redirect tests, template link updates, JS fetch updates, CSP update for books, and login `next=` updates.

4. Change redirect policy: temporary 302/307 first, then 301 after verification; keep non-conflicting legacy redirects indefinitely.

5. Treat `--nk-*` extraction as debt cleanup, not a permanent brand decision. Extract tokens, then evaluate merge-vs-separate with actual screens.