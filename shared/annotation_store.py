"""Annotation persistence for the Reader — stores highlights and annotations
in KB/Annotations/{slug}.md, decoupled from the source file lifecycle.

ADR-017: annotation data lives in its own file; source files are never mutated
by the reader save path.
"""

from __future__ import annotations

import json
import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from shared.config import get_vault_path
from shared.schemas.annotations import (
    AnnotationItemV1,
    AnnotationItemV2,
    AnnotationItemV3,
    AnnotationSetV1,
    AnnotationSetV2,
    AnnotationSetV3,
    AnnotationV1,
    AnnotationV2,
    AnnotationV3,
    CommentV2,
    HighlightV1,
    HighlightV2,
    HighlightV3,
    ReflectionV3,
)

# ── Legacy-name aliases (existing callers import Highlight/Annotation/AnnotationSet) ──

Highlight = HighlightV1
Annotation = AnnotationV1
AnnotationItem = AnnotationItemV1
AnnotationSet = AnnotationSetV1

__all__ = [
    "Highlight",
    "Annotation",
    "AnnotationItem",
    "AnnotationSet",
    "HighlightV1",
    "AnnotationV1",
    "AnnotationItemV1",
    "AnnotationSetV1",
    "HighlightV2",
    "AnnotationV2",
    "CommentV2",
    "AnnotationItemV2",
    "AnnotationSetV2",
    "HighlightV3",
    "AnnotationV3",
    "ReflectionV3",
    "AnnotationItemV3",
    "AnnotationSetV3",
]


# ── Slug ──────────────────────────────────────────────────────────────────────


def annotation_slug(filename: str, frontmatter: dict | None = None) -> str:
    """Derive annotation slug from source.

    Priority:
    1. frontmatter ``title`` — already-ingested sources have stable titles
    2. filename stem — inbox fallback
    """
    if frontmatter and frontmatter.get("title"):
        raw = str(frontmatter["title"])
    else:
        raw = Path(filename).stem
    return _slugify(raw)


def _slugify(text: str) -> str:
    # NFC normalise, then lower-case ASCII, keep CJK and alphanumeric
    text = unicodedata.normalize("NFC", text).strip()
    # lower-case only ASCII letters (keeps CJK casing intact)
    text = "".join(c.lower() if c.isascii() else c for c in text)
    # remove characters that are not word-chars, CJK, whitespace, or hyphens
    text = re.sub(r"[^\w一-鿿぀-ヿ\s-]", "", text)
    # collapse whitespace/underscores to hyphens
    text = re.sub(r"[\s_]+", "-", text)
    # collapse repeated hyphens and strip leading/trailing
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "untitled"


# ── File location ─────────────────────────────────────────────────────────────


def _annotations_dir() -> Path:
    return get_vault_path() / "KB" / "Annotations"


# ── File locking (per-slug, process-local) ────────────────────────────────────

_slug_locks: dict[str, threading.Lock] = {}
_slug_locks_meta = threading.Lock()


def _lock_for(slug: str) -> threading.Lock:
    with _slug_locks_meta:
        if slug not in _slug_locks:
            _slug_locks[slug] = threading.Lock()
        return _slug_locks[slug]


# ── AnnotationStore ───────────────────────────────────────────────────────────


AnnotationSetAny = AnnotationSetV1 | AnnotationSetV2 | AnnotationSetV3


class AnnotationStore:
    """CRUD for annotation sets stored as ``KB/Annotations/{slug}.md``.

    ADR-021 §1: ``save`` accepts v1, v2, or v3 sets; the on-disk format is dispatched
    by the set's runtime type. New saves coming from the Reader (paper + book) go
    through v3; v1/v2 remain readable for backward compat until migrated.
    """

    def save(self, ann_set: AnnotationSetAny) -> None:
        lock = _lock_for(ann_set.slug)
        with lock:
            d = _annotations_dir()
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{ann_set.slug}.md"
            path.write_text(_serialize(ann_set), encoding="utf-8")

    def load(self, slug: str) -> AnnotationSetAny | None:
        path = _annotations_dir() / f"{slug}.md"
        if not path.exists():
            return None
        return _parse(path.read_text(encoding="utf-8"), slug)

    def delete(self, slug: str) -> None:
        lock = _lock_for(slug)
        with lock:
            path = _annotations_dir() / f"{slug}.md"
            if path.exists():
                path.unlink()

    def mark_synced(self, slug: str) -> None:
        """Persist last_synced_at timestamp so unsynced_count reflects post-sync state."""
        lock = _lock_for(slug)
        with lock:
            path = _annotations_dir() / f"{slug}.md"
            if not path.exists():
                return
            ann_set = _parse(path.read_text(encoding="utf-8"), slug)
            ann_set.last_synced_at = _now_iso()
            path.write_text(_serialize(ann_set), encoding="utf-8")

    def unsynced_count(self, slug: str) -> int:
        """Count items whose modified_at is after last_synced_at.

        Returns len(items) when never synced (last_synced_at is None), 0 when no file.
        """
        ann_set = self.load(slug)
        if ann_set is None:
            return 0
        if ann_set.last_synced_at is None:
            return len(ann_set.items)
        return sum(1 for item in ann_set.items if item.modified_at > ann_set.last_synced_at)


