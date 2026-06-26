"""Book ingest queue consumer (agents/robin/book_ingest.py) — route B Slice 4C.

Real ``book_queue`` + ``books`` table + ``shared.epub_text`` (synthetic EPUB);
``IngestPipeline`` and ``read_book_blob`` are stubbed so no LLM / real book files
are needed. ``book_ingest_queue`` FKs to ``books``, so each test inserts a real
Book row. DB isolation via the autouse ``isolated_db`` fixture.
"""

from __future__ import annotations

import io
import zipfile

import pytest

import agents.robin.book_ingest as bi
from shared import book_queue
from shared.book_storage import insert_book
from shared.schemas.books import Book
from shared.state import _get_conn


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


class _FakePipeline:
    """Captures ingest() calls instead of running the real LLM pipeline."""

    calls: list = []

    def ingest(self, raw_path, source_type, **kw):  # noqa: ANN001
        _FakePipeline.calls.append({"raw_path": raw_path, "source_type": source_type})


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    _FakePipeline.calls = []
    monkeypatch.setattr(bi, "IngestPipeline", _FakePipeline)
    monkeypatch.setattr(bi, "read_book_blob", lambda book_id, *, lang: _tiny_epub())
    return tmp_path


def _status(book_id: str):
    return (
        _get_conn()
        .execute("SELECT status, error FROM book_ingest_queue WHERE book_id = ?", (book_id,))
        .fetchone()
    )


def test_run_once_ingests_queued_book(wired):
    _make_book("bk1")
    book_queue.enqueue("bk1")
    assert bi.run_once() == "bk1"
    assert _status("bk1")["status"] == "ingested"

    assert len(_FakePipeline.calls) == 1
    call = _FakePipeline.calls[0]
    assert call["source_type"] == "book"
    assert "Raw" in str(call["raw_path"]) and "Books" in str(call["raw_path"])

    raw = call["raw_path"]
    assert raw.exists()
    content = raw.read_text(encoding="utf-8")
    assert "書本內容第一段" in content
    assert "title: 測試書" in content


def test_run_once_empty_queue_returns_none(wired):
    assert bi.run_once() is None
    assert _FakePipeline.calls == []


def test_run_once_marks_failed_on_bad_epub(wired, monkeypatch):
    monkeypatch.setattr(bi, "read_book_blob", lambda book_id, *, lang: b"not an epub")
    _make_book("bad1")
    book_queue.enqueue("bad1")
    assert bi.run_once() == "bad1"
    row = _status("bad1")
    assert row["status"] == "failed"
    assert row["error"]
    assert _FakePipeline.calls == []  # never reached the pipeline


def test_run_once_ingests_monolingual_zh_via_bilingual_blob(wired, monkeypatch):
    """中譯-only 書（has_original=False）→ 讀 bilingual.epub（中文）ingest，不再 fail。"""
    seen = {}

    def _blob(book_id, *, lang):  # noqa: ANN001
        seen["lang"] = lang
        return _tiny_epub()

    monkeypatch.setattr(bi, "read_book_blob", _blob)
    _make_book("zhonly", has_original=False)
    book_queue.enqueue("zhonly")
    assert bi.run_once() == "zhonly"
    assert _status("zhonly")["status"] == "ingested"
    assert seen["lang"] == "bilingual"  # 讀中文 blob，不是 en
    assert len(_FakePipeline.calls) == 1


def test_run_once_reads_en_original_when_has_original(wired, monkeypatch):
    """雙語書（has_original=True）→ 讀 EN original.epub（route B 既有行為不變）。"""
    seen = {}

    def _blob(book_id, *, lang):  # noqa: ANN001
        seen["lang"] = lang
        return _tiny_epub()

    monkeypatch.setattr(bi, "read_book_blob", _blob)
    _make_book("bilingual_bk", has_original=True)
    book_queue.enqueue("bilingual_bk")
    assert bi.run_once() == "bilingual_bk"
    assert seen["lang"] == "en"


def test_drain_processes_all_then_stops(wired):
    _make_book("a")
    _make_book("b")
    book_queue.enqueue("a")
    book_queue.enqueue("b")
    assert bi.drain() == 2
    assert bi.run_once() is None  # queue drained
