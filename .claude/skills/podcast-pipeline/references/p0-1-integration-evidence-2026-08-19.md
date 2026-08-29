# P0-1 downstream baseline integration evidence — 2026-08-19

Target: `codex/moboo-e2e` at initial HEAD `2b58989e`.
Read-only source: `codex/zheng-guowei-long-highlight` at HEAD `37cb4b4c` plus dirty files.

## Adopted

- Finished-cut review vertical slice: authenticated manifest-bound media/subtitle review,
  append-only feedback revisions, terminal single-cut approval, and fail-closed `qa_final.json` gate.
- Finished approval → focused packaging handoff → Resolve `--render-only` receipt → Release registration.
- Packaging approval copies the selected title and thumbnail into the YouTube Release Target.
- Publish approval persists the submitted Title/Description/schedule before filesystem or OAuth preflight.
- OAuth token, upload logs, progress JSON, and child uploader share `NAKAMA_DATA_DIR` (DB parent fallback).
- Long-highlight description voice contract and the approved footer wording.

## Superseded or merged manually

- Source packaging router/template was based on the older pre-#1165–#1169 UI. Only the handoff,
  receipt and focused-board behavior was merged; target variant selection, cutout composition,
  geometry controls and final-folder behavior remain authoritative.
- Source highlight shortlist router was based on the older selection UI. Target program-video media
  route, selection ordering/rank validation and asset-version behavior were retained while adding
  finished review.
- The source audit's claim that Memo-first still uses Qwen as production primary is stale relative to
  target `2b58989e`; it was not copied into the Skill.

## Skipped

- Subtitle V2 evidence bootstrap and Verified Projection consumers: owned by P0-2/P0-3.
- YouTube upload, CC execution, and platform reconciliation: no external calls are part of P0-1.
- Cross-process resumable upload and automatic description generation remain follow-up gaps.

## Verification

- Pre-integration downstream loop: 78 passed.
- Finished review + existing highlight review: 32 passed.
- Packaging gate, including new handoff/receipt cases and existing #1165–#1169 cases: 53 passed.
- Publish review/prep/upload focused tests: 31 passed.
