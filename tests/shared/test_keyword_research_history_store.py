"""Round-trip tests for `shared.keyword_research_history_store` (Slice 2 / #258).

Hits the real sqlite via `shared.state.get_conn` with NAKAMA_STATE_DB_PATH
pointed at a tmp file (matches `tests/shared/test_audit_results_store.py`
pattern). No mocking — we want to confirm the schema actually accepts what
the module writes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import shared.state as state_module
from shared import keyword_research_history_store as kr_history


@pytest.fixture
def state_db(monkeypatch, tmp_path):
    """Pin NAKAMA_STATE_DB_PATH → tmp file; reset the global conn so this
    test gets its own database, isolated from any other test's writes.
    """
    db_path = tmp_path / "state.db"
    monkeypatch.setenv("NAKAMA_STATE_DB_PATH", str(db_path))
    # Force re-init of the cached connection on the next get_conn() call.
    state_module._conn = None  # type: ignore[attr-defined]
    yield db_path
    state_module._conn = None  # type: ignore[attr-defined]


def test_insert_returns_autoincrement_id(state_db):
    new_id = kr_history.insert_run(
        topic="間歇性斷食",
        en_topic="intermittent fasting",
        content_type="blog",
        report_md="# stub",
        triggered_by="web",
    )
    assert isinstance(new_id, int)
    assert new_id >= 1


def test_get_run_round_trips_all_fields(state_db):
    new_id = kr_history.insert_run(
        topic="深層睡眠",
        en_topic="deep sleep",
        content_type="youtube",
        report_md="## 標題\n\nbody body",
        triggered_by="web",
    )
    row = kr_history.get_run(new_id)
    assert row is not None
    assert row["id"] == new_id
    assert row["topic"] == "深層睡眠"
    assert row["en_topic"] == "deep sleep"
    assert row["content_type"] == "youtube"
    assert row["report_md"] == "## 標題\n\nbody body"
    assert row["triggered_by"] == "web"
    # created_at is a non-empty ISO string (defaulted to now-UTC by store)
    assert row["created_at"]


def test_get_run_returns_none_for_missing_id(state_db):
    assert kr_history.get_run(99999) is None


def test_en_topic_can_be_null(state_db):
    new_id = kr_history.insert_run(
        topic="topic-only",
        en_topic=None,  # auto-translated path
        content_type="blog",
        report_md="x",
        triggered_by="web",
    )
    row = kr_history.get_run(new_id)
    assert row is not None
    assert row["en_topic"] is None


def test_list_runs_sorted_desc_by_created_at(state_db):
    """Verify list_runs returns rows newest-first (created_at DESC)."""
    base = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    for i, label in enumerate(["a", "b", "c"]):
        kr_history.insert_run(
            topic=label,
            en_topic=None,
            content_type="blog",
            report_md=label,
            triggered_by="web",
            created_at=(base + timedelta(hours=i)).isoformat(),
        )
    rows = kr_history.list_runs()
    assert [r["topic"] for r in rows] == ["c", "b", "a"]


def test_count_and_list_pagination_offset(state_db):
    for i in range(5):
        kr_history.insert_run(
            topic=f"t{i}",
            en_topic=None,
            content_type="blog",
            report_md=str(i),
            triggered_by="web",
        )
    assert kr_history.count_runs() == 5
    page1 = kr_history.list_runs(limit=2, offset=0)
    page2 = kr_history.list_runs(limit=2, offset=2)
    page3 = kr_history.list_runs(limit=2, offset=4)
    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1
    # Combined topics across pages cover all 5 unique runs.
    seen = {r["topic"] for r in (page1 + page2 + page3)}
    assert seen == {"t0", "t1", "t2", "t3", "t4"}


def test_check_constraint_rejects_invalid_content_type(state_db):
    """Schema CHECK should reject content_type outside {blog, youtube}."""
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        kr_history.insert_run(
            topic="bad",
            en_topic=None,
            content_type="podcast",  # type: ignore[arg-type]
            report_md="x",
            triggered_by="web",
        )


def test_check_constraint_rejects_invalid_triggered_by(state_db):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        kr_history.insert_run(
            topic="bad",
            en_topic=None,
            content_type="blog",
            report_md="x",
            triggered_by="cron",  # type: ignore[arg-type]
        )


def test_to_taipei_display_converts_utc_to_taipei():
    # 2026-04-29T12:00:00Z is 2026-04-29 20:00 in Asia/Taipei (UTC+8)
    out = kr_history.to_taipei_display("2026-04-29T12:00:00+00:00")
    assert out == "2026-04-29 20:00"


def test_to_taipei_display_falls_back_for_unparseable_input():
    # Garbage string → fall back to truncate-and-replace pattern (no crash)
    out = kr_history.to_taipei_display("garbage-not-iso")
    assert isinstance(out, str)
