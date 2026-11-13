"""N521 retirement guard — book_digest_writer.write_digest is retired (was #432).

``digest.md`` (逐條劃線 + 🔗 KB 相關 + 👍/👎) is replaced by the unified Literature
Note (KB/Literature/{slug}.md); pilot 期 🔗 KB 相關 純 FTS5 (D-17), 👍/👎 暫砍 (D-21).
"""

from __future__ import annotations

import pytest

digest_mod = pytest.importorskip("agents.robin.book_digest_writer")


def test_write_digest_is_retired():
    with pytest.raises(digest_mod.RetiredWriterError):
        digest_mod.write_digest("any-book")


def test_digest_report_type_preserved():
    """DigestReport dataclass kept for type-import back-compat."""
    report = digest_mod.DigestReport(book_id="x")
    assert report.book_id == "x"
