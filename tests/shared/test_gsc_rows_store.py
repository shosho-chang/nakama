"""Tests for shared/gsc_rows_store.py — deep-module CRUD + window queries.

Coverage (per task prompt §Acceptance):
- upsert_rows: empty input no-op
- upsert_rows: idempotent re-write (PRIMARY KEY conflict path overwrites, not appends)
- upsert_rows: distinct rows append rather than replace
- query: closed window range filter
- query: optional keyword + page filters
- query: swapped since/until raises
- rank_change_28d: impression-weighted average
- rank_change_28d: prev window has no rows → prev_avg_pos None + delta None
- rank_change_28d: today injection produces deterministic windows
- rank_change_28d: aggregates across (country, device) combos
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from shared import gsc_rows_store
from shared.schemas.seo import GSCRowV1

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _row(
    *,
    site: str = "sc-domain:shosho.tw",
    day: date,
    query: str = "肌酸 功效",
    page: str = "https://shosho.tw/blog/creatine",
    country: str = "twn",
    device: str = "desktop",
    clicks: int = 5,
    impressions: int = 100,
    position: float = 12.0,
) -> GSCRowV1:
    ctr = clicks / impressions if impressions > 0 else 0.0
    return GSCRowV1(
        site=site,
        date=day,
        query=query,
        page=page,
        country=country,
        device=device,  # type: ignore[arg-type]
        clicks=clicks,
        impressions=impressions,
        ctr=ctr,
        position=position,
    )


# ---------------------------------------------------------------------------
# upsert_rows
# ---------------------------------------------------------------------------


def test_upsert_rows_empty_input_is_noop():
    assert gsc_rows_store.upsert_rows([]) == 0
    # Generator path
    assert gsc_rows_store.upsert_rows(iter(())) == 0


def test_upsert_rows_writes_distinct_rows():
    rows = [
        _row(day=date(2026, 4, 20), query="肌酸 功效"),
        _row(day=date(2026, 4, 20), query="肌酸 副作用"),
        _row(day=date(2026, 4, 21), query="肌酸 功效"),
    ]
    assert gsc_rows_store.upsert_rows(rows) == 3
    out = gsc_rows_store.query(
        site="sc-domain:shosho.tw",
        since=date(2026, 4, 20),
        until=date(2026, 4, 21),
    )
    assert len(out) == 3


def test_upsert_rows_idempotent_overwrites_existing_pk():
    """Re-running cron same day writes same PK → row count unchanged, latest values win."""
    site = "sc-domain:shosho.tw"
    pk_keys = {
        "day": date(2026, 4, 25),
        "query": "肌酸 功效",
        "page": "https://shosho.tw/blog/creatine",
        "country": "twn",
        "device": "desktop",
    }
    first = _row(**pk_keys, clicks=5, impressions=100, position=14.0)
    second = _row(**pk_keys, clicks=8, impressions=140, position=11.5)

    gsc_rows_store.upsert_rows([first])
    gsc_rows_store.upsert_rows([second])  # re-run — same PK

    out = gsc_rows_store.query(
        site=site,
        since=date(2026, 4, 25),
        until=date(2026, 4, 25),
    )
    assert len(out) == 1, "PK conflict path must overwrite, not append"
    only = out[0]
    assert only["clicks"] == 8
    assert only["impressions"] == 140
    assert only["position"] == 11.5


def test_upsert_rows_double_run_full_batch_idempotent():
    """Cron re-runs entire 7-day window twice → row count stays equal."""
    rows = [_row(day=date(2026, 4, 20) + timedelta(days=d), query="肌酸 功效") for d in range(7)]
    gsc_rows_store.upsert_rows(rows)
    initial = gsc_rows_store.query(
        site="sc-domain:shosho.tw",
        since=date(2026, 4, 20),
        until=date(2026, 4, 26),
    )
    assert len(initial) == 7

    # Re-run — exact same rows
    gsc_rows_store.upsert_rows(rows)
    after = gsc_rows_store.query(
        site="sc-domain:shosho.tw",
        since=date(2026, 4, 20),
        until=date(2026, 4, 26),
    )
    assert len(after) == 7, "idempotent re-run must not duplicate rows"


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


def test_query_filters_by_window():
    base = date(2026, 4, 1)
    rows = [_row(day=base + timedelta(days=d), query="x") for d in range(10)]
    gsc_rows_store.upsert_rows(rows)

    out = gsc_rows_store.query(
        site="sc-domain:shosho.tw",
        since=base + timedelta(days=2),
        until=base + timedelta(days=5),
    )
    assert [r["date"] for r in out] == [
        (base + timedelta(days=2)).isoformat(),
        (base + timedelta(days=3)).isoformat(),
        (base + timedelta(days=4)).isoformat(),
        (base + timedelta(days=5)).isoformat(),
    ]


def test_query_keyword_filter():
    day = date(2026, 4, 10)
    gsc_rows_store.upsert_rows(
        [
            _row(day=day, query="A"),
            _row(day=day, query="B"),
            _row(day=day, query="A", page="https://shosho.tw/blog/other"),
        ]
    )
    out = gsc_rows_store.query(
        site="sc-domain:shosho.tw",
        since=day,
        until=day,
        keyword="A",
    )
    assert {r["query"] for r in out} == {"A"}
    assert len(out) == 2


def test_query_page_filter():
    day = date(2026, 4, 10)
    page = "https://shosho.tw/blog/creatine"
    other_page = "https://shosho.tw/blog/sleep"
    gsc_rows_store.upsert_rows(
        [
            _row(day=day, query="A", page=page),
            _row(day=day, query="A", page=other_page),
        ]
    )
    out = gsc_rows_store.query(
        site="sc-domain:shosho.tw",
        since=day,
        until=day,
        page=page,
    )
    assert {r["page"] for r in out} == {page}


def test_query_swapped_window_raises():
    with pytest.raises(ValueError, match="after"):
        gsc_rows_store.query(
            site="sc-domain:shosho.tw",
            since=date(2026, 4, 5),
            until=date(2026, 4, 1),
        )


def test_query_returns_empty_when_no_data():
    assert (
        gsc_rows_store.query(
            site="sc-domain:shosho.tw",
            since=date(2026, 4, 1),
            until=date(2026, 4, 7),
        )
        == []
    )


def test_query_stable_ordering():
    """Result is sorted (date ASC, query ASC) — snapshot-friendly."""
    day = date(2026, 4, 10)
    gsc_rows_store.upsert_rows(
        [
            _row(day=day, query="zeta"),
            _row(day=day, query="alpha"),
            _row(day=day - timedelta(days=1), query="alpha"),
        ]
    )
    out = gsc_rows_store.query(
        site="sc-domain:shosho.tw",
        since=day - timedelta(days=1),
        until=day,
    )
    pairs = [(r["date"], r["query"]) for r in out]
    assert pairs == sorted(pairs)


# ---------------------------------------------------------------------------
# rank_change_28d
# ---------------------------------------------------------------------------


def test_rank_change_28d_impression_weighted_average():
    """Two rows in current window: position weighted by impressions."""
    today = date(2026, 4, 30)
    page = "https://shosho.tw/blog/creatine"
    keyword = "肌酸 功效"

    # current window: today-27 .. today
    # one row at impressions=100 position=10, one at impressions=200 position=4
    # weighted = (10*100 + 4*200) / (100+200) = 1800/300 = 6.0
    gsc_rows_store.upsert_rows(
        [
            _row(
                day=today - timedelta(days=5),
                query=keyword,
                page=page,
                impressions=100,
                position=10.0,
            ),
            _row(
                day=today - timedelta(days=2),
                query=keyword,
                page=page,
                impressions=200,
                position=4.0,
                device="mobile",
            ),
        ]
    )
    result = gsc_rows_store.rank_change_28d(
        keyword=keyword,
        url=page,
        today=today,
    )
    assert result.current_avg_pos == pytest.approx(6.0)
    assert result.current_impressions == 300
    assert result.prev_avg_pos is None
    assert result.delta is None


def test_rank_change_28d_aggregates_across_country_device():
    """Same (keyword, url) split across (country, device) is summed in the avg."""
    today = date(2026, 4, 30)
    page = "https://shosho.tw/blog/creatine"
    keyword = "肌酸 功效"

    # current: 4 rows, two day-pairs × two devices, total impressions = 400
    rows = [
        _row(
            day=today - timedelta(days=3),
            query=keyword,
            page=page,
            impressions=100,
            position=8.0,
            device="desktop",
            country="twn",
        ),
        _row(
            day=today - timedelta(days=3),
            query=keyword,
            page=page,
            impressions=100,
            position=12.0,
            device="mobile",
            country="twn",
        ),
        _row(
            day=today - timedelta(days=1),
            query=keyword,
            page=page,
            impressions=100,
            position=6.0,
            device="desktop",
            country="twn",
        ),
        _row(
            day=today - timedelta(days=1),
            query=keyword,
            page=page,
            impressions=100,
            position=10.0,
            device="mobile",
            country="twn",
        ),
    ]
    gsc_rows_store.upsert_rows(rows)
    result = gsc_rows_store.rank_change_28d(keyword=keyword, url=page, today=today)
    # weighted = (8 + 12 + 6 + 10) * 100 / 400 = 9.0
    assert result.current_avg_pos == pytest.approx(9.0)
    assert result.current_impressions == 400


def test_rank_change_28d_prev_and_current_with_delta():
    today = date(2026, 4, 30)
    page = "https://shosho.tw/blog/creatine"
    keyword = "肌酸 功效"

    # prev window: today-55 .. today-28
    # rank was 14
    gsc_rows_store.upsert_rows(
        [
            _row(
                day=today - timedelta(days=40),
                query=keyword,
                page=page,
                impressions=100,
                position=14.0,
            ),
            _row(
                day=today - timedelta(days=5),
                query=keyword,
                page=page,
                impressions=100,
                position=10.0,
            ),
        ]
    )
    result = gsc_rows_store.rank_change_28d(keyword=keyword, url=page, today=today)
    assert result.current_avg_pos == pytest.approx(10.0)
    assert result.prev_avg_pos == pytest.approx(14.0)
    # current - prev = 10 - 14 = -4 (rank improved)
    assert result.delta == pytest.approx(-4.0)


def test_rank_change_28d_no_rows_returns_none():
    """No data in either window → all None / 0."""
    result = gsc_rows_store.rank_change_28d(
        keyword="never seen",
        url="https://shosho.tw/blog/nothing",
        today=date(2026, 4, 30),
    )
    assert result.current_avg_pos is None
    assert result.prev_avg_pos is None
    assert result.delta is None
    assert result.current_impressions == 0


def test_rank_change_28d_today_defaults_to_utc(monkeypatch):
    """No `today` arg → today = datetime.now(UTC).date(). Smoke-test it returns
    a result without crashing (DB is empty — both windows None)."""
    result = gsc_rows_store.rank_change_28d(
        keyword="nope",
        url="https://shosho.tw/missing",
    )
    assert result.current_avg_pos is None
