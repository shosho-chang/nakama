"""Slice 4B — POST ``/api/books/{id}/ingest-request`` API contract.

Behavior:

- POST a book that exists with ``has_original=True`` → 200, queue row created
  (status="queued").
- POST a book that exists with ``has_original=False`` → 400, no queue row.
- POST a missing book → 404.
- POST same book twice in a row → idempotent (only one queued row).
- The route MUST also gate behind the existing ``check_auth`` cookie like the
  rest of ``/api/books/*``.

The ``GET /api/books/{book_id}`` route extension (added in this slice for the
书架 badge) returns ``ingest_status`` from the queue: ``queued`` / ``ingesting``
/ ``ingested`` / ``failed`` / ``partial`` / ``"never"`` if no queue row.
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
def app_client(books_dir, monkeypatch):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.books as books_module

    importlib.reload(auth_module)
    importlib.reload(books_module)
    importlib.reload(app_module)

    return TestClient(app_module.app, follow_redirects=False)


def _upload(tc: TestClient, book_id: str, *, with_original: bool = True) -> None:
    files = {"bilingual": ("c.epub", epub_clean(), "application/epub+zip")}
    if with_original:
        files["original"] = ("o.epub", epub_clean(), "application/epub+zip")
    data = {"book_id": book_id, "title": "T", "lang_pair": "en-zh"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 303, f"upload failed: {r.status_code} {r.text}"


# ---------------------------------------------------------------------------
# POST happy paths
# ---------------------------------------------------------------------------


def test_post_ingest_request_with_original_creates_queue_row(app_client):
    _upload(app_client, "alpha", with_original=True)
    r = app_client.post("/api/books/alpha/ingest-request")
    assert r.status_code == 200, r.text

    from shared.book_queue import next_queued

    assert next_queued() == "alpha"


def test_post_ingest_request_idempotent_double_post(app_client):
    """Second POST against an already-queued book returns 200 but does NOT
    create a duplicate row (book_queue.enqueue is idempotent)."""
    _upload(app_client, "alpha", with_original=True)
    r1 = app_client.post("/api/books/alpha/ingest-request")
    r2 = app_client.post("/api/books/alpha/ingest-request")
    assert r1.status_code == 200
    assert r2.status_code == 200

    # Verify only ONE queued row exists
    from shared.state import _get_conn

    rows = (
        _get_conn()
        .execute("SELECT * FROM book_ingest_queue WHERE book_id=?", ("alpha",))
        .fetchall()
    )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# POST failure paths
# ---------------------------------------------------------------------------


def test_post_ingest_request_book_without_original_returns_400(app_client):
    _upload(app_client, "alpha", with_original=False)
    r = app_client.post("/api/books/alpha/ingest-request")
    assert r.status_code == 400


def test_post_ingest_request_book_missing_returns_404(app_client):
    r = app_client.post("/api/books/nonexistent/ingest-request")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/books/{id} — extended with ingest_status field
# ---------------------------------------------------------------------------


def test_get_book_includes_ingest_status_never(app_client):
    _upload(app_client, "alpha", with_original=True)
    body = app_client.get("/api/books/alpha").json()
    assert body["ingest_status"] == "never"


def test_get_book_includes_ingest_status_queued(app_client):
    _upload(app_client, "alpha", with_original=True)
    app_client.post("/api/books/alpha/ingest-request")
    body = app_client.get("/api/books/alpha").json()
    assert body["ingest_status"] == "queued"


def test_get_book_includes_ingest_status_ingested(app_client):
    """Once the skill marks status=ingested, GET reflects it."""
    _upload(app_client, "alpha", with_original=True)
    app_client.post("/api/books/alpha/ingest-request")

    from shared.book_queue import mark_status

    mark_status("alpha", "ingesting")
    mark_status("alpha", "ingested", chapters_done=11)
    body = app_client.get("/api/books/alpha").json()
    assert body["ingest_status"] == "ingested"


# ---------------------------------------------------------------------------
# CSP header — ingest API endpoints carry script-src 'self'
# ---------------------------------------------------------------------------


def test_csp_header_present_on_ingest_api(app_client):
    _upload(app_client, "alpha", with_original=True)
    r = app_client.post("/api/books/alpha/ingest-request")
    csp = r.headers.get("content-security-policy", "")
    assert "script-src" in csp
    assert "'self'" in csp


# ---------------------------------------------------------------------------
# DELETE /api/books/{id}/ingest-request — cancel queued ingest
# ---------------------------------------------------------------------------


def test_delete_ingest_request_cancels_queued(app_client):
    _upload(app_client, "alpha", with_original=True)
    app_client.post("/api/books/alpha/ingest-request")

    r = app_client.delete("/api/books/alpha/ingest-request")
    assert r.status_code == 200
    assert app_client.get("/api/books/alpha").json()["ingest_status"] == "never"


def test_delete_ingest_request_returns_409_when_not_queued(app_client):
    _upload(app_client, "alpha", with_original=True)
    r = app_client.delete("/api/books/alpha/ingest-request")
    assert r.status_code == 409


def test_delete_ingest_request_refuses_when_ingesting(app_client):
    _upload(app_client, "alpha", with_original=True)
    app_client.post("/api/books/alpha/ingest-request")
    from shared.book_queue import mark_status

    mark_status("alpha", "ingesting")
    r = app_client.delete("/api/books/alpha/ingest-request")
    assert r.status_code == 409
    assert app_client.get("/api/books/alpha").json()["ingest_status"] == "ingesting"


# ---------------------------------------------------------------------------
# DELETE /api/books/{id} — full book removal
# ---------------------------------------------------------------------------


def test_delete_book_removes_row_files_and_queue(app_client, books_dir):
    _upload(app_client, "alpha", with_original=True)
    app_client.post("/api/books/alpha/ingest-request")

    r = app_client.delete("/api/books/alpha")
    assert r.status_code == 200
    assert app_client.get("/api/books/alpha").status_code == 404
    assert not (books_dir / "alpha").exists()


def test_delete_book_404_when_missing(app_client):
    r = app_client.delete("/api/books/nonexistent")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/books/{id}/cover — bytes from EPUB extracted at upload time
# ---------------------------------------------------------------------------


def test_get_cover_404_when_no_cover_in_epub(app_client):
    _upload(app_client, "alpha", with_original=True)
    r = app_client.get("/api/books/alpha/cover")
    assert r.status_code == 404


def test_get_cover_returns_image_bytes_from_epub_with_cover(app_client):
    from tests.shared._epub_fixtures import epub_with_cover

    files = {"bilingual": ("c.epub", epub_with_cover(), "application/epub+zip")}
    data = {"book_id": "alpha", "title": "T", "lang_pair": "en-zh"}
    r = app_client.post("/books/upload", data=data, files=files)
    assert r.status_code == 303

    r = app_client.get("/api/books/alpha/cover")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")
    assert len(r.content) > 0
