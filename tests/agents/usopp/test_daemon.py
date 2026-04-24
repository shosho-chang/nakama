"""Tests for agents.usopp.__main__ UsoppDaemon — Slice C1.

Covers poll / dispatch / reviewer lookup / action inference / update_post skip /
signal-driven shutdown. State machine + publish flow itself is covered by
test_publisher.py — these tests stop at the Publisher boundary with a mock.
"""

from __future__ import annotations

import signal
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from agents.usopp.__main__ import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_POLL_INTERVAL_S,
    UsoppDaemon,
    _build_from_env,
    _lookup_reviewer,
)
from shared import approval_queue, gutenberg_builder
from shared.schemas.approval import PublishWpPostV1, UpdateWpPostV1
from shared.schemas.publishing import (
    BlockNodeV1,
    DraftComplianceV1,
    DraftV1,
    PublishComplianceGateV1,
    PublishResultV1,
)

# ---------------------------------------------------------------------------
# Fixtures + helpers (kept independent from test_publisher.py's private helpers)
# ---------------------------------------------------------------------------


def _make_draft(op_id: str = "op_12345678") -> DraftV1:
    ast = [BlockNodeV1(block_type="paragraph", content="Keep a regular bedtime.")]
    return DraftV1(
        draft_id=f"draft_20260424T120000_{op_id[-6:]}",
        created_at=datetime.now(timezone.utc),
        agent="brook",
        operation_id=op_id,
        title="Sleep Tips",
        slug_candidates=["sleep-tips"],
        content=gutenberg_builder.build(ast),
        excerpt="An excerpt of at least twenty characters long for schema ok.",
        primary_category="blog",
        focus_keyword="sleep",
        meta_description=(
            "A meta description with at least fifty characters to pass validator ok here."
        ),
        compliance=DraftComplianceV1(
            schema_version=1,
            claims_no_therapeutic_effect=True,
            has_disclaimer=False,
        ),
        style_profile_id="blog@0.1.0",
    )


def _enqueue_approved_publish(
    draft: DraftV1,
    *,
    op_id: str = "op_12345678",
    scheduled_at=None,
    reviewer: str = "U_SHOSHO",
) -> int:
    payload = PublishWpPostV1(
        action_type="publish_post",
        target_site="wp_shosho",
        draft=draft,
        compliance_flags=PublishComplianceGateV1(),
        reviewer_compliance_ack=False,
        scheduled_at=scheduled_at,
    )
    qid = approval_queue.enqueue(
        source_agent="brook",
        payload_model=payload,
        operation_id=op_id,
        initial_status="in_review",
    )
    approval_queue.approve(qid, reviewer=reviewer)
    return qid


def _enqueue_approved_update(draft: DraftV1, op_id: str = "op_87654321") -> int:
    payload = UpdateWpPostV1(
        action_type="update_post",
        target_site="wp_shosho",
        wp_post_id=42,
        patch={"title": "Updated"},
        change_summary="fix typo",
        draft_id=draft.draft_id,
        compliance_flags=PublishComplianceGateV1(),
        reviewer_compliance_ack=False,
    )
    qid = approval_queue.enqueue(
        source_agent="brook",
        payload_model=payload,
        operation_id=op_id,
        initial_status="in_review",
    )
    approval_queue.approve(qid, reviewer="U_SHOSHO")
    return qid


def _make_daemon(
    *,
    publisher: MagicMock | None = None,
    poll_interval_s: int = 30,
    batch_size: int = 5,
) -> UsoppDaemon:
    wp = MagicMock()
    wp.site_id = "wp_shosho"
    pub = publisher or MagicMock()
    return UsoppDaemon(
        wp_client=wp,
        publisher=pub,
        worker_id="usopp-test",
        poll_interval_s=poll_interval_s,
        batch_size=batch_size,
    )


