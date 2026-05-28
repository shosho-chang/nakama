"""Tests for the YouTube av_reader route + WebVTT parser (ADR-035 PR1c-ii)."""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    import shared.config as cfg

    importlib.reload(cfg)
    return tmp_path


@pytest.fixture
def client(vault, monkeypatch):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)

    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(robin_module)

    app = FastAPI()
    app.include_router(robin_module.robin_router)

    @app.get("/login")
    def login(next: str = ""):
        return PlainTextResponse(f"login next={next}")

    return TestClient(app, follow_redirects=False), robin_module


def _write_watchlist_entry(
    vault_path: Path,
    video_id: str,
    *,
    title: str = "Longevity Research Update",
    channel: str = "Peter Attia",
    cast: list[str] | None = None,
    transcript: str | None = None,
) -> Path:
    entry_dir = vault_path / "Watchlist" / "youtube" / video_id
    entry_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "video_id": video_id,
        "title": title,
        "channel": channel,
        "url": f"https://youtube.com/watch?v={video_id}",
        "duration_s": 2400,
        "primary_lang": "en",
        "cast": cast or [],
        "transcript_path": "transcript.vtt",
        "added_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    (entry_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    if transcript is not None:
        (entry_dir / "transcript.vtt").write_text(transcript, encoding="utf-8")
    return entry_dir


# ── VTT parser unit tests ──────────────────────────────────────────────


def test_parse_webvtt_basic_cues():
    from thousand_sunny.routers.robin import _parse_webvtt

    vtt = """WEBVTT

00:00:00.000 --> 00:00:03.500
Welcome to the show.

00:00:03.500 --> 00:00:07.000
Today we talk about longevity.
"""
    cues = _parse_webvtt(vtt)
    assert len(cues) == 2
    assert cues[0]["start"] == 0.0
    assert cues[0]["end"] == 3.5
    assert cues[0]["label"] == "00:00"
    assert cues[0]["text"] == "Welcome to the show."
    assert cues[1]["start"] == 3.5
    assert cues[1]["text"] == "Today we talk about longevity."


def test_parse_webvtt_dedups_rolling_repeats():
    from thousand_sunny.routers.robin import _parse_webvtt

    # yt-dlp auto-sub style: same sentence appears in two consecutive
    # cues with shifted timing. Dedup collapses them; sentence-coalesce
    # then emits one cue per terminator.
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
Identical sentence here.

00:00:02.000 --> 00:00:04.500
Identical sentence here.

00:00:04.500 --> 00:00:07.000
Next thought after that.
"""
    cues = _parse_webvtt(vtt)
    assert len(cues) == 2
    assert cues[0]["text"] == "Identical sentence here."
    assert cues[0]["end"] == 4.5  # extended past the dedup
    assert cues[1]["text"] == "Next thought after that."


def test_parse_webvtt_strips_cue_tags():
    from thousand_sunny.routers.robin import _parse_webvtt

    vtt = """WEBVTT

00:00:01.000 --> 00:00:02.000
<c.colorE5E5E5>hello</c> <c.color00FFFF>world</c>
"""
    cues = _parse_webvtt(vtt)
    assert cues[0]["text"] == "hello world"


def test_parse_webvtt_handles_hour_timestamp():
    from thousand_sunny.routers.robin import _parse_webvtt

    vtt = """WEBVTT

01:23:45.000 --> 01:23:48.000
in the second hour.
"""
    cues = _parse_webvtt(vtt)
    assert cues[0]["start"] == 5025.0
    assert cues[0]["label"] == "1:23:45"


def test_parse_webvtt_empty_or_malformed_returns_empty():
    from thousand_sunny.routers.robin import _parse_webvtt

    assert _parse_webvtt("") == []
    assert _parse_webvtt("WEBVTT\n\nnot a cue\n") == []


def test_parse_webvtt_skips_inline_note_and_header_lines():
    from thousand_sunny.routers.robin import _parse_webvtt

    # NOTE blocks and stray WEBVTT-style lines inside a cue body should be
    # filtered out; only real text survives.
    vtt = """WEBVTT
Kind: captions
Language: en

NOTE this is a comment

