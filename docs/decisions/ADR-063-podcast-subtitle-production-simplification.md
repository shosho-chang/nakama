# ADR-063: Memo Dual-Audit Release V1 as the production subtitle contract

- Status: Accepted / Active
- Date: 2026-08-21
- Owners: Brook / Podcast production
- Stage: 5 製作
- Supersedes for production: ADR-056 Podcast Subtitle V2
- Retains: ADR-032 exact-copy、ADR-050 D4 provenance、ADR-056 artifacts as legacy forensic evidence

## Context

Formal Subtitle V2 Stage 7 attempted to preserve every correction through checkpoint basis migration,
packet materialization, Canonical Generation, Semantic Units, and Verified Projection. The approach is
auditable, but the real 抹布 episode exposed an operational mismatch: a 16,233-artifact basis migration
did not finish inside a five-minute bounded run, so the system could not reach the correction work packets
that were needed to finish the episode.

The episode was completed with a narrower supervised route: Memo boundary authority, two independent
full-text audits, deterministic agreement and arbitration, and two independent ASR model families for
every major-risk component. It preserved the safety properties needed for production delivery without
requiring the deep checkpoint machinery. On 2026-08-21 the user selected this narrower route as the
production default. Operational complexity is no longer a reason to stop an ordinary episode before the
first editorial review gate.

## Decision

The sole default production subtitle contract is:

```text
podcast-subtitle-memo-dual-audit-release-v1
```

The companion contract identities are:

- request: `podcast-subtitle-memo-dual-audit-release-request-v1`;
- export: `podcast-subtitle-memo-dual-audit-release-export-v1`;
- audio decisions: `podcast-subtitle-memo-dual-audit-audio-decisions-v1`;
- major-audio plan: `podcast-subtitle-memo-dual-audit-major-audio-plan-v1`;
- ASR provider output: `podcast-subtitle-memo-dual-audit-asr-provider-output-v1`;
- major-ASR run: `podcast-subtitle-memo-dual-audit-major-asr-run-v1`;
- status: `podcast-subtitle-memo-dual-audit-release-status-v1`.

Its state flow is:

```text
verified normalized-audio handoff
  -> Memo large-v2 recognition and accepted cue boundaries
  -> deterministic cue, coverage, and timebase QC
  -> two independent whole-transcript text audits
  -> strict deterministic consensus and bounded Arbitration
  -> all major-risk components audited by Faster-Whisper and Qwen3-ASR
  -> conflicts and non-major unresolved components retain exact Memo text
  -> hash-bound release SRT, release ledger, export manifest, and Stage 5 handoff
  -> Resolve project and highlight mining
  -> Highlight shortlist review
```

The canonical episode-local output directory is:

```text
<episode>/subtitle-release/memo-dual-audit-v1/
```

The Stage 5 contract is:

```text
podcast-subtitle-stage5-memo-dual-audit-handoff-v1
```

and its selector mode is:

```text
memo-dual-audit-v1
```

The operator must not label these outputs `degraded`. Existing 抹布 artifacts keep their historical names
and bytes; promotion happens only through a new receipt or handoff that binds existing bytes, never through
an in-place rename.

Formal Subtitle V2 Stage 7 code and checkpoints remain readable only through an explicit
`legacy-forensic` entry point. The Podcast E2E default must not call its checkpoint migration, correction
packet materialization, Canonical Generation, Semantic Unit, or Verified Projection paths.

The public production runner is `scripts/podcast_subtitle_release.py` with
`init`／`status`／`seal`／`prepare-major-audio`／`run-major-asr`／`build-audio-decisions`／`finalize`.
`init` is the only supported way to create the request; operators must
not handwrite it. `seal` binds the actual bytes of newly available inputs before the next status check, and
`finalize` auto-seals before committing the release. `verify-legacy` is isolated to historical bundles and
does not promote them. Pending status exits 3, ready／complete exits 0, and fatal contract or drift exits 2.
The request `episode_id` must exactly equal the episode-root folder basename. Operators derive it as
`Split-Path "<episode>" -Leaf`; both `init` and later request loads fail fast on mismatch. `status.json` is a
mutable diagnostic snapshot that commands may atomically replace, not immutable release evidence.

The immutable production model revisions are Faster-Whisper
`edaa852ec7e145841d8ffdb056a99866b5f0a478`, Qwen3-ASR
`7278e1e70fe206f11671096ffdd38061171dd6e5`, and Qwen3 ForcedAligner
`c7cbfc2048c462b0d63a45797104fc9db3ad62b7`. At `awaiting_major_dual_asr`, the operator creates the exact
clip plan, runs Faster once, runs Qwen once, and builds audio decisions from both manifests. Each family
loads once per command and reuses exact verified completed provider outputs on resume. The decision builder
accepts only an audit-derived candidate whose normalized target observation agrees across both families;
otherwise it records a Memo-retention decision. This sequence has no ordinary human gate.

