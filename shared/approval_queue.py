"""HITL approval queue — FSM + atomic claim + stale reset (ADR-006).

FSM Single Source of Truth：`ALLOWED_TRANSITIONS` dict。state.py 的 DB CHECK
字面值是**手動複寫**，本檔 import 時的 `assert` 鎖住兩邊同步 — 非真正的程式碼生成。

典型使用：
    # Brook enqueue
    draft_id = enqueue(
        source_agent="brook",
        payload_model=PublishWpPostV1(...),
        operation_id="op_12345678",
    )

    # Usopp claim
    batch = claim_approved_drafts(worker_id="usopp-1", source_agent="brook")
    for row in batch:
        try:
            result = publish(row)
            mark_published(row["id"], result)
        except Exception as e:
            mark_failed(row["id"], str(e))

    # Cron 每 5 分鐘跑
    reset_stale_claims()
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.schemas.approval import (
    ApprovalPayloadV1,
    ApprovalPayloadV1Adapter,
    PublishWpPostV1,
    UpdateWpPostV1,
)
from shared.state import _get_conn

# ---------------------------------------------------------------------------
# FSM SoT（ADR-006 §4）
# ---------------------------------------------------------------------------

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in_review", "approved"},
    "in_review": {"in_review", "approved", "rejected"},
    "approved": {"claimed"},
    "claimed": {"published", "failed", "approved"},  # approved 為 stale timeout reset
    "failed": {"claimed", "archived"},
    "published": {"archived"},
    "rejected": {"archived"},
    "archived": set(),
}

# FSM 衍生的完整 status 集合；import 時的 assert 鎖住與 state.py CHECK 子句的硬編碼重複
ALL_STATUSES: set[str] = set(ALLOWED_TRANSITIONS.keys()) | {
    s for targets in ALLOWED_TRANSITIONS.values() for s in targets
}

assert ALL_STATUSES == {
    "pending",
    "in_review",
    "approved",
    "rejected",
    "claimed",
    "published",
    "failed",
    "archived",
}, "FSM SoT 飄移：新增／移除狀態需同步 state.py CHECK 列表"


STALE_CLAIM_THRESHOLD_S = 10 * 60  # 10 分鐘無進展視為 worker 掛掉


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class IllegalStatusTransitionError(ValueError):
    """試圖走 ALLOWED_TRANSITIONS 未列出的狀態轉移。"""


class ConcurrentTransitionError(RuntimeError):
    """transition() 帶條件 UPDATE 發現 row 已被其他 worker 改過。"""


class UnknownPayloadVersionError(ValueError):
    """payload_version 在 schema 版本表裡找不到對應 parser。"""


class ComplianceAckMissingError(ValueError):
    """payload.compliance_flags 命中但 reviewer_compliance_ack=False，拒絕 claim。"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _snippet(title: str, limit: int = 80) -> str:
    return title if len(title) <= limit else title[: limit - 1] + "…"


def _target_platform(payload: ApprovalPayloadV1) -> str:
    if isinstance(payload, (PublishWpPostV1, UpdateWpPostV1)):
        return "wordpress"
    raise ValueError(f"unknown payload type: {type(payload).__name__}")


def _target_site(payload: ApprovalPayloadV1) -> str | None:
    if isinstance(payload, (PublishWpPostV1, UpdateWpPostV1)):
        return payload.target_site
    return None


def _title_of(payload: ApprovalPayloadV1) -> str:
    if isinstance(payload, PublishWpPostV1):
        return payload.draft.title
    if isinstance(payload, UpdateWpPostV1):
        return payload.change_summary
    raise ValueError(f"no title extractor for {type(payload).__name__}")


def _diff_target_id(payload: ApprovalPayloadV1) -> str | None:
    if isinstance(payload, UpdateWpPostV1):
        return str(payload.wp_post_id)
    return None


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------


