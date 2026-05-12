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
    # bilingual-en-zh mode renders as the human-readable 「EN→中」 badge
    # (template fix in #569); the raw ``en-zh`` lang_pair string is only
    # surfaced in the DB row, not the rendered HTML.
    assert "EN→中" in r2.text


# ---------------------------------------------------------------------------
# GET /books/upload
# ---------------------------------------------------------------------------


def test_books_upload_form_renders(app_client):
    tc, _ = app_client
    r = tc.get("/books/upload")
    assert r.status_code == 200
    assert "上傳新書" in r.text
    assert 'name="bilingual"' in r.text
    assert 'name="original"' in r.text
    # Form fields the new UI no longer surfaces — verify they are gone so the
    # simplified contract stays simple.
    assert 'name="book_id"' not in r.text
    assert 'name="title"' not in r.text
    assert 'name="lang_pair"' not in r.text
    # Drag-and-drop bootstrap is served from origin (CSP-safe).
    assert "/static/book_upload.js" in r.text


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


def test_books_upload_derives_book_id_from_epub_title_when_form_omits_it(app_client, books_dir):
    """Simplified UI sends only files; backend slugifies the EPUB title for book_id.

    EPUBSpec default title is "The Tracer" — slugify keeps the space → hyphen and
    preserves case, giving "The-Tracer".
    """
    tc, _ = app_client
    files = {"bilingual": ("c.epub", epub_clean(), "application/epub+zip")}

    r = tc.post("/books/upload", files=files)
    assert r.status_code == 303
    assert r.headers["location"] == "/books/The-Tracer"

    from shared.book_storage import get_book

    row = get_book("The-Tracer")
    assert row is not None
    assert row.title == "The Tracer"  # original title preserved on the row
    assert row.author == "Anon"
    assert row.lang_pair == "en-zh"
    assert (books_dir / "The-Tracer" / "bilingual.epub").exists()


def test_books_upload_falls_back_to_hash_id_when_title_missing(app_client, books_dir):
    """If the EPUB has no title at all, derive an id from book_version_hash."""
    from tests.shared._epub_fixtures import epub_minimal_metadata

    tc, _ = app_client
    files = {"bilingual": ("c.epub", epub_minimal_metadata(), "application/epub+zip")}

    r = tc.post("/books/upload", files=files)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/books/")
    book_id = location[len("/books/") :]
    # "Untitled" slugifies to "Untitled"; only when slugify returns empty (e.g. title
    # is whitespace or all-stripped chars) does the hash-prefixed id kick in. Both
    # shapes are valid, so accept either.
    assert book_id == "Untitled" or (book_id.startswith("book-") and len(book_id) == 17)


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
# Mode-aware upload — Phase 1 monolingual-zh pilot.
# ---------------------------------------------------------------------------


