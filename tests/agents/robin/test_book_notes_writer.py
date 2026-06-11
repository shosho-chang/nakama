"""N521 retirement guard — book_notes_writer.write_notes is retired.

``notes.md`` is replaced by the unified Literature Note (KB/Literature/{slug}.md).
The old write_notes contract tests (Slice 5B) are superseded; this file pins the
retirement so a future caller can't silently regress to the dead path.
"""

from __future__ import annotations

import pytest

writer_mod = pytest.importorskip("agents.robin.book_notes_writer")


def test_write_notes_is_retired():
    with pytest.raises(writer_mod.RetiredWriterError):
        writer_mod.write_notes("any-book", [])
