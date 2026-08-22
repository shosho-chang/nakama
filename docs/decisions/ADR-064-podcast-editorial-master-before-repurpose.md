# ADR-064: Human-approved Editorial Master before Podcast repurpose

- Status: Accepted / Runtime cutover pending
- Date: 2026-08-22
- Owner: Brook / Podcast Stage 5
- Stage: 5 Multi-channel Production
- Amends: ADR-063 human-gate order and post-Resolve routing

## Context

The clean subtitle/Resolve smoke for `20260805 林之晨` proved that Memo Dual-Audit Release V1 can reach
a valid Resolve project and Highlight shortlist. It did not prove that repurposed cuts inherit the user's
full-program editorial decisions.

The episode exposed the missing boundary. The full-program export
`EP121 林之晨 逆分工.mp4` is 3418.304 seconds, while the raw program feed and normalized-audio clock are
3447.666 and 3447.730 seconds. Long Highlight materialization nevertheless rebuilt from
`Default_2026-08-05_1.mp4` plus `normalized.wav`. As a result, `value-L01` retained a duplicated phrase,
cough and apology at raw time 1320.300–1323.140 even though the full-program edit had removed it.
The guest namecard was also placed at a template-fixed 8 seconds while the host was still speaking.

These are not subtitle defects. They are failures to distinguish the Stage 4 transcript truth from the
Stage 5 editorial timebase and media truth.

## Decision

Introduce **Editorial Master** as the only normal source for Podcast content repurpose.

An Editorial Master is the human-reviewed, locked full-program Resolve timeline after the user has:

1. added the recorded Intro and Outro;
2. reviewed the complete episode;
3. removed coughs, apologies, stalls, interruptions and unwanted passages;
4. approved that exact timeline as the fan-out baseline.

The normal order is:

```text
Auphonic normalization + Memo subtitle work
  || user records Intro/Outro
  -> agent creates the base full-program Resolve timeline
  -> user inserts Intro/Outro and performs full-program editorial review
  -> user approves and locks the Editorial Master
  -> agent exports and verifies Editorial Master media + retimed subtitles + receipt
  -> Highlight mining and all materialization use the Editorial Master only
  -> long Highlights / Shorts / Instagram carousel / later derivatives
```

ADR-063 remains authoritative for subtitle recognition, audit and release. Its statement that Highlight
shortlist is the first ordinary human editorial gate is superseded: the first ordinary Stage 5 gate is now
**Editorial Master approval**. Highlight shortlist, finished-cut review, packaging review and upload approval
remain later gates.

The production contract will be `podcast-editorial-master-v1`. Its episode-local receipt must bind at least:

- exact episode ID, Resolve project and timeline identity;
- the Memo Dual-Audit Stage 5 handoff identity;
- approved master media relative path, bytes, SHA-256 and duration;
- approved retimed subtitle relative path, bytes, SHA-256, cue count and timing QC;
- an ordered timeline edit map or equivalent source-to-master mapping;
- approval timestamp and explicit human-approved status;
- an immutable content hash covering the receipt.

Downstream consumers must reject a missing, stale, cross-episode or tampered receipt. They must not silently
fall back to `Default_*.mp4`, raw camera files, `normalized.wav`, or the release-SRT timebase after this
contract is active.

Visual events must bind to a content anchor (master cue IDs/range plus text hash), not only an absolute
second. A ripple edit must deterministically rebase later events. A guest identity card must additionally
pass a speaker-placement check: it begins on the guest's first substantive speech, never during a long host
opening. For `value-L01`, the user-approved placement is 43.0–48.2 seconds.

The finished-cut UI must distinguish asset-backed B-roll from titles, badges and fullscreen transitions.
Zero asset-backed B-roll is allowed when no concrete visual is trustworthy; it must not be confused with
the absence of all visual treatments. Editorial defects such as coughs are cuts, not B-roll opportunities.

## Current episode status

`20260805 林之晨` is the migration episode, not a conforming Editorial Master fixture yet:

- the human-edited full-program MP4/SRT exist but have no `podcast-editorial-master-v1` receipt or edit map;
- `value-L01` was manually rebuilt on 2026-08-22 to remove raw 1320.300–1323.140, move the guest card to
  43.0 seconds, and rebase later visual/SFX events by 2.84 seconds;
- `value-L02` and `punch-L04` remain raw-derived and must not be described as Editorial-Master-derived;
- no automatic rematerialization may overwrite the user's full-program timeline.

## P9 implementation task prompt

1. **Goal** — make a human-approved full-program Resolve timeline the fail-closed media/timebase source for
   every Podcast repurpose output.
2. **Scope** — add an Editorial Master domain module and CLI; update
   `scripts/build_resolve_project.py`, `scripts/run_highlight_cut.py`, `scripts/run_short_tighten.py`,
   `scripts/run_short_director.py`, finished-review manifest generation, and the Podcast/Highlight/Longform
   skills plus focused tests.
3. **Inputs** — ADR-063 Stage 5 handoff, the base Resolve project/timeline, user-added Intro/Outro and edits,
   approved master export, retimed master SRT, existing candidate/review contracts and camera-role mapping.
4. **Outputs** — `podcast-editorial-master-v1` receipt, immutable master media/SRT, edit map, content-anchored
   visual recipes, and downstream manifests whose lineage includes the Editorial Master content hash.
5. **Acceptance** — prove wrong episode, source drift, edited-media tamper, subtitle drift and raw fallback
   fail closed; prove a removed cough cannot reappear in any derivative; prove Intro/Outro and ripple cuts
   preserve candidate/title/B-roll alignment; prove guest cards cannot precede guest speech; run a real
   next-episode Resolve-to-shortlist smoke.
6. **Boundaries** — do not change Memo Dual-Audit lexical evidence, do not rewrite existing immutable
   subtitle releases, do not overwrite a user-edited timeline, do not auto-approve the Editorial Master,
   and do not upload to YouTube.

## Consequences

- Repurpose cannot race ahead of the user's full-program edit, but normalization, recognition and the user's
  Intro/Outro recording can still run in parallel.
- The added human gate is intentional: it is the cheapest point to prevent every later channel from
  reproducing the same editorial defect.
- Until the runtime task above is complete, the honest status for a new episode after Resolve base-timeline
  creation is `EDITORIAL_MASTER_RUNTIME_NOT_IMPLEMENTED`, not Highlight E2E completion.
