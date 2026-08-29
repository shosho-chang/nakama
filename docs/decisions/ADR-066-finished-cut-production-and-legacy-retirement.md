# ADR-066: Finished Cut Production 與 Podcast Highlight 舊版退役

- **Status**: Accepted — owner authorized implementation 2026-08-28; production cutover pending
- **Date**: 2026-08-28
- **Owner**: Brook / Podcast Stage 5
- **Stage**: 5 Multi-channel Production
- **Supersedes after cutover**: ADR-065 Podcast Highlight production path and finished-review revision producer
- **Preserves**: ADR-051 Director creative ownership; ADR-064 Editorial Master truth root
- **Does not supersede**: ADR-051 standalone `storyboard.yaml` workflow

## Review record

- Architecture/deletion review (`l3_render_path`): **APPROVE** — deep-module boundary, Long/Short authority,
  ADR-064 anchors, writer closure and physical deletion gate checked.
- Migration/Resolve/archive review (`l3_visual_compare`): **APPROVE** — Candidate/Release lifecycle, coordinated
  commit/compensating rollback, dependency reachability, archive integrity and retention checked.
- Adversarial authority review (`l3_semantic_trace`): **APPROVE** — semantic laundering, command provenance,
  state/response forgery, worker asset visibility, retry scope and fixed hot deadline checked.

The reviews caused material changes to this decision. In particular, `AcceptedStage` became the sole semantic
authority; old semantic media were removed from worker selection; `StagedReleaseCandidate` was separated from the
post-commit Release; the three-cut cutover gained one global rollback journal; and physical deletion gained a fixed,
non-extendable hot deadline. Review approval is technical; ADR acceptance remains an owner decision.

## Context

The first Long Highlight v2 production run exposed an incomplete cutover rather than one isolated rendering bug.
The new Long route existed beside, not instead of, ADR-065's revision-scoped visual DAG. Production still had
multiple writers and readers:

- the new Long orchestrator accepted host-supplied Director/DP rows and could mark them approved without a
  current-run acceptance identity;
- `adopt-existing` allowed semantic Director/DP rows to become production state;
- an episode-local materialization script could directly rewrite `state.json` from pending to approved;
- the finished-review watcher still called `podcast_highlight_visual_orchestrator.run_visual_pipeline`;
- Bridge still queried the ADR-065 visual DAG for v2 views and retained a v1 manifest reader;
- the new Long materializer still imported `run_short_broll` and `run_short_titles` as hidden cross-format
  production dependencies;
- current Long 2 and Long 3 recipes still referenced live media under `highlights/visual-pipeline/`.

For `punch-L04`, 34 prior Director events were copied into a new response with a constant time shift. The new
request did not contain the old proposal, so this was not an automatic core fallback: it was external response
authoring that the core lacked an interface to reject. Later, route-local code promoted pending DP/visual state to
approved outside the orchestrator. The result passed local asset/decode checks but retained old title density,
semantics and style.

The migration inventory also shows that this is not solved by deleting a directory:

- the current review manifest is still `nakama.finished_cut_review_manifest.v1`;
- `value-L01` has no new route;
- 64 unique media objects (about 1.345 GiB) are active across the three current cuts;
- 11 of those active objects are still addressed through old `visual-pipeline` paths;
- the legacy visual, review-revision and old-review payload is about 35.6 GiB;
- watcher, Bridge, manifests, pending revisions and Resolve rollback metadata still contain live old-contract
  dependencies.

Keeping historical evidence is legitimate. Keeping it discoverable and executable by production is not. The
system therefore needs one clean-room replacement with an explicit deletion gate, not more `if legacy` branches.

## Decision

Create one deep **Finished Cut Production** module whose seam is:

```text
approved cut + current feedback
  -> Director / DP / targeted visual work
  -> Resolve materialization and checked preview
  -> Finished Cut Release
  -> finished-review current index
```

The package lives at `agents/brook/script_video/finished_cut_production/`. Only its public module interface may
advance production or inspect a reviewable release. Watcher, Long/Short CLIs and Bridge become inbound adapters;
they do not parse or mutate route-specific state.

The production shape is:

```text
Long approved cut  -> LongV2Policy  -+
Short approved cut -> ShortPolicy   +-> ProductionRun -> AcceptedStage chain
targeted feedback  -> current IDs   -+                         |
                                                                v
                                      typed MaterializationPlan -> Finished Cut Release
                                                                          |
                                                                          v
                                                         finished review manifest v3
```

