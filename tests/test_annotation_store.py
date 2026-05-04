"""Unit tests for shared.annotation_store (PRD #337 Slice 1).

Coverage incremental — TDD vertical slicing.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shared.annotation_store import AnnotationStore, compute_annotation_slug
from shared.schemas.annotation import Annotation, AnnotationSet, Highlight


@pytest.fixture
def store(tmp_path, monkeypatch):
    """AnnotationStore 注入 tmp_path 當 vault root。"""
    monkeypatch.setattr("shared.annotation_store.get_vault_path", lambda: tmp_path)
    return AnnotationStore()


def _make_set(slug: str = "sleep-paper") -> AnnotationSet:
    ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
    return AnnotationSet(
        source_slug=slug,
        source_path=f"Inbox/kb/{slug}.md",
        marks=[
            Highlight(id="hl-1", reftext="Sleep is essential", created_at=ts, modified_at=ts),
            Annotation(
                id="ann-1",
                reftext="REM sleep particularly relates to procedural memory",
                note="這跟 hippocampus 在 NREM 的 SWR 配對嗎？",
                created_at=ts,
                modified_at=ts,
            ),
        ],
    )


class TestAnnotationStoreRoundTrip:
    def test_save_then_load_returns_equivalent_set(self, store):
        s = _make_set("sleep-paper")
        store.save(s)
        loaded = store.load("sleep-paper")
        assert loaded == s

    def test_load_missing_slug_returns_none(self, store):
        assert store.load("never-saved") is None


class TestAnnotationStoreDelete:
    def test_delete_removes_single_mark(self, store):
        s = _make_set("sleep-paper")
        store.save(s)
        assert len(store.load("sleep-paper").marks) == 2

        store.delete("sleep-paper", "ann-1")

        loaded = store.load("sleep-paper")
        assert len(loaded.marks) == 1
        assert loaded.marks[0].id == "hl-1"


class TestAnnotationStoreSyncBookkeeping:
    def test_mark_synced_updates_last_synced_at(self, store):
        s = _make_set("sleep-paper")
        store.save(s)
        assert store.load("sleep-paper").last_synced_at is None

        sync_ts = datetime(2026, 5, 4, 18, 30, 0, tzinfo=timezone.utc)
        store.mark_synced("sleep-paper", sync_ts)

        assert store.load("sleep-paper").last_synced_at == sync_ts


class TestAnnotationStoreUnsyncedCount:
    def test_never_synced_counts_all_marks(self, store):
        """last_synced_at=None → 全部 marks 都未 sync。"""
        s = _make_set("sleep-paper")
        store.save(s)
        assert store.unsynced_count("sleep-paper") == 2

    def test_after_sync_no_changes_count_zero(self, store):
        """sync 之後沒新動作 → unsynced=0。"""
        s = _make_set("sleep-paper")
        store.save(s)
        # sync_ts 必須晚於所有 marks 的 modified_at（_make_set 用 12:00）
        store.mark_synced("sleep-paper", datetime(2026, 5, 4, 13, 0, 0, tzinfo=timezone.utc))
        assert store.unsynced_count("sleep-paper") == 0

    def test_marks_modified_after_sync_are_counted(self, store):
        """sync 之後修改部分 marks → 只算修改過的。"""
        s = _make_set("sleep-paper")
        store.save(s)
        store.mark_synced("sleep-paper", datetime(2026, 5, 4, 13, 0, 0, tzinfo=timezone.utc))

        loaded = store.load("sleep-paper")
        loaded.marks[0].modified_at = datetime(2026, 5, 4, 14, 0, 0, tzinfo=timezone.utc)
        store.save(loaded)

        assert store.unsynced_count("sleep-paper") == 1

    def test_missing_slug_returns_zero(self, store):
        assert store.unsynced_count("never-saved") == 0


class TestComputeAnnotationSlug:
    """slug 對齊 KB/Wiki/Sources/{slug}.md 命名規則 — Q3 凍結 source-anchored 一檔。"""

    def test_inbox_strips_extension(self):
        assert compute_annotation_slug("inbox", "sleep-paper.md") == "sleep-paper"

    def test_inbox_normalizes_spaces_to_dashes(self):
        assert compute_annotation_slug("inbox", "sleep paper draft.md") == "sleep-paper-draft"

    def test_sources_pubmed_bilingual_kept_as_is(self):
        assert (
            compute_annotation_slug("sources", "pubmed-12345-bilingual.md")
            == "pubmed-12345-bilingual"
        )

    def test_cjk_filename_preserved(self):
        assert compute_annotation_slug("inbox", "睡眠論文.md") == "睡眠論文"