## Production invariants

1. The exact normalized audio and its verified handoff are the clock and content trust root.
   The request `episode_id` exactly equals the episode-root folder basename; no alternate slug normalization
   is allowed.
2. Memo large-v2 is the primary text and cue-boundary authority. Faster-Whisper and Qwen3-ASR are
   independent major-risk audit evidence, not global boundary authorities.
3. Both whole-transcript text audits must cover the same complete ordered cue set and bind raw exact cue
   text and timestamps. Missing, duplicated, reordered, or drifted cues fail closed.
4. Automatic text changes use a closed safe-category allowlist and deterministic agreement. Arbitration
   cannot introduce a replacement that was absent from the exact source-bound audit proposals.
5. Every `major_risk=true` component must have replay-verified Faster-Whisper and Qwen3-ASR evidence over
   immutable clips bound to the normalized-audio hash. A conflict retains Memo text and is recorded; it is
   not guessed from semantics.
6. Every non-major unresolved component explicitly retains Memo text and is recorded in the ledger.
7. The release SRT must have sequential, non-empty cues, positive duration, zero overlap, and complete
   coverage of the accepted Memo cue set. A fresh run over identical inputs must be byte-identical.
8. Release SRT, ledger, manifest, and handoff bind one another by exact relative path, size, and SHA-256.
   Partial output or destination collision fails before the commit marker is written.
9. Agent quorum owns recognition acceptance, cue acceptance, text audit, and major-risk audio audit when
   deterministic validation passes. Ordinary model disagreement retains Memo text and does not create a
   human gate.
10. Before Highlight shortlist review, the E2E may stop only for a wrong episode/audio binding or a
    catastrophic hash, coverage, or timebase failure. Provider/data-destination changes still require the
    authorization applicable to that external action.
11. Starting or continuing an identifiable episode authorizes that episode's configured Auphonic upload,
    Memo runner, and already-configured subscription workers under the standing Podcast E2E policy. It
    does not authorize YouTube upload.
12. The first ordinary human editorial gate is Highlight shortlist review. The system lists validated
    candidates and must not select winners for the user.
13. Highlight miners write `podcast-highlight-miner-output-v1` files bound to the official Stage 5 lineage.
    `run_highlight_cut.py --merge-miners` is the only mechanical merge: it validates all three roles, writes
    `podcast-highlight-candidates-v1`, and validates candidate boundaries before persona review.
14. The shortlist command fails closed unless the three scoring reviews, brand lens, and Renee lens bind the
    exact final candidates SHA-256 and completely cover the long-candidate ID set. Renee remains required by
    the runtime strict gate even though it does not affect numeric rank.

## Human gates

Before the shortlist, human intervention is exceptional and limited to:

- resolving an ambiguous episode or canonical audio path;
- a wrong-episode/audio finding;
- catastrophic hash, coverage, or timebase failure;
- a changed external provider or data destination that is outside existing authorization.

The regular first gate is Highlight shortlist review. Later gates remain finished-cut review, packaging
review, and explicit YouTube upload approval.

## Consequences

### Benefits

- Normal episodes no longer wait for deep checkpoint migration or full-audit packet materialization.
- High-risk lexical changes still receive two independent audio-model observations.
- Disagreement is safe and cheap: retain Memo text, record the conflict, and continue.
- The Stage 5 consumer receives one portable, hash-bound release contract rather than choosing among loose
  SRT files.
- The same production path can be driven by Codex or Claude Code because the operational truth lives in
  repository skills and contracts rather than platform memory.

### Trade-offs

- Production no longer emits Canonical Generation, Semantic Units, or Verified Projection.
- Non-major unresolved text is intentionally conservative and may preserve a Memo error.
- The old deep lineage remains useful for forensic research, but it is not evidence that the default E2E
  completed.

## Migration

1. Add the new production runner and schemas for the release and Stage 5 handoff contracts named above.
2. Route Resolve and highlight consumers to `memo-dual-audit-v1` by default; production commands must not
   require `--degraded-release-handoff`.
3. Move deep Full V2 entry points behind an explicit `legacy-forensic` mode and prove default routing does
   not import or invoke them.
4. Update Podcast and transcription skills to describe only this ADR as the default production path.
5. Keep all old checkpoints and evidence immutable. Do not rename or rewrite 抹布 artifacts.
6. Run the operational smoke on the next clean episode. 抹布 is a backward-compatibility fixture, not a
   clean acceptance fixture, because its Program Feed contains a source bitstream fault and its subtitle
   bundle predates this contract. This operational smoke remains pending and does not reverse the completed
   code cutover.
7. Code, schema, consumer, routing, and focused regression gates passed on 2026-08-21; this ADR is therefore
   `Accepted / Active`. Record the release contract hashes in the next clean episode handoff.

