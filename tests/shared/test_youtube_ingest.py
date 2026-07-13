"""Tests for ``shared.youtube_ingest`` — the yt-dlp wrapper used by ADR-035
PR1c-i Robin Watchlist ingestion route.

Seams:
- ``_run_yt_dlp`` is monkey-patched at the subprocess seam for caption tests.
- ``urllib.request.urlopen`` is monkey-patched for metadata (YouTube Data API).
No network is hit in any test.
"""

from __future__ import annotations

import io
import json
import subprocess
import urllib.error
import urllib.request
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
# _run_yt_dlp — base invocation contract
# ---------------------------------------------------------------------------


def test_run_yt_dlp_injects_js_runtimes_node(monkeypatch, tmp_path):
    """yt-dlp ≥2025.x defaults to Deno only; we must inject --js-runtimes node
    so Node.js (always present in this environment) is used instead.
    The runtime name is 'node', not 'nodejs' (yt-dlp rejects the latter).
    fetch_caption is used to trigger _run_yt_dlp (fetch_metadata now uses
    the YouTube Data API and no longer calls yt-dlp)."""
    captured: list[list[str]] = []
    monkeypatch.setattr(youtube_ingest.subprocess, "run", _fake_subprocess_run(captured))
    try:
        youtube_ingest.fetch_caption("dQw4w9WgXcQ", tmp_path / "stage")
    except Exception:
        pass
    assert captured, "subprocess.run was never called"
    cmd = captured[0]
    assert "--js-runtimes" in cmd
    assert cmd[cmd.index("--js-runtimes") + 1] == "node"
    assert "--extractor-args" not in cmd


def _fake_subprocess_run(captured: list[list[str]], returncode: int = 0):
    """Return a fake subprocess.run that records the cmd and returns a dummy result."""

    def _run(cmd, **_kwargs):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=returncode, stdout="", stderr="")

    return _run


def test_run_yt_dlp_uses_throwaway_cookie_copy(monkeypatch, tmp_path):
    """yt-dlp rewrites (rotates) the cookie jar back to the file it is handed
    on every run. We must hand it a disposable temp copy — never the user's
    exported file — so repeated calls don't degrade the source until YouTube
    rejects it as "not a bot". Verifies: (1) --cookies is injected, (2) the
    path passed is NOT the user's file, (3) the copy carries the original
    content, (4) the user's file survives yt-dlp's mutation untouched."""
    cookies_file = tmp_path / "cookies.txt"
    original = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n"
    cookies_file.write_text(original)
    captured: list[list[str]] = []
    copy_contents: list[str] = []

    def _run(cmd, **_kwargs):
        captured.append(list(cmd))
        # Read the handed-over file, then simulate yt-dlp mutating it to
        # prove the original is shielded by the copy.
        handed = Path(cmd[cmd.index("--cookies") + 1])
        copy_contents.append(handed.read_text())
        handed.write_text("# MUTATED BY yt-dlp\n")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setenv("YTDLP_COOKIES_PATH", str(cookies_file))
    monkeypatch.setattr(youtube_ingest.subprocess, "run", _run)
    try:
        youtube_ingest.fetch_caption("dQw4w9WgXcQ", tmp_path / "stage")
    except Exception:
        pass
    assert captured
    cmd = captured[0]
    assert "--cookies" in cmd
    handed_path = cmd[cmd.index("--cookies") + 1]
    assert handed_path != str(cookies_file)  # disposable copy, not the source
    assert copy_contents == [original]  # copy carried the real cookies
    assert cookies_file.read_text() == original  # source survived the mutation


def test_run_yt_dlp_no_cookies_when_env_unset(monkeypatch, tmp_path):
    """When YTDLP_COOKIES_PATH is not set, --cookies must NOT appear."""
    captured: list[list[str]] = []
    monkeypatch.delenv("YTDLP_COOKIES_PATH", raising=False)
    monkeypatch.setattr(youtube_ingest.subprocess, "run", _fake_subprocess_run(captured))
    try:
        youtube_ingest.fetch_caption("dQw4w9WgXcQ", tmp_path / "stage")
    except Exception:
        pass
    assert captured
    assert "--cookies" not in captured[0]


def test_run_yt_dlp_no_cookies_when_file_missing(monkeypatch, tmp_path):
    """When YTDLP_COOKIES_PATH is set but the file doesn't exist, --cookies
    is silently skipped rather than crashing."""
    captured: list[list[str]] = []
    monkeypatch.setenv("YTDLP_COOKIES_PATH", "/nonexistent/cookies.txt")
    monkeypatch.setattr(youtube_ingest.subprocess, "run", _fake_subprocess_run(captured))
    try:
        youtube_ingest.fetch_caption("dQw4w9WgXcQ", tmp_path / "stage")
    except Exception:
        pass
    assert captured
    assert "--cookies" not in captured[0]


