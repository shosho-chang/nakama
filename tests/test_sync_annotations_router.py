"""Integration tests for POST /sync-annotations/{slug} endpoint.

Covers:
- 403 when unauthenticated
- Empty AnnotationSet → 200 SyncReport with 0 annotations_merged
- AnnotationStore returns None → 200 SyncReport with error
- Happy path: ConceptPageAnnotationMerger mocked → 200 SyncReport returned

ConceptPageAnnotationMerger and AnnotationStore are mocked to isolate
the HTTP layer from the merger logic (tested separately in test_annotation_merger.py).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    import shared.config as cfg

    cfg._config = None
    return tmp_path


@pytest.fixture
def client(vault, monkeypatch):
    """TestClient with dev-mode auth (no WEB_PASSWORD set)."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)

    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(robin_module)

    app = FastAPI()
    app.include_router(robin_module.router)

    from fastapi.responses import PlainTextResponse

    @app.get("/login")
    def login(next: str = ""):
        return PlainTextResponse(f"login next={next}")

    return TestClient(app, follow_redirects=False), robin_module


@pytest.fixture
def auth_client(vault, monkeypatch):
    """TestClient with WEB_PASSWORD set — requires valid cookie."""
    monkeypatch.setenv("WEB_PASSWORD", "testpw")
    monkeypatch.setenv("WEB_SECRET", "testsecret")

    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(robin_module)

    app = FastAPI()
    app.include_router(robin_module.router)

    tc = TestClient(app, follow_redirects=False)
    from thousand_sunny.auth import make_token

    cookies = {"nakama_auth": make_token("testpw")}
    return tc, robin_module, cookies


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_sync_annotations_requires_auth(auth_client):
    tc, robin_module, _ = auth_client
    res = tc.post("/sync-annotations/some-slug")
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Router behaviour (merger mocked)
# ---------------------------------------------------------------------------


def test_sync_annotations_store_not_found(client, monkeypatch):
    """Store returns None → 200 with error in SyncReport."""
    tc, robin_module = client

    import agents.robin.annotation_merger as merger_mod
    from agents.robin.annotation_merger import SyncReport

    fake_report = SyncReport(
        source_slug="ghost-slug",
        concepts_updated=[],
        annotations_merged=0,
        skipped_annotations=0,
        errors=["AnnotationStore: no entry for slug 'ghost-slug'"],
    )
    monkeypatch.setattr(
        merger_mod.ConceptPageAnnotationMerger,
        "sync_source_to_concepts",
        lambda self, slug: fake_report,
    )

    res = tc.post("/sync-annotations/ghost-slug")
    assert res.status_code == 200
    data = res.json()
    assert data["annotations_merged"] == 0
    assert len(data["errors"]) > 0
    assert "unsynced_count" in data


def test_sync_annotations_empty_returns_zero_count(client, monkeypatch):
    """No annotations → 200 with 0 annotations_merged."""
    tc, robin_module = client

    import agents.robin.annotation_merger as merger_mod
    from agents.robin.annotation_merger import SyncReport

    fake_report = SyncReport(
        source_slug="empty-src",
        concepts_updated=[],
        annotations_merged=0,
        skipped_annotations=0,
        errors=[],
    )
    monkeypatch.setattr(
        merger_mod.ConceptPageAnnotationMerger,
        "sync_source_to_concepts",
        lambda self, slug: fake_report,
    )

    res = tc.post("/sync-annotations/empty-src")
    assert res.status_code == 200
    data = res.json()
    assert data["annotations_merged"] == 0
    assert data["source_slug"] == "empty-src"
    assert "unsynced_count" in data


def test_sync_annotations_success(client, monkeypatch):
    """Happy path: merger reports concepts updated."""
    tc, robin_module = client

    import agents.robin.annotation_merger as merger_mod
    from agents.robin.annotation_merger import SyncReport

    fake_report = SyncReport(
        source_slug="book-ch3",
        concepts_updated=["肌酸代謝", "睡眠品質"],
        annotations_merged=3,
        skipped_annotations=0,
        errors=[],
    )
    monkeypatch.setattr(
        merger_mod.ConceptPageAnnotationMerger,
        "sync_source_to_concepts",
        lambda self, slug: fake_report,
    )

    res = tc.post("/sync-annotations/book-ch3")
    assert res.status_code == 200
    data = res.json()
    assert data["annotations_merged"] == 3
    assert "肌酸代謝" in data["concepts_updated"]
    assert data["errors"] == []
    assert data["unsynced_count"] == 0  # no annotation file → unsynced_count=0
