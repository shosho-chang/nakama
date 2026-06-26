"""Book ingest queue consumer — route B (Slice 4C).

Drains ``book_ingest_queue``: for each queued book, read its EPUB → flatten to
spine-ordered text (``shared.epub_text``) → run the **same** ``IngestPipeline``
articles/videos use (Source summary + concept/entity extraction → KB/Wiki).
The queue is populated by the Reader's「Ingest 整本書」button
(``POST /robin/api/books/{id}/ingest-request`` → ``book_queue.enqueue``).

Which blob: a bilingual book (``has_original``) ingests the EN ``original.epub`` —
clean single-language; bilingual EPUBs interleave EN+ZH and summarize poorly. A
中譯-only book (mode ``monolingual-zh``, no original) ingests its ``bilingual.epub``
blob, which for that mode IS the Chinese text. Chinese-KB concept extraction reads
either the same way it would an English article (修修 回饋 item 5).

Run:  ``python -m agents.robin --mode book_ingest``   # drain the queue once
"""

from __future__ import annotations

from pathlib import Path

from agents.robin.ingest import IngestPipeline
from shared.book_queue import mark_status, next_queued
from shared.book_storage import get_book, read_book_blob
from shared.config import get_vault_path
from shared.epub_text import EPUBTextError, extract_text
from shared.log import get_logger
from shared.utils import slugify

logger = get_logger("nakama.book_ingest")

# Token-cost guard on extracted book text. The pipeline map-reduces large inputs,
# but an unbounded 500-page book is wasteful. ~240k chars ≈ a long non-fiction book.
_MAX_BOOK_CHARS = 240_000

# Where the flattened book text lands (KB/Raw is source material; mirrors
# Articles/ Papers/ Videos/). IngestPipeline reads this file's frontmatter for
# title/author, then summarizes the body.
_RAW_SUBDIR = ("KB", "Raw", "Books")


def _write_raw(vault: Path, slug: str, title: str, author: str, text: str) -> Path:
    """Write flattened book text → KB/Raw/Books/{slug}.md with frontmatter."""
    raw_dir = vault.joinpath(*_RAW_SUBDIR)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{slug}.md"
    fm = ["---", f"title: {title}"]
    if author:
        fm.append(f"author: {author}")
    fm += ["source_type: book", "---", ""]
    path.write_text("\n".join(fm) + text, encoding="utf-8")
    return path


def ingest_one(book_id: str, *, vault: Path | None = None) -> None:
    """Read book ``book_id``'s EPUB → flatten → run ``IngestPipeline`` (source_type=book).

    A bilingual book reads its EN ``original.epub``; a monolingual-zh 中譯本 (no
    original) reads its ``bilingual.epub`` blob, which for that mode is the Chinese
    text. Raises on failure so the caller can mark the queue row ``failed``.
    """
    vault = vault or get_vault_path()
    book = get_book(book_id)
    if book is None:
        raise LookupError(f"book {book_id!r} not in books table")

    lang = "en" if book.has_original else "bilingual"
    blob = read_book_blob(book_id, lang=lang)
    text = extract_text(blob, max_chars=_MAX_BOOK_CHARS)
    if not text.strip():
        raise EPUBTextError(f"book {book_id!r} produced no extractable text")

    slug = slugify(book.title) or book_id
    raw_path = _write_raw(vault, slug, book.title, book.author or "", text)
    IngestPipeline().ingest(raw_path, source_type="book")
    logger.info("book ingested: %s (%s)", book_id, book.title)


def run_once() -> str | None:
    """Process the oldest queued book. Returns its ``book_id``, or ``None`` if the
    queue is empty. Marks the row ``ingesting`` → ``ingested`` / ``failed``."""
    book_id = next_queued()
    if not book_id:
        return None
    mark_status(book_id, "ingesting")
    try:
        ingest_one(book_id)
    except Exception as exc:  # noqa: BLE001 — record on the row, keep draining the rest
        logger.exception("book ingest failed: %s", book_id)
        mark_status(book_id, "failed", error=f"{type(exc).__name__}: {exc}"[:300])
        return book_id
    mark_status(book_id, "ingested")
    return book_id


def drain(*, max_books: int = 20) -> int:
    """Process queued books until the queue is empty (or ``max_books``). Returns count."""
    n = 0
    while n < max_books:
        if run_once() is None:
            break
        n += 1
    return n
