"""Phase 1 publish-flow E2E — real WP REST against local Docker staging.

Boot prerequisites (see tests/fixtures/wp_staging/run.sh):
    docker compose up → seed → .env.test → source .env.test

This test exercises the real `agents.usopp.publisher.Publisher` state machine
against a real WordPress + SEOPress 9.4.1 install. LiteSpeed cache purging
still uses `noop` — the Day 1 decision happens in Slice C2b against the
production VPS (Docker image has no LSWS layer to validate against).

No WP-side teardown: each invocation uses a unique `draft_id` + slug so stale
posts do not collide across runs. Wipe via `docker compose down -v`.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from agents.usopp.publisher import Publisher
from shared import approval_queue
from shared.schemas.approval import PublishWpPostV1
from shared.schemas.publishing import (
    DraftV1,
    PublishComplianceGateV1,
    PublishRequestV1,
)
from shared.wordpress_client import WordPressClient

pytestmark = pytest.mark.live_wp


def _enqueue_and_claim(draft: DraftV1, op_id: str) -> int:
    """Mirror `UsoppDaemon._dispatch`'s preconditions: enqueue + approve + claim."""
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
    approval_queue.approve(qid, reviewer="e2e")
    approval_queue.claim_approved_drafts(worker_id="usopp-e2e", source_agent="brook", batch=5)
    return qid


def test_publish_flow_reaches_terminal_state(
    live_wp_client: WordPressClient,
    test_draft_factory: Callable[..., DraftV1],
) -> None:
    draft = test_draft_factory()
    qid = _enqueue_and_claim(draft, op_id=draft.operation_id)

    request = PublishRequestV1(
        draft=draft,
        action="publish",
        reviewer="e2e",
    )
    publisher = Publisher(live_wp_client)
    result = publisher.publish(
        request,
        approval_queue_id=qid,
        operation_id=draft.operation_id,
    )

    assert result.status == "published", (
        f"expected status=published, got {result.status} (reason={result.failure_reason})"
    )
    assert result.post_id is not None
    assert result.permalink and result.permalink.startswith("http://localhost:8888/")
    assert result.seo_status in {"written", "fallback_meta"}
    # LITESPEED_PURGE_METHOD=noop until Slice C2b Day 1 实测
    assert result.cache_purged is False

    # Round-trip: verify the post is retrievable via REST and carries the
    # nakama_draft_id meta that backs ADR-005b §2 idempotency.
    fetched = live_wp_client.get_post(result.post_id)
    assert fetched.status == "publish"
    assert fetched.meta.get("nakama_draft_id") == draft.draft_id
