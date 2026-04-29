"""GSC rows store — deep module over state.db `gsc_rows` (ADR-008 §2 / Phase 2a-min).

Three-method public API:

    upsert_rows(rows)               # idempotent UPSERT
    query(site, since, until, ...)  # window query for digest / dashboard
    rank_change_28d(keyword, url)   # rolling 28-day rank delta for SEO 中控台 v1.1

All other helpers are private (`_*`). Caller does not see SQL strings, primary
key composition, or aggregation maths — that is the deep impl behind the short
interface (Ousterhout). Mock the three methods, not the SQL.

Persisted shape lives in `shared/schemas/seo.py` `GSCRowV1`. Schema fields and
the SQLite column layout move together; UPSERT path is the single SoT for both
sides of that contract.

The cron (`agents/franky` GSC daily) is the only writer in Phase 2a-min;
read-side consumers will be `/bridge/seo` (v1.1 rank change panel) and the
forthcoming Phase 2a-full weekly digest.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from shared.log import get_logger
from shared.schemas.seo import GSCRowV1
from shared.state import _get_conn

logger = get_logger("nakama.gsc_rows_store")

_ROLLING_WINDOW_DAYS = 28


# ---------------------------------------------------------------------------
# Read-side return shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RankChange28d:
    """Rolling 28d rank-change snapshot for one (keyword, url) pair.

    Both windows are end-aligned to "today" of the caller's view. `delta` is
    `current_avg_pos - prev_avg_pos` (positive = rank got worse, negative =
    rank improved). When a window has no rows we return ``None`` to make the
    "no data yet" case unambiguous (do not silently coerce to 0 / NaN).
    """

    keyword: str
    url: str
    current_avg_pos: Optional[float]
    prev_avg_pos: Optional[float]
    delta: Optional[float]
    current_impressions: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def upsert_rows(rows: Iterable[GSCRowV1]) -> int:
    """UPSERT a batch of `GSCRowV1` rows. Returns the count actually written.

    Idempotent: re-running the cron on the same day overwrites rows under the
    same primary key (`site`, `date`, `query`, `page`, `country`, `device`)
    rather than appending. `fetched_at` is set to "now" (UTC) on every write
    so callers can observe data freshness.

    Empty input is a no-op (returns 0). Validation errors propagate (rows is
    already typed as `GSCRowV1` so callers must build valid models upstream).
    """
    rows = list(rows)
    if not rows:
        return 0

    conn = _get_conn()
    fetched_at = datetime.now(timezone.utc).isoformat()
    payload = [
        (
            r.site,
            r.date.isoformat(),
            r.query,
            r.page,
            r.country,
            r.device,
            r.clicks,
            r.impressions,
            r.ctr,
            r.position,
            fetched_at,
        )
        for r in rows
    ]
    conn.executemany(
        """
        INSERT INTO gsc_rows (
            site, date, query, page, country, device,
            clicks, impressions, ctr, position, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(site, date, query, page, country, device) DO UPDATE SET
            clicks      = excluded.clicks,
            impressions = excluded.impressions,
            ctr         = excluded.ctr,
            position    = excluded.position,
            fetched_at  = excluded.fetched_at
        """,
        payload,
    )
    conn.commit()
    logger.info("gsc_rows_upsert site_distinct=%d count=%d", len({r.site for r in rows}), len(rows))
    return len(rows)


def query(
    *,
    site: str,
    since: date,
    until: date,
    keyword: Optional[str] = None,
    page: Optional[str] = None,
) -> list[dict]:
    """Window query over `gsc_rows`. Closed range ``[since, until]``.

    Returns a list of dicts (one per row) with keys matching `GSCRowV1`
    fields plus `fetched_at`. Caller decides whether to re-validate as
    `GSCRowV1`; we do not coerce so the read path stays cheap.

    Filters:
        site:    GSC property string ('sc-domain:shosho.tw'); always required.
        keyword: optional exact-match filter on `query` column.
        page:    optional exact-match filter on `page` column.

    Result is ordered by `date ASC, query ASC` — stable for snapshots / tests.
    """
    if since > until:
        # Avoid silently empty result on swapped args.
        raise ValueError(f"since ({since}) is after until ({until})")

    conn = _get_conn()
    sql = (
        "SELECT site, date, query, page, country, device, "
        "       clicks, impressions, ctr, position, fetched_at "
        "FROM gsc_rows "
        "WHERE site = ? AND date BETWEEN ? AND ?"
    )
    params: list = [site, since.isoformat(), until.isoformat()]
    if keyword is not None:
        sql += " AND query = ?"
        params.append(keyword)
    if page is not None:
        sql += " AND page = ?"
        params.append(page)
    sql += " ORDER BY date ASC, query ASC"

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def rank_change_28d(
    *,
    keyword: str,
    url: str,
    today: Optional[date] = None,
) -> RankChange28d:
    """Compute current vs. prior 28-day average rank for one (keyword, url).

    Windows (closed ranges, both inclusive):
        current = ``[today - 27, today]``
        prev    = ``[today - 55, today - 28]``

    Average position is **impression-weighted** within each window — a row
    with 1 impression at position 1.0 should not outweigh a row with 100
    impressions at position 12.0. Falls back to the simple mean when the
    window's total impressions is 0 (rows still exist but everyone got 0
    impressions, edge case).

    `today` defaults to today's UTC date. Caller passes a Taipei-derived
    date when running from the `/bridge/seo` request handler so the rolling
    window aligns to the user's calendar day.

    Returns a `RankChange28d` (frozen). When a window has zero rows the
    corresponding `*_avg_pos` is ``None`` and `delta` is ``None`` — the
    caller should render "—" / "no data" rather than 0.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()
    from datetime import timedelta

    cur_start = today - timedelta(days=_ROLLING_WINDOW_DAYS - 1)
    cur_end = today
    prev_start = today - timedelta(days=2 * _ROLLING_WINDOW_DAYS - 1)
    prev_end = today - timedelta(days=_ROLLING_WINDOW_DAYS)

    cur_avg, cur_impr = _avg_position_in_window(keyword, url, cur_start, cur_end)
    prev_avg, _prev_impr = _avg_position_in_window(keyword, url, prev_start, prev_end)

    delta: Optional[float]
    if cur_avg is not None and prev_avg is not None:
        delta = cur_avg - prev_avg
    else:
        delta = None

    return RankChange28d(
        keyword=keyword,
        url=url,
        current_avg_pos=cur_avg,
        prev_avg_pos=prev_avg,
        delta=delta,
        current_impressions=cur_impr,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _avg_position_in_window(
    keyword: str,
    url: str,
    start: date,
    end: date,
) -> tuple[Optional[float], int]:
    """Aggregate (impression-weighted avg_position, total_impressions) for the
    given (keyword, url) over [start, end]. Returns ``(None, 0)`` when no rows.

    Aggregates across all `(country, device)` combos in the window — the
    rank-change panel cares about the headline number, not per-device split.
    """
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT
            SUM(impressions) AS total_impressions,
            SUM(position * impressions) AS weighted_pos_sum,
            SUM(position) AS plain_pos_sum,
            COUNT(*) AS row_count
        FROM gsc_rows
        WHERE query = ? AND page = ?
          AND date BETWEEN ? AND ?
        """,
        (keyword, url, start.isoformat(), end.isoformat()),
    ).fetchone()

    row_count = row["row_count"] if row else 0
    if not row_count:
        return (None, 0)

    impressions = int(row["total_impressions"] or 0)
    if impressions > 0:
        weighted_sum = float(row["weighted_pos_sum"] or 0.0)
        return (weighted_sum / impressions, impressions)

    # Edge case: rows exist but all have impressions=0 (rare; usually means
    # GSC showed a click without impressions, which shouldn't happen but the
    # data is what it is). Fall back to the plain mean.
    plain_sum = float(row["plain_pos_sum"] or 0.0)
    return (plain_sum / row_count, 0)


__all__ = [
    "RankChange28d",
    "upsert_rows",
    "query",
    "rank_change_28d",
]
