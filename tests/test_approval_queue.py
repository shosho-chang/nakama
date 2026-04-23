"""Tests for shared.approval_queue (ADR-006)."""

from __future__ import annotations

import concurrent.futures
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from shared import approval_queue, gutenberg_builder, state
from shared.schemas.approval import PublishWpPostV1
from shared.schemas.publishing import (
    BlockNodeV1,
    DraftComplianceV1,
    DraftV1,
    PublishComplianceGateV1,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_draft(slug: str = "test-article", op_id: str = "op_12345678") -> DraftV1:
    ast = [BlockNodeV1(block_type="paragraph", content=f"Body for {slug}")]
    return DraftV1(
        draft_id=f"draft_20260422T223000_{op_id[-6:]}",
        created_at=datetime.now(timezone.utc),
        agent="brook",
        operation_id=op_id,
        title=f"Title {slug}",
        slug_candidates=[slug],
        content=gutenberg_builder.build(ast),
        excerpt="An excerpt of at least twenty characters present here.",
        primary_category="blog",
        focus_keyword=slug,
        meta_description=(
            "A meta description that is at least fifty chars long to pass validator."
        ),
        compliance=DraftComplianceV1(
            schema_version=1,
            claims_no_therapeutic_effect=True,
            has_disclaimer=False,
        ),
        style_profile_id="blog@0.1.0",
    )


def _make_payload(
    *,
    slug: str = "test-article",
    op_id: str = "op_12345678",
    compliance_flag: bool = False,
    ack: bool = False,
) -> PublishWpPostV1:
    return PublishWpPostV1(
        action_type="publish_post",
        target_site="wp_shosho",
        draft=_make_draft(slug, op_id),
        compliance_flags=PublishComplianceGateV1(medical_claim=compliance_flag),
        reviewer_compliance_ack=ack,
    )


def _enqueue_approved(slug: str = "test-article", op_id: str = "op_12345678") -> int:
    """Helper: enqueue and auto-approve, returning queue id."""
    qid = approval_queue.enqueue(
        source_agent="brook",
        payload_model=_make_payload(slug=slug, op_id=op_id),
        operation_id=op_id,
        initial_status="in_review",
    )
    approval_queue.approve(qid, reviewer="shosho")
    return qid


# ---------------------------------------------------------------------------
# FSM SoT
# ---------------------------------------------------------------------------


class TestFSMSoT:
    def test_all_statuses_match_db_check(self):
        """State.py CHECK list must equal ALL_STATUSES (ADR-006 §4)."""
        conn = state._get_conn()
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='approval_queue'"
        ).fetchone()
        assert schema, "approval_queue table not created"
        match = re.search(r"CHECK\s*\(\s*status\s+IN\s*\(([^)]+)\)\s*\)", schema["sql"], re.DOTALL)
        assert match, "CHECK(status IN (...)) not found in schema"
        check_values = {s.strip().strip("'") for s in match.group(1).split(",")}
        assert check_values == approval_queue.ALL_STATUSES, (
            f"FSM drift: DB CHECK={check_values} vs ALL_STATUSES={approval_queue.ALL_STATUSES}"
        )

    def test_all_statuses_has_exactly_eight(self):
        assert len(approval_queue.ALL_STATUSES) == 8

    def test_archived_is_terminal(self):
        assert approval_queue.ALLOWED_TRANSITIONS["archived"] == set()


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------


class TestEnqueue:
    def test_enqueue_writes_row(self):
        qid = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(),
            operation_id="op_12345678",
            initial_status="in_review",
        )
        row = approval_queue.get_by_id(qid)
        assert row is not None
        assert row["source_agent"] == "brook"
        assert row["status"] == "in_review"
        assert row["target_platform"] == "wordpress"
        assert row["target_site"] == "wp_shosho"
        assert row["action_type"] == "publish_post"
        assert row["payload_version"] == 1
        assert row["title_snippet"].startswith("Title ")
        assert row["operation_id"] == "op_12345678"

    def test_enqueue_invalid_initial_status(self):
        with pytest.raises(ValueError, match="pending or in_review"):
            approval_queue.enqueue(
                source_agent="brook",
                payload_model=_make_payload(),
                operation_id="op_12345678",
                initial_status="approved",
            )

    def test_enqueue_cost_persisted(self):
        qid = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(),
            operation_id="op_12345678",
            cost_usd_compose=0.47,
        )
        row = approval_queue.get_by_id(qid)
        assert row["cost_usd_compose"] == pytest.approx(0.47)


