"""FCPXML 1.10 emitter — DaVinci Resolve timeline writer (ADR-032 §3).

V1 track: talking head (raw_recording.mp4), full duration, no transform.
V2 track: rendered B-roll mp4 references at each beat's timing.
A1 track: talking head audio.

Phase 1 layouts (full_aroll / full_broll) do not require adjust-transform.
side_overlay_* / pip_corner_br require transform, which requires a verified
DaVinci import fixture (ADR-032 §3b warning) — deferred to Phase 1.5.

--fcpxml-version flag falls back to 1.11 or 1.9 if 1.10 import fails in
Shosho's DaVinci Resolve version (ADR-032 §Risks).

PR-4 implementation.
"""

from __future__ import annotations


def emit(storyboard, episode_dir):  # pragma: no cover — PR-4
    raise NotImplementedError("PR-4 — see issue #715")