## Verification record — 2026-08-21

Independent QA issued **CODE CUTOVER GO** with no open P0 or P1 findings:

Targeted execution-lineage and fail-closed verification: **38 passed**.

```powershell
E:\nakama\.venv-v2\Scripts\python.exe -m pytest -q tests/scripts/test_podcast_subtitle_v2_evidence.py::test_prepare_recognition_rejects_valid_srt_from_another_execution tests/scripts/test_podcast_subtitle_v2_evidence.py::test_prepare_recognition_requires_execution_receipt_cli tests/scripts/test_podcast_subtitle_v2_evidence.py::test_prepare_recognition_rejects_tampered_execution_artifact tests/scripts/test_podcast_subtitle_v2_evidence.py::test_prepare_recognition_rejects_wrong_runtime_path tests/scripts/test_podcast_subtitle_v2_evidence.py::test_prepare_recognition_rejects_execution_receipt_path_escape tests/scripts/test_podcast_subtitle_v2_evidence.py::test_accept_recognition_fresh_rejects_execution_tamper_after_review tests/scripts/test_podcast_subtitle_v2_evidence.py::test_memo_srt_is_a_complete_recognition_import_without_handwritten_tokens tests/scripts/test_podcast_subtitle_v2_evidence.py::test_repair_memo_srt_merges_zero_duration_cue_forward_with_exact_lineage tests/scripts/test_podcast_subtitle_release.py::test_release_fresh_replay_rejects_tampered_memo_execution_artifact tests/scripts/test_podcast_subtitle_release.py::test_release_rejects_missing_memo_execution_receipt tests/scripts/test_podcast_subtitle_release.py::test_official_release_rejects_legacy_recognition_without_execution_lineage tests/scripts/test_podcast_subtitle_release.py::test_release_rejects_execution_reference_path_escape tests/scripts/test_podcast_subtitle_release.py::test_release_rejects_cross_episode_worker_audit_with_fresh_hash tests/scripts/test_podcast_subtitle_release.py::test_non_2630_episode_and_conflict_retain_memo tests/agents/brook/podcast_subtitles/test_memo_bundled_runner.py tests/agents/brook/podcast_subtitles/test_memo_vad_gap_repair.py::test_gap_repair_cli_flows_through_recognition_and_cue_evidence
```

Focused core, Stage 5, routing, and regression verification: **249 passed**.

```powershell
E:\nakama\.venv-v2\Scripts\python.exe -m pytest -q tests/scripts/test_podcast_subtitle_v2_evidence.py tests/scripts/test_podcast_subtitle_release.py tests/agents/brook/podcast_subtitles/test_memo_bundled_runner.py tests/agents/brook/podcast_subtitles/test_memo_vad_gap_repair.py tests/skills/test_podcast_pipeline_v2_skill.py tests/agents/brook/podcast_subtitles/test_faster_whisper_recognition_adapter.py tests/brook/script_video/test_verified_projection_handoff.py tests/test_highlight_cut.py tests/test_cut_shortlist.py
```

- Ruff: **PASS**;
- production CLI `--help` smoke: **PASS**;
- `git diff --check`: exit **0**;
- final severity count: **P0 = 0, P1 = 0**.

These results activate the repository production contract and default routing. They do not claim that a new
interview has completed the operational E2E. The next clean episode smoke from Auphonic through Highlight
shortlist review is still pending; 抹布 remains legacy/backward-compatibility evidence only.

## Acceptance criteria

- One supported production command runs from accepted Memo evidence to a valid Stage 5 handoff.
- The default route creates or modifies no `.subtitle-v2/create-checkpoints` artifact and invokes no Formal
  Stage 7 factory or basis migration.
- Two full text audits cover every cue and major-risk audio coverage is exactly complete.
- `awaiting_major_dual_asr` runs in order: prepare plan, Faster, Qwen, build decisions, seal, status, finalize;
  no manual transcript/segments are injected into provider output.
- Conflicting major-risk and all unresolved non-major components retain Memo text with ledger reasons.
- Fresh replay is byte-identical; tamper, path escape, partial output, unknown category, cue drift, and ASR
  conflict tests fail closed or retain source text as specified.
- Resolve and highlight consumers accept `podcast-subtitle-stage5-memo-dual-audit-handoff-v1` in
  `memo-dual-audit-v1` mode without the historical degraded flag.
- Operational follow-up: prove on the next clean episode that the active route advances automatically through
  Resolve and highlight mining to Highlight shortlist review unless a predeclared catastrophic condition
  occurs. This smoke is not part of the completed code cutover evidence above.
- Three lineage-bound miner outputs pass `--merge-miners`; strict review source-hash and complete-coverage
  checks pass before the long shortlist is rendered.
- Production documents call the cutover active only after the 2026-08-21 code and integration verification
  record above; operational episode completion is reported separately.
