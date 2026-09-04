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

# 2026-09-03（20260901 蘇予昕）：3×2s ≈ 4s 的預算對大檔不夠。37.8 GB 的
# program feed 剛 ImportMedia 進來、Resolve 還在建索引時，主影片連兩次 build
# 都在這裡用盡重試而整支沒上軌。事後同一支檔案、同一個 DRT 模板、同一種
# append 寫法重測 3/3 全過——差別只有「Resolve 有沒有喘過氣」。
#
# ⚠️ 別把這種 timing 失敗誤判成素材或模板壞掉：當時的 A/B（不套模板成功、
# 套模板失敗）看起來像模板的鍋，其實只是兩次嘗試中間隔了時間。要下這種
# 結論必須重現，單次觀察不算。
DEFAULT_RETRIES = 6
DEFAULT_DELAY = 5.0


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


def delete_checked(project, timeline, items, label: str) -> None:
    """`Timeline.DeleteClips` 的安全包裝：先確保是 current timeline，失敗就 raise。

    ⛔ 第三個坑（2026-09-04，20260901 蘇予昕）：`DeleteClips` 在**非 current**
    timeline 上會靜默回 `False`——不是報錯，就只是不做事、也不告訴你為什麼。
    `project.SetCurrentTimeline(timeline)` 是免費的（已經是 current 時也能重覆呼叫），
    所以每次刪除前都先設定，不要假設呼叫端已經設過。

    這個函式只包 `Timeline.DeleteClips`（刪 timeline 上的 item）。`MediaPool.DeleteClips`
    （刪 media pool 裡的素材，跟目前開哪條 timeline 無關）是不同方法、不同物件，
    沒有已知證據顯示它有一樣的坑，不在這個包裝範圍內。
    """
    if not project.SetCurrentTimeline(timeline):
        raise SystemExit(f"{label}: SetCurrentTimeline 失敗，無法確保刪除目標是 current timeline")
    if not timeline.DeleteClips(items):
        raise SystemExit(f"{label}: DeleteClips 失敗（已確認是 current timeline，仍回 False）")
