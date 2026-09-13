r"""跨集共用的 BGM／SFX 素材庫（修修 2026-09-10 盤點出來的斷點）。

以前 `assets/bgm`、`assets/sfx` 全是 per-episode：BGM 要從 `E:\data\music` 手動
轉檔複製，那五個 SFX 只存在於 20260723 謝伯讓 那一集，每集手抄。
"""

from __future__ import annotations

import subprocess

import pytest

from shared.asset_library import (
    AssetLibraryError,
    ensure_asset,
    find_in_library,
    library_root,
)


def _wav(path, *, seconds: float = 0.2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            str(seconds),
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def library(monkeypatch, tmp_path):
    root = tmp_path / "library"
    monkeypatch.setenv("NAKAMA_ASSET_LIBRARY", str(root))
    _wav(root / "sfx" / "ding.wav")
    _wav(root / "music" / "short-punch" / "slow-edges-mum-child-main-version-48501-01-45.mp3")
    _wav(root / "music" / "focus music" / "deep-work-loop.mp3")
    return root


@pytest.fixture
def episode(tmp_path):
    path = tmp_path / "20260901 蘇予昕"
    path.mkdir()
    return path


def test_library_root_is_overridable(library):
    assert library_root() == library


def test_a_prefix_matches_the_envato_serial_suffix(library):
    """庫裡的檔名帶 Envato 流水號尾巴，修修講的是前面那段。"""
    hit = find_in_library("slow-edges-mum-child", "bgm", "punch")
    assert hit is not None
    assert hit.name.startswith("slow-edges-mum-child-main-version")


def test_an_exact_name_beats_a_prefix(library):
    _wav(library / "music" / "short-punch" / "calm.mp3")
    _wav(library / "music" / "short-punch" / "calm-extended-version.mp3")
    assert find_in_library("calm", "bgm", "punch").stem == "calm"


def test_other_families_are_still_searched(library):
    """修修常常直接講曲名，不會先說它屬於哪一個 miner。"""
    assert find_in_library("deep-work-loop", "bgm", "punch") is not None


def test_mp3_is_transcoded_into_the_episode_not_back_into_the_library(library, episode):
    got = ensure_asset(episode, "slow-edges-mum-child", kind="bgm", family="punch")
    assert got == episode / "assets" / "bgm" / "slow-edges-mum-child.wav"
    assert got.is_file() and got.stat().st_size > 0
    # 庫是唯讀的來源，不是暫存區。
    assert not list((library / "music" / "short-punch").glob("*.wav"))


def test_the_second_call_reuses_the_transcode(library, episode):
    first = ensure_asset(episode, "slow-edges-mum-child", kind="bgm", family="punch")
    stamp = first.stat().st_mtime_ns
    again = ensure_asset(episode, "slow-edges-mum-child", kind="bgm", family="punch")
    assert again == first
    assert again.stat().st_mtime_ns == stamp


def test_a_wav_in_the_library_is_copied_verbatim(library, episode):
    got = ensure_asset(episode, "ding", kind="sfx")
    assert got.read_bytes() == (library / "sfx" / "ding.wav").read_bytes()


def test_the_episode_copy_always_wins(library, episode):
    """某一集想用不一樣的東西，把檔案放進 assets/ 就贏過庫，不必改參數。"""
    local = episode / "assets" / "sfx" / "ding.wav"
    _wav(local, seconds=0.5)
    mine = local.read_bytes()
    assert ensure_asset(episode, "ding", kind="sfx").read_bytes() == mine
    assert mine != (library / "sfx" / "ding.wav").read_bytes()


def test_a_missing_asset_names_both_places_it_looked(library, episode):
    with pytest.raises(AssetLibraryError) as excinfo:
        ensure_asset(episode, "no-such-track", kind="bgm", family="punch")
    message = str(excinfo.value)
    assert "集內沒有" in message
    assert "short-punch" in message


def test_a_library_that_does_not_exist_fails_loud(monkeypatch, episode, tmp_path):
    monkeypatch.setenv("NAKAMA_ASSET_LIBRARY", str(tmp_path / "nowhere"))
    with pytest.raises(AssetLibraryError, match="庫的目錄都不存在"):
        ensure_asset(episode, "ding", kind="sfx")
