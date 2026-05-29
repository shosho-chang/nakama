"""Tests for the YouTube av_reader route + WebVTT parser (ADR-035 PR1c-ii, PR2a)."""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient


def _write_annotation_set(vault_path: Path, video_id: str, items: list[dict]) -> Path:
    """Hand-craft a v3 KB/Annotations/youtube_{video_id}.md fixture file.

    Bypasses ``AnnotationStore.save`` so we can pin the exact on-disk shape
    used by the PR2a route loader (slug ``youtube_{video_id}``, v3 schema,
    ``cfi`` carries the ADR-035 §D5 ``t=`` locator).
    """
    ann_dir = vault_path / "KB" / "Annotations"
    ann_dir.mkdir(parents=True, exist_ok=True)
    path = ann_dir / f"youtube_{video_id}.md"
    items_json = json.dumps(items, ensure_ascii=False, indent=2)
    frontmatter = (
        "---\n"
        f"slug: youtube_{video_id}\n"
        "schema_version: 3\n"
        "base: youtube\n"
        f'updated_at: "{datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}"\n'
        "---\n"
    )
    path.write_text(frontmatter + "\n```json\n" + items_json + "\n```\n", encoding="utf-8")
    return path


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

    # Lines simplified vs real YT output but preserve the structure:
    # ghost cue is a 10ms cue with the prior carry-over as its only
    # body line; real cue body is [carry-over, new-content-with-tags].
    vtt = (
        "WEBVTT\nKind: captions\nLanguage: en\n\n"
        "00:00:00.000 --> 00:00:01.870 align:start position:0%\n\n"
        "A<00:00:00.200><c> lot</c><00:00:00.520><c> of</c>"
        "<00:00:00.800><c> people</c><00:00:01.040><c> think</c>\n\n"
        "00:00:01.870 --> 00:00:01.880 align:start position:0%\n"
        "A lot of people think\n\n\n"
        "00:00:01.880 --> 00:00:04.150 align:start position:0%\n"
        "A lot of people think\n"
        "is<00:00:02.440><c> getting</c><00:00:02.840><c> rid</c>"
        "<00:00:03.000><c> of</c><00:00:03.160><c> it</c>\n"
    )
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


# ── PR2a: locator parser + row shaping unit tests ──────────────────────


def test_parse_t_locator_single_point():
    from thousand_sunny.routers.robin import _parse_t_locator

    assert _parse_t_locator("t=123.4") == pytest.approx(123.4)


def test_parse_t_locator_range():
    from thousand_sunny.routers.robin import _parse_t_locator

    assert _parse_t_locator("t=123.4-145.7") == pytest.approx(123.4)


def test_parse_t_locator_none_or_malformed_returns_none():
    from thousand_sunny.routers.robin import _parse_t_locator

    assert _parse_t_locator(None) is None
    assert _parse_t_locator("") is None
    assert _parse_t_locator("cfi=/6/8") is None
    assert _parse_t_locator("t=abc") is None


def test_nearest_cue_index_exact_match():
    from thousand_sunny.routers.robin import _nearest_cue_index

    assert _nearest_cue_index([0.0, 3.0, 7.0], 3.0) == 1


def test_nearest_cue_index_within_tolerance():
    from thousand_sunny.routers.robin import _nearest_cue_index

    # 3.02 vs cue start 3.0 → within 50ms tol → snap to cue 1
    assert _nearest_cue_index([0.0, 3.0, 7.0], 3.02) == 1


def test_nearest_cue_index_returns_none_when_no_match_within_tolerance():
    from thousand_sunny.routers.robin import _nearest_cue_index

    # 5.0 is past tolerance for both neighbours → no floor fallback (would
    # misattribute the annotation to an unrelated cue).
    assert _nearest_cue_index([0.0, 3.0, 7.0], 5.0) is None


def test_nearest_cue_index_returns_none_when_far_past_last_cue():
    from thousand_sunny.routers.robin import _nearest_cue_index

    # Annotation locator far past every cue (e.g. transcript was re-fetched
    # after the annotation was saved) — must not mark the last cue.
    assert _nearest_cue_index([0.0, 3.0, 7.0], 100.0) is None


def test_nearest_cue_index_empty_returns_none():
    from thousand_sunny.routers.robin import _nearest_cue_index

    assert _nearest_cue_index([], 3.0) is None


