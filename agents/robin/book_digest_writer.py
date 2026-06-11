"""RETIRED (N521 / Centaur Literature 規格 §6 · D5).

``KB/Wiki/Sources/Books/{book_id}/digest.md`` 已退役。逐條劃線 + ``🔗 KB 相關``
撈卡現在 render 進統一的人讀 Literature Note ``KB/Literature/{slug}.md``，由
``shared/literature_writer.write_literature_note`` 產出 (pilot 期 ``🔗 KB 相關``
純 FTS5，D-17；👍/👎 回饋 D-21 暫砍)。

本檔保留為退役樁：``write_digest`` 一律 raise，避免任何 caller 靜默回到舊路徑。
``DigestReport`` dataclass 保留供既有 import 不爆 (型別參考用)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NoReturn


@dataclass
class DigestReport:
    """RETIRED — 舊 write_digest() 的回傳型別，保留供型別 import。"""

    book_id: str
    chapters_rendered: int = 0
    items_rendered: dict = field(default_factory=lambda: {"h": 0, "a": 0, "c": 0})
    hits_per_item_avg: float = 0.0
    render_duration_ms: int = 0
    errors: list[str] = field(default_factory=list)


class RetiredWriterError(RuntimeError):
    """呼叫已退役的 book digest writer (N521)。"""


def write_digest(*_args, **_kwargs) -> NoReturn:  # noqa: ANN002, ANN003
    """RETIRED — 改用 ``shared.literature_writer.write_literature_note``。"""
    raise RetiredWriterError(
        "book_digest_writer.write_digest 已於 N521 退役；"
        "digest.md 由統一的 KB/Literature/{slug}.md 取代。"
        "改用 shared.literature_writer.write_literature_note(slug, source_kind='book')。"
    )
