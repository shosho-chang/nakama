"""Behavior tests for ``shared.epub_metadata.extract_metadata`` (Slice 1B).

The reader uses extracted metadata to populate the ``books`` table on upload
(title / author / cover / ISBN / published year / TOC). All fields are
optional in the wild — Penguin EPUBs love omitting ``dc:date``, indie EPUBs
sometimes ship without ``dc:creator``. The contract: present field → string;
missing field → None; malformed package → ``MalformedEPUBError``.
"""

from __future__ import annotations

import pytest

from tests.shared._epub_fixtures import (
    EPUBSpec,
    epub_clean,
    epub_malformed_opf,
    epub_minimal_metadata,
    epub_with_cover,
    make_epub_blob,
)

meta_mod = pytest.importorskip(
    "shared.epub_metadata",
    reason="shared.epub_metadata is the production module Step 1B must create",
)
extract_metadata = meta_mod.extract_metadata
MalformedEPUBError = meta_mod.MalformedEPUBError


# ---------------------------------------------------------------------------
# Tracer bullet — basic title + author extraction.
# ---------------------------------------------------------------------------


def test_extract_returns_title_and_author_from_default_fixture():
    md = extract_metadata(epub_clean())
    assert md.title == "The Tracer"
    assert md.author == "Anon"


# ---------------------------------------------------------------------------
# Optional fields — present → typed; missing → None.
# ---------------------------------------------------------------------------


def test_extract_returns_isbn_when_present():
    md = extract_metadata(epub_clean())
    # urn:isbn:9780000000001 → "9780000000001" (impl strips the urn prefix)
    assert md.isbn == "9780000000001"


def test_extract_returns_published_year_as_int():
    md = extract_metadata(epub_clean())
    # dc:date "2024-03-15" → published_year=2024
    assert md.published_year == 2024


def test_extract_returns_language():
    md = extract_metadata(epub_clean())
    assert md.lang == "en"


def test_missing_title_returns_none():
    md = extract_metadata(epub_minimal_metadata())
    assert md.title is None


def test_missing_creator_returns_none():
    md = extract_metadata(epub_minimal_metadata())
    assert md.author is None


def test_missing_isbn_returns_none():
    md = extract_metadata(epub_minimal_metadata())
    assert md.isbn is None


def test_missing_date_returns_none():
    md = extract_metadata(epub_minimal_metadata())
    assert md.published_year is None


def test_missing_language_returns_none():
    md = extract_metadata(epub_minimal_metadata())
    assert md.lang is None


# ---------------------------------------------------------------------------
# Cover image detection — manifest item with properties="cover-image".
# ---------------------------------------------------------------------------


def test_cover_detected_from_manifest_property():
    md = extract_metadata(epub_with_cover())
    # cover_path is a path inside the EPUB zip ("OEBPS/cover.png").
    assert md.cover_path is not None
    assert md.cover_path.endswith("cover.png")


def test_no_cover_returns_none():
    md = extract_metadata(epub_clean())
    assert md.cover_path is None


# ---------------------------------------------------------------------------
# TOC extraction — read from nav.xhtml (epub3).
# ---------------------------------------------------------------------------


def test_toc_extracted_from_nav_xhtml():
    md = extract_metadata(epub_clean())
    titles = [entry.title for entry in md.toc]
    assert titles == ["Chapter 1", "Chapter 2"]


def test_toc_entries_carry_href():
    md = extract_metadata(epub_clean())
    hrefs = [entry.href for entry in md.toc]
    assert hrefs == ["ch1.xhtml", "ch2.xhtml"]


def test_no_nav_returns_empty_toc():
    spec = EPUBSpec(nav_xhtml=None)
    md = extract_metadata(make_epub_blob(spec))
    assert md.toc == []


# ---------------------------------------------------------------------------
# Failure modes — malformed package raises MalformedEPUBError.
# ---------------------------------------------------------------------------


def test_malformed_opf_raises():
    with pytest.raises(MalformedEPUBError):
        extract_metadata(epub_malformed_opf())


def test_non_zip_blob_raises():
    with pytest.raises(MalformedEPUBError):
        extract_metadata(b"\x00\x01\x02 not a zip")
