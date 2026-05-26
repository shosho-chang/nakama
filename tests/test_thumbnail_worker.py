"""ADR-033 PR4-A — thumbnail_worker.render_youtube_still + _build_argv.

We mock the asyncio subprocess so tests don't shell out to ``npx hyperframes``.
The contract under test:
  - argv shape (panel P6 — exec not shell, --variables-file not --variables)
  - variables JSON written with correct structure (data URLs base64-encoded)
  - first PNG from frames_dir is copied to out_png on success
  - ThumbnailRenderError raised on non-zero exit or empty frames_dir
  - leftover frames_dir from a previous attempt is wiped before re-render
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.foundry.render_workers.thumbnail_worker import (
    PODCAST_COMPOSITION,
    YOUTUBE_COMPOSITION,
    ThumbnailRenderError,
    _build_argv,
    _to_data_url,
    render_podcast_still,
    render_youtube_still,
)


@pytest.fixture
def assets(tmp_path: Path) -> dict[str, Path]:
    """Fake cutout + bg + target out_png."""
    cutout = tmp_path / "cutout.png"
    cutout.write_bytes(b"\x89PNG\r\n\x1a\nfake-cutout-bytes")
    bg = tmp_path / "bg.jpg"
    bg.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")
    out_png = tmp_path / "out" / "v1.png"
    return {"cutout": cutout, "bg": bg, "out_png": out_png, "video_dir": tmp_path / "video"}


def test_build_argv_uses_variables_file_not_inline_variables():
    """Panel P6: --variables JSON inline through shell would break on Windows."""
    variables_file = Path("/some/path/v1.variables.json")
    frames_dir = Path("/some/path/_frames_v1")
    argv = _build_argv(YOUTUBE_COMPOSITION, variables_file, frames_dir)
    assert argv[:3] == ["npx", "hyperframes", "render"]
    assert YOUTUBE_COMPOSITION in argv
    assert "--variables-file" in argv
    assert str(variables_file) in argv
    # The `--variables` (inline) form must NOT be used.
    assert "--variables" not in argv
    assert "--format" in argv
    assert "png-sequence" in argv
    assert "--no-browser-gpu" in argv


def test_to_data_url_unknown_suffix_falls_back_to_octet_stream():
    # Feed a non-image file (.py) — mime map only knows png/jpg/jpeg.
    p = Path(__file__)
    url = _to_data_url(p)
    assert url.startswith("data:")
    assert "application/octet-stream" in url
    assert ";base64," in url


def test_to_data_url_known_image_mimes(tmp_path: Path):
    png = tmp_path / "a.png"
    png.write_bytes(b"x")
    assert "image/png" in _to_data_url(png)
    jpg = tmp_path / "a.jpg"
    jpg.write_bytes(b"x")
    assert "image/jpeg" in _to_data_url(jpg)
    jpeg = tmp_path / "a.jpeg"
    jpeg.write_bytes(b"x")
    assert "image/jpeg" in _to_data_url(jpeg)


def test_render_missing_cutout_raises(assets: dict[str, Path]):
    assets["cutout"].unlink()
    with pytest.raises(FileNotFoundError, match="cutout missing"):
        asyncio.run(
            render_youtube_still(
                title_hook="妙用解密",
                cutout_path=assets["cutout"],
                bg_path=assets["bg"],
                out_png=assets["out_png"],
                video_dir=assets["video_dir"],
            )
        )


def test_render_missing_bg_raises(assets: dict[str, Path]):
    assets["bg"].unlink()
    with pytest.raises(FileNotFoundError, match="background missing"):
        asyncio.run(
            render_youtube_still(
                title_hook="妙用解密",
                cutout_path=assets["cutout"],
                bg_path=assets["bg"],
                out_png=assets["out_png"],
                video_dir=assets["video_dir"],
            )
        )


def _mock_subprocess(returncode: int = 0, stderr: bytes = b""):
    """Build a MagicMock that emulates asyncio.create_subprocess_exec result."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    return proc


