"""一集的「乾淨逐字稿」是哪一份——衍生產物不可以各自挑。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.episode_transcript import (
    EpisodeTranscriptError,
    TranscriptSource,
    resolve_transcript_srt,
)


def _legacy_episode(tmp_path: Path) -> Path:
    (tmp_path / "transcript.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n測試\n", encoding="utf-8"
    )
    return tmp_path


def test_legacy_episode_keeps_transcript_srt(tmp_path):
    """ADR-064 之前的集數行為不變。"""
    source = resolve_transcript_srt(_legacy_episode(tmp_path))
    assert source.origin == "legacy_transcript_srt"
    assert source.srt_path.name == "transcript.srt"
    assert source.lineage is None


def test_editorial_master_wins_over_legacy_file(tmp_path, monkeypatch):
    """兩份都在時用 Editorial Master——舊的那份含有已經剪掉的內容。"""
    _legacy_episode(tmp_path)
    receipt = tmp_path / "editorial-master" / "v1" / "EDITORIAL-MASTER.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"contract": "podcast-editorial-master-v1"}), encoding="utf-8")
    master_srt = receipt.parent / "master.srt"
    master_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n成品\n", encoding="utf-8")

    class _Selection:
        srt_path = master_srt

        def identity(self):
            return {"episode_id": "ep", "content_hash": "abc"}

    import agents.brook.script_video.editorial_master as em

    monkeypatch.setattr(
        em,
        "EditorialMasterRequest",
        lambda *a, **k: type("R", (), {"open": lambda _s: _Selection()})(),
    )
    source = resolve_transcript_srt(tmp_path)
    assert source.origin == "editorial_master"
    assert source.srt_path == master_srt
    assert source.lineage == {"episode_id": "ep", "content_hash": "abc"}


def test_broken_editorial_master_does_not_fall_back(tmp_path, monkeypatch):
    """驗不過**不可以**退回舊檔——那是在契約壞掉時偷偷改用含已剪內容的稿子。"""
    _legacy_episode(tmp_path)
    receipt = tmp_path / "editorial-master" / "v1" / "EDITORIAL-MASTER.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}", encoding="utf-8")

    import agents.brook.script_video.editorial_master as em

    def _boom(*_a, **_k):
        raise em.EditorialMasterContractError("content hash 不符")

    monkeypatch.setattr(em, "EditorialMasterRequest", _boom)
    with pytest.raises(EpisodeTranscriptError, match="不退回"):
        resolve_transcript_srt(tmp_path)


def test_no_source_at_all_is_an_error(tmp_path):
    with pytest.raises(EpisodeTranscriptError, match="既沒有 Editorial Master"):
        resolve_transcript_srt(tmp_path)


def test_source_is_frozen():
    """來源要能原樣寫進收據，不可被下游改掉。"""
    src = TranscriptSource(srt_path=Path("a.srt"), origin="legacy_transcript_srt")
    with pytest.raises(Exception):
        src.origin = "editorial_master"  # type: ignore[misc]
