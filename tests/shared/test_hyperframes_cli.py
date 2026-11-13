"""HyperFrames CLI 版本鎖定（2026-11-06：上游發新版，npx 停在安裝提示，封面出圖卡 10 分鐘）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.hyperframes_cli import DEFAULT_VIDEO_DIR, npx_argv, pinned_version


def _video_dir(tmp_path: Path, version: object) -> Path:
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"hyperframes": version}}), encoding="utf-8"
    )
    return tmp_path


def test_npx_argv_pins_exact_version_and_never_prompts(tmp_path: Path):
    argv = npx_argv("render", "x", video_dir=_video_dir(tmp_path, "0.8.206"))
    assert argv == ["npx", "--yes", "hyperframes@0.8.206", "render", "x"]


@pytest.mark.parametrize("version", ["^0.8.206", "~0.8.0", "latest", "*", None, ""])
def test_range_or_missing_version_is_refused(tmp_path: Path, version):
    """範圍版本會讓 npx 回去查 registry，等於沒鎖——直接擋下，不要悄悄放行。"""
    with pytest.raises(RuntimeError, match="確切版本"):
        pinned_version(_video_dir(tmp_path, version))


def test_repo_package_json_is_pinned_exactly():
    assert pinned_version(DEFAULT_VIDEO_DIR)


@pytest.mark.parametrize(
    "path",
    [
        "agents/brook/script_video/render_workers/thumbnail_worker.py",
        "agents/brook/script_video/render_workers/hyperframes_worker.py",
        ".claude/skills/thumbnail-brainstorm/scripts/guest_cutout.py",
    ],
)
def test_call_sites_go_through_the_pinned_helper(path: str):
    source = (DEFAULT_VIDEO_DIR.parent / path).read_text(encoding="utf-8")
    assert "npx_argv(" in source
    assert '"npx",\n        "hyperframes"' not in source
    assert '"hyperframes", "remove-background"' not in source
