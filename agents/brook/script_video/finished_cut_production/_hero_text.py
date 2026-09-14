"""Hero 大字卡不能只是把字幕放大。

修修 2026-09-09 定版（`.claude/skills/longform-cut/SKILL.md`〈Hero 大字卡的驗收
標準〉）：「如果 hero title 沒有一個很強的 punchline 的話，那出來其實是沒意義的。」
三個測試裡的第三條是**不是複述**——「拿掉這張卡，觀眾會少掉什麼？答不出來就是
複述」。

那條規則確實有送到 Director 手上：`_codex_semantic._STAGE_SKILL_MANUALS` 會把
longform-cut 手冊全文接進 prompt，2026-09-14 實際送出的 prompt 有 57,277 字，
「沒有 punchline 就不要放」「不是複述」「觀眾會截圖的那一句」「拿得走嗎」四句
全在裡面。Director 讀了，然後照樣把字幕抄上去：

    punch-L02  我們DNA就不是這樣子設計的      ← 逐字，只少一個「啊」
    punch-L02  不會停止愛父母，只會停止愛自己  ← 兩句合併，只去掉「他」
    punch-L03  其實就是重播                    ← 逐字
    punch-L03  成為一個自在的我                ← 整句的後半

同一個症狀 2026-09-08 就記在 `_codex_semantic.py` 的註解裡（「Hero 只是把字幕放
大」），當時的處方是把手冊接進 prompt。接上去了，六天後原封不動再來一次。**光靠
prompt 層的指示守不住，所以這裡補一道機械閘。**

判準用「正規化後是不是逐字稿的連續子字串」，不是模糊相似度——拿修修判「留」的兩
張當負面對照就知道為什麼：

    一天六七千個念頭都在罵自己
        逐字稿：「一天大概有六七千個念頭…然後絕大部分都在批判自己」
        跨段濃縮（插入「大概有」）＋換詞（罵 vs 批判）→ 不是子字串 ✓ 放行
    被允許不做，才有力氣做
        逐字稿裡根本沒有這句 → 放行

模糊相似度會誤傷第一張（它跟原文共用大量字元）；子字串測試把六個真實案例全部
分對。

只擋 hero。章節卡與轉場卡本來就允許貼近原話，那是它們的職責。
"""

from __future__ import annotations

import unicodedata

# 語助詞。卡片與原話的差別若只有這些，就不算改寫。
_PARTICLES = frozenset("啊阿呀呢吧嘛喔噢哦欸耶唉嗯哈齁囉啦咧嘞哩呦唷")

# 第三人稱。手冊〈人稱〉那條講的正是這個情境：講者在轉述別人的故事，卡片把「他」
# 拿掉就變成一句通則——那是語法搬運，不是編輯工作。
# 第一/第二人稱刻意保留：手冊明訂卡片裡的「我」必須是講者本人，那個區別有意義，
# 不能在正規化時抹掉。
_THIRD_PERSON = frozenset("他她它牠們")


def normalize_for_verbatim(text: str) -> str:
    """抽掉不構成改寫的差異：標點、空白、語助詞、第三人稱。

    全形轉半形（NFKC）之後逐字過濾，只留下有語意負載的字元。
    """
    folded = unicodedata.normalize("NFKC", text)
    kept = []
    for char in folded:
        if char.isspace():
            continue
        category = unicodedata.category(char)
        # P* 標點、S* 符號一律不算差異（「，」「、」「！」「—」…）。
        if category.startswith(("P", "S")):
            continue
        if char in _PARTICLES or char in _THIRD_PERSON:
            continue
        kept.append(char)
    return "".join(kept)


def is_verbatim_quote(display: str, cue_text: str) -> bool:
    """卡片是否只是把它自己引用的那幾句逐字搬上來。

    `cue_text` 是 Director 宣告的 master cue 的逐字稿原文（`derive_anchor().text`）
    ——也就是這張卡自己說「我的證據是這幾句」的那幾句。拿別的段落比對沒有意義。
    """
    card = normalize_for_verbatim(display)
    source = normalize_for_verbatim(cue_text)
    if not card or not source:
        # 正規化後空白代表卡片只有標點或語助詞。那是另一種壞，不是複述，
        # 交給既有的 `display.strip()` 與可讀長度檢查去擋，這裡不假裝有意見。
        return False
    return card in source