# ── PR2a: annotation list rendering ────────────────────────────────────


def test_watch_video_renders_3quadrant_layout_classes(client, vault):
    test_client, _ = client
    _write_watchlist_entry(vault, "abcDEF12345", transcript=None)
    resp = test_client.get("/robin/watchlist/abcDEF12345")
    assert resp.status_code == 200
    body = resp.text
    # 3-quadrant grid uses these CSS hooks (the regression we care about
    # is that the structural classes the CSS targets are emitted).
    assert "av-grid" in body
    assert "pane-player" in body
    assert "pane-cues" in body
    assert "pane-annotations" in body


def test_watch_video_renders_empty_annotation_state(client, vault):
    test_client, _ = client
    _write_watchlist_entry(vault, "abcDEF12345", transcript=None)
    resp = test_client.get("/robin/watchlist/abcDEF12345")
    assert resp.status_code == 200
    body = resp.text
    # Empty state copy from #787 acceptance criteria.
    assert "暫停影片開始寫筆記" in body
    # No annotation list container when empty.
    assert 'id="annList"' not in body


def test_watch_video_renders_annotation_rows_from_store(client, vault):
    test_client, _ = client
    vtt = """WEBVTT

00:00:00.000 --> 00:00:03.000
Welcome to the show.

00:00:03.000 --> 00:00:07.000
We talk longevity.

00:00:07.000 --> 00:00:12.000
And sleep.
"""
    video_id = "abcDEF12345"
    _write_watchlist_entry(vault, video_id, transcript=vtt)
    _write_annotation_set(
        vault,
        video_id,
        [
            {
                "type": "annotation",
                "schema_version": 3,
                "cfi": "t=3.0-7.0",
                "text_excerpt": "We talk longevity.",
                "note": "core thesis of the episode",
                "created_at": "2026-05-28T09:00:00Z",
                "modified_at": "2026-05-28T09:00:00Z",
            },
            {
                "type": "highlight",
                "schema_version": 3,
                "cfi": "t=7.0-12.0",
                "text_excerpt": "And sleep.",
                "text": "And sleep.",
                "created_at": "2026-05-28T09:01:00Z",
                "modified_at": "2026-05-28T09:01:00Z",
            },
        ],
    )
    resp = test_client.get(f"/robin/watchlist/{video_id}")
    assert resp.status_code == 200
    body = resp.text
    # Annotation rows rendered.
    assert 'id="annList"' in body
    assert "ann-row" in body
    assert "core thesis of the episode" in body
    assert "We talk longevity." in body
    # Highlight-only row carries the "(no note)" placeholder.
    assert "(no note)" in body
    # Row click target — data-start = locator start seconds.
    assert 'data-start="3.0"' in body
    assert 'data-start="7.0"' in body
    # Cues with matching annotation get the visual marker hook.
    assert "has-annotation" in body
    # Empty state should NOT render when annotations exist.
    assert "暫停影片開始寫筆記" not in body


def test_watch_video_annotations_render_newest_first(client, vault):
    """Store appends chronologically (oldest-first); template must reverse
    so reloads agree with the optimistic prepend used by PR2b write flow."""
    test_client, _ = client
    vtt = """WEBVTT

00:00:00.000 --> 00:00:03.000
First cue text.

00:00:03.000 --> 00:00:07.000
Second cue text.
"""
    video_id = "sortORDER123"
    _write_watchlist_entry(vault, video_id, transcript=vtt)
    _write_annotation_set(
        vault,
        video_id,
        [
            {
                "type": "annotation",
                "schema_version": 3,
                "cfi": "t=0.0-3.0",
                "text_excerpt": "First cue text.",
                "note": "OLDEST",
                "created_at": "2026-05-28T09:00:00Z",
                "modified_at": "2026-05-28T09:00:00Z",
            },
            {
                "type": "annotation",
                "schema_version": 3,
                "cfi": "t=3.0-7.0",
                "text_excerpt": "Second cue text.",
                "note": "NEWEST",
                "created_at": "2026-05-28T09:05:00Z",
                "modified_at": "2026-05-28T09:05:00Z",
            },
        ],
    )
    body = test_client.get(f"/robin/watchlist/{video_id}").text
    newest_pos = body.find("NEWEST")
    oldest_pos = body.find("OLDEST")
    assert newest_pos > 0 and oldest_pos > 0
    assert newest_pos < oldest_pos, "newest annotation must render before oldest"


