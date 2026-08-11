"""Resolve `AppendToTimeline` 的安全包裝：判 `[None]` + 重試。

⛔ 兩個踩過的坑，合在這裡處理：

1. **失敗時回 `[None]` 而不是 falsy**（2026-08-04 util-L4 事故）——`[None]` 是
   truthy，`if not items` 判不出來，script 照報成功、timeline 卻是空的。

2. **時序性失敗**（2026-08-05 安吉集事故）——timeline 剛從 DRT 模板匯入 /
   剛建立時，Resolve 還沒就緒，第一次 append 必定回 `[None]`，隔一下再送就成功。
   當時 `build_resolve_project` 只判 `if not ok`，主影片整支沒上軌卻回報
   "created"，一路到緊湊化才炸出來（v1 空、a1/字幕都在）。

所以判斷要嚴、而且要重試——只判不重試會把可復原的時序問題變成硬失敗。
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("resolve_append")

DEFAULT_RETRIES = 3
DEFAULT_DELAY = 2.0


def _bad(items) -> bool:
    """append 結果是否為失敗。`[None]`／空 list／None 都算。"""
    if not items:
        return True
    if isinstance(items, list) and (len(items) == 0 or items[0] is None):
        return True
    return False


def append_checked(
    media_pool,
    specs,
    label: str,
    *,
    retries: int = DEFAULT_RETRIES,
    delay: float = DEFAULT_DELAY,
):
    """AppendToTimeline + 失敗重試；重試用盡才 raise（fail loud，絕不靜默跳過）。

    label 進錯誤訊息，讓失敗時看得出是哪一段上軌失敗。
    """
    items = None
    for attempt in range(1, retries + 1):
        items = media_pool.AppendToTimeline(specs)
        if not _bad(items):
            if attempt > 1:
                logger.info(f"{label}: 第 {attempt} 次 append 成功")
            return items
        if attempt < retries:
            logger.warning(
                f"{label}: append 回 {items!r}（Resolve 未就緒），{delay}s 後重試 "
                f"{attempt}/{retries - 1}"
            )
            time.sleep(delay)
    raise SystemExit(f"{label}: 上軌失敗，重試 {retries} 次仍回 {items!r}")
