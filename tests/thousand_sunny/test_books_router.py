"""Tests for thousand_sunny.routers.books — Slice 1D Reader Web layer."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.shared._epub_fixtures import epub_clean, epub_with_script_tag


@pytest.fixture
def books_dir(tmp_path: Path, monkeypatch) -> Path:
    """Route NAKAMA_BOOKS_DIR to tmp so each test owns its fs slice."""
    d = tmp_path / "books"
    d.mkdir()
    monkeypatch.setenv("NAKAMA_BOOKS_DIR", str(d))
    return d


@pytest.fixture
def app_client(books_dir, monkeypatch):
    """TestClient against the real app (CSP middleware + books router + static mount)."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.books as books_module

    importlib.reload(auth_module)
    importlib.reload(books_module)
    importlib.reload(app_module)

    return TestClient(app_module.app, follow_redirects=False), books_module


def _epub_upload(file_bytes: bytes, name: str = "book.epub"):
    return ("bilingual", (name, file_bytes, "application/epub+zip"))


# ---------------------------------------------------------------------------
# GET /books
# ---------------------------------------------------------------------------


def test_books_library_empty(app_client):
    tc, _ = app_client
    r = tc.get("/books")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "書架是空的" in r.text


def test_books_library_lists_inserted_book(app_client, books_dir):
    tc, _ = app_client
    blob = epub_clean()
    files = {"bilingual": ("c.epub", blob, "application/epub+zip")}
    data = {"book_id": "alpha", "title": "Alpha Book", "lang_pair": "en-zh"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 303

    r2 = tc.get("/books")
    assert r2.status_code == 200
    assert "Alpha Book" in r2.text
    assert "en-zh" in r2.text


# ---------------------------------------------------------------------------
# GET /books/upload
# ---------------------------------------------------------------------------


def test_books_upload_form_renders(app_client):
    tc, _ = app_client
    r = tc.get("/books/upload")
    assert r.status_code == 200
    assert "上傳新書" in r.text
    assert 'name="bilingual"' in r.text
    assert 'name="book_id"' in r.text


# ---------------------------------------------------------------------------
# POST /books/upload
# ---------------------------------------------------------------------------


def test_books_upload_happy_path(app_client, books_dir):
    tc, _ = app_client
    blob = epub_clean()
    files = {"bilingual": ("c.epub", blob, "application/epub+zip")}
    data = {"book_id": "happy-id", "title": "Happy Book", "lang_pair": "en-zh"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 303
    assert r.headers["location"] == "/books/happy-id"

    stored = books_dir / "happy-id" / "bilingual.epub"
    assert stored.exists()
    assert stored.stat().st_size > 0

    from shared.book_storage import get_book

    row = get_book("happy-id")
    assert row is not None
    assert row.title == "Happy Book"
    assert row.lang_pair == "en-zh"
    assert row.book_version_hash and len(row.book_version_hash) == 64
    assert row.has_original is False


def test_books_upload_rejects_path_traversal_id(app_client):
    tc, _ = app_client
    blob = epub_clean()
    files = {"bilingual": ("c.epub", blob, "application/epub+zip")}
    data = {"book_id": "../etc", "title": "T", "lang_pair": "en-zh"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 400


def test_books_upload_rejects_empty_bilingual(app_client):
    tc, _ = app_client
    files = {"bilingual": ("empty.epub", b"", "application/epub+zip")}
    data = {"book_id": "empty-x", "title": "T", "lang_pair": "en-zh"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 400


def test_books_upload_sanitizes_script_tags(app_client, books_dir):
    tc, _ = app_client
    import io
    import zipfile

    blob = epub_with_script_tag()
    # Fixture sanity: the deflated zip still has <script> inside ch1.xhtml.
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert any(b"<script" in zf.read(n) for n in zf.namelist() if n.endswith(".xhtml"))

    files = {"bilingual": ("dirty.epub", blob, "application/epub+zip")}
    data = {"book_id": "dirty-id", "title": "Dirty", "lang_pair": "en-zh"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 303

    stored = books_dir / "dirty-id" / "bilingual.epub"
    assert stored.exists()
    raw = stored.read_bytes()
    # Sanitizer rebuilds the zip; <script> should not survive in any chapter.
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            if name.endswith(".xhtml"):
                assert b"<script" not in zf.read(name)


def test_books_upload_with_original(app_client, books_dir):
    tc, _ = app_client
    bi = epub_clean()
    en = epub_clean()
    files = {
        "bilingual": ("c.epub", bi, "application/epub+zip"),
        "original": ("o.epub", en, "application/epub+zip"),
    }
    data = {"book_id": "with-orig", "title": "With Original", "lang_pair": "en-zh"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 303
    assert (books_dir / "with-orig" / "original.epub").exists()

    from shared.book_storage import get_book

    row = get_book("with-orig")
    assert row is not None
    assert row.has_original is True


# ---------------------------------------------------------------------------
# GET /books/{book_id}
# ---------------------------------------------------------------------------


def test_book_reader_404_when_missing(app_client):
    tc, _ = app_client
    r = tc.get("/books/no-such-book")
    assert r.status_code == 404


def test_book_reader_200_when_exists(app_client):
    tc, _ = app_client
    blob = epub_clean()
    files = {"bilingual": ("c.epub", blob, "application/epub+zip")}
    data = {"book_id": "render-me", "title": "Render", "lang_pair": "en-zh"}
    tc.post("/books/upload", data=data, files=files)

    r = tc.get("/books/render-me")
    assert r.status_code == 200
    assert "Render" in r.text
    assert "foliate-view" in r.text
    # Reader bootstrap JS lives in /static/ (not inline) so CSP `script-src
    # 'self'` lets it execute. The reader module then imports the foliate-js
    # ESM entry from /vendor/foliate-js/.
    assert "/static/book_reader.js" in r.text


# ---------------------------------------------------------------------------
# GET /api/books/{id}/file
# ---------------------------------------------------------------------------


def test_api_book_file_returns_bilingual_bytes(app_client):
    tc, _ = app_client
    blob = epub_clean()
    files = {"bilingual": ("c.epub", blob, "application/epub+zip")}
    data = {"book_id": "fetch-me", "title": "Fetch", "lang_pair": "en-zh"}
    tc.post("/books/upload", data=data, files=files)

    r = tc.get("/api/books/fetch-me/file?lang=bilingual")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/epub+zip"
    # Sanitized blob is rebuilt so byte-equal to original is not guaranteed,
    # but it must be a valid zip with content.opf inside.
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "META-INF/container.xml" in zf.namelist()


def test_api_book_file_en_404_when_no_original(app_client):
    tc, _ = app_client
    blob = epub_clean()
    files = {"bilingual": ("c.epub", blob, "application/epub+zip")}
    data = {"book_id": "no-orig", "title": "T", "lang_pair": "en-zh"}
    tc.post("/books/upload", data=data, files=files)

    r = tc.get("/api/books/no-orig/file?lang=en")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# CSP middleware
# ---------------------------------------------------------------------------


def test_csp_header_present_on_books_routes(app_client):
    tc, _ = app_client
    r = tc.get("/books")
    csp = r.headers.get("content-security-policy", "")
    assert csp, "CSP header missing on /books"
    assert "script-src 'self'" in csp


def test_csp_header_present_on_api_books_routes(app_client):
    tc, _ = app_client
    blob = epub_clean()
    files = {"bilingual": ("c.epub", blob, "application/epub+zip")}
    data = {"book_id": "csp-id", "title": "T", "lang_pair": "en-zh"}
    tc.post("/books/upload", data=data, files=files)

    r = tc.get("/api/books/csp-id/file?lang=bilingual")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    assert "script-src 'self'" in csp


def test_csp_header_absent_on_root(app_client):
    tc, _ = app_client
    r = tc.get("/")
    # / is the Robin inbox — must not get the strict reader CSP because it
    # legitimately uses inline <script> blocks.
    assert "content-security-policy" not in {k.lower() for k in r.headers}