def test_watch_video_orphan_annotation_renders_without_data_start(client, vault):
    """Annotation whose ``cfi`` is missing / unparseable should still render
    (read-only display) but without a seek target."""
    test_client, _ = client
    video_id = "abcDEF12345"
    _write_watchlist_entry(vault, video_id, transcript=None)
    _write_annotation_set(
        vault,
        video_id,
        [
            {
                "type": "annotation",
                "schema_version": 3,
                "cfi": None,
                "text_excerpt": "free-floating thought",
                "note": "no anchor yet",
                "created_at": "2026-05-28T09:00:00Z",
                "modified_at": "2026-05-28T09:00:00Z",
            },
        ],
    )
    resp = test_client.get(f"/robin/watchlist/{video_id}")
    assert resp.status_code == 200
    body = resp.text
    assert "ann-row--orphan" in body
    assert "free-floating thought" in body
    assert "no anchor yet" in body
    # Orphan row has no data-start attribute.
    assert "data-start=" not in body
    # Time label falls back to placeholder.
    assert "--:--" in body


# ── PR2b: schema bump + write flow ─────────────────────────────────────


def test_highlight_v3_speaker_field_defaults_blank():
    """``speaker`` is a first-class field on HighlightV3 (ADR-035 PR2b).

    Default ``""`` keeps existing paper/book items round-tripping without
    a forced schema bump."""
    from shared.schemas.annotations import HighlightV3

    h = HighlightV3(text_excerpt="hello", text="hello")
    assert h.speaker == ""
    h2 = HighlightV3(text_excerpt="hello", text="hello", speaker="Peter Attia")
    assert h2.speaker == "Peter Attia"
    # Round-trip through model_dump survives the new field.
    restored = HighlightV3(**h2.model_dump())
    assert restored.speaker == "Peter Attia"


def test_annotation_v3_speaker_field_defaults_blank():
    from shared.schemas.annotations import AnnotationV3

    a = AnnotationV3(text_excerpt="span", note="thought")
    assert a.speaker == ""
    a2 = AnnotationV3(text_excerpt="span", note="thought", speaker="Andrew Huberman")
    assert a2.speaker == "Andrew Huberman"


def test_create_video_annotation_404_when_entry_missing(client):
    test_client, _ = client
    resp = test_client.post(
        "/robin/watchlist/abc123XYZ_-/annotation",
        json={
            "cue_start": 3.0,
            "cue_end": 7.0,
            "excerpt": "We talk longevity.",
            "speaker": "Peter Attia",
            "note": "core thesis",
            "highlight": False,
        },
    )
    assert resp.status_code == 404


def test_create_video_annotation_404_on_invalid_video_id_alphabet(client):
    test_client, _ = client
    resp = test_client.post(
        "/robin/watchlist/foo.bar/annotation",
        json={
            "cue_start": 3.0,
            "cue_end": 7.0,
            "excerpt": "x",
        },
    )
    assert resp.status_code == 404


def test_create_video_annotation_400_on_bad_range(client, vault):
    test_client, _ = client
    _write_watchlist_entry(vault, "abcDEF12345", transcript=None)
    # end <= start.
    resp = test_client.post(
        "/robin/watchlist/abcDEF12345/annotation",
        json={
            "cue_start": 7.0,
            "cue_end": 7.0,
            "excerpt": "x",
        },
    )
    assert resp.status_code == 400


def test_create_video_annotation_400_on_blank_excerpt(client, vault):
    test_client, _ = client
    _write_watchlist_entry(vault, "abcDEF12345", transcript=None)
    resp = test_client.post(
        "/robin/watchlist/abcDEF12345/annotation",
        json={
            "cue_start": 3.0,
            "cue_end": 7.0,
            "excerpt": "   ",
        },
    )
    assert resp.status_code == 400


