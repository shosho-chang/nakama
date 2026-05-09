"""ADR-024 Slice 2 (#510) — inbox-side ``/read`` route slug derivation.

Pre-#510: ``thousand_sunny/routers/robin.py:235`` did
``slug = annotation_slug(file, frontmatter)`` ad hoc, computing the slug from
whatever ``file`` URL the user landed on — even when ``foo-bilingual.md`` was
the user-facing sibling that ``_get_inbox_files`` would normally expose.

Post-#510: slug is derived via
``ReadingSourceRegistry.resolve(InboxKey(f"Inbox/kb/{file}")).annotation_key``,
which collapses to the bilingual sibling per the registry's
``_resolve_inbox`` rule (mirrors ``_get_inbox_files``).

This is an intentional convergence behavior change documented in PR #510:
the route ``/read?file=foo.md`` is reachable when ``foo-bilingual.md``
exists (the listing UI hides ``foo.md`` but the URL is not server-rewritten),
so the migration changes which frontmatter the slug derives from when both
siblings exist.

Tests below pin:
- T-N7: registry-derived slug is injected into the reader template
- T-N8: HTTP 404 when registry returns None (file genuinely missing)
- T-N9: bilingual-sibling URL convergence — ``/read?file=foo.md`` derives slug
  from ``foo-bilingual.md`` frontmatter, not ``foo.md`` frontmatter
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    import shared.config as cfg

    importlib.reload(cfg)
    return tmp_path


@pytest.fixture
def client(vault, monkeypatch):
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


# ---------------------------------------------------------------------------
# T-N7: registry-derived slug injected into reader template
# ---------------------------------------------------------------------------


def test_n7_inbox_reader_uses_registry_slug(client, vault, monkeypatch):
    """``/read?file=foo.md&base=inbox`` derives slug via ReadingSourceRegistry,
    NOT via legacy ``annotation_slug(file, frontmatter)``.

    Asserted by intercepting ReadingSourceRegistry.resolve and confirming it's
    the slug that flows through to the reader template (saved in subsequent
    annotation save).
    """
    tc, mod = client
    monkeypatch.setattr(mod, "fetch_images", lambda p: 0)

    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    src = inbox / "foo.md"
    src.write_text(
        '---\ntitle: "Original-only Title"\nlang: en\n---\n\nbody content',
        encoding="utf-8",
    )

    # Spy on the registry to confirm InboxKey is constructed with vault-relative path.
    captured: dict = {}
    real_registry_cls = mod.ReadingSourceRegistry

    class SpyRegistry(real_registry_cls):
        def resolve(self, key):
            captured["key_type"] = type(key).__name__
            captured["relative_path"] = getattr(key, "relative_path", None)
            return super().resolve(key)

    monkeypatch.setattr(mod, "ReadingSourceRegistry", SpyRegistry)

    r = tc.get("/read", params={"file": "foo.md"})
    assert r.status_code == 200
    assert captured["key_type"] == "InboxKey"
    assert captured["relative_path"] == "Inbox/kb/foo.md"

    # Slug derived from frontmatter title via annotation_slug, post-registry.
    assert "original-only-title" in r.text


# ---------------------------------------------------------------------------
# T-N8: HTTP 404 when registry returns None
# ---------------------------------------------------------------------------


def test_n8_inbox_reader_404_when_registry_returns_none(client, vault, monkeypatch):
    """If the file genuinely doesn't exist, the route raises 404 before even
    reaching the registry. Testing the explicit None branch requires forcing
    registry.resolve → None while file_path.exists() is True (e.g. corrupted
    frontmatter)."""
    tc, mod = client
    monkeypatch.setattr(mod, "fetch_images", lambda p: 0)

    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    # File exists on disk but registry returns None — simulate by patching
    # ReadingSourceRegistry.resolve to return None.
    (inbox / "exists-but-fails.md").write_text("---\ntitle: Foo\n---\nbody", encoding="utf-8")

    class _NoneRegistry:
        def resolve(self, _key):
            return None

    monkeypatch.setattr(mod, "ReadingSourceRegistry", lambda: _NoneRegistry())

    r = tc.get("/read", params={"file": "exists-but-fails.md"})
    assert r.status_code == 404


def test_n8_inbox_reader_404_when_file_missing(client, vault):
    """The pre-existing 404 branch (file path doesn't resolve) still fires —
    upstream of registry resolve."""
    tc, _ = client
    (vault / "Inbox" / "kb").mkdir(parents=True)
    r = tc.get("/read", params={"file": "nonexistent.md"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# T-N9: bilingual-sibling URL convergence — slug derives from sibling frontmatter
# ---------------------------------------------------------------------------


def test_n9_bilingual_sibling_url_convergence(client, vault, monkeypatch):
    """``/read?file=foo.md`` is reachable when ``foo-bilingual.md`` exists
    (URL not server-rewritten; only the inbox listing collapses). The
    registry-driven migration converges this so the slug derives from the
    BILINGUAL sibling frontmatter, NOT the original sibling frontmatter.

    Intentional behavior change documented in PR body.
    """
    tc, mod = client
    monkeypatch.setattr(mod, "fetch_images", lambda p: 0)

    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    # Two siblings with DIFFERENT titles — proves which one drove the slug.
    (inbox / "foo.md").write_text(
        '---\ntitle: "Original Plain Title"\nlang: en\n---\n\nbody',
        encoding="utf-8",
    )
    (inbox / "foo-bilingual.md").write_text(
        '---\ntitle: "Bilingual Reading Title"\nbilingual: true\n'
        'derived_from: "Inbox/kb/foo.md"\n---\n\nbilingual body',
        encoding="utf-8",
    )

    # Hit the original-only URL (still reachable per investigation §4.3).
    r = tc.get("/read", params={"file": "foo.md"})
    assert r.status_code == 200
    # Post-migration: slug derives from BILINGUAL sibling frontmatter title.
    assert "bilingual-reading-title" in r.text
    # Negative: pre-migration (legacy ad-hoc) would have used original title.
    assert "original-plain-title" not in r.text


def test_n9_inbox_reader_sources_base_keeps_legacy_derivation(client, vault, monkeypatch):
    """``base=sources`` (KB/Wiki/Sources/...) is NOT in the registry's scope,
    so the slug must fall back to the legacy ``annotation_slug(file, frontmatter)``
    behavior. This guards against a regression where the migration accidentally
    breaks the sources-side reader (e.g. PubMed bilingual files).
    """
    tc, mod = client
    monkeypatch.setattr(mod, "fetch_images", lambda p: 0)

    sources = vault / "KB" / "Wiki" / "Sources"
    sources.mkdir(parents=True)
    (sources / "pubmed-12345-bilingual.md").write_text(
        '---\ntitle: "PubMed 12345 — 雙語閱讀版"\nbilingual: true\n---\n\nbody',
        encoding="utf-8",
    )

    r = tc.get(
        "/read",
        params={"file": "pubmed-12345-bilingual.md", "base": "sources"},
    )
    assert r.status_code == 200
    # Slug derived from frontmatter title via annotation_slug (sources path).
    assert "pubmed-12345" in r.text