00:00:00.000 --> 00:00:02.000
real text
NOTE inline ignored
WEBVTT bogus continuation
"""
    cues = _parse_webvtt(vtt)
    assert len(cues) == 1
    assert cues[0]["text"] == "real text"


def test_parse_webvtt_drops_youtube_carryover_lines():
    """yt-dlp YouTube auto-sub format: ghost cue (10ms) holds carry-over
    text, real cue body is [carry-over line, new-content line]. Keep ONLY
    the new-content line so the cue stream reads as one chunk per spoken
    interval rather than repeating each line twice."""
    from thousand_sunny.routers.robin import _parse_webvtt

    vtt = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:01.870 align:start position:0%

A<00:00:00.200><c> lot</c><00:00:00.520><c> of</c><00:00:00.800><c> people</c><00:00:01.040><c> think</c>

00:00:01.870 --> 00:00:01.880 align:start position:0%
A lot of people think


00:00:01.880 --> 00:00:04.150 align:start position:0%
A lot of people think
is<00:00:02.440><c> getting</c><00:00:02.840><c> rid</c><00:00:03.000><c> of</c><00:00:03.160><c> it</c>
"""
    cues = _parse_webvtt(vtt)
    texts = [c["text"] for c in cues]
    # Three VTT cues → final stream after carry-over drop + sentence
    # coalesce: a single sentence ("A lot of people think is getting
    # rid of it") because neither raw cue ended in a terminator and
    # the trailing flush emits whatever accumulated.
    assert texts == ["A lot of people think is getting rid of it"]


def test_parse_webvtt_coalesces_into_sentences():
    """Each output cue ends on a sentence terminator (.!?) when one
    is present in the buffered text. Long groups get split into one
    output cue per sentence with timing distributed by character count."""
    from thousand_sunny.routers.robin import _parse_webvtt

    vtt = """WEBVTT

00:00:00.000 --> 00:00:03.000
A lot of people think emotion regulation is

00:00:03.000 --> 00:00:06.000
getting rid of a feeling. It's not what

00:00:06.000 --> 00:00:09.000
it is. It's just having another relationship.
"""
    cues = _parse_webvtt(vtt)
    texts = [c["text"] for c in cues]
    assert texts == [
        "A lot of people think emotion regulation is getting rid of a feeling.",
        "It's not what it is.",
        "It's just having another relationship.",
    ]
    # Timing distributed proportionally by character count across the 9s window.
    assert cues[0]["start"] == 0.0
    assert cues[-1]["end"] == 9.0
    assert cues[0]["end"] < cues[1]["start"] + 0.01  # adjacency


# ── Route behaviour ────────────────────────────────────────────────────


def test_watch_video_404_when_entry_missing(client):
    test_client, _ = client
    resp = test_client.get("/robin/watchlist/abc123XYZ_-")
    assert resp.status_code == 404


def test_watch_video_404_on_invalid_video_id_alphabet(client):
    # Slash / dot / spaces trigger the resolver's ValueError → 404.
    test_client, _ = client
    for bad in ["../etc", "abc/def", "foo bar", "foo.bar"]:
        resp = test_client.get(f"/robin/watchlist/{bad}")
        assert resp.status_code == 404, f"expected 404 for {bad!r}, got {resp.status_code}"


def test_watch_video_renders_with_fixture(client, vault):
    test_client, _ = client
    vtt = """WEBVTT

00:00:00.000 --> 00:00:03.000
Welcome to the conversation.

00:00:03.000 --> 00:00:07.000
Today we discuss longevity.
"""
    _write_watchlist_entry(
        vault,
        "abcDEF12345",
        title="Longevity Conversation",
        channel="Peter Attia",
        cast=["Peter Attia", "Andrew Huberman"],
        transcript=vtt,
    )
    resp = test_client.get("/robin/watchlist/abcDEF12345")
    assert resp.status_code == 200
    body = resp.text
    assert "Longevity Conversation" in body
    assert "Peter Attia" in body
    assert "Andrew Huberman" in body
    assert "Welcome to the conversation." in body
    assert 'data-video-id="abcDEF12345"' in body
    # Cues JSON embedded for the player JS:
    assert '"start": 3.0' in body or '"start":3.0' in body


def test_watch_video_renders_without_transcript(client, vault):
    test_client, _ = client
    # Manifest present, transcript.vtt absent → empty-cue state, still 200.
    _write_watchlist_entry(vault, "xyz789NOTX", transcript=None)
    resp = test_client.get("/robin/watchlist/xyz789NOTX")
    assert resp.status_code == 200
    assert "這支影片沒有可用的字幕" in resp.text


def test_watch_video_login_redirect_when_unauthenticated(vault, monkeypatch):
    monkeypatch.setenv("WEB_PASSWORD", "pw")
    monkeypatch.setenv("WEB_SECRET", "secret")

    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(robin_module)

    app = FastAPI()
    app.include_router(robin_module.robin_router)

    @app.get("/login")
    def login(next: str = ""):
        return PlainTextResponse(f"login next={next}")

    test_client = TestClient(app, follow_redirects=False)
    resp = test_client.get("/robin/watchlist/abcDEF12345")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"
