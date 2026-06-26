"""Book → KB/Raw text prep — shared by the synchronous ingest entry (`/start-book`).

Reads a book's EPUB blob → flattens to spine-ordered text (:mod:`shared.epub_text`)
→ writes ``KB/Raw/Books/{slug}.md`` with frontmatter. The web ingest flow then drives
the **same** ``/processing`` SSE pipeline articles/videos use (摘要 → 概念 → 寫入 →
開卡建議), so a book converges on the one ingest spine (ADR-043 Stage-3).

Which blob: a bilingual book (``has_original``) reads the EN ``original.epub`` — clean
single-language; bilingual EPUBs interleave EN+ZH and summarize poorly. A 中譯-only book
(mode ``monolingual-zh``, no original) reads its ``bilingual.epub`` blob, which for that
mode IS the Chinese text (修修 回饋 item 5).
"""

from __future__ import annotations

from pathlib import Path

from shared.book_storage import get_book, read_book_blob
from shared.config import get_vault_path
from shared.epub_text import EPUBTextError, extract_text
from shared.log import get_logger
from shared.utils import slugify

logger = get_logger("nakama.book_raw")

# Token-cost guard on extracted book text. The pipeline map-reduces large inputs,
# but an unbounded 500-page book is wasteful. ~240k chars ≈ a long non-fiction book.
_MAX_BOOK_CHARS = 240_000

# Where the flattened book text lands (KB/Raw is source material; mirrors
# Articles/ Papers/ Videos/). The ingest summarizer reads this file's frontmatter
# for title/author, then summarizes the body.
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


def prepare_book_raw(book_id: str, *, vault: Path | None = None) -> Path:
    """Read book ``book_id``'s EPUB → flatten → write ``KB/Raw/Books/{slug}.md``.

    Returns the raw_path (fed to the ingest summarizer as ``raw_path``). Raises
    ``LookupError`` if the book is unknown, ``EPUBTextError`` if it yields no text —
    the caller maps these to an HTTP error so the user sees why it didn't start.
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
    logger.info("book raw written: %s (%s) → %s", book_id, book.title, raw_path)
    return raw_path
