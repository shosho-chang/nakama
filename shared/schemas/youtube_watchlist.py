"""YouTube Watchlist entry schema (ADR-035 §D4).

Persisted as ``Watchlist/youtube/{video_id}/manifest.json``. Read by
``ReadingSourceRegistry._resolve_youtube`` (and by future ingestion routes
that write it after ``yt-dlp`` fetches metadata + captions).

Slice 1 (this file): schema-only. Producer (yt-dlp ingestion route) ships in
PR1c; consumer (registry resolver) ships in this PR.

The watchlist-entry ``schema_version`` is independent from
``ReadingSource.schema_version`` (which is fixed at 2 for ``youtube_video``
per V14) and from ``PromotionManifest.schema_version`` (which bumps to 2 per
V13 when timestamp_range anchors appear). All three move on their own
release cadence.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class YouTubeWatchlistEntry(BaseModel):
    """One entry under ``Watchlist/youtube/{video_id}/manifest.json``.

    Frozen value-object. The on-disk file is rewritten as a whole when the
    user edits cast / corrects metadata (no in-place mutation of a loaded
    instance).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1

    video_id: str
    """Canonical YouTube video id — 11-char URL slug. NEVER includes the
    ``v=`` prefix or the surrounding URL."""

    title: str
    channel: str
    url: str
    """Canonical ``https://youtube.com/watch?v={video_id}`` form. Stored
    redundantly with ``video_id`` so callers don't have to re-construct."""

    duration_s: int = Field(ge=0)
    """Total video length in seconds (from yt-dlp metadata)."""

    primary_lang: str
    """BCP-47 short tag for the transcript track. Derived from yt-dlp
    ``automatic_captions`` keys per ADR-035 §D2 priority (en > zh-Hant >
    zh-CN). ``"unknown"`` when no caption track is available."""

    cast: list[str] = Field(default_factory=list)
    """Free-text cast names declared at watchlist add time (ADR-035 §D3).
    Per typical podcast-interview shape, 1-4 names: ``[host, guest_a, ...]``.
    Empty list = ``unspecified`` speaker for every annotation until the user
    edits the entry."""

    transcript_path: str = "transcript.vtt"
    """Path RELATIVE to the entry directory (``Watchlist/youtube/{video_id}/``).
    Default is the WebVTT track yt-dlp produces; overridable for future
    Phase 2 ``Whisper``-transcribed track variants without re-keying."""

    added_at: str
    """ISO-8601 UTC timestamp string. Stable; never updated after first
    write (use a separate ``modified_at`` field if needed in a future
    schema_version bump)."""
