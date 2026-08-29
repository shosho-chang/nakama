from __future__ import annotations

from pathlib import Path

import pytest

from agents.brook.script_video.editorial_master import EditorialMasterContractError
from shared.highlight_materialization import (
    HighlightSource,
    build_materialization_receipt,
    verify_materialization_receipt,
    write_materialization_receipt,
)


class _MediaPoolItem:
    def __init__(self, path: Path) -> None:
        self.path = path

    def GetClipProperty(self, key: str) -> str:
        assert key == "File Path"
        return str(self.path)


class _TimelineItem:
    def __init__(self, path: Path) -> None:
        self.media = _MediaPoolItem(path)

    def GetMediaPoolItem(self) -> _MediaPoolItem:
        return self.media


class _Timeline:
    def __init__(
        self,
        master: Path,
        *,
        video: Path | None = None,
        audio: Path | None = None,
    ) -> None:
        self.video = [] if video is None else [_TimelineItem(video)]
        self.audio = [] if audio is None else [_TimelineItem(audio)]
        if video == master:
            self.video = [_TimelineItem(master)]
        if audio == master:
            self.audio = [_TimelineItem(master)]

    def GetName(self) -> str:
        return "長1 - verified"

    def GetUniqueId(self) -> str:
        return "timeline-uid"

    def GetItemListInTrack(self, track_type: str, index: int) -> list[_TimelineItem]:
        assert index == 1
        return self.video if track_type == "video" else self.audio


def _fixture(tmp_path: Path) -> tuple[Path, HighlightSource, dict[str, object]]:
    root = tmp_path / "episode"
    root.mkdir()
    media = root / "editorial-master" / "v1" / "master.mp4"
    srt = media.with_name("master.srt")
    media.parent.mkdir(parents=True)
    media.write_bytes(b"master")
    srt.write_text("master", encoding="utf-8")
    lineage = {
        "contract": "podcast-editorial-master-v1",
        "episode_id": root.name,
        "content_hash": "a" * 64,
        "master_media_sha256": "b" * 64,
        "master_srt_sha256": "c" * 64,
        "editorial_master_receipt": "editorial-master/v1/EDITORIAL-MASTER.json",
    }
    source = HighlightSource(srt_path=srt, media_path=media, lineage=lineage)
    source_range: dict[str, object] = {
        "start_sec": 10.0,
        "end_sec": 90.0,
        "start_frame": 300,
        "end_frame": 2700,
    }
    timeline = _Timeline(media, video=media, audio=media)
    write_materialization_receipt(
        root,
        build_materialization_receipt(
            root,
            cut_id="value-L01",
            cut_format="long",
            timeline=timeline,
            source_range=source_range,
            source=source,
        ),
    )
    return root, source, source_range


def test_expected_format_and_source_range_fail_closed(tmp_path: Path) -> None:
    root, source, source_range = _fixture(tmp_path)
    with pytest.raises(EditorialMasterContractError, match="format drift"):
        verify_materialization_receipt(
            root,
            "value-L01",
            source=source,
            expected_format="short",
            expected_source_range=source_range,
        )
    wrong_range = dict(source_range)
    wrong_range["start_sec"] = 11.0
    with pytest.raises(EditorialMasterContractError, match="source range drift"):
        verify_materialization_receipt(
            root,
            "value-L01",
            source=source,
            expected_format="long",
            expected_source_range=wrong_range,
        )


@pytest.mark.parametrize(("track", "message"), [("video", "video"), ("audio", "audio")])
def test_live_raw_av_item_is_rejected(tmp_path: Path, track: str, message: str) -> None:
    root, source, source_range = _fixture(tmp_path)
    raw = root / "Default_raw.mp4"
    raw.write_bytes(b"raw")
    media = source.media_path
    timeline = _Timeline(
        media,
        video=raw if track == "video" else media,
        audio=raw if track == "audio" else media,
    )
    with pytest.raises(EditorialMasterContractError, match=rf"{message} track 1 item"):
        verify_materialization_receipt(
            root,
            "value-L01",
            source=source,
            timeline=timeline,
            expected_format="long",
            expected_source_range=source_range,
        )


def test_live_empty_audio_track_is_rejected(tmp_path: Path) -> None:
    root, source, source_range = _fixture(tmp_path)
    timeline = _Timeline(source.media_path, video=source.media_path, audio=None)
    with pytest.raises(EditorialMasterContractError, match="audio track 1 is empty"):
        verify_materialization_receipt(
            root,
            "value-L01",
            source=source,
            timeline=timeline,
            expected_format="long",
            expected_source_range=source_range,
        )
