"""Behavior tests for ``shared.reading_source_registry`` (ADR-024 Slice 1
/ issue #509).

Coverage matrix per docs/plans/2026-05-09-N509-reading-source-registry.md
§4.4 — 18 tests covering ebook + inbox resolution, sibling canonicalization,
language normalization, NB1 unified failure policy (16/17/18), NB3 lang_pair
exclusion (1/2/3), NB4 empty-slug ValueError (13), reusability outside
FastAPI (14), and constructor fail-fast (15).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from shared import reading_source_registry as rsr_module
from shared.reading_source_registry import (
    BookKey,
    InboxKey,
    ReadingSourceRegistry,
    _normalize_primary_lang,
)
from shared.schemas.books import Book
from tests.shared._epub_fixtures import EPUBSpec, make_epub_blob

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Tmp vault root with ``Inbox/kb/`` pre-created."""
    inbox = tmp_path / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def books_dir(tmp_path: Path, monkeypatch) -> Path:
    """Reroute ``data/books/`` to a tmp dir."""
    target = tmp_path / "books"
    monkeypatch.setenv("NAKAMA_BOOKS_DIR", str(target))
    return target


@pytest.fixture
def registry(vault: Path) -> ReadingSourceRegistry:
    return ReadingSourceRegistry(vault_root=vault)


def _make_book(
    book_id: str = "alpha-book",
    has_original: bool = True,
    title: str = "Alpha",
    author: str | None = "Anon",
) -> Book:
    return Book(
        book_id=book_id,
        title=title,
        author=author,
        lang_pair="en-zh",
        genre=None,
        isbn="9780000000001",
        published_year=2024,
        has_original=has_original,
        book_version_hash="a" * 64,
        created_at="2026-05-05T00:00:00+00:00",
    )


def _store_book(book_id: str, *, has_original: bool, language: str) -> Book:
    """Insert a Book row + write blobs with the given OPF dc:language."""
    from shared.book_storage import insert_book, store_book_files

    bilingual_blob = make_epub_blob(EPUBSpec(language=language))
    original_blob = make_epub_blob(EPUBSpec(language=language)) if has_original else None
    store_book_files(book_id, bilingual=bilingual_blob, original=original_blob)
    book = _make_book(book_id=book_id, has_original=has_original)
    insert_book(book)
    return book


# ---------------------------------------------------------------------------
# Ebook tests
# ---------------------------------------------------------------------------


def test_resolve_ebook_with_original(registry: ReadingSourceRegistry, books_dir: Path):
    book_id = "alpha-book"
    _store_book(book_id, has_original=True, language="en")

    rs = registry.resolve(BookKey(book_id))

    assert rs is not None
    assert rs.kind == "ebook"
    assert rs.source_id == f"ebook:{book_id}"
    assert rs.annotation_key == book_id
    assert rs.has_evidence_track is True
    assert rs.evidence_reason is None
    assert rs.primary_lang == "en"
    assert len(rs.variants) == 2
    roles = {v.role for v in rs.variants}
    assert roles == {"original", "display"}
    original_v = next(v for v in rs.variants if v.role == "original")
    display_v = next(v for v in rs.variants if v.role == "display")
    assert original_v.path == f"data/books/{book_id}/original.epub"
    assert original_v.lang == "en"
    assert display_v.path == f"data/books/{book_id}/bilingual.epub"
    assert display_v.lang == "bilingual"
    # NB3: lang_pair must NOT be in metadata.
    assert "lang_pair" not in rs.metadata


def test_resolve_ebook_bilingual_only_true_bilingual(
    registry: ReadingSourceRegistry, books_dir: Path
):
    book_id = "beta-book"
    _store_book(book_id, has_original=False, language="en")

    rs = registry.resolve(BookKey(book_id))

    assert rs is not None
    assert rs.has_evidence_track is False
    assert rs.evidence_reason == "no_original_uploaded"
    assert rs.primary_lang == "en"
    assert len(rs.variants) == 1
    assert rs.variants[0].role == "display"
    assert rs.variants[0].path == f"data/books/{book_id}/bilingual.epub"
    assert "lang_pair" not in rs.metadata


