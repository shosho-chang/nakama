"""Tests for ADR-035 PR1c-i — Robin Watchlist ingestion routes.

Covers ``POST /robin/watchlist/add`` (yt-dlp metadata + caption staging) and
``POST /robin/watchlist/add/confirm`` (manifest + transcript.vtt write).
``yt-dlp`` is monkey-patched at the ``shared.youtube_ingest`` boundary so
no network is hit.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    """Per-test vault root with ``Watchlist/youtube/`` pre-created."""
    v = tmp_path / "vault"
    (v / "Watchlist" / "youtube").mkdir(parents=True)
    monkeypatch.setenv("VAULT_PATH", str(v))
    return v


@pytest.fixture
def app_client(vault, monkeypatch):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(robin_module)
    importlib.reload(app_module)

    return TestClient(app_module.app, follow_redirects=False), robin_module


# ---------------------------------------------------------------------------
# GET /robin/watchlist/add — URL paste form
# ---------------------------------------------------------------------------


def test_get_watchlist_add_renders_form(app_client):
    tc, _ = app_client
    r = tc.get("/robin/watchlist/add")
    assert r.status_code == 200
    assert "加入 Watchlist" in r.text
    assert 'name="url"' in r.text


# ---------------------------------------------------------------------------
# POST /robin/watchlist/add — happy path (mocked yt-dlp)
# ---------------------------------------------------------------------------


def _fake_metadata():
    from shared.youtube_ingest import YouTubeMetadata

    return YouTubeMetadata(
        video_id="dQw4w9WgXcQ",
        title="Test Video",
        channel="Test Channel",
        duration_s=300,
        url="https://youtube.com/watch?v=dQw4w9WgXcQ",
        available_auto_captions=["en"],
    )


def test_post_watchlist_add_happy_path(app_client, vault, monkeypatch):
    tc, robin_module = app_client

    def fake_fetch_metadata(url):
        assert "dQw4w9WgXcQ" in url
        return _fake_metadata()

    def fake_fetch_caption(video_id, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)
        vtt = output_dir / "transcript.vtt"
        vtt.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:03.000\nhello\n", encoding="utf-8")
        return vtt, "en"

    monkeypatch.setattr(robin_module, "fetch_metadata", fake_fetch_metadata)
    monkeypatch.setattr(robin_module, "fetch_caption", fake_fetch_caption)

    r = tc.post(
        "/robin/watchlist/add",
        data={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert r.status_code == 200, r.text
    assert "Test Video" in r.text
    assert "Test Channel" in r.text
    assert "dQw4w9WgXcQ" in r.text
    # Session cookie issued so confirm step can lookup staging.
    assert "robin_watchlist_session" in r.cookies


# ---------------------------------------------------------------------------
# POST /robin/watchlist/add — invalid URL → 400
# ---------------------------------------------------------------------------


def test_post_watchlist_add_invalid_url(app_client, monkeypatch):
    tc, robin_module = app_client

    def fake_fetch_metadata(url):
        from shared.youtube_ingest import InvalidYouTubeURL

        raise InvalidYouTubeURL("could not extract")

    monkeypatch.setattr(robin_module, "fetch_metadata", fake_fetch_metadata)

    r = tc.post("/robin/watchlist/add", data={"url": "https://example.com/not-yt"})
    assert r.status_code == 400
    assert "video id" in r.text or "URL" in r.text


# ---------------------------------------------------------------------------
# POST /robin/watchlist/add — no caption → 400
# ---------------------------------------------------------------------------


def test_post_watchlist_add_no_caption(app_client, monkeypatch):
    tc, robin_module = app_client

    monkeypatch.setattr(robin_module, "fetch_metadata", lambda url: _fake_metadata())

    def fake_fetch_caption(video_id, output_dir):
        from shared.youtube_ingest import NoCaptionAvailable

        raise NoCaptionAvailable("no captions")

    monkeypatch.setattr(robin_module, "fetch_caption", fake_fetch_caption)

    r = tc.post(
        "/robin/watchlist/add",
        data={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert r.status_code == 400
    assert "caption" in r.text.lower() or "字幕" in r.text


# ---------------------------------------------------------------------------
# POST /robin/watchlist/add — yt-dlp error → 400
# ---------------------------------------------------------------------------


def test_post_watchlist_add_ytdlp_error(app_client, monkeypatch):
    tc, robin_module = app_client

    def fake_fetch_metadata(url):
        from shared.youtube_ingest import YtDlpError

        raise YtDlpError("private video", stderr="ERROR: Private video")

    monkeypatch.setattr(robin_module, "fetch_metadata", fake_fetch_metadata)

    r = tc.post(
        "/robin/watchlist/add",
        data={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /robin/watchlist/add/confirm — happy path writes manifest + vtt
# ---------------------------------------------------------------------------


def _do_add(tc, robin_module, monkeypatch):
    monkeypatch.setattr(robin_module, "fetch_metadata", lambda url: _fake_metadata())

    def fake_fetch_caption(video_id, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)
        vtt = output_dir / "transcript.vtt"
        vtt.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:03.000\nhello\n", encoding="utf-8")
        return vtt, "en"

    monkeypatch.setattr(robin_module, "fetch_caption", fake_fetch_caption)
    r = tc.post(
        "/robin/watchlist/add",
        data={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert r.status_code == 200, r.text
    return r.cookies["robin_watchlist_session"]


def test_post_watchlist_confirm_writes_manifest_and_vtt(app_client, vault, monkeypatch):
    tc, robin_module = app_client
    sid = _do_add(tc, robin_module, monkeypatch)

    r = tc.post(
        "/robin/watchlist/add/confirm",
        cookies={"robin_watchlist_session": sid},
        data={"cast": ["Andrew Huberman", "Guest A"]},
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/robin/watchlist/dQw4w9WgXcQ"

    entry_dir = vault / "Watchlist" / "youtube" / "dQw4w9WgXcQ"
    manifest = entry_dir / "manifest.json"
    transcript = entry_dir / "transcript.vtt"
    assert manifest.is_file()
    assert transcript.is_file()
    assert "WEBVTT" in transcript.read_text(encoding="utf-8")

    # Validate via the schema — defence-in-depth.
    from shared.schemas.youtube_watchlist import YouTubeWatchlistEntry

    entry = YouTubeWatchlistEntry.model_validate_json(manifest.read_bytes())
    assert entry.video_id == "dQw4w9WgXcQ"
    assert entry.title == "Test Video"
    assert entry.channel == "Test Channel"
    assert entry.duration_s == 300
    assert entry.primary_lang == "en"
    assert entry.cast == ["Andrew Huberman", "Guest A"]
    assert entry.transcript_path == "transcript.vtt"


def test_post_watchlist_confirm_accepts_empty_cast(app_client, vault, monkeypatch):
    tc, robin_module = app_client
    sid = _do_add(tc, robin_module, monkeypatch)

    r = tc.post(
        "/robin/watchlist/add/confirm",
        cookies={"robin_watchlist_session": sid},
        data={"cast": ["", "   "]},
    )
    assert r.status_code == 303

    manifest = vault / "Watchlist" / "youtube" / "dQw4w9WgXcQ" / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["cast"] == []


def test_post_watchlist_confirm_without_session_redirects(app_client):
    tc, _ = app_client
    r = tc.post("/robin/watchlist/add/confirm", data={"cast": "X"})
    assert r.status_code == 303
    assert "/robin/watchlist/add" in r.headers["location"]


# ---------------------------------------------------------------------------
# Schema validation rejection — bad session video_id (defence-in-depth)
# ---------------------------------------------------------------------------


def test_post_watchlist_confirm_rejects_bad_video_id(app_client, vault, monkeypatch):
    tc, robin_module = app_client

    # Build a session manually with an unsafe video_id (route's regex guard
    # should reject before we ever touch the vault).
    sid = robin_module._new_session(
        step="watchlist_cast",
        video_id="../etc/passwd",
        title="x",
        channel="y",
        duration_s=1,
        url="https://youtube.com/watch?v=x",
        primary_lang="en",
        staging_vtt=str(vault / "tmp.vtt"),
    )

    r = tc.post(
        "/robin/watchlist/add/confirm",
        cookies={"robin_watchlist_session": sid},
        data={"cast": "X"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Schema validation rejection — bad title (empty channel + duration_s<0
# can't be triggered via session, so cover the path via direct schema
# construction in the resolver fixture).
# ---------------------------------------------------------------------------


def test_youtube_watchlist_entry_rejects_invalid_video_id():
    from pydantic import ValidationError

    from shared.schemas.youtube_watchlist import YouTubeWatchlistEntry

    with pytest.raises(ValidationError):
        YouTubeWatchlistEntry(
            video_id="../etc",
            title="x",
            channel="y",
            url="https://youtube.com/",
            duration_s=1,
            primary_lang="en",
            cast=[],
            transcript_path="transcript.vtt",
            added_at="2026-05-28T00:00:00Z",
        )
