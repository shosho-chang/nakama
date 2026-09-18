"""build_resolve_project 的 project 設定——播放幀率必須跟著 timeline 幀率。

這個 bug 不會報錯：Resolve 照樣建得起 project、timeline 看起來也正常，只有播放
時一格一格頓、聲音斷續。2026-09-18 謝伯讓集實際量到 timelineFrameRate 30 /
timelinePlaybackFrameRate 24，耗掉一個上午才找到。沒有測試就再也看不見它。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "build_resolve_project", _REPO / "scripts" / "build_resolve_project.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_resolve_project"] = module
    spec.loader.exec_module(module)
    return module


class _Project:
    def __init__(self) -> None:
        self.settings: dict[str, str] = {}

    def SetSetting(self, key: str, value: str) -> bool:  # noqa: N802 - Resolve API
        self.settings[key] = value
        return True


def test_playback_frame_rate_follows_the_timeline_frame_rate():
    """兩者不一致就是用 24 播 30——沒有錯誤訊息，只有卡頓。"""
    module = _load()
    project = _Project()

    module.apply_project_settings(project, {"fps": 30.0, "width": 1920, "height": 1080})

    assert project.settings["timelineFrameRate"] == "30"
    assert project.settings["timelinePlaybackFrameRate"] == "30"


def test_non_integer_frame_rate_stays_matched():
    """29.97 這種也要一致；字串格式沿用既有的去尾零寫法。"""
    module = _load()
    project = _Project()

    module.apply_project_settings(project, {"fps": 29.97, "width": 1920, "height": 1080})

    assert project.settings["timelineFrameRate"] == "29.97"
    assert project.settings["timelinePlaybackFrameRate"] == project.settings["timelineFrameRate"]


def test_resolution_is_skipped_when_the_probe_came_back_empty():
    """探不到長寬時不要寫 0 進去——維持既有行為。"""
    module = _load()
    project = _Project()

    module.apply_project_settings(project, {"fps": 30.0, "width": 0, "height": 0})

    assert "timelineResolutionWidth" not in project.settings
    assert "timelineResolutionHeight" not in project.settings
    assert project.settings["timelineInputResMismatchBehavior"] == "centerCrop"
