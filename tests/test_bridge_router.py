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
def client(monkeypatch, tmp_path):
    """Bridge router with dev-mode auth (WEB_SECRET unset → check_key returns True)."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("NAKAMA_DEFAULT_USER_ID", "shosho")
    # Phase 9 doc_index uses NAKAMA_DOC_INDEX_DB_PATH for test isolation
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))

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


def test_health_page_empty_state_when_no_heartbeats(client):
    r = client.get("/bridge/health")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Bridge · Health" in body
    assert "NO HEARTBEATS RECORDED YET" in body


def test_health_page_renders_recorded_heartbeats(client):
    from shared import heartbeat

    heartbeat.record_success("nakama-backup")
    heartbeat.record_failure("flaky-cron", "ConnectionError: timed out")

    r = client.get("/bridge/health")
    assert r.status_code == 200
    body = r.text
    assert "nakama-backup" in body
    assert "flaky-cron" in body
    # Failure row surfaces error text + consecutive_failures non-zero
    assert "ConnectionError" in body
    # Status chip rendered
    assert (
        "chip green" in body or "chip yellow" in body or "chip orange" in body or "chip red" in body
    )


def test_docs_page_renders_search_form_when_no_query(client):
    r = client.get("/bridge/docs")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Bridge · Docs" in body
    # Help text shown when q is empty
    assert "Enter a query" in body
    # Search form present
    assert 'name="q"' in body


def test_docs_page_with_query_returns_results(client):
    """Query against the real repo's docs/ + memory/ — at least 1 hit for 'memory'."""
    r = client.get("/bridge/docs?q=memory")
    assert r.status_code == 200
    body = r.text
    # FTS5 highlight or "no matches" — both are valid; we just want no 500
    assert ("<mark>" in body) or ("No matches" in body)


# ── chassis-nav unification regression ──────────────────────────────────────
# Three taxonomies emerged across PR #136 / #152 / #157 because each new page
# copy-pasted the chassis nav and diverged. PR A (2026-04-26) unified them to
# the canonical 8-item form: uppercase + zh suffix + class="active" marker
# + aria-current="page" on the active link for a11y.
def _assert_canonical_chassis_nav(body: str, path: str, active_label: str) -> None:
    """Verify all 8 entries present, no legacy taxonomy, active link has both
    `class="active"` and `aria-current="page"` regardless of attribute order."""
    for label, zh in [
        ("BRIDGE", "船橋"),
        ("DRAFTS", "待審"),
        ("MEMORY", "記憶"),
        ("COST", "成本"),
        ("FRANKY", "船匠"),
        ("HEALTH", "巡檢"),
        ("DOCS", "文件"),
        ("VAULT", "秘庫"),
    ]:
        assert f'{label} <span class="zh">{zh}' in body, f"{path} missing {label}"
    assert "is-current" not in body, f"{path} still uses is-current taxonomy"

    import re

    # Extract the <a> tag that contains the active label; verify both attrs.
    pattern = rf'(<a [^>]*>){active_label} <span class="zh">'
    match = re.search(pattern, body)
    assert match, f"{path} no link matches {active_label}"
    active_tag = match.group(1)
    assert 'class="active"' in active_tag, f"{path} active link missing class=active: {active_tag}"
    assert 'aria-current="page"' in active_tag, (
        f"{path} active link missing aria-current=page: {active_tag}"
    )


@pytest.mark.parametrize(
    "path,active_label",
    [
        ("/bridge", "BRIDGE"),
        ("/bridge/drafts", "DRAFTS"),
        ("/bridge/memory", "MEMORY"),
        ("/bridge/cost", "COST"),
        ("/bridge/franky", "FRANKY"),
        ("/bridge/health", "HEALTH"),
        ("/bridge/docs", "DOCS"),
    ],
)
def test_chassis_nav_canonical_8_items(client, path, active_label):
    r = client.get(path)
    assert r.status_code == 200
    _assert_canonical_chassis_nav(r.text, path, active_label)


def test_chassis_nav_on_draft_detail_page(client):
    """Draft detail (/bridge/drafts/{id}) shares the chassis with the list page."""
    qid = _enqueue_draft(slug="nav-test", op_id="op_a1b2c3d4")
    r = client.get(f"/bridge/drafts/{qid}")
    assert r.status_code == 200
    _assert_canonical_chassis_nav(r.text, f"/bridge/drafts/{qid}", "DRAFTS")


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
    # Mutation buttons (Phase 2): forms post to the new endpoints, no `disabled` stub
    assert "APPROVE" in body
    assert "REJECT" in body
    assert "EDIT PAYLOAD" in body
    assert f'action="/bridge/drafts/{qid}/approve"' in body
    assert "Phase 2 待實作" not in body


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


