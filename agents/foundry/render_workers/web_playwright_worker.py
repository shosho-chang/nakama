"""External web-page highlight worker (ADR-032 §1, Phase 1.5).

Reuses agents.foundry.lib.web_highlight_record (promoted from spike in PR-1).
URL + quote text → CDP screencast → mp4 with highlight overlay animation.
"""

from __future__ import annotations

from pathlib import Path


async def render(beat: dict, out_dir: Path) -> Path:
    raise NotImplementedError("Phase 1.5 — wire dispatcher to web_highlight_record")
