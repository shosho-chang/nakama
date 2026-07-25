"""Thumbnail still render worker (ADR-033 D10).

Wraps ``npx hyperframes render --format png-sequence`` to capture a single PNG
from a Hyperframes composition at ``video/compositions/thumbnail_youtube/``.

Why png-sequence instead of mp4 + ffmpeg extract: avoids ffmpeg dependency and
the intermediate encode step. Hyperframes writes RGBA PNGs directly to an
output directory; we copy frame 1 to the final ``out_png`` path.

Why ``asyncio.create_subprocess_exec(argv...)`` (panel P6, Codex audit §1):
``hyperframes_worker.py`` uses ``_shell`` with single-quoted JSON for the
``--variables`` argument. On Windows ``cmd.exe`` single quotes are not shell
quoting; the embedded JSON braces would be passed verbatim and parsed wrong.
This worker uses the ``exec`` variant so JSON is passed as an argv element and
never re-tokenised by a shell.

Variables flow:
  Caller → variables dict (Python) → JSON file under ``out_png.parent`` →
  ``npx hyperframes render --variables-file <path>`` → composition reads via
  ``window.__hyperframes.getVariables()``.

The ``cutout_path`` and ``bg_path`` files are base64-encoded inline into the
variables JSON as data URLs. This avoids Chrome ``file://`` access concerns
when assets live outside the Hyperframes project dir (vault attachments).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# repo_root/agents/brook/script_video/render_workers/thumbnail_worker.py → repo_root
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_VIDEO_DIR = _REPO_ROOT / "video"

YOUTUBE_COMPOSITION = "compositions/thumbnail_youtube"
PODCAST_COMPOSITION = "compositions/thumbnail_podcast"


class ThumbnailRenderError(Exception):
    """Hyperframes subprocess failed for a thumbnail render."""

    def __init__(self, out_png: Path, stderr_tail: str) -> None:
        self.out_png = out_png
        self.stderr_tail = stderr_tail
        super().__init__(f"thumbnail render failed (target={out_png.name}): {stderr_tail!r}")


def _to_data_url(path: Path) -> str:
    """Read a PNG/JPEG from disk and return a base64 data URL."""
    suffix = path.suffix.lower().lstrip(".")
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
        suffix, "application/octet-stream"
    )
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def _build_argv(
    composition: str,
    variables_file: Path,
    frames_dir: Path,
) -> list[str]:
    """Construct the ``npx hyperframes render`` argv list.

    Kept as a pure function so tests can assert the exact arg sequence without
    actually spawning hyperframes.
    """
    return [
        "npx",
        "hyperframes",
        "render",
        composition,
        "--variables-file",
        str(variables_file),
        "--format",
        "png-sequence",
        "-o",
        str(frames_dir),
        "--fps",
        "30",
        "--quality",
        "standard",
        "--quiet",
        "--no-browser-gpu",
    ]


async def _render_still(
    composition: str,
    variables: dict,
    out_png: Path,
    video_dir: Path,
) -> Path:
    """Common Hyperframes execution path — used by both YouTube + Podcast renders.

    Writes ``variables`` as JSON into ``out_png.parent``, runs ``npx hyperframes
    render`` against ``composition``, copies the first emitted PNG to ``out_png``.

    Pure mechanism — caller validates input files + builds the variables dict.
    """
    out_png.parent.mkdir(parents=True, exist_ok=True)
    variables_file = out_png.parent / f"{out_png.stem}.variables.json"
    frames_dir = out_png.parent / f"_frames_{out_png.stem}"

    # Clean any leftovers from a previous attempt on this slot.
    if frames_dir.exists():
        shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True)

    variables_file.write_text(
        json.dumps(variables, ensure_ascii=False),
        encoding="utf-8",
    )

    argv = _build_argv(composition, variables_file, frames_dir)
    logger.info(
        "thumbnail render start: out=%s composition=%s",
        out_png.name,
        composition,
    )

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(video_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate()

    if proc.returncode != 0:
        tail = stderr_bytes.decode(errors="replace")[-500:]
        # Leave frames_dir + variables_file for debugging on failure.
        raise ThumbnailRenderError(out_png, tail)

    pngs = sorted(p for p in frames_dir.iterdir() if p.suffix.lower() == ".png")
    if not pngs:
        raise ThumbnailRenderError(
            out_png,
            f"hyperframes produced no PNGs in {frames_dir}",
        )

    shutil.copy2(pngs[0], out_png)

    # Cleanup intermediates only on success.
    shutil.rmtree(frames_dir, ignore_errors=True)
    variables_file.unlink(missing_ok=True)

    logger.info("thumbnail render done: %s", out_png)
    return out_png


async def render_youtube_still(
    *,
    title_hook: str,
    cutout_path: Path,
    bg_path: Path | None = None,
    out_png: Path,
    accent_decoration: str = "",
    palette: dict | None = None,
    video_dir: Path | None = None,
) -> Path:
    """Render one YouTube thumbnail to ``out_png`` (1280×720 PNG).

    Args:
        title_hook: 3-5 字 punchy hook for the large foreground text.
        cutout_path: transparent-PNG host cutout from
            ``shared.cutout_library.pick_youtube_host`` (lives in vault).
        bg_path: optional background image. When ``None``, the composition's
            CSS gradient fallback (palette.bg) is used. PR4-A ships without
            background images; PR5 wires Unsplash + AI generation.
        out_png: final PNG destination (under
            ``data/thumbnails/{slug}/runs/{ts}/v{n}.png``).
        accent_decoration: optional small text/number/icon ("3", "65歲", "⚡").
        palette: optional ``{bg, fg, accent, bg_darken}`` overrides.
        video_dir: override for the Hyperframes project root. Default
            ``repo_root/video/``.

    Returns:
        ``out_png`` (resolved Path) on success.

    Raises:
        FileNotFoundError: cutout_path missing (bg_path None is fine).
        ThumbnailRenderError: hyperframes returned non-zero or produced no PNGs.
    """
    video_dir = video_dir or DEFAULT_VIDEO_DIR

    if not cutout_path.is_file():
        raise FileNotFoundError(f"cutout missing: {cutout_path}")
    if bg_path is not None and not bg_path.is_file():
        raise FileNotFoundError(f"background missing: {bg_path}")

    variables = {
        "title_hook": title_hook,
        "cutout_data_url": _to_data_url(cutout_path),
        "bg_data_url": _to_data_url(bg_path) if bg_path is not None else "",
        "accent_decoration": accent_decoration,
        "palette": palette or {},
    }

    return await _render_still(YOUTUBE_COMPOSITION, variables, out_png, video_dir)


async def render_podcast_still(
    *,
    title_hook: str,
    host_cutout_path: Path,
    guest_cutout_path: Path,
    bg_path: Path | None = None,
    out_png: Path,
    accent_decoration: str = "",
    palette: dict | None = None,
    video_dir: Path | None = None,
) -> Path:
    """Render one Podcast thumbnail to ``out_png`` (1280×720 PNG, DOAC style).

    Args:
        title_hook: 3-7 字 hook for the centred large text.
        host_cutout_path: 修修's transparent-PNG cutout for this episode (lives
            under ``Attachments/cutouts/podcast/{ep_slug}/host_v{n}.png`` —
            chosen via :func:`shared.cutout_library.pick_podcast_host`).
        guest_cutout_path: guest's transparent-PNG cutout for this episode.
        bg_path: optional background image. ``None`` falls back to the
            composition's CSS gradient (palette.bg).
        out_png: final PNG destination.
        accent_decoration: optional small text ("EP. 12", "Part 2", "⚡").
        palette: optional ``{bg, fg, accent, bg_darken}`` overrides.
        video_dir: override for the Hyperframes project root.

    Returns:
        ``out_png`` (resolved Path) on success.

    Raises:
        FileNotFoundError: host_cutout_path or guest_cutout_path missing.
        ThumbnailRenderError: hyperframes returned non-zero or produced no PNGs.
    """
    video_dir = video_dir or DEFAULT_VIDEO_DIR

    if not host_cutout_path.is_file():
        raise FileNotFoundError(f"host cutout missing: {host_cutout_path}")
    if not guest_cutout_path.is_file():
        raise FileNotFoundError(f"guest cutout missing: {guest_cutout_path}")
    if bg_path is not None and not bg_path.is_file():
        raise FileNotFoundError(f"background missing: {bg_path}")

    variables = {
        "title_hook": title_hook,
        "host_cutout_data_url": _to_data_url(host_cutout_path),
        "guest_cutout_data_url": _to_data_url(guest_cutout_path),
        "bg_data_url": _to_data_url(bg_path) if bg_path is not None else "",
        "accent_decoration": accent_decoration,
        "palette": palette or {},
    }

    return await _render_still(PODCAST_COMPOSITION, variables, out_png, video_dir)