# ---------------------------------------------------------------------------
# FSM transitions
# ---------------------------------------------------------------------------


class TestTransitions:
    def test_legal_transition_succeeds(self):
        qid = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(),
            operation_id="op_12345678",
            initial_status="in_review",
        )
        approval_queue.approve(qid, reviewer="shosho", note="LGTM")
        row = approval_queue.get_by_id(qid)
        assert row["status"] == "approved"
        assert row["reviewer"] == "shosho"
        assert row["review_note"] == "LGTM"
        assert row["reviewed_at"] is not None

    def test_illegal_transition_raises(self):
        qid = _enqueue_approved()
        with pytest.raises(approval_queue.IllegalStatusTransitionError):
            approval_queue.transition(
                draft_id=qid,
                from_status="approved",
                to_status="published",  # must go via 'claimed'
                actor="test",
            )

    def test_concurrent_transition_raises_when_status_drifted(self):
        """If row was already moved by another actor, conditional UPDATE matches 0 rows."""
        qid = _enqueue_approved()
        # Move to claimed outside of transition() to simulate drift
        conn = state._get_conn()
        conn.execute("UPDATE approval_queue SET status='claimed' WHERE id=?", (qid,))
        conn.commit()
        with pytest.raises(approval_queue.ConcurrentTransitionError):
            approval_queue.approve(qid, reviewer="shosho")  # expects in_review

    def test_reject(self):
        qid = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(),
            operation_id="op_12345678",
            initial_status="in_review",
        )
        approval_queue.reject(qid, reviewer="shosho", note="off-brand")
        row = approval_queue.get_by_id(qid)
        assert row["status"] == "rejected"
        assert row["review_note"] == "off-brand"


# ---------------------------------------------------------------------------
# Atomic claim
# ---------------------------------------------------------------------------


class TestAtomicClaim:
    def test_single_worker_claims(self):
        ids = [_enqueue_approved(f"slug-{i}", f"op_abcd{i:04x}") for i in range(3)]
        claimed = approval_queue.claim_approved_drafts(
            worker_id="usopp-1", source_agent="brook", batch=5
        )
        assert len(claimed) == 3
        assert {c["id"] for c in claimed} == set(ids)

    def test_claim_respects_batch_size(self):
        for i in range(5):
            _enqueue_approved(f"s-{i}", f"op_aaaa{i:04x}")
        claimed = approval_queue.claim_approved_drafts(
            worker_id="usopp-1", source_agent="brook", batch=2
        )
        assert len(claimed) == 2

    def test_claim_filters_by_source_agent(self):
        qid = _enqueue_approved("for-brook")
        claimed = approval_queue.claim_approved_drafts(
            worker_id="usopp-1", source_agent="other-agent", batch=5
        )
        assert claimed == []
        # Still claimable by correct agent
        claimed2 = approval_queue.claim_approved_drafts(
            worker_id="usopp-1", source_agent="brook", batch=5
        )
        assert len(claimed2) == 1 and claimed2[0]["id"] == qid

    def test_sequential_batches_cover_all_rows_without_overlap(self):
        """Repeated claim_approved_drafts() eventually claims every row, none twice.

        This verifies the atomicity property (UPDATE...RETURNING + WHERE IN SELECT
        with LIMIT is atomic per SQLite statement contract) without needing multi-
        process orchestration. In production each worker is a separate systemd service
        with its own connection — the real stress test lives in DoD integration suite.
        """
        ids = {_enqueue_approved(f"s-{i}", f"op_{i:08x}") for i in range(12)}

        all_claimed: list[int] = []
        for _ in range(10):  # enough iterations to drain
            batch = approval_queue.claim_approved_drafts(
                worker_id="single", source_agent="brook", batch=5
            )
            if not batch:
                break
            all_claimed.extend(c["id"] for c in batch)

        assert set(all_claimed) == ids, "some draft was missed"
        assert len(all_claimed) == len(set(all_claimed)), "some draft was double-claimed"

    @pytest.mark.skip(
        reason="multi-thread stress requires connection-per-thread or multiprocess harness "
        "(Phase 1 DoD integration test), not representative of systemd-service-per-worker prod"
    )
    def test_concurrent_workers_do_not_double_claim(self):
        ids = [_enqueue_approved(f"s-{i}", f"op_{i:08x}") for i in range(30)]

        def worker(wid: str) -> list[int]:
            claimed = approval_queue.claim_approved_drafts(
                worker_id=wid, source_agent="brook", batch=50
            )
            return [c["id"] for c in claimed]

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(worker, f"w-{i}") for i in range(5)]
            all_claimed: list[int] = []
            for f in futures:
                all_claimed.extend(f.result())

        assert sorted(all_claimed) == sorted(ids)
        assert len(all_claimed) == len(set(all_claimed))


