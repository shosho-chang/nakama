"""成品檔名規則：完整版用集數名，精華用 cut_id（修修 2026-10-26）。"""

from __future__ import annotations

from pathlib import Path

from shared.export_paths import (
    export_media_path,
    export_stem,
    export_subtitle_path,
    find_export_media,
    is_exported,
)


def test_full_cut_is_named_after_the_episode(tmp_path):
    episode = tmp_path / "20261021 Kelly Tsai"

    assert export_stem(episode, "full") == "20261021 Kelly Tsai"
    assert export_media_path(episode, "full") == (
        episode / "highlights" / "exports" / "20261021 Kelly Tsai.mp4"
    )
    assert export_subtitle_path(episode, "full") == (
        episode / "highlights" / "exports" / "20261021 Kelly Tsai.srt"
    )


def test_highlight_cuts_keep_their_cut_id(tmp_path):
    episode = tmp_path / "20261021 Kelly Tsai"

    assert export_stem(episode, "punch-L01") == "punch-L01"
    assert export_media_path(episode, "punch-S03").name == "punch-S03.mp4"


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"mp4")
    return path


def test_full_cut_exported_under_the_episode_name(tmp_path):
    episode = tmp_path / "20261021 Kelly Tsai"
    assert not is_exported(episode, "full")

    media = _touch(episode / "highlights" / "exports" / "20261021 Kelly Tsai.mp4")

    assert find_export_media(episode, "full") == media


def test_legacy_full_mp4_still_counts_as_exported(tmp_path):
    """舊集數的完整版叫 full.mp4——不能因為改名就說它沒匯出、叫人重 render 兩小時。"""
    episode = tmp_path / "20260901 蘇予昕"
    legacy = _touch(episode / "highlights" / "exports" / "full.mp4")

    assert find_export_media(episode, "full") == legacy


def test_new_name_wins_over_legacy(tmp_path):
    episode = tmp_path / "20260901 蘇予昕"
    _touch(episode / "highlights" / "exports" / "full.mp4")
    current = _touch(episode / "highlights" / "exports" / "20260901 蘇予昕.mp4")

    assert find_export_media(episode, "full") == current


def test_highlight_cut_has_no_legacy_fallback(tmp_path):
    episode = tmp_path / "20261021 Kelly Tsai"
    # 一支叫 full.mp4 的檔不代表 punch-L01 匯出過
    _touch(episode / "highlights" / "exports" / "full.mp4")

    assert not is_exported(episode, "punch-L01")
    _touch(episode / "highlights" / "exports" / "punch-L01.mp4")
    assert is_exported(episode, "punch-L01")
