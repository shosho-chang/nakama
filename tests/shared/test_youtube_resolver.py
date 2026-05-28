"""Tests for ADR-035 PR1b — YouTubeKey resolver + lister extension +
defensive dispatch fixes on source_map_builder + promotion_preflight.

Mirrors the BookKey / InboxKey patterns in test_reading_source_registry.py
and test_reading_source_lister.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.promotion_preflight import PromotionPreflight
from shared.reading_source_lister import RegistryReadingSourceLister
from shared.reading_source_registry import (
    ReadingSourceRegistry,
    YouTubeKey,
)
from shared.schemas.reading_source import ReadingSource, SourceVariant
from shared.schemas.youtube_watchlist import YouTubeWatchlistEntry
from shared.source_map_builder import SourceMapBuilder

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Tmp vault root with ``Watchlist/youtube/`` pre-created."""
    (tmp_path / "Watchlist" / "youtube").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def registry(vault: Path) -> ReadingSourceRegistry:
    return ReadingSourceRegistry(vault_root=vault)


def _make_entry(
    vault: Path,
    *,
    video_id: str = "dQw4w9WgXcQ",
    title: str = "Andrew Huberman on Dopamine",
    channel: str = "Huberman Lab",
    duration_s: int = 5400,
    primary_lang: str = "en",
    cast: list[str] | None = None,
    transcript_path: str = "transcript.vtt",
    write_transcript: bool = True,
    manifest_video_id: str | None = None,
) -> Path:
    """Build ``Watchlist/youtube/{video_id}/`` with manifest.json + optional
    transcript stub. Returns the entry directory."""
    entry_dir = vault / "Watchlist" / "youtube" / video_id
    entry_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "video_id": manifest_video_id if manifest_video_id is not None else video_id,
        "title": title,
        "channel": channel,
        "url": f"https://youtube.com/watch?v={video_id}",
        "duration_s": duration_s,
        "primary_lang": primary_lang,
        "cast": cast if cast is not None else ["host", "Andrew Huberman"],
        "transcript_path": transcript_path,
        "added_at": "2026-05-28T00:00:00Z",
    }
    (entry_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    if write_transcript:
        (entry_dir / transcript_path).write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello world\n",
            encoding="utf-8",
        )
    return entry_dir


# ── Resolver ───────────────────────────────────────────────────────────────


def test_resolve_youtube_happy_path(registry: ReadingSourceRegistry, vault: Path):
    _make_entry(vault)
    rs = registry.resolve(YouTubeKey(video_id="dQw4w9WgXcQ"))
    assert rs is not None
    assert rs.kind == "youtube_video"
    assert rs.schema_version == 2  # V14 invariant
    assert rs.source_id == "youtube:dQw4w9WgXcQ"
    assert rs.annotation_key == "youtube_dQw4w9WgXcQ"
    assert rs.primary_lang == "en"
    assert rs.has_evidence_track is True
    assert rs.evidence_reason is None
    assert len(rs.variants) == 1
    variant = rs.variants[0]
    assert variant.role == "original"
    assert variant.format == "vtt"
    assert variant.path == "Watchlist/youtube/dQw4w9WgXcQ/transcript.vtt"
    # metadata payload
    assert rs.metadata["video_id"] == "dQw4w9WgXcQ"
    assert rs.metadata["channel"] == "Huberman Lab"
    assert rs.metadata["duration_s"] == "5400"
    assert rs.metadata["url"] == "https://youtube.com/watch?v=dQw4w9WgXcQ"
    # cast lifted to top-level field (F7); no longer in metadata dict
    assert "cast" not in rs.metadata
    assert rs.cast == ["host", "Andrew Huberman"]


def test_resolve_youtube_missing_entry_returns_none(registry: ReadingSourceRegistry):
    assert registry.resolve(YouTubeKey(video_id="absent_video")) is None


