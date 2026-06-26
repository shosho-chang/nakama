"""Unit tests for ``shared.book_raw.prepare_book_raw`` — book EPUB → KB/Raw/Books.

Real ``books`` table + ``shared.epub_text`` (synthetic EPUB); ``read_book_blob`` is
stubbed so no real book files are needed. DB isolation via the autouse ``isolated_db``
fixture. (The whole-pipeline run is exercised by the /processing SSE flow — here we
only cover the blob→text→raw-file prep that ``/start-book`` calls.)
"""

from __future__ import annotations

import io
import zipfile

import pytest

import shared.book_raw as br
from shared.book_storage import insert_book
from shared.epub_text import EPUBTextError
from shared.schemas.books import Book


def _tiny_epub(body: str = "<p>書本內容第一段。</p>") -> bytes:
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest>'
        '<item id="c0" href="ch.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="c0"/></spine></package>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf"'
            ' media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr(
            "OEBPS/ch.xhtml",
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
            + body
            + "</body></html>",
        )
    return buf.getvalue()


def _make_book(book_id: str, *, has_original: bool = True, title: str = "測試書") -> None:
    insert_book(
        Book(
            book_id=book_id,
            title=title,
            author="某作者",
            lang_pair="en-zh",
            genre=None,
            isbn=None,
            published_year=None,
            has_original=has_original,
            book_version_hash="h" * 64,
            created_at="2026-06-26T00:00:00+00:00",
        )
    )


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setattr(br, "read_book_blob", lambda book_id, *, lang: _tiny_epub())
    return tmp_path


def test_prepare_writes_raw_with_frontmatter(wired):
    _make_book("bk1", title="測試書")
    raw = br.prepare_book_raw("bk1", vault=wired)
    assert "Raw" in str(raw) and "Books" in str(raw)
    content = raw.read_text(encoding="utf-8")
    assert "書本內容第一段" in content
    assert "title: 測試書" in content
    assert "source_type: book" in content


def test_prepare_missing_book_raises_lookup(wired):
    with pytest.raises(LookupError):
        br.prepare_book_raw("ghost", vault=wired)


def test_prepare_bad_epub_raises_epubtext(wired, monkeypatch):
    monkeypatch.setattr(br, "read_book_blob", lambda book_id, *, lang: b"not an epub")
    _make_book("bad1")
    with pytest.raises(EPUBTextError):
        br.prepare_book_raw("bad1", vault=wired)


def test_prepare_monolingual_zh_reads_bilingual_blob(wired, monkeypatch):
    """中譯-only 書（has_original=False）→ 讀 bilingual.epub（中文）blob。"""
    seen = {}

    def _blob(book_id, *, lang):  # noqa: ANN001
        seen["lang"] = lang
        return _tiny_epub()

    monkeypatch.setattr(br, "read_book_blob", _blob)
    _make_book("zhonly", has_original=False)
    br.prepare_book_raw("zhonly", vault=wired)
    assert seen["lang"] == "bilingual"


def test_prepare_bilingual_reads_en_blob(wired, monkeypatch):
    """雙語書（has_original=True）→ 讀 EN original.epub blob（乾淨單語）。"""
    seen = {}

    def _blob(book_id, *, lang):  # noqa: ANN001
        seen["lang"] = lang
        return _tiny_epub()

    monkeypatch.setattr(br, "read_book_blob", _blob)
    _make_book("bi", has_original=True)
    br.prepare_book_raw("bi", vault=wired)
    assert seen["lang"] == "en"
