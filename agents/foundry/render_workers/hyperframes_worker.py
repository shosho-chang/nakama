"""Hyperframes B-roll render worker (ADR-032 §1).

Wraps `npx --prefix video/ hyperframes render` for Phase 1 BigStat component.

PR-4 implementation.
"""

from __future__ import annotations


class HyperframesRenderError(Exception):
    """Hyperframes subprocess returned non-zero. Carries beat_id + stderr tail."""

    def __init__(self, beat_id: int, stderr_tail: str):
        self.beat_id = beat_id
        self.stderr_tail = stderr_tail
        super().__init__(f"hyperframes render failed for beat {beat_id}: {stderr_tail!r}")


async def render(beat):  # pragma: no cover — PR-4
    raise NotImplementedError("PR-4 — see issue #715")