def test_render_success_writes_variables_json_and_copies_first_frame(
    assets: dict[str, Path],
):
    written_argv: list[list[str]] = []

    async def fake_create(*argv, cwd=None, stdout=None, stderr=None):
        written_argv.append(list(argv))
        # Simulate hyperframes producing a PNG in frames_dir before exit
        frames_dir = Path(argv[argv.index("-o") + 1])
        (frames_dir / "frame-0001.png").write_bytes(b"\x89PNG\r\n\x1a\nrendered-bytes")
        (frames_dir / "frame-0002.png").write_bytes(b"\x89PNG\r\n\x1a\nlater-frame")
        return _mock_subprocess(returncode=0)

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
        result = asyncio.run(
            render_youtube_still(
                title_hook="妙用解密",
                cutout_path=assets["cutout"],
                bg_path=assets["bg"],
                out_png=assets["out_png"],
                accent_decoration="⚡",
                palette={"accent": "#FFD400", "bg_darken": 0.55},
                video_dir=assets["video_dir"],
            )
        )

    assert result == assets["out_png"]
    assert assets["out_png"].is_file()
    # First (lowest-numbered) frame wins
    assert assets["out_png"].read_bytes() == b"\x89PNG\r\n\x1a\nrendered-bytes"

    # Variables JSON was deleted after success (cleanup)
    variables_file = assets["out_png"].parent / "v1.variables.json"
    assert not variables_file.exists()
    # Frames dir wiped after success
    frames_dir = assets["out_png"].parent / "_frames_v1"
    assert not frames_dir.exists()

    # argv shape correctness — cwd was video_dir
    assert len(written_argv) == 1
    argv = written_argv[0]
    assert argv[0:3] == ("npx", "hyperframes", "render") or argv[0:3] == [
        "npx",
        "hyperframes",
        "render",
    ]


def test_render_success_variables_json_contains_data_urls(assets: dict[str, Path]):
    """During subprocess, variables file should exist with base64'd assets."""
    captured_variables: dict = {}

    async def fake_create(*argv, cwd=None, stdout=None, stderr=None):
        # Inspect the variables file written before subprocess starts
        vf_idx = argv.index("--variables-file") + 1
        captured_variables.update(json.loads(Path(argv[vf_idx]).read_text(encoding="utf-8")))
        frames_dir = Path(argv[argv.index("-o") + 1])
        (frames_dir / "frame-0001.png").write_bytes(b"PNG")
        return _mock_subprocess(returncode=0)

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
        asyncio.run(
            render_youtube_still(
                title_hook="妙用解密",
                cutout_path=assets["cutout"],
                bg_path=assets["bg"],
                out_png=assets["out_png"],
                accent_decoration="65 歲",
                palette={"bg": "#000000"},
                video_dir=assets["video_dir"],
            )
        )

    assert captured_variables["title_hook"] == "妙用解密"
    assert captured_variables["accent_decoration"] == "65 歲"
    assert captured_variables["palette"] == {"bg": "#000000"}
    assert captured_variables["cutout_data_url"].startswith("data:image/png;base64,")
    assert captured_variables["bg_data_url"].startswith("data:image/jpeg;base64,")


def test_render_subprocess_failure_raises(assets: dict[str, Path]):
    async def fake_create(*argv, cwd=None, stdout=None, stderr=None):
        return _mock_subprocess(returncode=1, stderr=b"hyperframes: composition error xyz")

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
        with pytest.raises(ThumbnailRenderError, match="composition error xyz"):
            asyncio.run(
                render_youtube_still(
                    title_hook="x",
                    cutout_path=assets["cutout"],
                    bg_path=assets["bg"],
                    out_png=assets["out_png"],
                    video_dir=assets["video_dir"],
                )
            )

    # On failure, frames_dir + variables_file kept for debugging
    assert (assets["out_png"].parent / "_frames_v1").exists()
    assert (assets["out_png"].parent / "v1.variables.json").exists()


