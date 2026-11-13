"""RETIRED (N521 / Centaur Literature 規格 §6 · D5).

``KB/Wiki/Sources/Books/{book_id}/notes.md`` 已退役。章末心得 (ReflectionV3) 現在
render 進統一的人讀 Literature Note ``KB/Literature/{slug}.md``，由
``shared/literature_writer.write_literature_note`` 產出。

本檔保留為退役樁：``write_notes`` 一律 raise，避免任何 caller 靜默回到舊路徑。
若你在找「把劃線/註解/心得寫成人讀檔」的入口，用 ``shared.literature_writer``。
"""

from __future__ import annotations

from typing import NoReturn


class RetiredWriterError(RuntimeError):
    """呼叫已退役的 book notes writer (N521)。"""


def write_notes(*_args, **_kwargs) -> NoReturn:  # noqa: ANN002, ANN003
    """RETIRED — 改用 ``shared.literature_writer.write_literature_note``。"""
    raise RetiredWriterError(
        "book_notes_writer.write_notes 已於 N521 退役；"
        "notes.md 由統一的 KB/Literature/{slug}.md 取代。"
        "改用 shared.literature_writer.write_literature_note(slug, source_kind='book')。"
    )
