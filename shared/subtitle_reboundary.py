"""切點重修——把壞斷句搬到**音檔的靜音處**（修修 2026-08-12「詞庫永遠修不完」裁決）。

## 判準反轉（2026-08-12）

舊版：只修 `boundary_reason` 旗標的切點，候選限 jieba 詞邊界、比 `est_gap`。
問題是這一整條鏈都建立在「詞典知道那是一個詞」上，而詞典永遠不會涵蓋集別
詞彙——安吉 SL3 的「冒牌者」`FREQ=None`，jieba 切成「冒牌｜者」，四條規則
一致判定「…完全沒有冒牌」｜「者的問題…」是**乾淨切點**並出貨。實測三支長片
575 個切點有 148 個切在連續發聲中間，舊規則抓到 **0** 個。

現版以 `shared.pause_map.PauseMap`（音檔 20ms RMS 包絡）為主判準：
- **音檔說吵就修**，不必知道被切開的是什麼詞
- 候選排序比真實靜音；jieba 詞界降為軟限制，可被真靜音推翻
- 封閉類詞素規則（者｜們開頭…）不可被推翻——那是語言事實不是詞典覆蓋率
- 沒有停頓圖時退回舊判準，stats["pause_used"]=False 供呼叫端回報

殘餘：說話者整段沒停頓時（安吉語速快）音檔給不出答案，靠封閉類詞素規則與
人工判讀收尾。實測全片吵切點 771 → 37。

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


IMPROVE = 3.0  # 沒有真靜音可落時，新位置至少要安靜這麼多倍才值得搬


def _pick(cands: list[dict], quiet: float | None, noisy: float | None) -> dict:
    """從候選裡選切點——三層，一層比一層不確定。

    ① **真靜音**（RMS ≤ 該集靜音門檻）：人在這裡停了，信音檔。取最安靜。
    ② **半停頓**（≤ 吵門檻）：音量明顯下降但不是全靜，仍是有效訊號。取最安靜。
    ③ **全都在連續發聲中**：音檔對這一刀**沒有意見**，此時比 RMS 是自欺——
       2026-08-12 實測「冒牌者」那一刀四個合法候選 RMS 0.032–0.080 全在吵門檻
       之上，比 RMS 會選到 22/10 字的硬上限切點。改用兩個不靠詞典的結構訊號：
       原文既有的**空格**（house style：停頓 ≥0.3s 標空格，等於上游標好的分句點）
       優先，其次離原切點近、兩句長度平衡。

    沒有停頓圖時退回舊判準 est_gap 最大（已知弱，只在無音檔時用）。
    """
    if cands[0]["rms"] is None:
        return max(cands, key=lambda c: (c["gap"], -c["dist"]))
    for bar in (quiet, noisy):
        if bar is None:
            continue
        tier = [c for c in cands if c["rms"] <= bar]
        if tier:
            return min(tier, key=lambda c: (round(c["rms"], 5), not c["space"], c["dist"]))
    return min(cands, key=lambda c: (not c["space"], c["dist"], c["imbalance"]))


def _space_positions(a: str, b: str) -> set[int]:
    """joint 的裸位置集合：原文在該處有空格。

    house style 是「cue 內停頓 ≥0.3s 標半形空格」（`cue_builder.PAUSE_SPACE`），
    校正層也會在分句處留空格——所以既有空格＝上游已經標好的分句點。當音檔
    對某一刀沒有意見時，這是最好的免費訊號，而且完全不靠詞典。
    """
    out: set[int] = set()
    pos = 0
    for text in (a, b):
        for ch in text:
            if ch.isspace():
                out.add(pos)
            else:
                pos += 1
    return out


def _round(
    cues: list[Cue],
    times: list[Span],
    starts: list[int],
    depths: list[int],
    pause=None,
) -> tuple[list[Cue], int]:
    """一輪重修。切點移動後 starts[i+1] 立即更新（該對總字數不變，i+2 之後不受影響）。

    觸發條件（2026-08-12 反轉）：舊版只修 `boundary_reason` 旗標的切點，於是
    詞典沒收的詞（冒牌者）全數逃逸——安吉三支長片 575 個切點有 148 個切在
    連續發聲中間，舊規則抓到 0 個。現在**音檔說吵就修**，詞典只是另一個觸發源。
    """
    out = [list(c) for c in cues]
    moved = 0
    for i in range(len(out) - 1):
        a, b = out[i][2].strip(), out[i + 1][2].strip()
        ba, bb = bare(a), bare(b)
        joint, base, orig = ba + bb, starts[i], len(ba)
        lex = boundary_reason(a, b)
        cur_rms = None
        if pause is not None and 0 < base + orig < len(times):
            cur_rms = pause.floor(times[base + orig][0])
        noisy = cur_rms is not None and cur_rms > pause.noisy
        if not lex and not noisy:
            continue
        d0 = depths[i]
        cands: list[dict] = []
        depth = d0
        bounds = _word_bounds(joint)
        spaces = _space_positions(a, b)
        for p in range(1, len(joint)):
            ch = joint[p - 1]
            if ch in OPEN_BRACKETS:
                depth += 1
            elif ch in CLOSE_BRACKETS:
                depth = max(0, depth - 1)
            if p == orig or depth != 0:
                continue
            if not (MIN_CUE_CHARS <= p <= HARD_MAX_CHARS):
                continue
            if not (MIN_CUE_CHARS <= len(joint) - p <= HARD_MAX_CHARS):
                continue
            gi = base + p
            if gi <= 0 or gi >= len(times):
                continue
            rms = pause.floor(times[gi][0]) if pause is not None else None
            # 詞界限制可被**真靜音**推翻：jieba 說這裡不是詞界，但人在這裡
            # 停了 60ms，那就是詞界——音檔是真值，詞典只是意見。
            if p not in bounds and not (rms is not None and rms <= pause.quiet):
                continue
            # 封閉類詞素規則（者｜們開頭、的｜結尾…）不可被推翻：那是語言事實，
            # 不是詞典覆蓋率問題。停頓也不能讓「者」變成合法句首。
            if boundary_reason(joint[:p], joint[p:]):
                continue
            gap = est_gap(
                (joint[p - 1], times[gi - 1][0], times[gi - 1][1]),
                (joint[p], times[gi][0], times[gi][1]),
            )
            cands.append(
                {
                    "p": p,
                    "rms": rms,
                    "gap": gap,
                    "space": p in spaces,
                    "dist": abs(p - orig),
                    "imbalance": abs(p - (len(joint) - p)),
                }
            )
        if not cands:
            continue
        best = _pick(
            cands,
            pause.quiet if pause is not None else None,
            pause.noisy if pause is not None else None,
        )
        # 音檔觸發（沒有詞典違規）時要求實質改善，否則不動——避免為了
        # 千分之一的 RMS 差把好好的切點搬來搬去。
        if not lex and cur_rms is not None and best["rms"] is not None:
            if not (best["rms"] <= pause.quiet or best["rms"] < cur_rms / IMPROVE):
                continue
        p = best["p"]
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
    # ASCII 接縫例外：`_seam_join` 明訂「ASCII 相接補一格」（否則 We+are→Weare）。
    # 切點搬移把原本分屬兩句的英文併進同一句時，這個空格是規則要求的產物，不是
    # 「結 婚」那種重推空格。只放行**兩側都是 ASCII** 的位置，CJK 一律仍是違規。
    extra = {
        p
        for p in extra
        if not (0 < p < len(b1) and b1[p - 1].isascii() and b1[p].isascii())
    }
    if extra:
        raise ValueError(f"reboundary 不變量②破裂：新增空格於裸位置 {sorted(extra)[:5]}")


def repair_cues(
    cues: list[Cue],
    *,
    words: list[dict] | None = None,
    pause=None,
    rounds: int = MAX_ROUNDS,
) -> tuple[list[Cue], dict]:
    """切點重修主入口。回傳 (新 cues, stats)。

    `pause`（`shared.pause_map.PauseMap`）是主判準：切點必須落在靜音上。
    不傳就退回舊的詞典判準——那條路已知會讓集別詞彙被攔腰切開，
    stats["pause_used"] 會標 False，呼叫端要把這件事回報出來。
    """
    if len(cues) < 2:
        return cues, {"moved": 0, "rounds": 0, "sanitized": 0, "pause_used": pause is not None}
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
        cues, moved = _round(cues, times, starts, _depth_before(cues), pause=pause)
        total += moved
        if not moved:
            break
    cues, fixed = sanitize(cues)
    _verify(original, cues)
    return cues, {
        "moved": total,
        "rounds": used,
        "sanitized": fixed,
        "pause_used": pause is not None,
    }
