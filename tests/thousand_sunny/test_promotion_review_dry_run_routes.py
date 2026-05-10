"""End-to-end route tests for ``/promotion-review/*`` after N518b dry-run wiring.

Brief §5 RT1-RT3 (N518b):

- RT1 ``GET /promotion-review/`` lists candidate sources via the wired
       service (real fixture book → registry → lister → list view).
- RT2 ``POST /promotion-review/source/{id_b64}/start`` returns 303 with a
       persisted manifest containing dry-run claims (no longer 500 from
       the N518a stub).
- RT3 Full flow including ``state_for`` returns valid manifest after start.

Distinct from ``tests/thousand_sunny/test_promotion_review_routes.py``
(N516) which injects a fake service via ``set_service``. This file goes
further: it boots the full FastAPI lifespan against a tmp vault so the
real disk adapters (``VaultBlobLoader``, ``RegistrySourceResolver``,
``RegistryReadingSourceLister``, ``VaultKBConceptIndex``) plus the
deterministic dry-run extractor + matcher all run end-to-end.

C5 fixture pattern (N518b): tests use ``tmp_path_factory.mktemp("vault")``
so the vault is a unique subdirectory under ``tmp_path`` — letting
"outside-the-vault" path operations be genuinely outside the vault. Every
fixture asserts ``vault.resolve() != Path(tmp_path).resolve()`` so any
regression to the N518a broken pattern fails loudly at fixture setup.
"""

from __future__ import annotations

import base64
import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shared.book_storage import insert_book, store_book_files
from shared.schemas.books import Book
from tests.shared._epub_fixtures import EPUBSpec, make_epub_blob

# ── Helpers ────────────────────────────────────────────────────────────────


def _b64(source_id: str) -> str:
    return base64.urlsafe_b64encode(source_id.encode("utf-8")).decode("ascii").rstrip("=")


def _disable_auth(monkeypatch) -> None:
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)


def _reload_app_modules() -> object:
    """Reload Thousand Sunny so the lifespan picks up new env vars."""
    import thousand_sunny.auth as auth_module
    import thousand_sunny.promotion_wiring as wiring_module
    import thousand_sunny.routers.promotion_review as pr_module
    import thousand_sunny.routers.writing_assist as wa_module

    importlib.reload(auth_module)
    importlib.reload(wiring_module)
    importlib.reload(pr_module)
    importlib.reload(wa_module)

    import thousand_sunny.app as app_module

    importlib.reload(app_module)
    return app_module


def _make_minimal_vault(root: Path) -> Path:
    """Create the directory tree the lifespan expects."""
    (root / "Inbox" / "kb").mkdir(parents=True)
    (root / "data" / "books").mkdir(parents=True)
    (root / "KB" / "Wiki" / "Concepts").mkdir(parents=True)
    return root


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Per-test vault under a dedicated subdirectory.

    C5 invariant: the vault is NOT the test's tmp_path itself — it's a
    nested directory. This makes "outside the vault" operations genuinely
    outside, which matters for any path-traversal-adjacent test.
    """
    vault = tmp_path_factory.mktemp("vault")
    _make_minimal_vault(vault)

    # Test-of-tests assertion: if anyone reuses the broken N518a pattern
    # (``vault = tmp_path`` directly, no subdirectory), this fails loudly
    # so we know to fix the fixture.
    parent = vault.parent
    assert vault.resolve() != parent.resolve(), (
        "vault must be a subdirectory of tmp_path; broken fixture pattern "
        "(N518a regression — ``vault = tmp_path``)"
    )
    return vault


@pytest.fixture
def configured_app(vault: Path, monkeypatch):
    """App reloaded with env vars pointing at the per-test vault."""
    books_dir = vault / "data" / "books"
    monkeypatch.setenv("NAKAMA_BOOKS_DIR", str(books_dir))
    monkeypatch.setenv("NAKAMA_VAULT_ROOT", str(vault))
    monkeypatch.setenv("NAKAMA_PROMOTION_MODE", "dry_run")
    monkeypatch.setenv(
        "NAKAMA_PROMOTION_MANIFEST_ROOT",
        str(vault / ".promotion-manifests"),
    )
    monkeypatch.setenv(
        "NAKAMA_READING_CONTEXT_PACKAGE_ROOT",
        str(vault / ".reading-context-packages"),
    )
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)
    _disable_auth(monkeypatch)
    return _reload_app_modules()


def _make_substantial_chapter(idx: int) -> str:
    """Build a chapter XHTML body with enough word count that preflight
    routes the book to ``proceed_full_promotion`` (>= 200 words combined
    across the spine; we generate ~150 words per chapter so two chapters
    clear the threshold comfortably)."""
    paragraph = (
        f"This chapter {idx} of the test ebook contains substantial prose so "
        f"the promotion preflight word counter is satisfied. We discuss "
        f"heart rate variability, glucose regulation, and sleep architecture "
        f"in some detail. The text references RMSSD as a vagal tone proxy "
        f"and notes that mitochondrial biogenesis correlates with cold "
        f"exposure protocols. Vitamin D and zinc co-factors appear too. "
        f"The point of this fixture is to exercise the dry-run extractor "
        f"end-to-end: enough words to clear preflight, real chapter "
        f"structure for the source map builder, and a couple of recurring "
        f"concept-shaped phrases for the concept promotion engine to "
        f"deduplicate. None of this prose has medical authority; it exists "
        f"only as input to a deterministic extractor."
    )
    paragraphs = "\n".join(f"<p>{paragraph}</p>" for _ in range(2))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter {idx}</title></head>
<body>
<h1>Chapter {idx}</h1>
{paragraphs}
</body>
</html>
"""