Long and Short retain different creative policy. The shared module owns only ordering, acceptance,
materialization, rollback, release projection and current-index atomicity. `LongV2Policy` owns the eight-minute
minimum, section/chapter rules, Long visual density and landscape-stock rules. `ShortPolicy` owns the approved
Short winner, 9:16 composition, Short title density and Short visual policy. Neither policy can mint an acceptance,
write run state, create a materialization plan or publish a Release. A Short path is not a Long fallback, and a
Long path never imports Short policy.

### Public module responsibilities

The public interface exposes only:

- `advance(command_id: ApprovedCutId | TargetedRevisionId)` — load the authoritative command by ID,
  idempotently advance it and return `pending | needs_review | review_ready | failed`;
- `inspect_run(command_id)` — return the immutable current pre-release checkpoint for that exact command, including
  current stage/event authority, build readiness and typed policy diagnostics, but no worker packet, filesystem path
  or inspection media;
- `request_correction(command_id, stage, event_id, feedback)` — before materialization, derive the exact current
  stage/event base inside the aggregate and mint one same-run `event_retry` request;
- `request_revision(current_release_ref, event_id, feedback)` — verify an event against exact current and mint one
  scoped `TargetedRevisionId` in a new run after a Release already exists;
- `inspect_current(episode_id)` — return read-only Finished Cut views from the exact current Release index.

Callers cannot pass stage rows, base acceptance IDs, state mappings, filesystem paths or release payloads.
`inspect_run` resolves only the exact command's current run; `inspect_current` cannot open an arbitrary or historical
Release. Format policies, workers and inbound adapters cannot construct an acceptance, materialization plan or
Release.

`ApprovedCutId` resolves only through the upstream approved-cut store and names an ADR-064 Editorial Master plus
human-approved winner/tight-cut identities. `TargetedRevisionId` resolves only through the module-private command
store and names exact current Release/event IDs plus explicit feedback. Neither command schema permits stage rows,
recipes, assets, filesystem paths or route state. Route-local code, workers and format policies cannot write either
store or mint IDs; unknown-origin, legacy-derived or payload-bearing IDs are rejected before a `ProductionRun` is
created.

Everything else is implementation detail: stage ordering, stage-response acceptance, run state, asset lookup,
Resolve duplicate-swap/rollback, preview probing, release construction and current-manifest publication.

There is no production `legacy` adapter, fallback flag or dual writer.

### Production run and stage authority

Long and Short semantic work are owned internally by a `ProductionRun` aggregate. A module-private
`AcceptedStage` store is the only semantic authority. Worker responses are proposals, not approvals;
`acceptance_id` can be minted only by the aggregate after current-run checks pass. Constructors and writers for
`AcceptedStage`, `MaterializationPlan` and `FinishedCutRelease` are not public. Persisted `state.json` is a
rebuildable view of those records and cannot authorize materialization.

Each stage proposal uses a lightweight current-run envelope:

```json
{
  "schema": "nakama.finished-cut-stage-response.v1",
  "run_id": "core-created",
  "request_id": "core-created-one-shot",
  "episode_id": "...",
  "cut_id": "...",
  "format": "long|short",
  "stage": "director|dp|visual_review",
  "attempt": 1,
  "scope": {"kind": "full_stage|event_retry", "event_id": null},
  "parent_acceptance_id": null,
  "events": []
}
```

The module enforces these invariants:

1. A proposal must match the outstanding run, request, format, stage, attempt and retry scope and is accepted at
   most once. Static `responses/<stage>/all.json` is not an authority.
2. Every accepted Director event carries `{master_cue_ids | master_cue_range, text_hash}` from ADR-064. The worker
   returns current cue IDs and visual intent; the aggregate derives quote, time and section from the current
   Editorial Master/tight-cut map. This **Semantic Evidence Range** explains the full argument supporting the
   decision; it is not the display duration. It remains on the event projection and Release so ripple edits can
   rebase deterministically.
3. DP coverage binds the exact current Director `acceptance_id`. DP uses current asset-catalog references, never
   raw absolute, sibling-route or `highlights/visual-pipeline/` paths, and cannot change Director intent or semantic
   anchors. For every non-A-roll event it selects `placement_cue_ids` only from an event-scoped cue-candidate view.
   The aggregate verifies an ordered, unique, contiguous, same-section subset and exclusively mints the
   **Visual Placement** t0/t1 used by derived media, components and the timeline. Intentional A-roll has no placement.
   A chapter cannot use arbitrary DP timing: its Director proof must be exactly the first current cue of a canonical
   `transition_before` section, and core places the chapter at that section's t0 for three seconds, capped by the cut.