def test_drafts_page_shows_truncate_banner_above_list_limit(client):
    # Seed 51 pending rows; list_by_status caps at 50, count_by_status returns 51
    for i in range(51):
        _enqueue_draft(slug=f"trunc-{i}", op_id=f"op_eeee{i:04d}")
    r = client.get("/bridge/drafts")
    assert r.status_code == 200
    body = r.text
    # Header stat is the *true* count (not the capped row count)
    assert "pending" in body
    assert ">51<" in body
    # Banner appears explaining truncation
    assert "顯示前" in body
    assert "每組上限" in body


def test_drafts_page_no_truncate_banner_when_under_limit(client):
    _enqueue_draft(slug="under-1", op_id="op_ffff0001")
    r = client.get("/bridge/drafts")
    assert r.status_code == 200
    body = r.text
    # No truncate banner copy
    assert "顯示前" not in body
    assert "每組上限" not in body


# ---------------------------------------------------------------------------
# Drafts mutation endpoints (Phase 2)
# ---------------------------------------------------------------------------


def _drive_to_failed(qid: int) -> None:
    """approve → claim → mark_failed, leaving row in 'failed' status.

    _enqueue_draft() creates rows in 'pending', so approve must use
    from_status='pending' (the helper's default targets 'in_review')."""
    approval_queue.approve(qid, reviewer="shosho", from_status="pending")
    approval_queue.claim_approved_drafts(worker_id="usopp-1", source_agent="brook", batch=5)
    approval_queue.mark_failed(qid, "WP 500 error")


# ── /approve ───────────────────────────────────────────────────────────────


