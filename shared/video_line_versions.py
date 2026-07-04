"""Foundry export-pipeline version constants (ADR-038 §D2).

Single source of truth for cache-invalidation knobs. Bumping any of these
constants forces a full re-render / re-emit on the next pipeline run because
they participate in the content-addressed hash inputs.

When to bump
------------
- ``EXPORT_VERSION`` — when the rendered b-roll mp4 output format / pipeline
  semantics change in a way that makes previously-cached
  ``out/b_roll_<hash>.mp4`` files no longer valid (e.g. switching codecs,
  changing hyperframes runtime, fixing a determinism bug in a worker).
- ``FCPXML_SCHEMA_VERSION`` — when the FCPXML emitter changes structure
  (track layout, asset shape, transform semantics) such that previously
  emitted ``episode.fcpxml`` should not be reused.

Each bump invalidates every cached artifact in one go — this is the global
"flush" lever. For per-beat invalidation see ``agents/brook/script_video/export_hash.py``.
"""

from __future__ import annotations

EXPORT_VERSION: int = 1
"""Increment to invalidate ALL rendered b-roll mp4 caches."""

FCPXML_SCHEMA_VERSION: int = 1
"""Increment to invalidate ALL emitted episode.fcpxml files."""