4. Visual review binds the exact current DP acceptance. Only this full current acceptance chain can produce a
   typed `MaterializationPlan`; changing `state.json` cannot.
5. A targeted retry packet is built internally from one current `AcceptedStage` event plus explicit feedback.
   Unaffected rows are copied by the aggregate and cannot be returned by the worker. Extra rows, stale bases or
   field overreach leave the retry in `needs_review`; no full-stage retry is triggered automatically.
6. The materializer accepts only a typed plan from the aggregate, never an arbitrary state mapping or `--state`
   file.

### Pre-release event inspection and correction

Every structurally accepted Director, DP and visual-review response is an operator checkpoint before the next
stage is dispatched. `inspect_run(command_id)` projects only immutable current authority: run/status, outstanding
stage and scope, current and superseded acceptance IDs, cue-derived event anchors, intent/display,
 semantic/implementation/lane, semantic-evidence timing, Visual Placement timing, final asset reference, visual
 status, build readiness and typed policy diagnostics.
It never returns worker packets, inspection derivatives, absolute paths, old route metadata or arbitrary historical
records. A visual row may be structurally accepted with a failed review status so that its exact event can be
inspected and corrected, but such a checkpoint can never authorize a materialization plan or Release.

Before a materialization plan exists, an operator may call
`request_correction(command_id, stage, event_id, feedback)`. The caller supplies no stage payload or base ID. The
aggregate resolves the exact current acceptance containing that event, writes its ID to the private
`StageRequest.base_acceptance_id`, retires that stage and all downstream stages from the current chain, and mints
one `event_retry`. AcceptedStage history remains append-only. When the response is accepted, the aggregate copies
unaffected rows byte-for-byte from that exact base; the worker may return only the requested event.

If a downstream stage has never run, it may subsequently make its first normal full-stage request. If it already
ran, correction cascades only for the same event through each affected downstream stage; no automatic full-stage
retry is permitted. A corrected DP event rebuilds and visually reviews only its derived component while retaining
unaffected component/final-asset authorities. A visual correction may retry visual review for that event or the
operator may explicitly return that event to DP. Any stale base, wrong command/run/stage/event, extra returned row,
field overreach, already-claimed invalidated request, existing materialization plan/Candidate or durable dispatch
conflict fails closed in `needs_review` without a second dispatch.

This same-run **Pre-release Event Correction** is distinct from Release **Targeted Revision**. The latter verifies
an event against exact current Release and starts a new command/run; it is not a fallback for skipping pre-release
checkpoints. The operator sequence for a fresh cut is therefore `advance -> inspect_run -> [request_correction ->
advance -> inspect_run]*` at Director, DP and visual stages, followed by materialization only after the all-approved
current chain passes format policy.

The production semantic adapter accepts only a core-created current request packet. A Director sees only the
current Editorial Master/tight SRT, canonical chapter map where applicable and current feedback; it never receives
prior event/title/recipe metadata. DP sees the current Director acceptance plus a filtered Worker Selection Catalog
containing only `reusable_neutral` acquisition media such as Stock, photographs and non-editorial source clips, plus
an event-scoped exact cue-to-text/time/section candidate view from which it must choose Visual Placement cue IDs. It
never sees prior title/chapter/concept-card renders, composites or their event metadata. The worker workspace does
not mount episode legacy trees or the Forensic Archive.
Generic directory/file response runners are test/development fixtures only and cannot be installed as production
adapters.

Production `adopt-existing`, `adopt-winner --director/--dp` and semantic `import-draft` are removed. One-time
migration may carry only a human-approved winner/tight cut and media objects; it cannot import Director, DP,
visual-review rows, statuses or acceptances. The protection boundary covers every supported production and
route-local operation, not a malicious operator with unrestricted OS-level write access.

### Staged candidate and Finished Cut Release

`StagedReleaseCandidate` is a private, non-reviewable pre-commit object. It binds a `preview_ready` Resolve
transaction, probed preview/subtitle, current AcceptedStage chain and proposed event/recipe projection. It cannot be
indexed by current, opened by Bridge or treated as a Release.

Only after its Resolve transaction is committed may Finished Cut Production seal a Candidate into an immutable
`nakama.finished_cut_release.v1`. Sealing binds the actual commit/backup receipt and creates the final Release
identity. A manifest v3 can reference only sealed Releases.