# ---------------------------------------------------------------------------
# Compliance gate
# ---------------------------------------------------------------------------


class TestComplianceGate:
    def test_claim_rejects_flagged_without_ack(self):
        """ADR-005b §10: compliance flag set + ack=False → fail on claim."""
        qid = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(compliance_flag=True, ack=False),
            operation_id="op_abcd1234",
            initial_status="in_review",
        )
        approval_queue.approve(qid, reviewer="shosho")
        claimed = approval_queue.claim_approved_drafts(
            worker_id="usopp-1", source_agent="brook", batch=5
        )
        assert claimed == []
        # Row should have been marked failed with specific error
        row = approval_queue.get_by_id(qid)
        assert row["status"] == "failed"
        assert "reviewer_compliance_ack" in row["error_log"]

    def test_claim_allows_flagged_with_ack(self):
        """Flagged but reviewer acknowledged → claim proceeds normally."""
        qid = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(compliance_flag=True, ack=True),
            operation_id="op_abcd5678",
            initial_status="in_review",
        )
        approval_queue.approve(qid, reviewer="shosho")
        claimed = approval_queue.claim_approved_drafts(
            worker_id="usopp-1", source_agent="brook", batch=5
        )
        assert len(claimed) == 1
        assert claimed[0]["id"] == qid


# ---------------------------------------------------------------------------
# Unknown payload_version fallback (borderline #2)
# ---------------------------------------------------------------------------


class TestUnknownPayloadVersionFallback:
    """`claim_approved_drafts` 遇 payload_version != 1 時必須 mark_failed 兜底，
    而不是 raise UnknownPayloadVersionError 導致整個 batch 爆炸。"""

    def _enqueue_then_force_version(self, slug: str, op_id: str, version: int) -> int:
        """Helper：正常 enqueue V1 payload，再直接改 DB 的 payload_version
        欄位模擬 schema drift（future V2 已進 DB 但 adapter 尚未 deploy 的情境）。"""
        qid = _enqueue_approved(slug, op_id)
        conn = state._get_conn()
        conn.execute(
            "UPDATE approval_queue SET payload_version = ? WHERE id = ?",
            (version, qid),
        )
        conn.commit()
        return qid

    def test_unknown_version_marked_failed_not_raised(self):
        qid = self._enqueue_then_force_version("future-schema", "op_deadbeef", version=2)

        # 重要：不該 raise UnknownPayloadVersionError
        claimed = approval_queue.claim_approved_drafts(
            worker_id="usopp-1", source_agent="brook", batch=5
        )
        assert claimed == []

        row = approval_queue.get_by_id(qid)
        assert row["status"] == "failed"
        assert "payload_version=2" in row["error_log"]
        assert "no V2 adapter" in row["error_log"]

    def test_unknown_version_does_not_abort_batch(self):
        """單一 bad row 不應擋住同 batch 其他有效 row 被 claim。"""
        bad_id = self._enqueue_then_force_version("drift", "op_aaaa0001", version=99)
        good_ids = {_enqueue_approved(f"ok-{i}", f"op_aaaa{i:04x}") for i in (2, 3)}

        claimed = approval_queue.claim_approved_drafts(
            worker_id="usopp-1", source_agent="brook", batch=5
        )
        assert {c["id"] for c in claimed} == good_ids, "good rows 應被正常 claim"
        assert approval_queue.get_by_id(bad_id)["status"] == "failed"

    def test_unknown_version_does_not_increment_retry(self):
        """Schema drift 不是 transient 失敗 → retry_count 應維持 0。"""
        qid = self._enqueue_then_force_version("non-retryable", "op_cccc0001", version=3)
        approval_queue.claim_approved_drafts(worker_id="usopp-1", source_agent="brook", batch=5)
        row = approval_queue.get_by_id(qid)
        assert row["retry_count"] == 0, "retry_count 不該因 schema drift 遞增"


# ---------------------------------------------------------------------------
# Publish / fail
# ---------------------------------------------------------------------------


