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
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from shared.config import get_vault_path

# ── Schema ────────────────────────────────────────────────────────────────────


class Highlight(BaseModel):
    type: Literal["highlight"] = "highlight"
    text: str
    created_at: str = Field(default_factory=lambda: _now_iso())
    modified_at: str = Field(default_factory=lambda: _now_iso())


class Annotation(BaseModel):
    type: Literal["annotation"] = "annotation"
    ref: str
    note: str
    created_at: str = Field(default_factory=lambda: _now_iso())
    modified_at: str = Field(default_factory=lambda: _now_iso())


AnnotationItem = Annotated[Union[Highlight, Annotation], Field(discriminator="type")]


class AnnotationSet(BaseModel):
    slug: str
    source_filename: str
    base: str
    items: list[AnnotationItem] = Field(default_factory=list)
    updated_at: str = Field(default_factory=lambda: _now_iso())
    last_synced_at: str | None = None


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

    def save(self, ann_set: AnnotationSet) -> None:
        lock = _lock_for(ann_set.slug)
        with lock:
            d = _annotations_dir()
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{ann_set.slug}.md"
            path.write_text(_serialize(ann_set), encoding="utf-8")

    def load(self, slug: str) -> AnnotationSet | None:
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


def _serialize(ann_set: AnnotationSet) -> str:
    items_json = json.dumps(
        [item.model_dump() for item in ann_set.items],
        ensure_ascii=False,
        indent=2,
    )
    synced_line = f'last_synced_at: "{ann_set.last_synced_at}"\n' if ann_set.last_synced_at else ""
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


def _parse(text: str, slug: str) -> AnnotationSet:
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

    items: list[AnnotationItem] = []
    m = _JSON_BLOCK_RE.search(text)
    if m:
        for raw in json.loads(m.group(1)):
            t = raw.get("type")
            if t == "highlight":
                items.append(Highlight(**raw))
            elif t == "annotation":
                items.append(Annotation(**raw))

    return AnnotationSet(
        slug=fm.get("slug", slug),
        source_filename=fm.get("source", ""),
        base=fm.get("base", "inbox"),
        items=items,
        updated_at=fm.get("updated_at", _now_iso()),
        last_synced_at=fm.get("last_synced_at") or None,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
