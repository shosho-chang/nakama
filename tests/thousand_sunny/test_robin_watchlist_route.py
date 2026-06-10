"""Tests for ``GET /robin/watchlist`` (ADR-035 §F4 — issue #763).

The list view bypasses ``PromotionReviewService.list_pending`` and walks
``{vault}/Watchlist/youtube/`` directly via ``RegistryReadingSourceLister``.

Acceptance (issue #763):

- 200 + lists every ``youtube_video`` source on disk
- Each row links to ``/robin/watchlist/{video_id}``
- Empty state when no entries → message, not a crash
- Broken entry (malformed manifest) is silently skipped, not propagated
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    """Vault root with empty ``Watchlist/youtube/`` pre-created.

    The watchlist route resolves the vault via ``shared.config.get_vault_path``
    which reads ``VAULT_PATH`` at call time, so we set it on the env before
    reloading the app module.
    """
    (tmp_path / "Watchlist" / "youtube").mkdir(parents=True)
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    return tmp_path


@pytest.fixture
def app_client(vault: Path, monkeypatch):
    """TestClient with auth disabled + Robin router mounted.

    ``WEB_PASSWORD``/``WEB_SECRET`` cleared → ``check_auth`` returns True
    without a cookie. ``DISABLE_ROBIN`` cleared → ``robin_router`` (which
    owns ``/robin/watchlist``) is mounted by ``thousand_sunny.app`` at
    import time.
    """
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(robin_module)
    importlib.reload(app_module)

    return TestClient(app_module.app, follow_redirects=False)


def _make_entry(
    vault: Path,
    *,
    video_id: str,
    title: str = "Sample video",
    channel: str = "Sample channel",
    duration_s: int = 3600,
    primary_lang: str = "en",
    cast: list[str] | None = None,
) -> Path:
    """Write a minimal valid ``Watchlist/youtube/{video_id}/manifest.json``."""
    entry_dir = vault / "Watchlist" / "youtube" / video_id
    entry_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "video_id": video_id,
        "title": title,
        "channel": channel,
        "url": f"https://youtube.com/watch?v={video_id}",
        "duration_s": duration_s,
        "primary_lang": primary_lang,
        "cast": cast if cast is not None else ["host"],
        "transcript_path": "transcript.vtt",
        "added_at": "2026-05-28T00:00:00Z",
    }
    (entry_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (entry_dir / "transcript.vtt").write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhi\n", encoding="utf-8"
    )
    return entry_dir


# ── Empty state ────────────────────────────────────────────────────────────


def test_watchlist_empty_renders_empty_state(app_client):
    r = app_client.get("/robin/watchlist")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "還沒有影片" in r.text


# ── Populated list ─────────────────────────────────────────────────────────


def test_watchlist_lists_youtube_entries(app_client, vault):
    _make_entry(
        vault,
        video_id="dQw4w9WgXcQ",
        title="Andrew Huberman on Dopamine",
        channel="Huberman Lab",
        duration_s=5400,
        cast=["host", "Andrew Huberman"],
    )
    _make_entry(
        vault,
        video_id="abc12345XYZ",
        title="Sleep and dementia",
        channel="Peter Attia",
        duration_s=2730,
    )
    r = app_client.get("/robin/watchlist")
    assert r.status_code == 200
    # Both titles present
    assert "Andrew Huberman on Dopamine" in r.text
    assert "Sleep and dementia" in r.text
    # Both link targets present (acceptance: row links to /robin/watchlist/{id})
    assert 'href="/robin/watchlist/dQw4w9WgXcQ"' in r.text
    assert 'href="/robin/watchlist/abc12345XYZ"' in r.text
    # Channel rendered
    assert "Huberman Lab" in r.text
    # Duration rendered in H:MM:SS for 5400s (1:30:00) and M:SS for 2730s (45:30)
    assert "1:30:00" in r.text
    assert "45:30" in r.text
    # Cast preview rendered
    assert "Andrew Huberman" in r.text


# ── Broken entry skip ──────────────────────────────────────────────────────


def test_watchlist_skips_broken_entries(app_client, vault):
    """A malformed manifest must not blank the whole list view."""
    # One well-formed entry
    _make_entry(vault, video_id="okay_video", title="Good one")
    # One broken entry — invalid JSON
    broken = vault / "Watchlist" / "youtube" / "broken_idd"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("not-valid-json{", encoding="utf-8")
    # One entry where video_id mismatches the dir name (resolver returns None)
    _make_entry(vault, video_id="mismatch_id")  # manifest video_id == dir name
    mismatch_dir = vault / "Watchlist" / "youtube" / "mismatch_xx"
    mismatch_dir.mkdir(parents=True)
    (mismatch_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "video_id": "different_id",
                "title": "drifted",
                "channel": "x",
                "url": "https://youtube.com/watch?v=different_id",
                "duration_s": 10,
                "primary_lang": "en",
                "cast": [],
                "transcript_path": "transcript.vtt",
                "added_at": "2026-05-28T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    r = app_client.get("/robin/watchlist")
    assert r.status_code == 200
    assert "Good one" in r.text
    # The drifted-title row must not appear (resolver returned None)
    assert "drifted" not in r.text


# ── Auth gate ──────────────────────────────────────────────────────────────


# ── Unit tests for projection helpers ──────────────────────────────────────


def test_format_duration_branches():
    """Cover the three branches: empty / short / long."""
    from thousand_sunny.routers.robin import _format_duration

    assert _format_duration(None) == ""
    assert _format_duration(-1) == ""
    assert _format_duration(45) == "0:45"
    assert _format_duration(125) == "2:05"
    assert _format_duration(3600) == "1:00:00"
    assert _format_duration(5400) == "1:30:00"


def test_watchlist_row_coerces_cast_and_duration_variants():
    """Defensive coercion: cast may be list, JSON-string, malformed, or missing.

    Same defensiveness for duration_s (int / str-digit / missing / garbage).
    Today the registry emits ``metadata['cast']`` as a JSON string + duration
    as a str-int; F7 #765 will flip cast to a real list. Both shapes must
    project to the same row dict so the surface keeps working across the
    schema migration.
    """
    from shared.schemas.reading_source import ReadingSource, SourceVariant
    from thousand_sunny.routers.robin import _watchlist_row

    def _rs(metadata: dict[str, str]) -> ReadingSource:
        return ReadingSource(
            schema_version=2,
            source_id="youtube:vid1234567",
            annotation_key="youtube_vid1234567",
            kind="youtube_video",
            title="t",
            author="ch",
            primary_lang="en",
            has_evidence_track=True,
            evidence_reason=None,
            variants=[
                SourceVariant(
                    role="original",
                    format="vtt",
                    lang="en",
                    path="Watchlist/youtube/vid1234567/transcript.vtt",
                )
            ],
            metadata=metadata,
        )

    # cast as JSON string (production shape today)
    row = _watchlist_row(
        _rs(
            {
                "video_id": "vid1234567",
                "channel": "ch",
                "duration_s": "120",
                "url": "https://youtube.com/watch?v=vid1234567",
                "cast": json.dumps(["a", "b", "c", "d"]),
            }
        )
    )
    # only first 3 cast names surface in preview
    assert row["cast_preview"] == "a, b, c"
    assert row["duration"] == "2:00"

    # cast as malformed JSON → empty preview
    row = _watchlist_row(
        _rs(
            {
                "video_id": "vid1234567",
                "duration_s": "not-a-number",
                "cast": "{not json",
            }
        )
    )
    assert row["cast_preview"] == ""
    assert row["duration"] == ""

    # cast as JSON object (not list) → empty preview
    row = _watchlist_row(
        _rs(
            {
                "video_id": "vid1234567",
                "duration_s": "0",
                "cast": json.dumps({"unexpected": "shape"}),
            }
        )
    )
    assert row["cast_preview"] == ""

    # missing metadata keys entirely → graceful defaults
    row = _watchlist_row(_rs({}))
    assert row["video_id"] == ""
    assert row["duration"] == ""
    assert row["cast_preview"] == ""
    # falls back to rs.title when video_id is empty
    assert row["title"] == "t"


def test_watchlist_redirects_to_login_without_auth(app_client, vault, monkeypatch):
    """When auth is enabled, the route must redirect to /login."""
    # Re-enable auth by setting WEB_PASSWORD + reloading auth + app
    monkeypatch.setenv("WEB_PASSWORD", "secret")
    monkeypatch.setenv("WEB_SECRET", "shhh")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(robin_module)
    importlib.reload(app_module)

    client = TestClient(app_module.app, follow_redirects=False)
    r = client.get("/robin/watchlist")
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")