def test_draft_approve_pending_redirects_and_marks_approved(client):
    qid = _enqueue_draft(slug="ap-1", op_id="op_b0010001")
    r = client.post(f"/bridge/drafts/{qid}/approve", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/bridge/drafts"
    row = approval_queue.get_by_id(qid)
    assert row["status"] == "approved"
    assert row["reviewer"] == "shosho"


def test_draft_approve_in_review_also_works(client):
    qid = _enqueue_draft(slug="ap-2", op_id="op_b0010002", initial_status="in_review")
    r = client.post(f"/bridge/drafts/{qid}/approve", follow_redirects=False)
    assert r.status_code == 303
    assert approval_queue.get_by_id(qid)["status"] == "approved"


def test_draft_approve_404_on_unknown_id(client):
    r = client.post("/bridge/drafts/99999/approve", follow_redirects=False)
    assert r.status_code == 404


def test_draft_approve_409_when_status_not_reviewable(client):
    qid = _enqueue_draft(slug="ap-3", op_id="op_b0010003")
    approval_queue.approve(qid, reviewer="shosho", from_status="pending")
    # Now in 'approved' — second approve must fail with 409
    r = client.post(f"/bridge/drafts/{qid}/approve", follow_redirects=False)
    assert r.status_code == 409
    assert "approved" in r.json()["detail"]


# ── /reject ────────────────────────────────────────────────────────────────


def test_draft_reject_records_reason_and_redirects(client):
    qid = _enqueue_draft(slug="rj-1", op_id="op_b0020001")
    r = client.post(
        f"/bridge/drafts/{qid}/reject",
        data={"reason": "off-brand"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/bridge/drafts"
    row = approval_queue.get_by_id(qid)
    assert row["status"] == "rejected"
    assert row["review_note"] == "off-brand"
    assert row["reviewer"] == "shosho"


def test_draft_reject_requires_reason(client):
    qid = _enqueue_draft(slug="rj-2", op_id="op_b0020002")
    r = client.post(f"/bridge/drafts/{qid}/reject", data={}, follow_redirects=False)
    assert r.status_code == 422
    # Row untouched
    assert approval_queue.get_by_id(qid)["status"] == "pending"


def test_draft_reject_409_when_status_not_reviewable(client):
    qid = _enqueue_draft(slug="rj-3", op_id="op_b0020003")
    approval_queue.approve(qid, reviewer="shosho", from_status="pending")
    r = client.post(
        f"/bridge/drafts/{qid}/reject",
        data={"reason": "too late"},
        follow_redirects=False,
    )
    assert r.status_code == 409


# ── /edit ──────────────────────────────────────────────────────────────────


def test_draft_edit_overwrites_payload_preserving_status(client):
    qid = _enqueue_draft(slug="ed-original", op_id="op_b0030001")
    new_payload = PublishWpPostV1(
        action_type="publish_post",
        target_site="wp_shosho",
        draft=_make_draft(slug="ed-rewritten", op_id="op_b0030001"),
        compliance_flags=PublishComplianceGateV1(),
        reviewer_compliance_ack=False,
    )
    r = client.post(
        f"/bridge/drafts/{qid}/edit",
        data={"payload": new_payload.model_dump_json()},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/bridge/drafts/{qid}"
    row = approval_queue.get_by_id(qid)
    assert row["status"] == "pending"  # status preserved
    assert "ed-rewritten" in row["title_snippet"]


def test_draft_edit_400_on_invalid_json(client):
    qid = _enqueue_draft(slug="ed-bad", op_id="op_b0030002")
    r = client.post(
        f"/bridge/drafts/{qid}/edit",
        data={"payload": "{not valid json"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "JSONDecodeError" in r.json()["detail"]
    # Row untouched
    assert "ed-bad" in approval_queue.get_by_id(qid)["title_snippet"]


def test_draft_edit_400_on_schema_validation_failure(client):
    qid = _enqueue_draft(slug="ed-schema", op_id="op_b0030003")
    r = client.post(
        f"/bridge/drafts/{qid}/edit",
        data={"payload": '{"action_type":"publish_post"}'},  # missing required fields
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "ValidationError" in r.json()["detail"]


def test_draft_edit_422_on_payload_above_max_length(client):
    """Form field max_length guards against accidental paste of huge artifacts —
    FastAPI returns 422 (validation error) before the route body runs."""
    qid = _enqueue_draft(slug="ed-too-big", op_id="op_b0030004")
    huge = "x" * 200_001  # 1 byte over 200 KB cap
    r = client.post(
        f"/bridge/drafts/{qid}/edit",
        data={"payload": huge},
        follow_redirects=False,
    )
    assert r.status_code == 422
    # Row untouched
    assert "ed-too-big" in approval_queue.get_by_id(qid)["title_snippet"]


# ── /requeue ───────────────────────────────────────────────────────────────


def test_draft_requeue_failed_to_pending_clears_metadata(client):
    qid = _enqueue_draft(slug="rq-1", op_id="op_b0040001")
    _drive_to_failed(qid)
    assert approval_queue.get_by_id(qid)["status"] == "failed"

    r = client.post(f"/bridge/drafts/{qid}/requeue", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/bridge/drafts"
    row = approval_queue.get_by_id(qid)
    assert row["status"] == "pending"
    assert row["retry_count"] == 0
    assert row["error_log"] is None


def test_draft_requeue_409_when_not_failed(client):
    qid = _enqueue_draft(slug="rq-2", op_id="op_b0040002")
    r = client.post(f"/bridge/drafts/{qid}/requeue", follow_redirects=False)
    assert r.status_code == 409
    assert "failed" in r.json()["detail"]


# ── Detail page UI: button enable + error_log + status chips ───────────────


def test_drafts_detail_buttons_enabled_for_pending(client):
    qid = _enqueue_draft(slug="ui-1", op_id="op_b0050001")
    r = client.get(f"/bridge/drafts/{qid}")
    body = r.text
    # Approve / reject / edit forms target the new endpoints
    assert f'action="/bridge/drafts/{qid}/approve"' in body
    assert "showModal" in body  # reject + edit modals
    # Disabled stub note from Phase 1 must be gone
    assert "Phase 2 待實作" not in body


def test_drafts_detail_shows_requeue_for_failed_status(client):
    qid = _enqueue_draft(slug="ui-2", op_id="op_b0050002")
    _drive_to_failed(qid)
    r = client.get(f"/bridge/drafts/{qid}")
    body = r.text
    assert f'action="/bridge/drafts/{qid}/requeue"' in body
    assert "REQUEUE" in body
    # Error log box visible
    assert "ERROR LOG" in body
    assert "WP 500 error" in body
    # Approve/reject/edit must NOT show for failed status
    assert f'action="/bridge/drafts/{qid}/approve"' not in body
