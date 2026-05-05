"""Behavior tests for the Slice 2C annotation API on the books router.

Two endpoints:

- ``GET /api/books/{book_id}/annotations`` returns the current
  ``AnnotationSetV2`` JSON, or 404 when the book is not registered. An
  unwritten book returns an empty set (200 with ``items: []``), not 404 — only
  the book row's existence gates the route.

- ``POST /api/books/{book_id}/annotations`` does a full-replace write of an
  ``AnnotationSetV2`` JSON body. Per-slug ``threading.Lock`` in
  ``AnnotationStore`` prevents lost updates under concurrent POST.

These tests share the ``app_client`` fixture from ``test_books_router.py`` so
they exercise the real CSP middleware + auth gating + DB + filesystem stack.
"""

from __future__ import annotations

import importlib
import threading
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
    """KB/Annotations/ lives under VAULT_PATH — isolate per test."""
    d = tmp_path / "vault"
    d.mkdir()
    monkeypatch.setenv("VAULT_PATH", str(d))
    return d


@pytest.fixture
def app_client(books_dir, vault_dir, monkeypatch):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)

    import shared.annotation_store as ann_store
    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.books as books_module

    importlib.reload(ann_store)
    importlib.reload(auth_module)
    importlib.reload(books_module)
    importlib.reload(app_module)

    return TestClient(app_module.app, follow_redirects=False)


