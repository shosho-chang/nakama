"""3-path render dispatcher (ADR-032 §1).

Phase 1 only `hyperframes` is implemented. The other two workers raise
NotImplementedError until web_highlight_record.py promotion + Robin URL
scheme land in Phase 1.5.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from agents.foundry.render_workers import (
    hyperframes_worker,
    reader_playwright_worker,
    web_playwright_worker,
)

logger = logging.getLogger(__name__)


async def dispatch_beat(beat: dict, out_dir: Path) -> Path:
    """Route a single beat to the worker named by `beat.broll.render_target`.

    Returns the rendered mp4 path. Raises NotImplementedError for Phase 1.5
    targets and ValueError for unknown targets.
    """
    broll = beat.get("broll")
    if broll is None:
        raise ValueError(f"beat {beat.get('beat_id')} has no broll spec")
    target = broll["render_target"]
    if target == "hyperframes":
        return await hyperframes_worker.render(beat, out_dir)
    if target == "reader-playwright":
        return await reader_playwright_worker.render(beat, out_dir)
    if target == "web-playwright":
        return await web_playwright_worker.render(beat, out_dir)
    raise ValueError(f"unknown render_target: {target!r}")


async def run_queue(
    beats: list[dict],
    out_dir: Path,
    concurrency: int = 1,
) -> list[Path]:
    """Render a list of beats with a Semaphore-bounded concurrency.

    Phase 1 default concurrency=1 (ADR-032 §8 conservative — measure first
    then raise in Phase 1.5). Returns rendered paths in input order.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be ≥ 1")
    sem = asyncio.Semaphore(concurrency)

    async def _one(b: dict) -> Path:
        async with sem:
            return await dispatch_beat(b, out_dir)

    return await asyncio.gather(*[_one(b) for b in beats])