class TestExecutionOutcomes:
    def test_mark_published(self):
        qid = _enqueue_approved()
        approval_queue.claim_approved_drafts(worker_id="usopp-1", source_agent="brook", batch=5)
        approval_queue.mark_published(qid, {"post_id": 42, "url": "https://ex.tw/42"})
        row = approval_queue.get_by_id(qid)
        assert row["status"] == "published"
        assert row["published_at"] is not None
        assert '"post_id": 42' in row["execution_result"]

    def test_mark_failed_increments_retry(self):
        qid = _enqueue_approved()
        approval_queue.claim_approved_drafts(worker_id="usopp-1", source_agent="brook", batch=5)
        approval_queue.mark_failed(qid, "WP 500 error")
        row = approval_queue.get_by_id(qid)
        assert row["status"] == "failed"
        assert row["retry_count"] == 1
        assert row["error_log"] == "WP 500 error"


# ---------------------------------------------------------------------------
# Reset stale claims
# ---------------------------------------------------------------------------


class TestResetStaleClaims:
    def test_reset_old_claimed_back_to_approved(self):
        qid = _enqueue_approved()
        approval_queue.claim_approved_drafts(worker_id="dead-worker", source_agent="brook", batch=5)
        # Rewind claimed_at to simulate > 10 min stale
        conn = state._get_conn()
        stale_delta = timedelta(seconds=approval_queue.STALE_CLAIM_THRESHOLD_S + 60)
        stale_time = (datetime.now(timezone.utc) - stale_delta).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        conn.execute("UPDATE approval_queue SET claimed_at = ? WHERE id = ?", (stale_time, qid))
        conn.commit()

        reset_ids = approval_queue.reset_stale_claims()
        assert reset_ids == [qid]
        row = approval_queue.get_by_id(qid)
        assert row["status"] == "approved"
        assert row["worker_id"] is None
        assert row["claimed_at"] is None

    def test_fresh_claims_not_reset(self):
        qid = _enqueue_approved()
        approval_queue.claim_approved_drafts(worker_id="busy-worker", source_agent="brook", batch=5)
        # claimed_at is now ≈ just now, well within threshold
        reset_ids = approval_queue.reset_stale_claims()
        assert reset_ids == []
        row = approval_queue.get_by_id(qid)
        assert row["status"] == "claimed"


# ---------------------------------------------------------------------------
# Discriminated union (regression — ADR-006 §2)
# ---------------------------------------------------------------------------


class TestPayloadDiscriminator:
    def test_validate_python_dispatches_on_action_type(self):
        from shared.schemas.approval import ApprovalPayloadV1Adapter, UpdateWpPostV1

        raw = {
            "schema_version": 1,
            "action_type": "update_post",
            "target_site": "wp_shosho",
            "wp_post_id": 123,
            "patch": {"title": "new"},
            "change_summary": "title edit",
            "draft_id": "draft_xyz",
            "compliance_flags": {
                "schema_version": 1,
                "medical_claim": False,
                "absolute_assertion": False,
                "matched_terms": [],
            },
            "reviewer_compliance_ack": False,
        }
        payload = ApprovalPayloadV1Adapter.validate_python(raw)
        assert isinstance(payload, UpdateWpPostV1)
        assert payload.wp_post_id == 123

    def test_unknown_action_type_rejected(self):
        from pydantic import ValidationError

        from shared.schemas.approval import ApprovalPayloadV1Adapter

        with pytest.raises(ValidationError):
            ApprovalPayloadV1Adapter.validate_python({"action_type": "burn_everything"})


# ---------------------------------------------------------------------------
# new_operation_id helper
# ---------------------------------------------------------------------------


def test_new_operation_id_matches_draft_pattern():
    op_id = approval_queue.new_operation_id()
    assert re.fullmatch(r"op_[0-9a-f]{8}", op_id)


# Sanity: conftest's isolated_db autouse ensures every test gets a fresh tmp DB.
# If this ever stops working, tests will cross-contaminate through state.db.
def test_conftest_isolated_db_active():
    """Guard: ensure approval_queue starts empty per test (conftest isolation works)."""
    conn = state._get_conn()
    try:
        count = conn.execute("SELECT COUNT(*) FROM approval_queue").fetchone()[0]
    except sqlite3.OperationalError:
        pytest.fail("approval_queue table missing — state._init_tables did not run")
    assert count == 0
