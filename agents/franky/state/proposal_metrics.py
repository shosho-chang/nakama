"""Proposal lifecycle CRUD + FSM (ADR-023 §6).

Persistence layer for the franky evolution-loop proposal lifecycle:

    candidate → promoted → triaged → ready | wontfix
                                       └─ ready → shipped → verified | rejected

The FSM single source of truth is `ALLOWED_TRANSITIONS`. The CHECK enum in
migration 014 / `shared/state.py` is a manual mirror; an import-time assert
rejects drift between the two.

Public CRUD helpers (called by S3 weekly synthesis, S4 retrospective, and
human-driven triage tooling):

    insert_candidate(frontmatter, *, week_iso, ...)  -> int  (db row id)
    mark_promoted(proposal_id, *, issue_number=None) -> None
    mark_triaged(proposal_id) -> None
    mark_ready(proposal_id) -> None
    mark_wontfix(proposal_id, reason) -> None
    mark_shipped(proposal_id, *, pr_url, commit_sha) -> None
    mark_verified(proposal_id, *, post_ship_value) -> None
    mark_rejected(proposal_id, *, reason) -> None

Plus a generic `transition(proposal_id, target_status, **fields)` that
backstops any of the above. The named helpers exist so callers don't have
to remember which side-fields each transition fills.

S3 / S4 hook stubs (`on_promote`, `on_ship`, `on_verify`) are exposed as
no-op callables this slice; S3 wires up the real callbacks when it lands.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from shared.schemas.proposal_metrics import (
    ProposalFrontmatterV1,
    ProposalStatus,
)
from shared.state import _get_conn

# ---------------------------------------------------------------------------
# FSM SoT (ADR-023 §6)
# ---------------------------------------------------------------------------

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "candidate": {"promoted", "rejected"},
    "promoted": {"triaged", "rejected"},
    "triaged": {"ready", "wontfix"},
    "ready": {"shipped", "rejected"},
    "shipped": {"verified", "rejected"},
    # Terminal states
    "wontfix": set(),
    "verified": set(),
    "rejected": set(),
}

ALL_STATUSES: set[str] = set(ALLOWED_TRANSITIONS.keys()) | {
    s for targets in ALLOWED_TRANSITIONS.values() for s in targets
}

# Hard guard: schema and FSM SoT must agree. Any drift here is a bug — adding
# a status to the FSM without updating migration 014 / state.py CHECK will
# happily accept rows that the DB will then reject at INSERT time.
assert ALL_STATUSES == {
    "candidate",
    "promoted",
    "triaged",
    "ready",
    "wontfix",
    "shipped",
    "verified",
    "rejected",
}, "FSM SoT drift: update migration 014 + shared/state.py CHECK and ProposalStatus literal"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class IllegalStatusTransitionError(ValueError):
    """Tried to take a transition not present in ALLOWED_TRANSITIONS."""


class ProposalNotFoundError(LookupError):
    """No row for the given proposal_id."""


class DuplicateProposalError(ValueError):
    """insert_candidate called for a proposal_id that already exists."""


# ---------------------------------------------------------------------------
# Hook stubs — S3 / S4 wire real callbacks; this slice keeps them inert.
# ---------------------------------------------------------------------------

# Each hook takes the post-transition row (as a dict from the DB row_factory)
# and returns nothing. Keeping them mutable module-level lists lets S3 register
# additional callbacks without monkey-patching this module's API surface.
on_promote_hooks: list[Callable[[dict[str, Any]], None]] = []
on_ship_hooks: list[Callable[[dict[str, Any]], None]] = []
on_verify_hooks: list[Callable[[dict[str, Any]], None]] = []


def _fire(hooks: list[Callable[[dict[str, Any]], None]], row: dict[str, Any]) -> None:
    for hook in hooks:
        try:
            hook(row)
        except Exception:  # pragma: no cover - hook errors must not break FSM
            # Hooks are advisory; a failing notifier should not corrupt the
            # lifecycle row. S3 is responsible for its own error handling.
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    # Decode the JSON-array columns so callers see Python lists.
    for col in ("related_adr", "related_issues", "source_item_ids"):
        raw = d.get(col)
        d[col] = json.loads(raw) if raw else []
    d["panel_recommended"] = bool(d.get("panel_recommended", 0))
    return d


def _check_transition(current: str, target: str) -> None:
    if current not in ALLOWED_TRANSITIONS:
        raise IllegalStatusTransitionError(f"current status {current!r} is not a known FSM state")
    if target not in ALLOWED_TRANSITIONS[current]:
        raise IllegalStatusTransitionError(
            f"illegal transition {current!r} → {target!r}; "
            f"allowed: {sorted(ALLOWED_TRANSITIONS[current])}"
        )


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def get(proposal_id: str) -> dict[str, Any]:
    """Return the row for `proposal_id` as a dict (lists decoded).

    Raises ProposalNotFoundError if no such row.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM proposal_metrics WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()
    out = _row_to_dict(row)
    if out is None:
        raise ProposalNotFoundError(proposal_id)
    return out


