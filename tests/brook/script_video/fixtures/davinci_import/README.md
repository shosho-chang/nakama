# DaVinci Resolve import smoke test fixture

ADR-032 Phase 1 acceptance gate — manual import test that proves the FCPXML
shape Foundry emits is actually consumable by DaVinci Resolve.

## What this fixture is

- `minimal.fcpxml` — hand-authored FCPXML 1.10, V1 + V2 stack, no transforms
- `black10s.mp4` — 10s of black + silent stereo audio at 48k (proxy for talking-head A-roll)
- `bigstat3s.mp4` — 3s of solid PANTONE 165 orange (proxy for a BigStat B-roll render)

Timing layout:

```
t=0s ────────────────────────────────────── t=10s
V1: [────── black10s (A-roll proxy) ──────]
V2:             [── bigstat3s ──]
                t=4s          t=7s
```

The B-roll is attached as a nested `asset-clip` with `lane="1"` and
`offset="120/30s"` (4 seconds into the A-roll). No `adjust-transform` is
applied — Phase 1 layouts are `full_aroll` and `full_broll` only, which means
B-roll fully covers the A-roll for its duration. Side-overlay / PiP layouts
land in Phase 1.5 and will add a transform fixture separately.

## How to run the smoke test (manual, blocking PR-4 sign-off)

1. Open DaVinci Resolve (any recent version, tested target: 19.x).
2. `File → Import → Timeline...` and select `minimal.fcpxml` in this directory.
3. Confirm DaVinci does not raise schema errors during import.
4. In the imported timeline, scrub to t=4s and verify the orange B-roll appears
   on V2 over the black A-roll.
5. Scrub to t=7s and verify the B-roll has ended; only black remains.
6. Report outcome in the PR-4 comment thread:
   - ✅ pass → record DaVinci version + comment "FCPXML 1.10 import OK"
   - ❌ fail → record DaVinci version + error text + try fallback below

## Fallback: FCPXML version flag

If DaVinci 1.10 import fails, try:

```
sed -i 's|version="1.10"|version="1.11"|' minimal.fcpxml
```

(or 1.9). Re-import. If a different version succeeds, comment with the version
that works so we can update Foundry's `fcpxml_emitter.py` default. See ADR-032
§3 (`fcpxml_version_default` / `fcpxml_version_fallback`).

## Why this fixture is hand-authored

The Foundry FCPXML emitter (`agents/brook/script_video/fcpxml_emitter.py`) lands in PR-4
proper. To break the chicken-and-egg — "test the emitter against DaVinci
before writing the emitter" — this fixture provides a known-good target shape
the emitter must match. After the emitter ships, an additional automated test
(`test_fcpxml_emitter.py`) will assert it produces structurally equivalent
XML to this fixture given an equivalent storyboard input.
