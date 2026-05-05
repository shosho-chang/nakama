"""Tests for agents/robin/zotero_reader.py.

Slice 1 #389 — TDD on the SQLite-backed Zotero reader.

Per ADR-018, sync agent reads ``~/Zotero/zotero.sqlite`` directly (not Web API)
and selects HTML snapshot preferred / PDF fallback. This test file exercises:

- URI parsing
- Item lookup + attachment selection
- SQLite copy-to-tmp lock-safe pattern
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.robin.zotero_reader import (
    NoAttachmentError,
    ZoteroReader,
    parse_zotero_uri,
)
from tests.agents.robin._zotero_fixture import (
    add_html_snapshot,
    add_journal_article,
    add_journal_article_with_html_snapshot,
    add_pdf_attachment,
    init_zotero_lib,
)

# ---------------------------------------------------------------------------
# parse_zotero_uri
# ---------------------------------------------------------------------------


def test_parse_zotero_uri_extracts_item_key():
    """Tracer bullet — happy path URI returns the bare 8-char itemKey."""
    assert parse_zotero_uri("zotero://select/library/items/ABC12345") == "ABC12345"


@pytest.mark.parametrize(
    "uri",
    [
        "",  # empty
        "https://example.com/paper",  # plain web URL
        "zotero://collection/library/Foo",  # wrong path
        "zotero://select/library/items/",  # missing key
        "zotero://select/groups/123/items/ABC12345",  # group library (out of MVP scope)
        "zotero://select/library/items/abc12345 extra",  # trailing garbage
    ],
)
def test_parse_zotero_uri_returns_none_for_invalid(uri: str):
    """Anything that isn't a personal-library item-select URI is rejected."""
    assert parse_zotero_uri(uri) is None


# ---------------------------------------------------------------------------
# ZoteroReader.get_item — HTML snapshot happy path
# ---------------------------------------------------------------------------


def test_get_item_returns_html_snapshot_attachment(tmp_path: Path):
    """Reader returns a ZoteroItem whose chosen attachment is the HTML snapshot.

    Fixture: one journalArticle item with a single text/html attachment.
    Asserts: title comes from itemData; attachment_path is the on-disk
    ``storage/{attachment_key}/snapshot.html`` file; attachment_type is
    ``text/html``.
    """
    fixture = init_zotero_lib(tmp_path / "Zotero")
    add_journal_article_with_html_snapshot(
        fixture,
        item_key="ABC12345",
        attachment_key="XYZ98765",
        title="A Sample Paper on Mitochondria",
    )

    reader = ZoteroReader(fixture.zotero_root)
    item = reader.get_item("ABC12345")

    assert item.item_key == "ABC12345"
    assert item.title == "A Sample Paper on Mitochondria"
    assert item.attachment_type == "text/html"
    assert item.attachment_path == fixture.storage_dir / "XYZ98765" / "snapshot.html"
    assert item.attachment_path.exists()


def test_get_item_prefers_html_when_both_html_and_pdf_present(tmp_path: Path):
    """Q1 lock — HTML snapshot wins over PDF when both attached."""
    fixture = init_zotero_lib(tmp_path / "Zotero")
    parent_id = add_journal_article(fixture, item_key="ABC12345", title="Mixed Item")
    add_pdf_attachment(fixture, parent_item_id=parent_id, attachment_key="PDF00001")
    add_html_snapshot(fixture, parent_item_id=parent_id, attachment_key="HTML0001")

    item = ZoteroReader(fixture.zotero_root).get_item("ABC12345")

    assert item.attachment_type == "text/html"
    assert item.attachment_path == fixture.storage_dir / "HTML0001" / "snapshot.html"


def test_get_item_falls_back_to_pdf_when_no_html_snapshot(tmp_path: Path):
    """Q8 — PDF-only items (e.g. arXiv preprints) walk the PDF path.

    Slice 1 records the selection in ZoteroItem; the actual pymupdf4llm
    extraction is Slice 2.
    """
    fixture = init_zotero_lib(tmp_path / "Zotero")
    parent_id = add_journal_article(fixture, item_key="ABC12345", title="Preprint")
    add_pdf_attachment(fixture, parent_item_id=parent_id, attachment_key="PDF00001")

    item = ZoteroReader(fixture.zotero_root).get_item("ABC12345")

    assert item.attachment_type == "application/pdf"
    assert item.attachment_path == fixture.storage_dir / "PDF00001" / "paper.pdf"


def test_get_item_raises_no_attachment_error_when_metadata_only(tmp_path: Path):
    """Metadata-only item (no HTML, no PDF) → NoAttachmentError sentinel."""
    fixture = init_zotero_lib(tmp_path / "Zotero")
    add_journal_article(fixture, item_key="ABC12345", title="Citation only")

    with pytest.raises(NoAttachmentError):
        ZoteroReader(fixture.zotero_root).get_item("ABC12345")


def test_get_item_raises_key_error_for_unknown_item_key(tmp_path: Path):
    """Item key not in the library → KeyError (caller can distinguish from
    NoAttachmentError, which means item exists but has no usable attachment)."""
    fixture = init_zotero_lib(tmp_path / "Zotero")

    with pytest.raises(KeyError):
        ZoteroReader(fixture.zotero_root).get_item("UNKNOWN1")