`nakama.finished_cut_release.v1` is the only reviewable per-cut production snapshot. It binds:

- episode, cut and format;
- Editorial Master and approved-winner identities;
- exact tighten, Director, DP and visual-review acceptance IDs from the current run;
- every event's ADR-064 Master cue anchor and text hash;
- final event projection and recipe identities;
- preview and subtitle path, bytes, digest, duration and media probe;
- active asset-set, authoritative episode-source and any declared video-correction-source identities;
- committed Resolve transaction, rollback availability/expiry and rollback reference.

`nakama.finished_cut_review_manifest.v3` is only a deterministic index of current Releases. The Finished Cut
Production module is its sole writer and publishes pointer-last. Bridge reads the exact current index. If current is
missing or invalid, Bridge reports that state; it never selects a historical manifest by filename ordering.

Historical v1/v2 manifests are forensic inputs only and cannot authorize review approval, revision pickup or a
new release.

### Active dependencies and Forensic Archive

Current Releases distinguish four dependency classes:

1. **Active Asset Store** — selected Stock, generated cards, insets and other derived visual media;
2. **Authoritative Episode Source** — ADR-064 Editorial Master media/SRT and its contract; it is the only normal
   Podcast derivative base and is retained at its authoritative location, bound by path, digest and retention;
3. **Video Correction Source** — an optional, explicitly declared video-only camera asset for a bounded correction
   overlay; its audio is disabled and it cannot rebuild A-roll, dialogue or the cut base;
4. **Release Artifact** — final preview, review subtitle, event projection and recipes bound by the Release.

Derived visual media resolve through an episode-level content-addressed **Active Asset Store**:

```text
<episode>/highlights/assets-v2/sha256/<prefix>/<sha256>.<ext>
```

Current accepted stages and recipes contain catalog references resolved by the store, not paths into old route
trees. Authoritative Episode Sources are the only explicit store exception; they remain independently reachable
and hash-bound by the Release/rebuild contract. A declared Video Correction Source is the sole narrow additional
exception; raw camera/audio and `normalized.wav` are never fallback sources.

The Active Asset Store is a rebuild resolver, not the worker-selection surface. Semantic renders from an existing
Release may remain there for exact rebuild/rollback but are excluded from the Worker Selection Catalog. Current
title, chapter and concept-card media are rendered from the current AcceptedStage/recipe. Byte reuse is allowed only
when the core resolves an exact current recipe identity; a worker cannot select it.

Original acquisition/render evidence is retained in a **Forensic Archive** outside the episode/runtime root (and
outside the `G:\Footages` episode discovery root). The archive is not symlinked or junctioned into active paths and
is never scanned by Bridge or watcher. Its inspector may restore data only into an isolated staging root; it cannot
call Finished Cut Production, watcher, Bridge or Resolve, write an episode/current pointer, or execute archived
code. A separately scoped, one-shot cutover rollback tool may restore the exact pre-cutover deployment/data only
during the hot rollback window and is physically deleted when that window closes.

The active store receives a compact asset receipt containing media digest, source/license facts and the archive
object reference. Old receipts remain byte-for-byte in the archive; their old path literals are not copied into
active state.

### Validation profile

Normal production keeps only inexpensive structural gates:

- current run/request/parent acceptance identity;
- ADR-064 Master cue/text anchors and, for Long, canonical chapter anchors;
- format policy and event coverage;
- active asset-catalog resolution;
- media readability/playability;
- targeted-retry scope;
- final preview/subtitle probe and committed transaction.

It does not restore ADR-065's full receipt DAG, repeated runtime-tree hashes, same-session semantic audit, semantic
similarity checks or automatic full-stage retries. One-time migration digests only current reachable dependencies
needed to establish the new trust root and compare three staged outputs. The remaining legacy tree is archived as
opaque history; normal production never repeats migration verification.

## One-time cutover plan

### Phase 0 — Build the replacement behind tests

1. Add the Finished Cut Production package and its fake semantic/timeline adapters.
2. Implement the private `AcceptedStage` store, current-run acceptance and typed materialization plans for both
   `LongV2Policy` and `ShortPolicy`.
3. Add immutable `inspect_run` checkpoints and same-run exact-event correction before any Candidate may exist;
   keep superseded AcceptedStages as append-only history and prove no full-stage redispatch.
