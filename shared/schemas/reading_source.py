"""Reading Source schema (ADR-024 Slice 1 / issue #509).

Pre-promotion identity for the two Reading Source kinds: ``ebook`` and
``inbox_document``. Web documents land in the inbox via Toast / Obsidian
Clipper, so origin is metadata not a separate kind. Textbook is a promotion
*mode* applied to an ebook source — it never produces or consumes a
``ReadingSource``.

Slice 1 fields only. Promotion-stage fields (``manifest_id``,
``promotion_status``, review decisions) are added by later slices; do not
add them speculatively here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SchemaVersion = Literal[1, 2]
"""ReadingSource wire-format version.

- v=1: ``ebook`` / ``inbox_document`` SourceKind only (#509).
- v=2: adds ``youtube_video`` SourceKind + ``vtt`` VariantFormat (ADR-035 §D5/D8).
       Existing v=1 records remain readable; new ``youtube_video`` rows MUST
       set ``schema_version=2`` (enforced by V14).
"""

SourceKind = Literal["ebook", "inbox_document", "youtube_video"]
"""Closed enum.

- v=1 members:
  - ``ebook``          — record in the ``books`` table; epub blob in
                         ``data/books/{book_id}/``.
  - ``inbox_document`` — markdown file in ``Inbox/kb/{slug}.md``. Web documents
                         arrive via Toast / Obsidian Clipper into the same
                         directory; origin is metadata, not a separate kind.
- v=2 added (ADR-035 §D1/D8):
  - ``youtube_video``  — video file backed by YouTube. Self-contained
                         directory ``Watchlist/youtube/{video_id}/`` holds
                         manifest + transcript.vtt + per-annotation frame
                         snapshots. Audio-only ``podcast`` SourceKind
                         deferred per ADR-035 §D8 ("audio-only on demand only").
"""

TrackRole = Literal["original", "display"]
"""``original`` is the factual evidence layer (en source for an English
book; zh source for a Chinese article; absent when only a bilingual file
exists).

``display`` is the Reader UX layer (bilingual EPUB, ``-bilingual.md``
sibling). May coincide with the original when no separate display track
exists.
"""

VariantFormat = Literal["epub", "markdown", "vtt"]
"""Closed enum.

- v=1 members: ``epub`` (ebook) / ``markdown`` (inbox_document).
- v=2 added:   ``vtt`` — WebVTT transcript file for ``youtube_video`` source
               (ADR-035 §D5). Path convention:
               ``Watchlist/youtube/{video_id}/transcript.vtt``.
"""


class SourceVariant(BaseModel):
    """One concrete file/blob backing a Reading Source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: TrackRole
    format: VariantFormat
    lang: str
    """BCP-47 short tag: ``"en"`` / ``"zh-Hant"`` / ``"bilingual"``."""

    path: str
    """Canonical path syntax (single contract):

    - ebook  → ``data/books/{book_id}/original.epub`` or
               ``data/books/{book_id}/bilingual.epub`` (matches
               ``shared.book_storage.read_book_blob`` signature).
    - inbox  → vault-relative md path (e.g. ``Inbox/kb/foo.md``).
    """

    bytes_estimate: int | None = None


class ReadingSource(BaseModel):
    """Normalized Reading Source — pre-promotion identity + tracks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: SchemaVersion = 1

    source_id: str
    """Stable namespace-qualified identity. NEVER mutates with frontmatter
    edits or sibling lifecycle changes.

    - ``ebook``          → ``ebook:{book_id}``
    - ``inbox_document`` → ``inbox:{logical_original_path}`` where
                           ``logical_original_path`` strips the
                           ``-bilingual.md`` suffix if present, otherwise
                           the path as-is. ``InboxKey('Inbox/web/foo.md')``
                           and ``InboxKey('Inbox/web/foo-bilingual.md')``
                           BOTH resolve to ``inbox:Inbox/web/foo.md``.
    - ``youtube_video``  → ``youtube:{video_id}`` (canonical YouTube video
                           id, the 11-char URL slug — ``dQw4w9WgXcQ`` etc.).
                           Stable across re-ingestion / re-transcription.

    NB3 (v3): in case (b) bilingual-only, ``logical_original_path`` does
    NOT exist on disk. ``source_id`` is a **logical identity, not a
    filesystem lookup key**. Use ``variants[*].path`` for file access.
    """

    annotation_key: str
    """Key for joining ``KB/Annotations/{annotation_key}.md`` per the
    existing reader save path. May mutate with frontmatter title changes
    (per ``annotation_slug`` semantics). Do NOT use for stable identity.

    - ``ebook``          → ``book_id`` (matches existing
                           ``KB/Annotations/{book_id}.md`` save path).
    - ``inbox_document`` → ``annotation_slug(user_facing_filename, fm)``
                           where ``user_facing`` follows the
                           ``_get_inbox_files`` collapse rule — bilingual
                           sibling if it exists, plain otherwise.
    - ``youtube_video``  → ``youtube_{video_id}`` (matches the
                           ``Watchlist/youtube/{video_id}/`` dir prefix;
                           kept distinct from ebook's bare ``book_id`` to
                           avoid namespace collision).
    """

    kind: SourceKind
    title: str
    author: str | None = None

    primary_lang: str
    """BCP-47 short language of the evidence / original-language content.
    Derived from upstream metadata only — never from ``Book.lang_pair``.

    - ``ebook``          → ``BookMetadata.lang`` via
                           ``extract_metadata(blob_bytes)``.
    - ``inbox_document`` → frontmatter ``lang`` field; ``"unknown"`` if
                           absent.
    - ``youtube_video``  → preferred auto-caption track language reported
                           by yt-dlp ``automatic_captions`` (ADR-035 §D2
                           Phase 1: ``en`` > ``zh-Hant`` > ``zh-CN``);
                           ``"unknown"`` when no caption available.

    Normalization (see ``_normalize_primary_lang``):

    - any ``zh-*`` tag (``zh``, ``zh-TW``, ``zh-Hant``, ``zh-CN``,
      ``zh-Hans``) → ``"zh-Hant"``
    - any ``en-*`` tag (``en``, ``en-US``, ``en-GB``)        → ``"en"``
    - missing / empty / unrecognized                           → ``"unknown"``

    NEVER ``"bilingual"``. NEVER defaults to ``"en"``.

    NB2 (v3): in case (b) bilingual-only inbox, ``primary_lang`` is
    best-effort / low-confidence — the translator's ``lang:`` convention
    is not pinned. Downstream slices (#511 / #513 / #514) MUST consult
    ``evidence_reason == "bilingual_only_inbox"`` and treat this lang
    value as low-confidence.
    """

    has_evidence_track: bool
    """``True`` when an original-language track exists. Downstream slices
    (#511 / #513 / #514) decide block / defer / degrade. #509 enforces no
    policy.
    """

    evidence_reason: str | None = None
    """Short stable reason code when ``has_evidence_track=False``;
    ``None`` when ``True``.

    Closed set for ``schema_version=1`` (NB6 contract):

    - ``"no_original_uploaded"`` — ebook with ``has_original=False``;
      only ``bilingual.epub`` blob exists.
    - ``"bilingual_only_inbox"`` — inbox_document where only the
      ``-bilingual.md`` sibling exists; no plain original sibling on disk.

    Extension protocol: closed for ``schema_version=1``. Adding a new
    code requires (a) bumping ``schema_version``, (b) updating this
    docstring, (c) updating downstream policy in #511 / #513 / #514.
    Silent extension is forbidden.
    """

    variants: list[SourceVariant] = Field(default_factory=list, min_length=1)
    """At least one variant. Stable invariants:

    - ``has_evidence_track=True``  ⇒ exactly one variant has
      ``role='original'``.
    - ``has_evidence_track=False`` ⇒ no variant has ``role='original'``.
    """

    metadata: dict[str, str] = Field(default_factory=dict)
    """Cheap pass-through string-only metadata.

    NB3 (v3): ``Book.lang_pair`` is NEVER passed through even though it
    exists in main. Consumers MUST use ``ReadingSource.primary_lang`` for
    language semantics. Re-deriving language from ``Book.lang_pair`` on
    the downstream side bypasses Q1 拍板 and is forbidden by §6 boundary
    13.

    - ``ebook`` → ``isbn``, ``published_year``, ``original_metadata_lang``
      (raw ``BookMetadata.lang`` before ``_normalize_primary_lang``).
    - ``inbox`` → ``original_url``, ``fulltext_layer``, ``fulltext_source``
      (per ``IngestResult`` frontmatter contract).
    - ``youtube_video`` (ADR-035 §D4) → ``video_id``, ``channel``,
      ``duration_s`` (string-encoded int), ``url`` (canonical
      ``https://youtube.com/watch?v={video_id}``), ``cast`` (JSON-encoded
      ``list[str]`` host + guests per ADR-035 §D3).
    """

    @model_validator(mode="after")
    def _validate_schema_version_kind_coupling(self) -> "ReadingSource":
        # V14 (ADR-035 §D8) — ``youtube_video`` kind requires schema_version >= 2.
        # Mirrors PromotionManifest's V12/V13: v=1 records cannot silently
        # gain new SourceKind capability.
        if self.kind == "youtube_video" and self.schema_version < 2:
            raise ValueError(
                f"kind='youtube_video' requires schema_version >= 2; "
                f"got schema_version={self.schema_version}"
            )
        return self
