"""從 WhisperX 字級時間戳重建 SRT cue — 取代「字數硬切 + 線性內插」。

修修 2026-07-25 驗收回饋：舊路徑（`transcriber._whisperx_to_srt`）把長 segment
按字數切、時間用字元位置線性內插，切出「先請/教老師」「從/哪裡」這種斷詞斷句。
本模組改從 align 後的**字級真實時間戳**建 cue：

1. 每個 ASR segment（VAD/句級邊界）內，jieba 斷詞 → 只在**詞邊界**切
2. 切點優先選**語音停頓最大**處（字間 gap），不是字數到了就切
3. cue 時間 = 首字 start / 末字 end（真實時間，零內插）
4. 字數軟上限 / 硬上限沿用 house style（14 / 22，對齊 shared/transcriber.py）

輸入吃 `whisperx.align()` 的 segments（每個含 word-level "words"）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MAX_CHARS = 14  # 軟上限（同 transcriber._MAX_SUBTITLE_CHARS）
HARD_MAX_CHARS = 22  # 硬上限（同 script_align）
PAUSE_FORCE_BREAK = 0.6  # 字間停頓 ≥ 此秒數 → 強制斷（自然句界）
MIN_CUE_CHARS = 4  # 停頓斷句的最短 cue（避免碎成單字）


def _tokenize(words: list[dict]) -> list[tuple[str, float, float]]:
    """words → (text, start, end) tuple 清單，丟掉空 token。"""
    out = []
    for w in words:
        text = (w.get("word") or "").strip()
        if not text or w.get("start") is None or w.get("end") is None:
            continue
        out.append((text, float(w["start"]), float(w["end"])))
    return out


def _jieba_spans(tokens: list[tuple[str, float, float]]) -> list[tuple[int, int]]:
    """jieba 斷詞 → 每個詞對應的 token index 範圍 [i, j)。

    中文 token 是單字、英文 token 是整詞；先串成字串記每字元屬於哪個
    token，jieba 切完映射回 token 邊界（詞絕不橫跨 cue）。
    """
    import jieba

    text = "".join(t[0] for t in tokens)
    char_to_token: list[int] = []
    for idx, (t, _, _) in enumerate(tokens):
        char_to_token.extend([idx] * len(t))

    spans: list[tuple[int, int]] = []
    pos = 0
    for word in jieba.cut(text):
        w_start, w_end = pos, pos + len(word)
        pos = w_end
        tok_start = char_to_token[w_start]
        tok_end = char_to_token[w_end - 1] + 1
        # 詞跨在同一 token 序列內取 token 邊界；相鄰同 token 詞合併範圍
        if spans and spans[-1][1] > tok_start:
            spans[-1] = (spans[-1][0], max(spans[-1][1], tok_end))
        else:
            spans.append((tok_start, tok_end))
    return spans


def segment_to_cues(
    words: list[dict],
    *,
    max_chars: int = MAX_CHARS,
    hard_max: int = HARD_MAX_CHARS,
    pause_break: float = PAUSE_FORCE_BREAK,
) -> list[tuple[float, float, str]]:
    """單一 segment 的字級 words → cue 列表 (start, end, text)。"""
    tokens = _tokenize(words)
    if not tokens:
        return []

    spans = _jieba_spans(tokens)

    def span_len(span: tuple[int, int]) -> int:
        return sum(len(tokens[i][0]) for i in range(span[0], span[1]))

    def gap_after(span: tuple[int, int]) -> float:
        """此詞之後的語音停頓（下一 token start − 本詞末 token end）。"""
        j = span[1]
        if j >= len(tokens):
            return float("inf")  # segment 結尾
        return max(0.0, tokens[j][1] - tokens[j - 1][2])

    import jieba

    def bad_boundary(j0: int) -> bool:
        """切在 j0 前會不會把詞切半：跨界二字在 jieba 詞典裡（如 對於）→ 壞切點。"""
        if j0 <= 0 or j0 >= len(tokens):
            return False
        left, right = tokens[j0 - 1][0], tokens[j0][0]
        if len(left) != 1 or len(right) != 1:
            return False  # ASCII 整詞不受影響
        return bool(jieba.dt.FREQ.get(left + right))

    # 括號深度：cue 絕不能在《…》「…」內部切開（書名/專有名詞完整性）
    _OPEN, _CLOSE = "《「", "》」"

    def depth_at(j0: int) -> int:
        d = 0
        for i in range(j0):
            for ch in tokens[i][0]:
                if ch in _OPEN:
                    d += 1
                elif ch in _CLOSE:
                    d = max(0, d - 1)
        return d

    cues: list[tuple[float, float, str]] = []
    cur: list[tuple[int, int]] = []
    cur_chars = 0

    def flush(*, avoid_bad_boundary: bool = False) -> None:
        nonlocal cur, cur_chars
        if not cur:
            return
        # 壞切點（詞被切半）→ 把最後 1–2 個詞退回下一個 cue
        carry: list[tuple[int, int]] = []
        if avoid_bad_boundary:
            while len(cur) > 1 and len(carry) < 2 and bad_boundary(cur[-1][1]):
                carry.insert(0, cur.pop())
        # 括號內部 → 繼續退詞直到切點在括號外（退光了就照切，硬上限保底）
        while len(cur) > 1 and depth_at(cur[-1][1]) > 0:
            carry.insert(0, cur.pop())
        i0, j0 = cur[0][0], cur[-1][1]
        text = _join_tokens(tokens, (i0, j0))
        cues.append((tokens[i0][1], tokens[j0 - 1][2], text))
        cur = carry
        cur_chars = sum(span_len(s) for s in cur)

    for k, span in enumerate(spans):
        w_len = span_len(span)
        # 塞進來會爆硬上限 → 先 flush 再收
        if cur and cur_chars + w_len > hard_max:
            flush(avoid_bad_boundary=True)
        cur.append(span)
        cur_chars += w_len

        pause = gap_after(span)
        in_bracket = depth_at(span[1]) > 0
        if pause >= pause_break and cur_chars >= MIN_CUE_CHARS and not in_bracket:
            flush()  # 自然停頓：最優先切點
            continue
        if cur_chars >= max_chars and not in_bracket:
            # 過了軟上限：往後看一小段，若近處有較大停頓就等它，否則現在切
            lookahead = spans[k + 1 : k + 4]
            ahead_chars = sum(span_len(s) for s in lookahead)
            pause_near = any(gap_after(s) >= 0.2 for s in lookahead)
            if not (pause_near and cur_chars + ahead_chars <= hard_max):
                flush(avoid_bad_boundary=True)
    flush()
    return cues


def _join_tokens(tokens: list[tuple[str, float, float]], span: tuple[int, int]) -> str:
    """組 cue 文字：CJK 相連不加空格，ASCII 詞與前後加半形空格；
    連續單一 ASCII 字母（縮寫如 A+I → AI）直接相連。"""
    parts: list[str] = []
    for i in range(span[0], span[1]):
        text = tokens[i][0]
        if parts and (text[0].isascii() or parts[-1][-1].isascii()):
            prev = parts[-1]
            if len(prev) == 1 and prev.isascii() and len(text) == 1 and text.isascii():
                pass  # 縮寫字母連寫
            else:
                parts.append(" ")
        parts.append(text)
    return "".join(parts)


def aligned_segments_to_srt(aligned_segments: list[dict], **kwargs) -> str:
    """whisperx.align() 的 segments → SRT 字串（真實字級時間戳）。"""
    all_cues: list[tuple[float, float, str]] = []
    for seg in aligned_segments:
        all_cues.extend(segment_to_cues(seg.get("words", []), **kwargs))

    lines: list[str] = []
    for seq, (start, end, text) in enumerate(all_cues, start=1):
        lines.append(f"{seq}\n{_ts(start)} --> {_ts(end)}\n{text}\n")
    return "\n".join(lines)


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
