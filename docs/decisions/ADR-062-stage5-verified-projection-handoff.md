# ADR-062：Stage 5 Verified Projection handoff

- Status: Accepted
- Date: 2026-08-13
- Pipeline anchor: Stage 4 Atomic Content → Stage 5 Production
- Extends: ADR-032、ADR-050 D4、ADR-056

## Decision

Brook `script_video` production `plan` and `run` do not accept a loose SRT path.
`episode.yaml` must bind one `episode_root`, Projection ID, Generation ID, and
SHA-256 of the exact Projection Manifest bytes under `subtitle_v2_handoff`.

The public read-only `open_verified_projection` composition seam constructs a
fresh Podcast Subtitle V2 module and revalidates the complete immutable chain:
Generation artifacts, Correction Ledger ancestry, semantic execution receipts,
boundary constraints, derived projection, Quality Report, render bytes,
Projection Manifest, Projection ID, and the caller-supplied episode/generation/
manifest binding. It returns typed artifacts plus the exact on-disk bytes and
digests; it does not normalize audio, recognize speech, correct text, audit
audio, or create another projection.

`plan` flattens only the returned SRT bytes. It does not normalize or correct
text in Stage 5. Every storyboard anchor must be an exact substring or planning
fails before publishing the storyboard. On success it writes
`storyboard.provenance.json`, binding the storyboard to projection, generation,
manifest, Quality Report, SRT, canonical, profile, token sequence, and output
hashes.

`emit` constructs another fresh verifier, requires exact provenance equality,
and realigns every beat. Timing or cue-ID drift fails before the emitter runs.
Missing handoff data, a bare `transcript.srt`, a self-reported `passed` flag,
artifact tampering, identity drift, or a failed Quality Report never falls back
to legacy input.

## Legacy boundary

`cleanup` and `correct-srt` remain non-production authoring utilities. Their
default subtitle output is `legacy_transcript.srt`; `plan` and `run` ignore it.
Any textual correction discovered in Stage 5 must return to Subtitle V2
`resolve`, create a new immutable Generation, and produce a new Verified
Projection binding.

## Consequences

- Stage 5 cannot silently consume stale or hand-edited subtitle bytes.
- Storyboard and FCPXML lineage can be audited back to one exact projection.
- Reverification may do local deterministic replay work, but cannot call paid
  or mutating provider action ports.
- Existing V1/cleanup subtitle files remain usable only outside this production
  handoff and are not promoted into V2 truth by file presence.
