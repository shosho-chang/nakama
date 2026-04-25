"""Tests for thousand_sunny.routers.bridge — memory CRUD + cost API."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from shared import agent_memory, approval_queue, gutenberg_builder, state
from shared.schemas.approval import PublishWpPostV1
from shared.schemas.publishing import (
    BlockNodeV1,
    DraftComplianceV1,
    DraftV1,
    PublishComplianceGateV1,
)


@pytest.fixture
def client(monkeypatch):
    """Bridge router with dev-mode auth (WEB_SECRET unset → check_key returns True)."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("NAKAMA_DEFAULT_USER_ID", "shosho")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge as bridge_module

    importlib.reload(auth_module)
    importlib.reload(bridge_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


@pytest.fixture
def seed_memories():
    """Insert three memories for nami + one for zoro."""
    agent_memory.add(
        agent="nami",
        user_id="shosho",
        type="preference",
        subject="work_hours",
        content="修修偏好早上深度工作",
    )
    agent_memory.add(
        agent="nami", user_id="shosho", type="fact", subject="location", content="修修在台灣"
    )
    agent_memory.add(
        agent="nami",
        user_id="shosho",
        type="decision",
        subject="project_choice",
        content="先做 Bridge UI",
    )
    agent_memory.add(
        agent="zoro",
        user_id="shosho",
        type="preference",
        subject="keyword_channel",
        content="偏好 YouTube 關鍵字",
    )


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------


def test_bridge_index_renders_html(client):
    r = client.get("/bridge")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "NAKAMA / BRIDGE" in body
    assert 'href="/bridge/memory"' in body
    assert 'href="/bridge/cost"' in body
    assert "'/brook/chat'" in body


def test_bridge_index_hides_robin_when_disabled(client):
    # Fixture sets DISABLE_ROBIN=1 → Robin tile shows as disabled with a note.
    r = client.get("/bridge")
    assert "DISABLE_ROBIN" in r.text


def test_memory_page_renders_html(client):
    r = client.get("/bridge/memory")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Bridge · Memory" in body
    assert "/bridge/api/memory" in body


def test_cost_page_renders_html(client):
    r = client.get("/bridge/cost")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Bridge · Cost" in body
    assert "/bridge/api/cost" in body


# ---------------------------------------------------------------------------
# Memory endpoints
# ---------------------------------------------------------------------------


def test_list_agents_returns_distinct_agents(client, seed_memories):
    r = client.get("/bridge/api/memory/agents")
    assert r.status_code == 200
    body = r.json()
    assert set(body["agents"]) == {"nami", "zoro"}


def test_memory_list_filters_by_agent(client, seed_memories):
    r = client.get("/bridge/api/memory?agent=nami")
    assert r.status_code == 200
    body = r.json()
    assert body["agent"] == "nami"
    assert body["user_id"] == "shosho"
    assert len(body["memories"]) == 3
    assert {m["subject"] for m in body["memories"]} == {"work_hours", "location", "project_choice"}


def test_memory_list_empty_for_unknown_agent(client, seed_memories):
    r = client.get("/bridge/api/memory?agent=robin")
    assert r.status_code == 200
    assert r.json()["memories"] == []


def test_memory_patch_content_only(client, seed_memories):
    target = agent_memory.list_all(agent="nami", user_id="shosho")[0]
    r = client.patch(
        f"/bridge/api/memory/{target.id}",
        json={"content": "改過的內容"},
    )
    assert r.status_code == 200
    assert r.json()["content"] == "改過的內容"
    # Untouched fields preserved
    assert r.json()["type"] == target.type
    assert r.json()["subject"] == target.subject


def test_memory_patch_type_and_confidence(client, seed_memories):
    target = agent_memory.list_all(agent="nami", user_id="shosho")[0]
    r = client.patch(
        f"/bridge/api/memory/{target.id}",
        json={"type": "fact", "confidence": 0.5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "fact"
    assert body["confidence"] == 0.5


def test_memory_patch_rejects_bad_confidence(client, seed_memories):
    target = agent_memory.list_all(agent="nami", user_id="shosho")[0]
    r = client.patch(
        f"/bridge/api/memory/{target.id}",
        json={"confidence": 1.5},
    )
    assert r.status_code == 422  # Pydantic validation


def test_memory_patch_404_on_unknown_id(client, seed_memories):
    r = client.patch("/bridge/api/memory/99999", json={"content": "x"})
    assert r.status_code == 404


def test_memory_delete(client, seed_memories):
    target = agent_memory.list_all(agent="nami", user_id="shosho")[0]
    r = client.delete(f"/bridge/api/memory/{target.id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    remaining = agent_memory.list_all(agent="nami", user_id="shosho")
    assert target.id not in {m.id for m in remaining}


def test_memory_delete_404_on_unknown_id(client, seed_memories):
    r = client.delete("/bridge/api/memory/99999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Cost endpoint
# ---------------------------------------------------------------------------


def _seed_api_calls():
    state.record_api_call(
        agent="nami",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=200,
        cache_write_tokens=100,
    )
    state.record_api_call(
        agent="nami",
        model="claude-haiku-4-5",
        input_tokens=500,
        output_tokens=100,
    )
    state.record_api_call(
        agent="zoro",
        model="claude-sonnet-4-6",
        input_tokens=2000,
        output_tokens=1000,
    )


def test_cost_overview_7d_summary_shape(client):
    _seed_api_calls()
    r = client.get("/bridge/api/cost?range=7d")
    assert r.status_code == 200
    body = r.json()

    assert body["range"] == "7d"
    assert body["days"] == 7
    assert body["bucket"] == "day"
    assert body["agent_filter"] is None

    summary = {(row["agent"], row["model"]): row for row in body["summary"]}
    assert ("nami", "claude-sonnet-4-6") in summary
    assert ("nami", "claude-haiku-4-5") in summary
    assert ("zoro", "claude-sonnet-4-6") in summary

    nami_sonnet = summary[("nami", "claude-sonnet-4-6")]
    assert nami_sonnet["input_tokens"] == 1000
    assert nami_sonnet["output_tokens"] == 500
    assert nami_sonnet["cache_read_tokens"] == 200
    assert nami_sonnet["cache_write_tokens"] == 100
    # (1000*3 + 500*15 + 200*0.30 + 100*3.75) / 1e6 = 10935 / 1e6 = 0.010935
    assert nami_sonnet["cost_usd"] == pytest.approx(0.010935, abs=1e-6)


def test_cost_overview_filters_by_agent(client):
    _seed_api_calls()
    r = client.get("/bridge/api/cost?range=7d&agent=nami")
    assert r.status_code == 200
    body = r.json()
    assert body["agent_filter"] == "nami"
    agents_in_summary = {row["agent"] for row in body["summary"]}
    assert agents_in_summary == {"nami"}


def test_cost_overview_24h_uses_hour_bucket(client):
    _seed_api_calls()
    r = client.get("/bridge/api/cost?range=24h")
    assert r.status_code == 200
    body = r.json()
    assert body["bucket"] == "hour"
    # Each bucket should look like 'YYYY-MM-DDTHH:00'
    for row in body["timeseries"]:
        assert len(row["bucket"]) == 16
        assert row["bucket"][13:] == ":00"


def test_cost_overview_rejects_unknown_range(client):
    r = client.get("/bridge/api/cost?range=1y")
    assert r.status_code == 400


def test_cost_overview_empty_when_no_calls(client):
    r = client.get("/bridge/api/cost?range=7d")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == []
    assert body["timeseries"] == []
    assert body["total_cost_usd"] == 0.0


def test_cost_overview_pricing_map_includes_seen_models(client):
    _seed_api_calls()
    r = client.get("/bridge/api/cost?range=7d")
    body = r.json()
    assert set(body["pricing"].keys()) == {"claude-sonnet-4-6", "claude-haiku-4-5"}
    assert body["pricing"]["claude-sonnet-4-6"]["input_usd_per_mtok"] == 3.0


# ---------------------------------------------------------------------------
# Drafts pages — read-only HITL approval queue UI
# ---------------------------------------------------------------------------


def _make_draft(slug: str = "test-article", op_id: str = "op_12345678") -> DraftV1:
    ast = [BlockNodeV1(block_type="paragraph", content=f"Body for {slug}")]
    return DraftV1(
        draft_id=f"draft_20260425T210000_{op_id[-6:]}",
        created_at=datetime.now(timezone.utc),
        agent="brook",
        operation_id=op_id,
        title=f"Title for {slug}",
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


def _enqueue_draft(
    *,
    slug: str = "test-article",
    op_id: str = "op_12345678",
    compliance_flag: bool = False,
    ack: bool = False,
    initial_status: str = "pending",
) -> int:
    payload = PublishWpPostV1(
        action_type="publish_post",
        target_site="wp_shosho",
        draft=_make_draft(slug=slug, op_id=op_id),
        compliance_flags=PublishComplianceGateV1(medical_claim=compliance_flag),
        reviewer_compliance_ack=ack,
    )
    return approval_queue.enqueue(
        source_agent="brook",
        payload_model=payload,
        operation_id=op_id,
        initial_status=initial_status,
    )


# ── Drafts list page ────────────────────────────────────────────────────────


def test_drafts_page_renders_when_empty(client):
    r = client.get("/bridge/drafts")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Bridge · Drafts" in body
    # Empty state copy is intentional, not "No drafts found"
    assert "沒有草稿等你" in body
    assert "list_by_status" in body  # keeps the empty hint pointing reviewers somewhere useful


def test_drafts_page_lists_pending_row(client):
    qid = _enqueue_draft(slug="my-article", op_id="op_aaaa1111")
    r = client.get("/bridge/drafts")
    assert r.status_code == 200
    body = r.text
    assert f"DR-{qid:04d}" in body
    assert "Title for my-article"[:40] in body
    assert "wp_shosho" in body
    assert ">PENDING<" in body
    assert "op_aaaa1111" in body


def test_drafts_page_separates_pending_and_in_review(client):
    pending_id = _enqueue_draft(slug="pending-one", op_id="op_eee11111")
    in_review_id = _enqueue_draft(
        slug="review-one", op_id="op_eee22222", initial_status="in_review"
    )
    r = client.get("/bridge/drafts")
    assert r.status_code == 200
    body = r.text
    assert f"DR-{pending_id:04d}" in body
    assert f"DR-{in_review_id:04d}" in body
    assert ">IN REVIEW<" in body
    assert ">PENDING<" in body


def test_drafts_page_marks_compliance_flag_without_ack(client):
    _enqueue_draft(slug="flagged-one", op_id="op_fff33333", compliance_flag=True, ack=False)
    r = client.get("/bridge/drafts")
    assert "compliance ack required" in r.text


def test_drafts_page_handles_corrupt_payload(client):
    # Insert a row with deliberately broken payload JSON to verify the
    # parse-fail path renders without crashing the whole list.
    qid = _enqueue_draft(slug="broken", op_id="op_bad44444")
    conn = state._get_conn()
    conn.execute(
        "UPDATE approval_queue SET payload = ? WHERE id = ?",
        ("{this is not valid json", qid),
    )
    conn.commit()

    r = client.get("/bridge/drafts")
    assert r.status_code == 200
    body = r.text
    assert ">BROKEN<" in body
    assert "payload parse failed" in body


# ── Drafts detail page ──────────────────────────────────────────────────────


def test_drafts_detail_renders_for_existing_id(client):
    qid = _enqueue_draft(slug="detail-one", op_id="op_dee55555")
    r = client.get(f"/bridge/drafts/{qid}")
    assert r.status_code == 200
    body = r.text
    assert f"DR-{qid:04d}" in body
    assert "Title for detail-one" in body
    assert "publish_post" in body
    assert "wp_shosho" in body
    # Stub buttons present + disabled
    assert "APPROVE" in body
    assert "REJECT" in body
    assert "EDIT PAYLOAD" in body
    assert body.count("disabled") >= 3
    assert "Phase 2" in body


def test_drafts_detail_404_on_unknown_id(client):
    r = client.get("/bridge/drafts/99999")
    assert r.status_code == 404


def test_drafts_detail_renders_compliance_warning(client):
    qid = _enqueue_draft(slug="warn-one", op_id="op_eee66666", compliance_flag=True, ack=False)
    r = client.get(f"/bridge/drafts/{qid}")
    assert r.status_code == 200
    assert "COMPLIANCE" in r.text
    assert "reviewer_compliance_ack" in r.text


def test_drafts_detail_handles_corrupt_payload(client):
    qid = _enqueue_draft(slug="bad-detail", op_id="op_bad77777")
    conn = state._get_conn()
    conn.execute(
        "UPDATE approval_queue SET payload = ? WHERE id = ?",
        ("{not json", qid),
    )
    conn.commit()

    r = client.get(f"/bridge/drafts/{qid}")
    assert r.status_code == 200
    body = r.text
    assert "PARSE ERROR" in body
    assert "manual triage" in body


# ── Hub badge / nav wiring ──────────────────────────────────────────────────


def test_hub_index_shows_pending_count(client):
    _enqueue_draft(slug="hub-1", op_id="op_abc11111")
    _enqueue_draft(slug="hub-2", op_id="op_abc22222")
    r = client.get("/bridge")
    assert r.status_code == 200
    body = r.text
    assert "DRAFTS · PENDING" in body
    assert 'href="/bridge/drafts"' in body
    # Pending count rendered server-side; 2 drafts should appear in the cell
    assert ">2<" in body


def test_hub_index_pending_count_zero_when_empty(client):
    r = client.get("/bridge")
    assert r.status_code == 200
    body = r.text
    assert "DRAFTS · PENDING" in body
    # Zero state copy: queue clear
    assert "queue clear" in body
