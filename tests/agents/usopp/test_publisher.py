"""Tests for agents.usopp.publisher — state machine + idempotency + compliance.

Covers ADR-005b §1 (state machine), §2 (idempotency), §2.1 (advisory lock),
§3 (SEOPress three-tier), §4 (atomic publish), §10 (compliance gate).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from agents.usopp.publisher import (
    CategoryNotMappedError,
    Publisher,
    ValidationMismatchError,
)
from shared import approval_queue, gutenberg_builder
from shared.schemas.approval import PublishWpPostV1
from shared.schemas.external.wordpress import WpPostV1
from shared.schemas.publishing import (
    BlockNodeV1,
    DraftComplianceV1,
    DraftV1,
    PublishComplianceGateV1,
    PublishRequestV1,
)

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _make_draft(
    *,
    slug: str = "example-post",
    title: str = "Helpful Sleep Tips",
    body: str = "Keep a regular bedtime and morning light.",
    op_id: str = "op_12345678",
    primary_category: str = "blog",
) -> DraftV1:
    ast = [BlockNodeV1(block_type="paragraph", content=body)]
    return DraftV1(
        draft_id=f"draft_20260423T120000_{op_id[-6:]}",
        created_at=datetime.now(timezone.utc),
        agent="brook",
        operation_id=op_id,
        title=title,
        slug_candidates=[slug],
        content=gutenberg_builder.build(ast),
        excerpt=("An excerpt of at least twenty characters long to pass the schema validator."),
        primary_category=primary_category,  # type: ignore[arg-type]
        focus_keyword=slug,
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


def _make_request(
    draft: DraftV1,
    *,
    action: str = "publish",
    reviewer: str = "U_SHOSHO",
    scheduled_at=None,
    featured_media_id: int | None = None,
) -> PublishRequestV1:
    return PublishRequestV1(
        draft=draft,
        action=action,  # type: ignore[arg-type]
        reviewer=reviewer,
        scheduled_at=scheduled_at,
        featured_media_id=featured_media_id,
    )


def _enqueue_approved(draft: DraftV1, op_id: str = "op_12345678") -> int:
    """Enqueue, approve, and claim the row — returns queue id in status=claimed.

    Mirrors the daemon's `claim_approved_drafts()` call that wraps publisher.publish().
    """
    payload = PublishWpPostV1(
        action_type="publish_post",
        target_site="wp_shosho",
        draft=draft,
        compliance_flags=PublishComplianceGateV1(),
        reviewer_compliance_ack=False,
    )
    qid = approval_queue.enqueue(
        source_agent="brook",
        payload_model=payload,
        operation_id=op_id,
        initial_status="in_review",
    )
    approval_queue.approve(qid, reviewer="shosho")
    # Daemon would call this next; mirror in test
    approval_queue.claim_approved_drafts(worker_id="usopp-test", source_agent="brook", batch=5)
    return qid


def _wp_post(
    *,
    post_id: int = 42,
    status: str = "draft",
    slug: str = "example-post",
    draft_id: str = "draft_20260423T120000_345678",
) -> WpPostV1:
    """Build a valid WpPostV1 for mocking return values."""
    return WpPostV1.model_validate(
        {
            "id": post_id,
            "date": "2026-04-23T12:00:00",
            "date_gmt": "2026-04-23T12:00:00",
            "guid": {"rendered": f"http://wp.test/?p={post_id}", "protected": False},
            "modified": "2026-04-23T12:00:00",
            "modified_gmt": "2026-04-23T12:00:00",
            "slug": slug,
            "status": status,
            "type": "post",
            "link": f"http://wp.test/{slug}/",
            "title": {"rendered": "Helpful Sleep Tips", "protected": False},
            "content": {"rendered": "<p>...</p>", "protected": False},
            "excerpt": {"rendered": "<p>...</p>", "protected": False},
            "author": 1,
            "featured_media": 0,
            "comment_status": "open",
            "ping_status": "open",
            "sticky": False,
            "template": "",
            "format": "standard",
            "meta": {"nakama_draft_id": draft_id},
            "categories": [],
            "tags": [],
        }
    )


def _mk_happy_wp_client(
    *,
    find_by_meta_hit: bool = False,
    seopress_ok: bool = True,
) -> MagicMock:
    """A MagicMock WP client that walks the happy state-machine path."""
    client = MagicMock()
    client._site_id = "wp_test"
    client.find_by_meta.return_value = (
        _wp_post(post_id=99, status="publish") if find_by_meta_hit else None
    )
    created_post = _wp_post(post_id=42, status="draft")
    client.create_post.return_value = created_post
    client.get_post.return_value = created_post  # default; overridden per call
    client.update_post.return_value = _wp_post(post_id=42, status="publish")

    if seopress_ok:
        client.write_seopress_meta.return_value = (True, "rest")
    else:
        from shared.schemas.external.seopress import SEOPressSchemaDriftError

        client.write_seopress_meta.side_effect = SEOPressSchemaDriftError("drift")
    client.write_seopress_fallback_meta.return_value = True
    client._request.return_value = {"ok": True}  # for litespeed purge
    return client


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_full_publish_succeeds(self, monkeypatch):
        monkeypatch.setenv("LITESPEED_PURGE_METHOD", "noop")
        draft = _make_draft()
        approval_id = _enqueue_approved(draft, op_id=draft.operation_id)

        wp = _mk_happy_wp_client()
        pub = Publisher(
            wp,
            category_map={"blog": 1},
            tag_map={},
        )

        # The state machine calls wp.get_post twice (validate + post-publish); both return a
        # post with the matching draft_id meta.
        wp.get_post.side_effect = [
            _wp_post(post_id=42, status="draft", draft_id=draft.draft_id),
            _wp_post(post_id=42, status="publish", draft_id=draft.draft_id),
        ]

        result = pub.publish(
            _make_request(draft),
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )

        assert result.status == "published"
        assert result.post_id == 42
        assert result.seo_status == "written"
        assert result.cache_purged is False  # LITESPEED_PURGE_METHOD=noop
        # create_post + update_post invoked; find_by_meta checked once
        wp.find_by_meta.assert_called_once()
        wp.create_post.assert_called_once()
        wp.update_post.assert_called_once()

    def test_draft_only_skips_publish_and_purge(self, monkeypatch):
        monkeypatch.setenv("LITESPEED_PURGE_METHOD", "rest")
        draft = _make_draft()
        approval_id = _enqueue_approved(draft, op_id=draft.operation_id)

        wp = _mk_happy_wp_client()
        wp.get_post.side_effect = [
            _wp_post(post_id=42, status="draft", draft_id=draft.draft_id),
            _wp_post(post_id=42, status="draft", draft_id=draft.draft_id),
        ]
        pub = Publisher(wp, category_map={"blog": 1}, tag_map={})

        result = pub.publish(
            _make_request(draft, action="draft_only"),
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )

        assert result.status == "draft_only"
        # update_post should not have been called for status change
        wp.update_post.assert_not_called()
        # purge should not run for draft_only even if method=rest
        assert result.cache_purged is False

    def test_schedule_action_uses_future_status(self, monkeypatch):
        monkeypatch.setenv("LITESPEED_PURGE_METHOD", "noop")
        draft = _make_draft()
        approval_id = _enqueue_approved(draft, op_id=draft.operation_id)
        when = datetime.now(timezone.utc) + timedelta(hours=3)

        wp = _mk_happy_wp_client()
        wp.get_post.side_effect = [
            _wp_post(post_id=42, status="draft", draft_id=draft.draft_id),
            _wp_post(post_id=42, status="future", draft_id=draft.draft_id),
        ]
        pub = Publisher(wp, category_map={"blog": 1}, tag_map={})

        result = pub.publish(
            _make_request(draft, action="schedule", scheduled_at=when),
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )

        assert result.status == "scheduled"
        update_kwargs = wp.update_post.call_args.kwargs
        assert update_kwargs["status"] == "future"
        assert "date_gmt" in update_kwargs


# ---------------------------------------------------------------------------
# Compliance gate
# ---------------------------------------------------------------------------


class TestComplianceGate:
    def test_medical_claim_blocks_publish(self):
        draft = _make_draft(
            title="治癒失眠的終極方法",
            body="這個方法百分之百有效",
        )
        approval_id = _enqueue_approved(draft, op_id=draft.operation_id)
        wp = _mk_happy_wp_client()
        pub = Publisher(wp, category_map={"blog": 1}, tag_map={})

        result = pub.publish(
            _make_request(draft),
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )

        assert result.status == "failed"
        assert "compliance_flag" in (result.failure_reason or "")
        # No WP writes should have occurred
        wp.create_post.assert_not_called()
        wp.update_post.assert_not_called()

    def test_reviewer_ack_bypasses_compliance_block(self, monkeypatch):
        """ADR-005b §10 escape hatch: ack=True lets flagged drafts publish.

        Models Bridge HITL two-step: reviewer saw the flag, confirmed via the
        explicit acknowledgement checkbox, set reviewer_compliance_ack=True,
        then approved. Publisher must honour that decision and not re-block,
        but still persist the flags for audit.
        """
        monkeypatch.setenv("LITESPEED_PURGE_METHOD", "noop")
        draft = _make_draft(
            title="治癒失眠的終極方法",
            body="這個方法百分之百有效",
        )
        # Enqueue with ack=True directly to simulate post-HITL state.
        payload = PublishWpPostV1(
            action_type="publish_post",
            target_site="wp_shosho",
            draft=draft,
            compliance_flags=PublishComplianceGateV1(medical_claim=True, matched_terms=["治癒"]),
            reviewer_compliance_ack=True,
        )
        approval_id = approval_queue.enqueue(
            source_agent="brook",
            payload_model=payload,
            operation_id=draft.operation_id,
            initial_status="in_review",
        )
        approval_queue.approve(approval_id, reviewer="shosho")
        approval_queue.claim_approved_drafts(worker_id="usopp-test", source_agent="brook", batch=5)

        wp = _mk_happy_wp_client()
        wp.get_post.side_effect = [
            _wp_post(post_id=42, status="draft", draft_id=draft.draft_id),
            _wp_post(post_id=42, status="publish", draft_id=draft.draft_id),
        ]
        pub = Publisher(wp, category_map={"blog": 1}, tag_map={})

        result = pub.publish(
            _make_request(draft),
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )

        assert result.status == "published"
        # Flags still persisted in publish_jobs for audit.
        from shared.state import _get_conn

        row = (
            _get_conn()
            .execute(
                "SELECT compliance_flags FROM publish_jobs WHERE draft_id = ?",
                (draft.draft_id,),
            )
            .fetchone()
        )
        assert row is not None
        assert row["compliance_flags"] is not None
        parsed = PublishComplianceGateV1.model_validate_json(row["compliance_flags"])
        assert parsed.medical_claim is True
        assert "治癒" in parsed.matched_terms


# ---------------------------------------------------------------------------
# WP error propagation
# ---------------------------------------------------------------------------


class TestWPErrorPropagation:
    def test_wp_server_error_in_publish_step_marks_failed(self, monkeypatch):
        """Tenacity-exhausted WPServerError in update_post should mark job failed
        rather than bubble out as an unhandled exception.
        """
        monkeypatch.setenv("LITESPEED_PURGE_METHOD", "noop")
        from shared.wordpress_client import WPServerError

        draft = _make_draft()
        approval_id = _enqueue_approved(draft, op_id=draft.operation_id)

        wp = _mk_happy_wp_client()
        # First get_post (validate) succeeds; update_post blows up.
        wp.get_post.side_effect = [
            _wp_post(post_id=42, status="draft", draft_id=draft.draft_id),
        ]
        wp.update_post.side_effect = WPServerError("503 after retries")

        pub = Publisher(wp, category_map={"blog": 1}, tag_map={})
        result = pub.publish(
            _make_request(draft),
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )

        assert result.status == "failed"
        assert "503" in (result.failure_reason or "")
        # approval_queue row moved to failed too
        row = approval_queue.get_by_id(approval_id)
        assert row is not None
        assert row["status"] == "failed"


# ---------------------------------------------------------------------------
# Idempotency (both layers)
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_call_returns_already_published(self, monkeypatch):
        monkeypatch.setenv("LITESPEED_PURGE_METHOD", "noop")
        draft = _make_draft()
        approval_id = _enqueue_approved(draft, op_id=draft.operation_id)

        wp = _mk_happy_wp_client()
        wp.get_post.side_effect = [
            _wp_post(post_id=42, status="draft", draft_id=draft.draft_id),
            _wp_post(post_id=42, status="publish", draft_id=draft.draft_id),
        ]
        pub = Publisher(wp, category_map={"blog": 1}, tag_map={})

        first = pub.publish(
            _make_request(draft),
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )
        assert first.status == "published"

        # Second call — DB row is in state=done, should return already_published w/o
        # invoking more WP calls.
        wp.create_post.reset_mock()
        wp.update_post.reset_mock()
        wp.find_by_meta.reset_mock()

        second = pub.publish(
            _make_request(draft),
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )
        assert second.status == "already_published"
        wp.create_post.assert_not_called()
        wp.update_post.assert_not_called()
        wp.find_by_meta.assert_not_called()

    def test_wp_side_dedup_adopts_orphan_post(self):
        draft = _make_draft()
        approval_id = _enqueue_approved(draft, op_id=draft.operation_id)

        wp = _mk_happy_wp_client(find_by_meta_hit=True)
        # set the orphan's draft_id meta so it looks like a legitimate past publish
        wp.find_by_meta.return_value = _wp_post(
            post_id=77,
            status="publish",
            slug="example-post",
            draft_id=draft.draft_id,
        )
        pub = Publisher(wp, category_map={"blog": 1}, tag_map={})

        result = pub.publish(
            _make_request(draft),
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )

        assert result.status == "already_published"
        assert result.post_id == 77
        # Shouldn't have created a new post
        wp.create_post.assert_not_called()


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------


class TestCrashRecovery:
    def test_resume_from_seo_ready_state(self, monkeypatch):
        """Simulate crash after SEO write but before validate — second run finishes."""
        monkeypatch.setenv("LITESPEED_PURGE_METHOD", "noop")
        draft = _make_draft()
        approval_id = _enqueue_approved(draft, op_id=draft.operation_id)

        # Manually insert a publish_jobs row already in state=seo_ready
        from shared.state import _get_conn

        conn = _get_conn()
        conn.execute(
            """INSERT INTO publish_jobs
               (draft_id, approval_queue_id, operation_id, state,
                state_updated_at, claimed_at, post_id, permalink, seo_status)
               VALUES (?, ?, ?, 'seo_ready', ?, ?, ?, ?, 'written')""",
            (
                draft.draft_id,
                approval_id,
                draft.operation_id,
                "2026-04-23T12:00:00+00:00",
                "2026-04-23T12:00:00+00:00",
                42,
                "http://wp.test/example-post/",
            ),
        )
        conn.commit()

        wp = _mk_happy_wp_client()
        # On resume: validate → update_post → purge. Two get_post calls (validate + post-publish).
        wp.get_post.side_effect = [
            _wp_post(post_id=42, status="draft", draft_id=draft.draft_id),
            _wp_post(post_id=42, status="publish", draft_id=draft.draft_id),
        ]
        pub = Publisher(wp, category_map={"blog": 1}, tag_map={})

        result = pub.publish(
            _make_request(draft),
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )

        assert result.status == "published"
        # create_post should NOT be called — we resumed past it
        wp.create_post.assert_not_called()
        # SEO write should NOT be called either
        wp.write_seopress_meta.assert_not_called()

    def test_resume_from_media_ready_probes_orphan_before_create(self, monkeypatch):
        """Bug #1 regression: resume from media_ready must still probe WP side.

        If the prior run advanced to media_ready then crashed mid-create_post,
        the WP side may already have an orphan post with this draft_id meta.
        Without this check, resume would call create_post again and produce
        a duplicate.
        """
        monkeypatch.setenv("LITESPEED_PURGE_METHOD", "noop")
        draft = _make_draft()
        approval_id = _enqueue_approved(draft, op_id=draft.operation_id)

        # Seed publish_jobs in state=media_ready (mimics crash after advance)
        from shared.state import _get_conn

        conn = _get_conn()
        conn.execute(
            """INSERT INTO publish_jobs
               (draft_id, approval_queue_id, operation_id, state,
                state_updated_at, claimed_at)
               VALUES (?, ?, ?, 'media_ready', ?, ?)""",
            (
                draft.draft_id,
                approval_id,
                draft.operation_id,
                "2026-04-23T12:00:00+00:00",
                "2026-04-23T12:00:00+00:00",
            ),
        )
        conn.commit()

        wp = _mk_happy_wp_client()
        # Orphan exists on WP side — probe must find + adopt it
        wp.find_by_meta.return_value = _wp_post(
            post_id=55,
            status="publish",
            slug="example-post",
            draft_id=draft.draft_id,
        )
        pub = Publisher(wp, category_map={"blog": 1}, tag_map={})

        result = pub.publish(
            _make_request(draft),
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )

        assert result.status == "already_published"
        assert result.post_id == 55
        # Critical: no duplicate create_post call
        wp.create_post.assert_not_called()


# ---------------------------------------------------------------------------
# Scheduled datetime hardening
# ---------------------------------------------------------------------------


class TestScheduleDatetimeHardening:
    def test_naive_scheduled_at_raises_publisher_error(self, monkeypatch):
        """Bug #2 regression: naive datetime should surface as PublisherError
        (which the publish() wrapper catches and records as failed), not as
        an unlabelled ValueError from astimezone().
        """
        monkeypatch.setenv("LITESPEED_PURGE_METHOD", "noop")
        draft = _make_draft()
        approval_id = _enqueue_approved(draft, op_id=draft.operation_id)

        # Bypass PublishRequestV1 validator via model_construct() to simulate
        # a callpath that produced a naive datetime (Bridge/cron/JSON).
        naive = datetime(2026, 4, 23, 12, 0, 0)  # no tzinfo
        bad_request = PublishRequestV1.model_construct(
            schema_version=1,
            draft=draft,
            action="schedule",
            scheduled_at=naive,
            featured_media_id=None,
            reviewer="U_SHOSHO",
        )

        wp = _mk_happy_wp_client()
        wp.get_post.side_effect = [
            _wp_post(post_id=42, status="draft", draft_id=draft.draft_id),
        ]
        pub = Publisher(wp, category_map={"blog": 1}, tag_map={})

        result = pub.publish(
            bad_request,
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )

        assert result.status == "failed"
        assert "timezone-aware" in (result.failure_reason or "")


# ---------------------------------------------------------------------------
# Category / tag handling
# ---------------------------------------------------------------------------


class TestCategoryMapping:
    def test_unknown_primary_category_raises(self):
        draft = _make_draft(primary_category="blog")
        approval_id = _enqueue_approved(draft, op_id=draft.operation_id)
        wp = _mk_happy_wp_client()
        # Category map missing "blog"
        pub = Publisher(wp, category_map={}, tag_map={})

        result = pub.publish(
            _make_request(draft),
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )
        assert result.status == "failed"
        assert "primary_category" in (result.failure_reason or "")

    def test_unknown_primary_category_raises_directly(self):
        """Unit-level: the helper raises when category is missing."""
        draft = _make_draft(primary_category="blog")
        wp = _mk_happy_wp_client()
        pub = Publisher(wp, category_map={}, tag_map={})
        with pytest.raises(CategoryNotMappedError):
            pub._create_draft_post(
                draft=draft,
                featured_media_id=None,
                operation_id=draft.operation_id,
            )


# ---------------------------------------------------------------------------
# Validation mismatch
# ---------------------------------------------------------------------------


class TestValidation:
    def test_meta_mismatch_raises_and_marks_failed(self, monkeypatch):
        monkeypatch.setenv("LITESPEED_PURGE_METHOD", "noop")
        draft = _make_draft()
        approval_id = _enqueue_approved(draft, op_id=draft.operation_id)

        wp = _mk_happy_wp_client()
        # get_post returns a post whose meta doesn't match — triggers ValidationMismatchError
        wp.get_post.side_effect = [
            _wp_post(post_id=42, status="draft", draft_id="draft_WRONG_ID"),
        ]
        pub = Publisher(wp, category_map={"blog": 1}, tag_map={})

        result = pub.publish(
            _make_request(draft),
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )
        assert result.status == "failed"
        assert "mismatch" in (result.failure_reason or "").lower()

    def test_validation_mismatch_type(self):
        """The exception type subclasses PublisherError."""
        assert issubclass(ValidationMismatchError, RuntimeError)


# ---------------------------------------------------------------------------
# SEOPress skip alert
# ---------------------------------------------------------------------------


class TestSEOPressSkipAlert:
    def test_seo_skipped_fires_critical_alert(self, monkeypatch):
        monkeypatch.setenv("LITESPEED_PURGE_METHOD", "noop")
        # Both REST + fallback fail → skipped
        draft = _make_draft()
        approval_id = _enqueue_approved(draft, op_id=draft.operation_id)

        wp = _mk_happy_wp_client(seopress_ok=False)
        wp.write_seopress_fallback_meta.return_value = False
        wp.get_post.side_effect = [
            _wp_post(post_id=42, status="draft", draft_id=draft.draft_id),
            _wp_post(post_id=42, status="publish", draft_id=draft.draft_id),
        ]

        captured_alerts: list[Any] = []

        def capture_dispatch(alert):
            captured_alerts.append(alert)
            return {"dispatched": True}

        monkeypatch.setattr(
            "agents.usopp.publisher.dispatch_alert",
            capture_dispatch,
        )
        pub = Publisher(wp, category_map={"blog": 1}, tag_map={})

        result = pub.publish(
            _make_request(draft),
            approval_queue_id=approval_id,
            operation_id=draft.operation_id,
        )
        assert result.status == "published"
        assert result.seo_status == "skipped"
        assert len(captured_alerts) == 1
        assert captured_alerts[0].rule_id == "seopress_skipped"
        assert captured_alerts[0].severity == "critical"
