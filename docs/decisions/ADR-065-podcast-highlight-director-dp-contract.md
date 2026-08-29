# ADR-065: Podcast Highlight Director → DP production contract

- **Status**: Proposed (implementation and independent QA pending)
- **Date**: 2026-08-25
- **Owner**: Brook / Podcast Stage 5
- **Stage**: 5 Multi-channel Production
- **Adapts**: ADR-051 Director skill for Podcast Highlight production
- **Amends**: ADR-064 visual-event materialization boundary
- **Does not supersede**: ADR-051 standalone `storyboard.yaml` workflow

## Context

ADR-051 correctly kept visual judgment in agent skills and deterministic validation/rendering in code. Podcast
Highlight production later grew a separate Resolve-native route, but reused two misleading executable names:

- `run_short_director.py` directs camera selection and rebuilds the derived Resolve Timeline;
- `run_short_broll.py` materializes an already-approved visual recipe onto that Timeline.

Neither executable invokes the `brook-director` or `brook-dp` skill. The current S9 documentation nevertheless
placed those scripts where a reader would expect Director and DP judgment to occur. In production this allowed a
technically valid `_broll.json` to reach Resolve with only file, provenance, minimum-count and non-overlap checks.
It did not prove that the chosen picture expressed the exact transcript sentence. The result could therefore pass
mechanical gates while pairing a high-pressure post-war education passage with a child playing a learning game.

The standalone Video Production Line and Podcast Highlight also have different truth roots. ADR-051's original
route uses `data/script_video/<episode>/storyboard.yaml`; Podcast derivatives must use the human-approved Editorial
Master and the exact preflight-selected cut-local tight SRT defined by ADR-064. Silently treating either shape as
the other would create a second source of truth.

## Decision

Podcast Highlight visual production uses an episode-local, cut-scoped receipt chain:

```text
<episode>/highlights/visual-pipeline/<cut-id>/
  PENDING.json
  CURRENT.json
  revisions/<revision-id>/
    DIRECTOR-WORK.json
    DIRECTOR-PLAN.json
    DP-FULFILLMENT.json
    SEMANTIC-AUDIT.json
  jobs/<revision-id>/
    workers/
    receipts/
```

`revision-id` is derived deterministically from current source identities and, for finished-review changes, the
exact immutable job `request.json`. Callers may not select a revision ID, infer the newest request by mtime, or
bind a generation to the append-only feedback file. `PENDING.json` selects the in-progress generation.
`CURRENT.json` changes atomically, pointer-last, only when a trusted semantic-audit acceptance has verified all
four artifacts. A failed second generation therefore preserves the previous reviewable CURRENT.

The contracts and responsibilities are:

| Artifact | Contract | Producer | Responsibility |
|---|---|---|---|
| `DIRECTOR-WORK.json` | `podcast-highlight-visual-work-packet-v1` | deterministic runtime | Bind exact episode/cut, revision request, Editorial Master, winner/materialization lineage and tight SRT |
| `DIRECTOR-PLAN.json` | `podcast-highlight-director-plan-v1` | trusted acceptance of an isolated `brook-director` proposal | Select exact transcript cues and state visual intent, category, description and negative constraints |
| `DP-FULFILLMENT.json` | `podcast-highlight-dp-fulfillment-v1` | trusted acceptance of a distinct `brook-dp` proposal | Fulfil every Director event with target lane, mode, search angles, candidates, selected asset/render recipe, reason and provenance |
| `SEMANTIC-AUDIT.json` | `podcast-highlight-visual-semantic-audit-v1` | trusted acceptance of the resumed original Director session, distinct from DP | Audit every selected materialization and whether the visible result matches the cited transcript meaning |

Agents write only isolated proposal files and candidate assets. Trusted code supplies
`{worker_id, execution_id, role, session_id}` from the actual dispatch, rejects self-reported identity fields,
fresh-validates proposal bytes and writes immutable canonical artifacts. Phase-local execution receipts bind
the prompt/input/proposal/output hashes to that observed identity. Director plan and audit share worker and
session IDs but use different execution IDs; every DP identity field is distinct.