def list_by_status(status: ProposalStatus) -> list[dict[str, Any]]:
    """All proposals in a given status, oldest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM proposal_metrics WHERE status = ? ORDER BY created_at ASC",
        (status,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------


def insert_candidate(
    frontmatter: ProposalFrontmatterV1,
    *,
    week_iso: str,
    panel_recommended: bool = False,
    baseline_source: Optional[str] = None,
    baseline_value: Optional[str] = None,
    verification_owner: Optional[str] = None,
    try_cost_estimate: Optional[str] = None,
    source_item_ids: Optional[list[str]] = None,
) -> int:
    """Insert a new proposal in `candidate` status.

    Returns the new DB row id. Raises DuplicateProposalError if proposal_id
    already exists (UNIQUE constraint).
    """
    conn = _get_conn()
    now = _now()
    try:
        cur = conn.execute(
            """INSERT INTO proposal_metrics (
                    proposal_id, week_iso,
                    related_adr, related_issues,
                    metric_type, success_metric,
                    baseline_source, baseline_value,
                    verification_owner, try_cost_estimate,
                    panel_recommended, status,
                    created_at, source_item_ids
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?)""",
            (
                frontmatter.proposal_id,
                week_iso,
                json.dumps(frontmatter.related_adr, ensure_ascii=False),
                json.dumps(frontmatter.related_issues, ensure_ascii=False),
                frontmatter.metric_type,
                frontmatter.success_metric,
                baseline_source,
                baseline_value,
                verification_owner,
                try_cost_estimate,
                1 if panel_recommended else 0,
                now,
                json.dumps(source_item_ids or [], ensure_ascii=False),
            ),
        )
    except sqlite3.IntegrityError as e:
        raise DuplicateProposalError(frontmatter.proposal_id) from e
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Generic transition
# ---------------------------------------------------------------------------


def transition(
    proposal_id: str,
    target_status: ProposalStatus,
    *,
    extra_fields: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Move `proposal_id` to `target_status`, optionally setting extra fields.

    `extra_fields` is a column-name → value mapping (e.g.
    `{"related_pr": "...", "shipped_at": "..."}`). The caller is responsible
    for naming columns that exist; this layer trusts well-known callers
    (the named `mark_*` helpers below) and validates only the FSM edge.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT status FROM proposal_metrics WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise ProposalNotFoundError(proposal_id)

    _check_transition(row["status"], target_status)

    fields: dict[str, Any] = {"status": target_status}
    if extra_fields:
        fields.update(extra_fields)

    set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
    params = list(fields.values()) + [proposal_id]
    conn.execute(
        f"UPDATE proposal_metrics SET {set_clause} WHERE proposal_id = ?",
        params,
    )
    conn.commit()
    return get(proposal_id)


# ---------------------------------------------------------------------------
# Named helpers — fill the canonical side-fields for each transition.
# ---------------------------------------------------------------------------


def mark_promoted(
    proposal_id: str,
    *,
    issue_number: Optional[int] = None,
) -> dict[str, Any]:
    extra: dict[str, Any] = {"promoted_at": _now()}
    if issue_number is not None:
        extra["issue_number"] = issue_number
    out = transition(proposal_id, "promoted", extra_fields=extra)
    _fire(on_promote_hooks, out)
    return out


def mark_triaged(proposal_id: str) -> dict[str, Any]:
    return transition(
        proposal_id,
        "triaged",
        extra_fields={"triaged_at": _now()},
    )


def mark_ready(proposal_id: str) -> dict[str, Any]:
    """triaged → ready (issue is queued for an agent / human to ship)."""
    return transition(proposal_id, "ready")


def mark_wontfix(proposal_id: str, reason: str) -> dict[str, Any]:
    """Terminal wontfix from triage. `reason` is written to baseline_source
    only when that column is empty, so quantitative proposals' real
    baseline_source set at insert is preserved (review feedback PR #481).
    Quantitative + wontfix loses the reason — acceptable until a dedicated
    `wontfix_reason` column is added (deferred to schema __v2)."""
    row = get(proposal_id)
    if row is None:
        raise IllegalStatusTransitionError(f"proposal_id {proposal_id!r} not found")
    extra: dict[str, Any] = {}
    if not row.get("baseline_source"):
        extra["baseline_source"] = f"wontfix: {reason}"
    return transition(proposal_id, "wontfix", extra_fields=extra)


def mark_shipped(
    proposal_id: str,
    *,
    pr_url: str,
    commit_sha: str,
) -> dict[str, Any]:
    out = transition(
        proposal_id,
        "shipped",
        extra_fields={
            "shipped_at": _now(),
            "related_pr": pr_url,
            "related_commit": commit_sha,
        },
    )
    _fire(on_ship_hooks, out)
    return out


def mark_verified(
    proposal_id: str,
    *,
    post_ship_value: str,
) -> dict[str, Any]:
    out = transition(
        proposal_id,
        "verified",
        extra_fields={
            "verified_at": _now(),
            "post_ship_value": post_ship_value,
        },
    )
    _fire(on_verify_hooks, out)
    return out


def mark_rejected(
    proposal_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    """`rejected` is reachable from candidate / promoted / ready / shipped.
    The current status is taken from the DB; FSM check rejects illegal edges
    (e.g. wontfix → rejected)."""
    return transition(
        proposal_id,
        "rejected",
        extra_fields={"post_ship_value": f"rejected: {reason}"},
    )


__all__ = [
    "ALLOWED_TRANSITIONS",
    "ALL_STATUSES",
    "DuplicateProposalError",
    "IllegalStatusTransitionError",
    "ProposalNotFoundError",
    "get",
    "insert_candidate",
    "list_by_status",
    "mark_promoted",
    "mark_ready",
    "mark_rejected",
    "mark_shipped",
    "mark_triaged",
    "mark_verified",
    "mark_wontfix",
    "on_promote_hooks",
    "on_ship_hooks",
    "on_verify_hooks",
    "transition",
]
