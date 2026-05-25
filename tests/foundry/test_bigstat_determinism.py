"""BigStat render verification + determinism findings.

ADR-032 Acceptance Criterion (Determinism) originally required sha256-identical
mp4 across two renders with identical parameters. **Investigation 2026-05-26
on Windows host showed Hyperframes 0.6.42 default render path is NOT
bit-deterministic** — neither container atoms, the H.264 video stream, nor the
decoded raw pixel bitmap are stable across runs (491-956 byte mp4 size drift,
divergent raw-pixel sha256 between runs).

Codex audit §4 + Gemini audit §4 both flagged this risk during ADR-032 panel
review. Confirmed empirically here.

For Phase 1 we keep an asserting test that checks the render path actually
works (output exists, expected duration, expected frame count) and KEEP a
separate `xfail` test that documents the byte-determinism finding so the
acceptance bar is not silently lowered. Phase 1.5 will revisit with
`hyperframes render --docker` (deterministic container mode) — see follow-up
GitHub issue.

CI-skipped: requires `npx hyperframes` (heavy, ~10s wall time per render) and
local Chrome.
"""

from __future__ import annotations

import hashlib
import json
import os
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


def _hyperframes_available() -> bool:
    if shutil.which("npx") is None:
        return False
    if not (VIDEO_DIR / "node_modules" / "hyperframes").is_dir():
        return False
    return True


pytestmark = pytest.mark.skipif(
    os.environ.get("CI") == "true" or not _hyperframes_available(),
    reason="requires local npx + video/node_modules/hyperframes",
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
    """Two renders produce same frame count + duration + resolution.

    This is the weaker determinism guarantee we can hold in Phase 1 — visual
    structure is reproducible even if encoder output bytes are not.
    """
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Hyperframes 0.6.42 default render path on Windows host is NOT "
        "byte-deterministic — confirmed 2026-05-26. Track via Phase 1.5 "
        "`--docker` migration. Failing this xfail = unexpected pass = good news, "
        "revisit acceptance."
    ),
)
def test_bigstat_render_is_byte_deterministic(tmp_path: Path) -> None:
    """ADR-032 original acceptance — kept as xfail to surface if it ever passes."""
    out1 = tmp_path / "run1.mp4"
    out2 = tmp_path / "run2.mp4"
    _render(out1)
    _render(out2)
    assert _sha256(out1) == _sha256(out2)
