"""對外文案的 AI 腔禁用清單——標題與 YouTube 描述共用，規則只在這裡寫一次。

修修點名過、出現就整條重寫的寫法。這不是窮舉「好文案」的規則，只是**哨兵**：
LLM 評審看不出 AI 腔（同類模型寫、同類模型評——20261111 李惠貞的「離職前算的那筆帳」
在 panel 拿過 54/60），所以要有一道 deterministic 的硬關卡。

新增一條的時機：修修說「這是 AI slop」時，把那個寫法加進來並註明日期與原句。
"""

from __future__ import annotations

import re

#: (pattern, 修修什麼時候點名的)
AI_SLOP_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"不是[^。！？\n]{0,40}[，,、]?\s*而是"), "「不是…而是」AI 腔（描述，2026-09）"),
    (
        re.compile(r"不只[^。！？\n]{0,40}[，,、]?\s*更(?:是|要|能)?"),
        "「不只…更」AI 腔（描述，2026-09）",
    ),
    (re.compile(r"這一段會"), "「這一段會」導覽腔（描述，2026-09）"),
    (re.compile(r"帶你看"), "「帶你看」導覽腔（描述，2026-09）"),
    (re.compile(r"深入探討"), "「深入探討」（描述，2026-09）"),
    # 2026-11-13 20261111 李惠貞：「那筆帳」「算一筆帳」這種，全都是 AI slop，嚴禁。
    # 上一集「公司在算帳」也是同一族——被當成勝出範本一路抄下來。
    (
        re.compile(r"(?:那|這|一)筆帳|算(?:清楚)?(?:一筆)?帳"),
        "「那筆帳／算一筆帳／算帳」（修修 2026-11-13）",
    ),
)


def slop_hits(text: str) -> list[str]:
    """回傳命中的規則說明；乾淨回空 list。"""
    return [label for pattern, label in AI_SLOP_RULES if pattern.search(text)]
