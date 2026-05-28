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


def test_post_watchlist_add_ytdlp_caption_error(app_client, monkeypatch):
    tc, robin_module = app_client
    monkeypatch.setattr(robin_module, "fetch_metadata", lambda url: _fake_metadata())

    def fake_fetch_caption(video_id, output_dir):
        from shared.youtube_ingest import YtDlpError

        raise YtDlpError("net failure", stderr="ERROR: HTTP 429")

    monkeypatch.setattr(robin_module, "fetch_caption", fake_fetch_caption)

    r = tc.post(
        "/robin/watchlist/add",
        data={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert r.status_code == 400


def test_post_watchlist_confirm_500_when_staged_vtt_missing(app_client, vault, monkeypatch):
    tc, robin_module = app_client

    # Build a session pointing at a vtt that doesn't exist on disk (simulates
    # a crashed/cleaned tmp state between add and confirm).
    sid = robin_module._new_session(
        step="watchlist_cast",
        video_id="dQw4w9WgXcQ",
        title="x",
        channel="y",
        duration_s=1,
        url="https://youtube.com/watch?v=dQw4w9WgXcQ",
        primary_lang="en",
        staging_vtt=str(vault / "does-not-exist.vtt"),
    )
    r = tc.post(
        "/robin/watchlist/add/confirm",
        cookies={"robin_watchlist_session": sid},
        data={"cast": "X"},
    )
    assert r.status_code == 500
    assert "staged transcript missing" in r.text


def test_post_watchlist_confirm_validation_error_returns_400(app_client, vault, monkeypatch):
    tc, robin_module = app_client

    # Negative duration_s violates ``ge=0`` constraint in the schema.
    sid = robin_module._new_session(
        step="watchlist_cast",
        video_id="dQw4w9WgXcQ",
        title="x",
        channel="y",
        duration_s=-5,
        url="https://youtube.com/watch?v=dQw4w9WgXcQ",
        primary_lang="en",
        staging_vtt=str(vault / "ignore.vtt"),
    )
    r = tc.post(
        "/robin/watchlist/add/confirm",
        cookies={"robin_watchlist_session": sid},
        data={"cast": "X"},
    )
    assert r.status_code == 400
    assert "watchlist entry" in r.text or "驗證" in r.text


def test_staging_root_lives_outside_watchlist_youtube(app_client, vault, monkeypatch):
    """PR #771 code-review finding #1: staging dir must NOT live inside
    ``Watchlist/youtube/`` (would trip the reading_source_lister with a
    dot-prefixed dir + spurious WARNING log every scan)."""
    tc, robin_module = app_client

    def fake_fetch_metadata(url):
        return _fake_metadata()

    def fake_fetch_caption(video_id, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)
        vtt = output_dir / "transcript.vtt"
        vtt.write_text("WEBVTT\n", encoding="utf-8")
        return vtt, "en"

    monkeypatch.setattr(robin_module, "fetch_metadata", fake_fetch_metadata)
    monkeypatch.setattr(robin_module, "fetch_caption", fake_fetch_caption)

    tc.post(
        "/robin/watchlist/add",
        data={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    # No .staging dir under Watchlist/youtube/.
    assert not (vault / "Watchlist" / "youtube" / ".staging").exists()
    # Staging dir under the sibling ``.youtube_staging``.
    assert (vault / "Watchlist" / ".youtube_staging" / "dQw4w9WgXcQ").is_dir()


def test_sweep_orphan_staging_removes_old_dirs(app_client, vault):
    """PR #771 code-review finding #2: orphan staging dirs older than the
    session TTL get swept by ``_sweep_orphan_staging``."""
    tc, robin_module = app_client

    import os
    import time

    # Hand-build an "old" orphan: dir + vtt with mtime far in the past.
    orphan_dir = vault / "Watchlist" / ".youtube_staging" / "OLDORPHANXX"
    orphan_dir.mkdir(parents=True)
    (orphan_dir / "transcript.vtt").write_text("WEBVTT\n", encoding="utf-8")
    old_ts = time.time() - robin_module._STAGING_ORPHAN_TTL - 60
    os.utime(orphan_dir, (old_ts, old_ts))

    # Recent dir — must NOT be swept.
    recent_dir = vault / "Watchlist" / ".youtube_staging" / "RECENT_XXXX"
    recent_dir.mkdir(parents=True)
    (recent_dir / "transcript.vtt").write_text("WEBVTT\n", encoding="utf-8")

    robin_module._sweep_orphan_staging()
    assert not orphan_dir.exists(), "orphan staging dir should have been swept"
    assert recent_dir.exists(), "recent staging dir must not be swept"


def test_post_watchlist_confirm_cleans_staging_leftovers(app_client, vault, monkeypatch):
    """Confirm path cleans up staging leftovers (yt-dlp may leave .json /
    .info files next to the vtt). Covers the inner unlink loop branch."""
    tc, robin_module = app_client

    def fake_fetch_metadata(url):
        return _fake_metadata()

    def fake_fetch_caption(video_id, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)
        vtt = output_dir / "transcript.vtt"
        vtt.write_text("WEBVTT\n", encoding="utf-8")
        # Simulate yt-dlp leftover info-json artefact.
        (output_dir / "info.json").write_text("{}", encoding="utf-8")
        return vtt, "en"

    monkeypatch.setattr(robin_module, "fetch_metadata", fake_fetch_metadata)
    monkeypatch.setattr(robin_module, "fetch_caption", fake_fetch_caption)

    r = tc.post(
        "/robin/watchlist/add",
        data={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    sid = r.cookies["robin_watchlist_session"]

    r2 = tc.post(
        "/robin/watchlist/add/confirm",
        cookies={"robin_watchlist_session": sid},
        data={"cast": "X"},
    )
    assert r2.status_code == 303
    # Staging dir should be gone (cleanup succeeded incl. leftover unlink).
    staging_dir = vault / "Watchlist" / "youtube" / ".staging" / "dQw4w9WgXcQ"
    assert not staging_dir.exists()


def test_post_watchlist_confirm_cleanup_tolerates_missing_staging(app_client, vault, monkeypatch):
    """If the staging dir is already gone (e.g. another process cleaned it),
    the cleanup branch should ``except OSError: pass`` without crashing."""
    tc, robin_module = app_client

    # Hand-build a session with a staging_vtt that exists but no staging dir
    # (we'll create the vtt in a different location).
    vtt = vault / "isolated_vtt" / "transcript.vtt"
    vtt.parent.mkdir(parents=True, exist_ok=True)
    vtt.write_text("WEBVTT\n", encoding="utf-8")

    sid = robin_module._new_session(
        step="watchlist_cast",
        video_id="dQw4w9WgXcQ",
        title="t",
        channel="c",
        duration_s=10,
        url="https://youtube.com/watch?v=dQw4w9WgXcQ",
        primary_lang="en",
        staging_vtt=str(vtt),
    )
    r = tc.post(
        "/robin/watchlist/add/confirm",
        cookies={"robin_watchlist_session": sid},
        data={"cast": "X"},
    )
    assert r.status_code == 303
    # Entry written despite no staging dir to clean.
    assert (vault / "Watchlist" / "youtube" / "dQw4w9WgXcQ" / "manifest.json").exists()


def test_post_watchlist_confirm_sets_auth_cookie(app_client, vault, monkeypatch):
    """Confirm response should re-attach the ``nakama_auth`` cookie so the
    follow-up GET to the reader detail page (#762) stays authenticated."""
    tc, robin_module = app_client

    vtt = vault / "tmp" / "x.vtt"
    vtt.parent.mkdir(parents=True, exist_ok=True)
    vtt.write_text("WEBVTT\n", encoding="utf-8")

    sid = robin_module._new_session(
        step="watchlist_cast",
        video_id="dQw4w9WgXcQ",
        title="t",
        channel="c",
        duration_s=10,
        url="https://youtube.com/watch?v=dQw4w9WgXcQ",
        primary_lang="en",
        staging_vtt=str(vtt),
    )
    r = tc.post(
        "/robin/watchlist/add/confirm",
        cookies={"robin_watchlist_session": sid, "nakama_auth": "preserved-token"},
        data={"cast": "X"},
    )
    assert r.status_code == 303
    # The nakama_auth cookie is re-set on the redirect response.
    set_cookie = r.headers.get("set-cookie", "")
    assert "nakama_auth=preserved-token" in set_cookie


def test_auth_redirects_when_unauthenticated(app_client, monkeypatch):
    tc, robin_module = app_client
    # Force auth gate active.
    monkeypatch.setenv("WEB_PASSWORD", "secret")
    monkeypatch.setenv("WEB_SECRET", "shh")
    import thousand_sunny.auth as auth_module

    importlib.reload(auth_module)
    importlib.reload(robin_module)

    # GET form → 302 to login.
    r = tc.get("/robin/watchlist/add")
    assert r.status_code == 302
    assert "/login" in r.headers["location"]

    # POST add → 302 to login.
    r2 = tc.post("/robin/watchlist/add", data={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert r2.status_code == 302
    assert "/login" in r2.headers["location"]

    # POST confirm → 302 to login.
    r3 = tc.post("/robin/watchlist/add/confirm", data={"cast": "X"})
    assert r3.status_code == 302
    assert "/login" in r3.headers["location"]


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