def test_render_empty_frames_dir_raises(assets: dict[str, Path]):
    """hyperframes returned 0 but produced no PNGs — fail loudly."""

    async def fake_create(*argv, cwd=None, stdout=None, stderr=None):
        return _mock_subprocess(returncode=0)

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
        with pytest.raises(ThumbnailRenderError, match="no PNGs"):
            asyncio.run(
                render_youtube_still(
                    title_hook="x",
                    cutout_path=assets["cutout"],
                    bg_path=assets["bg"],
                    out_png=assets["out_png"],
                    video_dir=assets["video_dir"],
                )
            )


def test_render_wipes_previous_frames_dir(assets: dict[str, Path]):
    """Re-running on the same out_png slot must not see stale frames."""
    frames_dir = assets["out_png"].parent / "_frames_v1"
    frames_dir.mkdir(parents=True)
    stale = frames_dir / "frame-0099.png"
    stale.write_bytes(b"stale-bytes")

    async def fake_create(*argv, cwd=None, stdout=None, stderr=None):
        # Verify stale frame was wiped before subprocess
        d = Path(argv[argv.index("-o") + 1])
        assert not (d / "frame-0099.png").exists(), "previous frames_dir should be wiped"
        (d / "frame-0001.png").write_bytes(b"\x89PNG\r\n\x1a\nfresh")
        return _mock_subprocess(returncode=0)

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
        asyncio.run(
            render_youtube_still(
                title_hook="x",
                cutout_path=assets["cutout"],
                bg_path=assets["bg"],
                out_png=assets["out_png"],
                video_dir=assets["video_dir"],
            )
        )

    assert assets["out_png"].read_bytes() == b"\x89PNG\r\n\x1a\nfresh"


# render_podcast_still — DOAC style, two cutouts


@pytest.fixture
def podcast_assets(tmp_path: Path) -> dict[str, Path]:
    host = tmp_path / "host.png"
    host.write_bytes(b"\x89PNG\r\n\x1a\nfake-host-bytes")
    guest = tmp_path / "guest.png"
    guest.write_bytes(b"\x89PNG\r\n\x1a\nfake-guest-bytes")
    bg = tmp_path / "bg.jpg"
    bg.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")
    out_png = tmp_path / "out" / "ep01.png"
    return {
        "host": host,
        "guest": guest,
        "bg": bg,
        "out_png": out_png,
        "video_dir": tmp_path / "video",
    }


def test_render_podcast_missing_host_raises(podcast_assets: dict[str, Path]):
    podcast_assets["host"].unlink()
    with pytest.raises(FileNotFoundError, match="host cutout missing"):
        asyncio.run(
            render_podcast_still(
                title_hook="關鍵對話",
                host_cutout_path=podcast_assets["host"],
                guest_cutout_path=podcast_assets["guest"],
                bg_path=podcast_assets["bg"],
                out_png=podcast_assets["out_png"],
                video_dir=podcast_assets["video_dir"],
            )
        )


def test_render_podcast_missing_guest_raises(podcast_assets: dict[str, Path]):
    podcast_assets["guest"].unlink()
    with pytest.raises(FileNotFoundError, match="guest cutout missing"):
        asyncio.run(
            render_podcast_still(
                title_hook="關鍵對話",
                host_cutout_path=podcast_assets["host"],
                guest_cutout_path=podcast_assets["guest"],
                bg_path=podcast_assets["bg"],
                out_png=podcast_assets["out_png"],
                video_dir=podcast_assets["video_dir"],
            )
        )