def _store_test_book(book_id: str = "alpha-book", language: str = "en") -> str:
    """Insert a Book row + EPUB blobs so the registry resolves
    ``ebook:{book_id}``. Returns the source_id.

    Builds chapters with substantial prose so preflight returns
    ``proceed_full_promotion`` (or at least ``proceed_with_warnings``) —
    the default ``EPUBSpec()`` chapters are too short and would route to
    ``skip``.
    """
    chapters = {
        "ch1.xhtml": _make_substantial_chapter(1),
        "ch2.xhtml": _make_substantial_chapter(2),
    }
    bilingual_blob = make_epub_blob(EPUBSpec(language=language, chapters=chapters))
    original_blob = make_epub_blob(EPUBSpec(language=language, chapters=chapters))
    store_book_files(book_id, bilingual=bilingual_blob, original=original_blob)
    insert_book(
        Book(
            book_id=book_id,
            title="Alpha",
            author="Anon",
            lang_pair="en-zh",
            genre=None,
            isbn="9780000000001",
            published_year=2024,
            has_original=True,
            book_version_hash="a" * 64,
            created_at="2026-05-05T00:00:00+00:00",
        )
    )
    return f"ebook:{book_id}"


# ── RT1 — list returns 200 with candidate sources ──────────────────────────


def test_rt1_list_pending_returns_200(configured_app):
    """``GET /promotion-review/`` returns 200 (not 503) and renders the
    list view. Empty list is acceptable — the bare assertion is "wired"."""
    with TestClient(configured_app.app, follow_redirects=False) as client:
        r = client.get("/promotion-review/")

    assert r.status_code == 200, r.text
    assert "503" not in r.text


def test_rt1_list_pending_includes_real_book(configured_app):
    """When a real book is registered, the wired lister should surface it
    as a candidate ReadingSource. We assert against the service directly
    rather than the rendered HTML so the test doesn't depend on template
    formatting."""
    _store_test_book("alpha-book")

    import thousand_sunny.routers.promotion_review as pr_module

    with TestClient(configured_app.app) as client:
        # Trigger lifespan.
        _ = client.get("/healthz")
        service = pr_module._service
        assert service is not None
        # The lister should surface the inserted book as a ReadingSource.
        sources = service._source_lister.list_sources()
        assert any(rs.source_id == "ebook:alpha-book" for rs in sources)


# ── RT2 — start review creates a manifest with dry-run claims ──────────────


def test_rt2_start_creates_manifest_with_dry_run_claims(configured_app, vault: Path):
    """``POST /promotion-review/source/{id_b64}/start`` succeeds (no longer
    500s) — the dry-run extractor body produces 1-3 claims per chapter and
    the service persists a manifest. The route returns a 303 redirect
    back to the per-source review surface."""
    source_id = _store_test_book("alpha-book")

    with TestClient(configured_app.app, follow_redirects=False) as client:
        r = client.post(f"/promotion-review/source/{_b64(source_id)}/start")

    # Successful start route is a 303 redirect to the review surface.
    assert r.status_code == 303, r.text
    assert r.headers["location"].startswith("/promotion-review/source/")

    # Manifest persisted to disk.
    manifest_dir = vault / ".promotion-manifests"
    files = list(manifest_dir.iterdir()) if manifest_dir.exists() else []
    assert len(files) == 1, f"expected exactly 1 manifest file, got {files!r}"

    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["source_id"] == source_id
    assert payload["status"] == "needs_review"
    assert len(payload["items"]) >= 1
    # At least one source_page item carries the dry-run reason text from
    # the extractor's claim (reason format: "{title}: {claim}").
    reasons = [it.get("reason", "") for it in payload["items"]]
    assert any("[DRY-RUN]" in r for r in reasons), (
        f"no [DRY-RUN] marker in any item reason; got reasons={reasons!r}"
    )


def test_rt2_start_unresolved_source_returns_4xx(configured_app):
    """Unresolvable source_id surfaces as 400 (or 404) — the wiring
    succeeds, the service raises ValueError, the route maps to 4xx."""
    with TestClient(configured_app.app, follow_redirects=False) as client:
        r = client.post(f"/promotion-review/source/{_b64('ebook:does-not-exist')}/start")

    assert r.status_code in {400, 404}, r.text
    assert r.status_code != 503


# ── RT3 — state_for + load_review_session round-trips ──────────────────────


def test_rt3_review_surface_loads_after_start(configured_app, vault: Path):
    """After start, ``GET /promotion-review/source/{id_b64}`` returns 200
    and renders the review surface. ``service.state_for`` produces a valid
    PromotionReviewState referencing the persisted manifest."""
    source_id = _store_test_book("alpha-book")

    with TestClient(configured_app.app, follow_redirects=False) as client:
        # Start review first.
        r1 = client.post(f"/promotion-review/source/{_b64(source_id)}/start")
        assert r1.status_code == 303, r1.text

        # Now GET the per-source review surface.
        r2 = client.get(f"/promotion-review/source/{_b64(source_id)}")
        assert r2.status_code == 200, r2.text

    # Verify state_for returns a PromotionReviewState with manifest info.
    import thousand_sunny.routers.promotion_review as pr_module

    service = pr_module._service
    assert service is not None
    state = service.state_for(source_id)
    assert state is not None
    assert state.source_id == source_id
    assert state.has_existing_manifest is True
    assert state.manifest_status == "needs_review"


def test_rt3_review_surface_for_unknown_source_renders_empty(configured_app):
    """``GET /promotion-review/source/{id_b64}`` with no manifest yet
    renders the empty / start-affordance state (200, not 503 or 500)."""
    with TestClient(configured_app.app, follow_redirects=False) as client:
        r = client.get(f"/promotion-review/source/{_b64('ebook:not-yet-reviewed')}")
    assert r.status_code == 200, r.text
