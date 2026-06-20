"""Tests for shared.video_transcript_writer — the .vtt → readable .md renderer.

Renders ``KB/Raw/Videos/{id}.md`` (timestamped-paragraph prose + frontmatter)
from the machine ``{id}.vtt``. This .md is both the human-readable transcript
and the preferred ``/start-video`` ingest input.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    import shared.config as cfg

    importlib.reload(cfg)
    return tmp_path


def _write_vtt(vault: Path, video_id: str) -> None:
    vtt = vault / "KB" / "Raw" / "Videos" / f"{video_id}.vtt"
    vtt.parent.mkdir(parents=True, exist_ok=True)
    vtt.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nWelcome to the show.\n\n"
        "00:01:05.000 --> 00:01:07.000\nToday we discuss sleep.\n",
        encoding="utf-8",
    )


def _write_manifest(vault: Path, video_id: str) -> None:
    d = vault / "Watchlist" / "youtube" / video_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "video_id": video_id,
                "channel": "Sleep Lab",
                "title": "Why We Sleep",
                "cast": ["Host", "Guest"],
            }
        ),
        encoding="utf-8",
    )


def test_renders_frontmatter_and_timestamped_body(vault):
    from shared.video_transcript_writer import write_video_transcript_md

    _write_vtt(vault, "vid123")
    _write_manifest(vault, "vid123")

    out = write_video_transcript_md(vault, "vid123")
    assert out == vault / "KB" / "Raw" / "Videos" / "vid123.md"
    text = out.read_text(encoding="utf-8")
    # Frontmatter
    assert "type: video_transcript" in text
    assert "video_id: vid123" in text
    assert "Why We Sleep" in text  # display title from manifest
    assert "Sleep Lab" in text  # channel → title + author
    assert "KB/Raw/Videos/vid123.vtt" in text  # source pointer back to machine raw
    # Body: timestamped paragraph, cleaned prose, no raw vtt scaffolding
    assert "**[00:01]**" in text
    assert "Welcome to the show." in text
    assert "Today we discuss sleep." in text
    assert "-->" not in text


def test_missing_vtt_returns_none(vault):
    from shared.video_transcript_writer import write_video_transcript_md

    assert write_video_transcript_md(vault, "nope") is None
    assert not (vault / "KB" / "Raw" / "Videos" / "nope.md").exists()


def test_empty_vtt_returns_none(vault):
    from shared.video_transcript_writer import write_video_transcript_md

    vtt = vault / "KB" / "Raw" / "Videos" / "empty.vtt"
    vtt.parent.mkdir(parents=True, exist_ok=True)
    vtt.write_text("WEBVTT\n\nnot a cue line\n", encoding="utf-8")
    assert write_video_transcript_md(vault, "empty") is None


def test_missing_manifest_falls_back_to_slug_title(vault):
    from shared.video_transcript_writer import write_video_transcript_md

    _write_vtt(vault, "noman")
    out = write_video_transcript_md(vault, "noman")
    assert out is not None
    text = out.read_text(encoding="utf-8")
    assert "youtube_noman" in text  # title fell back to slug (no manifest)
