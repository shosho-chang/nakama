"""社群直播字幕的顯示層：release.srt → 燒進影片用的 display.srt。

Memo Dual-Audit 的 ``release.srt`` 時間軸＝原始 Zoom 錄影，文字保留 Memo 原本的標點；
podcast 在 DaVinci 裡做的顯示層處理，直播不進 DaVinci，所以在這裡用固定程式做（2026-11-20）：

1. 拿掉靜音幻覺：指定的時間區段（例如中場休息）整段丟掉、純括號標註的 cue 丟掉，
   cue 裡夾的括號標註（``(音量注意)``）刪掉。
2. 簡轉繁（OpenCC s2twp＋既有的過度轉換修正，``shared.transcriber._to_traditional``）。
3. 遲疑語助詞（``shared.subtitle_finalize.strip_fillers``）；整條只剩語助詞就丟掉。
4. 標點 house style：只留《》「」，句中標點換成半形空白、句尾標點刪掉；英數之間的
   ``.`` ``%`` ``&`` ``+`` ``/`` ``-`` 這類屬於詞本身的符號保留（3.5、Q&A、50%）。
5. 半形英數與漢字之間補空白（``space_han_latin``）。
6. 剪頭尾：只留 ``[start, end)`` 範圍內的 cue，時間平移到 0 起點、重新編號。
7. 自動換行：超過 ``max_line_chars`` 的 cue 拆成多行（**不拆 cue、不動時間**——Memo 只有句級
   時間戳，拆 cue 就得內插時間），斷點優先選空白（原本的標點位置），其次是 jieba 詞邊界，
   《》「」裡面不斷。
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

from shared.subtitle_finalize import filler_only, format_srt, parse_srt_text, space_han_latin
from shared.subtitle_finalize import strip_fillers as _strip_fillers

KEEP_MARKS = "《》「」"
#: 屬於詞本身、夾在英數之間才保留的符號
_WORD_JOINERS = ".%&+/-'"
_PAREN_RE = re.compile(r"[\(（][^\(\)（）]*[\)）]")
_SPACES_RE = re.compile(r"[ \t　]{2,}")


@dataclass(frozen=True)
class DisplayStats:
    source_cues: int
    dropped_in_ranges: int
    dropped_annotation: int
    dropped_filler: int
    outside_window: int
    output_cues: int
    wrapped_cues: int


def _is_punct(ch: str) -> bool:
    return unicodedata.category(ch).startswith("P") or ch in "~～^`|"


def _is_alnum(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def clean_punctuation(text: str) -> str:
    """只留《》「」與英數詞內符號；句中標點→半形空白，句尾標點刪除。冪等。"""
    out: list[str] = []
    for i, ch in enumerate(text):
        if ch in KEEP_MARKS:
            out.append(ch)
            continue
        if ch in _WORD_JOINERS:
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""
            # 3.5、Q&A、co-pilot、50%（% 只看前面）
            if _is_alnum(prev) and (_is_alnum(nxt) or ch == "%"):
                out.append(ch)
                continue
        if _is_punct(ch) or ch == "+":
            out.append(" ")
            continue
        out.append(ch)
    cleaned = _SPACES_RE.sub(" ", "".join(out))
    return "\n".join(line.strip() for line in cleaned.splitlines() if line.strip())


def strip_annotations(text: str) -> str:
    """刪掉括號標註（``(音量注意)``、``( 字幕組:…)``）；回空字串＝整條都是標註。"""
    prev = None
    while prev != text:
        prev, text = text, _PAREN_RE.sub(" ", text)
    return _SPACES_RE.sub(" ", text).strip()


def _protected_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for open_, close in (("《", "》"), ("「", "」")):
        for m in re.finditer(
            re.escape(open_) + r"[^" + open_ + close + r"]*" + re.escape(close), text
        ):
            spans.append((m.start(), m.end()))
    return spans


def _break_points(text: str) -> tuple[list[int], list[int]]:
    """可以換行的位置（索引＝下一行從這裡開始）。空白優先，其次 jieba 詞邊界。"""
    protected = _protected_spans(text)

    def ok(i: int) -> bool:
        if i <= 0 or i >= len(text):
            return False
        # 英數詞中間、以及英文詞組（Next Token Prediction）的詞與詞之間都不斷
        left, right = text[:i].rstrip(), text[i:].lstrip()
        if left and right and _is_alnum(left[-1]) and _is_alnum(right[0]):
            return False
        return not any(s < i < e for s, e in protected)

    spaces = [i + 1 for i, ch in enumerate(text) if ch == " " and ok(i + 1)]
    from shared.subtitle_finalize import _tw_jieba

    jieba = _tw_jieba()  # 繁體詞庫；內建簡體詞庫會把繁中切碎（「釋｜出」）
    words = []
    pos = 0
    for w in jieba.cut(text, HMM=False):
        pos += len(w)
        words.append(pos)
    bounds = [i for i in words if ok(i)]
    return sorted(set(spaces)), sorted(set(bounds))


#: 換行後不該出現在行首的字（量詞、助詞）與不該留在行尾的字（數詞、指示詞）
_NO_LINE_START = "個的了們嗎呢吧啊著過地得"
_NO_LINE_END = "一這那每某幾兩"


def _break_penalty(text: str, i: int) -> float:
    """斷在 i 的代價（以字寬計）：避免「一｜個」「這｜個」「做｜的」這種斷法。"""
    penalty = 0.0
    if text[i:].lstrip()[:1] in _NO_LINE_START:
        penalty += 6
    if text[:i].rstrip()[-1:] in _NO_LINE_END:
        penalty += 6
    return penalty


def display_width(text: str) -> float:
    """燒字幕的視覺寬度：漢字與全形＝1，半形英數與空白＝0.55。"""
    return sum(0.55 if ch.isascii() else 1.0 for ch in text)


def wrap_line(text: str, max_chars: int) -> str:
    """視覺寬度超過 max_chars 就切成多行（盡量等寬），斷在空白或詞邊界。"""
    text = text.strip()
    total = display_width(text)
    if total <= max_chars:
        return text
    # cum[i] = text[:i] 的寬度
    cum = [0.0]
    for ch in text:
        cum.append(cum[-1] + (0.55 if ch.isascii() else 1.0))
    spaces, bounds = _break_points(text)
    cuts: list[int] = []
    start = 0
    # 每次只決定下一個斷點：剩下的部分照「還需要幾行」平均分，斷完再看剩下的放不放得下
    while cum[-1] - cum[start] > max_chars:
        rest = cum[-1] - cum[start]
        ideal_w = cum[start] + rest / math.ceil(rest / max_chars)

        def fits(c: int, start: int = start) -> bool:
            return start < c < len(text) and cum[c] - cum[start] <= max_chars

        def pick(cands: list[int], ideal_w: float = ideal_w, fits=fits) -> int | None:
            inside = [c for c in cands if fits(c)]
            return (
                min(inside, key=lambda c: abs(cum[c] - ideal_w) + _break_penalty(text, c))
                if inside
                else None
            )

        # 空白離理想點 4 個字寬以內就用空白（原本的標點位置），否則用最近的詞邊界
        near_spaces = [c for c in spaces if abs(cum[c] - ideal_w) <= 4]
        cut = pick(near_spaces) or pick(bounds) or pick(spaces)
        if cut is None:
            cut = max((c for c in range(start + 1, len(text)) if fits(c)), default=None)
        if cut is None:
            break  # 單一個放不下的詞（極長英文），寧可超寬也不從中間切
        cuts.append(cut)
        start = cut
    pieces, prev = [], 0
    for c in cuts + [len(text)]:
        pieces.append(text[prev:c].strip())
        prev = c
    return "\n".join(p for p in pieces if p)


def build_display_cues(
    cues: list[tuple[float, float, str]],
    *,
    start: float = 0.0,
    end: float | None = None,
    drop_ranges: list[tuple[float, float]] = (),
    max_line_chars: int = 24,
    to_traditional=None,
) -> tuple[list[tuple[float, float, str]], DisplayStats]:
    if to_traditional is None:
        from shared.transcriber import _to_traditional as to_traditional

    dropped_ranges = dropped_annot = dropped_filler = outside = wrapped = 0
    out: list[tuple[float, float, str]] = []
    for s, e, text in cues:
        if any(s >= a and e <= b for a, b in drop_ranges):
            dropped_ranges += 1
            continue
        if end is not None and s >= end or e <= start:
            outside += 1
            continue
        body = strip_annotations(text)
        if not body:
            dropped_annot += 1
            continue
        body = to_traditional(body)
        if filler_only(body):
            dropped_filler += 1
            continue
        body = _strip_fillers(body)
        body = space_han_latin(clean_punctuation(body))
        body = " ".join(body.splitlines())
        if not body.strip():
            dropped_filler += 1
            continue
        lines = wrap_line(body, max_line_chars)
        if "\n" in lines:
            wrapped += 1
        ns = max(s, start) - start
        ne = (min(e, end) if end is not None else e) - start
        if ne <= ns:
            outside += 1
            continue
        out.append((ns, ne, lines))
    stats = DisplayStats(
        source_cues=len(cues),
        dropped_in_ranges=dropped_ranges,
        dropped_annotation=dropped_annot,
        dropped_filler=dropped_filler,
        outside_window=outside,
        output_cues=len(out),
        wrapped_cues=wrapped,
    )
    return out, stats


def build_display_srt(srt_text: str, **kwargs) -> tuple[str, DisplayStats]:
    cues, stats = build_display_cues(parse_srt_text(srt_text.lstrip("﻿")), **kwargs)
    return format_srt(cues), stats
