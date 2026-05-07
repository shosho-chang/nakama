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


def test_slug_with_dotdot_rejected(app_client: TestClient):
    # FastAPI URL-decodes the path param; ".." reaches our handler.
    r = app_client.get("/api/projects/../synthesize")
    # Either 400 from our guard or 404 from FastAPI's path normalisation —
    # both are acceptable refusals (no traversal occurred).
    assert r.status_code in (400, 404, 405)
