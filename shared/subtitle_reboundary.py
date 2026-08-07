"""切點重修——把壞斷句「搬」到最近的合法語意邊界（修修 2026-08-06 根治裁決）。

`subtitle_finalize.find_bad_boundaries` 只**偵測**（我｜覺得、蠻｜好奇、詞被切半），
本模組負責**修**：對每個被旗標的相鄰 cue 對，在兩句串起來的文字裡重找切點——
只落 jieba 詞邊界、必須通過同一套規則、優先選語音停頓最大處。**文字一字不動**，
只有切點位置移動。

字級時間戳（words.json）有就用真值算停頓；沒有（精選/緊版等已重對時的 SRT）
就在 cue 自身時間範圍內線性內插——只移動幾個字的切點，誤差可忽略。

⚠️ 顯示層與工作真值分離：`transcript.srt` 只跑重修**不跑 finalize**
（cue 時間要貼語音，下游靠它切片）；顯示副本才跑 finalize 補空隙。
"""

from __future__ import annotations

import difflib
import re

from shared.cue_builder import HARD_MAX_CHARS, MIN_CUE_CHARS, PAUSE_SPACE, est_gap
from shared.subtitle_finalize import _tw_jieba, boundary_reason

MAX_ROUNDS = 3

Cue = tuple[float, float, str]
Span = tuple[float, float]


def bare(text: str) -> str:
    return re.sub(r"\s", "", text)


def char_times_from_words(words: list[dict], target: str) -> list[Span]:
    """校正後字串每字元的 (start, end)——difflib 對回 ASR 字級時間戳。

    replace/insert 段（校正動過的字）沒有對應 ASR 字，用前後錨點線性內插。
    """
    src_chars: list[str] = []
    src_times: list[Span] = []
    for w in words:
        text = (w.get("word") or "").strip()
        if not text or w.get("start") is None or w.get("end") is None:
            continue
        s, e, n = float(w["start"]), float(w["end"]), len(text)
        for k, ch in enumerate(text):
            src_chars.append(ch)
            src_times.append((s + (e - s) * k / n, s + (e - s) * (k + 1) / n))

    out: list[Span | None] = [None] * len(target)
    sm = difflib.SequenceMatcher(None, "".join(src_chars), target, autojunk=False)
    for tag, i1, _i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(j2 - j1):
                out[j1 + k] = src_times[i1 + k]
    known = [i for i, v in enumerate(out) if v is not None]
    if not known:
        raise ValueError("字元對齊全失敗——SRT 與 words.json 不是同一集？")
    for i in range(len(out)):
        if out[i] is not None:
            continue
        prev = next((k for k in reversed(known) if k < i), None)
        nxt = next((k for k in known if k > i), None)
        if prev is None:
            out[i] = out[known[0]]
        elif nxt is None:
            out[i] = out[known[-1]]
        else:
            t0, t1 = out[prev][1], out[nxt][0]
            span = max(nxt - prev, 1)
            out[i] = (
                t0 + (t1 - t0) * (i - prev) / span,
                t0 + (t1 - t0) * (i - prev + 1) / span,
            )
    return [v for v in out if v is not None]


def char_times_from_cues(cues: list[Cue]) -> list[Span]:
    """無字級時間戳時的退路：每個 cue 內字元均分自身時間範圍。"""
    out: list[Span] = []
    for s, e, text in cues:
        chars = bare(text)
        n = len(chars)
        if not n:
            continue
        step = (e - s) / n
        out.extend((s + step * k, s + step * (k + 1)) for k in range(n))
    return out


def _word_bounds(text: str) -> set[int]:
    bounds = {0, len(text)}
    pos = 0
    for tok in _tw_jieba().cut(text, HMM=False):
        pos += len(tok)
        bounds.add(pos)
    return bounds


