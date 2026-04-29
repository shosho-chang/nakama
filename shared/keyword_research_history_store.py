"""Storage façade for `keyword_research_runs` — Slice 2 (#258 / A′).

PRD #255: persist each web-UI keyword-research run so the user can browse
history (list page) and re-download a report (detail page). Schema lives in
`migrations/007_keyword_research_runs.sql`; canonical DDL is also in
`shared/state.py::_init_tables` for the dual-source idempotent pattern.

Module split rationale: keep the SQL behind a small set of named functions
(`insert_run` / `list_runs` / `count_runs` / `get_run`) so the bridge
router can stay HTTP-shaped and tests can target this module directly with
an in-memory sqlite (see `tests/shared/test_keyword_research_history_store.py`).

Timestamps stored UTC ISO 8601 (matching `audit_results.audited_at`); display
layer converts to Asia/Taipei via `to_taipei_display`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional, TypedDict
from zoneinfo import ZoneInfo

from shared.state import _get_conn

TriggeredBy = Literal["web", "lifeos"]
ContentType = Literal["blog", "youtube"]


class KeywordResearchRunRow(TypedDict):
    """Shape returned by `list_runs` / `get_run`. Mirrors the table columns."""

    id: int
    topic: str
    en_topic: Optional[str]
    content_type: str
    report_md: str
    created_at: str  # ISO 8601 UTC
    triggered_by: str


def _now_utc_iso() -> str:
    """UTC ISO 8601 — same convention as `audit_results.audited_at`."""
    return datetime.now(timezone.utc).isoformat()


def to_taipei_display(iso_utc: str) -> str:
    """Convert a UTC ISO 8601 string to a `YYYY-MM-DD HH:MM` Taipei display.

    Falls back to the truncated raw string if parsing fails (we'd rather
    show *something* than raise during a list render). The fallback path
    matches the existing `audit_results.audited_at[:16].replace('T', ' ')`
    convention used in `templates/bridge/seo.html`.
    """
    try:
        dt = datetime.fromisoformat(iso_utc)
    except ValueError:
        return (iso_utc or "")[:16].replace("T", " ")
    if dt.tzinfo is None:
        # Defensive: legacy rows that pre-date the tz-aware convention.
        dt = dt.replace(tzinfo=timezone.utc)
    taipei = dt.astimezone(ZoneInfo("Asia/Taipei"))
    return taipei.strftime("%Y-%m-%d %H:%M")


def insert_run(
    *,
    topic: str,
    en_topic: Optional[str],
    content_type: ContentType,
    report_md: str,
    triggered_by: TriggeredBy,
    created_at: Optional[str] = None,
) -> int:
    """Insert one run; returns the new row id.

    `created_at` defaults to `_now_utc_iso()`; tests pin a specific value.
    The router calls this best-effort — failures must not break the
    user-facing render (catch in caller).
    """
    ts = created_at or _now_utc_iso()
    conn = _get_conn()
    cur = conn.execute(
        """
        INSERT INTO keyword_research_runs
            (topic, en_topic, content_type, report_md, created_at, triggered_by)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (topic, en_topic, content_type, report_md, ts, triggered_by),
    )
    conn.commit()
    new_id = cur.lastrowid
    if new_id is None:
        raise RuntimeError("insert_run: lastrowid was None")
    return new_id


def list_runs(*, limit: int = 20, offset: int = 0) -> list[KeywordResearchRunRow]:
    """Page-friendly list query, sorted by created_at DESC.

    Pagination contract: the router passes `?offset=N`; returned page size
    is exactly `limit` unless we're at the tail. Use `count_runs()` for the
    total so the template can decide whether to render Next/Prev.
    """
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT id, topic, en_topic, content_type, report_md,
               created_at, triggered_by
          FROM keyword_research_runs
         ORDER BY created_at DESC, id DESC
         LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]  # type: ignore[return-value]


def count_runs() -> int:
    """Total number of runs — used to compute pagination bounds."""
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) AS n FROM keyword_research_runs").fetchone()
    return int(row[0])


def get_run(run_id: int) -> Optional[KeywordResearchRunRow]:
    """Fetch a single run by id, or None if not found.

    Used by the detail page; returns the full report_md so the page can
    re-render markdown without re-running research_keywords.
    """
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT id, topic, en_topic, content_type, report_md,
               created_at, triggered_by
          FROM keyword_research_runs
         WHERE id = ?
        """,
        (run_id,),
    ).fetchone()
    return dict(row) if row else None  # type: ignore[return-value]
