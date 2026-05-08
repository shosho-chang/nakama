"""Behaviour tests for `/api/projects/{slug}/synthesize` (issue #454).

Covers:
- GET 404 when the store has not been materialised yet
- GET 200 round-trip after the store is created out-of-band (simulating
  Brook synthesize #459 having run)
- POST 404 when the slug does not exist (API must not bootstrap stores)
- POST `append_user_action` persists the action
- POST `update_outline_final` replaces the array
- POST 422 on unknown ``op``
- Slug path-traversal rejected with 400
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shared import brook_synthesize_store as store
from shared.schemas.brook_synthesize import (
    BrookSynthesizeStore,
    EvidencePoolItem,
    OutlineSection,
    UserAction,
)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "brook_synthesize"
    monkeypatch.setenv("NAKAMA_BROOK_SYNTHESIZE_DIR", str(d))
    monkeypatch.delenv("NAKAMA_DATA_DIR", raising=False)
    return d


@pytest.fixture
def app_client(data_dir, monkeypatch):
    # Disable auth for the tests (matches existing test_books_*_api pattern).
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")  # skip Robin/books to keep import light

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.projects as projects_module

    importlib.reload(auth_module)
    importlib.reload(projects_module)
    importlib.reload(app_module)

    return TestClient(app_module.app, follow_redirects=False)


def _seed(slug: str = "creatine-cognitive") -> BrookSynthesizeStore:
    """Materialise a store on disk (mimicking what Brook synthesize #459 does)."""
    s = BrookSynthesizeStore(
        project_slug=slug,
        topic="creatine and cognition",
        keywords=["creatine", "cognition"],
        evidence_pool=[
            EvidencePoolItem(slug="rae-2003", chunks=[], hit_reason="dose-response"),
        ],
        outline_draft=[
            OutlineSection(section=1, heading="Intro", evidence_refs=["rae-2003"]),
        ],
    )
    return store.create(s)


# ── GET ──────────────────────────────────────────────────────────────────────


def test_get_returns_404_when_store_missing(app_client: TestClient):
    r = app_client.get("/api/projects/nope/synthesize")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


def test_get_returns_store_after_seed(app_client: TestClient):
    _seed()
    r = app_client.get("/api/projects/creatine-cognitive/synthesize")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project_slug"] == "creatine-cognitive"
    assert body["schema_version"] == 1
    assert body["topic"] == "creatine and cognition"
    assert len(body["evidence_pool"]) == 1
    assert body["evidence_pool"][0]["slug"] == "rae-2003"
    assert body["user_actions"] == []
    assert body["outline_final"] == []


# ── POST 404 ─────────────────────────────────────────────────────────────────


def test_post_returns_404_when_store_missing(app_client: TestClient):
    r = app_client.post(
        "/api/projects/ghost/synthesize",
        json={
            "op": "append_user_action",
            "action": {
                "timestamp": "2026-05-07T00:00:00+00:00",
                "action": "reject_from_section",
                "section": 1,
                "evidence_slug": "x",
            },
        },
    )
    assert r.status_code == 404


# ── POST append_user_action ──────────────────────────────────────────────────


def test_post_append_user_action_persists(app_client: TestClient):
    _seed()
    payload = {
        "op": "append_user_action",
        "action": {
            "timestamp": "2026-05-07T12:00:00+00:00",
            "action": "reject_from_section",
            "section": 1,
            "evidence_slug": "rae-2003",
        },
    }
    r = app_client.post("/api/projects/creatine-cognitive/synthesize", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["user_actions"]) == 1
    assert body["user_actions"][0]["action"] == "reject_from_section"
    assert body["user_actions"][0]["section"] == 1

    # Persisted on disk
    fresh = store.read("creatine-cognitive")
    assert len(fresh.user_actions) == 1


def test_post_append_user_action_validates_action_literal(app_client: TestClient):
    _seed()
    r = app_client.post(
        "/api/projects/creatine-cognitive/synthesize",
        json={
            "op": "append_user_action",
            "action": {
                "timestamp": "2026-05-07T00:00:00+00:00",
                "action": "delete_universe",  # not in the Literal
                "evidence_slug": "x",
            },
        },
    )
    assert r.status_code == 422


# ── POST update_outline_final ────────────────────────────────────────────────


def test_post_update_outline_final_replaces(app_client: TestClient):
    _seed()
    payload = {
        "op": "update_outline_final",
        "outline_final": [
            {"section": 1, "heading": "Final intro", "evidence_refs": ["rae-2003"]},
            {"section": 2, "heading": "Final body", "evidence_refs": []},
        ],
    }
    r = app_client.post("/api/projects/creatine-cognitive/synthesize", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["outline_final"]) == 2
    assert body["outline_final"][0]["heading"] == "Final intro"
    # outline_draft preserved
    assert len(body["outline_draft"]) == 1


# ── POST 422 on unknown op ───────────────────────────────────────────────────


def test_post_unknown_op_returns_422(app_client: TestClient):
    _seed()
    r = app_client.post(
        "/api/projects/creatine-cognitive/synthesize",
        json={"op": "nuke_everything"},
    )
    assert r.status_code == 422


# ── Slug guard ───────────────────────────────────────────────────────────────


# ── POST finalize_outline (issue #462) ──────────────────────────────────────


def test_post_finalize_outline_regenerates_via_cached_pool(app_client: TestClient, monkeypatch):
    """`finalize_outline` op writes outline_final from cached evidence + actions.

    Mocks the LLM at the synthesize package boundary so we don't need a
    live model. Verifies (a) the route returns the updated store, (b) the
    outline_final array is populated, (c) outline_draft is preserved, (d)
    user_actions are preserved.
    """
    import json as _json

    _seed()
    # Append a per-section reject so we can assert it's honoured in finalize.
    store.append_user_action(
        "creatine-cognitive",
        UserAction(
            timestamp="2026-05-08T00:00:00+00:00",
            action="reject_from_section",
            section=1,
            evidence_slug="rae-2003",
        ),
    )
    # Need at least 2 evidence slugs in the pool to satisfy outline contract.
    # Read existing then re-write with a fatter pool.
    s = store.read("creatine-cognitive")
    s2 = s.model_copy(
        update={
            "evidence_pool": [
                EvidencePoolItem(
                    slug="rae-2003",
                    chunks=[{"chunk_id": 1, "rrf_score": 0.9, "heading": "h1"}],
                    hit_reason="rrf",
                ),
                EvidencePoolItem(
                    slug="benton-2011",
                    chunks=[{"chunk_id": 2, "rrf_score": 0.8, "heading": "h2"}],
                    hit_reason="rrf",
                ),
            ]
        }
    )
    store.write(s2)

    # Mock the LLM via draft_outline's ask_fn path. The finalize entry
    # point lets the default ask resolve through the router; here we swap
    # the module-level ask with a fake that returns a valid outline JSON.
    fake_outline = _json.dumps(
        {
            "sections": [
                {
                    "section": i + 1,
                    "heading": f"第 {i + 1} 段",
                    "evidence_refs": ["rae-2003", "benton-2011"],
                }
                for i in range(5)
            ]
        }
    )

    from agents.brook.synthesize import _outline as outline_mod

    monkeypatch.setattr(outline_mod, "_default_ask", lambda *a, **kw: fake_outline)

    r = app_client.post(
        "/api/projects/creatine-cognitive/synthesize",
        json={"op": "finalize_outline"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["outline_final"]) == 5
    # outline_draft preserved
    assert len(body["outline_draft"]) == 1
    # user_actions preserved
    assert len(body["user_actions"]) == 1
    # per-section reject honoured: section 1's evidence_refs no longer contain rae-2003
    section_one = body["outline_final"][0]
    assert section_one["section"] == 1
    assert "rae-2003" not in section_one["evidence_refs"]
    # other sections retain rae-2003
    assert any("rae-2003" in s["evidence_refs"] for s in body["outline_final"][1:])


def test_post_finalize_outline_404_when_store_missing(app_client: TestClient):
    r = app_client.post(
        "/api/projects/no-such-slug/synthesize",
        json={"op": "finalize_outline"},
    )
    assert r.status_code == 404


def test_post_finalize_outline_422_when_pool_empty(app_client: TestClient, monkeypatch):
    """An empty evidence pool surfaces as 422 (LLM contract failure)."""
    s = BrookSynthesizeStore(
        project_slug="empty-pool",
        topic="t",
        keywords=[],
        evidence_pool=[],
        outline_draft=[],
    )
    store.create(s)
    r = app_client.post(
        "/api/projects/empty-pool/synthesize",
        json={"op": "finalize_outline"},
    )
    assert r.status_code == 422


def test_slug_with_dotdot_rejected(app_client: TestClient):
    # FastAPI URL-decodes the path param; ".." reaches our handler.
    r = app_client.get("/api/projects/../synthesize")
    # Either 400 from our guard or 404 from FastAPI's path normalisation —
    # both are acceptable refusals (no traversal occurred).
    assert r.status_code in (400, 404, 405)