def enqueue(
    *,
    source_agent: str,
    payload_model: ApprovalPayloadV1,
    operation_id: str,
    priority: int = 50,
    initial_status: str = "pending",
    cost_usd_compose: float | None = None,
) -> int:
    """Insert a new draft into approval_queue. Returns the new row id.

    Args:
        source_agent: enqueueing agent ("brook", "chopper", ...).
        payload_model: validated Pydantic model (PublishWpPostV1 / UpdateWpPostV1).
        operation_id: observability.md §2 operation_id, for cross-log correlation.
        priority: 0-100; higher = earlier claim within same status.
        initial_status: 'pending' (default) or 'in_review' to skip triage.
        cost_usd_compose: LLM cost to produce this draft, shown in Bridge UI.
    """
    if initial_status not in ("pending", "in_review"):
        raise ValueError(f"initial_status must be pending or in_review, got {initial_status!r}")

    payload_json = payload_model.model_dump_json()
    target_platform = _target_platform(payload_model)
    target_site = _target_site(payload_model)
    action_type = payload_model.action_type
    title_snippet = _snippet(_title_of(payload_model))
    diff_target_id = _diff_target_id(payload_model)
    # ADR-005b §10: mirror payload.reviewer_compliance_ack to DB column for UI list filtering
    ack = 1 if getattr(payload_model, "reviewer_compliance_ack", False) else 0

    conn = _get_conn()
    now = _now_iso()
    cur = conn.execute(
        """INSERT INTO approval_queue
           (created_at, updated_at, source_agent, target_platform, target_site,
            action_type, priority, payload_version, payload, title_snippet,
            diff_target_id, status, operation_id, cost_usd_compose,
            reviewer_compliance_ack)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            now,
            now,
            source_agent,
            target_platform,
            target_site,
            action_type,
            priority,
            payload_model.schema_version,
            payload_json,
            title_snippet,
            diff_target_id,
            initial_status,
            operation_id,
            cost_usd_compose,
            ack,
        ),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Atomic claim（ADR-006 §3）
# ---------------------------------------------------------------------------


def claim_approved_drafts(
    *,
    worker_id: str,
    source_agent: str,
    batch: int = 5,
    timeout_s: int = 5,
) -> list[dict[str, Any]]:
    """Atomically claim `batch` approved drafts for this worker.

    Atomicity comes from SQLite's single-statement UPDATE...RETURNING with IN-subselect:
    SQLite evaluates subselect + update as one atomic step per statement. Concurrent
    writers serialize via SQLite's own file-level locking (WAL mode permits 1 writer
    + N readers); `check_same_thread=False` only disables Python's thread-origin check
    and does not itself add a mutex. The single-statement design avoids fighting
    Python sqlite3's implicit transaction management (reliability.md §2), giving the
    same semantics as an explicit BEGIN IMMEDIATE.

    Compliance post-filter: rows with compliance_flags triggered but no
    reviewer_compliance_ack are briefly claimed at SQL level then reverted to failed
    via `mark_failed()` with a diagnostic. The SQL itself does NOT filter these out —
    ADR-005b §10's defense-in-depth requires the Python-side re-check so a missing
    reviewer ack never silently escapes to the worker.

    Unknown payload_version fallback: a row with `payload_version != 1` is marked
    failed (non-retry-able, `increment_retry=False`) and the loop continues to
    other batch rows, instead of raising `UnknownPayloadVersionError` and aborting
    the whole claim. Rationale: schema drift is a ship-time bug, not a transient
    condition — one bad row shouldn't stall the whole worker, and the diagnostic
    in the failed row surfaces the drift for triage.
    """
    conn = _get_conn()
    conn.execute(f"PRAGMA busy_timeout = {timeout_s * 1000}")

    now = _now_iso()
    rows = conn.execute(
        """UPDATE approval_queue
           SET status     = 'claimed',
               worker_id  = ?,
               claimed_at = ?,
               updated_at = ?
           WHERE id IN (
               SELECT id FROM approval_queue
               WHERE status = 'approved' AND source_agent = ?
               ORDER BY priority DESC, created_at ASC
               LIMIT ?
           )
           RETURNING id, payload_version, payload, operation_id, action_type,
                     target_platform, target_site, reviewer_compliance_ack""",
        (worker_id, now, now, source_agent, batch),
    ).fetchall()
    conn.commit()

    claimed: list[dict[str, Any]] = []
    for row in rows:
        if row["payload_version"] != 1:
            # Schema drift — unknown payload_version has no adapter. Don't raise:
            # that would abort the rest of the batch and leave sibling rows in an
            # ambiguous half-claimed state. Instead mark just this row failed
            # with a diagnostic; it sits in 'failed' until the relevant adapter
            # lands, and other rows in the batch keep processing.
            mark_failed(
                draft_id=row["id"],
                error_log=(
                    f"unknown payload_version={row['payload_version']} — "
                    f"no V{row['payload_version']} adapter (known: V1). "
                    "Schema drift; ignore until adapter added."
                ),
                actor=worker_id,
                increment_retry=False,  # adapter gap is not a retry-able condition
            )
            continue
        raw_payload = json.loads(row["payload"])
        payload = ApprovalPayloadV1Adapter.validate_python(raw_payload)

        flags = payload.compliance_flags
        if (flags.medical_claim or flags.absolute_assertion) and not bool(
            row["reviewer_compliance_ack"]
        ):
            # Revert this claim — row never belonged here
            mark_failed(
                draft_id=row["id"],
                error_log="compliance flag set but reviewer_compliance_ack=0; reopened for HITL",
                actor=worker_id,
            )
            continue

        claimed.append(
            {
                "id": row["id"],
                "payload": payload,
                "operation_id": row["operation_id"],
                "action_type": row["action_type"],
                "target_platform": row["target_platform"],
                "target_site": row["target_site"],
            }
        )
    return claimed


# ---------------------------------------------------------------------------
# FSM transition
# ---------------------------------------------------------------------------


def transition(
    *,
    draft_id: int,
    from_status: str,
    to_status: str,
    actor: str,
    note: str | None = None,
    execution_result: dict | None = None,
    error_log: str | None = None,
    increment_retry: bool = False,
) -> None:
    """Transition a queue row from one status to another, atomic via conditional UPDATE.

    Raises:
        IllegalStatusTransitionError: if from→to not in ALLOWED_TRANSITIONS.
        ConcurrentTransitionError: if the row was not in from_status when we ran UPDATE
                                   (another worker / actor already moved it).
    """
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
        raise IllegalStatusTransitionError(
            f"draft={draft_id} {from_status}→{to_status} not in ALLOWED_TRANSITIONS"
        )

    conn = _get_conn()
    now = _now_iso()

    set_fragments = ["status = ?", "updated_at = ?"]
    params: list[Any] = [to_status, now]

    if to_status == "approved" and from_status == "in_review":
        set_fragments.append("reviewer = ?")
        params.append(actor)
        set_fragments.append("reviewed_at = ?")
        params.append(now)
        if note is not None:
            set_fragments.append("review_note = ?")
            params.append(note)
    if to_status == "rejected":
        set_fragments.append("reviewer = ?")
        params.append(actor)
        set_fragments.append("reviewed_at = ?")
        params.append(now)
        if note is not None:
            set_fragments.append("review_note = ?")
            params.append(note)
    if to_status == "published":
        set_fragments.append("published_at = ?")
        params.append(now)
        if execution_result is not None:
            set_fragments.append("execution_result = ?")
            params.append(json.dumps(execution_result, ensure_ascii=False))
    if to_status == "failed" and error_log is not None:
        set_fragments.append("error_log = ?")
        params.append(error_log)
    if increment_retry:
        set_fragments.append("retry_count = retry_count + 1")
    # reset stale claim：claimed → approved 時清掉 worker_id / claimed_at
    if from_status == "claimed" and to_status == "approved":
        set_fragments.append("worker_id = NULL")
        set_fragments.append("claimed_at = NULL")

    params.extend([draft_id, from_status])
    sql = "UPDATE approval_queue SET " + ", ".join(set_fragments) + " WHERE id = ? AND status = ?"
    cur = conn.execute(sql, params)
    conn.commit()
    if cur.rowcount == 0:
        raise ConcurrentTransitionError(
            f"draft={draft_id} was not in status={from_status!r} at update time"
        )


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------


def approve(draft_id: int, *, reviewer: str, note: str | None = None) -> None:
    """HITL approve — in_review → approved."""
    transition(
        draft_id=draft_id,
        from_status="in_review",
        to_status="approved",
        actor=reviewer,
        note=note,
    )


def reject(draft_id: int, *, reviewer: str, note: str | None = None) -> None:
    """HITL reject — in_review → rejected."""
    transition(
        draft_id=draft_id,
        from_status="in_review",
        to_status="rejected",
        actor=reviewer,
        note=note,
    )


def mark_published(draft_id: int, execution_result: dict, *, actor: str = "usopp") -> None:
    """Worker publish success — claimed → published."""
    transition(
        draft_id=draft_id,
        from_status="claimed",
        to_status="published",
        actor=actor,
        execution_result=execution_result,
    )


def mark_failed(
    draft_id: int,
    error_log: str,
    *,
    actor: str = "usopp",
    increment_retry: bool = True,
) -> None:
    """Worker publish failure — claimed → failed."""
    transition(
        draft_id=draft_id,
        from_status="claimed",
        to_status="failed",
        actor=actor,
        error_log=error_log,
        increment_retry=increment_retry,
    )


# ---------------------------------------------------------------------------
# Stale claim reset (ADR-006 §4.1)
# ---------------------------------------------------------------------------


def reset_stale_claims() -> list[int]:
    """Reset rows stuck in 'claimed' for > STALE_CLAIM_THRESHOLD_S back to 'approved'.

    Returns the draft ids that were reset.
    """
    conn = _get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=STALE_CLAIM_THRESHOLD_S)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
    rows = conn.execute(
        "SELECT id FROM approval_queue WHERE status = 'claimed' AND claimed_at < ?",
        (cutoff,),
    ).fetchall()

    reset_ids: list[int] = []
    for row in rows:
        try:
            transition(
                draft_id=row["id"],
                from_status="claimed",
                to_status="approved",
                actor="stale_claim_reset",
                note=f"claimed > {STALE_CLAIM_THRESHOLD_S}s, worker presumed dead",
            )
            reset_ids.append(row["id"])
        except ConcurrentTransitionError:
            # Worker 在掃描空窗裡剛好寫 published/failed，正常跳過
            continue
    return reset_ids


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def get_by_id(draft_id: int) -> dict[str, Any] | None:
    """Fetch one queue row by id; returns None if not found."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM approval_queue WHERE id = ?", (draft_id,)).fetchone()
    return dict(row) if row else None


def list_by_status(
    status: str, *, source_agent: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """List rows by status, optionally filtered by source_agent."""
    conn = _get_conn()
    if source_agent:
        rows = conn.execute(
            """SELECT * FROM approval_queue
               WHERE status = ? AND source_agent = ?
               ORDER BY priority DESC, created_at ASC
               LIMIT ?""",
            (status, source_agent, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM approval_queue
               WHERE status = ?
               ORDER BY priority DESC, created_at ASC
               LIMIT ?""",
            (status, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def new_operation_id() -> str:
    """Generate an operation_id matching DraftV1's op_[0-9a-f]{8} pattern."""
    return f"op_{uuid.uuid4().hex[:8]}"