def test_resolve_ebook_phase1_monolingual_zh(registry: ReadingSourceRegistry, books_dir: Path):
    book_id = "gamma-book"
    _store_book(book_id, has_original=False, language="zh-TW")

    rs = registry.resolve(BookKey(book_id))

    assert rs is not None
    assert rs.primary_lang == "zh-Hant"
    assert rs.variants[0].lang == "zh-Hant"
    assert rs.has_evidence_track is False
    assert rs.evidence_reason == "no_original_uploaded"
    assert "lang_pair" not in rs.metadata


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("zh", "zh-Hant"),
        ("zh-TW", "zh-Hant"),
        ("zh-Hant", "zh-Hant"),
        ("zh-CN", "zh-Hant"),
        ("zh-Hans", "zh-Hant"),
        ("en", "en"),
        ("en-US", "en"),
        ("en-GB", "en"),
        ("ja", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_resolve_ebook_lang_normalization(raw: str | None, expected: str):
    assert _normalize_primary_lang(raw) == expected


def test_resolve_ebook_missing(registry: ReadingSourceRegistry, books_dir: Path):
    assert registry.resolve(BookKey("never-inserted")) is None


def test_resolve_ebook_orphan_blob_no_db_row(registry: ReadingSourceRegistry, books_dir: Path):
    """Blob on disk but no DB row → None (DB is the source of truth)."""
    from shared.book_storage import store_book_files

    store_book_files("orphan-book", bilingual=make_epub_blob(EPUBSpec(language="en")))
    # No insert_book call.
    assert registry.resolve(BookKey("orphan-book")) is None


# ---------------------------------------------------------------------------
# Inbox tests
# ---------------------------------------------------------------------------


_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "reading_source" / "inbox"


def _copy_fixture(src_name: str, vault: Path, dest_name: str | None = None) -> Path:
    """Copy a static inbox fixture into the tmp vault's ``Inbox/kb/``."""
    src = _FIXTURES / src_name
    dest = vault / "Inbox" / "kb" / (dest_name or src_name)
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def test_resolve_inbox_plain_only(registry: ReadingSourceRegistry, vault: Path):
    _copy_fixture("foo-en-only.md", vault, dest_name="foo.md")

    rs = registry.resolve(InboxKey("Inbox/kb/foo.md"))

    assert rs is not None
    assert rs.kind == "inbox_document"
    assert rs.source_id == "inbox:Inbox/kb/foo.md"
    assert rs.has_evidence_track is True
    assert rs.evidence_reason is None
    assert rs.primary_lang == "en"
    assert len(rs.variants) == 1
    assert rs.variants[0].role == "original"
    assert rs.variants[0].path == "Inbox/kb/foo.md"
    # annotation_key uses annotation_slug(filename, fm)
    from shared.annotation_store import annotation_slug

    assert rs.annotation_key == annotation_slug("foo.md", {"title": "Foo", "lang": "en"})


def test_resolve_inbox_bilingual_only(registry: ReadingSourceRegistry, vault: Path):
    _copy_fixture("qux-bilingual.md", vault)

    rs = registry.resolve(InboxKey("Inbox/kb/qux-bilingual.md"))

    assert rs is not None
    # source_id points at logical original, even though that file does not
    # exist on disk (N3 design callout — logical identity, NOT filesystem
    # lookup key).
    assert rs.source_id == "inbox:Inbox/kb/qux.md"
    logical_original = vault / "Inbox" / "kb" / "qux.md"
    assert not logical_original.exists()
    assert rs.has_evidence_track is False
    assert rs.evidence_reason == "bilingual_only_inbox"
    assert len(rs.variants) == 1
    assert rs.variants[0].role == "display"
    assert rs.variants[0].lang == "bilingual"
    assert rs.variants[0].path == "Inbox/kb/qux-bilingual.md"


def test_resolve_inbox_both_siblings_canonicalize(registry: ReadingSourceRegistry, vault: Path):
    _copy_fixture("baz.md", vault)
    _copy_fixture("baz-bilingual.md", vault)

    rs_plain = registry.resolve(InboxKey("Inbox/kb/baz.md"))
    rs_bilingual = registry.resolve(InboxKey("Inbox/kb/baz-bilingual.md"))

    assert rs_plain is not None and rs_bilingual is not None
    assert rs_plain.source_id == rs_bilingual.source_id == "inbox:Inbox/kb/baz.md"
    # Both should expose 2 variants.
    for rs in (rs_plain, rs_bilingual):
        assert len(rs.variants) == 2
        roles = {v.role for v in rs.variants}
        assert roles == {"original", "display"}
        assert rs.has_evidence_track is True
        assert rs.evidence_reason is None
    # annotation_key derives from bilingual sibling (collapse rule); both
    # inputs should produce the same key.
    assert rs_plain.annotation_key == rs_bilingual.annotation_key
    from shared.annotation_store import annotation_slug

    assert rs_plain.annotation_key == annotation_slug(
        "baz-bilingual.md", {"title": "Baz", "derived_from": "baz.md"}
    )


def test_resolve_inbox_bilingual_only_no_title_falls_back_to_logical_stem(
    registry: ReadingSourceRegistry, vault: Path
):
    """F7 regression: bilingual-only inbox doc with NO frontmatter ``title``
    must fall back to the **logical original** stem (``foo``) — never to the
    user-facing bilingual stem (``foo-bilingual``). The ``-bilingual`` suffix
    must not leak into ``ReadingSource.title``.
    """
    bilingual = vault / "Inbox" / "kb" / "foo-bilingual.md"
    bilingual.write_text(
        "---\nlang: en\nderived_from: foo.md\n---\n\nbody only.\n",
        encoding="utf-8",
    )

    rs = registry.resolve(InboxKey("Inbox/kb/foo-bilingual.md"))

    assert rs is not None
    assert rs.title == "foo"
    assert rs.title != "foo-bilingual"


def test_resolve_inbox_bilingual_only_with_title_keeps_user_facing_title(
    registry: ReadingSourceRegistry, vault: Path
):
    """F7 invariant: when bilingual frontmatter HAS a ``title``, registry
    keeps it (does not silently switch to logical stem).
    """
    _copy_fixture("qux-bilingual.md", vault)

    rs = registry.resolve(InboxKey("Inbox/kb/qux-bilingual.md"))

    assert rs is not None
    assert rs.title == "Qux"


def test_resolve_inbox_missing_lang_frontmatter(registry: ReadingSourceRegistry, vault: Path):
    _copy_fixture("no-lang.md", vault)

    rs = registry.resolve(InboxKey("Inbox/kb/no-lang.md"))

    assert rs is not None
    assert rs.primary_lang == "unknown"


def test_resolve_inbox_missing_path(registry: ReadingSourceRegistry, vault: Path):
    assert registry.resolve(InboxKey("Inbox/kb/does-not-exist.md")) is None


def test_resolve_inbox_path_outside_vault(registry: ReadingSourceRegistry, vault: Path):
    with pytest.raises(ValueError, match="escapes vault"):
        registry.resolve(InboxKey("../../etc/passwd"))


def test_resolve_inbox_empty_annotation_slug(
    registry: ReadingSourceRegistry, vault: Path, monkeypatch
):
    """NB4 contract: if ``annotation_slug`` returns empty, ``_resolve_inbox``
    raises ``ValueError`` with offending-path message — never emits a
    ReadingSource with empty ``annotation_key``.
    """
    _copy_fixture("foo-en-only.md", vault, dest_name="foo.md")
    monkeypatch.setattr(rsr_module, "annotation_slug", lambda *args, **kwargs: "")

    with pytest.raises(ValueError, match="annotation_slug returned empty"):
        registry.resolve(InboxKey("Inbox/kb/foo.md"))


# ---------------------------------------------------------------------------
# Reusability + bootstrap
# ---------------------------------------------------------------------------


def test_no_fastapi_imports():
    """Importing ``shared.reading_source_registry`` must not pull FastAPI or
    Thousand Sunny into ``sys.modules`` — confirms reusability outside route
    handlers (issue #509 AC).
    """
    src = textwrap.dedent(
        """
        import sys
        import shared.reading_source_registry  # noqa: F401

        offending = [
            m for m in sys.modules if m == "fastapi" or m.startswith("fastapi.")
        ]
        offending += [
            m
            for m in sys.modules
            if m == "thousand_sunny" or m.startswith("thousand_sunny.")
        ]
        if offending:
            print("OFFENDING:" + ",".join(offending))
            sys.exit(1)
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout


def test_vault_root_required(monkeypatch):
    """``ReadingSourceRegistry(vault_root=None)`` falls back to
    ``get_vault_path()`` — when that raises, the constructor must surface
    the failure rather than swallow it.
    """

    def boom():
        raise RuntimeError("vault unconfigured")

    monkeypatch.setattr(rsr_module, "get_vault_path", boom)
    with pytest.raises(RuntimeError, match="vault unconfigured"):
        ReadingSourceRegistry(vault_root=None)


# ---------------------------------------------------------------------------
# NB1 unified failure policy — tests 16 / 17 / 18
# ---------------------------------------------------------------------------


def test_resolve_ebook_malformed_blob(registry: ReadingSourceRegistry, books_dir: Path, caplog):
    """NB1: blob is a valid zip but missing OPF / dc:language → returns
    ``None`` + WARNING; does not raise.
    """
    from shared.book_storage import insert_book, store_book_files

    book_id = "malformed-book"
    # Empty zip — valid zipfile, no META-INF/container.xml. extract_metadata
    # will raise MalformedEPUBError.
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass
    store_book_files(book_id, bilingual=buf.getvalue())
    insert_book(_make_book(book_id=book_id, has_original=False))

    caplog.set_level("WARNING", logger="nakama.shared.reading_source_registry")
    rs = registry.resolve(BookKey(book_id))

    assert rs is None
    assert any(getattr(rec, "category", None) == "ebook_blob_read_failed" for rec in caplog.records)


def test_resolve_ebook_blob_io_error(registry: ReadingSourceRegistry, books_dir: Path, caplog):
    """NB1: blob is registered in DB but missing on disk at resolve time →
    returns ``None`` + WARNING; does not raise ``FileNotFoundError``.
    """
    from shared.book_storage import _books_root, insert_book, store_book_files

    book_id = "io-error-book"
    store_book_files(book_id, bilingual=make_epub_blob(EPUBSpec(language="en")))
    insert_book(_make_book(book_id=book_id, has_original=False))
    # Delete the blob file after registering — simulates filesystem rollback
    # or external cleanup between insert and resolve.
    blob_path = _books_root() / book_id / "bilingual.epub"
    blob_path.unlink()

    caplog.set_level("WARNING", logger="nakama.shared.reading_source_registry")
    rs = registry.resolve(BookKey(book_id))

    assert rs is None
    assert any(getattr(rec, "category", None) == "ebook_blob_read_failed" for rec in caplog.records)


def test_resolve_inbox_malformed_frontmatter(registry: ReadingSourceRegistry, vault: Path, caplog):
    """NB1: malformed YAML in frontmatter → returns ``None`` + WARNING;
    does not raise.
    """
    bad = vault / "Inbox" / "kb" / "broken.md"
    # Unclosed quote in YAML → yaml.YAMLError on parse.
    bad.write_text(
        '---\ntitle: "unterminated\nlang: en\n---\n\nbody\n',
        encoding="utf-8",
    )

    caplog.set_level("WARNING", logger="nakama.shared.reading_source_registry")
    rs = registry.resolve(InboxKey("Inbox/kb/broken.md"))

    assert rs is None
    assert any(
        getattr(rec, "category", None) == "inbox_frontmatter_parse_failed" for rec in caplog.records
    )
