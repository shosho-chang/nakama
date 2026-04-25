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
# Payload parse-error fallback (borderline #2.5)
# ---------------------------------------------------------------------------


class TestPayloadParseErrorFallback:
    """`claim_approved_drafts` 遇 payload 字串壞掉 / schema 部分欄位 drift 時必須 mark_failed
    兜底，而不是 raise JSONDecodeError / ValidationError / TypeError 炸整個 batch。

    同 unknown-version 一樣屬於 ADR-006 borderline，走 `increment_retry=False` 非 retry。
    """

    def _enqueue_then_corrupt_payload(self, slug: str, op_id: str, new_payload: str | None) -> int:
        """Helper：正常 enqueue V1 payload，再直接改 DB 的 payload 欄位模擬損壞。"""
        qid = _enqueue_approved(slug, op_id)
        conn = state._get_conn()
        conn.execute(
            "UPDATE approval_queue SET payload = ? WHERE id = ?",
            (new_payload, qid),
        )
        conn.commit()
        return qid

    def test_corrupted_json_marked_failed_not_raised(self):
        qid = self._enqueue_then_corrupt_payload(
            "bad-json", "op_beef0001", new_payload="{not valid json"
        )
        # 重要：不該 raise JSONDecodeError
        claimed = approval_queue.claim_approved_drafts(
            worker_id="usopp-1", source_agent="brook", batch=5
        )
        assert claimed == []

        row = approval_queue.get_by_id(qid)
        assert row["status"] == "failed"
        assert "parse failed" in row["error_log"]
        assert "JSONDecodeError" in row["error_log"]

    def test_schema_drift_valid_json_marked_failed_not_raised(self):
        """合法 JSON 但缺必要欄位 → ValidationError 改 soft-fail。"""
        # schema_version=1 對齊 payload_version 整數，但 draft 欄位整段砍 → ValidationError
        drifted = '{"schema_version":1,"action_type":"publish_post","target_site":"wp_shosho"}'
        qid = self._enqueue_then_corrupt_payload("schema-drift", "op_beef0002", new_payload=drifted)

        claimed = approval_queue.claim_approved_drafts(
            worker_id="usopp-1", source_agent="brook", batch=5
        )
        assert claimed == []

        row = approval_queue.get_by_id(qid)
        assert row["status"] == "failed"
        assert "parse failed" in row["error_log"]
        assert "ValidationError" in row["error_log"]

    def test_parse_error_does_not_abort_batch(self):
        """單一壞 row 不應擋住同 batch 其他有效 row 被 claim。"""
        bad_id = self._enqueue_then_corrupt_payload(
            "broken", "op_dddd0001", new_payload="{not json"
        )
        good_ids = {_enqueue_approved(f"ok-{i}", f"op_eeee{i:04x}") for i in (2, 3)}

        claimed = approval_queue.claim_approved_drafts(
            worker_id="usopp-1", source_agent="brook", batch=5
        )
        assert {c["id"] for c in claimed} == good_ids, "good rows 應被正常 claim"
        assert approval_queue.get_by_id(bad_id)["status"] == "failed"

    def test_parse_error_does_not_increment_retry(self):
        """Payload 壞掉 / schema drift 都是 ship-time bug，不該走 retry 路徑。"""
        qid = self._enqueue_then_corrupt_payload("no-retry", "op_eeee0001", new_payload="bad{json")
        approval_queue.claim_approved_drafts(worker_id="usopp-1", source_agent="brook", batch=5)
        row = approval_queue.get_by_id(qid)
        assert row["retry_count"] == 0, "retry_count 不該因 parse error 遞增"


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
# count_by_status — cheap COUNT(*) for badges, avoids LIMIT 50 truncation
# ---------------------------------------------------------------------------


