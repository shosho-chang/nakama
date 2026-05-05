"""SQLite-backed reader for the local Zotero library (ADR-018, Slice 1 #389).

Public surface (TDD-driven, grow as tests demand):

- ``parse_zotero_uri(uri)`` — extract itemKey from ``zotero://select/library/items/{key}`` URIs.
- ``ZoteroItem`` — value object: metadata + selected primary attachment.
- ``ZoteroReader(zotero_root)`` — open library; ``get_item(key)`` returns a ``ZoteroItem``.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal

_ZOTERO_URI_RE = re.compile(r"^zotero://select/library/items/([A-Z0-9]+)$")


def parse_zotero_uri(uri: str) -> str | None:
    """Extract ``itemKey`` from a ``zotero://select/library/items/{key}`` URI.

    Returns the key on a valid URI, or ``None`` for any other input.
    """
    match = _ZOTERO_URI_RE.match(uri)
    return match.group(1) if match else None


_AttachmentType = Literal["text/html", "application/pdf"]


@dataclass(frozen=True)
class ZoteroItem:
    """Metadata + selected primary attachment for a single Zotero item.

    The chosen attachment is ``text/html`` snapshot if present, else
    ``application/pdf`` (Slice 2). Other Zotero metadata fields (DOI,
    authors, publication, date) are added in subsequent TDD cycles.
    """

    item_key: str
    title: str
    attachment_path: Path
    attachment_type: _AttachmentType
    doi: str | None = None
    publication: str | None = None
    date: str | None = None
    authors: list[str] = field(default_factory=list)


class NoAttachmentError(Exception):
    """Item exists in the library but has neither an HTML snapshot nor a PDF
    attachment usable for sync. Caller should write a ``no_attachment``
    placeholder to inbox so the failure is visible in the Inbox row UI.
    """


class ZoteroReader:
    """SQLite-backed reader for the local Zotero library.

    ``zotero_root`` is the directory containing ``zotero.sqlite`` and
    ``storage/`` (default ``~/Zotero/`` on Mac, ``%USERPROFILE%\\Zotero\\``
    on Windows). All reads go through a tmp-file copy so a running Zotero
    desktop's exclusive lock does not block sync.
    """

    def __init__(self, zotero_root: Path) -> None:
        self._zotero_root = zotero_root

    @property
    def _db_path(self) -> Path:
        return self._zotero_root / "zotero.sqlite"

    @property
    def _storage_dir(self) -> Path:
        return self._zotero_root / "storage"

    def get_item(self, item_key: str) -> ZoteroItem:
        """Return the ``ZoteroItem`` matching ``item_key`` in the local library.

        Selects the primary attachment in order: HTML snapshot → PDF.

        Raises:
            KeyError: ``item_key`` not present in the library.
            NoAttachmentError: item exists but has neither HTML snapshot nor PDF.
        """
        with self._open_readonly_copy() as conn:
            cur = conn.cursor()

            # Resolve parent itemID from public key.
            cur.execute("SELECT itemID FROM items WHERE key = ?", (item_key,))
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"Zotero item not found: {item_key}")
            parent_item_id = row[0]

            # Title via itemData → itemDataValues join.
            cur.execute(
                """
                SELECT v.value
                FROM itemData d
                JOIN itemDataValues v ON v.valueID = d.valueID
                JOIN fields f ON f.fieldID = d.fieldID
                WHERE d.itemID = ? AND f.fieldName = 'title'
                """,
                (parent_item_id,),
            )
            title_row = cur.fetchone()
            title = title_row[0] if title_row else ""

            attachment = self._pick_primary_attachment(cur, parent_item_id)
            if attachment is None:
                raise NoAttachmentError(
                    f"item {item_key} has neither HTML snapshot nor PDF attachment"
                )
            attachment_key, content_type, filename = attachment

        return ZoteroItem(
            item_key=item_key,
            title=title,
            attachment_path=self._storage_dir / attachment_key / filename,
            attachment_type=content_type,  # type: ignore[arg-type]
        )

    @staticmethod
    def _pick_primary_attachment(
        cur: sqlite3.Cursor, parent_item_id: int
    ) -> tuple[str, str, str] | None:
        """Return ``(attachment_key, content_type, filename)`` for the chosen
        attachment, or ``None`` if no usable one exists.

        HTML snapshot wins over PDF; other content types are ignored.
        """
        cur.execute(
            """
            SELECT i.key, ia.contentType, ia.path
            FROM itemAttachments ia
            JOIN items i ON i.itemID = ia.itemID
            WHERE ia.parentItemID = ?
              AND ia.contentType IN ('text/html', 'application/pdf')
            """,
            (parent_item_id,),
        )
        rows = cur.fetchall()
        if not rows:
            return None

        # Prefer HTML.
        for key, content_type, path in rows:
            if content_type == "text/html":
                return key, content_type, path.removeprefix("storage:")

        # PDF fallback.
        for key, content_type, path in rows:
            if content_type == "application/pdf":
                return key, content_type, path.removeprefix("storage:")

        return None  # unreachable: IN clause limits to the two types above

    @contextmanager
    def _open_readonly_copy(self) -> Iterator[sqlite3.Connection]:
        """Copy ``zotero.sqlite`` to a tmp file and yield a connection.

        Bypasses the exclusive lock Zotero desktop holds while running.
        """
        with tempfile.TemporaryDirectory(prefix="nakama-zotero-") as tmp_dir:
            tmp_db = Path(tmp_dir) / "zotero.sqlite"
            shutil.copy2(self._db_path, tmp_db)
            conn = sqlite3.connect(tmp_db)
            try:
                yield conn
            finally:
                conn.close()
