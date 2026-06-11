"""Behavior tests for the Slice 2C annotation API on the books router.

Two endpoints:

- ``GET /robin/api/books/{book_id}/annotations`` returns the stored annotation
  set (v3 on disk; a fabricated empty v3 set for an unwritten book), or 404 when
  the book is not registered. An unwritten book returns an empty set (200 with
  ``items: []``), not 404 — only the book row's existence gates the route.

- ``POST /robin/api/books/{book_id}/annotations`` does a full-replace write of an
  ``AnnotationSetV2`` or ``AnnotationSetV3`` JSON body (normalised to v3 on
  disk). Per-slug ``threading.Lock`` in ``AnnotationStore`` prevents lost
  updates under concurrent POST.

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
    r = tc.post("/robin/books/upload", data=data, files=files)
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
# GET /robin/api/books/{book_id}/annotations
# ---------------------------------------------------------------------------


def test_get_annotations_empty_set_for_unwritten_book(app_client):
    _upload(app_client, "empty-book")
    r = app_client.get("/robin/api/books/empty-book/annotations")
    assert r.status_code == 200
    body = r.json()
    # Must be v3: the reader mutates this set in place and POSTs it back, and its
    # action handlers build v3-shaped items. A fabricated v2 set routed the POST
    # to AnnotationSetV2 validation, which 422'd every first save on a fresh book.
    assert body["schema_version"] == 3
    assert body["book_id"] == "empty-book"
    assert body["items"] == []
    # ADR-044 §B8: conflicts key is always present, empty in the common case.
    assert body["conflicts"] == []


def test_get_annotations_surfaces_sync_conflicts(app_client, vault_dir: Path):
    _upload(app_client, "alpha")
    ann_dir = vault_dir / "KB" / "Annotations"
    ann_dir.mkdir(parents=True, exist_ok=True)
    (ann_dir / "alpha.sync-conflict-20260525-143000-ABC123.md").write_text("{}", encoding="utf-8")
    body = app_client.get("/robin/api/books/alpha/annotations").json()
    assert len(body["conflicts"]) == 1
    conflict = body["conflicts"][0]
    assert conflict["slug"] == "alpha"
    assert conflict["device"] == "ABC123"
    assert conflict["conflict_timestamp"] == "20260525-143000"


def test_get_annotations_404_when_book_missing(app_client):
    r = app_client.get("/robin/api/books/nonexistent/annotations")
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
    post = app_client.post("/robin/api/books/alpha/annotations", json=payload)
    assert post.status_code == 200, post.text

    got = app_client.get("/robin/api/books/alpha/annotations")
    assert got.status_code == 200
    body = got.json()
    assert body["book_id"] == "alpha"
    assert len(body["items"]) == 1
    assert body["items"][0]["type"] == "highlight"
    assert body["items"][0]["cfi"].startswith("epubcfi(")


# ---------------------------------------------------------------------------
# POST /robin/api/books/{book_id}/annotations
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
    app_client.post("/robin/api/books/alpha/annotations", json=first)

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
    r = app_client.post("/robin/api/books/alpha/annotations", json=second)
    assert r.status_code == 200

    got = app_client.get("/robin/api/books/alpha/annotations").json()
    assert len(got["items"]) == 2
    # ADR-021 §1: book POST upgrades v2 ``comment`` → v3 ``reflection`` on save;
    # GET round-trip surfaces the v3 type alongside the unchanged ``annotation``.
    assert {it["type"] for it in got["items"]} == {"annotation", "reflection"}


def test_post_annotations_404_when_book_missing(app_client):
    r = app_client.post(
        "/robin/api/books/nonexistent/annotations",
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
    r = app_client.post("/robin/api/books/alpha/annotations", json=bad_payload)
    assert r.status_code in (400, 422)


def test_post_annotations_rejects_book_id_mismatch(app_client):
    """URL book_id must match payload book_id; otherwise reject."""
    _upload(app_client, "alpha")
    payload = _v2_payload("beta")  # mismatch
    r = app_client.post("/robin/api/books/alpha/annotations", json=payload)
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
            r = app_client.post("/robin/api/books/race/annotations", json=payload)
            assert r.status_code == 200
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent POST raised: {errors}"
    final = app_client.get("/robin/api/books/race/annotations").json()
    assert final["book_id"] == "race"
    assert len(final["items"]) == 1


# ---------------------------------------------------------------------------
# CSP — annotation API endpoints carry the same script-src 'self' header
# ---------------------------------------------------------------------------


def test_csp_header_present_on_annotations_api(app_client):
    _upload(app_client, "csp-test")
    r = app_client.get("/robin/api/books/csp-test/annotations")
    csp = r.headers.get("content-security-policy", "")
    assert "script-src" in csp
    assert "'self'" in csp


# ---------------------------------------------------------------------------
# Background digest trigger (issue #432)
# ---------------------------------------------------------------------------


def test_post_annotations_dispatches_literature_background_task(app_client, monkeypatch):
    """POST annotations must immediately return 200 with digest_status='queued'
    and dispatch the Literature Note render (N521) as a background task exactly
    once, keyed by the annotation-set slug."""
    calls: list[str] = []

    def fake_render(slug: str, **kwargs):
        calls.append(slug)

    import shared.literature_writer as lw

    monkeypatch.setattr(lw, "write_literature_note", fake_render)

    _upload(app_client, "digest-book")
    payload = _v2_payload("digest-book")
    r = app_client.post("/robin/api/books/digest-book/annotations", json=payload)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("digest_status") == "queued"
    # TestClient runs background tasks synchronously; the render must have fired once.
    assert calls == ["digest-book"], f"expected literature render once, got: {calls}"


def test_post_annotations_digest_status_queued_in_response(app_client, monkeypatch):
    """Response must include digest_status='queued' regardless of render outcome."""
    monkeypatch.setattr(
        "shared.literature_writer.write_literature_note",
        lambda slug, **kwargs: None,
    )
    _upload(app_client, "status-check")
    r = app_client.post(
        "/robin/api/books/status-check/annotations", json=_v2_payload("status-check")
    )
    assert r.status_code == 200
    assert r.json()["digest_status"] == "queued"


# ---------------------------------------------------------------------------
# Regression: fresh-book first-highlight save must succeed
#
# Bug scenario before fix: book_reader.js emptyAnnotationSet() emitted
# schema_version=2, but actionHighlight built items with the v3 shape (with a
# ``text`` field). HighlightV2 has extra="forbid" → first highlight on a
# freshly imported book always 422'd. Books with prior annotation files were
# unaffected because the GET round-trip returned a v3 set, so currentSet was
# v3 by the time the user clicked highlight.
#
# Fix: emptyAnnotationSet() now emits schema_version=3, matching the v3-shaped
# items the handlers build. These tests pin the payload shape the client
# actually sends.
# ---------------------------------------------------------------------------


def _v3_empty_set(book_id: str) -> dict:
    """Mirror book_reader.js emptyAnnotationSet() after the fix."""
    return {
        "schema_version": 3,
        "slug": book_id,
        "book_id": book_id,
        "book_version_hash": _HASH,
        "items": [],
        "updated_at": _TS,
        "last_synced_at": None,
    }


def _v3_highlight_item(text: str = "selected passage") -> dict:
    """Mirror book_reader.js actionHighlight() item shape — note ``text`` field."""
    return {
        "type": "highlight",
        "cfi": "epubcfi(/6/4!/4/2:0)",
        "text_excerpt": text,
        "book_version_hash": _HASH,
        "text": text,
        "created_at": _TS,
        "modified_at": _TS,
    }


def test_post_v3_empty_set_succeeds(app_client):
    """A fresh book's first save (still no items) must succeed under v3."""
    _upload(app_client, "fresh-book")
    r = app_client.post(
        "/robin/api/books/fresh-book/annotations",
        json=_v3_empty_set("fresh-book"),
    )
    assert r.status_code == 200, r.text


