"""Audit results store — deep module over state.db `audit_results` (PRD #226 slice 4).

Five-method public API (issue #232 acceptance):

    insert_run(...)                                  # write a new audit row
    get_by_id(audit_id)                              # fetch one row by PK
    latest_for_post(target_site, wp_post_id)         # most recent audit for a WP post
    update_suggestion(audit_id, rule_id, status, ..) # slice #5 review state
    mark_exported(audit_id, queue_id)                # slice #6 export hand-off

All other helpers are private (`_*`). Caller does not see SQL strings, JSON
serialization details, or row-to-dict shape — those are deep-impl concerns
behind a short interface (Ousterhout / `feedback_deep_module_vs_leaky_abstraction.md`).

Persisted shape lives in `migrations/006_audit_results.sql` + the canonical
copy in `shared/state.py::_init_tables`. Pydantic shape for the suggestion
JSON list lives in `shared/schemas/seo_audit_review.py` (`AuditSuggestionV1`).

Hidden constraints worth recording:

1. Multiple audits per post are stored as a history (id AUTOINCREMENT PK +
   secondary index on `(target_site, wp_post_id, audited_at DESC)`). The PRD
   text said "PK (target_site, wp_post_id)" intent-wise, but User Story #4
   demands history. Resolution: history table + `latest_for_post` aggregate.
2. `wp_post_id=NULL` is allowed for non-WP audits. `latest_for_post` always
   takes both args; if either is None, only URL-based lookup is meaningful
   (see `latest_for_url`). Slice 4 only wires WP-side.
3. `update_suggestion` rewrites `suggestions_json` in place. We re-serialize
   the whole list to keep the JSON contiguous (SQLite blob update is the
   same cost regardless of patch size).

Tests live in `tests/shared/test_audit_results_store.py`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import ValidationError

from shared.log import get_logger
from shared.schemas.seo_audit_review import (
    AuditSuggestionV1,
    OverallGrade,
    SuggestionStatus,
)
from shared.state import _get_conn

logger = get_logger("nakama.audit_results_store")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AuditNotFoundError(LookupError):
    """`get_by_id` / mutator could not find the audit row."""


class SuggestionNotFoundError(LookupError):
    """`update_suggestion` could not find a suggestion with that rule_id."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_suggestions(suggestions: list[AuditSuggestionV1]) -> str:
    """JSON-serialize a list of `AuditSuggestionV1` for the `suggestions_json`
    column. Uses `model_dump(mode='json')` so `AwareDatetime` -> ISO string.
    """
    return json.dumps(
        [s.model_dump(mode="json") for s in suggestions],
        ensure_ascii=False,
    )


def _deserialize_suggestions(raw: str) -> list[AuditSuggestionV1]:
    """Parse `suggestions_json` back into typed models.

    Bad / drifted entries are dropped with a debug log rather than blowing
    up the whole row read — the bridge UI must keep rendering even if a
    historical audit row has a malformed suggestion blob (graceful degrade
    similar to `wp_post_lister._project`).
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("audit_results suggestions_json corrupt: %s", exc)
        return []
    if not isinstance(parsed, list):
        logger.warning(
            "audit_results suggestions_json is not a list (got %s)", type(parsed).__name__
        )
        return []
    out: list[AuditSuggestionV1] = []
    for entry in parsed:
        try:
            out.append(AuditSuggestionV1.model_validate(entry))
        except ValidationError as exc:
            logger.debug("audit_results dropped malformed suggestion: %s", exc)
            continue
    return out


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Translate a sqlite3.Row into the public dict shape the bridge consumes.

    Renders `suggestions_json` into a typed list. Other columns pass through
    as the DB stored them (status / count ints / timestamp strings).
    """
    d = dict(row)
    d["suggestions"] = _deserialize_suggestions(d.pop("suggestions_json", "") or "")
    return d


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def insert_run(
    *,
    url: str,
    target_site: Optional[str],
    wp_post_id: Optional[int],
    focus_keyword: str,
    audited_at: datetime,
    overall_grade: OverallGrade,
    pass_count: int,
    warn_count: int,
    fail_count: int,
    skip_count: int,
    suggestions: list[AuditSuggestionV1],
    raw_markdown: str,
) -> int:
    """Insert one audit run row. Returns the new row id.

    Caller is `agents/brook/audit_runner.run`. The runner is responsible for
    parsing the audit script's frontmatter into `overall_grade` / counts /
    suggestions; this store does not crack open the markdown.

    `audited_at` is naive-disallowed: pass `datetime.now(timezone.utc)` or
    similar. We persist as ISO 8601 with offset.
    """
    if audited_at.tzinfo is None:
        # Defense-in-depth: schema typing already forbids naive, but the
        # runner could feed us anything until #234 starts validating.
        raise ValueError("audited_at must be timezone-aware")
    conn = _get_conn()
    cur = conn.execute(
        """
        INSERT INTO audit_results (
            target_site, wp_post_id, url, focus_keyword,
            audited_at, overall_grade,
            pass_count, warn_count, fail_count, skip_count,
            suggestions_json, raw_markdown,
            review_status, approval_queue_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'fresh', NULL)
        """,
        (
            target_site,
            wp_post_id,
            url,
            focus_keyword,
            audited_at.astimezone(timezone.utc).isoformat(),
            overall_grade,
            pass_count,
            warn_count,
            fail_count,
            skip_count,
            _serialize_suggestions(suggestions),
            raw_markdown,
        ),
    )
    conn.commit()
    audit_id = cur.lastrowid
    logger.info(
        "audit_results insert id=%d target_site=%s wp_post_id=%s grade=%s suggestions=%d",
        audit_id,
        target_site,
        wp_post_id,
        overall_grade,
        len(suggestions),
    )
    return audit_id