4. Consolidate duplicate Resolve transaction implementations behind the module.
5. Extract format-neutral pre-rendered B-roll/title application from `run_short_*`. Any retained production CLI is
   a zero-logic wrapper around `advance(ID)`; it cannot load state, validate/apply recipes or mutate Resolve/current.
6. Add public-interface tests before attaching production callers.

No production write path changes in this phase.

### Phase 1 — Dark-install every successor caller

1. Implement Long base/targeted and Short base/targeted adapters against `advance(ID)`, but do not activate them in
   production yet.
2. Prepare the finished-review watcher to call only `advance`, with no ADR-065 producer or local Resolve
   transaction.
3. Prepare Bridge to call only `inspect_current`, with no visual-DAG reads, virtual manifest construction or
   historical-manifest fallback.
4. Remove writer capability from `scripts/run_short_review.py`,
   `scripts/build_finished_review_manifest.py` and
   `scripts/build_long_highlight_playback_manifest.py`; delete obsolete writers or reduce a retained CLI to a
   zero-logic public-interface wrapper.
5. Prepare the module as the sole writer of `finished_review_manifest_current.json` and update production skills
   so no command invokes ADR-065 or a direct state/Resolve/current mutation.

This phase lands dormant code only. The live deployment, old writer and old current pointer remain unchanged until
Phase 5. Old and new writers are never active simultaneously.

### Phase 2 — Freeze and bounded inventory

1. Stop revision pickup and finished-review writes for a bounded maintenance window.
2. Require every active Resolve transaction to be committed or explicitly rolled back.
3. Record legacy CURRENT/PENDING and mark every old PENDING revision `abandoned: superseded_by_ADR_066`; it may not
   finalize. Carry unresolved editorial feedback into a fresh targeted-revision command, not an old stage row.
4. Compute a reachability closure from the exact current manifest, current recipes, source contract and Resolve
   transaction. Classify each reachable dependency as Active Asset Store, Authoritative Episode Source, optional
   Video Correction Source or Release Artifact. Record path, bytes, digest, retention and readability/probe only
   for that active set.
5. Record each legacy tree as one opaque archive unit with root, total bytes, file count, retention and a normalized
   unit SHA-256/Merkle root (or a single immutable-container digest). Do not build a per-object receipt/referrer DAG
   for inactive evidence.
6. Verify the three current previews/SRTs and the dynamically discovered active objects before any move. The
   observed 2026-08-28 snapshot is 64 unique derived assets (1.345 GiB), not a hard-coded future count.

The active inventory normalizes Windows path case, slash style and Unicode. A missing or ambiguous reachable
reference aborts the cutover; it is never guessed.

### Phase 3 — Establish fresh authority and self-contained cuts

1. Copy and digest-verify the reachable derived media into the Active Asset Store. Bind only ADR-064 Editorial
   Master media/SRT/contract as the Authoritative Episode Source. Register any required camera correction through
   the explicit video-only correction contract; never authorize raw audio or raw-camera A-roll fallback.
2. Start fresh ADR-066 `ProductionRun`s for Long 1, Long 2 and Long 3 from only their human-approved winner/tight
   cut, current Editorial Master, current feedback and filtered Worker Selection Catalog.
3. Run a fresh Director -> operator checkpoint/correction -> DP -> operator checkpoint/correction -> visual-review
   checkpoint/correction chain for each cut. Do not copy/import prior Director, DP or visual-review events,
   statuses, responses or acceptance metadata. Only `reusable_neutral` existing media may be selected through the
   new catalog; prior semantics cannot be laundered by changing their path.
4. Give all three cuts explicit route references and typed MaterializationPlans whose acceptance IDs were minted
   after this cutover began. A Candidate cannot exist until its Resolve transaction and preview are ready.
5. Sanitize unresolved editorial feedback into the new command contract. Historical exchange responses,
   projections, QA snapshots, state files and revision-job identities remain archive-only.

### Phase 4 — Prepare Resolve transactions and staged verification

1. For each cut, create a duplicate-swap transaction and apply its typed MaterializationPlan to a work timeline;
   never modify the canonical timeline in place.
2. Keep all three transactions at `preview_ready`; do not commit any timeline in this phase.
3. Snapshot timeline start/end, every track and item, source in/out, retime, transform/crop/composite, Fusion/title
   parameters, audio gain/effects and media-object digest.
4. Render staged previews and require stream/duration/decode checks, offline-frame detection, decoded audio PCM
   equality where the plan promises unchanged audio, and a full-frame SSIM comparison for path-only relinks.
   Fresh semantic output receives normal independent visual review instead of comparison to the old creative plan.