def test_post_v3_first_highlight_on_fresh_book_succeeds(app_client):
    """The exact payload book_reader.js sends for the first highlight on a
    book that had no annotations file: v3 empty set with one v3-shaped highlight
    item appended. Pre-fix this 422'd because the client emitted v2 schema_version
    with a v3-shaped item containing the forbidden ``text`` field."""
    _upload(app_client, "fresh-book")
    payload = _v3_empty_set("fresh-book")
    payload["items"] = [_v3_highlight_item("first highlight on fresh book")]
    r = app_client.post(
        "/robin/api/books/fresh-book/annotations",
        json=payload,
    )
    assert r.status_code == 200, r.text

    # And round-trip: GET should return v3 with the item present.
    got = app_client.get("/robin/api/books/fresh-book/annotations")
    assert got.status_code == 200
    body = got.json()
    assert body["schema_version"] == 3
    assert len(body["items"]) == 1
    assert body["items"][0]["type"] == "highlight"
    assert body["items"][0]["text"] == "first highlight on fresh book"


def test_post_v3_set_with_reflection_item_succeeds(app_client):
    """The C 反思 flow on a v3 set — book_reader.js posts a ``reflection`` item
    (the v3 wire name; ``comment`` was v2-only and 422s against the v3 union).
    Pins the payload shape submitComment() sends since the 2026-06-10 fix."""
    _upload(app_client, "refl-book")
    payload = _v3_empty_set("refl-book")
    payload["items"] = [
        {
            "type": "reflection",
            "chapter_ref": "ch01.xhtml",
            "cfi_anchor": "epubcfi(/6/4!/4/2,/1:0,/1:8)",
            "body": "chapter-level thought",
            "book_version_hash": _HASH,
            "created_at": _TS,
            "modified_at": _TS,
        }
    ]
    r = app_client.post("/robin/api/books/refl-book/annotations", json=payload)
    assert r.status_code == 200, r.text
    got = app_client.get("/robin/api/books/refl-book/annotations").json()
    assert [it["type"] for it in got["items"]] == ["reflection"]
    assert got["items"][0]["body"] == "chapter-level thought"