The only normal state order is:

```text
awaiting_init
  → awaiting_director
  → awaiting_dp
  → awaiting_semantic_audit
  → ready_to_materialize
```

Invalid, missing, stale, cross-episode, partial-coverage or mismatched receipts fail closed. A downstream artifact
already existing is not permission to bypass an earlier receipt.

### Production ordering

For each winner, the orchestrator must:

1. finish tightening and identity placement against the exact Editorial Master derivative;
2. run `run_short_director.py` only as the **camera/Timeline director**;
3. initialize or resume the exact pending generation and fresh-verify `DIRECTOR-WORK.json`;
4. dispatch an isolated agent with the complete `brook-director` skill, then trusted-accept its proposal;
5. dispatch a different isolated agent/session with the complete `brook-dp` skill, require concrete/pre-rendered
   candidates, then trusted-accept its proposal;
6. resume the original Director session for the second-pass semantic audit and trusted-accept only complete
   all-match findings;
7. fresh-verify CURRENT, then deterministically emit exact B-roll and title recipes from its materializations;
8. only when state is `ready_to_materialize`, let `run_short_broll.py` and `run_short_titles.py` act as
   **materializers** for DP's B-roll and title implementations;
9. continue SFX, render packet and finished-cut review.

The stable Codex entrypoint is
`scripts/podcast_highlight_visual_orchestrator.py <episode> --cut-id <id> [--revision-request <request.json>]`.
It opens Director with `codex exec --json`, opens DP in another session and returns to Director with
`codex exec resume <director-session-id>`. It uses no `--add-dir`, danger/yolo or approval-bypass flags; each
worker sees only its job directory and deterministic input snapshot. Claude Code may perform the same workflow
with isolated subagents, but must preserve and resume the original Director handle and use the same deterministic
`init → accept-director → accept-dp → accept-audit → verify` API order.

Finished-review revisions use the same producer. The router seals feedback and upstream identities into the
immutable job request. The generic revision agent may create only non-visual tightening input. Before any Resolve
transaction, the watcher runs this visual orchestrator, deterministically emits both audited recipes, and
fresh-validates them. It then rebuilds the v2 finished-review manifest. A worker crash, bad proposal or semantic
mismatch records a failed job without changing CURRENT, recipes, preview Timeline or the previous review manifest.

Creative judgment remains agent-owned and can run in either Codex or Claude Code's local subscription runtime.
Deterministic code owns work-packet construction, schemas, hashes, path containment, identity/freshness checks,
coverage and materialization authorization. Skills may not invent contract fields or write around a failed state.

### Human interaction and Bridge

Bridge is a read-only view of freshly verified production truth. For every visual event it displays the exact
transcript quote and time, Director intent/category/description/on-screen text, DP target/mode/search
candidates/selection reason and
provenance, plus semantic-audit verdict/rationale/worker identity. Missing or stale receipts appear as pending or
failed states; the page must not present an unverified artifact as completed.

Bridge reads `visual_pipeline_status` and `verify_visual_pipeline` on each finished-page GET; it does not parse
canonical JSON or execution receipts itself. A pending generation may be reported beside the still-fresh
previous CURRENT, but no pending proposal is displayed as verified content. The finished-review manifest is
`nakama.finished_cut_review_manifest.v2`; every cut binds the Stock Video v2 receipt and the exact CURRENT pointer
plus work/Director/DP/audit identity DAG used to build its preview.
Existing v1 manifests remain explicit legacy read-only previews during migration. Bridge may accept revision
feedback against them, but must disable approval and must not rewrite or re-sign them as v2. Only a newly built v2
manifest can authorize the downstream finished-cut approval.

The receipt chain covers every content visual, including Stock, Hero/keyword text, quote/chapter cards and other
DP implementations. Structural badges, camera correction and the guest namecard may retain their separate
deterministic contracts; they do not masquerade as transcript-driven content visuals.