_store = AnnotationStore()


def get_annotation_store() -> AnnotationStore:
    return _store


# ── v1/v2 → v3 upgrade ───────────────────────────────────────────────────────
#
# ADR-021 §1: new saves go through v3. Reader UI clients currently still post
# v1 (paper) or v2 (book) shapes; we upgrade in-place at the save boundary so
# the on-disk store is uniformly v3 going forward. ``CommentV2`` (type ==
# "comment") becomes ``ReflectionV3`` (type == "reflection") — a vocabulary
# rename with no semantic shift. Migration script for existing v1 files lives
# in ``scripts/migrate_annotations_v3.py`` and reuses these helpers.


def _highlight_v1_to_v3(item: HighlightV1) -> HighlightV3:
    return HighlightV3(
        text=item.text,
        text_excerpt=item.text,  # paper highlights anchor by exact text
        created_at=item.created_at,
        modified_at=item.modified_at,
    )


def _annotation_v1_to_v3(item: AnnotationV1) -> AnnotationV3:
    return AnnotationV3(
        text_excerpt=item.ref,  # v1 ``ref`` was the anchor span
        ref=item.ref,
        note=item.note,
        created_at=item.created_at,
        modified_at=item.modified_at,
    )


def _highlight_v2_to_v3(item: HighlightV2) -> HighlightV3:
    return HighlightV3(
        cfi=item.cfi,
        text_excerpt=item.text_excerpt,
        book_version_hash=item.book_version_hash,
        text=item.text_excerpt,  # v2 stored only the excerpt; mirror it as body
        created_at=item.created_at,
        modified_at=item.modified_at,
    )


def _annotation_v2_to_v3(item: AnnotationV2) -> AnnotationV3:
    return AnnotationV3(
        cfi=item.cfi,
        text_excerpt=item.text_excerpt,
        book_version_hash=item.book_version_hash,
        note=item.note,
        created_at=item.created_at,
        modified_at=item.modified_at,
    )


def _comment_v2_to_reflection_v3(item: CommentV2) -> ReflectionV3:
    return ReflectionV3(
        chapter_ref=item.chapter_ref,
        cfi_anchor=item.cfi_anchor,
        book_version_hash=item.book_version_hash,
        body=item.body,
        created_at=item.created_at,
        modified_at=item.modified_at,
    )


def upgrade_to_v3(ann_set: AnnotationSetAny) -> AnnotationSetV3:
    """Return a v3 set equivalent to ``ann_set``. Already-v3 input is returned
    as-is so this is safe to call at every save boundary (idempotent)."""
    if isinstance(ann_set, AnnotationSetV3):
        return ann_set
    if isinstance(ann_set, AnnotationSetV1):
        items: list[AnnotationItemV3] = []
        for it in ann_set.items:
            if isinstance(it, HighlightV1):
                items.append(_highlight_v1_to_v3(it))
            elif isinstance(it, AnnotationV1):
                items.append(_annotation_v1_to_v3(it))
        return AnnotationSetV3(
            slug=ann_set.slug,
            base=ann_set.base,
            source_filename=ann_set.source_filename,
            items=items,
            updated_at=ann_set.updated_at,
            last_synced_at=ann_set.last_synced_at,
        )
    # v2
    items_v3: list[AnnotationItemV3] = []
    for it in ann_set.items:
        if isinstance(it, HighlightV2):
            items_v3.append(_highlight_v2_to_v3(it))
        elif isinstance(it, AnnotationV2):
            items_v3.append(_annotation_v2_to_v3(it))
        elif isinstance(it, CommentV2):
            items_v3.append(_comment_v2_to_reflection_v3(it))
    return AnnotationSetV3(
        slug=ann_set.slug,
        base="books",
        book_id=ann_set.book_id,
        book_version_hash=ann_set.book_version_hash,
        items=items_v3,
        updated_at=ann_set.updated_at,
        last_synced_at=ann_set.last_synced_at,
    )


# ── Serialisation ─────────────────────────────────────────────────────────────