def _render(joint: str, times: list[Span], base: int, lo: int, hi: int) -> str:
    """還原 cue 文字——停頓 ≥PAUSE_SPACE 補半形空格（house style）。"""
    parts: list[str] = []
    for k in range(lo, hi):
        if parts:
            prev = (joint[k - 1], times[base + k - 1][0], times[base + k - 1][1])
            nxt = (joint[k], times[base + k][0], times[base + k][1])
            if est_gap(prev, nxt) >= PAUSE_SPACE:
                parts.append(" ")
            elif joint[k].isascii() != joint[k - 1].isascii():
                parts.append(" ")
        parts.append(joint[k])
    return "".join(parts).strip()


def _round(cues: list[Cue], times: list[Span], starts: list[int]) -> tuple[list[Cue], int]:
    """一輪重修。切點移動後 starts[i+1] 立即改變（該對總字數不變，i+2 之後不受影響）。"""
    out = [list(c) for c in cues]
    moved = 0
    for i in range(len(out) - 1):
        a, b = out[i][2].strip(), out[i + 1][2].strip()
        if not boundary_reason(a, b):
            continue
        ba, bb = bare(a), bare(b)
        joint, base, orig = ba + bb, starts[i], len(ba)
        cands = []
        for p in sorted(_word_bounds(joint)):
            if p == orig or not (MIN_CUE_CHARS <= p <= HARD_MAX_CHARS):
                continue
            if not (MIN_CUE_CHARS <= len(joint) - p <= HARD_MAX_CHARS):
                continue
            if boundary_reason(joint[:p], joint[p:]):
                continue
            gi = base + p
            if gi <= 0 or gi >= len(times):
                continue
            gap = est_gap(
                (joint[p - 1], times[gi - 1][0], times[gi - 1][1]),
                (joint[p], times[gi][0], times[gi][1]),
            )
            cands.append((gap, -abs(p - orig), p))
        if not cands:
            continue
        _, _, p = max(cands)
        gi = base + p
        out[i][2] = _render(joint, times, base, 0, p)
        out[i][1] = times[gi - 1][1]
        out[i + 1][2] = _render(joint, times, base, p, len(joint))
        out[i + 1][0] = times[gi][0]
        starts[i + 1] = gi
        moved += 1
    return [tuple(c) for c in out], moved


def sanitize(cues: list[Cue], min_dur: float = 0.24) -> tuple[list[Cue], int]:
    """時間軸消毒：字級時間戳偶有微幅逆序（±40ms）＋校正段內插，夾成嚴格單調。"""
    out = [list(c) for c in cues]
    fixed = 0
    for i in range(len(out)):
        if i and out[i][0] < out[i - 1][1]:
            out[i][0] = out[i - 1][1]
            fixed += 1
        if out[i][1] <= out[i][0] + 1e-3:
            out[i][1] = out[i][0] + min_dur
            fixed += 1
    return [tuple(c) for c in out], fixed


def repair_cues(
    cues: list[Cue],
    *,
    words: list[dict] | None = None,
    rounds: int = MAX_ROUNDS,
) -> tuple[list[Cue], dict]:
    """切點重修主入口。回傳 (新 cues, stats)。文字內容保證不變（僅切點位置移動）。"""
    if len(cues) < 2:
        return cues, {"moved": 0, "rounds": 0, "sanitized": 0}
    target = "".join(bare(t) for _, _, t in cues)
    times = char_times_from_words(words, target) if words else char_times_from_cues(cues)
    if len(times) != len(target):
        raise ValueError(f"字元時間戳數不符：{len(times)} vs {len(target)}")
    total = 0
    used = 0
    for used in range(1, rounds + 1):
        starts, acc = [], 0
        for _, _, t in cues:
            starts.append(acc)
            acc += len(bare(t))
        cues, moved = _round(cues, times, starts)
        total += moved
        if not moved:
            break
    cues, fixed = sanitize(cues)
    return cues, {"moved": total, "rounds": used, "sanitized": fixed}
