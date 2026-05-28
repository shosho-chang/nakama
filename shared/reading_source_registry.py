"""Reading Source Registry (ADR-024 Slice 1 / issue #509).

Resolves a ``BookKey``, ``InboxKey``, or ``YouTubeKey`` (ADR-035 §D1) to a
normalized ``ReadingSource`` value object. Resolver only — no enumeration,
no promotion, no LLM, no UI, no vault or DB writes.

NB1 contract (v3): every blob-read / metadata-extract / frontmatter-parse
failure returns ``None`` and logs a ``WARNING`` via
``shared.log.get_logger("nakama.shared.reading_source_registry")``. The
registry never propagates uncontrolled exceptions to callers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import yaml
from pydantic import ValidationError

from shared import book_storage
from shared.annotation_store import annotation_slug
from shared.config import get_vault_path
from shared.epub_metadata import extract_metadata
from shared.log import get_logger
from shared.schemas.reading_source import ReadingSource, SourceVariant
from shared.schemas.youtube_watchlist import YouTubeWatchlistEntry
from shared.utils import read_text

_logger = get_logger("nakama.shared.reading_source_registry")

_VALID_YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]+")
"""YouTube video ID alphabet per YouTube's published URL spec. Used as a
path-traversal guard before joining ``video_id`` to the vault root. We
deliberately don't pin the length at 11 — YouTube has historically issued
shorter test ids and the registry shouldn't reject those wholesale; the
``Watchlist/youtube/{video_id}/manifest.json`` lookup will surface a
``None`` for non-existent entries anyway."""


# ---------------------------------------------------------------------------
# Source keys
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BookKey:
    """Identifies an ebook by its row in the ``books`` table."""

    book_id: str


@dataclass(frozen=True)
class InboxKey:
    """Vault-relative path under the inbox (ADR-028: ``Inbox/web/``; e.g.
    ``Inbox/web/foo.md`` or ``Inbox/web/foo-bilingual.md``). Both siblings
    resolve to the same logical Reading Source. The prefix is not validated
    here — resolution is path-agnostic; only the ``-bilingual.md`` suffix
    is special-cased.
    """

    relative_path: str


@dataclass(frozen=True)
class YouTubeKey:
    """Identifies a YouTube video watchlist entry (ADR-035 §D1).

    Resolution reads ``Watchlist/youtube/{video_id}/manifest.json``
    (validated by :class:`YouTubeWatchlistEntry`) and emits a
    ``ReadingSource(kind='youtube_video', schema_version=2)``. The variant
    points at ``Watchlist/youtube/{video_id}/{transcript_path}`` (default
    ``transcript.vtt``) as the original-track evidence.
    """

    video_id: str


SourceKey = Union[BookKey, InboxKey, YouTubeKey]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_primary_lang(raw: str | None) -> str:
    """Normalize a BCP-47-ish lang tag to project-canonical short form.

    Per 修修's stated language scope (永遠 zh-Hant + en):

    - any ``zh-*`` tag → ``"zh-Hant"``
    - any ``en-*`` tag → ``"en"``
    - missing / empty / unrecognized → ``"unknown"``

    NEVER returns ``"bilingual"``. Caller must NOT default to ``"en"`` when
    the upstream value is missing.
    """
    if not raw:
        return "unknown"
    s = raw.lower().strip()
    if s.startswith("zh"):
        return "zh-Hant"
    if s.startswith("en"):
        return "en"
    return "unknown"


def _strict_parse_frontmatter(content: str) -> tuple[dict, str]:
    """Like ``shared.utils.extract_frontmatter`` but lets ``yaml.YAMLError``
    propagate so the registry can surface NB1 ``inbox_frontmatter_parse_failed``
    warnings instead of silently swallowing.
    """
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    fm = yaml.safe_load(parts[1])
    if not isinstance(fm, dict):
        fm = {}
    return fm, parts[2].strip()


_BILINGUAL_SUFFIX = "-bilingual.md"


def _logical_original(relative_path: str) -> str:
    """Strip ``-bilingual.md`` suffix if present; else return path as-is."""
    if relative_path.endswith(_BILINGUAL_SUFFIX):
        return relative_path[: -len(_BILINGUAL_SUFFIX)] + ".md"
    return relative_path


def _logical_bilingual(logical_original: str) -> str:
    """Compute the bilingual sibling path for a logical original."""
    return logical_original[:-3] + _BILINGUAL_SUFFIX  # strip ``.md``


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ReadingSourceRegistry:
    """Resolve a ``SourceKey`` to a normalized ``ReadingSource``.

    Public surface depends only on ``shared/`` modules — no FastAPI, no
    Thousand Sunny route handlers (asserted by ``test_no_fastapi_imports``).
    """

    def __init__(self, vault_root: Path | None = None) -> None:
        self._vault = Path(vault_root) if vault_root else get_vault_path()

    # ------------------------------------------------------------------
    # Public dispatch
    # ------------------------------------------------------------------

    def resolve(self, key: SourceKey) -> ReadingSource | None:
        if isinstance(key, BookKey):
            return self._resolve_book(key.book_id)
        if isinstance(key, InboxKey):
            return self._resolve_inbox(key.relative_path)
        if isinstance(key, YouTubeKey):
            return self._resolve_youtube(key.video_id)
        raise TypeError(f"Unknown SourceKey: {type(key).__name__}")

    # ------------------------------------------------------------------
    # Ebook resolution
    # ------------------------------------------------------------------

    def _resolve_book(self, book_id: str) -> ReadingSource | None:
        try:
            book = book_storage.get_book(book_id)
        except Exception:  # noqa: BLE001 — NB1 unified failure policy
            _logger.warning(
                "ebook get_book failed",
                extra={"category": "ebook_get_book_failed", "book_id": book_id},
            )
            return None
        if book is None:
            return None

        blob_lang: str = "en" if book.has_original else "bilingual"
        try:
            blob = book_storage.read_book_blob(book_id, lang=blob_lang)
            metadata = extract_metadata(blob)
        except Exception:  # noqa: BLE001 — NB1 unified failure policy
            _logger.warning(
                "ebook blob read or metadata extract failed",
                extra={
                    "category": "ebook_blob_read_failed",
                    "book_id": book_id,
                    "blob_lang": blob_lang,
                },
            )
            return None

        primary_lang = _normalize_primary_lang(metadata.lang)

        variants: list[SourceVariant] = []
        if book.has_original:
            variants.append(
                SourceVariant(
                    role="original",
                    format="epub",
                    lang=primary_lang,
                    path=f"data/books/{book_id}/original.epub",
                )
            )
            variants.append(
                SourceVariant(
                    role="display",
                    format="epub",
                    lang="bilingual",
                    path=f"data/books/{book_id}/bilingual.epub",
                )
            )
        else:
            variants.append(
                SourceVariant(
                    role="display",
                    format="epub",
                    lang=primary_lang,
                    path=f"data/books/{book_id}/bilingual.epub",
                )
            )

        evidence_reason = None if book.has_original else "no_original_uploaded"

        # NB3 contract — Book.lang_pair is deliberately NOT passed through.
        meta_dict: dict[str, str] = {}
        if book.isbn:
            meta_dict["isbn"] = book.isbn
        if book.published_year is not None:
            meta_dict["published_year"] = str(book.published_year)
        if metadata.lang:
            meta_dict["original_metadata_lang"] = metadata.lang

        return ReadingSource(
            source_id=f"ebook:{book_id}",
            annotation_key=book_id,
            kind="ebook",
            title=book.title,
            author=book.author,
            primary_lang=primary_lang,
            has_evidence_track=book.has_original,
            evidence_reason=evidence_reason,
            variants=variants,
            metadata=meta_dict,
        )

    # ------------------------------------------------------------------
    # Inbox resolution
    # ------------------------------------------------------------------

    def _resolve_inbox(self, relative_path: str) -> ReadingSource | None:
        logical_original = _logical_original(relative_path)
        logical_bilingual = _logical_bilingual(logical_original)

        # Path-traversal guard. Resolve the candidate inputs against the
        # vault root and confirm they stay inside.
        vault_resolved = self._vault.resolve()
        for candidate in (relative_path, logical_original, logical_bilingual):
            target = (self._vault / candidate).resolve()
            try:
                target.relative_to(vault_resolved)
            except ValueError as exc:
                raise ValueError(f"InboxKey path escapes vault: {relative_path!r}") from exc

        original_path = self._vault / logical_original
        bilingual_path = self._vault / logical_bilingual
        original_exists = original_path.is_file()
        bilingual_exists = bilingual_path.is_file()
        if not original_exists and not bilingual_exists:
            return None

        # Pick user-facing sibling (matches _get_inbox_files collapse rule).
        user_facing_path = bilingual_path if bilingual_exists else original_path

        try:
            user_facing_text = read_text(user_facing_path)
            user_facing_fm, _ = _strict_parse_frontmatter(user_facing_text)
        except (OSError, yaml.YAMLError):
            _logger.warning(
                "inbox frontmatter parse failed",
                extra={
                    "category": "inbox_frontmatter_parse_failed",
                    "relative_path": relative_path,
                },
            )
            return None

        slug = annotation_slug(user_facing_path.name, user_facing_fm)
        if not slug:
            # NB4 contract — defensive: never emit a ReadingSource with empty
            # annotation_key. annotation_slug currently falls back to
            # "untitled", but if a future change drops that fallback we want
            # an early loud failure instead of a silently broken join key.
            raise ValueError(f"annotation_slug returned empty for inbox path {relative_path!r}")

        # Determine the original-side frontmatter for primary_lang. In case
        # (b) bilingual-only, primary_lang derives from the bilingual sibling
        # frontmatter and is documented as low-confidence (NB2).
        if original_exists:
            try:
                original_text = read_text(original_path)
                original_fm, _ = _strict_parse_frontmatter(original_text)
            except (OSError, yaml.YAMLError):
                _logger.warning(
                    "inbox frontmatter parse failed",
                    extra={
                        "category": "inbox_frontmatter_parse_failed",
                        "relative_path": str(original_path.relative_to(self._vault)),
                    },
                )
                return None
            lang_source_fm = original_fm
        else:
            lang_source_fm = user_facing_fm

        primary_lang = _normalize_primary_lang(lang_source_fm.get("lang"))

        # Build variants — three cases (a) plain-only / (b) bilingual-only /
        # (c) both per plan §4.3.
        variants: list[SourceVariant] = []
        if original_exists and not bilingual_exists:
            variants.append(
                SourceVariant(
                    role="original",
                    format="markdown",
                    lang=primary_lang,
                    path=logical_original,
                )
            )
            has_evidence_track = True
            evidence_reason = None
        elif bilingual_exists and not original_exists:
            variants.append(
                SourceVariant(
                    role="display",
                    format="markdown",
                    lang="bilingual",
                    path=logical_bilingual,
                )
            )
            has_evidence_track = False
            evidence_reason = "bilingual_only_inbox"
        else:  # both exist
            variants.append(
                SourceVariant(
                    role="original",
                    format="markdown",
                    lang=primary_lang,
                    path=logical_original,
                )
            )
            variants.append(
                SourceVariant(
                    role="display",
                    format="markdown",
                    lang="bilingual",
                    path=logical_bilingual,
                )
            )
            has_evidence_track = True
            evidence_reason = None

        # F7 fix: fall back to the *logical original* stem so the
        # `-bilingual` suffix never leaks into ReadingSource.title for
        # bilingual-only docs missing a frontmatter title. logical_original
        # collapses both siblings to the plain stem, so plain-only / both
        # cases keep the same fallback they had before.
        title = str(user_facing_fm.get("title") or Path(logical_original).stem)
        author_raw = user_facing_fm.get("author")
        author = str(author_raw) if author_raw else None

        meta_dict: dict[str, str] = {}
        for k in ("original_url", "fulltext_layer", "fulltext_source"):
            v = user_facing_fm.get(k)
            if v:
                meta_dict[k] = str(v)

        return ReadingSource(
            source_id=f"inbox:{logical_original}",
            annotation_key=slug,
            kind="inbox_document",
            title=title,
            author=author,
            primary_lang=primary_lang,
            has_evidence_track=has_evidence_track,
            evidence_reason=evidence_reason,
            variants=variants,
            metadata=meta_dict,
        )

    # ------------------------------------------------------------------
    # YouTube resolution (ADR-035 §D1)
    # ------------------------------------------------------------------

    def _resolve_youtube(self, video_id: str) -> ReadingSource | None:
        """Read ``Watchlist/youtube/{video_id}/manifest.json`` and build a
        ``ReadingSource(kind='youtube_video', schema_version=2)``.

        Per NB1: missing entry / parse failure / schema mismatch → ``None``
        + WARNING log; never propagates exceptions.

        Path traversal: video_id is restricted to YouTube's documented
        id alphabet (``[A-Za-z0-9_-]``). Any ``/`` / ``\\`` / ``.`` /
        whitespace etc. raises ``ValueError`` before touching the
        filesystem. Mirrors the ``InboxKey`` path-escape guard but
        cheaper (id is small and well-defined).
        """
        if not _VALID_YOUTUBE_ID.fullmatch(video_id):
            raise ValueError(
                f"YouTubeKey path escapes vault: video_id={video_id!r} "
                f"(must match {_VALID_YOUTUBE_ID.pattern})"
            )

        entry_rel = f"Watchlist/youtube/{video_id}"
        entry_dir = self._vault / entry_rel

        # Symlink-escape guard (PR1b review F3) — mirrors the InboxKey
        # path-escape check. The alphabet guard above blocks ``..`` in
        # ``video_id`` but a symlink named with valid characters could
        # still redirect outside the vault. Only do the resolve dance if
        # the entry actually exists; missing entries are NB1 None.
        if entry_dir.is_dir() or entry_dir.is_symlink():
            vault_resolved = self._vault.resolve()
            try:
                entry_dir.resolve().relative_to(vault_resolved)
            except ValueError as exc:
                raise ValueError(
                    f"YouTubeKey path escapes vault via symlink: video_id={video_id!r}"
                ) from exc

        manifest_path = entry_dir / "manifest.json"
        if not manifest_path.is_file():
            return None

        try:
            entry = YouTubeWatchlistEntry.model_validate_json(manifest_path.read_bytes())
        except (OSError, ValidationError):
            _logger.warning(
                "youtube watchlist manifest parse failed",
                extra={
                    "category": "youtube_watchlist_parse_failed",
                    "video_id": video_id,
                },
            )
            return None

        if entry.video_id != video_id:
            # Cheap consistency check — directory name must match the
            # canonical id inside the manifest. Catches user-edited dirs
            # that drift from their content.
            _logger.warning(
                "youtube watchlist video_id mismatch",
                extra={
                    "category": "youtube_watchlist_video_id_mismatch",
                    "dir_video_id": video_id,
                    "manifest_video_id": entry.video_id,
                },
            )
            return None

        transcript_rel = f"{entry_rel}/{entry.transcript_path}"
        variant = SourceVariant(
            role="original",
            format="vtt",
            lang=entry.primary_lang,
            path=transcript_rel,
        )

        metadata: dict[str, str] = {
            "video_id": entry.video_id,
            "channel": entry.channel,
            "duration_s": str(entry.duration_s),
            "url": entry.url,
        }

        return ReadingSource(
            schema_version=2,
            source_id=f"youtube:{video_id}",
            annotation_key=f"youtube_{video_id}",
            kind="youtube_video",
            title=entry.title,
            author=entry.channel,
            primary_lang=entry.primary_lang,
            has_evidence_track=True,
            evidence_reason=None,
            variants=[variant],
            metadata=metadata,
            cast=list(entry.cast),
        )