def test_upload_zh_epub_auto_detects_monolingual_zh(app_client, books_dir):
    """The simplified UI sends ``mode=auto`` (or omits the param, which
    defaults to ``auto``). Detection must read EPUB metadata.lang=zh-TW
    and store the book as monolingual-zh."""
    from tests.shared._epub_fixtures import epub_monolingual_zh

    tc, _ = app_client
    blob = epub_monolingual_zh()
    files = {"bilingual": ("zh.epub", blob, "application/epub+zip")}
    data = {"book_id": "zh-pilot-auto"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 303

    from shared.book_storage import get_book

    row = get_book("zh-pilot-auto")
    assert row is not None
    assert row.mode == "monolingual-zh"
    assert row.lang_pair == "zh-zh"
    assert row.has_original is False


def test_upload_zh_epub_without_metadata_lang_falls_back_to_body_sample(app_client, books_dir):
    """When EPUB metadata.lang is absent, the route extracts a body sample
    and falls back to ``shared.lang_detect``. zh body → monolingual-zh."""
    from tests.shared._epub_fixtures import epub_monolingual_zh

    tc, _ = app_client
    blob = epub_monolingual_zh(declare_lang=False)
    files = {"bilingual": ("zh-no-lang.epub", blob, "application/epub+zip")}
    data = {"book_id": "zh-body-fallback"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 303

    from shared.book_storage import get_book

    row = get_book("zh-body-fallback")
    assert row is not None
    assert row.mode == "monolingual-zh"


def test_upload_en_epub_resolves_to_bilingual_en_zh(app_client, books_dir):
    """Existing English-only upload path must keep its previous behaviour —
    ``mode=auto`` sees ``language=en`` and resolves to bilingual-en-zh."""
    tc, _ = app_client
    blob = epub_clean()  # EPUBSpec default language="en"
    files = {"bilingual": ("en.epub", blob, "application/epub+zip")}
    data = {"book_id": "en-default"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 303

    from shared.book_storage import get_book

    row = get_book("en-default")
    assert row is not None
    assert row.mode == "bilingual-en-zh"


def test_upload_explicit_mode_overrides_detection(app_client, books_dir):
    """Caller can pin ``mode=monolingual-zh`` even on an English EPUB —
    operator override path. Useful when metadata.lang is misleading."""
    tc, _ = app_client
    blob = epub_clean()  # English
    files = {"bilingual": ("forced.epub", blob, "application/epub+zip")}
    data = {"book_id": "forced-zh", "mode": "monolingual-zh"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 303

    from shared.book_storage import get_book

    row = get_book("forced-zh")
    assert row is not None
    assert row.mode == "monolingual-zh"


def test_upload_invalid_mode_value_returns_400(app_client):
    tc, _ = app_client
    blob = epub_clean()
    files = {"bilingual": ("c.epub", blob, "application/epub+zip")}
    data = {"book_id": "bad-mode", "mode": "klingon"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 400
    assert "invalid mode" in r.text


def test_upload_with_only_original_field_succeeds(app_client, books_dir):
    """Per PRD §4.5 S2: bilingual is optional. If only ``original`` is
    supplied, route promotes it into the bilingual slot so the Reader has
    a display copy, treats book as bilingual-only (has_original=False)."""
    tc, _ = app_client
    blob = epub_clean()
    files = {"original": ("only-orig.epub", blob, "application/epub+zip")}
    data = {"book_id": "orig-only"}
    r = tc.post("/books/upload", data=data, files=files)
    assert r.status_code == 303

    # bilingual.epub slot should still exist (promoted from the original
    # bytes), so the Reader can render the book.
    assert (books_dir / "orig-only" / "bilingual.epub").exists()

    from shared.book_storage import get_book

    row = get_book("orig-only")
    assert row is not None
    # No paired original archived → has_original=False
    assert row.has_original is False


def test_upload_with_neither_field_returns_400(app_client):
    tc, _ = app_client
    r = tc.post("/books/upload", data={"book_id": "ghost"})
    assert r.status_code == 400


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


# ---------------------------------------------------------------------------
# GET /api/books/{book_id} — book metadata (book_version_hash for Reader JS)
# ---------------------------------------------------------------------------


def test_api_book_metadata_returns_book_row(app_client):
    tc, _ = app_client
    blob = epub_clean()
    files = {"bilingual": ("c.epub", blob, "application/epub+zip")}
    data = {"book_id": "meta-id", "title": "Meta", "lang_pair": "en-zh"}
    tc.post("/books/upload", data=data, files=files)

    r = tc.get("/api/books/meta-id")
    assert r.status_code == 200
    body = r.json()
    assert body["book_id"] == "meta-id"
    assert body["title"] == "Meta"
    assert body["lang_pair"] == "en-zh"
    assert body["has_original"] is False
    assert isinstance(body["book_version_hash"], str)
    assert len(body["book_version_hash"]) == 64
    assert "created_at" in body


def test_api_book_metadata_404(app_client):
    tc, _ = app_client
    r = tc.get("/api/books/no-such-book")
    assert r.status_code == 404