def _ok_publish_result(op_id: str = "op_12345678") -> PublishResultV1:
    return PublishResultV1(
        status="published",
        post_id=42,
        permalink="https://shosho.tw/p/42",
        seo_status="written",
        cache_purged=True,
        operation_id=op_id,
        completed_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# run_once — claim + dispatch plumbing
# ---------------------------------------------------------------------------


class TestRunOnce:
    def test_empty_queue_returns_zero_and_does_not_invoke_publisher(self):
        pub = MagicMock()
        daemon = _make_daemon(publisher=pub)
        assert daemon.run_once() == 0
        pub.publish.assert_not_called()

    def test_happy_path_dispatches_to_publisher_with_built_request(self):
        draft = _make_draft()
        qid = _enqueue_approved_publish(draft, op_id=draft.operation_id)

        pub = MagicMock()
        pub.publish.return_value = _ok_publish_result(draft.operation_id)
        daemon = _make_daemon(publisher=pub)

        processed = daemon.run_once()

        assert processed == 1
        pub.publish.assert_called_once()
        # Inspect the PublishRequestV1 we built
        call_kwargs = pub.publish.call_args.kwargs
        call_request = pub.publish.call_args.args[0]
        assert call_kwargs["approval_queue_id"] == qid
        assert call_kwargs["operation_id"] == draft.operation_id
        assert call_request.draft.draft_id == draft.draft_id
        assert call_request.action == "publish"  # no scheduled_at
        assert call_request.scheduled_at is None
        assert call_request.reviewer == "U_SHOSHO"

    def test_scheduled_at_maps_to_action_schedule(self):
        draft = _make_draft()
        future = datetime.now(timezone.utc) + timedelta(hours=3)
        _enqueue_approved_publish(draft, op_id=draft.operation_id, scheduled_at=future)

        pub = MagicMock()
        pub.publish.return_value = _ok_publish_result(draft.operation_id)
        daemon = _make_daemon(publisher=pub)
        daemon.run_once()

        call_request = pub.publish.call_args.args[0]
        assert call_request.action == "schedule"
        assert call_request.scheduled_at == future

    def test_update_post_is_marked_failed_and_publisher_not_called(self):
        draft = _make_draft()
        qid = _enqueue_approved_update(draft)

        pub = MagicMock()
        daemon = _make_daemon(publisher=pub)
        daemon.run_once()

        pub.publish.assert_not_called()
        # Row should now be in 'failed' with a helpful error_log
        from shared.state import _get_conn

        row = (
            _get_conn()
            .execute("SELECT status, error_log FROM approval_queue WHERE id = ?", (qid,))
            .fetchone()
        )
        assert row["status"] == "failed"
        assert "update_post" in row["error_log"]
        assert "Phase 1" in row["error_log"]

    def test_unexpected_publisher_exception_marks_failed_and_survives(self):
        draft = _make_draft()
        qid = _enqueue_approved_publish(draft, op_id=draft.operation_id)

        pub = MagicMock()
        pub.publish.side_effect = RuntimeError("surprise!")
        daemon = _make_daemon(publisher=pub)

        # Must not raise — daemon must keep living
        processed = daemon.run_once()
        assert processed == 1

        from shared.state import _get_conn

        row = (
            _get_conn()
            .execute(
                "SELECT status, error_log, retry_count FROM approval_queue WHERE id = ?",
                (qid,),
            )
            .fetchone()
        )
        assert row["status"] == "failed"
        assert "RuntimeError" in row["error_log"]
        assert row["retry_count"] == 1  # unexpected path increments retry

    def test_batch_size_passed_through_to_claim(self, monkeypatch):
        draft1 = _make_draft(op_id="op_aaaaaaaa")
        draft2 = _make_draft(op_id="op_bbbbbbbb")
        _enqueue_approved_publish(draft1, op_id=draft1.operation_id)
        _enqueue_approved_publish(draft2, op_id=draft2.operation_id)

        pub = MagicMock()
        pub.publish.side_effect = [
            _ok_publish_result(draft1.operation_id),
            _ok_publish_result(draft2.operation_id),
        ]
        daemon = _make_daemon(publisher=pub, batch_size=1)
        assert daemon.run_once() == 1
        assert pub.publish.call_count == 1
        # Next tick drains the second one
        assert daemon.run_once() == 1
        assert pub.publish.call_count == 2


# ---------------------------------------------------------------------------
# reviewer lookup
# ---------------------------------------------------------------------------


class TestReviewerLookup:
    def test_reads_reviewer_from_approval_queue_row(self):
        draft = _make_draft()
        qid = _enqueue_approved_publish(draft, op_id=draft.operation_id, reviewer="U_ALICE")
        assert _lookup_reviewer(qid) == "U_ALICE"

    def test_unknown_reviewer_falls_back_when_null(self):
        from shared.state import _get_conn

        draft = _make_draft()
        qid = _enqueue_approved_publish(draft, op_id=draft.operation_id)
        # Force reviewer NULL — defensive case; approve() should normally have written it
        _get_conn().execute("UPDATE approval_queue SET reviewer = NULL WHERE id = ?", (qid,))
        _get_conn().commit()
        assert _lookup_reviewer(qid) == "unknown"

    def test_missing_row_falls_back_to_unknown(self):
        assert _lookup_reviewer(999999) == "unknown"

    def test_warning_log_carries_operation_id(self, caplog):
        """observability.md §2: warning on NULL reviewer must correlate to the draft's op."""
        import logging

        draft = _make_draft()
        qid = _enqueue_approved_publish(draft, op_id=draft.operation_id)
        from shared.state import _get_conn

        _get_conn().execute("UPDATE approval_queue SET reviewer = NULL WHERE id = ?", (qid,))
        _get_conn().commit()

        with caplog.at_level(logging.WARNING, logger="nakama.usopp.daemon"):
            _lookup_reviewer(qid, operation_id="op_testoprn")

        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "op=op_testoprn" in joined
        assert f"id={qid}" in joined


# ---------------------------------------------------------------------------
# Signal-driven shutdown + interruptible sleep
# ---------------------------------------------------------------------------


class TestShutdown:
    def test_request_shutdown_sets_flag(self):
        daemon = _make_daemon()
        assert daemon._shutdown is False
        daemon.request_shutdown(signal.SIGTERM, None)
        assert daemon._shutdown is True

    def test_request_shutdown_is_idempotent(self):
        daemon = _make_daemon()
        daemon.request_shutdown(signal.SIGTERM, None)
        daemon.request_shutdown(signal.SIGTERM, None)
        assert daemon._shutdown is True

    def test_sleep_interruptible_wakes_promptly_on_shutdown(self):
        daemon = _make_daemon(poll_interval_s=5)

        def trip():
            time.sleep(0.1)
            daemon._shutdown = True

        t = threading.Thread(target=trip, daemon=True)
        started = time.monotonic()
        t.start()
        daemon._sleep_interruptible()
        elapsed = time.monotonic() - started
        # Should exit within ~1.1s because sleep is chunked at 1s
        assert elapsed < 1.5, f"sleep did not wake promptly: elapsed={elapsed:.2f}s"

    def test_run_exits_cleanly_when_already_shutdown(self, monkeypatch):
        """If someone trips _shutdown before .run() is called, loop exits without work."""
        draft = _make_draft()
        _enqueue_approved_publish(draft, op_id=draft.operation_id)

        pub = MagicMock()
        daemon = _make_daemon(publisher=pub, poll_interval_s=1)
        daemon._shutdown = True

        # Neuter signal registration — pytest main thread can't install handlers on all OSes
        monkeypatch.setattr(signal, "signal", lambda *a, **kw: None)
        daemon.run()
        pub.publish.assert_not_called()


# ---------------------------------------------------------------------------
# env-driven factory
# ---------------------------------------------------------------------------


class TestBuildFromEnv:
    def test_defaults_when_env_absent(self, monkeypatch):
        monkeypatch.setenv("WP_SHOSHO_BASE_URL", "http://wp.test/wp-json")
        monkeypatch.setenv("WP_SHOSHO_USERNAME", "nakama_publisher")
        monkeypatch.setenv("WP_SHOSHO_APP_PASSWORD", "abcd efgh ijkl mnop")
        monkeypatch.delenv("USOPP_POLL_INTERVAL_S", raising=False)
        monkeypatch.delenv("USOPP_BATCH_SIZE", raising=False)
        monkeypatch.delenv("USOPP_WORKER_ID", raising=False)
        monkeypatch.delenv("USOPP_TARGET_SITE", raising=False)

        daemon = _build_from_env()
        assert daemon.poll_interval_s == DEFAULT_POLL_INTERVAL_S
        assert daemon.batch_size == DEFAULT_BATCH_SIZE
        assert daemon.worker_id.startswith("usopp-")
        assert daemon.wp.site_id == "wp_shosho"

    def test_env_overrides_applied(self, monkeypatch):
        monkeypatch.setenv("WP_SHOSHO_BASE_URL", "http://wp.test/wp-json")
        monkeypatch.setenv("WP_SHOSHO_USERNAME", "nakama_publisher")
        monkeypatch.setenv("WP_SHOSHO_APP_PASSWORD", "abcd efgh ijkl mnop")
        monkeypatch.setenv("USOPP_POLL_INTERVAL_S", "7")
        monkeypatch.setenv("USOPP_BATCH_SIZE", "3")
        monkeypatch.setenv("USOPP_WORKER_ID", "usopp-ci-runner")

        daemon = _build_from_env()
        assert daemon.poll_interval_s == 7
        assert daemon.batch_size == 3
        assert daemon.worker_id == "usopp-ci-runner"


@pytest.mark.skipif(
    not hasattr(signal, "SIGTERM"),
    reason="signal module missing SIGTERM (very unusual)",
)
def test_signal_constants_present():
    """Sanity — daemon wires SIGTERM + SIGINT, this just guards the test suite."""
    assert signal.SIGTERM
    assert signal.SIGINT
