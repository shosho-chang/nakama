"""N521 retirement guard — book_digest_writer is retired (was ADR-024 Slice 2 T-N5).

The V3 digest duck-typing path is superseded by the unified Literature Note
(KB/Literature/{slug}.md) rendered from the V3 annotation set.
"""

from __future__ import annotations

import pytest

digest_mod = pytest.importorskip("agents.robin.book_digest_writer")


def test_write_digest_v3_path_is_retired():
    with pytest.raises(digest_mod.RetiredWriterError):
        digest_mod.write_digest("test-v3-book")