def test_render_podcast_uses_podcast_composition(podcast_assets: dict[str, Path]):
    """Podcast render must dispatch to compositions/thumbnail_podcast."""
    captured_argv: list[list[str]] = []

    async def fake_create(*argv, cwd=None, stdout=None, stderr=None):
        captured_argv.append(list(argv))
        frames_dir = Path(argv[argv.index("-o") + 1])
        (frames_dir / "frame-0001.png").write_bytes(b"\x89PNG\r\n\x1a\npodcast-rendered")
        return _mock_subprocess(returncode=0)

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
        asyncio.run(
            render_podcast_still(
                title_hook="關鍵對話",
                host_cutout_path=podcast_assets["host"],
                guest_cutout_path=podcast_assets["guest"],
                out_png=podcast_assets["out_png"],
                video_dir=podcast_assets["video_dir"],
            )
        )

    assert PODCAST_COMPOSITION in captured_argv[0]
    assert YOUTUBE_COMPOSITION not in captured_argv[0]


def test_render_podcast_variables_json_has_both_cutouts(podcast_assets: dict[str, Path]):
    captured_variables: dict = {}

    async def fake_create(*argv, cwd=None, stdout=None, stderr=None):
        vf_idx = argv.index("--variables-file") + 1
        captured_variables.update(json.loads(Path(argv[vf_idx]).read_text(encoding="utf-8")))
        frames_dir = Path(argv[argv.index("-o") + 1])
        (frames_dir / "frame-0001.png").write_bytes(b"PNG")
        return _mock_subprocess(returncode=0)

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
        asyncio.run(
            render_podcast_still(
                title_hook="關鍵對話",
                host_cutout_path=podcast_assets["host"],
                guest_cutout_path=podcast_assets["guest"],
                bg_path=podcast_assets["bg"],
                out_png=podcast_assets["out_png"],
                accent_decoration="EP. 12",
                palette={"bg": "#000000", "accent": "#FFD400"},
                video_dir=podcast_assets["video_dir"],
            )
        )

    assert captured_variables["title_hook"] == "關鍵對話"
    assert captured_variables["accent_decoration"] == "EP. 12"
    assert captured_variables["palette"] == {"bg": "#000000", "accent": "#FFD400"}
    assert captured_variables["host_cutout_data_url"].startswith("data:image/png;base64,")
    assert captured_variables["guest_cutout_data_url"].startswith("data:image/png;base64,")
    assert captured_variables["bg_data_url"].startswith("data:image/jpeg;base64,")
    # No legacy YouTube `cutout_data_url` key — podcast schema is distinct
    assert "cutout_data_url" not in captured_variables


def test_render_podcast_no_bg_emits_empty_string(podcast_assets: dict[str, Path]):
    captured_variables: dict = {}

    async def fake_create(*argv, cwd=None, stdout=None, stderr=None):
        vf_idx = argv.index("--variables-file") + 1
        captured_variables.update(json.loads(Path(argv[vf_idx]).read_text(encoding="utf-8")))
        frames_dir = Path(argv[argv.index("-o") + 1])
        (frames_dir / "frame-0001.png").write_bytes(b"PNG")
        return _mock_subprocess(returncode=0)

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
        asyncio.run(
            render_podcast_still(
                title_hook="關鍵對話",
                host_cutout_path=podcast_assets["host"],
                guest_cutout_path=podcast_assets["guest"],
                bg_path=None,
                out_png=podcast_assets["out_png"],
                video_dir=podcast_assets["video_dir"],
            )
        )

    assert captured_variables["bg_data_url"] == ""


def test_render_podcast_subprocess_failure_keeps_debug_artifacts(
    podcast_assets: dict[str, Path],
):
    async def fake_create(*argv, cwd=None, stdout=None, stderr=None):
        return _mock_subprocess(returncode=2, stderr=b"hyperframes podcast comp failed")

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
        with pytest.raises(ThumbnailRenderError, match="podcast comp failed"):
            asyncio.run(
                render_podcast_still(
                    title_hook="x",
                    host_cutout_path=podcast_assets["host"],
                    guest_cutout_path=podcast_assets["guest"],
                    out_png=podcast_assets["out_png"],
                    video_dir=podcast_assets["video_dir"],
                )
            )

    # Same debug-artifact convention as YouTube path
    assert (podcast_assets["out_png"].parent / "_frames_ep01").exists()
    assert (podcast_assets["out_png"].parent / "ep01.variables.json").exists()
