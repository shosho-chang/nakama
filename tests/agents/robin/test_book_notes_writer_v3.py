"""N521 retirement guard — book_notes_writer is retired (was ADR-024 Slice 2 T-N6).

The V3 reflection → notes.md duck-typing path is superseded by the unified
Literature Note (KB/Literature/{slug}.md); reflections now render as 章末心得 there.
"""

from __future__ import annotations

import pytest

writer_mod = pytest.importorskip("agents.robin.book_notes_writer")


def test_write_notes_v3_path_is_retired():
    with pytest.raises(writer_mod.RetiredWriterError):
        writer_mod.write_notes("v3-book", [])
