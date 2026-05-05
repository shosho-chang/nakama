"""Slice 3B — GET/PUT ``/api/books/{id}/progress`` API contract.

Behavior pinned:

- ``GET`` returns ``BookProgress`` JSON. When the book exists but has no row
  yet, return a sane empty-state set (200 with ``last_cfi: null`` etc.). 404
  when the book itself isn't in the ``books`` table.

- ``PUT`` upserts the row (last-write-wins; nakama is single-user, no
  per-user dimension). 200 on success, 404 when the book is missing.

- Concurrent PUTs against the same book MUST NOT crash; whichever wins is
  reflected in the next GET. SQLite's per-connection threading guard keeps
  us safe but the route handler should not introduce extra locks.

The ``app_client`` fixture mirrors the existing ``test_books_router.py``
shape — TestClient + isolated_db + NAKAMA_BOOKS_DIR.
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


def _upload(tc: TestClient, book_id: str = "alpha") -> None:
    files = {"bilingual": ("c.epub", epub_clean(), "application/epub+zip")}
    data = {"book_id": book_id, "title": "T", "lang_pair": "en-zh"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 303, f"upload failed: {r.status_code} {r.text}"


_TS = "2026-05-05T00:00:00Z"


def _payload(book_id: str = "alpha", **overrides) -> dict:
    base = {
        "book_id": book_id,
        "last_cfi": "epubcfi(/6/4!/4/2:0)",
        "last_chapter_ref": "ch01.xhtml",
        "last_spread_idx": 3,
        "percent": 0.42,
        "total_reading_seconds": 120,
        "updated_at": _TS,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


def test_get_progress_empty_state_for_unwritten_book(app_client):
    _upload(app_client, "alpha")
    r = app_client.get("/api/books/alpha/progress")
    assert r.status_code == 200
    body = r.json()
    assert body["book_id"] == "alpha"
    assert body["last_cfi"] is None
    assert body["last_chapter_ref"] is None
    assert body["last_spread_idx"] == 0
    assert body["percent"] == 0.0
    assert body["total_reading_seconds"] == 0


def test_get_progress_404_when_book_missing(app_client):
    r = app_client.get("/api/books/nonexistent/progress")
    assert r.status_code == 404


def test_get_progress_returns_persisted_row(app_client):
    _upload(app_client, "alpha")
    put = app_client.put("/api/books/alpha/progress", json=_payload("alpha"))
    assert put.status_code == 200, put.text

    got = app_client.get("/api/books/alpha/progress").json()
    assert got["book_id"] == "alpha"
    assert got["last_cfi"] == "epubcfi(/6/4!/4/2:0)"
    assert got["last_chapter_ref"] == "ch01.xhtml"
    assert got["last_spread_idx"] == 3
    assert got["percent"] == 0.42
    assert got["total_reading_seconds"] == 120


# ---------------------------------------------------------------------------
# PUT
# ---------------------------------------------------------------------------


def test_put_progress_upserts_first_time(app_client):
    _upload(app_client, "alpha")
    r = app_client.put("/api/books/alpha/progress", json=_payload("alpha"))
    assert r.status_code == 200


def test_put_progress_overwrites_existing(app_client):
    _upload(app_client, "alpha")
    app_client.put("/api/books/alpha/progress", json=_payload("alpha", last_spread_idx=1))
    app_client.put("/api/books/alpha/progress", json=_payload("alpha", last_spread_idx=42))
    got = app_client.get("/api/books/alpha/progress").json()
    assert got["last_spread_idx"] == 42


def test_put_progress_404_when_book_missing(app_client):
    r = app_client.put(
        "/api/books/nonexistent/progress",
        json=_payload("nonexistent"),
    )
    assert r.status_code == 404


def test_put_progress_rejects_book_id_mismatch(app_client):
    _upload(app_client, "alpha")
    r = app_client.put("/api/books/alpha/progress", json=_payload("beta"))
    assert r.status_code in (400, 422)


def test_put_progress_rejects_extra_field(app_client):
    """extra="forbid" on BookProgress — unknown payload key must reject."""
    _upload(app_client, "alpha")
    bad = _payload("alpha")
    bad["mystery"] = "field"
    r = app_client.put("/api/books/alpha/progress", json=bad)
    assert r.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Concurrency — last-write-wins, no crash
# ---------------------------------------------------------------------------


def test_put_progress_concurrent_no_crash(app_client):
    _upload(app_client, "race")

    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            r = app_client.put(
                "/api/books/race/progress",
                json=_payload("race", last_spread_idx=idx),
            )
            assert r.status_code == 200, r.text
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent PUT raised: {errors}"
    final = app_client.get("/api/books/race/progress").json()
    assert final["book_id"] == "race"
    assert 0 <= final["last_spread_idx"] < 8


# ---------------------------------------------------------------------------
# CSP header — progress API endpoints carry the same script-src 'self' header
# ---------------------------------------------------------------------------


def test_csp_header_present_on_progress_api(app_client):
    _upload(app_client, "csp-test")
    r = app_client.get("/api/books/csp-test/progress")
    csp = r.headers.get("content-security-policy", "")
    assert "script-src" in csp
    assert "'self'" in csp
