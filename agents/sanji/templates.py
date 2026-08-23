"""Template registry 載入與渲染（訊息治理的執行面）。

三鐵則：
1. Sanji 不自創文案——所有對外用詞來自 ``agents/sanji/templates/*.txt``（git 版控＝修修審核面）。
2. 變體選擇是**確定性**的（hash(user_id, day)）——重放/重試產生一字不差的同一句，
   跟 grant 的冪等性對齊。
3. 找不到模板檔＝設定錯誤，直接炸（fail-fast），不退回即席生成。
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "templates"


@lru_cache(maxsize=16)
def _pool(name: str) -> tuple[str, ...]:
    """讀變體池：`---` 單獨一行分隔變體；`#` 開頭的行是註解。"""
    path = _TEMPLATE_DIR / name
    raw = path.read_text(encoding="utf-8")

    variants: list[str] = []
    current: list[str] = []
    for line in raw.splitlines():
        if line.strip() == "---":
            if current:
                variants.append("\n".join(current).strip())
                current = []
            continue
        if line.lstrip().startswith("#"):
            continue
        current.append(line)
    if current and "".join(current).strip():
        variants.append("\n".join(current).strip())

    if not variants:
        raise RuntimeError(f"template pool empty: {path}")
    return tuple(variants)


def _pick(name: str, user_id: int, day: str) -> str:
    pool = _pool(name)
    digest = hashlib.sha256(f"{name}:{user_id}:{day}".encode()).digest()
    return pool[digest[0] % len(pool)]


def render_checkin_reply(
    *,
    user_id: int,
    day: str,
    xp: int,
    berry: int,
    streak: int,
    bonus_xp: int = 0,
    bonus_berry: int = 0,
    returned_after_gap: bool = False,
) -> str:
    """組打卡回覆：基礎行（或回歸行）＋（滿七時）連續獎行。"""
    pool_name = "welcome_back.txt" if returned_after_gap and streak == 1 else "checkin_approved.txt"
    text = _pick(pool_name, user_id, day).format(xp=xp, berry=berry, streak=streak)

    if bonus_xp > 0:
        bonus_line = _pool("streak_bonus.txt")[0].format(
            bonus_xp=bonus_xp, bonus_berry=bonus_berry, streak=streak
        )
        text = f"{text}\n{bonus_line}"

    return text