def test_run_yt_dlp_injects_proxy_when_env_set(monkeypatch, tmp_path):
    """When YTDLP_PROXY is set, --proxy <url> is injected so the YouTube
    request egresses through a residential IP (the VPS datacenter IP is
    bot-detected). When unset, --proxy must NOT appear."""
    captured: list[list[str]] = []
    monkeypatch.setenv("YTDLP_PROXY", "socks5://127.0.0.1:1080")
    monkeypatch.delenv("YTDLP_COOKIES_PATH", raising=False)
    monkeypatch.setattr(youtube_ingest.subprocess, "run", _fake_subprocess_run(captured))
    try:
        youtube_ingest.fetch_caption("dQw4w9WgXcQ", tmp_path / "stage")
    except Exception:
        pass
    assert captured
    cmd = captured[0]
    assert "--proxy" in cmd
    assert cmd[cmd.index("--proxy") + 1] == "socks5://127.0.0.1:1080"


def test_run_yt_dlp_no_proxy_when_env_unset(monkeypatch, tmp_path):
    """When YTDLP_PROXY is not set, --proxy must NOT appear."""
    captured: list[list[str]] = []
    monkeypatch.delenv("YTDLP_PROXY", raising=False)
    monkeypatch.delenv("YTDLP_COOKIES_PATH", raising=False)
    monkeypatch.setattr(youtube_ingest.subprocess, "run", _fake_subprocess_run(captured))
    try:
        youtube_ingest.fetch_caption("dQw4w9WgXcQ", tmp_path / "stage")
    except Exception:
        pass
    assert captured
    assert "--proxy" not in captured[0]


# ---------------------------------------------------------------------------
# fetch_metadata — YouTube Data API v3
# ---------------------------------------------------------------------------


def _make_completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _mock_urlopen(payload: dict):
    """Return a context-manager fake for urllib.request.urlopen."""

    class _FakeResp:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    return lambda url, timeout=15: _FakeResp()


_API_ITEM = {
    "snippet": {"title": "Test Title", "channelTitle": "Channel X"},
    "contentDetails": {"duration": "PT20M34S"},
}


def test_fetch_metadata_parses_api_response(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")
    monkeypatch.setattr(urllib.request, "urlopen", _mock_urlopen({"items": [_API_ITEM]}))
    meta = youtube_ingest.fetch_metadata("https://youtu.be/dQw4w9WgXcQ")
    assert meta.video_id == "dQw4w9WgXcQ"
    assert meta.title == "Test Title"
    assert meta.channel == "Channel X"
    assert meta.duration_s == 20 * 60 + 34
    assert meta.url == "https://youtube.com/watch?v=dQw4w9WgXcQ"
    assert meta.available_auto_captions == []


def test_fetch_metadata_invalid_url_raises():
    with pytest.raises(youtube_ingest.InvalidYouTubeURL):
        youtube_ingest.fetch_metadata("https://example.com")


def test_fetch_metadata_no_api_key_raises(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(youtube_ingest.YtDlpError) as exc:
        youtube_ingest.fetch_metadata("https://youtu.be/dQw4w9WgXcQ")
    assert "YOUTUBE_API_KEY" in str(exc.value)


def test_fetch_metadata_empty_items_raises(monkeypatch):
    """Private / deleted video → API returns items=[] → YtDlpError."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")
    monkeypatch.setattr(urllib.request, "urlopen", _mock_urlopen({"items": []}))
    with pytest.raises(youtube_ingest.YtDlpError):
        youtube_ingest.fetch_metadata("https://youtu.be/dQw4w9WgXcQ")


def test_fetch_metadata_http_error_raises(monkeypatch):
    """API returning HTTP 403 (bad key / quota) → YtDlpError with status."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "bad-key")

    def _raise(url, timeout=15):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b"quota exceeded"))

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    with pytest.raises(youtube_ingest.YtDlpError) as exc:
        youtube_ingest.fetch_metadata("https://youtu.be/dQw4w9WgXcQ")
    assert "403" in str(exc.value)


def test_iso8601_duration_to_seconds():
    f = youtube_ingest._iso8601_duration_to_seconds
    assert f("PT3M33S") == 213
    assert f("PT1H2M3S") == 3723
    assert f("P1DT0H0M0S") == 86400
    assert f("PT0S") == 0
    assert f("") == 0
    assert f("garbage") == 0


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
