"""Behavior tests for ``shared.epub_sanitizer.sanitize_epub`` (Slice 1A).

The sanitizer is the second line of defense against XSS from user-uploaded
EPUBs (CSP ``script-src 'self'`` is the first). foliate-js renders chapter
HTML directly into our origin, so any ``<script>`` or ``on*`` handler that
slips through gets eval'd with full session-cookie access. These tests pin
the behavior through the public interface only; the implementation may use
any HTML/XML parser internally.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from tests.shared._epub_fixtures import (
    EPUBSpec,
    epub_clean,
    epub_with_inline_handlers,
    epub_with_script_tag,
    make_epub_blob,
)

# Module under test — failing import is part of the RED phase.
sanitizer = pytest.importorskip(
    "shared.epub_sanitizer",
    reason="shared.epub_sanitizer is the production module Step 1A must create",
)
sanitize_epub = sanitizer.sanitize_epub
EPUBStructureError = sanitizer.EPUBStructureError


# ---------------------------------------------------------------------------
# Helpers — read post-sanitize zip back without leaking impl details.
# ---------------------------------------------------------------------------


def _read_chapter(blob: bytes, name: str) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return zf.read(f"OEBPS/{name}").decode("utf-8")


def _chapter_names(blob: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return [n for n in zf.namelist() if n.startswith("OEBPS/") and n.endswith(".xhtml")]


# ---------------------------------------------------------------------------
# Tracer bullet — sanitizing a clean EPUB returns a parseable EPUB.
# ---------------------------------------------------------------------------


def test_sanitize_clean_epub_returns_parseable_zip():
    out = sanitize_epub(epub_clean())
    # The output must be a real zip; opening it must not raise.
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        names = zf.namelist()
    assert "mimetype" in names
    assert "META-INF/container.xml" in names
    assert "OEBPS/content.opf" in names


# ---------------------------------------------------------------------------
# Script-tag stripping — the malicious payload is gone after sanitize.
# ---------------------------------------------------------------------------


def test_sanitize_strips_script_tags_from_chapter_html():
    out = sanitize_epub(epub_with_script_tag())
    ch1 = _read_chapter(out, "ch1.xhtml")
    assert "<script" not in ch1
    assert "alert(1)" not in ch1
    assert "document.cookie" not in ch1
    # Surrounding text must survive.
    assert "before" in ch1
    assert "after" in ch1


def test_sanitize_strips_script_in_head_and_body():
    """Script tags can appear in <head> as well as <body>; both must be killed."""
    out = sanitize_epub(epub_with_script_tag())
    ch1 = _read_chapter(out, "ch1.xhtml")
    # Two <script> tags in the fixture, one in head, one in body.
    assert ch1.lower().count("<script") == 0


# ---------------------------------------------------------------------------
# Inline event-handler stripping — on* attributes nuked across all elements.
# ---------------------------------------------------------------------------


def test_sanitize_strips_inline_on_handlers():
    out = sanitize_epub(epub_with_inline_handlers())
    ch1 = _read_chapter(out, "ch1.xhtml")
    for handler in ("onload=", "onclick=", "onmouseover=", "onerror="):
        assert handler not in ch1, f"{handler} survived sanitize"
    # Element text and href must survive — sanitizer only removes the dangerous attr.
    assert "click me" in ch1
    assert 'href="#x"' in ch1


# ---------------------------------------------------------------------------
# Deep walk — every chapter file inside the zip is sanitized, not just ch1.
# ---------------------------------------------------------------------------


def test_sanitize_walks_every_xhtml_in_zip():
    spec = EPUBSpec(
        chapters={
            "ch1.xhtml": '<html><body><script>a()</script><p>one</p></body></html>',
            "ch2.xhtml": '<html><body><p onclick="b()">two</p></body></html>',
            "ch3.xhtml": '<html><body><img onerror="c()" src="x"/></body></html>',
        },
        nav_xhtml=None,  # spice — exercise sanitizer when nav is absent
    )
    out = sanitize_epub(make_epub_blob(spec))
    for name in ("ch1.xhtml", "ch2.xhtml", "ch3.xhtml"):
        body = _read_chapter(out, name)
        assert "<script" not in body
        assert "onclick=" not in body
        assert "onerror=" not in body


# ---------------------------------------------------------------------------
# EPUB structural integrity — mimetype first + uncompressed survives sanitize.
# ---------------------------------------------------------------------------


def test_sanitize_preserves_mimetype_first_and_stored():
    out = sanitize_epub(epub_with_script_tag())
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        # mimetype must be the first member and stored (uncompressed) per
        # EPUB-OCF spec; foliate-js / readers reject blobs that violate this.
        first_info = zf.infolist()[0]
        assert first_info.filename == "mimetype"
        assert first_info.compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/epub+zip"


def test_sanitize_preserves_container_xml():
    out = sanitize_epub(epub_with_script_tag())
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        container = zf.read("META-INF/container.xml").decode("utf-8")
    assert "OEBPS/content.opf" in container


def test_sanitize_preserves_chapter_count():
    inp = epub_with_script_tag()
    out = sanitize_epub(inp)
    assert sorted(_chapter_names(inp)) == sorted(_chapter_names(out))


# ---------------------------------------------------------------------------
# Idempotent — sanitizing twice equals sanitizing once.
# ---------------------------------------------------------------------------


def test_sanitize_is_idempotent_on_dirty_input():
    once = sanitize_epub(epub_with_script_tag())
    twice = sanitize_epub(once)
    # Compare the chapter bodies, not the byte-equal zip stream — zip metadata
    # (timestamps, central-dir order) may diverge harmlessly across runs.
    assert _read_chapter(once, "ch1.xhtml") == _read_chapter(twice, "ch1.xhtml")


# ---------------------------------------------------------------------------
# Failure modes — non-EPUB blob raises EPUBStructureError.
# ---------------------------------------------------------------------------


def test_sanitize_rejects_non_zip_blob():
    with pytest.raises(EPUBStructureError):
        sanitize_epub(b"this is not a zip at all")


def test_sanitize_rejects_zip_without_container_xml():
    """A zip lacking META-INF/container.xml is not a valid OCF package."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("hello.txt", "world")
    with pytest.raises(EPUBStructureError):
        sanitize_epub(buf.getvalue())