_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def _serialize(ann_set: AnnotationSetAny) -> str:
    items_json = json.dumps(
        [item.model_dump() for item in ann_set.items],
        ensure_ascii=False,
        indent=2,
    )
    synced_line = f'last_synced_at: "{ann_set.last_synced_at}"\n' if ann_set.last_synced_at else ""
    if isinstance(ann_set, AnnotationSetV3):
        # ADR-021 §1: v3 frontmatter carries whichever headers the source provided
        # (paper → source_filename; book → book_id + book_version_hash). Empty fields
        # are omitted to keep the on-disk file lean.
        lines = [
            "---",
            f"slug: {ann_set.slug}",
            "schema_version: 3",
            f"base: {ann_set.base}",
        ]
        if ann_set.source_filename:
            lines.append(f"source: {ann_set.source_filename}")
        if ann_set.book_id:
            lines.append(f"book_id: {ann_set.book_id}")
        if ann_set.book_version_hash:
            lines.append(f"book_version_hash: {ann_set.book_version_hash}")
        lines.append(f'updated_at: "{ann_set.updated_at}"')
        if ann_set.last_synced_at:
            lines.append(f'last_synced_at: "{ann_set.last_synced_at}"')
        lines.append("---")
        return "\n".join(lines) + f"\n\n```json\n{items_json}\n```\n"
    if isinstance(ann_set, AnnotationSetV2):
        return (
            f"---\n"
            f"slug: {ann_set.slug}\n"
            f"schema_version: 2\n"
            f"book_id: {ann_set.book_id}\n"
            f"book_version_hash: {ann_set.book_version_hash}\n"
            f"base: books\n"
            f'updated_at: "{ann_set.updated_at}"\n'
            f"{synced_line}"
            f"---\n\n"
            f"```json\n{items_json}\n```\n"
        )
    return (
        f"---\n"
        f"slug: {ann_set.slug}\n"
        f"source: {ann_set.source_filename}\n"
        f"base: {ann_set.base}\n"
        f'updated_at: "{ann_set.updated_at}"\n'
        f"{synced_line}"
        f"---\n\n"
        f"```json\n{items_json}\n```\n"
    )


def _parse(text: str, slug: str) -> AnnotationSetAny:
    fm: dict[str, str] = {}
    if text.startswith("---"):
        try:
            end = text.index("---", 3)
        except ValueError:
            end = len(text)
        for line in text[3:end].splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip().strip('"')

    raw_items: list[dict] = []
    m = _JSON_BLOCK_RE.search(text)
    if m:
        raw_items = json.loads(m.group(1))

    schema_version = fm.get("schema_version")
    is_v3 = schema_version == "3"
    # ADR-021 §1: v3 dispatch comes BEFORE the v2 base==books fallback, otherwise a
    # v3 book-rooted set would be mis-identified as v2 just because its frontmatter
    # carries ``base: books``.
    if is_v3:
        items_v3: list[AnnotationItemV3] = []
        for raw in raw_items:
            t = raw.get("type")
            if t == "highlight":
                items_v3.append(HighlightV3(**raw))
            elif t == "annotation":
                items_v3.append(AnnotationV3(**raw))
            elif t == "reflection":
                items_v3.append(ReflectionV3(**raw))
        return AnnotationSetV3(
            slug=fm.get("slug", slug),
            base=fm.get("base", "inbox"),
            source_filename=fm.get("source") or None,
            book_id=fm.get("book_id") or None,
            book_version_hash=fm.get("book_version_hash") or None,
            items=items_v3,
            updated_at=fm.get("updated_at", _now_iso()),
            last_synced_at=fm.get("last_synced_at") or None,
        )

    is_v2 = schema_version == "2" or fm.get("base") == "books"

    if is_v2:
        items_v2: list[AnnotationItemV2] = []
        for raw in raw_items:
            t = raw.get("type")
            if t == "highlight":
                items_v2.append(HighlightV2(**raw))
            elif t == "annotation":
                items_v2.append(AnnotationV2(**raw))
            elif t == "comment":
                items_v2.append(CommentV2(**raw))
        return AnnotationSetV2(
            slug=fm.get("slug", slug),
            book_id=fm.get("book_id", slug),
            book_version_hash=fm.get("book_version_hash", ""),
            base="books",
            items=items_v2,
            updated_at=fm.get("updated_at", _now_iso()),
            last_synced_at=fm.get("last_synced_at") or None,
        )

    items_v1: list[AnnotationItemV1] = []
    for raw in raw_items:
        t = raw.get("type")
        if t == "highlight":
            items_v1.append(Highlight(**raw))
        elif t == "annotation":
            items_v1.append(Annotation(**raw))
    return AnnotationSet(
        slug=fm.get("slug", slug),
        source_filename=fm.get("source", ""),
        base=fm.get("base", "inbox"),
        items=items_v1,
        updated_at=fm.get("updated_at", _now_iso()),
        last_synced_at=fm.get("last_synced_at") or None,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