This contract adds no ordinary approval gate. Director, DP and semantic audit continue automatically when their
inputs are unambiguous. Human input is requested only for genuine ambiguity, unavailable authority, licensing or
subjective editorial choice. Finished-cut review remains the normal downstream human gate.

### Two routes remain explicit

- **Standalone script-driven video** continues to use ADR-051 `storyboard.yaml`, its existing Bridge text/visual
  review and render/emit commands.
- **Podcast Highlight** uses the revision-scoped receipt DAG above and Resolve materialization. It must not silently fall
  back to standalone storyboard files.

The skills can share visual grammar and taste rules, but their runtime contracts and lineage roots stay explicit.

## Affected Principles & Conflict Check

### ADR-051 — creative judgment stays in a skill

> 「讀校正後字幕 → 分鏡判斷 → 取得外部素材 → 產 storyboard.yaml」這一層創意工作，做成 **Claude agent skill**
> （`.claude/skills/brook-director/`），不做成 pipeline 程式；render / emit / cache / Bridge 審核等既有
> pipeline 程式維持不動，由 skill 呼叫。

**Still satisfied.** Podcast adds deterministic envelopes around the judgment; it does not move Director or DP
selection into a one-shot Python planner. The different output filename adapts the skill to ADR-064's cut-local
truth root and does not rewrite ADR-051 history or the standalone route.

### ADR-051 panel discipline — contracts belong to deterministic tools

> skill 是導演（orchestrator + 品味載體），但契約歸 deterministic 工具

**Still satisfied.** Code constructs identities and verifies every receipt; agents only fill the typed creative
outputs. No skill may add ad-hoc fields to make one episode pass.

### ADR-064 — content anchors, fail-closed lineage and semantic accuracy

> Downstream consumers must reject a missing, stale, cross-episode or tampered receipt. They must not silently
> fall back to `Default_*.mp4`, raw camera files, `normalized.wav`, or the release-SRT timebase after this
> contract is active.

> Visual events must bind to a content anchor (master cue IDs/range plus text hash), not only an absolute
> second.

> Abstract passages still must not be covered with misleading metaphors: the Director must find three concrete,
> filmable moments elsewhere in the cut or keep the cut revision-required.

**Strengthened.** Work packets bind the same Master and tight-SRT identities, visual events cite exact transcript
evidence, and the same-Director second-pass semantic audit makes misleading-but-technically-valid stock a blocking failure.
There is no raw-media or count-only fallback.

### ADR-050 — Brook ownership and package boundary

> Sub-package 保持硬邊界（自己的 README / CONTEXT.md / tests 樹），緩解 panel 的 monolith 顧慮 — Brook 其他模組不得
> import 其內部，只准走 CLI / pipeline 頂層 API（call-not-host 內化成 package 邊界）。

**Still satisfied.** Ownership stays with Brook; Bridge and orchestration consume only the visual-pipeline public
API. No new agent or cross-context domain owner is introduced, so top-level agent ownership documents do not
change.

## Consequences

- The misnamed Resolve scripts can no longer be mistaken for agent skill execution in production documentation.
- A minimum Stock Video count remains a density guard, not evidence of semantic quality.
- Existing Podcast `_broll.json` files without fresh Director/DP/audit receipts are legacy artifacts and cannot
  authorize a new materialization.
- Bridge gains inspectable provenance without becoming another mutable source of truth.
- The same Director worker's semantic-audit pass adds latency, but removes the larger cost of rendering and reviewing an incoherent cut.

## Acceptance evidence required before Active

- contract tests for missing, stale, cross-episode, partial and semantic-mismatch receipts;
- a materializer regression proving no receipt chain means no Resolve mutation;
- Bridge route/template tests for complete, pending, invalid and escaped untrusted text;
- skill routing tests proving Director → DP → semantic audit/validate → materialize order;
- an operational `value-L01` rerun whose formerly mismatched education footage cannot pass semantic audit.
