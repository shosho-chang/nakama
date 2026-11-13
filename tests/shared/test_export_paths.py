"""成品檔名與落點：完整版用集數名、放 final/（修修 2026-10-26、2026-11-13），精華用 cut_id。"""

from __future__ import annotations

from pathlib import Path

from shared.export_paths import (
    episode_dir_of_export,
    export_audio_path,
    export_media_path,
    export_stem,
    export_subtitle_path,
    find_export_media,
    is_exported,
)


def test_full_cut_is_named_after_the_episode(tmp_path):
    episode = tmp_path / "20261021 Kelly Tsai"

    assert export_stem(episode, "full") == "20261021 Kelly Tsai"
    assert export_media_path(episode, "full") == episode / "final" / "20261021 Kelly Tsai.mp4"
    assert export_subtitle_path(episode, "full") == episode / "final" / "20261021 Kelly Tsai.srt"
    assert export_audio_path(episode) == episode / "final" / "20261021 Kelly Tsai.mp3"


def test_highlight_cuts_keep_their_cut_id(tmp_path):
    episode = tmp_path / "20261021 Kelly Tsai"

    assert export_stem(episode, "punch-L01") == "punch-L01"
    assert export_media_path(episode, "punch-S03") == (
        episode / "highlights" / "exports" / "punch-S03.mp4"
    )


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"mp4")
    return path


def test_full_cut_exported_under_the_episode_name(tmp_path):
    episode = tmp_path / "20261021 Kelly Tsai"
    assert not is_exported(episode, "full")

    media = _touch(episode / "final" / "20261021 Kelly Tsai.mp4")

    assert find_export_media(episode, "full") == media


def test_full_cut_exported_before_final_still_counts(tmp_path):
    """2026-11-13 以前的集數，完整版在 highlights/exports/<集數名>.mp4，不搬、照樣算匯出過。"""
    episode = tmp_path / "20261021 Kelly Tsai"
    legacy = _touch(episode / "highlights" / "exports" / "20261021 Kelly Tsai.mp4")

    assert find_export_media(episode, "full") == legacy


def test_legacy_full_mp4_still_counts_as_exported(tmp_path):
    """舊集數的完整版叫 full.mp4——不能因為改名就說它沒匯出、叫人重 render 兩小時。"""
    episode = tmp_path / "20260901 蘇予昕"
    legacy = _touch(episode / "highlights" / "exports" / "full.mp4")

    assert find_export_media(episode, "full") == legacy


def test_new_name_wins_over_legacy(tmp_path):
    episode = tmp_path / "20260901 蘇予昕"
    _touch(episode / "highlights" / "exports" / "full.mp4")
    _touch(episode / "highlights" / "exports" / "20260901 蘇予昕.mp4")
    current = _touch(episode / "final" / "20260901 蘇予昕.mp4")

    assert find_export_media(episode, "full") == current


def test_highlight_cut_has_no_legacy_fallback(tmp_path):
    episode = tmp_path / "20261021 Kelly Tsai"
    # 一支叫 full.mp4 的檔不代表 punch-L01 匯出過
    _touch(episode / "highlights" / "exports" / "full.mp4")

    assert not is_exported(episode, "punch-L01")
    _touch(episode / "highlights" / "exports" / "punch-L01.mp4")
    assert is_exported(episode, "punch-L01")


def test_episode_dir_is_recovered_from_either_output_folder(tmp_path):
    episode = tmp_path / "20261111 李惠貞"

    assert episode_dir_of_export(episode / "final" / "20261111 李惠貞.mp4") == episode
    assert episode_dir_of_export(episode / "highlights" / "exports" / "punch-L01.mp4") == episode