5. Add one representative chapter, title and B-roll visual spot check per cut. Do not manually inspect every event
   as a migration-only second audit.
6. After the transaction is `preview_ready` and its preview/subtitle probes pass, build the private
   `StagedReleaseCandidate` for that cut.

### Phase 5 — Coordinated production cutover

1. Require three `StagedReleaseCandidate`s, three `preview_ready` Resolve transactions, an unpublished index plan
   and a pinned pre-cutover deployment before entering maintenance.
2. Stop watcher and Bridge and create one global cutover journal. Commit the three Resolve transactions in a fixed
   order while retaining backups. Keep the old current pointer and deployment active-but-stopped until all three
   commits succeed; no user request can observe an intermediate state.
3. After all three commits succeed, seal each Candidate into a final immutable Release using its actual committed
   transaction/backup receipt, build and verify final manifest v3 from those Release identities, then publish its
   pointer last, activate the dark-installed deployment and restart services.
4. The new Resolve adapter must support and test `committed + retained backup -> compensating rollback`. If any
   commit, Release seal, final-index build, pointer swap, service start or UAT step fails, stop services, remove any
   unpublished final artifacts, restore the previous pointer/deployment and reverse committed timelines in reverse
   order.
5. Verify Bridge playback, Range requests, subtitles, event seek, review save and approval on all cuts.
6. On duplicate timelines and staged Candidates, run one no-op/relink-only targeted revision per cut; verify exactly
   one event scope and unchanged unaffected-event identities, then discard/rollback the smoke. It cannot modify
   user current.

Only a successful global journal completion exposes manifest v3. Before Phase 6, rollback restores the old
deployment and old pointer together; the new Bridge is never asked to read v1/v2.

### Phase 6 — Quarantine and disable legacy production

1. Define active data by current Release reachability, not directory name. Keep only current AcceptedStage stores,
   current transactions, Releases, source contracts and Release-reachable artifacts active. Move all unreachable
   legacy visual trees, old routes, exchange/asset-map/projection/QA snapshots, review revisions, historical
   manifests/feedback/identity and old transaction metadata into the external Forensic Archive.
2. The mover may archive only `class=archive && refcount_from_current_release=0`. Current committed transaction and
   rollback backup remain active while a current Release references them.
3. Copy each opaque unit to a temporary archive object, verify its bytes/file-count/unit root, atomically publish
   the archive receipt, and only then remove the source. A partial cross-volume copy never becomes an archive unit.
4. Assert active JSON contains zero old route/revision paths. Place an archive sentinel named
   `finished_review_manifest_99999999.json` and prove Bridge/watcher ignore it; removing exact current must return an
   error, never fallback.
5. Production callers, CLIs and skills contain no ADR-065 invocation and no direct state/Resolve/current mutation.
   The pre-cutover deployment and exact cutover rollback tool remain sealed and non-production for the hot window;
   old source modules are not importable from the active deployment.
6. Mark ADR-065 Superseded by ADR-066 and preserve it only as historical rationale.

Operational retirement is complete here: no production process can execute or discover the old pipeline. Physical
source deletion follows the bounded hot rollback gate rather than contradicting it.

### Phase 7 — Hot gate, physical code deletion and cold retention

- Phase 6 may begin only after three-cut UAT, owner acceptance of current and clone-only targeted-revision smoke.
  The hot rollback deadline is fixed at `phase6_at + 14 days` and cannot be extended. Any unresolved blocker before
  the deadline triggers global rollback; it does not keep old executable code alive indefinitely.
- At the hot gate, physically delete ADR-065 modules/CLIs, the sealed pre-cutover deployment and cutover rollback
  executable; remove all production route branches and skill commands. Archived source remains inert bytes only.
- Delete a Resolve backup only when no current Release references it. First publish a superseding Release or an
  append-only rollback-expiry receipt so `inspect_current` no longer claims rollback is available.
- `heavy_delete_at = phase6_at + 90 days` (necessarily after the fixed hot deadline) and still requires owner
  sign-off. At that time,
  delete heavy archived video/render/contact-sheet blobs. Permanently retain the small original manifests,
  feedback, transaction/PENDING/CURRENT metadata, revision request/results, inventory, release/migration receipts,
  ADRs and deletion receipt.

