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

_VIDEO_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
"""Mirrors ``ReadingSourceRegistry._VALID_YOUTUBE_ID``. Defence-in-depth so
that a producer constructing this model directly (e.g., PR1c ingestion
writer) is rejected at schema validation rather than only at resolver-time
dir-vs-manifest mismatch."""

_TRANSCRIPT_PATH_PATTERN = r"^[A-Za-z0-9._-]+\.(vtt|webvtt)$"
"""Single-segment filename ending in a WebVTT extension. Rejects ``..``,
``/``, ``\\`` and empty strings — closes the path-traversal vector the
``video_id`` regex doesn't cover (PR1b review F2)."""


class YouTubeWatchlistEntry(BaseModel):
    """One entry under ``Watchlist/youtube/{video_id}/manifest.json``.

    Frozen value-object. The on-disk file is rewritten as a whole when the
    user edits cast / corrects metadata (no in-place mutation of a loaded
    instance).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1

    video_id: str = Field(pattern=_VIDEO_ID_PATTERN, min_length=1)
    """Canonical YouTube video id — 11-char URL slug. NEVER includes the
    ``v=`` prefix or the surrounding URL. Constrained to the YouTube id
    alphabet for defence-in-depth (mirrors the resolver's runtime guard)."""

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

    transcript_path: str = Field(default="transcript.vtt", pattern=_TRANSCRIPT_PATH_PATTERN)
    """Single-segment WebVTT filename RELATIVE to the entry directory
    (``Watchlist/youtube/{video_id}/``). Default is the track yt-dlp
    produces; overridable for future Phase 2 ``Whisper``-transcribed track
    variants without re-keying.

    Constrained to ``[A-Za-z0-9._-]+\\.(vtt|webvtt)`` — rejects ``..``,
    path separators, and empty strings so that a hostile or buggy
    ``manifest.json`` cannot smuggle a vault-escape into
    ``ReadingSource.variants[0].path`` (PR1b review F2)."""

    added_at: str
    """ISO-8601 UTC timestamp string. Stable; never updated after first
    write (use a separate ``modified_at`` field if needed in a future
    schema_version bump)."""