def test_fresh_book_get_mutate_post_roundtrip(app_client):
    """The reader's actual save flow: GET the set, append an item to the returned
    body verbatim (book_reader.js does ``{...currentSet, items: [...]}``), POST it
    back. This is the seam where both 2026-06-10 regressions lived:

    - GET fabricated a v2 set for a book with no annotation file, so the POST was
      validated as AnnotationSetV2 and the v3-shaped item 422'd, and
    - GET decorates the body with the read-only ``conflicts`` key (ADR-044 §B8),
      which the reader round-trips and set-level extra="forbid" rejected — this
      one broke saves on ALL books, not just fresh ones.
    """
    _upload(app_client, "fresh-book")
    body = app_client.get("/robin/api/books/fresh-book/annotations").json()
    body["items"] = [*body["items"], _v3_highlight_item("round-trip highlight")]
    body["updated_at"] = _TS
    r = app_client.post("/robin/api/books/fresh-book/annotations", json=body)
    assert r.status_code == 200, r.text

    got = app_client.get("/robin/api/books/fresh-book/annotations").json()
    assert got["schema_version"] == 3
    assert len(got["items"]) == 1
    assert got["items"][0]["text"] == "round-trip highlight"


def test_existing_book_get_mutate_post_roundtrip(app_client):
    """Same round-trip for a book that already has annotations on disk — pins the
    ``conflicts`` regression independently of the fresh-book v2/v3 one."""
    _upload(app_client, "alpha")
    seed = _v3_empty_set("alpha")
    seed["items"] = [_v3_highlight_item("first")]
    assert app_client.post("/robin/api/books/alpha/annotations", json=seed).status_code == 200

    body = app_client.get("/robin/api/books/alpha/annotations").json()
    assert body["conflicts"] == []  # decoration present — the reader will echo it
    body["items"] = [*body["items"], _v3_highlight_item("second")]
    r = app_client.post("/robin/api/books/alpha/annotations", json=body)
    assert r.status_code == 200, r.text
    got = app_client.get("/robin/api/books/alpha/annotations").json()
    assert [it["text"] for it in got["items"]] == ["first", "second"]


def test_post_v2_set_with_v3_shaped_item_still_422s(app_client):
    """Defensive: if some future client mistakenly mixes a v2 set with a v3
    item (the exact bug we just fixed), the server must still 422 — extra
    ``text`` field is forbidden in HighlightV2. Locks in the schema contract."""
    _upload(app_client, "mixed")
    bad = _v2_payload("mixed", items=[_v3_highlight_item("oops")])
    r = app_client.post("/robin/api/books/mixed/annotations", json=bad)
    assert r.status_code == 422
    detail = r.json()["detail"]
    # Pydantic surfaces extra_forbidden for the ``text`` field
    assert any(
        err.get("type") == "extra_forbidden" and err.get("loc", [])[-1] == "text" for err in detail
    ), detail