Disk preflight is computed, not hard-coded:
`archive_copy_bytes + missing_active_store_bytes + max(1.25 * current_preview_bytes, staged_preview_budget) +
temp_retry_reserve + filesystem_margin`. The 2026-08-28 estimate of 35.584 GiB legacy data and 45 GiB free space is
only a snapshot/minimum floor; source and external archive volumes are checked independently. Estimated
implementation and cutover work is 3–5 working days, excluding retention windows.

## Terminal deletion gate

Retirement is not complete until all are true:

1. Production code and skills, excluding tests/docs and the data-only archive inspector, contain zero references
   to:
   `highlight_visual_pipeline`, `podcast_highlight_visual_orchestrator`,
   `podcast_highlight_visual_pipeline`, `visual_pipeline_lineage` and ADR-065 artifact contracts.
2. Production has no `adopt-existing`, semantic adoption/import, generic response-directory adapter or
   arbitrary-state materializer entrypoint.
3. Unknown-origin/legacy-derived commands and commands containing stage rows, asset/recipe payloads or paths are
   rejected before run creation; only the approved-cut store and `request_revision` can mint accepted command IDs.
4. Every current Release binds post-cutover core-minted tighten/Director/DP/visual `AcceptedStage` IDs and ADR-064
   event anchors. No semantic stage was migrated or imported.
5. Active episode JSON contains zero `visual-pipeline`, old review-revision or old review-media paths; every active
   file is reachable from exact current Releases or an Authoritative Episode Source contract.
6. Long 1/2/3 can be rebuilt from current Releases, private AcceptedStage chains, the Active Asset Store, ADR-064
   Editorial Master contracts and any explicitly declared video-only correction source.
7. A fresh worker packet cannot see an old semantic card/render even when an existing Release can still rebuild it;
   DP can select only `reusable_neutral` catalog media.
8. Long base, Long targeted revision, Short base and Short targeted revision tests all pass through
   `advance(ID)`, with distinct Long/Short policy tests and no ADR-065 fallback.
9. A forged `state.json`, stale proposal, wrong-parent response, extra retry event, worker-returned unaffected row,
   raw legacy path or direct CLI call cannot create an acceptance, plan, Resolve mutation or Release.
10. Watcher failure cannot change current; global cutover rollback restores deployment, pointer and committed
   timelines in reverse order.
11. Bridge uses only manifest v3 and cannot display a historical/archive sentinel when current is absent.
12. Removing the entire old visual-pipeline tree does not change Long, Short, watcher, Bridge, rebuild or targeted
    revision results.
13. A writer-capability scan proves only Finished Cut Production can write
    `finished_review_manifest_current.json`; `run_short_review.py`, both old manifest builders and all production
    CLIs/skills have no direct writer/mutation path.
14. The old production modules, CLIs, cutover rollback executable and pre-cutover deployment are physically absent
    after the hot gate.
15. Every opaque archive unit passes unit-root/file-count verification and a restore-to-isolated-staging drill;
    archive restore has no production mutation capability.

## Considered options

### Harden ADR-065 in place

Rejected. It preserves the exact receipt/freshness machinery whose cost and repeated failure modes motivated the
new Long flow, while watcher, Bridge and materializer remain separate writers/readers.

### Add a watcher adapter or `legacy=false` flag

Rejected. This hides one call but leaves the old DAG, dual manifest readers, direct state mutation and hidden
`run_short_*` dependencies executable. It fails the deletion test.

### Delete `visual-pipeline` immediately

Rejected. Current recipes still address old media; deletion would create offline Resolve assets and make targeted
rebuilds irreproducible without removing copied old semantics from the new response.

### Expand `LongHighlightOrchestrator` into a universal Long/Short orchestrator

Rejected. Short would need to understand Long duration/chapter vocabulary, and finished-review/Resolve concerns
would enlarge an already broad interface. Format adapters behind Finished Cut Production preserve locality.

## Consequences

- There is one production ordering and one current release writer.
- The module-private AcceptedStage chain, not `state.json` or a worker response, is the sole semantic authority.
- Pre-release correction is an inspectable, same-run exact-event authority transition; superseded AcceptedStages
  remain append-only and no correction authorizes a full-stage retry.
- Long and Short no longer share creative policy accidentally through filenames or hidden imports.
- Historical evidence remains available without remaining executable.
- Targeted retries remain cheap and do not trigger full LLM reruns.
- Bridge becomes a projection of Finished Cut Releases rather than another visual-pipeline reader.
- The one-time migration is operationally significant and needs free disk space, a maintenance window and Resolve
  relink verification.
