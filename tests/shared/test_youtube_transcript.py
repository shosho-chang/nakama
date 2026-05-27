"""Tests for shared.youtube_transcript — YouTube transcript fetcher.

URL/ID parsing 跟 text formatting 走真實函式測試（純 stdlib，無 IO）。
``fetch_transcript`` 的 HTTP path 用 monkeypatch 攔截 ``youtube_transcript_api``
模組，避免實際打 YouTube。
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from shared import youtube_transcript as yt
from shared.youtube_transcript import (
    TranscriptSegment,
    YouTubeTranscriptError,
    fetch_transcript,
    parse_video_id,
    to_plain_text,
    to_timestamped_text,
    total_duration,
)

# ---------------------------------------------------------------------------
# parse_video_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=10", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),  # raw id
    ],
)
def test_parse_video_id_accepts_various_forms(url, expected):
    assert parse_video_id(url) == expected


def test_parse_video_id_empty_raises():
    with pytest.raises(YouTubeTranscriptError, match="不能為空"):
        parse_video_id("   ")


def test_parse_video_id_bad_string_raises():
    with pytest.raises(YouTubeTranscriptError, match="無法從輸入辨識"):
        parse_video_id("https://example.com/whatever")


def test_parse_video_id_wrong_length_raises():
    with pytest.raises(YouTubeTranscriptError, match="無法從輸入辨識"):
        parse_video_id("tooShort")  # 8 chars


# ---------------------------------------------------------------------------
# text formatting
# ---------------------------------------------------------------------------


def _segs() -> list[TranscriptSegment]:
    return [
        TranscriptSegment(text="hello world", start=0.0, duration=2.0),
        TranscriptSegment(text="[音樂]", start=2.5, duration=3.0),
        TranscriptSegment(text="這是一段中文", start=6.0, duration=4.0),
        TranscriptSegment(text="", start=10.5, duration=1.0),
        TranscriptSegment(text="final line", start=72.0, duration=3.0),
    ]


def test_to_plain_text_strips_music_and_empty():
    out = to_plain_text(_segs())
    assert "hello world" in out
    assert "[音樂]" not in out
    assert "這是一段中文" in out
    assert "final line" in out
    # all on one line
    assert "\n" not in out


def test_to_timestamped_text_formats_minutes_seconds():
    out = to_timestamped_text(_segs())
    lines = out.split("\n")
    assert lines[0].startswith("[00:00]")
    assert "hello world" in lines[0]
    # second segment is [音樂] — but timestamped path preserves text as-is
    # (timestamp path does NOT strip music markers, only plain text does)
    assert any(line.startswith("[01:12]") and "final line" in line for line in lines)


def test_to_timestamped_text_formats_hours_for_long_videos():
    long = [TranscriptSegment(text="late", start=3725.0, duration=2.0)]  # 1:02:05
    out = to_timestamped_text(long)
    assert "[1:02:05]" in out


def test_total_duration_uses_last_segment():
    assert total_duration(_segs()) == 75.0  # 72 + 3


def test_total_duration_empty():
    assert total_duration([]) == 0.0


# ---------------------------------------------------------------------------
# fetch_transcript — mock youtube_transcript_api module
# ---------------------------------------------------------------------------


def _install_fake_yta(monkeypatch, *, get_transcript_return=None, get_raise=None):
    """Inject a fake ``youtube_transcript_api`` module so ``fetch_transcript`` can import it."""
    fake = types.ModuleType("youtube_transcript_api")

    class _FakeApi:
        @staticmethod
        def get_transcript(video_id, languages=None):
            if get_raise:
                raise get_raise
            return get_transcript_return or []

        @staticmethod
        def list_transcripts(video_id):
            raise RuntimeError("not used in these tests")

    fake.YouTubeTranscriptApi = _FakeApi
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake)


def test_fetch_transcript_happy_path(monkeypatch):
    _install_fake_yta(
        monkeypatch,
        get_transcript_return=[
            {"text": "hi", "start": 0.0, "duration": 1.5},
            {"text": "again", "start": 1.5, "duration": 2.0},
        ],
    )
    segments = fetch_transcript("https://youtu.be/dQw4w9WgXcQ")
    assert len(segments) == 2
    assert segments[0].text == "hi"
    assert segments[0].start == 0.0
    assert segments[0].duration == 1.5


def test_fetch_transcript_falls_back_when_preferred_lang_fails(monkeypatch):
    fake = types.ModuleType("youtube_transcript_api")

    fallback_transcript = MagicMock()
    fallback_transcript.language_code = "ja"
    fallback_transcript.fetch.return_value = [
        {"text": "こんにちは", "start": 0.0, "duration": 1.0}
    ]

    class _FakeApi:
        @staticmethod
        def get_transcript(video_id, languages=None):
            raise RuntimeError("preferred langs not available")

        @staticmethod
        def list_transcripts(video_id):
            return iter([fallback_transcript])

    fake.YouTubeTranscriptApi = _FakeApi
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake)

    segments = fetch_transcript("dQw4w9WgXcQ")
    assert len(segments) == 1
    assert segments[0].text == "こんにちは"


def test_fetch_transcript_total_failure_raises(monkeypatch):
    fake = types.ModuleType("youtube_transcript_api")

    class _FakeApi:
        @staticmethod
        def get_transcript(video_id, languages=None):
            raise RuntimeError("disabled")

        @staticmethod
        def list_transcripts(video_id):
            raise RuntimeError("none available")

    fake.YouTubeTranscriptApi = _FakeApi
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake)

    with pytest.raises(YouTubeTranscriptError, match="無法取得"):
        fetch_transcript("dQw4w9WgXcQ")


def test_fetch_transcript_missing_dependency_raises(monkeypatch):
    # Force ImportError by removing module + blocking re-import
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", None)
    with pytest.raises(YouTubeTranscriptError, match="缺少依賴"):
        fetch_transcript("dQw4w9WgXcQ")


def test_fetch_transcript_propagates_bad_url(monkeypatch):
    # parse_video_id raises before yta is touched
    with pytest.raises(YouTubeTranscriptError, match="無法從輸入辨識"):
        fetch_transcript("not a youtube url")


# Reference test — ensure module exposes the things we expect
def test_module_surface():
    assert hasattr(yt, "fetch_transcript")
    assert hasattr(yt, "parse_video_id")
    assert hasattr(yt, "to_plain_text")
    assert hasattr(yt, "to_timestamped_text")
    assert hasattr(yt, "total_duration")
    assert hasattr(yt, "TranscriptSegment")
    assert hasattr(yt, "YouTubeTranscriptError")
