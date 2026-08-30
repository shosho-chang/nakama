"""中文顯示層斷行／斷句 — 字卡與字幕共用同一套「乾淨切點」判準。

字幕的斷點判準已經在 `shared.subtitle_finalize.boundary_reason` 裡（七規則、
一路是血淚累積出來的）；**字卡不該另外發明一套**。本模組把它接過來用，只補
上「同一句話裡有好幾個不會壞的切點時，選哪一個」這一層。

## 兩種切點不是同一回事

- **斷句（card）**：切出來的兩段**先後**出現，觀眾看完前段才看到後段。
  「所以我覺得｜接下來的時代」在這裡是壞切點——賓語子句被丟到下一張卡，
  前一張看起來話沒講完。判準用 `boundary_reason` 全套。
- **斷行（line）**：同一張卡的兩行**同時**在畫面上，觀眾一眼讀完。這裡只要
  詞不被剖開、行首行尾不掛虛字就好；沿用斷句那套會誤殺到只剩「所以」孤零零
  一行。判準只取「詞完整 ＋ 黏著字 ＋ 括號」那幾條。

## 選點不只看均衡

最均衡的切點常常是最爛的那個——「當我們把玩這件｜事情排除在學習之外」7/9
最均衡，卻把「這件事情」剖成兩半。所以再加一層語法偏好：**右段以謂語起手**
（動詞／副詞／介詞／連接詞）代表切在子句接縫上，**左段以名詞收尾**代表左邊
自己站得住。單字補語（到／著／完／起來…）開頭一律重罰——那是動詞的尾巴，
不是新的開始，而且 `boundary_reason` 抓不到它（「用到」不是 jieba 詞）。
"""

from __future__ import annotations

from typing import Literal

from shared.cue_builder import jieba_boundaries
from shared.subtitle_finalize import (
    _HEAD_STICKY,
    _TAIL_STICKY,
    CLOSE_BRACKETS,
    MODAL_TAIL_WORDS,
    OPEN_BRACKETS,
    PREDICATE_HEAD_FLAGS,
    boundary_reason,
)

BreakMode = Literal["card", "line"]

#: 左段結尾「自己站得住」的詞性：名詞類、獨立動詞、習語、簡稱。
#: 代名詞（r）與數量詞（m/q）刻意不收——「這件」「一個」收尾就是拆到一半。
_COMPLETE_TAIL_FLAGS = frozenset({"n", "nr", "ns", "nt", "nz", "ng", "v", "vn", "l", "i", "j"})

#: 動補結構的補語：接在動詞後面，單獨當右段開頭＝把動詞攔腰砍
#: （「使用｜到的肌肉」）。
_COMPLEMENT_HEADS = frozenset("到著了過得掉住完成起出來去上下開")

#: 輕動詞／繫詞：詞性是動詞（拿得到 +1）但單獨收尾等於話沒講完
#: （「甚至覺得玩是｜很邪惡的事情」）。`_TAIL_STICKY` 已擋掉的不重複列。
_LIGHT_VERB_TAIL = frozenset({"是", "有", "去", "給", "做", "用", "來"})


def _posseg(text: str):
    import jieba.posseg as posseg

    from shared.transcriber import ensure_tw_jieba

    ensure_tw_jieba()
    return list(posseg.cut(text, HMM=False))


def _line_break_ok(left: str, right: str) -> bool:
    """同框兩行之間的切點：只管詞完整、黏著字、括號。"""
    if not left or not right:
        return False
    if left[-1] in OPEN_BRACKETS or right[0] in CLOSE_BRACKETS:
        return False
    return right[0] not in _HEAD_STICKY and left[-1] not in _TAIL_STICKY


def clean_breaks(text: str, mode: BreakMode = "card") -> list[int]:
    """所有「切了不會壞」的 char 位置（不含 0 與 len）。"""
    out = []
    for i in sorted(jieba_boundaries(text)):
        if not 0 < i < len(text):
            continue
        left, right = text[:i], text[i:]
        ok = _line_break_ok(left, right) if mode == "line" else boundary_reason(left, right) is None
        if ok:
            out.append(i)
    return out


def break_score(text: str, i: int) -> float:
    """切點語法品質；越高越好。均衡度是最弱的一項，不是主判準。"""
    left, right = text[:i], text[i:]
    score = 0.0
    head = _posseg(right)[0] if right else None
    tail = _posseg(left)[-1] if left else None
    if head is not None:
        if len(head.word) == 1 and head.word in _COMPLEMENT_HEADS:
            score -= 3.0
        elif head.flag.startswith("v") or head.flag in PREDICATE_HEAD_FLAGS:
            score += 2.0
    if tail is not None:
        if tail.flag in _COMPLETE_TAIL_FLAGS:
            score += 1.0
        # 助動詞／連接副詞收尾＝主要動詞在下一行（subtitle_finalize 的同一條
        # 真值，斷行版只扣分不否決——同框兩行讀得到後半，沒有斷句那麼致命）
        if tail.word in MODAL_TAIL_WORDS:
            score -= 2.5
        elif tail.word in _LIGHT_VERB_TAIL:
            score -= 1.5
        elif tail.flag == "r" and len(tail.word) == 1:
            score -= 1.0  # 主語孤懸在行尾（「所以我｜覺得…」）
    return score - abs(len(left) - len(right)) / len(text)


def _best_break(text: str, candidates: list[int]) -> int | None:
    if not candidates:
        return None
    return max(candidates, key=lambda i: (break_score(text, i), -abs(2 * i - len(text))))


def wrap_lines(text: str, limit: int, max_lines: int = 2) -> list[str] | None:
    """排成 1–max_lines 行、每行 ≤ limit 字；排不下回 None。

    ⚠️ 不做「排不下就降級成別的樣式」——那是把版面問題推給樣式，
    正解是回上游把過長的子句拆開（`split_clause`）。
    """
    if len(text) <= limit:
        return [text]
    if max_lines < 2:
        return None
    fits = [i for i in clean_breaks(text, "line") if i <= limit]
    for cut in (fits, [i for i in sorted(jieba_boundaries(text)) if 0 < i <= limit]):
        for i in sorted(cut, key=lambda i: -break_score(text, i)):
            rest = wrap_lines(text[i:], limit, max_lines - 1)
            if rest is not None:
                return [text[:i], *rest]
    return None


def split_clause(text: str, limit: int, max_lines: int = 2) -> list[str]:
    """把排不進 limit×max_lines 的子句，在語法接縫處拆成數段（逐字承接）。

    回傳的每一段都保證 `wrap_lines` 排得下；真的找不到切點時原樣回傳
    （讓下游的版面驗證去爆，不要在這裡安靜地生出爛版面）。
    """
    if wrap_lines(text, limit, max_lines) is not None:
        return [text]
    i = _best_break(text, clean_breaks(text, "card")) or _best_break(
        text, [b for b in sorted(jieba_boundaries(text)) if 0 < b < len(text)]
    )
    if i is None:
        return [text]
    return split_clause(text[:i], limit, max_lines) + split_clause(text[i:], limit, max_lines)