- Old v1/v2 review pages cease being production surfaces after cutover; forensic restore is explicit.
- Physical blob deletion is delayed for rollback safety, but production retirement is immediate at Phase 6.

## Open follow-up — Release Amendment authority

This decision defines two ways to change a cut: `request_correction` before a `MaterializationPlan` exists, and
`request_revision` against an exact current Release. Neither fits a **mechanical, non-semantic** change to an
already sealed Release — retiring a component to intentional A-roll, or re-rendering an existing event's asset at a
new recipe identity. Both keep the base Release's entire `AcceptedStage` chain and replace only the
`MaterializationPlan`; routing them through `request_revision` would mint a new run and re-dispatch semantic
workers for a change that has no semantic content.

The L04 cutover needed exactly that twice, so it was done with episode-local operations under `.cache/`:

```text
release-8ca1a6eb  plan-1410680187...              20 components   run authority output
      -> amendment 1  suppress_components          5 supporting_title -> intentional A-roll
release-22a0424   plan-suppression-e8080c9b...    15 components
      -> amendment 2  replace_component_assets     5 fullscreen_transition assets -> v4
release-af65a1d7  plan-transition-v4-833e4ac1...  15 components   current
```

All three Releases share one `run_id`, `command_id` and all three acceptance IDs, which confirms the amendments
were mechanical. But `runs/authority.json` still describes only `plan-1410680187...`, and the operations that
derived the other two plans lived in an ignored directory — so the current Release was not re-derivable from
anything under version control.

**Interim measure (landed):** `agents/brook/script_video/finished_cut_production/amendments/` holds a typed
`nakama.finished_cut_amendment_journal.v1` record of the chain plus the two operations it pins by SHA-256. The
journal's transform parameters are derived by diffing the sealed Releases, not copied from the scripts, and its
loader fails closed on a broken chain, a chain that does not end at current, a missing plan replacement, an event
count change or a forged acceptance chain. This closes the provenance gap; it does not make the amendments
re-executable through the module.

**Still owed:** promote the transform vocabulary into a public `request_amendment(current_release_ref, operation)`
command whose `advance` reuses the base acceptance chain, mints the plan inside the aggregate and publishes through
the normal Candidate/commit/seal/pointer-last path, with `amendments` persisted beside `runs` and
`targeted_revisions` in the authority store. Acceptance for that work should be **plan equality** with the recorded
journal, not preview byte equality — a 1 GiB preview render is not a reproducibility contract. Once it lands,
`amendments/operations/` is deleted and the journal becomes an aggregate-written artifact.

## P9 implementation prompt

1. **Goal** — replace every ADR-065 production caller with Finished Cut Production, migrate all current cuts to
   self-contained Releases/assets, and physically delete the old executable pipeline after a proven atomic cutover.
2. **Scope** — add `agents/brook/script_video/finished_cut_production/`; update Long orchestrator/materializer,
   Short production policy, finished-review watcher, the three named manifest writers, Bridge review
   adapter/router, relevant skills/tests/docs; add bounded inventory and hot-window cutover rollback tools; finally
   delete those tools, ADR-065 runtime modules and obsolete writers.
3. **Inputs** — ADR-051 Director skill, ADR-064 Editorial Master, approved Long/Short winners, current review
   manifest/feedback, current Resolve transactions/timelines, current recipes/media, the 2026-08-28 retirement
   inventory and this ADR.
4. **Outputs** — Finished Cut Production module, private command/AcceptedStage stores, Long/Short policies,
   current-request semantic adapter, filtered Worker Selection Catalog, Active Asset Store,
   StagedReleaseCandidate/Finished Cut Release v1, finished-review manifest v3, fresh-authority L01/L02/L04
   Releases, external Forensic Archive, global cutover/deletion receipts and superseded ADR-065.
5. **Acceptance** — pass the terminal deletion gate above, focused public-interface/authority tests, one Long and
   one Short real Resolve smoke, three-cut Bridge UAT, compensating global rollback, clone-only targeted-revision
   smoke, active reachability/zero-reference scan and archive non-discovery test.
6. **Boundaries** — do not modify Stage 4 subtitle truth, overwrite Editorial Master or canonical Resolve timelines,
   import prior semantic stages, upload/publish to YouTube, reintroduce repeated full receipt/hash validation or
   semantic-similarity policing, infer missing asset references, run old/new writers concurrently, or expose the
   Forensic Archive to runtime discovery.
