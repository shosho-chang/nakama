"""Robin Reader EPUB highlight worker (ADR-032 §1, Phase 1.5).

Reuses agents.foundry.lib.web_highlight_record (promoted from spike in PR-1)
once Robin Reader URL scheme is defined (Phase 1.5).
"""

from __future__ import annotations


async def render(beat):
    raise NotImplementedError(
        "Phase 1.5 — promote web_highlight_record + define Robin URL scheme first"
    )
