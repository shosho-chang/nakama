"""Brook compose-and-enqueue E2E happy path (Phase 6 Slice 4).

End-to-end: topic in → ``compose_and_enqueue`` → DraftV1 / PublishWpPostV1 →
``approval_queue`` row landing in ``status='pending'`` for HITL.

What is mocked:
- ``shared.anthropic_client.ask_claude_multi`` — returns fixed AST-shaped JSON
  (``_slice4_helpers.brook_llm_response_json``) so no real Claude call.

What is NOT mocked:
- AST → Gutenberg HTML build (``shared.gutenberg_builder``)
- ``BlockNodeV1`` / ``DraftV1`` / ``PublishWpPostV1`` validation
- ``approval_queue.enqueue`` write path (against ``isolated_db`` autouse tmp DB)
- compliance scan (regex over plaintext)
- tag filter

Marker: none. Runs on every CI invocation.
"""

from __future__ import annotations

import pytest

from shared import approval_queue
from tests.e2e._slice4_helpers import fake_ask_claude_multi


@pytest.fixture
def mock_brook_llm(monkeypatch):
    """Patch the Anthropic call site Brook uses."""
    monkeypatch.setattr("agents.brook.compose.ask_claude_multi", fake_ask_claude_multi)


def test_brook_compose_and_enqueue_happy_path(mock_brook_llm):
    """Topic → compose_and_enqueue → approval_queue row in 'pending'."""
    from agents.brook.compose import compose_and_enqueue

    result = compose_and_enqueue(
        topic="how sleep cycles work",
        category="science",
        kb_context="",
        source_content="",
    )

    # Return shape contract
    assert isinstance(result["queue_row_id"], int)
    assert result["queue_row_id"] > 0
    assert result["draft_id"].startswith("draft_")
    assert result["operation_id"].startswith("op_")
    assert result["category"] == "science"
    assert result["title"] == "Brook E2E happy path test article"
    # compliance_flags is a real PublishComplianceGateV1, not just a dict
    assert hasattr(result["compliance_flags"], "medical_claim")
    # tag_filter accepted "sleep" — locks lenient-pass semantics; will fail loud
    # if the whitelist flips to strict and "sleep" gets dropped (or we change
    # the helper payload to a tag not in the whitelist)
    assert result["tag_filter_rejected"] == []

    # Row landed in approval_queue with the correct shape
    row = approval_queue.get_by_id(result["queue_row_id"])
    assert row is not None
    assert row["status"] == "pending"
    assert row["source_agent"] == "brook"
    assert row["target_platform"] == "wordpress"
    assert row["target_site"] == "wp_shosho"
    assert row["action_type"] == "publish_post"
    assert row["payload_version"] == 1
    assert row["title_snippet"].startswith("Brook E2E")
    assert row["operation_id"] == result["operation_id"]


def test_brook_enqueue_visible_via_list_by_status(mock_brook_llm):
    """Smoke: row enqueued by happy path is visible via the public list API."""
    from agents.brook.compose import compose_and_enqueue

    result = compose_and_enqueue(
        topic="circadian rhythms and shift work",
        category="science",
    )

    pending = approval_queue.list_by_status("pending", source_agent="brook")
    ids = [r["id"] for r in pending]
    assert result["queue_row_id"] in ids
