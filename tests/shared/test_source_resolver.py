"""Tests for ``shared.source_resolver.RegistrySourceResolver`` (ADR-024
Slice 10 / N518a).

Brief §5 AT4-AT7:

- AT4 ``resolve("ebook:abc123")`` delegates to registry, returns its result.
- AT5 ``resolve("inbox:Inbox/kb/foo.md")`` works without parsing the path.
- AT6 Registry says missing → resolver returns ``None`` (NOT raises).
- AT7 Mock registry; assert ``resolver.resolve(id)`` calls
  ``registry.resolve`` exactly once with a key derived from the raw id.
  No file IO from resolver itself.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from shared.reading_source_registry import BookKey, InboxKey
from shared.schemas.reading_source import ReadingSource, SourceVariant
from shared.source_resolver import RegistrySourceResolver


def _ebook_source(book_id: str = "abc123") -> ReadingSource:
    return ReadingSource(
        source_id=f"ebook:{book_id}",
        annotation_key=book_id,
        kind="ebook",
        title="Test Book",
        author=None,
        primary_lang="en",
        has_evidence_track=True,
        evidence_reason=None,
        variants=[
            SourceVariant(
                role="original",
                format="epub",
                lang="en",
                path=f"data/books/{book_id}/original.epub",
            )
        ],
        metadata={},
    )


def _inbox_source(rel_path: str = "Inbox/kb/foo.md") -> ReadingSource:
    return ReadingSource(
        source_id=f"inbox:{rel_path}",
        annotation_key="foo",
        kind="inbox_document",
        title="Foo",
        author=None,
        primary_lang="en",
        has_evidence_track=True,
        evidence_reason=None,
        variants=[
            SourceVariant(
                role="original",
                format="markdown",
                lang="en",
                path=rel_path,
            )
        ],
        metadata={},
    )


# ── AT4 — ebook namespace ───────────────────────────────────────────────────


def test_at4_registry_source_resolver_resolves_ebook_namespace():
    expected = _ebook_source("abc123")
    fake_registry = MagicMock()
    fake_registry.resolve.return_value = expected

    resolver = RegistrySourceResolver(registry=fake_registry)
    out = resolver.resolve("ebook:abc123")

    assert out is expected
    fake_registry.resolve.assert_called_once()
    key = fake_registry.resolve.call_args.args[0]
    assert isinstance(key, BookKey)
    assert key.book_id == "abc123"


# ── AT5 — inbox namespace ───────────────────────────────────────────────────


def test_at5_registry_source_resolver_resolves_inbox_namespace():
    expected = _inbox_source("Inbox/kb/foo.md")
    fake_registry = MagicMock()
    fake_registry.resolve.return_value = expected

    resolver = RegistrySourceResolver(registry=fake_registry)
    out = resolver.resolve("inbox:Inbox/kb/foo.md")

    assert out is expected
    fake_registry.resolve.assert_called_once()
    key = fake_registry.resolve.call_args.args[0]
    assert isinstance(key, InboxKey)
    # Key's relative_path is the raw post-namespace string — NOT parsed
    # as a filesystem path.
    assert key.relative_path == "Inbox/kb/foo.md"


# ── AT6 — unknown returns None ───────────────────────────────────────────────


def test_at6_registry_source_resolver_unknown_returns_none():
    fake_registry = MagicMock()
    fake_registry.resolve.return_value = None

    resolver = RegistrySourceResolver(registry=fake_registry)
    assert resolver.resolve("ebook:does-not-exist") is None
    assert resolver.resolve("inbox:Inbox/kb/missing.md") is None


def test_resolver_unknown_namespace_returns_none_without_registry_call():
    """A ``source_id`` with no recognized namespace prefix doesn't even
    reach the registry — the adapter returns ``None`` directly."""
    fake_registry = MagicMock()
    resolver = RegistrySourceResolver(registry=fake_registry)
    assert resolver.resolve("not-a-real-namespace:xyz") is None
    fake_registry.resolve.assert_not_called()


def test_resolver_empty_source_id_returns_none():
    fake_registry = MagicMock()
    resolver = RegistrySourceResolver(registry=fake_registry)
    assert resolver.resolve("") is None
    fake_registry.resolve.assert_not_called()


def test_resolver_namespace_prefix_only_returns_none():
    """``ebook:`` (empty body) is not a valid id — return None without
    calling the registry."""
    fake_registry = MagicMock()
    resolver = RegistrySourceResolver(registry=fake_registry)
    assert resolver.resolve("ebook:") is None
    assert resolver.resolve("inbox:") is None
    fake_registry.resolve.assert_not_called()


# ── AT7 — never parses path; calls registry once with raw id ────────────────


def test_at7_registry_source_resolver_never_parses_path():
    """Resolver does NOT do filesystem IO. Confirm by passing a fake
    registry that records calls; the resolver only delegates."""
    fake_registry = MagicMock()
    fake_registry.resolve.return_value = None

    resolver = RegistrySourceResolver(registry=fake_registry)
    # Use a path-shaped id that would tempt a naive impl to walk
    # filesystem segments. Resolver should pass it whole.
    raw_id = "inbox:Inbox/kb/some-deep/path.md"
    resolver.resolve(raw_id)

    fake_registry.resolve.assert_called_once()
    key = fake_registry.resolve.call_args.args[0]
    assert isinstance(key, InboxKey)
    # Whole post-namespace string preserved verbatim — adapter did NOT
    # split / collapse / canonicalize.
    assert key.relative_path == "Inbox/kb/some-deep/path.md"


def test_resolver_does_not_import_book_storage_or_fastapi():
    """Static import surface check — adapter shouldn't pull in heavy
    deps just to translate a string into a SourceKey."""
    import shared.source_resolver as mod

    src = mod.__file__ or ""
    assert src.endswith("source_resolver.py")
    # Walk module dict for forbidden top-level imports.
    forbidden = {"book_storage", "fastapi", "anthropic"}
    for name in vars(mod):
        assert name not in forbidden, f"forbidden import surfaced: {name}"
