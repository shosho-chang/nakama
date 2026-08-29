"""Sanji 本地狀態（cursor／打卡投影／判定佇列）的行為測試。tmp sqlite，不碰 state.db。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.sanji.store import Store


@pytest.fixture()
def store(tmp_path: Path):
    s = Store(db_path=tmp_path / "test.db")
    yield s
    s.close()


def test_cursor_roundtrip(store: Store):
    assert store.get_cursor("events") == 0
    store.set_cursor("events", 123)
    assert store.get_cursor("events") == 123
    store.set_cursor("events", 456)
    assert store.get_cursor("events") == 456


def test_streak_counts_consecutive_days_only(store: Store):
    for day in ("2026-10-01", "2026-10-02", "2026-10-03", "2026-10-05"):
        store.record_checkin_day(42, day, "2026Q4", feed_id=1)
    assert store.current_streak(42, "2026-10-03") == 3
    assert store.current_streak(42, "2026-10-05") == 1  # 10-04 斷檔
    assert store.current_streak(42, "2026-10-04") == 0


def test_checkin_day_idempotent(store: Store):
    store.record_checkin_day(42, "2026-10-01", "2026Q4", feed_id=1)
    store.record_checkin_day(42, "2026-10-01", "2026Q4", feed_id=99)  # 同日第二篇
    assert store.current_streak(42, "2026-10-01") == 1


def test_judgment_queue_lifecycle(store: Store):
    assert store.enqueue_judgment(1001, feed_id=5, user_id=42, day="2026-10-01", season="2026Q4")
    assert not store.enqueue_judgment(
        1001, feed_id=5, user_id=42, day="2026-10-01", season="2026Q4"
    )

    pending = store.pending()
    assert len(pending) == 1 and pending[0]["event_id"] == 1001

    store.decide(1001, "approved", note="haiku:pass")
    assert store.pending() == []


def test_fail_open_scan_finds_stale_pending(store: Store):
    store.enqueue_judgment(1002, feed_id=6, user_id=42, day="2026-10-01", season="2026Q4")
    # 剛排入 → 未滿門檻
    assert store.pending_older_than_hours(48) == []
    # 0 小時門檻 → 立即命中（驗證 SQL 時間比較方向正確）
    stale = store.pending_older_than_hours(0)
    assert [r["event_id"] for r in stale] == [1002]
