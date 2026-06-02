"""Tests for ``shared.youtube_ingest`` — the yt-dlp wrapper used by ADR-035
PR1c-i Robin Watchlist ingestion route. ``yt-dlp`` is monkey-patched at the
``_run_yt_dlp`` subprocess seam so no network is hit.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from shared import youtube_ingest

# ---------------------------------------------------------------------------
# extract_video_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("http://youtube.com/watch?v=dQw4w9WgXcQ&t=42", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=10", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube-nocookie.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),  # bare id
        ("  dQw4w9WgXcQ  ", "dQw4w9WgXcQ"),  # whitespace
    ],
)
def test_extract_video_id_happy_cases(url, expected):
    assert youtube_ingest.extract_video_id(url) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "https://example.com/watch?v=dQw4w9WgXcQ",  # not a YT host
        "not a url",
        "https://www.youtube.com/playlist?list=PLxxx",  # no v=
        "abc",  # too short for bare id
    ],
)
def test_extract_video_id_rejects(bad):
    with pytest.raises(youtube_ingest.InvalidYouTubeURL):
        youtube_ingest.extract_video_id(bad)


# ---------------------------------------------------------------------------
# fetch_metadata
# ---------------------------------------------------------------------------


def _make_completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_fetch_metadata_parses_yt_dlp_json(monkeypatch):
    payload = {
        "id": "dQw4w9WgXcQ",
        "title": "Test Title",
        "channel": "Channel X",
        "duration": 1234,
        "automatic_captions": {"en": [{}], "zh-Hant": [{}], "fr": [{}]},
    }
    monkeypatch.setattr(
        youtube_ingest,
        "_run_yt_dlp",
        lambda args, timeout=90: _make_completed(0, stdout=json.dumps(payload)),
    )
    meta = youtube_ingest.fetch_metadata("https://youtu.be/dQw4w9WgXcQ")
    assert meta.video_id == "dQw4w9WgXcQ"
    assert meta.title == "Test Title"
    assert meta.channel == "Channel X"
    assert meta.duration_s == 1234
    assert meta.url == "https://youtube.com/watch?v=dQw4w9WgXcQ"
    assert "en" in meta.available_auto_captions
    assert "zh-Hant" in meta.available_auto_captions


def test_fetch_metadata_falls_back_to_uploader(monkeypatch):
    payload = {"title": "T", "uploader": "U", "duration": 0, "automatic_captions": {}}
    stdout = json.dumps(payload)
    monkeypatch.setattr(
        youtube_ingest, "_run_yt_dlp", lambda *a, **k: _make_completed(0, stdout=stdout)
    )
    meta = youtube_ingest.fetch_metadata("https://youtu.be/dQw4w9WgXcQ")
    assert meta.channel == "U"


def test_fetch_metadata_invalid_url_raises():
    with pytest.raises(youtube_ingest.InvalidYouTubeURL):
        youtube_ingest.fetch_metadata("https://example.com")


def test_fetch_metadata_subprocess_failure(monkeypatch):
    monkeypatch.setattr(
        youtube_ingest, "_run_yt_dlp", lambda *a, **k: _make_completed(1, stderr="ERROR: Private")
    )
    with pytest.raises(youtube_ingest.YtDlpError) as exc:
        youtube_ingest.fetch_metadata("https://youtu.be/dQw4w9WgXcQ")
    assert "Private" in exc.value.stderr


def test_fetch_metadata_non_json_stdout(monkeypatch):
    monkeypatch.setattr(
        youtube_ingest, "_run_yt_dlp", lambda *a, **k: _make_completed(0, stdout="not json")
    )
    with pytest.raises(youtube_ingest.YtDlpError):
        youtube_ingest.fetch_metadata("https://youtu.be/dQw4w9WgXcQ")


# ---------------------------------------------------------------------------
# fetch_caption
# ---------------------------------------------------------------------------


def test_fetch_caption_picks_en_first(monkeypatch, tmp_path: Path):
    out = tmp_path / "stage"

    def fake_run(args, timeout=90):
        # Simulate yt-dlp writing both en and zh-Hant.
        out.mkdir(parents=True, exist_ok=True)
        (out / "dQw4w9WgXcQ.en.vtt").write_text("WEBVTT\n", encoding="utf-8")
        (out / "dQw4w9WgXcQ.zh-Hant.vtt").write_text("WEBVTT\n", encoding="utf-8")
        return _make_completed(0)

    monkeypatch.setattr(youtube_ingest, "_run_yt_dlp", fake_run)
    path, lang = youtube_ingest.fetch_caption("dQw4w9WgXcQ", out)
    assert lang == "en"
    assert path.name == "dQw4w9WgXcQ.en.vtt"


def test_fetch_caption_returns_actual_variant_tag(monkeypatch, tmp_path: Path):
    """PR #771 code-review fix: when yt-dlp emits ``en-orig`` (re-upload
    auto-caption), the returned lang must be the actual tag, not the
    priority constant — so manifest.primary_lang matches the on-disk
    track name verbatim."""
    out = tmp_path / "stage"

    def fake_run(args, timeout=90):
        out.mkdir(parents=True, exist_ok=True)
        (out / "dQw4w9WgXcQ.en-orig.vtt").write_text("WEBVTT\n", encoding="utf-8")
        return _make_completed(0)

    monkeypatch.setattr(youtube_ingest, "_run_yt_dlp", fake_run)
    path, lang = youtube_ingest.fetch_caption("dQw4w9WgXcQ", out)
    assert lang == "en-orig"
    assert path.name == "dQw4w9WgXcQ.en-orig.vtt"


def test_fetch_caption_falls_back_to_zh_hant(monkeypatch, tmp_path: Path):
    out = tmp_path / "stage"

    def fake_run(args, timeout=90):
        out.mkdir(parents=True, exist_ok=True)
        (out / "dQw4w9WgXcQ.zh-Hant.vtt").write_text("WEBVTT\n", encoding="utf-8")
        return _make_completed(0)

    monkeypatch.setattr(youtube_ingest, "_run_yt_dlp", fake_run)
    path, lang = youtube_ingest.fetch_caption("dQw4w9WgXcQ", out)
    assert lang == "zh-Hant"


def test_fetch_caption_no_vtt_raises(monkeypatch, tmp_path: Path):
    out = tmp_path / "stage"

    def fake_run(args, timeout=90):
        out.mkdir(parents=True, exist_ok=True)
        return _make_completed(0)

    monkeypatch.setattr(youtube_ingest, "_run_yt_dlp", fake_run)
    with pytest.raises(youtube_ingest.NoCaptionAvailable):
        youtube_ingest.fetch_caption("dQw4w9WgXcQ", out)


def test_fetch_caption_off_priority_lang_only(monkeypatch, tmp_path: Path):
    """yt-dlp dropped a French caption only — treat as unavailable
    (ADR-035 §D2 only consumes en / zh-Hant / zh-CN)."""
    out = tmp_path / "stage"

    def fake_run(args, timeout=90):
        out.mkdir(parents=True, exist_ok=True)
        (out / "dQw4w9WgXcQ.fr.vtt").write_text("WEBVTT\n", encoding="utf-8")
        return _make_completed(0)

    monkeypatch.setattr(youtube_ingest, "_run_yt_dlp", fake_run)
    with pytest.raises(youtube_ingest.NoCaptionAvailable):
        youtube_ingest.fetch_caption("dQw4w9WgXcQ", out)


def test_fetch_caption_subprocess_failure(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        youtube_ingest, "_run_yt_dlp", lambda *a, **k: _make_completed(1, stderr="net err")
    )
    with pytest.raises(youtube_ingest.YtDlpError):
        youtube_ingest.fetch_caption("dQw4w9WgXcQ", tmp_path)


def test_fetch_caption_subprocess_failure_but_vtt_landed(monkeypatch, tmp_path: Path):
    """yt-dlp can exit non-zero (e.g. 429 on zh-Hant) AND still write an EN
    VTT thanks to --ignore-errors. We must keep the file we got."""
    video_id = "dQw4w9WgXcQ"

    def fake_run(*a, **k):
        (tmp_path / f"{video_id}.en.vtt").write_text("WEBVTT\n\n", encoding="utf-8")
        return _make_completed(
            1,
            stderr="ERROR: Unable to download video subtitles for 'zh-Hant': HTTP Error 429",
        )

    monkeypatch.setattr(youtube_ingest, "_run_yt_dlp", fake_run)
    path, lang = youtube_ingest.fetch_caption(video_id, tmp_path)
    assert lang == "en"
    assert path.name == f"{video_id}.en.vtt"


def test_fetch_caption_rejects_unsafe_video_id(tmp_path: Path):
    with pytest.raises(youtube_ingest.InvalidYouTubeURL):
        youtube_ingest.fetch_caption("../etc/passwd", tmp_path)