def test_resolve_youtube_malformed_manifest_returns_none(
    registry: ReadingSourceRegistry, vault: Path
):
    entry_dir = vault / "Watchlist" / "youtube" / "broken_id"
    entry_dir.mkdir(parents=True)
    (entry_dir / "manifest.json").write_text("not-valid-json{", encoding="utf-8")
    assert registry.resolve(YouTubeKey(video_id="broken_id")) is None


def test_resolve_youtube_schema_mismatch_returns_none(registry: ReadingSourceRegistry, vault: Path):
    """Missing required field (e.g. duration_s) -> Pydantic raises -> None."""
    entry_dir = vault / "Watchlist" / "youtube" / "incomplete_id"
    entry_dir.mkdir(parents=True)
    (entry_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "video_id": "incomplete_id",
                "title": "x",
                "channel": "y",
                "url": "https://youtube.com/watch?v=incomplete_id",
                # duration_s missing
                "primary_lang": "en",
                "added_at": "2026-05-28T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    assert registry.resolve(YouTubeKey(video_id="incomplete_id")) is None


def test_resolve_youtube_video_id_mismatch_returns_none(
    registry: ReadingSourceRegistry, vault: Path
):
    """Directory name vs manifest.video_id drift -> None + warning."""
    _make_entry(vault, video_id="dirX", manifest_video_id="dirY")
    assert registry.resolve(YouTubeKey(video_id="dirX")) is None


def test_resolve_youtube_path_traversal_rejected(
    registry: ReadingSourceRegistry,
):
    """video_id with ``..`` is rejected by the vault-relative guard."""
    with pytest.raises(ValueError, match="escapes vault"):
        registry.resolve(YouTubeKey(video_id="../escape"))


def test_resolve_youtube_unknown_lang_passes_through(registry: ReadingSourceRegistry, vault: Path):
    """primary_lang='unknown' is valid and surfaces unchanged."""
    _make_entry(vault, video_id="unk_id", primary_lang="unknown")
    rs = registry.resolve(YouTubeKey(video_id="unk_id"))
    assert rs is not None
    assert rs.primary_lang == "unknown"


def test_resolve_youtube_empty_cast_ok(registry: ReadingSourceRegistry, vault: Path):
    """cast=[] is the documented unspecified state, not an error."""
    _make_entry(vault, video_id="nocast_id", cast=[])
    rs = registry.resolve(YouTubeKey(video_id="nocast_id"))
    assert rs is not None
    assert rs.cast == []
    assert "cast" not in rs.metadata


def test_resolve_youtube_cast_roundtrips_as_list(registry: ReadingSourceRegistry, vault: Path):
    """F7: cast=["host","guest"] round-trips through the resolver as a
    typed ``list[str]`` on the top-level ``cast`` field — never as a
    JSON-encoded string in ``metadata``."""
    _make_entry(vault, video_id="cast_id", cast=["host", "guest_a", "guest_b"])
    rs = registry.resolve(YouTubeKey(video_id="cast_id"))
    assert rs is not None
    assert rs.cast == ["host", "guest_a", "guest_b"]
    assert isinstance(rs.cast, list)
    assert "cast" not in rs.metadata


def test_resolve_youtube_custom_transcript_path(registry: ReadingSourceRegistry, vault: Path):
    """Phase 2 may produce e.g. ``transcript.whisper.vtt``; that path
    surfaces in the variant unchanged.
    """
    _make_entry(
        vault,
        video_id="alt_id",
        transcript_path="transcript.whisper.vtt",
    )
    rs = registry.resolve(YouTubeKey(video_id="alt_id"))
    assert rs is not None
    assert rs.variants[0].path == "Watchlist/youtube/alt_id/transcript.whisper.vtt"


# ── Lister ─────────────────────────────────────────────────────────────────


def test_lister_enumerates_youtube_watchlist(
    registry: ReadingSourceRegistry, vault: Path, tmp_path: Path
):
    _make_entry(vault, video_id="vid_a")
    _make_entry(vault, video_id="vid_b")
    lister = RegistryReadingSourceLister(
        registry=registry,
        inbox_root=vault / "Inbox" / "web",
        books_root=tmp_path / "books-empty",
        watchlist_youtube_root=vault / "Watchlist" / "youtube",
    )
    sources = lister.list_sources()
    yt_sources = [s for s in sources if s.kind == "youtube_video"]
    assert len(yt_sources) == 2
    assert {s.source_id for s in yt_sources} == {"youtube:vid_a", "youtube:vid_b"}


def test_lister_skips_youtube_when_root_none(
    registry: ReadingSourceRegistry, vault: Path, tmp_path: Path
):
    """Backward compat — callers not passing watchlist_youtube_root get
    empty youtube enumeration even when the directory exists on disk."""
    _make_entry(vault, video_id="ignored_id")
    lister = RegistryReadingSourceLister(
        registry=registry,
        inbox_root=vault / "Inbox" / "web",
        books_root=tmp_path / "books-empty",
        # watchlist_youtube_root omitted
    )
    sources = lister.list_sources()
    assert not any(s.kind == "youtube_video" for s in sources)


def test_lister_youtube_root_absent_no_crash(
    registry: ReadingSourceRegistry, vault: Path, tmp_path: Path
):
    """When watchlist_youtube_root is set but the directory does not exist,
    the lister returns [] for that arm without raising."""
    lister = RegistryReadingSourceLister(
        registry=registry,
        inbox_root=vault / "Inbox" / "web",
        books_root=tmp_path / "books-empty",
        watchlist_youtube_root=tmp_path / "no-such-dir",
    )
    assert lister.list_sources() == []


def test_lister_skips_broken_youtube_entry(
    registry: ReadingSourceRegistry, vault: Path, tmp_path: Path
):
    """A broken manifest.json is logged + skipped; sibling valid entries
    still surface."""
    _make_entry(vault, video_id="good_id")
    bad_dir = vault / "Watchlist" / "youtube" / "bad_id"
    bad_dir.mkdir()
    (bad_dir / "manifest.json").write_text("garbage{", encoding="utf-8")
    lister = RegistryReadingSourceLister(
        registry=registry,
        inbox_root=vault / "Inbox" / "web",
        books_root=tmp_path / "books-empty",
        watchlist_youtube_root=vault / "Watchlist" / "youtube",
    )
    yt = [s for s in lister.list_sources() if s.kind == "youtube_video"]
    assert {s.source_id for s in yt} == {"youtube:good_id"}


# ── Schema (YouTubeWatchlistEntry) ─────────────────────────────────────────


def test_youtube_watchlist_entry_rejects_traversal_in_transcript_path():
    """F2 — transcript_path cannot smuggle ``..`` or path separators
    through the schema."""
    base_kwargs = dict(
        video_id="abc123",
        title="t",
        channel="c",
        url="https://youtube.com/watch?v=abc123",
        duration_s=10,
        primary_lang="en",
        added_at="2026-05-28T00:00:00Z",
    )
    for bad in ["../escape.vtt", "../../etc/passwd", "sub/transcript.vtt", "", "transcript.txt"]:
        with pytest.raises(ValidationError):
            YouTubeWatchlistEntry(**base_kwargs, transcript_path=bad)


def test_youtube_watchlist_entry_rejects_bad_video_id():
    """F5 — schema-side video_id mirrors the registry's alphabet guard."""
    base_kwargs = dict(
        title="t",
        channel="c",
        url="https://youtube.com/watch?v=x",
        duration_s=10,
        primary_lang="en",
        added_at="2026-05-28T00:00:00Z",
    )
    for bad in ["", "../escape", "with space", "with/slash", "with.dot"]:
        with pytest.raises(ValidationError):
            YouTubeWatchlistEntry(**base_kwargs, video_id=bad)


def test_resolve_youtube_symlink_escape_rejected(
    registry: ReadingSourceRegistry, vault: Path, tmp_path: Path
):
    """F3 — symlink at Watchlist/youtube/{alphabet-valid} pointing outside
    vault raises ValueError, mirroring the InboxKey guard."""
    import os
    import sys

    if sys.platform == "win32" and not _can_symlink():
        pytest.skip("Windows symlink creation requires admin / dev mode")
    outside = tmp_path.parent / "outside_vault"
    outside.mkdir(exist_ok=True)
    link = vault / "Watchlist" / "youtube" / "evilid"
    os.symlink(outside, link, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes vault"):
        registry.resolve(YouTubeKey(video_id="evilid"))


def _can_symlink() -> bool:
    """Probe Windows for symlink privilege; True elsewhere."""
    import os
    import sys
    import tempfile

    if sys.platform != "win32":
        return True
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "t"
        link = Path(tmp) / "l"
        target.mkdir()
        try:
            os.symlink(target, link, target_is_directory=True)
            return True
        except (OSError, NotImplementedError):
            return False


def test_youtube_watchlist_entry_rejects_negative_duration():
    with pytest.raises(ValidationError):
        YouTubeWatchlistEntry(
            video_id="x",
            title="y",
            channel="z",
            url="https://youtube.com/watch?v=x",
            duration_s=-1,
            primary_lang="en",
            added_at="2026-05-28T00:00:00Z",
        )


def test_youtube_watchlist_entry_extra_field_rejected():
    """``extra="forbid"`` on the schema means typo'd keys raise."""
    with pytest.raises(ValidationError):
        YouTubeWatchlistEntry(
            video_id="x",
            title="y",
            channel="z",
            url="https://youtube.com/watch?v=x",
            duration_s=10,
            primary_lang="en",
            added_at="2026-05-28T00:00:00Z",
            unknown_field="oops",
        )


# ── Dispatch hole defensive fixes (PR1a review #3 / #4) ────────────────────


def test_source_map_builder_refuses_youtube_video():
    """Defensive fix — youtube_video flowed through ``_inspect_inbox``
    pre-PR1b, producing junk. Should now route to the error path."""
    rs = ReadingSource(
        schema_version=2,
        source_id="youtube:vid_z",
        annotation_key="youtube_vid_z",
        kind="youtube_video",
        title="t",
        primary_lang="en",
        has_evidence_track=True,
        variants=[
            SourceVariant(
                role="original",
                format="vtt",
                lang="en",
                path="Watchlist/youtube/vid_z/transcript.vtt",
            )
        ],
    )

    class _NoopExtractor:
        def extract(self, *_args, **_kwargs):  # pragma: no cover — never called
            raise AssertionError("extractor must not run for unsupported kind")

    builder = SourceMapBuilder(blob_loader=lambda _: b"")
    result = builder.build(rs, extractor=_NoopExtractor())
    assert result.error is not None
    assert "youtube_video" in result.error
    assert result.items == []


def test_promotion_preflight_refuses_youtube_video():
    """Defensive fix — youtube_video flowed through ``_inspect_markdown``
    pre-PR1b. Should now route to the inspector_error / defer path."""
    rs = ReadingSource(
        schema_version=2,
        source_id="youtube:vid_pre",
        annotation_key="youtube_vid_pre",
        kind="youtube_video",
        title="t",
        primary_lang="en",
        has_evidence_track=True,
        variants=[
            SourceVariant(
                role="original",
                format="vtt",
                lang="en",
                path="Watchlist/youtube/vid_pre/transcript.vtt",
            )
        ],
    )
    preflight = PromotionPreflight(blob_loader=lambda _: b"")
    report = preflight.run(rs)
    assert report.error is not None
    assert "youtube_video" in report.error
    # error implies inspector failure -> action policy routes to defer.
    assert report.recommended_action == "defer"
