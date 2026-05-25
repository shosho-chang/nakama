"""3-path render dispatcher (ADR-032 §1).

Phase 1 only `hyperframes` is implemented. The other two workers raise
NotImplementedError until web_highlight_record.py promotion + Robin URL
scheme are landed in Phase 1.5.

PR-4 implementation.
"""

from __future__ import annotations


async def dispatch_beat(beat):  # pragma: no cover — PR-4
    raise NotImplementedError("PR-4 — see issue #715")


async def run_queue(beats, concurrency: int = 1):  # pragma: no cover — PR-4
    raise NotImplementedError("PR-4 — see issue #715")