class TestCountByStatus:
    def test_zero_when_empty(self):
        assert approval_queue.count_by_status("pending") == 0

    def test_counts_pending_rows(self):
        for i in range(3):
            approval_queue.enqueue(
                source_agent="brook",
                payload_model=_make_payload(slug=f"a-{i}", op_id=f"op_aaaa1{i:03d}"),
                operation_id=f"op_aaaa1{i:03d}",
            )
        assert approval_queue.count_by_status("pending") == 3
        assert approval_queue.count_by_status("approved") == 0

    def test_filters_by_source_agent(self):
        for i in range(2):
            approval_queue.enqueue(
                source_agent="brook",
                payload_model=_make_payload(slug=f"b-{i}", op_id=f"op_bbbb2{i:03d}"),
                operation_id=f"op_bbbb2{i:03d}",
            )
        approval_queue.enqueue(
            source_agent="chopper",
            payload_model=_make_payload(slug="c-0", op_id="op_cccc3000"),
            operation_id="op_cccc3000",
        )
        assert approval_queue.count_by_status("pending") == 3
        assert approval_queue.count_by_status("pending", source_agent="brook") == 2
        assert approval_queue.count_by_status("pending", source_agent="chopper") == 1

    def test_count_returns_true_total_above_list_limit(self):
        # The bug count_by_status fixes: list_by_status caps at 50, so
        # len(list_by_status(...)) silently truncates the badge.
        for i in range(55):
            approval_queue.enqueue(
                source_agent="brook",
                payload_model=_make_payload(slug=f"d-{i}", op_id=f"op_dddd{i:04d}"),
                operation_id=f"op_dddd{i:04d}",
            )
        assert approval_queue.count_by_status("pending") == 55
        # Confirm the trap: list_by_status capped at 50 even with 55 rows present
        assert len(approval_queue.list_by_status("pending")) == 50


# ---------------------------------------------------------------------------
# Bridge UI Phase 2 mutations — extended approve/reject + update_payload + requeue
# ---------------------------------------------------------------------------


class TestApproveFromPending:
    """ADR-006 §4: pending → approved is a legal one-step transition."""

    def test_pending_to_approved_records_reviewer(self):
        qid = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(),
            operation_id="op_a0010001",
            initial_status="pending",
        )
        approval_queue.approve(qid, reviewer="shosho", note="quick LGTM", from_status="pending")
        row = approval_queue.get_by_id(qid)
        assert row["status"] == "approved"
        assert row["reviewer"] == "shosho"
        assert row["reviewed_at"] is not None
        assert row["review_note"] == "quick LGTM"

    def test_pending_to_approved_default_from_status_fails_loudly(self):
        """Default from_status='in_review' MUST raise on a pending row — silent
        fallthrough would overwrite 'pending' rows that were never reviewed."""
        qid = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(),
            operation_id="op_a0010002",
            initial_status="pending",
        )
        with pytest.raises(approval_queue.ConcurrentTransitionError):
            approval_queue.approve(qid, reviewer="shosho")  # default = in_review


class TestRejectFromPending:
    """Bridge UI: reviewer can reject a pending draft without first promoting to
    in_review (FSM extended in this PR with pending → rejected)."""

    def test_pending_to_rejected(self):
        qid = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(),
            operation_id="op_a0020001",
            initial_status="pending",
        )
        approval_queue.reject(qid, reviewer="shosho", note="off-topic", from_status="pending")
        row = approval_queue.get_by_id(qid)
        assert row["status"] == "rejected"
        assert row["reviewer"] == "shosho"
        assert row["review_note"] == "off-topic"

    def test_pending_to_rejected_in_allowed_transitions(self):
        assert "rejected" in approval_queue.ALLOWED_TRANSITIONS["pending"]


class TestUpdatePayload:
    """update_payload overwrites payload + recomputed denorm columns; status preserved."""

    def test_update_recomputes_title_snippet_and_keeps_status(self):
        qid = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(slug="original", op_id="op_a0030001"),
            operation_id="op_a0030001",
            initial_status="in_review",
        )
        new_payload = _make_payload(slug="rewritten-headline", op_id="op_a0030001")
        approval_queue.update_payload(
            qid,
            payload_model=new_payload,
            expected_status="in_review",
        )
        row = approval_queue.get_by_id(qid)
        assert row["status"] == "in_review", "edit must not change status"
        assert "rewritten-headline" in row["title_snippet"]
        # payload column reflects new content
        assert "rewritten-headline" in row["payload"]
        assert "Title original" not in row["payload"]

    def test_update_payload_pending_row(self):
        qid = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(slug="orig", op_id="op_a0030002"),
            operation_id="op_a0030002",
            initial_status="pending",
        )
        new_payload = _make_payload(slug="edited", op_id="op_a0030002")
        approval_queue.update_payload(
            qid,
            payload_model=new_payload,
            expected_status="pending",
        )
        row = approval_queue.get_by_id(qid)
        assert row["status"] == "pending"
        assert "edited" in row["title_snippet"]

    def test_update_payload_expected_status_drift_raises(self):
        qid = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(),
            operation_id="op_a0030003",
            initial_status="pending",
        )
        # Move row to a different status outside the helper to simulate drift
        conn = state._get_conn()
        conn.execute("UPDATE approval_queue SET status='in_review' WHERE id=?", (qid,))
        conn.commit()
        with pytest.raises(approval_queue.ConcurrentTransitionError):
            approval_queue.update_payload(
                qid,
                payload_model=_make_payload(slug="never-saved", op_id="op_a0030003"),
                expected_status="pending",
            )

    def test_update_payload_unknown_id_raises(self):
        with pytest.raises(ValueError, match="not found"):
            approval_queue.update_payload(
                99999,
                payload_model=_make_payload(),
            )


