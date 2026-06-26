"""Auth-gating tests for /robin/api/books/* in production mode (issue #848).

All /api/books/* endpoints must return 403 for unauthenticated requests when
WEB_PASSWORD is set. Endpoints that were already gated are covered as regression.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.shared._epub_fixtures import epub_clean


@pytest.fixture
def books_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "books"
    d.mkdir()
    monkeypatch.setenv("NAKAMA_BOOKS_DIR", str(d))
    return d


@pytest.fixture
def vault_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "vault"
    d.mkdir()
    monkeypatch.setenv("VAULT_PATH", str(d))
    return d


@pytest.fixture
def prod_client(books_dir, vault_dir, monkeypatch):
    """Production mode: WEB_PASSWORD + WEB_SECRET set, no dev bypass."""
    monkeypatch.setenv("WEB_PASSWORD", "testpass")
    monkeypatch.setenv("WEB_SECRET", "testsecret")
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)
    monkeypatch.delenv("NAKAMA_DEV_AUTH_BYPASS", raising=False)

    import shared.annotation_store as ann_store
    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.books as books_module

    importlib.reload(ann_store)
    importlib.reload(auth_module)
    importlib.reload(books_module)
    importlib.reload(app_module)

    tc = TestClient(app_module.app, follow_redirects=False)
    valid_cookie = auth_module.make_token("testpass")
    return tc, valid_cookie


def _upload_authed(tc: TestClient, cookie: str, book_id: str = "alpha") -> None:
    files = {"bilingual": ("c.epub", epub_clean(), "application/epub+zip")}
    data = {"book_id": book_id, "title": "T", "lang_pair": "en-zh"}
    r = tc.post(
        "/robin/books/upload",
        data=data,
        files=files,
        cookies={"nakama_auth": cookie},
    )
    assert r.status_code == 303, f"upload failed: {r.status_code} {r.text}"


# ---------------------------------------------------------------------------
# 403 without auth — newly gated endpoints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/robin/api/books/alpha"),
        ("GET", "/robin/api/books/alpha/cover"),
        ("GET", "/robin/api/books/alpha/annotations"),
        ("POST", "/robin/api/books/alpha/annotations"),
        ("GET", "/robin/api/books/alpha/progress"),
        ("PUT", "/robin/api/books/alpha/progress"),
    ],
)
def test_unauthenticated_returns_403(prod_client, method, path):
    tc, _ = prod_client
    r = tc.request(method, path, json={})
    assert r.status_code == 403, f"{method} {path} → expected 403, got {r.status_code}"


# ---------------------------------------------------------------------------
# Regression: already-gated endpoints still require auth
# ---------------------------------------------------------------------------


def test_delete_book_still_requires_auth(prod_client):
    tc, _ = prod_client
    assert tc.delete("/robin/api/books/alpha").status_code == 403


def test_file_endpoint_still_requires_auth(prod_client):
    tc, _ = prod_client
    assert tc.get("/robin/api/books/alpha/file").status_code == 403


# ---------------------------------------------------------------------------
# Authenticated requests reach normal logic
# ---------------------------------------------------------------------------


def test_authenticated_get_metadata_returns_book(prod_client):
    tc, cookie = prod_client
    _upload_authed(tc, cookie)
    r = tc.get("/robin/api/books/alpha", cookies={"nakama_auth": cookie})
    assert r.status_code == 200
    assert r.json()["book_id"] == "alpha"


def test_authenticated_annotations_roundtrip(prod_client, monkeypatch):
    monkeypatch.setattr(
        "shared.literature_writer.write_literature_note",
        lambda slug, **kwargs: None,
    )
    tc, cookie = prod_client
    _upload_authed(tc, cookie)

    payload = {
        "schema_version": 2,
        "slug": "alpha",
        "book_id": "alpha",
        "book_version_hash": "a" * 64,
        "base": "books",
        "items": [],
        "updated_at": "2026-01-01T00:00:00Z",
        "last_synced_at": None,
    }
    r = tc.post(
        "/robin/api/books/alpha/annotations",
        json=payload,
        cookies={"nakama_auth": cookie},
    )
    assert r.status_code == 200

    r2 = tc.get("/robin/api/books/alpha/annotations", cookies={"nakama_auth": cookie})
    assert r2.status_code == 200
    assert r2.json()["book_id"] == "alpha"


def test_authenticated_progress_roundtrip(prod_client):
    from datetime import datetime, timezone

    tc, cookie = prod_client
    _upload_authed(tc, cookie)

    r = tc.get("/robin/api/books/alpha/progress", cookies={"nakama_auth": cookie})
    assert r.status_code == 200

    payload = {
        "book_id": "alpha",
        "last_cfi": None,
        "last_chapter_ref": None,
        "last_spread_idx": 0,
        "percent": 10.0,
        "total_reading_seconds": 60,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    r2 = tc.put(
        "/robin/api/books/alpha/progress",
        json=payload,
        cookies={"nakama_auth": cookie},
    )
    assert r2.status_code == 200
