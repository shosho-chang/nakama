"""Annotation store — deep module over `KB/Annotations/{source-slug}.md` (PRD #337 Slice 1).

Public API (issue #338 acceptance):

    save(ann_set)                      # write AnnotationSet to KB/Annotations/{slug}.md
    load(source_slug)                  # read back as AnnotationSet (or None if missing)
    delete(source_slug, mark_id)       # remove single mark
    unsynced_count(source_slug)        # marks with modified_at > last_synced_at (Slice 3)
    mark_synced(source_slug, ts)       # update last_synced_at after Robin sync (Slice 2)

Persistence shape：YAML frontmatter 含整份 AnnotationSet（schema_version /
source_slug / source_path / last_synced_at / marks list）；body 留 stub
（修修在 Obsidian vault 翻 markdown 時看不到亂碼，但 source of truth 在 frontmatter）。

Tests live in `tests/test_annotation_store.py`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

from shared.config import get_vault_path
from shared.schemas.annotation import AnnotationSet
from shared.utils import slugify
from shared.vault_rules import ANNOTATIONS_PREFIX

# 路徑由 vault_rules.ANNOTATIONS_PREFIX 凍結（含結尾 /），存檔時 strip。
KB_ANNOTATIONS_DIR = ANNOTATIONS_PREFIX.rstrip("/")


def compute_annotation_slug(base: str, filename: str) -> str:
    """Reader (base, filename) → KB/Annotations/{slug}.md 的 slug。

    Q3 凍結 KB/Annotations/{slug}.md 跟 source 一檔對應；slug 對齊
    KB/Wiki/Sources/{slug}.md 命名規則（PubMed 雙語版 stem 沿用、Inbox 走 filename slugify）。
    """
    stem = Path(filename).stem
    return slugify(stem)


class AnnotationStore:
    def _file_path(self, source_slug: str) -> Path:
        return get_vault_path() / KB_ANNOTATIONS_DIR / f"{source_slug}.md"

    def save(self, ann_set: AnnotationSet) -> Path:
        target = self._file_path(ann_set.source_slug)
        target.parent.mkdir(parents=True, exist_ok=True)
        fm = ann_set.model_dump(mode="json")
        fm_str = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False)
        body_stub = (
            f"# Annotations for [[{ann_set.source_slug}]]\n\n"
            "_This file is the source of truth for highlights and annotations. "
            "Managed by Robin — do not edit by hand._\n"
        )
        content = f"---\n{fm_str}---\n\n{body_stub}"
        target.write_text(content, encoding="utf-8")
        return target

    def load(self, source_slug: str) -> AnnotationSet | None:
        target = self._file_path(source_slug)
        if not target.exists():
            return None
        text = target.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return None
        end = text.index("\n---", 4)
        fm_str = text[4:end]
        fm = yaml.safe_load(fm_str)
        return AnnotationSet.model_validate(fm)

    def delete(self, source_slug: str, mark_id: str) -> None:
        existing = self.load(source_slug)
        if existing is None:
            return
        existing.marks = [m for m in existing.marks if m.id != mark_id]
        self.save(existing)

    def mark_synced(self, source_slug: str, ts: datetime) -> None:
        existing = self.load(source_slug)
        if existing is None:
            return
        existing.last_synced_at = ts
        self.save(existing)

    def unsynced_count(self, source_slug: str) -> int:
        existing = self.load(source_slug)
        if existing is None:
            return 0
        if existing.last_synced_at is None:
            return len(existing.marks)
        return sum(1 for m in existing.marks if m.modified_at > existing.last_synced_at)
