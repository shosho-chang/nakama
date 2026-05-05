"""Minimal Zotero-schema SQLite fixture for ``ZoteroReader`` tests.

Seeds only the tables / fields ``ZoteroReader`` actually queries (not the full
Zotero schema). Reference: Zotero ``schema.sql`` ``items`` / ``itemTypes`` /
``itemData`` / ``itemDataValues`` / ``fields`` / ``itemAttachments``.

Public API is intentionally compositional — ``add_journal_article`` returns
the parent itemID, then any number of ``add_*_attachment`` calls hang
attachments off it. ``add_journal_article_with_html_snapshot`` is kept as a
single-call convenience for the most common test case.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

# itemType IDs (Zotero schema canonical values used in test fixtures only —
# ``ZoteroReader`` selects on ``contentType`` / ``parentItemID`` not typeID).
_JOURNAL_ARTICLE = 1
_ATTACHMENT = 14

# field IDs (Zotero schema canonical values).
_FIELD_TITLE = 110

_DEFAULT_HTML_BODY = "<html><body><h1>Test</h1><p>Body paragraph.</p></body></html>"
_DEFAULT_PDF_BODY = b"%PDF-1.4\n%fake fixture pdf bytes\n"


_CREATE_SQL = """
CREATE TABLE items (
    itemID INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    itemTypeID INTEGER NOT NULL
);
CREATE TABLE itemDataValues (
    valueID INTEGER PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE itemData (
    itemID INTEGER NOT NULL,
    fieldID INTEGER NOT NULL,
    valueID INTEGER NOT NULL,
    PRIMARY KEY (itemID, fieldID)
);
CREATE TABLE fields (
    fieldID INTEGER PRIMARY KEY,
    fieldName TEXT NOT NULL UNIQUE
);
CREATE TABLE itemAttachments (
    itemID INTEGER PRIMARY KEY,
    parentItemID INTEGER,
    contentType TEXT,
    path TEXT
);
"""


@dataclass(frozen=True)
class ZoteroFixture:
    """Returned by ``init_zotero_lib``. Owns layout 信息 for the test."""

    zotero_root: Path
    db_path: Path
    storage_dir: Path


def init_zotero_lib(zotero_root: Path) -> ZoteroFixture:
    """Build empty Zotero-style layout: ``zotero.sqlite`` + ``storage/``.

    Caller passes a tmp directory; this function creates the DB schema +
    seeds the ``fields`` lookup rows. Returns a :class:`ZoteroFixture`
    handle for downstream seeders.
    """
    zotero_root.mkdir(parents=True, exist_ok=True)
    storage_dir = zotero_root / "storage"
    storage_dir.mkdir(exist_ok=True)
    db_path = zotero_root / "zotero.sqlite"

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_CREATE_SQL)
        conn.executemany(
            "INSERT INTO fields (fieldID, fieldName) VALUES (?, ?)",
            [(_FIELD_TITLE, "title")],
        )
        conn.commit()
    finally:
        conn.close()

    return ZoteroFixture(zotero_root=zotero_root, db_path=db_path, storage_dir=storage_dir)


def add_journal_article(fixture: ZoteroFixture, *, item_key: str, title: str) -> int:
    """Insert one ``journalArticle`` item with title metadata.

    Returns the parent ``itemID`` so downstream ``add_*_attachment`` calls
    can hang attachments off it.
    """
    conn = sqlite3.connect(fixture.db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO items (key, itemTypeID) VALUES (?, ?)",
            (item_key, _JOURNAL_ARTICLE),
        )
        parent_item_id = cur.lastrowid

        cur.execute(
            "INSERT INTO itemDataValues (value) VALUES (?)",
            (title,),
        )
        title_value_id = cur.lastrowid
        cur.execute(
            "INSERT INTO itemData (itemID, fieldID, valueID) VALUES (?, ?, ?)",
            (parent_item_id, _FIELD_TITLE, title_value_id),
        )
        conn.commit()
        return parent_item_id
    finally:
        conn.close()


def add_html_snapshot(
    fixture: ZoteroFixture,
    *,
    parent_item_id: int,
    attachment_key: str,
    filename: str = "snapshot.html",
    body: str = _DEFAULT_HTML_BODY,
) -> None:
    """Hang a ``text/html`` snapshot attachment off ``parent_item_id``."""
    _add_attachment(
        fixture,
        parent_item_id=parent_item_id,
        attachment_key=attachment_key,
        content_type="text/html",
        filename=filename,
    )
    snapshot_dir = fixture.storage_dir / attachment_key
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / filename).write_text(body, encoding="utf-8")


def add_pdf_attachment(
    fixture: ZoteroFixture,
    *,
    parent_item_id: int,
    attachment_key: str,
    filename: str = "paper.pdf",
    body: bytes = _DEFAULT_PDF_BODY,
) -> None:
    """Hang an ``application/pdf`` attachment off ``parent_item_id``."""
    _add_attachment(
        fixture,
        parent_item_id=parent_item_id,
        attachment_key=attachment_key,
        content_type="application/pdf",
        filename=filename,
    )
    pdf_dir = fixture.storage_dir / attachment_key
    pdf_dir.mkdir(parents=True, exist_ok=True)
    (pdf_dir / filename).write_bytes(body)


def add_journal_article_with_html_snapshot(
    fixture: ZoteroFixture,
    *,
    item_key: str,
    attachment_key: str,
    title: str,
    snapshot_filename: str = "snapshot.html",
    snapshot_body: str = _DEFAULT_HTML_BODY,
) -> None:
    """Single-call convenience for the most common test case (HTML-only item)."""
    parent_item_id = add_journal_article(fixture, item_key=item_key, title=title)
    add_html_snapshot(
        fixture,
        parent_item_id=parent_item_id,
        attachment_key=attachment_key,
        filename=snapshot_filename,
        body=snapshot_body,
    )


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _add_attachment(
    fixture: ZoteroFixture,
    *,
    parent_item_id: int,
    attachment_key: str,
    content_type: str,
    filename: str,
) -> None:
    """Shared insertion path for ``itemAttachments``."""
    conn = sqlite3.connect(fixture.db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO items (key, itemTypeID) VALUES (?, ?)",
            (attachment_key, _ATTACHMENT),
        )
        attachment_item_id = cur.lastrowid
        cur.execute(
            "INSERT INTO itemAttachments (itemID, parentItemID, contentType, path) "
            "VALUES (?, ?, ?, ?)",
            (attachment_item_id, parent_item_id, content_type, f"storage:{filename}"),
        )
        conn.commit()
    finally:
        conn.close()