def get_by_id(audit_id: int) -> Optional[dict[str, Any]]:
    """Fetch one audit row by PK. Returns ``None`` when not found.

    Returned dict has all DB columns + `suggestions: list[AuditSuggestionV1]`
    (deserialized). `suggestions_json` is removed from the output to avoid
    consumers double-handling the JSON.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM audit_results WHERE id = ?",
        (audit_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def latest_for_post(target_site: str, wp_post_id: int) -> Optional[dict[str, Any]]:
    """Return the newest audit row for a (target_site, wp_post_id) pair.

    Section 1 of `/bridge/seo` calls this once per row to populate the GRADE
    + LAST AUDITED columns. ``None`` when never audited (the template renders
    a `—` placeholder).
    """
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT * FROM audit_results
        WHERE target_site = ? AND wp_post_id = ?
        ORDER BY audited_at DESC
        LIMIT 1
        """,
        (target_site, wp_post_id),
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def latest_for_url(url: str) -> Optional[dict[str, Any]]:
    """Return the newest audit row for a URL (no wp_post_id required).

    Used by external / non-WP audit kick-offs: we look the URL up to see if
    there's already a fresh result we should reuse, even though `wp_post_id`
    is NULL. Not in slice 4 router yet but exposed for slice 5 to consume.
    """
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT * FROM audit_results
        WHERE url = ?
        ORDER BY audited_at DESC
        LIMIT 1
        """,
        (url,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def update_suggestion(
    *,
    audit_id: int,
    rule_id: str,
    status: SuggestionStatus,
    edited_value: Optional[str] = None,
    reviewed_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Mutate one suggestion's review state inside `audit_results.suggestions_json`.

    Slice #234 (review UI) is the primary caller. We rewrite the entire
    `suggestions_json` blob — single-row transaction, no row growth so the
    cost is constant.

    Side effect: when the row's `review_status` is still 'fresh', flips it
    to 'in_review' so the inbox surface (slice #234) can list active reviews.

    Raises:
        AuditNotFoundError      — no row with `audit_id`.
        SuggestionNotFoundError — row exists but no suggestion has `rule_id`.
        ValueError              — `status='edited'` without `edited_value`.

    Returns the updated audit row (as `get_by_id` would). The new
    `AuditSuggestionV1` is at index `[i]` matching the original list order.
    """
    if status == "edited" and not edited_value:
        raise ValueError("edited_value is required when status='edited'")
    if reviewed_at is not None and reviewed_at.tzinfo is None:
        raise ValueError("reviewed_at must be timezone-aware")

    conn = _get_conn()
    row = conn.execute(
        "SELECT id, suggestions_json, review_status FROM audit_results WHERE id = ?",
        (audit_id,),
    ).fetchone()
    if row is None:
        raise AuditNotFoundError(f"audit_results.id={audit_id} not found")

    suggestions = _deserialize_suggestions(row["suggestions_json"] or "")
    target_idx = None
    for i, s in enumerate(suggestions):
        if s.rule_id == rule_id:
            target_idx = i
            break
    if target_idx is None:
        raise SuggestionNotFoundError(
            f"audit_results.id={audit_id} has no suggestion rule_id={rule_id!r}"
        )

    old = suggestions[target_idx]
    # AuditSuggestionV1 is frozen → reconstruct via model_copy(update=...).
    new = old.model_copy(
        update={
            "status": status,
            "edited_value": edited_value if status == "edited" else None,
            "reviewed_at": reviewed_at or datetime.now(timezone.utc),
        }
    )
    suggestions[target_idx] = new

    next_review_status = row["review_status"]
    if next_review_status == "fresh":
        next_review_status = "in_review"

    conn.execute(
        """
        UPDATE audit_results
        SET suggestions_json = ?, review_status = ?
        WHERE id = ?
        """,
        (_serialize_suggestions(suggestions), next_review_status, audit_id),
    )
    conn.commit()
    logger.info(
        "audit_results update_suggestion id=%d rule_id=%s status=%s",
        audit_id,
        rule_id,
        status,
    )

    fresh = get_by_id(audit_id)
    assert fresh is not None  # we just updated it
    return fresh


def mark_exported(audit_id: int, queue_id: int) -> dict[str, Any]:
    """Set `review_status='exported'` + `approval_queue_id=queue_id`.

    Called by slice #235 export action after the `approval_queue.enqueue`
    succeeds. Idempotent: re-calling with the same `queue_id` is fine; calling
    with a different `queue_id` overwrites (last writer wins — slice #235
    will guard against this at the action level).

    Raises `AuditNotFoundError` when the row is missing.
    """
    conn = _get_conn()
    cur = conn.execute(
        """
        UPDATE audit_results
        SET review_status = 'exported', approval_queue_id = ?
        WHERE id = ?
        """,
        (queue_id, audit_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise AuditNotFoundError(f"audit_results.id={audit_id} not found")
    logger.info(
        "audit_results mark_exported id=%d approval_queue_id=%d",
        audit_id,
        queue_id,
    )
    fresh = get_by_id(audit_id)
    assert fresh is not None
    return fresh


__all__ = [
    "AuditNotFoundError",
    "SuggestionNotFoundError",
    "insert_run",
    "get_by_id",
    "latest_for_post",
    "latest_for_url",
    "update_suggestion",
    "mark_exported",
]
