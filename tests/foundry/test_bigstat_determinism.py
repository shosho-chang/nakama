"""BigStat render verification + visual determinism (ADR-032 acceptance).

ADR-032 originally required sha256-identical mp4 across two renders. 2026-05-26
investigation proved this is **unachievable with Hyperframes 0.6.42** even
under `--docker` mode (H.264 encoder multithreading produces small bitstream
variations across runs). The actual achievable bar is visual determinism via
SSIM: empirically observed SSIM ≥ 0.9997 between any two renders of the same
composition with identical params.

Tests:
1. `test_bigstat_render_produces_valid_mp4` — render path E2E works, output
   matches composition spec (1920×1080, 5s, 150 frames).
2. `test_bigstat_render_dimensions_are_stable` — frame count + duration +
   resolution identical across runs (structural determinism).
3. `test_bigstat_render_is_visually_deterministic` — SSIM ≥ 0.99 between two
   independent renders. This is the **canonical acceptance** post-ADR-032
   amendment.
4. `test_bigstat_render_is_byte_deterministic` — kept as `xfail(strict=True)`
   so an unexpected pass (e.g. Hyperframes upstream fixes encoder) surfaces a
   chance to tighten the bar.

CI-skipped: requires local npx + hyperframes + ffmpeg (heavy, ~10s per render).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VIDEO_DIR = REPO_ROOT / "video"
BIGSTAT_DIR = VIDEO_DIR / "compositions" / "bigstat"

EXPECTED_DURATION_SEC = 5.0
EXPECTED_FPS = 30
EXPECTED_FRAMES = int(EXPECTED_DURATION_SEC * EXPECTED_FPS)
DURATION_TOLERANCE_SEC = 0.05

# Empirically observed SSIM between two independent renders on Windows host
# was 0.9997 (2026-05-26). 0.99 is a comfortable acceptance floor — anything
# below would indicate real visual drift, not encoder noise.
SSIM_FLOOR = 0.99


def _hyperframes_available() -> bool:
    if shutil.which("npx") is None:
        return False
    if not (VIDEO_DIR / "node_modules" / "hyperframes").is_dir():
        return False
    return True


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    os.environ.get("CI") == "true" or not _hyperframes_available() or not _ffmpeg_available(),
    reason="requires local npx + video/node_modules/hyperframes + ffmpeg",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _render(out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        (
            f"npx hyperframes render compositions/bigstat "
            f"-c compositions/bigstat.html "
            f'-o "{out_path}" '
            f"-q standard --quiet --no-browser-gpu"
        ),
        cwd=VIDEO_DIR,
        check=True,
        shell=True,
    )


def _probe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames,duration,r_frame_rate,width,height",
            "-count_frames",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)["streams"][0]


def _ssim(a: Path, b: Path) -> float:
    """Return the All-channel SSIM between two videos using ffmpeg's ssim filter."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(a),
            "-i",
            str(b),
            "-lavfi",
            "ssim",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    match = re.search(r"All:([\d.]+)", result.stderr)
    if not match:
        raise RuntimeError(f"SSIM parse failed; ffmpeg stderr tail: {result.stderr[-400:]}")
    return float(match.group(1))


def test_bigstat_render_produces_valid_mp4(tmp_path: Path) -> None:
    """Render path works end-to-end and outputs match composition spec."""
    out = tmp_path / "bigstat.mp4"
    _render(out)

    assert out.stat().st_size > 100_000, "mp4 implausibly small"

    info = _probe(out)
    assert info["width"] == 1920
    assert info["height"] == 1080
    assert abs(float(info["duration"]) - EXPECTED_DURATION_SEC) < DURATION_TOLERANCE_SEC
    assert int(info["nb_read_frames"]) == EXPECTED_FRAMES


def test_bigstat_render_dimensions_are_stable(tmp_path: Path) -> None:
    """Two renders share frame count + duration + resolution."""
    out1 = tmp_path / "run1.mp4"
    out2 = tmp_path / "run2.mp4"
    _render(out1)
    _render(out2)

    p1 = _probe(out1)
    p2 = _probe(out2)
    assert p1["width"] == p2["width"]
    assert p1["height"] == p2["height"]
    assert p1["nb_read_frames"] == p2["nb_read_frames"]
    assert abs(float(p1["duration"]) - float(p2["duration"])) < DURATION_TOLERANCE_SEC


def test_bigstat_render_is_visually_deterministic(tmp_path: Path) -> None:
    """Two renders are visually identical (SSIM ≥ 0.99).

    This is the canonical determinism acceptance for ADR-032 post-2026-05-26
    amendment. Encoder bitstream is not bit-exact (multithreaded H.264) but
    visual output is reproducible to >99.97% pixel similarity empirically.
    """
    out1 = tmp_path / "run1.mp4"
    out2 = tmp_path / "run2.mp4"
    _render(out1)
    _render(out2)

    ssim = _ssim(out1, out2)
    assert ssim >= SSIM_FLOOR, (
        f"BigStat render visual determinism failed: SSIM={ssim:.6f} < {SSIM_FLOOR} "
        f"(expected ≥ 0.9997 empirically)"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Hyperframes 0.6.42 H.264 encoder is not bit-exact across runs, even "
        "under --docker mode (confirmed 2026-05-26). Kept as xfail to surface "
        "any upstream fix that would let us tighten acceptance back to byte-level."
    ),
)
def test_bigstat_render_is_byte_deterministic(tmp_path: Path) -> None:
    """Original ADR-032 v1 acceptance — kept as xfail."""
    out1 = tmp_path / "run1.mp4"
    out2 = tmp_path / "run2.mp4"
    _render(out1)
    _render(out2)
    assert _sha256(out1) == _sha256(out2)