class TestRequeue:
    """failed → pending, with retry_count + error_log + claim metadata cleared."""

    def _enqueue_then_fail(self, slug: str, op_id: str) -> int:
        qid = _enqueue_approved(slug, op_id)
        approval_queue.claim_approved_drafts(worker_id="usopp-1", source_agent="brook", batch=5)
        approval_queue.mark_failed(qid, "WP 500 error")
        return qid

    def test_failed_to_pending_clears_failure_metadata(self):
        qid = self._enqueue_then_fail("fail-1", "op_a0040001")
        # Sanity precondition
        row = approval_queue.get_by_id(qid)
        assert row["status"] == "failed"
        assert row["retry_count"] == 1
        assert row["error_log"] == "WP 500 error"
        assert row["worker_id"] == "usopp-1"

        approval_queue.requeue(qid, actor="shosho")

        row = approval_queue.get_by_id(qid)
        assert row["status"] == "pending"
        assert row["retry_count"] == 0
        assert row["error_log"] is None
        assert row["worker_id"] is None
        assert row["claimed_at"] is None

    def test_requeue_from_pending_raises(self):
        """requeue() targets 'failed' rows; calling on a pending row must fail
        with ConcurrentTransitionError (the conditional UPDATE matches 0 rows
        because the row's status is 'pending', not 'failed')."""
        qid = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(),
            operation_id="op_a0040002",
        )
        with pytest.raises(approval_queue.ConcurrentTransitionError):
            approval_queue.requeue(qid, actor="shosho")

    def test_pending_to_pending_illegal(self):
        """FSM still rejects a literal pending → pending transition; only the
        failed → pending edge was added in this PR."""
        qid = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(),
            operation_id="op_a0040003",
        )
        with pytest.raises(approval_queue.IllegalStatusTransitionError):
            approval_queue.transition(
                draft_id=qid,
                from_status="pending",
                to_status="pending",
                actor="shosho",
            )


class TestFSMBoundaryNegative:
    """Lock the boundary of the two new edges (pending→rejected, failed→pending)
    so future ALLOWED_TRANSITIONS edits cannot silently widen the FSM.

    Each case asserts that a status which sits *next to* one of the new edges
    cannot itself reach the new target — i.e. only the explicit edge was added.
    """

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            # only failed→pending was added, not these:
            ("approved", "pending"),
            ("rejected", "pending"),
            ("published", "pending"),
            ("claimed", "pending"),
            ("in_review", "pending"),  # backward-edge into pending forbidden
            # archived is terminal — nothing leaves it:
            ("archived", "pending"),
            ("archived", "approved"),
            ("archived", "in_review"),
            # rejected can only go to archived, not loop back:
            ("rejected", "approved"),
            ("rejected", "in_review"),
            # approved → pending shortcuts the audit/review path; explicitly forbidden:
            ("approved", "in_review"),
            ("approved", "rejected"),
        ],
    )
    def test_illegal_transition_raises(self, from_status: str, to_status: str):
        # We only need the FSM check, which fires before the DB UPDATE — pass an
        # arbitrary draft_id; the IllegalStatusTransitionError must be raised
        # before any row is touched.
        with pytest.raises(approval_queue.IllegalStatusTransitionError):
            approval_queue.transition(
                draft_id=999_999,
                from_status=from_status,
                to_status=to_status,
                actor="cron",
            )


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
