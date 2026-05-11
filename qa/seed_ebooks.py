"""Seed two test ebooks into ``data/books/`` for the N518 ebook QA.

Mirrors ``tests/shared/test_books_path_alignment.py`` two_root_layout
fixture but writes to the worktree's actual ``data/books/`` (cwd-relative
default of ``book_storage._DEFAULT_BOOKS_DIR``) so the running uvicorn
sees the seeded entries via the production wiring path.

Two books cover both ``has_original`` branches the registry treats
differently:

- ``qa-bilingual-only`` — bilingual.epub only (Robin's URL ingest flow)
- ``qa-bilingual-and-original`` — bilingual.epub + original.epub
  (PDF/EPUB upload flow)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Adjust path so this script can be run from anywhere.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared import book_storage  # noqa: E402
from shared.schemas.books import Book  # noqa: E402
from shared.state import _get_conn, _init_tables  # noqa: E402
from tests.shared._epub_fixtures import (  # noqa: E402
    EPUBSpec,
    make_epub_blob,
)

# Build a book with 5 chapters × ~100 words each ≈ 500 words total — passes
# the 200-word ``very_short`` cutoff and the 5-entry TOC heuristic so
# preflight returns ``proceed_full_promotion`` and the source surfaces in
# the list view. The body is repetitive Lorem-style filler purely to clear
# the threshold; we don't care about content quality for wiring QA.
_FILLER = (
    "The discipline of evidence-based reading promotion balances signal "
    "and noise. Each chapter contributes representative claims that the "
    "downstream concept matcher will surface as candidates. The promotion "
    "review surface exists to keep humans in the loop on what enters the "
    "global concept layer of the knowledge base. " * 4
)


def _chapter_xhtml(idx: int) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter {idx}</title></head>
<body>
<h1>Chapter {idx}: QA Filler</h1>
<p>{_FILLER}</p>
</body>
</html>
"""


def _nav_xhtml(n_chapters: int) -> str:
    items = "\n    ".join(
        f'<li><a href="ch{i}.xhtml">Chapter {i}</a></li>' for i in range(1, n_chapters + 1)
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body>
<nav epub:type="toc" id="toc">
  <h1>Contents</h1>
  <ol>
    {items}
  </ol>
</nav>
</body>
</html>
"""


def _make_long_epub(*, title: str, creator: str, n_chapters: int = 5) -> bytes:
    chapters = {f"ch{i}.xhtml": _chapter_xhtml(i) for i in range(1, n_chapters + 1)}
    spec = EPUBSpec(
        title=title,
        creator=creator,
        language="en",
        chapters=chapters,
        nav_xhtml=_nav_xhtml(n_chapters),
    )
    return make_epub_blob(spec)


def _book_row(book_id: str, *, title: str, has_original: bool) -> Book:
    return Book(
        book_id=book_id,
        title=title,
        author="QA Seed",
        lang_pair="en→zh-Hant",
        genre=None,
        isbn=None,
        published_year=None,
        has_original=has_original,
        book_version_hash="q" * 64,
        created_at="2026-05-10T20:00:00+08:00",
    )


def main() -> None:
    print(f"books_root() = {book_storage.books_root().resolve()}")

    # Provision SQLite tables (in case state.db doesn't exist yet).
    with _get_conn() as conn:
        _init_tables(conn)

    # Book 1: bilingual only
    bid1 = "qa-bilingual-only"
    bilingual_only = _make_long_epub(title="QA Bilingual Only", creator="QA Author")
    book_storage.store_book_files(bid1, bilingual=bilingual_only)
    try:
        book_storage.insert_book(_book_row(bid1, title="QA Bilingual Only", has_original=False))
        print(f"  inserted {bid1}")
    except Exception as exc:
        print(f"  insert_book({bid1}) raised: {exc} (already seeded?)")

    # Book 2: bilingual + original
    bid2 = "qa-bilingual-and-original"
    bilingual_two = _make_long_epub(title="QA Bilingual+Original", creator="QA Author")
    original_two = _make_long_epub(title="QA Bilingual+Original (Original)", creator="QA Author")
    book_storage.store_book_files(bid2, bilingual=bilingual_two, original=original_two)
    try:
        book_storage.insert_book(_book_row(bid2, title="QA Bilingual+Original", has_original=True))
        print(f"  inserted {bid2}")
    except Exception as exc:
        print(f"  insert_book({bid2}) raised: {exc} (already seeded?)")

    print("Seed complete. Both books should now surface in /promotion-review/.")


if __name__ == "__main__":
    main()
