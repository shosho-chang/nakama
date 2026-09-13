"""One streamed digest for every artifact this module measures.

同一支「分塊讀完、算 sha256」本來在四個地方各有一份：`_active_store`、
`_hyperframes_renderer`、`_materialization._file_sha256`、`_plan_record._measure`。
四份的分塊大小碰巧一樣，所以沒有人發現它們是同一支——直到有人要改其中一份。

`measure_file` 多回一個 bytes 數，因為量成品的那兩處同時要 size 與 digest，
而分開算等於把一份 1 GB 的檔讀兩次。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: 1 MiB。preview 大約 1 GB，整個讀進記憶體的代價是一次全檔配置。
_BLOCK_BYTES = 1 << 20


def measure_file(path: Path) -> tuple[int, str]:
    """This file's size and sha256, without holding it in memory."""

    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(_BLOCK_BYTES), b""):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def file_digest(path: Path) -> str:
    """This file's sha256."""

    return measure_file(path)[1]


__all__ = ["file_digest", "measure_file"]
