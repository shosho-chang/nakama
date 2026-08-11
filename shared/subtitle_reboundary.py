"""切點重修——把壞斷句「搬」到最近的合法語意邊界（修修 2026-08-06 根治裁決）。

`subtitle_finalize.boundary_reason` 只**偵測**（我｜覺得、的｜…、孤兒括號），
本模組負責**修**：對每個被旗標的相鄰 cue 對，在兩句文字裡重找切點——只落
jieba 詞邊界、必須通過同一套偵測規則、括號深度為 0、優先選語音停頓最大處。

⚠️ 文字處理鐵律（2026-08-07 安吉 45s「結 婚」慘案後定版）：**切原文，不重渲染**。
第一版把兩句打散成裸字元再用 est_gap 重推空格——WhisperX 的 align 拖尾（「結」
被拉長 1.1s）讓它在詞中間塞出空格、還把校正層原有的停頓空格全部毀掉。現版
只把原文字串在切點處剖開搬移，內部空格逐字保留；唯一的接縫規則：CJK 相接
不加空格（「結」+「婚以後」→「結婚以後」）、ASCII 相接補一格。每次修復後
run-time 驗證兩條不變量（裸文字恆等、不新增任何原文沒有的空格），違反即 raise
——寧可炸也不默默出貨壞字幕。

時間：字級時間戳（words.json）有就用真值；沒有就在 cue 時間範圍內線性內插。

顯示層與工作真值分離：`transcript.srt` 只跑重修**不跑 finalize**（cue 時間要
貼語音，下游靠它切片）；顯示副本才補空隙。
"""

from __future__ import annotations

import difflib
import re

from shared.cue_builder import HARD_MAX_CHARS, MIN_CUE_CHARS, est_gap
from shared.subtitle_finalize import (
    CLOSE_BRACKETS,
    OPEN_BRACKETS,
    _tw_jieba,
    boundary_reason,
)

MAX_ROUNDS = 3

Cue = tuple[float, float, str]
Span = tuple[float, float]


def bare(text: str) -> str:
    return re.sub(r"\s", "", text)


def char_times_from_words(words: list[dict], target: str) -> list[Span]:
    """校正後字串每字元的 (start, end)——difflib 對回 ASR 字級時間戳。

    replace/insert 段（校正動過的字）沒有對應 ASR 字，用前後錨點線性內插。
    內插時間**只拿來排序停頓候選**，絕不拿來重推空格（見模組 docstring 鐵律）。
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
        n = len(bare(text))
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


def _bare_index_map(text: str) -> list[int]:
    """裸位置 k → 原字串索引（text[:idx] 的裸長度恰為 k）。長度 = 裸長+1。"""
    idx = [0]
    for i, ch in enumerate(text):
        if not ch.isspace():
            idx.append(i + 1)
    return idx


def _seam_join(left: str, right: str) -> str:
    """接縫規則：CJK 相接不加空格，ASCII 相接補一格（不重推任何其他空格）。"""
    left, right = left.rstrip(), right.lstrip()
    if not left or not right:
        return left + right
    if left[-1].isascii() and right[0].isascii():
        return left + " " + right
    return left + right


def _carve(a_text: str, b_text: str, p: int, ba_len: int) -> tuple[str, str]:
    """把 cue 對的切點搬到裸位置 p（原字串剖開搬移，內部空格逐字保留）。

    搬移片段的**外緣**（成為新 cue 起點/終點的那一側）必須 strip——切點落在
    校正空格之後時，那個空格會黏在片段外緣變成「 我覺得…」的首空格 cue
    （2026-08-07 安吉三支長片 r005/r006 稽核，每支 3–8 句中招）。
    """
    if p < ba_len:  # a 的尾巴搬去 b 開頭
        ia = _bare_index_map(a_text)[p]
        return a_text[:ia].rstrip(), _seam_join(a_text[ia:].lstrip(), b_text)
    ib = _bare_index_map(b_text)[p - ba_len]  # b 的開頭搬去 a 結尾
    return _seam_join(a_text, b_text[:ib]), b_text[ib:].lstrip()


def _depth_before(cues: list[Cue]) -> list[int]:
    """每個 cue 起點的括號深度（從整集第一句累計；閉多於開夾 0）。"""
    depths = []
    d = 0
    for _, _, text in cues:
        depths.append(d)
        for ch in text:
            if ch in OPEN_BRACKETS:
                d += 1
            elif ch in CLOSE_BRACKETS:
                d = max(0, d - 1)
    return depths


def _round(
    cues: list[Cue], times: list[Span], starts: list[int], depths: list[int]
) -> tuple[list[Cue], int]:
    """一輪重修。切點移動後 starts[i+1] 立即更新（該對總字數不變，i+2 之後不受影響）。"""
    out = [list(c) for c in cues]
    moved = 0
    for i in range(len(out) - 1):
        a, b = out[i][2].strip(), out[i + 1][2].strip()
        if not boundary_reason(a, b):
            continue
        ba, bb = bare(a), bare(b)
        joint, base, orig = ba + bb, starts[i], len(ba)
        d0 = depths[i]
        cands = []
        depth = d0
        bounds = _word_bounds(joint)
        for p in range(1, len(joint)):
            ch = joint[p - 1]
            if ch in OPEN_BRACKETS:
                depth += 1
            elif ch in CLOSE_BRACKETS:
                depth = max(0, depth - 1)
            if p == orig or p not in bounds or depth != 0:
                continue
            if not (MIN_CUE_CHARS <= p <= HARD_MAX_CHARS):
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
        new_a, new_b = _carve(out[i][2].strip(), out[i + 1][2].strip(), p, orig)
        out[i][2] = new_a
        out[i][1] = times[gi - 1][1]
        out[i + 1][2] = new_b
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


def _space_offsets(cues: list[Cue]) -> set[int]:
    """全片空格的「裸位置」集合（每個空格記它前面累計的裸字元數）。"""
    offsets = set()
    pos = 0
    for _, _, text in cues:
        for ch in text:
            if ch.isspace():
                offsets.add(pos)
            else:
                pos += 1
    return offsets


def _verify(before: list[Cue], after: list[Cue]) -> None:
    """兩條不變量（違反即 raise——寧炸不默默出貨壞字幕）。

    ① 裸文字恆等：修復只搬切點，一個字都不可增刪改。
    ② 空格零新增：輸出的每個空格，原文同裸位置必須本來就有空格
       （接縫處**刪**空格合法；**生**出原文沒有的空格＝「結 婚」慘案重演）。
    """
    b0 = "".join(bare(t) for _, _, t in before)
    b1 = "".join(bare(t) for _, _, t in after)
    if b0 != b1:
        raise ValueError("reboundary 不變量①破裂：裸文字被改動")
    extra = _space_offsets(after) - _space_offsets(before)
    if extra:
        raise ValueError(f"reboundary 不變量②破裂：新增空格於裸位置 {sorted(extra)[:5]}")


def repair_cues(
    cues: list[Cue],
    *,
    words: list[dict] | None = None,
    rounds: int = MAX_ROUNDS,
) -> tuple[list[Cue], dict]:
    """切點重修主入口。回傳 (新 cues, stats)。"""
    if len(cues) < 2:
        return cues, {"moved": 0, "rounds": 0, "sanitized": 0}
    original = list(cues)
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
        cues, moved = _round(cues, times, starts, _depth_before(cues))
        total += moved
        if not moved:
            break
    cues, fixed = sanitize(cues)
    _verify(original, cues)
    return cues, {"moved": total, "rounds": used, "sanitized": fixed}
