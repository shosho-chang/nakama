"""Tests for shared.digest_indexer."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from shared.digest_indexer import (
    DIGEST_TYPES,
    ConflictFile,
    DigestIndexer,
    DigestNotFoundError,
)


def _today() -> str:
    return datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()


def _days_ago(n: int) -> str:
    d = datetime.now(ZoneInfo("Asia/Taipei")).date() - timedelta(days=n)
    return d.isoformat()


def _write(vault: Path, type_dir: str, date_: str, body: str = "") -> Path:
    d = vault / type_dir
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{date_}.md"
    f.write_text(body, encoding="utf-8")
    return f


PUBMED_SAMPLE = """---
date: '2026-05-24'
created_by: robin
selected_count: 12
editor_pick_count: 8
type: digest
---

# PubMed 每日精選 — 2026-05-24

> 今日精選以多篇發表於頂級期刊的研究為主。

**候選總數**：67　**入選**：12
"""

AI_SAMPLE = """---
date: '2026-05-23'
created_by: franky
selected_count: 4
type: digest
---

# AI 每日情報 — 2026-05-23

> 今日以 Claude Code 功能更新為主軸。
"""


class TestLatestPerType:
    def test_empty_vault(self, tmp_path):
        idx = DigestIndexer(tmp_path)
        assert idx.latest_per_type() == {"pubmed": None, "ai": None}

    def test_returns_newest(self, tmp_path):
        _write(tmp_path, "KB/Wiki/Digests/PubMed", "2026-05-20", PUBMED_SAMPLE)
        _write(tmp_path, "KB/Wiki/Digests/PubMed", "2026-05-24", PUBMED_SAMPLE)
        _write(tmp_path, "KB/Wiki/Digests/AI", "2026-05-23", AI_SAMPLE)
        idx = DigestIndexer(tmp_path)
        latest = idx.latest_per_type()
        assert latest["pubmed"].date == "2026-05-24"
        assert latest["pubmed"].selected_count == 12
        assert latest["pubmed"].editor_pick_count == 8
        assert latest["ai"].date == "2026-05-23"
        assert latest["ai"].selected_count == 4
        assert latest["ai"].editor_pick_count is None  # AI digests don't have this

    def test_summary_extracted(self, tmp_path):
        _write(tmp_path, "KB/Wiki/Digests/PubMed", "2026-05-24", PUBMED_SAMPLE)
        idx = DigestIndexer(tmp_path)
        entry = idx.latest_per_type()["pubmed"]
        assert "頂級期刊" in entry.summary


class TestLastNDays:
    def test_empty(self, tmp_path):
        idx = DigestIndexer(tmp_path)
        assert idx.last_n_days(n=7) == []

    def test_includes_present_skips_missing(self, tmp_path):
        _write(tmp_path, "KB/Wiki/Digests/PubMed", _today(), PUBMED_SAMPLE)
        _write(tmp_path, "KB/Wiki/Digests/AI", _days_ago(2), AI_SAMPLE)
        idx = DigestIndexer(tmp_path)
        entries = idx.last_n_days(n=7)
        dates_types = {(e.type, e.date) for e in entries}
        assert ("pubmed", _today()) in dates_types
        assert ("ai", _days_ago(2)) in dates_types
        assert len(entries) == 2

    def test_n_bound(self, tmp_path):
        _write(tmp_path, "KB/Wiki/Digests/PubMed", _days_ago(10), PUBMED_SAMPLE)
        _write(tmp_path, "KB/Wiki/Digests/PubMed", _days_ago(2), PUBMED_SAMPLE)
        idx = DigestIndexer(tmp_path)
        entries = idx.last_n_days(n=7)
        assert len(entries) == 1
        assert entries[0].date == _days_ago(2)


class TestGetAndLoad:
    def test_get_existing(self, tmp_path):
        _write(tmp_path, "KB/Wiki/Digests/PubMed", "2026-05-24", PUBMED_SAMPLE)
        idx = DigestIndexer(tmp_path)
        e = idx.get("pubmed", "2026-05-24")
        assert e.type == "pubmed"
        assert e.relative_path == "KB/Wiki/Digests/PubMed/2026-05-24.md"
        assert e.detail_url == "/bridge/digests/pubmed/2026-05-24"

    def test_get_missing_raises(self, tmp_path):
        idx = DigestIndexer(tmp_path)
        with pytest.raises(DigestNotFoundError):
            idx.get("pubmed", "2026-05-24")

    def test_get_unknown_type(self, tmp_path):
        idx = DigestIndexer(tmp_path)
        with pytest.raises(DigestNotFoundError):
            idx.get("podcast", "2026-05-24")

    def test_get_invalid_date(self, tmp_path):
        idx = DigestIndexer(tmp_path)
        with pytest.raises(DigestNotFoundError):
            idx.get("pubmed", "not-a-date")

    def test_load_text_strips_frontmatter(self, tmp_path):
        _write(tmp_path, "KB/Wiki/Digests/PubMed", "2026-05-24", PUBMED_SAMPLE)
        idx = DigestIndexer(tmp_path)
        text = idx.load_text("pubmed", "2026-05-24")
        assert "# PubMed 每日精選" in text
        assert "created_by: robin" not in text  # frontmatter removed
        assert "---" not in text.splitlines()[0]


class TestDigestTypes:
    def test_canonical_types(self):
        assert DIGEST_TYPES == ("pubmed", "ai")


class TestConflictFiles:
    def test_empty_when_no_conflicts(self, tmp_path):
        _write(tmp_path, "KB/Wiki/Digests/PubMed", "2026-05-24", PUBMED_SAMPLE)
        idx = DigestIndexer(tmp_path)
        assert idx.list_conflict_files() == []

    def test_detects_syncthing_conflict_file(self, tmp_path):
        d = tmp_path / "KB" / "Wiki" / "Digests" / "PubMed"
        d.mkdir(parents=True)
        (d / "2026-05-24.md").write_text(PUBMED_SAMPLE, encoding="utf-8")
        (d / "2026-05-24.sync-conflict-20260524-101530-WIN.md").write_text(
            "conflict body", encoding="utf-8"
        )
        idx = DigestIndexer(tmp_path)
        conflicts = idx.list_conflict_files()
        assert len(conflicts) == 1
        c = conflicts[0]
        assert isinstance(c, ConflictFile)
        assert c.type == "pubmed"
        assert c.original_date == "2026-05-24"
        assert c.relative_path == (
            "KB/Wiki/Digests/PubMed/2026-05-24.sync-conflict-20260524-101530-WIN.md"
        )
        assert c.conflict_timestamp == "20260524-101530"
        assert c.device == "WIN"

    def test_newest_conflict_first(self, tmp_path):
        d = tmp_path / "KB" / "Wiki" / "Digests" / "AI"
        d.mkdir(parents=True)
        (d / "2026-05-20.sync-conflict-20260520-093000-MAC.md").write_text("a", encoding="utf-8")
        (d / "2026-05-24.sync-conflict-20260524-101530-WIN.md").write_text("b", encoding="utf-8")
        idx = DigestIndexer(tmp_path)
        ts = [c.conflict_timestamp for c in idx.list_conflict_files()]
        assert ts == ["20260524-101530", "20260520-093000"]

    def test_regular_digest_not_classified_as_conflict(self, tmp_path):
        _write(tmp_path, "KB/Wiki/Digests/PubMed", "2026-05-24", PUBMED_SAMPLE)
        idx = DigestIndexer(tmp_path)
        assert idx.list_conflict_files() == []

    def test_unrelated_file_ignored(self, tmp_path):
        d = tmp_path / "KB" / "Wiki" / "Digests" / "PubMed"
        d.mkdir(parents=True)
        (d / "README.md").write_text("x", encoding="utf-8")
        (d / "2026-05-24-draft.md").write_text("x", encoding="utf-8")
        idx = DigestIndexer(tmp_path)
        assert idx.list_conflict_files() == []