def test_create_video_annotation_writes_highlight(client, vault):
    """★ quick-highlight (note empty, highlight=True) lands as a HighlightV3
    item with ``cfi=t={start}-{end}`` and the supplied speaker chip."""
    test_client, _ = client
    video_id = "abcDEF12345"
    _write_watchlist_entry(vault, video_id, cast=["Peter Attia"], transcript=None)
    resp = test_client.post(
        f"/robin/watchlist/{video_id}/annotation",
        json={
            "cue_start": 3.0,
            "cue_end": 7.0,
            "excerpt": "We talk longevity.",
            "speaker": "Peter Attia",
            "note": "",
            "highlight": True,
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["annotation"]["type"] == "highlight"
    assert payload["annotation"]["speaker"] == "Peter Attia"
    assert payload["annotation"]["start"] == 3.0
    assert payload["annotation"]["excerpt"] == "We talk longevity."

    # Persisted to KB/Annotations/youtube_{video_id}.md.
    ann_path = vault / "KB" / "Annotations" / f"youtube_{video_id}.md"
    assert ann_path.exists()
    raw = ann_path.read_text(encoding="utf-8")
    assert "schema_version: 3" in raw
    assert "t=3.0-7.0" in raw
    assert '"speaker": "Peter Attia"' in raw
    assert '"type": "highlight"' in raw


def test_create_video_annotation_writes_annotation_with_note(client, vault):
    """N-key editor save (note non-empty) lands as an AnnotationV3 item — even
    if the client sends ``highlight=True`` the server re-discriminates by
    note presence so the on-disk shape stays consistent."""
    test_client, _ = client
    video_id = "abcDEF12345"
    _write_watchlist_entry(vault, video_id, cast=["Peter Attia"], transcript=None)
    resp = test_client.post(
        f"/robin/watchlist/{video_id}/annotation",
        json={
            "cue_start": 3.0,
            "cue_end": 7.0,
            "excerpt": "We talk longevity.",
            "speaker": "",
            "note": "core thesis of the episode",
            "highlight": True,  # client lied; server re-discriminates by note
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["annotation"]["type"] == "annotation"
    assert payload["annotation"]["speaker"] == ""
    assert payload["annotation"]["note"] == "core thesis of the episode"

    ann_path = vault / "KB" / "Annotations" / f"youtube_{video_id}.md"
    raw = ann_path.read_text(encoding="utf-8")
    assert '"type": "annotation"' in raw
    assert "core thesis of the episode" in raw


def test_create_video_annotation_appends_to_existing_set(client, vault):
    """Two saves against the same video accumulate in one annotation file."""
    test_client, _ = client
    video_id = "abcDEF12345"
    _write_watchlist_entry(vault, video_id, transcript=None)

    def _post(start, end, note):
        return test_client.post(
            f"/robin/watchlist/{video_id}/annotation",
            json={
                "cue_start": start,
                "cue_end": end,
                "excerpt": f"text at {start}",
                "speaker": "",
                "note": note,
                "highlight": not note,
            },
        )

    assert _post(3.0, 7.0, "first").status_code == 200
    assert _post(10.0, 14.0, "").status_code == 200

    ann_path = vault / "KB" / "Annotations" / f"youtube_{video_id}.md"
    raw = ann_path.read_text(encoding="utf-8")
    assert "t=3.0-7.0" in raw
    assert "t=10.0-14.0" in raw
    # First was an annotation (note non-empty), second a highlight.
    assert '"type": "annotation"' in raw
    assert '"type": "highlight"' in raw


def test_create_video_annotation_template_after_reload(client, vault):
    """Saved annotations re-render on the watch page (closes the persistence
    loop end-to-end for browser UAT)."""
    test_client, _ = client
    video_id = "abcDEF12345"
    vtt = """WEBVTT

00:00:00.000 --> 00:00:03.000
Welcome.

00:00:03.000 --> 00:00:07.000
We talk longevity.
"""
    _write_watchlist_entry(vault, video_id, cast=["Peter Attia"], transcript=vtt)
    save = test_client.post(
        f"/robin/watchlist/{video_id}/annotation",
        json={
            "cue_start": 3.0,
            "cue_end": 7.0,
            "excerpt": "We talk longevity.",
            "speaker": "Peter Attia",
            "note": "core thesis",
            "highlight": False,
        },
    )
    assert save.status_code == 200
    page = test_client.get(f"/robin/watchlist/{video_id}")
    assert page.status_code == 200
    body = page.text
    assert 'id="annList"' in body
    assert "core thesis" in body
    assert "Peter Attia" in body
    assert 'data-start="3.0"' in body
    # Editor scaffold lives on the page so PR2b UI is reachable.
    assert 'id="annEditor"' in body
    assert 'id="annTextarea"' in body
    # ★ button is emitted on each cue.
    assert "cue-star" in body


def test_watch_video_template_renders_editor_when_no_cast(client, vault):
    """Editor still renders without cast chips so N-key open works even on
    videos with no declared cast (chip strip is suppressed)."""
    test_client, _ = client
    _write_watchlist_entry(vault, "abcDEF12345", cast=[], transcript=None)
    resp = test_client.get("/robin/watchlist/abcDEF12345")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="annEditor"' in body
    assert 'data-no-cast="true"' in body
    # No chip strip when cast is empty.
    assert 'class="ann-chip"' not in body


def test_create_video_annotation_unauthenticated_401(vault, monkeypatch):
    """POST without auth cookie returns 401, not a redirect — the frontend
    uses fetch and needs a status it can branch on."""
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
    resp = test_client.post(
        "/robin/watchlist/abcDEF12345/annotation",
        json={"cue_start": 3.0, "cue_end": 7.0, "excerpt": "x"},
    )
    assert resp.status_code == 401


# ── DELETE / upsert coverage (PR2b one-mark-per-cue) ─────────────────────────


def test_delete_video_highlight_removes_matching_item(client, vault):
    """Star toggle off blanket-removes whatever sits on the cue."""
    test_client, _ = client
    video_id = "abcDEF12345"
    _write_watchlist_entry(vault, video_id, transcript=None)
    # Seed a highlight on cue start=3.0.
    test_client.post(
        f"/robin/watchlist/{video_id}/annotation",
        json={
            "cue_start": 3.0,
            "cue_end": 7.0,
            "excerpt": "We talk longevity.",
            "speaker": "",
            "note": "",
            "highlight": True,
        },
    )
    resp = test_client.delete(f"/robin/watchlist/{video_id}/annotation?cue_start=3.0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["removed"] == 1
    assert body["cue_start"] == 3.0
    # File contents reflect the removal — no t=3.0 locator left.
    ann_path = vault / "KB" / "Annotations" / f"youtube_{video_id}.md"
    raw = ann_path.read_text(encoding="utf-8")
    assert "t=3.0-7.0" not in raw


def test_delete_video_highlight_removes_annotation_too(client, vault):
    """One-mark-per-cue: DELETE blows away the annotation even if it has a note."""
    test_client, _ = client
    video_id = "abcDEF12345"
    _write_watchlist_entry(vault, video_id, transcript=None)
    test_client.post(
        f"/robin/watchlist/{video_id}/annotation",
        json={
            "cue_start": 3.0,
            "cue_end": 7.0,
            "excerpt": "x",
            "speaker": "",
            "note": "with a real note",
            "highlight": False,
        },
    )
    resp = test_client.delete(f"/robin/watchlist/{video_id}/annotation?cue_start=3.0")
    assert resp.status_code == 200
    assert resp.json()["removed"] == 1


def test_delete_video_highlight_no_op_when_nothing_on_cue(client, vault):
    """DELETE on a cue with no mark returns removed=0, not 404."""
    test_client, _ = client
    video_id = "abcDEF12345"
    _write_watchlist_entry(vault, video_id, transcript=None)
    resp = test_client.delete(f"/robin/watchlist/{video_id}/annotation?cue_start=99.0")
    assert resp.status_code == 200
    assert resp.json()["removed"] == 0


def test_delete_video_highlight_404_when_video_missing(client, vault):
    test_client, _ = client
    resp = test_client.delete("/robin/watchlist/zzzZZZ99999/annotation?cue_start=3.0")
    assert resp.status_code == 404


def test_delete_video_highlight_400_on_negative_cue_start(client, vault):
    test_client, _ = client
    video_id = "abcDEF12345"
    _write_watchlist_entry(vault, video_id, transcript=None)
    resp = test_client.delete(f"/robin/watchlist/{video_id}/annotation?cue_start=-1.0")
    assert resp.status_code == 400


def test_delete_video_highlight_unauthenticated_401(vault, monkeypatch):
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
    resp = test_client.delete("/robin/watchlist/abcDEF12345/annotation?cue_start=3.0")
    assert resp.status_code == 401


def test_create_video_annotation_upsert_replaces_same_cue(client, vault):
    """Saving on a cue that already has a mark replaces in-place, not appends."""
    test_client, _ = client
    video_id = "abcDEF12345"
    _write_watchlist_entry(vault, video_id, transcript=None)
    # First save: highlight (no note).
    r1 = test_client.post(
        f"/robin/watchlist/{video_id}/annotation",
        json={
            "cue_start": 3.0,
            "cue_end": 7.0,
            "excerpt": "We talk longevity.",
            "speaker": "",
            "note": "",
            "highlight": True,
        },
    )
    assert r1.status_code == 200
    assert r1.json()["replaced"] is False
    # Second save on SAME cue: annotation with note → should replace, not append.
    r2 = test_client.post(
        f"/robin/watchlist/{video_id}/annotation",
        json={
            "cue_start": 3.0,
            "cue_end": 7.0,
            "excerpt": "We talk longevity.",
            "speaker": "",
            "note": "upgraded with note",
            "highlight": False,
        },
    )
    assert r2.status_code == 200
    assert r2.json()["replaced"] is True
    # Only one item on disk.
    ann_path = vault / "KB" / "Annotations" / f"youtube_{video_id}.md"
    raw = ann_path.read_text(encoding="utf-8")
    assert raw.count("t=3.0-7.0") == 1
    assert "upgraded with note" in raw
    assert '"type": "annotation"' in raw
    # Highlight upgraded → no leftover highlight type at this cue.
    # (Other cues may have highlights; this file only has one item total.)
    assert raw.count('"type": ') == 1


def test_create_video_annotation_upsert_drift_tolerance(client, vault):
    """Cue start drift within 50ms still treated as the same cue for upsert."""
    test_client, _ = client
    video_id = "abcDEF12345"
    _write_watchlist_entry(vault, video_id, transcript=None)
    test_client.post(
        f"/robin/watchlist/{video_id}/annotation",
        json={
            "cue_start": 3.0,
            "cue_end": 7.0,
            "excerpt": "x",
            "speaker": "",
            "note": "first",
            "highlight": False,
        },
    )
    # 30ms drift — within the 50ms tol.
    r2 = test_client.post(
        f"/robin/watchlist/{video_id}/annotation",
        json={
            "cue_start": 3.03,
            "cue_end": 7.03,
            "excerpt": "x",
            "speaker": "",
            "note": "second",
            "highlight": False,
        },
    )
    assert r2.json()["replaced"] is True


def test_create_video_annotation_upsert_outside_tolerance_appends(client, vault):
    """Cue start far enough away → distinct cue, separate item."""
    test_client, _ = client
    video_id = "abcDEF12345"
    _write_watchlist_entry(vault, video_id, transcript=None)
    test_client.post(
        f"/robin/watchlist/{video_id}/annotation",
        json={
            "cue_start": 3.0,
            "cue_end": 7.0,
            "excerpt": "x",
            "speaker": "",
            "note": "first",
            "highlight": False,
        },
    )
    r2 = test_client.post(
        f"/robin/watchlist/{video_id}/annotation",
        json={
            "cue_start": 5.0,
            "cue_end": 9.0,
            "excerpt": "y",
            "speaker": "",
            "note": "second",
            "highlight": False,
        },
    )
    assert r2.json()["replaced"] is False


def test_watch_video_lit_star_marker_for_cues_with_marks(client, vault):
    """Cues with a saved mark render with the ``has-annotation`` class so the
    star is lit. PR2b collapses the prior highlight/annotation split into a
    single marker set."""
    test_client, _ = client
    video_id = "abcDEF12345"
    vtt = """WEBVTT

00:00:00.000 --> 00:00:03.000
Welcome.

00:00:03.000 --> 00:00:07.000
We talk longevity.
"""
    _write_watchlist_entry(vault, video_id, transcript=vtt)
    # Save a mark on the second cue (start=3.0).
    test_client.post(
        f"/robin/watchlist/{video_id}/annotation",
        json={
            "cue_start": 3.0,
            "cue_end": 7.0,
            "excerpt": "We talk longevity.",
            "speaker": "",
            "note": "",
            "highlight": True,
        },
    )
    page = test_client.get(f"/robin/watchlist/{video_id}")
    body = page.text
    # The marked cue's <div> carries has-annotation.
    assert "has-annotation" in body
