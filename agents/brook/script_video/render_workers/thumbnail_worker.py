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
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logger = logging.getLogger(__name__)

_MEASUREMENT_SCHEMA = "nakama.thumbnail_composition_measurement.v1"
_MEASURED_SELECTORS = ("protected_center_bbox", "host_bbox", "guest_bbox", "title_bbox")

# repo_root/agents/brook/script_video/render_workers/thumbnail_worker.py → repo_root
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_VIDEO_DIR = _REPO_ROOT / "video"

YOUTUBE_COMPOSITION = "compositions/thumbnail_youtube"
PODCAST_COMPOSITION = "compositions/thumbnail_podcast"


class ThumbnailRenderError(Exception):
    """Hyperframes subprocess failed for a thumbnail render."""

    def __init__(self, out_png: Path, stderr_tail: str) -> None:
        self.out_png = out_png
        self.stderr_tail = stderr_tail
        super().__init__(f"thumbnail render failed (target={out_png.name}): {stderr_tail!r}")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class _MeasurementReceiver:
    """One-shot loopback receiver used by the rendered DOM to return actual boxes."""

    def __init__(self) -> None:
        self.payload: dict | None = None
        self.event = threading.Event()
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    receiver.payload = json.loads(self.rfile.read(length))
                    self.send_response(204)
                except Exception:
                    receiver.payload = None
                    self.send_response(400)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                receiver.event.set()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/measurement"

    def __enter__(self) -> _MeasurementReceiver:
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)


def _validate_measurement(payload: dict | None, out_png: Path) -> dict:
    if not isinstance(payload, dict):
        raise ThumbnailRenderError(out_png, "composition emitted no DOM measurement")
    canvas = payload.get("canvas")
    boxes = payload.get("bboxes")
    if not isinstance(canvas, dict) or canvas.get("width") != 1280 or canvas.get("height") != 720:
        raise ThumbnailRenderError(out_png, "composition measurement has invalid canvas")
    if not isinstance(boxes, dict):
        raise ThumbnailRenderError(out_png, "composition measurement has no bboxes")
    for selector in _MEASURED_SELECTORS[:3]:
        box = boxes.get(selector)
        if not isinstance(box, dict) or any(
            box.get(k) is None for k in ("x", "y", "width", "height")
        ):
            raise ThumbnailRenderError(out_png, f"composition selector missing: {selector}")
    return {"canvas": canvas, "bboxes": boxes}


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp_name, path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


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
    measurement_context: dict | None = None,
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

    receiver = _MeasurementReceiver() if measurement_context is not None else None
    if receiver is not None:
        variables = {**variables, "__composition_measurement_url": receiver.url}
        receiver.__enter__()
    variables_file.write_text(json.dumps(variables, ensure_ascii=False), encoding="utf-8")

    argv = _build_argv(composition, variables_file, frames_dir)
    # Windows 的 npx 是 npx.cmd — CreateProcess 不吃 PATHEXT，spawn 前解析成完整路徑。
    argv[0] = shutil.which(argv[0]) or argv[0]
    logger.info(
        "thumbnail render start: out=%s composition=%s",
        out_png.name,
        composition,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(video_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await proc.communicate()
    finally:
        if receiver is not None:
            receiver.event.wait(timeout=2)
            receiver.__exit__(None, None, None)

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

    frame_bytes = pngs[0].read_bytes()
    if receiver is not None:
        measured = _validate_measurement(receiver.payload, out_png)
        public_variables = {k: v for k, v in variables.items() if not k.startswith("__")}
        image_evidence = {
            name: {
                "path": str(path.resolve()),
                "sha256": _sha256_bytes(path.read_bytes()),
            }
            for name, path in measurement_context["images"].items()
        }
        package = json.loads((video_dir / "package.json").read_text(encoding="utf-8"))
        hyperframes_version = (package.get("dependencies") or {}).get("hyperframes")
        composition_source = video_dir / composition / "index.html"
        sidecar = {
            "schema": _MEASUREMENT_SCHEMA,
            "composition": measurement_context["composition"],
            "renderer": {"name": "hyperframes", "version": hyperframes_version},
            "composition_sha256": _sha256_bytes(composition_source.read_bytes()),
            **measured,
            "assets": image_evidence,
            "variables_sha256": _sha256_bytes(
                json.dumps(
                    public_variables, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ),
            "png_sha256": _sha256_bytes(frame_bytes),
        }
        _atomic_write(out_png, frame_bytes)
        _atomic_write(
            out_png.with_suffix(out_png.suffix + ".composition.json"),
            (json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n").encode(),
        )
    else:
        _atomic_write(out_png, frame_bytes)

    # Cleanup intermediates only on success.
    shutil.rmtree(frames_dir, ignore_errors=True)
    variables_file.unlink(missing_ok=True)
    logger.info("thumbnail render done: %s", out_png)
    return out_png


async def render_thumbnail(
    composition: str,
    *,
    variables: dict,
    images: dict[str, Path] | None = None,
    out_png: Path,
    video_dir: Path | None = None,
) -> Path:
    """封面設計系統 v1 的通用入口（thumbnail_full / thumbnail_reaction / thumbnail_topic）。

    ``images`` 把「composition 變數名 → 圖檔路徑」轉成 data URL 塞進 variables
    （變數名照 composition 的 *_data_url 慣例由 caller 給全名）。缺檔 fail loud。
    """
    merged = dict(variables)
    for var_name, path in (images or {}).items():
        if not path.exists():
            raise FileNotFoundError(f"{var_name}: image not found: {path}")
        merged[var_name] = _to_data_url(path)
    measurement_context = None
    if composition == "thumbnail_reaction":
        required = {"prop_image_data_url", "host_cutout_data_url", "guest_cutout_data_url"}
        missing = required.difference(images or {})
        if missing:
            raise ValueError(f"thumbnail_reaction measurement requires images: {sorted(missing)}")
        measurement_context = {"composition": composition, "images": dict(images or {})}
    return await _render_still(
        f"compositions/{composition}",
        merged,
        out_png,
        video_dir or DEFAULT_VIDEO_DIR,
        measurement_context,
    )


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