def _upload(tc: TestClient, book_id: str = "test-book") -> None:
    """Register a book by uploading a clean fixture EPUB."""
    files = {"bilingual": ("c.epub", epub_clean(), "application/epub+zip")}
    data = {"book_id": book_id, "title": "T", "lang_pair": "en-zh"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 303, f"upload failed: {r.status_code} {r.text}"


_TS = "2026-05-05T00:00:00Z"
_HASH = "a" * 64


def _v2_payload(book_id: str, items: list[dict] | None = None) -> dict:
    return {
        "schema_version": 2,
        "slug": book_id,
        "book_id": book_id,
        "book_version_hash": _HASH,
        "base": "books",
        "items": items or [],
        "updated_at": _TS,
        "last_synced_at": None,
    }


# ---------------------------------------------------------------------------
# GET /api/books/{book_id}/annotations
# ---------------------------------------------------------------------------


def test_get_annotations_empty_set_for_unwritten_book(app_client):
    _upload(app_client, "empty-book")
    r = app_client.get("/api/books/empty-book/annotations")
    assert r.status_code == 200
    body = r.json()
    assert body["schema_version"] == 2
    assert body["book_id"] == "empty-book"
    assert body["items"] == []


def test_get_annotations_404_when_book_missing(app_client):
    r = app_client.get("/api/books/nonexistent/annotations")
    assert r.status_code == 404


def test_get_annotations_returns_persisted_items(app_client):
    _upload(app_client, "alpha")
    payload = _v2_payload(
        "alpha",
        items=[
            {
                "type": "highlight",
                "cfi": "epubcfi(/6/4!/4/2:0)",
                "text_excerpt": "first line",
                "book_version_hash": _HASH,
                "created_at": _TS,
                "modified_at": _TS,
            }
        ],
    )
    post = app_client.post("/api/books/alpha/annotations", json=payload)
    assert post.status_code == 200, post.text

    got = app_client.get("/api/books/alpha/annotations")
    assert got.status_code == 200
    body = got.json()
    assert body["book_id"] == "alpha"
    assert len(body["items"]) == 1
    assert body["items"][0]["type"] == "highlight"
    assert body["items"][0]["cfi"].startswith("epubcfi(")


# ---------------------------------------------------------------------------
# POST /api/books/{book_id}/annotations
# ---------------------------------------------------------------------------


def test_post_annotations_full_replace_overwrites(app_client):
    _upload(app_client, "alpha")
    first = _v2_payload(
        "alpha",
        items=[
            {
                "type": "highlight",
                "cfi": "epubcfi(/6/4!/4/2:0)",
                "text_excerpt": "first",
                "book_version_hash": _HASH,
                "created_at": _TS,
                "modified_at": _TS,
            }
        ],
    )
    app_client.post("/api/books/alpha/annotations", json=first)

    second = _v2_payload(
        "alpha",
        items=[
            {
                "type": "annotation",
                "cfi": "epubcfi(/6/4!/4/2:5)",
                "text_excerpt": "second",
                "note": "replacement",
                "book_version_hash": _HASH,
                "created_at": _TS,
                "modified_at": _TS,
            },
            {
                "type": "comment",
                "chapter_ref": "ch01.xhtml",
                "cfi_anchor": None,
                "body": "long reflection",
                "book_version_hash": _HASH,
                "created_at": _TS,
                "modified_at": _TS,
            },
        ],
    )
    r = app_client.post("/api/books/alpha/annotations", json=second)
    assert r.status_code == 200

    got = app_client.get("/api/books/alpha/annotations").json()
    assert len(got["items"]) == 2
    assert {it["type"] for it in got["items"]} == {"annotation", "comment"}


def test_post_annotations_404_when_book_missing(app_client):
    r = app_client.post(
        "/api/books/nonexistent/annotations",
        json=_v2_payload("nonexistent"),
    )
    assert r.status_code == 404


def test_post_annotations_rejects_v1_payload(app_client):
    """v1 paper-shape payload must be rejected — book endpoint only accepts v2."""
    _upload(app_client, "alpha")
    bad_payload = {
        "schema_version": 1,
        "slug": "alpha",
        "source_filename": "alpha.md",
        "base": "inbox",
        "items": [{"type": "highlight", "text": "x", "created_at": _TS, "modified_at": _TS}],
        "updated_at": _TS,
    }
    r = app_client.post("/api/books/alpha/annotations", json=bad_payload)
    assert r.status_code in (400, 422)


def test_post_annotations_rejects_book_id_mismatch(app_client):
    """URL book_id must match payload book_id; otherwise reject."""
    _upload(app_client, "alpha")
    payload = _v2_payload("beta")  # mismatch
    r = app_client.post("/api/books/alpha/annotations", json=payload)
    assert r.status_code in (400, 422)


def test_post_annotations_concurrent_no_lost_update(app_client):
    """N parallel POSTs against the same book must all complete without crash;
    final read must surface a valid AnnotationSetV2 (last-write-wins is fine)."""
    _upload(app_client, "race")

    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            payload = _v2_payload(
                "race",
                items=[
                    {
                        "type": "highlight",
                        "cfi": f"epubcfi(/6/{idx}!/4/2:0)",
                        "text_excerpt": f"text-{idx}",
                        "book_version_hash": _HASH,
                        "created_at": _TS,
                        "modified_at": _TS,
                    }
                ],
            )
            r = app_client.post("/api/books/race/annotations", json=payload)
            assert r.status_code == 200
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent POST raised: {errors}"
    final = app_client.get("/api/books/race/annotations").json()
    assert final["book_id"] == "race"
    assert len(final["items"]) == 1


# ---------------------------------------------------------------------------
# CSP — annotation API endpoints carry the same script-src 'self' header
# ---------------------------------------------------------------------------


def test_csp_header_present_on_annotations_api(app_client):
    _upload(app_client, "csp-test")
    r = app_client.get("/api/books/csp-test/annotations")
    csp = r.headers.get("content-security-policy", "")
    assert "script-src" in csp
    assert "'self'" in csp


# ---------------------------------------------------------------------------
# Background digest trigger (issue #432)
# ---------------------------------------------------------------------------


def test_post_annotations_dispatches_digest_background_task(app_client, monkeypatch):
    """POST annotations must immediately return 200 with digest_status='queued'
    and dispatch write_digest as a background task exactly once."""
    calls: list[str] = []

    def fake_write_digest(book_id: str):
        calls.append(book_id)

    import agents.robin.book_digest_writer as bdw

    monkeypatch.setattr(bdw, "write_digest", fake_write_digest)

    _upload(app_client, "digest-book")
    payload = _v2_payload("digest-book")
    r = app_client.post("/api/books/digest-book/annotations", json=payload)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("digest_status") == "queued"
    # TestClient runs background tasks synchronously; write_digest must have been called
    assert calls == ["digest-book"], f"expected write_digest called once, got: {calls}"


def test_post_annotations_digest_status_queued_in_response(app_client, monkeypatch):
    """Response must include digest_status='queued' regardless of digest outcome."""
    monkeypatch.setattr(
        "agents.robin.book_digest_writer.write_digest",
        lambda book_id: None,
    )
    _upload(app_client, "status-check")
    r = app_client.post("/api/books/status-check/annotations", json=_v2_payload("status-check"))
    assert r.status_code == 200
    assert r.json()["digest_status"] == "queued"
