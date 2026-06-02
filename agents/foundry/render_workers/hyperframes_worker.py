"""Hyperframes B-roll render worker (ADR-032 §1).

Wraps `npx --prefix video/ hyperframes render` for Phase 1 BigStat component.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# repo_root/agents/foundry/render_workers/hyperframes_worker.py → repo_root
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_VIDEO_DIR = _REPO_ROOT / "video"


class HyperframesRenderError(Exception):
    """Hyperframes subprocess returned non-zero. Carries beat_id + stderr tail."""

    def __init__(self, beat_id: int, stderr_tail: str) -> None:
        self.beat_id = beat_id
        self.stderr_tail = stderr_tail
        super().__init__(f"hyperframes render failed for beat {beat_id}: {stderr_tail!r}")


def _build_command(
    component: str,
    out_path: Path,
    params: dict | None,
) -> str:
    parts = [
        "npx",
        "hyperframes",
        "render",
        f"compositions/{component}",
        "-c",
        f"compositions/{component}.html",
        "-o",
        f'"{out_path}"',
        "-q",
        "standard",
        "--quiet",
        "--no-browser-gpu",
    ]
    if params:
        parts.extend(["--variables", f"'{json.dumps(params)}'"])
    return " ".join(parts)


async def render(
    beat: dict,
    out_dir: Path,
    video_dir: Path | None = None,
    cached_hash: str | None = None,
) -> Path:
    """Render a beat's B-roll via npx hyperframes; return the mp4 path.

    Raises HyperframesRenderError on non-zero exit. Output filename is
    ``b_roll_<cached_hash>.mp4`` inside ``out_dir`` (ADR-038 §D2 content-
    addressed export). ``cached_hash`` is computed by the dispatcher via
    ``agents.foundry.export_hash.compute_beat_hash``; passing ``None`` is a
    contract violation and raises ``ValueError`` — workers never compute the
    hash themselves so the dispatcher's cache-skip stays authoritative.
    """
    video_dir = video_dir or DEFAULT_VIDEO_DIR
    beat_id = beat["beat_id"]
    broll = beat.get("broll")
    if broll is None:
        raise ValueError(f"beat {beat_id} has no broll spec — cannot render")
    if not cached_hash:
        raise ValueError(
            f"beat {beat_id} render called without cached_hash; "
            f"dispatcher must compute hash before invoking worker (ADR-038 §D2)"
        )

    component = broll["component"]
    params = broll.get("params") or {}
    out_path = out_dir / f"b_roll_{cached_hash}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = _build_command(component, out_path, params)
    logger.info("hyperframes render beat=%d cmd=%s", beat_id, cmd)

    if sys.platform == "win32":
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(video_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    else:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(video_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    _, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr_bytes.decode(errors="replace")[-500:]
        raise HyperframesRenderError(beat_id, tail)

    return out_path
