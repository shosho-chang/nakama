"""黏著度——**從這一集自己的逐字稿長出來的詞表**，取代永遠補不完的詞庫。

修修 2026-08-12：「如果需要建立詞庫的話，那永遠都修不完。」音檔靜音解決了
大部分（切點必須落在停頓上），但說話者整段連著講時音檔給不出答案——實測
「健身｜教練」「黑馬｜班」的靜音持續長度都是 **0 ms**。那時候需要一個判斷
「這兩個字是不是同一個詞」的訊號，而且不能是手維護的清單。

本模組的判準是**條件機率**，語料就是這一集的逐字稿本身：

    P(Y|X) = count(XY 出現在 cue 內部) / count(X)

「冒牌｜者」P(者|牌)=0.86——「牌」幾乎總是接「者」，它們是同一個詞。
「可是｜我」雖然同現 102 次，P(我|是) 只有 0.11——那是虛詞搭配不是詞。
實測校準（安吉 20260415）：真案例 0.69–0.86，假陽性全部 ≤0.17，中間空得很開。

**已知限制**：字元 bigram 對只出現兩三次的罕見詞太稀疏（「黑馬｜班」同現 1 次
就抓不到），那類要靠音檔靜音與封閉類詞素規則收。這裡不假裝它是銀彈。

英文另走一條路：中文靠統計，英文靠**這一集出現過的 token 集合**——
「tea」+「m」拼起來是語料裡的 `team` 就是切開了詞。精確，不需要機率。
"""

from __future__ import annotations

import re
from collections import Counter

MIN_N = 3  # 至少同現這麼多次才有統計意義
MIN_P = 0.60  # 條件機率門檻（真案例 0.69+，假陽性 ≤0.17）
_ASCII_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9']*")


def _is_word_char(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


class Cohesion:
    """一集的字元黏著度表 + 英文 token 集合。"""

    def __init__(self, cues: list, *, min_n: int = MIN_N, min_p: float = MIN_P):
        self.min_n, self.min_p = min_n, min_p
        self.uni: Counter = Counter()
        self.bi: Counter = Counter()
        self.ascii_tokens: set[str] = set()
        for cue in cues:
            text = cue[2] if isinstance(cue, (tuple, list)) else str(cue)
            self.ascii_tokens.update(m.group().lower() for m in _ASCII_TOKEN.finditer(text))
            # 只數 cue **內部**、且不跨空格的相鄰字——空格是 house style 的停頓
            # 標記，跨過去的相鄰不代表它們黏在一起
            for chunk in text.split():
                self.uni.update(chunk)
                self.bi.update(chunk[i : i + 2] for i in range(len(chunk) - 1))

    def splits_cjk(self, x: str, y: str) -> float | None:
        """x｜y 這一刀是不是切在詞中間？回傳條件機率（不是就回 None）。"""
        if not x or not y or _is_word_char(x) or _is_word_char(y):
            return None
        n = self.bi.get(x + y, 0)
        if n < self.min_n:
            return None
        p = max(n / max(self.uni.get(x, 1), 1), n / max(self.uni.get(y, 1), 1))
        return p if p >= self.min_p else None

    def splits_ascii_word(self, tail: str, head: str) -> str | None:
        """切點兩側都是英數字，且拼起來是本集出現過的英文詞 → 詞被切開。

        `boundary_reason` 對 ASCII 直接放行（jieba 會把整串英文當一個詞，
        跑詞跨界必誤報），於是英文完全沒有防線——2026-08-12 實測本模組加入前，
        重切把 `team` 切成 `tea`｜`m` 出貨。
        """
        if not tail or not head or not (_is_word_char(tail[-1]) and _is_word_char(head[0])):
            return None
        ra = _ASCII_TOKEN.search(tail[::-1])
        a = tail[-len(ra.group()) :] if ra and tail[-1].isalpha() else tail[-1]
        rb = _ASCII_TOKEN.match(head)
        b = rb.group() if rb else head[0]
        whole = (a + b).lower()
        return whole if whole in self.ascii_tokens else None

    def reason(self, tail: str, head: str) -> str | None:
        """給**現有切點**用的完整判定（候選過濾走 splits_cjk + 空格規則）。"""
        a, b = (tail or "").strip(), (head or "").strip()
        if not a or not b:
            return None
        word = self.splits_ascii_word(a, b)
        if word:
            return f"英文詞「{word}」被切開"
        p = self.splits_cjk(a[-1], b[0])
        if p is not None:
            return f"「{a[-1]}{b[0]}」在本集是同一個詞（黏著度 {p:.2f}）"
        return None
