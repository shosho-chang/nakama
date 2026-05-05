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
    AnnotationSetV1,
    AnnotationSetV2,
    AnnotationV1,
    AnnotationV2,
    CommentV2,
    HighlightV1,
    HighlightV2,
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


class AnnotationStore:
    """CRUD for annotation sets stored as ``KB/Annotations/{slug}.md``."""

    def save(self, ann_set: AnnotationSetV1 | AnnotationSetV2) -> None:
        lock = _lock_for(ann_set.slug)
        with lock:
            d = _annotations_dir()
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{ann_set.slug}.md"
            path.write_text(_serialize(ann_set), encoding="utf-8")

    def load(self, slug: str) -> AnnotationSetV1 | AnnotationSetV2 | None:
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


# ── Serialisation ─────────────────────────────────────────────────────────────

_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def _serialize(ann_set: AnnotationSetV1 | AnnotationSetV2) -> str:
    items_json = json.dumps(
        [item.model_dump() for item in ann_set.items],
        ensure_ascii=False,
        indent=2,
    )
    synced_line = f'last_synced_at: "{ann_set.last_synced_at}"\n' if ann_set.last_synced_at else ""
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


def _parse(text: str, slug: str) -> AnnotationSetV1 | AnnotationSetV2:
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

    is_v2 = fm.get("schema_version") == "2" or fm.get("base") == "books"

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
