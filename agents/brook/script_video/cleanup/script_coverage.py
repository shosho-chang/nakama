"""Script-coverage mistake removal — 以逐字稿為中介選最終 take（cleanup v2）.

修修拍攝工作流：照稿講、講錯拍手、重講到對為止；成品 timeline 的文字
必須「跟逐字稿對得上、且沒有停頓」（2026-07-30 DoD）。

v1（``script_align.detect_script_anchored_cuts``）用「retake 開頭 ASR 指紋
回溯比對拍手前 ASR」— 兩個結構性失敗（同支素材實測）：

1. 搜尋窗以上一個拍手為界，拍手誤偵測把窗切碎 → 真 NG 找不到重複文字
2. 同句話兩次 take 的 ASR 輸出不同（實測「AppWorks」三次 take 被辨識成
   AppleWorks / AppWords / AppleEarth）→ ASR 對 ASR 相似度過不了門檻

v2 反轉主體：**逐字稿是 ground truth，剪輯 = 為稿上每個子句選出最後一次
成功朗讀**。兩次 take 的 ASR 再怎麼不同，各自對到「稿」上是同一個位置 —
ASR 錯字互不影響。拍手降級為交叉驗證訊號與 ad-lib 裁決錨點。

演算法：

1. 逐字稿切 clause 單元（句讀切分、過短合併）；比對用正規化另過
   OpenCC t2s — WhisperX 會隨機輸出簡體（實測「愛卡拉…程世嘉」整句
   變簡體），不歸一會整句 miss
2. 每個單元在 ASR 正規化字元流上滑窗 fuzzy 比對，收集全部 occurrences
3. 單調 DP 選 take：時間與稿序皆遞增、分數分桶後**偏好較晚的 take**
   （重講到對為止 → 最後一次是好的）＋ 同 take 連續性 bonus
   （避免在兩次 take 之間無謂跳接）
4. 未選中的 ASR 區塊分類：以「稿能解釋此區塊的字元比例」定性 —
   高（≥0.55）＝failed-take 整塊剪；低＝ad-lib 保留。混合塊（ad-lib
   結尾黏著 scripted NG 嘗試）自然落在低分 → 保留後由第 5 步雕刻
5. ad-lib 區塊內部用拍手 + ``script_align`` 指紋回溯雕出 NG（稿外內容
   沒有稿可對，退回 ASR 自我相似；搜尋窗跨出區塊 — retake 本體常在
   區塊後的 scripted 段），cut 範圍 clamp 回區塊內
6. 停頓收斂：保留字之間 gap 超過 ``max_gap`` 的收到 target gap
7. 驗證：保留文字對稿覆蓋率、重複覆蓋（漏網 NG）、拍手去向表、
   選中 take 內含拍手的可疑清單

字級 timestamps 由 ``scripts/run_whisperx_words.py`` 產出；本模組純
CPU Python（rapidfuzz + opencc），CI 不碰 GPU。
"""

from __future__ import annotations

import dataclasses
import functools
import logging
import re
from typing import Sequence

from agents.brook.script_video.cleanup.clap_impulse import NgMarker
from agents.brook.script_video.cleanup.cuts import CutPoint
from agents.brook.script_video.cleanup.script_align import (
    Word,
    detect_script_anchored_cuts,
    normalize_text,
)

logger = logging.getLogger(__name__)

# ── 單元切分 ──────────────────────────────────────────────────────────
_SENTENCE_OR_CLAUSE = re.compile(r"(?<=[。！？!?，、；：,;:\n])")
_UNIT_MIN_CHARS = 6  # 正規化字元數下限：太短（「對！」）易誤匹配，向後合併

# ── occurrence 搜尋 ──────────────────────────────────────────────────
_SCORE_CUTOFF = 68.0  # rapidfuzz ratio（0–100）
_COARSE_STEP_DIV = 5
_BOUNDARY_TRIM = 3

# ── DP 選 take ───────────────────────────────────────────────────────
_SCORE_BUCKET = 8.0  # 桶內視為同分 → 偏好較晚 take
_OVERLAP_TOLERANCE_CHARS = 3
_CONTIGUITY_BONUS = 0.02  # 相鄰單元且字元流相接 → 同 take 連續 bonus
_CONTIGUITY_MAX_CHARS = 4

# ── gap 分類 ─────────────────────────────────────────────────────────
_NOISE_MAX_SEC = 1.2
_NOISE_MAX_WORDS = 3
_SCRIPT_EXPLAIN_CUTOFF = 0.55  # 區塊字元被稿解釋比例 ≥ 此值 → failed-take
_EXPLAIN_UNIT_RATIO = 65.0  # 單元在區塊內滑窗 ratio ≥ 此值算「解釋了」該窗

# ── ad-lib 雕刻 ──────────────────────────────────────────────────────
_ADLIB_CUT_CLAMP_HEAD = 0.2  # cut 允許超出區塊頭的秒數
_ADLIB_CUT_CLAMP_TAIL = 1.0  # cut 允許超出區塊尾的秒數
# 指紋接不住的拍手（拍手後轉講「別的內容」而非重講 → 無重複文字）：
# 拍手緊鄰語音（≤2s）→ 被否決語句 = 拍手前最後一個 word-run，自動剪；
# 拍手隔著長沉默 → 語意不明（可能只否決懸空的幾個字），不硬剪，
# 改出裁決項給 LLM/人工，裁決結果經 manual_cuts 重放。
# 實測案例：孤懸的段落起頭「最後」（隔 6s 拍手）、懸空收尾
# 「…在這個年代我覺得」（隔 9s 拍手）— 都不是 heuristic 能拿準的。
_ROLLBACK_TIGHT_SEC = 2.0  # 拍手與前字距離 ≤ 此值才自動剪
_ROLLBACK_RUN_GAP = 0.5  # word-run 的斷點 gap
_ROLLBACK_RUN_CAP_SEC = 6.0  # run 超過此長度不自動剪，出裁決項
_ROLLBACK_LEAD_IN = 0.12  # cut 結尾留給 retake 的 lead-in

# ── 停頓收斂 ─────────────────────────────────────────────────────────
_MAX_GAP_SEC = 0.50
_TARGET_TAIL_SEC = 0.18
_TARGET_HEAD_SEC = 0.12
_HEAD_PAD_SEC = 0.30
_TAIL_PAD_SEC = 1.00
# 短於此的保留段是 cut 邊界縫隙漏出的孤兒字（實測：裁決 cut 與指紋
# cut 鏈之間 0.5s 的「最」）— 丟棄併入前後 cut，不是內容。
_MIN_SEGMENT_SEC = 0.8

# ── 拍手否決（hard constraint）──────────────────────────────────────
# 2026-07-30 血淚：拍手前修修會想 3–9 秒才拍 — 用「離拍手多近」當窗口
# （初版 0.8s）接不住。正確語意：拍手否決其前的整個語音 run；重錄從
# 哪個字開始，否決範圍就從那個字開始 — 由「同單元是否存在更晚的
# 乾淨 take」決定，不由時間窗決定。
_DAMN_RUN_GAP = 1.2  # 否決 run 的字間斷點
_DAMN_OVERLAP_FRAC = 0.5  # occurrence 字數落在否決 run 內 ≥ 此比例 → 否決

# ── 時間戳修復 ───────────────────────────────────────────────────────
# WhisperX 對齊崩壞時把好幾秒的語音壓縮進極短區間（實測 31 字/秒），
# 之後的切點、字幕全跟著錯 — 用 VAD 實測語音範圍重內插。
_GARBAGE_RATE = 12.0  # 單字字速超過此值（字/秒）視為壓縮
_GARBAGE_MIN_WORDS = 8  # 連續此數量以上才算崩壞 run（單字誤差不觸發）
_REPAIR_VAD_RMS = 0.02
_REPAIR_MAX_EXTEND_SEC = 12.0  # 修復時最多往後找語音端點這麼遠


@functools.lru_cache(maxsize=8192)
def _t2s_char(ch: str) -> str:
    """單字元繁→簡（比對用；逐字轉保證長度 1:1，時間索引不漂移）。"""
    import opencc

    global _T2S_CONVERTER
    try:
        conv = _T2S_CONVERTER
    except NameError:
        conv = _T2S_CONVERTER = opencc.OpenCC("t2s")
    out = conv.convert(ch)
    return out if len(out) == 1 else ch


def _match_norm(text: str) -> str:
    """比對用正規化：normalize_text + 逐字 t2s。"""
    norm, _ = normalize_text(text)
    return "".join(_t2s_char(c) for c in norm)


@dataclasses.dataclass(frozen=True)
class _TimedChar:
    char: str
    start: float
    end: float
    word_idx: int


@dataclasses.dataclass(frozen=True)
class Unit:
    """逐字稿 clause 單元。text 保留原文（顯示/SRT 用），norm 是比對用。"""

    idx: int
    text: str
    norm: str


@dataclasses.dataclass(frozen=True)
class Occurrence:
    unit_idx: int
    c0: int
    c1: int
    score: float
    t0: float
    t1: float
    w0: int
    w1: int


@dataclasses.dataclass
class Block:
    w0: int
    w1: int
    t0: float
    t1: float
    text: str
    classification: str  # "failed-take" | "noise" | "adlib"
    explain_frac: float = 0.0  # 稿能解釋的字元比例


@dataclasses.dataclass
class CleanPlan:
    units: list[Unit]
    selected: list[Occurrence]
    unmatched_units: list[int]
    blocks: list[Block]
    adlib_keep_spans: list[tuple[int, int]]
    adlib_internal_cuts: list[CutPoint]
    kept_word_idx: list[int]
    cuts: list[CutPoint]
    kept_segments: list[tuple[float, float]]
    warnings: list[str]
    # 時間戳修復後的 words — 所有索引/時間都指向這份，下游（SRT、驗證）
    # 必須用它而不是呼叫端的原始 words（修復會平移時間）
    words: list[Word] = dataclasses.field(default_factory=list)
    # 拍手否決 run 的字索引（驗證與 QC 用）
    damned_word_idx: set[int] = dataclasses.field(default_factory=set)
    # 語意不明的拍手（隔長沉默、run 過長）→ 裁決項：LLM/人工看完給
    # manual_cuts 重跑。每項: {clap, run_start, run_end, run_text, gap}
    adjudications: list[dict] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage 0 — 時間戳修復（WhisperX 對齊崩壞區）
# ---------------------------------------------------------------------------


def repair_word_timestamps(
    words: Sequence[Word],
    audio_path,
    *,
    rate_threshold: float = _GARBAGE_RATE,
    min_words: int = _GARBAGE_MIN_WORDS,
) -> tuple[list[Word], list[str]]:
    """壓縮崩壞的字級時間戳 → 以 VAD 實測語音範圍均勻重內插。

    實測（2026-07-30 頻道復出 13.2–16.7s）：WhisperX 把 ~6 秒的語音
    壓進 3.5 秒（31 字/秒），之後的切點與字幕全跟著錯。修復：找出
    連續 ``min_words`` 個字速 > ``rate_threshold`` 的 run，往後用 VAD
    找真實語音端點，把 run 的字均勻攤回 [run 頭, 語音端點]。

    ``audio_path`` 為 None 時原樣返回（測試/無音訊環境）。
    """
    if audio_path is None:
        return list(words), []

    import numpy as np

    from agents.brook.script_video.cleanup.mistake_removal import (
        _load_wav,
        _lowpass_filter,
    )

    def rate(w: Word) -> float:
        return (len(_match_norm(w.text)) or 1) / max(w.end - w.start, 1e-3)

    flags = [rate(w) > rate_threshold for w in words]
    repaired = list(words)
    notes: list[str] = []
    audio = lpf = None
    sr = 0
    n = len(words)

    # 原始 runs（連續 flagged）→ 合併相鄰（崩壞區裡零星單字的字速
    # 「看起來正常」會把 run 碎化，每段的修復 cap 都是下一段的頭 →
    # 實際攤不開）— 間隔 ≤3 個字就併，合併後整體字速仍超標才修
    raw_runs: list[list[int]] = []
    i = 0
    while i < n:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and flags[j + 1]:
            j += 1
        raw_runs.append([i, j])
        i = j + 1
    merged_runs: list[list[int]] = []
    for r in raw_runs:
        if merged_runs and r[0] - merged_runs[-1][1] <= 4:
            merged_runs[-1][1] = r[1]
        else:
            merged_runs.append(r)

    for i, j in merged_runs:
        total_c = sum(len(_match_norm(w.text)) or 1 for w in words[i : j + 1])
        span = max(0.05, words[j].end - words[i].start)
        if j - i + 1 >= min_words and total_c / span > rate_threshold * 0.8:
            if audio is None:
                audio, sr = _load_wav(audio_path)
                lpf = _lowpass_filter(audio, sr, 3000.0)
            run_start = words[i].start
            # cap 留 0.2s 邊距，避免反向 VAD 掃到下一個 take 自己的開頭
            cap = (words[j + 1].start - 0.2) if j + 1 < n else words[j].end + _REPAIR_MAX_EXTEND_SEC
            cap = min(cap, words[j].end + _REPAIR_MAX_EXTEND_SEC)
            cap = max(cap, words[j].end)
            # 反向掃描：cap 往回找最後一個有聲 30ms 窗
            frame = max(1, int(sr * 0.03))
            b = min(len(lpf), int(cap * sr))
            a = max(0, int(words[j].end * sr))
            vad_end = words[j].end
            pos = b - frame
            while pos > a:
                seg = lpf[pos : pos + frame].astype(np.float64)
                if float(np.sqrt(np.mean(seg**2))) >= _REPAIR_VAD_RMS:
                    vad_end = (pos + frame) / sr
                    break
                pos -= frame
            new_end = max(words[j].end, vad_end)
            total_chars = sum(len(_match_norm(w.text)) or 1 for w in words[i : j + 1])
            scale = (new_end - run_start) / max(1, total_chars)
            cursor = run_start
            for k in range(i, j + 1):
                c = len(_match_norm(words[k].text)) or 1
                repaired[k] = Word(text=words[k].text, start=cursor, end=cursor + c * scale)
                cursor += c * scale
            notes.append(
                f"時間戳修復：{run_start:.1f}–{words[j].end:.1f}s 的 {j - i + 1} 字"
                f"（{total_chars / max(0.1, words[j].end - run_start):.0f} 字/秒壓縮）"
                f"→ 攤回 {run_start:.1f}–{new_end:.1f}s"
            )
    return repaired, notes


# ---------------------------------------------------------------------------
# 拍手否決 run
# ---------------------------------------------------------------------------


def damned_word_indices(
    words: Sequence[Word],
    clap_times: Sequence[float],
    *,
    run_gap: float = _DAMN_RUN_GAP,
) -> set[int]:
    """每個拍手往回標記其否決的語音 run（字索引集合）。

    拍手語意：否決其前的語音 — 不論拍手前停頓多久（實測 3–9 秒都有）。
    否決是否生效由下游決定：同單元存在拍手後的乾淨 take 才排除；
    唯一 take（重錄從更後面開始 → 前段其實被默許）照留。
    """
    damned: set[int] = set()
    for t in clap_times:
        k = None
        for idx in range(len(words) - 1, -1, -1):
            if words[idx].start <= t:
                k = idx
                break
        if k is None:
            continue
        j = k
        while j - 1 >= 0 and words[j].start - words[j - 1].end < run_gap:
            j -= 1
        damned.update(range(j, k + 1))
    return damned


def excluded_occurrence_indices(
    words: Sequence[Word],
    occurrences: Sequence[Occurrence],
    clap_times: Sequence[float],
    *,
    run_gap: float = _DAMN_RUN_GAP,
) -> set[int]:
    """逐拍手判定要排除的 occurrence（回傳 occurrences 的索引集合）。

    語意：「重錄從哪個字開始，否決範圍就從那個字開始」。拍手 t 否決其
    前語音 run 中的 take — 但只在該單元於 **t 之後**還有 take 時排除
    （之後沒有重錄 = 該內容被默許，例如整段開頭）。

    全域 run 標記行不通（2026-07-30 教訓）：take 是連續語音，run 必然
    回溯到 take 開頭，37 個拍手會把幾乎所有 take 都標到 — 只有
    「per-clap + 存在拍手後替代」的條件式排除才對應拍攝協議。
    """
    # 每個拍手的 run 起點（時間）
    run_starts: list[tuple[float, float]] = []  # (run_start, clap_t)
    for t in clap_times:
        k = None
        for idx in range(len(words) - 1, -1, -1):
            if words[idx].start <= t:
                k = idx
                break
        if k is None:
            continue
        j = k
        while j - 1 >= 0 and words[j].start - words[j - 1].end < run_gap:
            j -= 1
        run_starts.append((words[j].start, t))

    excluded: set[int] = set()
    for rs, t in run_starts:
        for i, o in enumerate(occurrences):
            if i in excluded:
                continue
            # occurrence 完整落在該拍手的否決 run 內
            if o.t0 >= rs - 0.5 and o.t1 <= t + 0.3:
                if any(a.unit_idx == o.unit_idx and a.t0 > t for a in occurrences):
                    excluded.add(i)
    return excluded


# ---------------------------------------------------------------------------
# Stage 1 — 單元切分與字元流
# ---------------------------------------------------------------------------


def split_units(script_text: str, *, min_chars: int = _UNIT_MIN_CHARS) -> list[Unit]:
    pieces = [p for p in _SENTENCE_OR_CLAUSE.split(script_text) if p.strip()]
    units: list[Unit] = []
    buf_text = ""
    buf_norm = ""
    for piece in pieces:
        norm = _match_norm(piece)
        buf_text += piece
        buf_norm += norm
        if len(buf_norm) >= min_chars:
            units.append(Unit(idx=len(units), text=buf_text.strip(), norm=buf_norm))
            buf_text = ""
            buf_norm = ""
    if buf_norm:
        if units:
            last = units[-1]
            units[-1] = Unit(
                idx=last.idx, text=last.text + buf_text.strip(), norm=last.norm + buf_norm
            )
        else:
            units.append(Unit(idx=0, text=buf_text.strip(), norm=buf_norm))
    return units


def build_char_stream(words: Sequence[Word]) -> list[_TimedChar]:
    stream: list[_TimedChar] = []
    for wi, w in enumerate(words):
        norm = _match_norm(w.text)
        if not norm:
            continue
        n = len(norm)
        span = max(0.0, w.end - w.start)
        for k, ch in enumerate(norm):
            stream.append(
                _TimedChar(
                    char=ch,
                    start=w.start + span * k / n,
                    end=w.start + span * (k + 1) / n,
                    word_idx=wi,
                )
            )
    return stream


# ---------------------------------------------------------------------------
# Stage 2 — occurrence 搜尋
# ---------------------------------------------------------------------------


def _ratio(a: str, b: str) -> float:
    from rapidfuzz import fuzz

    return float(fuzz.ratio(a, b))


def find_occurrences(
    units: Sequence[Unit],
    stream: Sequence[_TimedChar],
    *,
    score_cutoff: float = _SCORE_CUTOFF,
) -> list[Occurrence]:
    asr = "".join(c.char for c in stream)
    n = len(asr)
    occurrences: list[Occurrence] = []

    for unit in units:
        L = len(unit.norm)
        if L == 0 or n < 4:
            continue
        step = max(1, L // _COARSE_STEP_DIV)
        coarse: list[tuple[int, float]] = []
        for j in range(0, max(1, n - L + 1), step):
            s = _ratio(asr[j : j + L], unit.norm)
            if s >= score_cutoff - 6:
                coarse.append((j, s))
        if not coarse:
            continue
        # non-max suppression 取局部最佳：背靠背立即重講（同文相接）會讓
        # 相鄰窗分數形成連續橋，connectivity 聚類會把兩次 take 併成一個
        # cluster 只留第一次 — NMS 以 0.6L 最小間隔保住每次 take
        coarse.sort(key=lambda x: -x[1])
        min_sep = max(2, int(L * 0.6))
        peaks: list[tuple[int, float]] = []
        for j, s in coarse:
            if all(abs(j - pj) >= min_sep for pj, _ in peaks):
                peaks.append((j, s))
        for j0, s0 in peaks:
            best_j, best_s = j0, s0
            for j in range(max(0, j0 - step), min(n - 1, j0 + step) + 1):
                s = _ratio(asr[j : j + L], unit.norm)
                if s > best_s:
                    best_j, best_s = j, s
            b0, b1, bs = best_j, min(n, best_j + L), best_s
            for d0 in range(-_BOUNDARY_TRIM, _BOUNDARY_TRIM + 1):
                for d1 in range(-_BOUNDARY_TRIM, _BOUNDARY_TRIM + 1):
                    a, b = best_j + d0, min(n, best_j + L + d1)
                    if a < 0 or b - a < max(4, L - _BOUNDARY_TRIM):
                        continue
                    s = _ratio(asr[a:b], unit.norm)
                    if s > bs:
                        b0, b1, bs = a, b, s
            if bs < score_cutoff:
                continue
            occurrences.append(
                Occurrence(
                    unit_idx=unit.idx,
                    c0=b0,
                    c1=b1,
                    score=bs,
                    t0=stream[b0].start,
                    t1=stream[b1 - 1].end,
                    w0=stream[b0].word_idx,
                    w1=stream[b1 - 1].word_idx,
                )
            )

    occurrences.sort(key=lambda o: (o.unit_idx, o.t0))
    return occurrences


# ---------------------------------------------------------------------------
# Stage 3 — 單調 DP 選最終 take
# ---------------------------------------------------------------------------


def select_final_takes(
    units: Sequence[Unit],
    occurrences: Sequence[Occurrence],
    *,
    total_duration_sec: float,
) -> list[Occurrence]:
    """時間、稿序皆單調遞增下最大化覆蓋；桶內同分偏好較晚 take。

    拍手否決的 hard constraint 在進來之前處理 —
    ``excluded_occurrence_indices`` 先把被拍手否決且有替代的 take 濾掉
    （2026-07-30 血淚：重錄 take 的 ASR 比失敗 take 差一個分數桶，
    軟性 lateness 偏好被蓋掉，成品留了修修拍手否決的那次）。

    連續性 bonus：相鄰單元的 occurrence 在字元流上相接（同一次 take
    連續讀下來）→ 加分，避免文字同分時在兩次 take 之間無謂跳接
    （跳接 = 畫面上多一個不必要的 jump cut）。
    """

    occs = sorted(occurrences, key=lambda o: o.t0)
    n = len(occs)
    if n == 0:
        return []

    def gain(o: Occurrence) -> float:
        bucket = round(o.score / _SCORE_BUCKET)
        length = len(units[o.unit_idx].norm)
        lateness = 0.01 * (o.t0 / max(1.0, total_duration_sec))
        return (bucket * _SCORE_BUCKET / 100.0 + lateness) * length

    best: list[float] = [0.0] * n
    prev: list[int] = [-1] * n
    for i, oi in enumerate(occs):
        best[i] = gain(oi)
        for j in range(i):
            oj = occs[j]
            if oj.unit_idx >= oi.unit_idx:
                continue
            if oj.c1 > oi.c0 + _OVERLAP_TOLERANCE_CHARS:
                continue
            bonus = 0.0
            if (
                oi.unit_idx == oj.unit_idx + 1
                and abs(oi.c0 - oj.c1) <= _CONTIGUITY_MAX_CHARS
            ):
                bonus = _CONTIGUITY_BONUS * len(units[oi.unit_idx].norm)
            cand = best[j] + gain(oi) + bonus
            if cand > best[i]:
                best[i] = cand
                prev[i] = j
    end = max(range(n), key=lambda i: best[i])
    chain: list[Occurrence] = []
    i = end
    while i != -1:
        chain.append(occs[i])
        i = prev[i]
    chain.reverse()
    return chain


def micro_cuts_for_claps_inside_takes(
    words: Sequence[Word],
    selected: Sequence[Occurrence],
    clap_times: Sequence[float],
    warnings: list[str],
) -> list[CutPoint]:
    """選中 take 內部的拍手 → 微 NG cut（講到一半否決又原地接續的 case）。

    實測：「去年底 Cloud—」講到一半拍手重來，WhisperX 把跨拍手的英文
    字母拉長吸收（一個字母 1.6s），兩次嘗試合併成單一 occurrence。
    處理：拍手兩側最近的完整字之間切掉（往回跳過 ASCII 字母殘渣到
    CJK 字界）。
    """
    cuts: list[CutPoint] = []
    for o in selected:
        for t in clap_times:
            if not (o.t0 + 0.2 < t < o.t1 - 0.2):
                continue
            occ_words = list(range(o.w0, o.w1 + 1))
            before = [wi for wi in occ_words if words[wi].end <= t]
            after = [wi for wi in occ_words if words[wi].start >= t]
            if not before or not after:
                continue
            bi = before[-1]
            # ASCII 字母是 ASR 把跨拍手語音打散的殘渣 — 退到 CJK 字界
            while bi - 1 >= o.w0 and words[bi].text.isascii():
                bi -= 1
            start = words[bi].end
            end = max(t + 0.05, words[after[0]].start - _ROLLBACK_LEAD_IN)
            if start < end:
                cuts.append(
                    CutPoint(type="ripple-delete", start_sec=start, end_sec=end,
                             reason="clap-in-take", confidence=0.5)
                )
                warnings.append(
                    f"拍手 @ {t:.1f}s 落在選中 take 內（單元 {o.unit_idx}）→ "
                    f"微切 [{start:.1f}–{end:.1f}s] — 請覆核"
                )
    return cuts


# ---------------------------------------------------------------------------
# Stage 4 — 未匹配區塊分類
# ---------------------------------------------------------------------------


def _explain_fraction(block_norm: str, units: Sequence[Unit]) -> float:
    """區塊字元有多少比例能被「某個稿單元的滑窗匹配」解釋。

    failed take 幾乎全是稿內容的重讀 → 高比例；ad-lib 低；
    「ad-lib 尾端黏著 scripted NG 嘗試」的混合塊落在中低 → 判 ad-lib
    保留，再由指紋雕刻清內部 NG。
    """
    from rapidfuzz import fuzz

    n = len(block_norm)
    if n == 0:
        return 0.0
    covered = [False] * n
    for u in units:
        L = len(u.norm)
        if L == 0:
            continue
        if L >= n:
            # 區塊比單元短（講一半就拍手的前綴片段）— 整塊落在單元內
            # 就算全數解釋。partial_ratio 只在「短找長」方向安全：反向
            # （單元找長區塊）會把只含一小段稿文的混合塊誤判成 failed。
            if float(fuzz.partial_ratio(block_norm, u.norm)) >= _EXPLAIN_UNIT_RATIO:
                covered = [True] * n
                break
            continue
        step = max(1, L // 3)
        for j in range(0, n - L + 1, step):
            if _ratio(block_norm[j : j + L], u.norm) >= _EXPLAIN_UNIT_RATIO:
                for k in range(j, j + L):
                    covered[k] = True
    return sum(covered) / n


def classify_blocks(
    words: Sequence[Word],
    units: Sequence[Unit],
    selected: Sequence[Occurrence],
    *,
    noise_max_sec: float = _NOISE_MAX_SEC,
    explain_cutoff: float = _SCRIPT_EXPLAIN_CUTOFF,
) -> list[Block]:
    kept = set()
    for o in selected:
        kept.update(range(o.w0, o.w1 + 1))

    blocks: list[Block] = []
    run: list[int] = []
    for wi in range(len(words)):
        if wi in kept:
            if run:
                blocks.append(_make_block(words, units, run, noise_max_sec, explain_cutoff))
                run = []
        else:
            run.append(wi)
    if run:
        blocks.append(_make_block(words, units, run, noise_max_sec, explain_cutoff))
    return blocks


def _make_block(
    words: Sequence[Word],
    units: Sequence[Unit],
    run: list[int],
    noise_max_sec: float,
    explain_cutoff: float,
) -> Block:
    w0, w1 = run[0], run[-1]
    t0, t1 = words[w0].start, words[w1].end
    text = "".join(words[i].text for i in run)
    norm = _match_norm(text)

    if (t1 - t0) <= noise_max_sec or len(run) <= _NOISE_MAX_WORDS:
        return Block(w0=w0, w1=w1, t0=t0, t1=t1, text=text, classification="noise")

    frac = _explain_fraction(norm, units)
    cls = "failed-take" if frac >= explain_cutoff else "adlib"
    return Block(
        w0=w0, w1=w1, t0=t0, t1=t1, text=text,
        classification=cls, explain_frac=round(frac, 2),
    )


# ---------------------------------------------------------------------------
# Stage 5+6 — 保留集組裝與停頓收斂
# ---------------------------------------------------------------------------


def _last_run_before(
    words: Sequence[Word],
    block: Block,
    clap_t: float,
) -> tuple[float, float, str] | None:
    """拍手前區塊內最後一個 word-run（gap < _ROLLBACK_RUN_GAP 相連）。"""
    in_block = [
        w for w in words if w.start >= block.t0 and w.end <= min(clap_t, block.t1)
    ]
    if not in_block:
        return None
    run_end_w = in_block[-1]
    run_start = run_end_w.start
    for prev_w, cur_w in zip(reversed(in_block[:-1]), reversed(in_block[1:])):
        if cur_w.start - prev_w.end >= _ROLLBACK_RUN_GAP:
            break
        run_start = prev_w.start
    text = "".join(w.text for w in in_block if w.start >= run_start)
    return run_start, run_end_w.end, text


def build_clean_plan(
    words: Sequence[Word],
    script_text: str,
    *,
    total_duration_sec: float,
    ng_markers: Sequence[NgMarker] = (),
    max_gap_sec: float = _MAX_GAP_SEC,
    adlib_policy: str = "keep",  # "keep" | "cut"
    tail_policy: str = "script-end",  # "script-end" | "keep-all"
    manual_cuts: Sequence[CutPoint] = (),  # 裁決層回填的 cut（重放用）
    audio_path=None,  # 給時間戳修復與 VAD 用；None = 跳過修復
) -> CleanPlan:
    warnings: list[str] = []
    words, repair_notes = repair_word_timestamps(list(words), audio_path)
    warnings.extend(repair_notes)

    all_claps = sorted(t for m in ng_markers for t in m.clap_times)
    damned = damned_word_indices(words, all_claps)

    units = split_units(script_text)
    stream = build_char_stream(words)
    occurrences = find_occurrences(units, stream)
    excl = excluded_occurrence_indices(words, occurrences, all_claps)
    if excl:
        excl_units = sorted({occurrences[i].unit_idx for i in excl})
        warnings.append(
            f"拍手否決排除 {len(excl)} 個 take（單元 {excl_units}）— "
            f"改選拍手後的重錄 take"
        )
    survivors = [o for i, o in enumerate(occurrences) if i not in excl]
    selected = select_final_takes(units, survivors, total_duration_sec=total_duration_sec)

    # 縫隙吸收：相鄰選中單元之間的短字縫（<1.5s、無拍手、語音連續）是
    # 同一次 take 的連續語流 — 中間的字必然是稿文的 ASR 亂碼版（實測
    # 「親手寫過一行code」→「親手寫規範口」被 occurrence 邊界修整裁掉，
    # 成品憑空少半句）。展開後段 occurrence 把縫隙收進來。
    for k in range(len(selected) - 1):
        a, b = selected[k], selected[k + 1]
        if b.unit_idx != a.unit_idx + 1 or b.w0 <= a.w1 + 1:
            continue
        if not (0 < b.t0 - a.t1 <= 1.5):
            continue
        if any(a.t1 < t < b.t0 for t in all_claps):
            continue
        gap_idx = list(range(a.w1 + 1, b.w0))
        chain = [a.w1] + gap_idx + [b.w0]
        if any(words[y].start - words[x].end >= 0.6 for x, y in zip(chain, chain[1:])):
            continue
        gap_text = "".join(words[i].text for i in gap_idx)
        selected[k + 1] = dataclasses.replace(
            b, w0=gap_idx[0], t0=words[gap_idx[0]].start
        )
        warnings.append(
            f"縫隙吸收：單元 {a.unit_idx}→{b.unit_idx} 間 "
            f"[{words[gap_idx[0]].start:.1f}–{words[gap_idx[-1]].end:.1f}s]"
            f"「{gap_text[:20]}」（ASR 亂碼的稿文）併入保留"
        )

    got = {o.unit_idx for o in selected}
    unmatched_units = [u.idx for u in units if u.idx not in got]
    for ui in unmatched_units:
        warnings.append(
            f"稿單元 {ui} 沒有任何 occurrence（沒讀到或 ASR 全損）: 「{units[ui].text}」"
        )

    blocks = classify_blocks(words, units, selected)

    # ad-lib 區塊：預設保留；內部用「拍手 + 指紋回溯」雕出 NG。
    # 搜尋窗跨出區塊（retake 本體常是區塊後的 scripted 段），但 cut
    # clamp 回區塊範圍 — 不准動到已選中的 take。
    clap_times = all_claps
    adlib_keep: list[tuple[int, int]] = []
    adlib_internal_cuts: list[CutPoint] = list(manual_cuts)
    adlib_internal_cuts.extend(
        micro_cuts_for_claps_inside_takes(words, selected, clap_times, warnings)
    )
    adjudications: list[dict] = []
    for blk in blocks:
        if blk.classification != "adlib":
            continue
        if adlib_policy == "cut":
            continue
        adlib_keep.append((blk.w0, blk.w1))
        # 區塊「附近」的拍手都要有去向：含 t1 之後 1.5s（否決區塊尾語句、
        # 拍手落在區塊外靜音的 case）
        inner_claps = [t for t in clap_times if blk.t0 < t < blk.t1 + 1.5]
        if not inner_claps:
            continue
        # 邊界錨：區塊前最近的拍手當回溯窗下界（防指紋誤入更早內容）
        boundary = [t for t in clap_times if t <= blk.t0]
        clap_args = ([boundary[-1]] if boundary else []) + inner_claps
        raw_cuts = detect_script_anchored_cuts(words, clap_args)
        lo = blk.t0 - _ADLIB_CUT_CLAMP_HEAD
        hi = blk.t1 + _ADLIB_CUT_CLAMP_TAIL
        blk_cuts: list[CutPoint] = []
        for c in raw_cuts:
            s, e = max(c.start_sec, lo), min(c.end_sec, hi)
            if s < e:
                blk_cuts.append(
                    CutPoint(type="ripple-delete", start_sec=s, end_sec=e,
                             reason="adlib-fingerprint", confidence=c.confidence)
                )
        # 指紋接不住的拍手（拍手後轉講別的內容 → 無重複文字可對）：
        # 拍手語意學上必然否決其前的語句。緊鄰語音的拍手自動剪最後一個
        # word-run；隔長沉默的拍手語意不明（可能只否決懸空幾個字）→
        # 出裁決項，不硬剪。
        for t in inner_claps:
            if any(c.start_sec <= t <= c.end_sec for c in blk_cuts):
                continue
            if any(c.start_sec <= t <= c.end_sec for c in manual_cuts):
                continue
            run = _last_run_before(words, blk, t)
            if run is None:
                continue
            run_start, run_end, run_text = run
            nxt = next((w for w in words if w.start > t), None)
            cut_end = max(t + 0.05, (nxt.start - _ROLLBACK_LEAD_IN) if nxt else t + 0.05)
            cut_end = min(cut_end, blk.t1 + _ADLIB_CUT_CLAMP_TAIL)
            tight = (t - run_end) <= _ROLLBACK_TIGHT_SEC
            short = (run_end - run_start) <= _ROLLBACK_RUN_CAP_SEC
            if tight and short and run_start < cut_end:
                blk_cuts.append(
                    CutPoint(type="ripple-delete", start_sec=run_start, end_sec=cut_end,
                             reason="adlib-clap-rollback", confidence=0.6)
                )
                warnings.append(
                    f"拍手 @ {t:.1f}s 指紋無對應 → 自動剪緊鄰 word-run "
                    f"[{run_start:.1f}–{cut_end:.1f}s]「{run_text[:40]}」— 請覆核"
                )
            else:
                adjudications.append(
                    {
                        "clap": round(t, 2),
                        "run_start": round(run_start, 2),
                        "run_end": round(run_end, 2),
                        "gap_to_clap": round(t - run_end, 2),
                        "run_text": run_text,
                        "cut_end_if_cut": round(cut_end, 2),
                        "block": [round(blk.t0, 2), round(blk.t1, 2)],
                    }
                )
        adlib_internal_cuts.extend(blk_cuts)

    # 最終保留 word 集
    kept_idx = set()
    for o in selected:
        kept_idx.update(range(o.w0, o.w1 + 1))
    for w0, w1 in adlib_keep:
        kept_idx.update(range(w0, w1 + 1))
    for c in adlib_internal_cuts:
        for wi in list(kept_idx):
            mid = (words[wi].start + words[wi].end) / 2
            if c.start_sec <= mid <= c.end_sec:
                kept_idx.discard(wi)

    # tail policy：稿的最後一個單元就是影片的結尾。之後的內容（誤錄、
    # 被切斷的補充）預設丟棄 — 保留需明確 tail_policy="keep-all"
    if tail_policy == "script-end" and selected:
        script_end = max(o.t1 for o in selected)
        dropped = [wi for wi in kept_idx if words[wi].start > script_end + 0.5]
        if dropped:
            dropped_text = "".join(words[wi].text for wi in sorted(dropped))
            warnings.append(
                f"tail_policy=script-end：丟棄稿尾之後 {len(dropped)} 個字"
                f"（{words[min(dropped)].start:.1f}s 起）「{dropped_text[:60]}…」"
            )
            kept_idx.difference_update(dropped)
    kept_sorted = sorted(kept_idx)

    # 保留字 → 保留時間段（gap ≤ max_gap 併段；> max_gap 收斂成 jump cut）
    seg_bounds: list[tuple[int, int]] = []
    if kept_sorted:
        first = prev = kept_sorted[0]
        for wi in kept_sorted[1:]:
            if words[wi].start - words[prev].end > max_gap_sec:
                seg_bounds.append((first, prev))
                first = wi
            prev = wi
        seg_bounds.append((first, prev))

    # 孤兒微段過濾：cut 邊界縫隙漏出的零碎字（如兩個 cut 之間 0.5s 的
    # 半個詞）不是內容 — 丟字併入 cut。但**選中 take 的字豁免**：
    # micro-cut 可能把 take 開頭隔成短片段（實測「去年底」0.48s），
    # 那是稿內容不是殘渣。
    sel_words: set[int] = set()
    for o in selected:
        sel_words.update(range(o.w0, o.w1 + 1))
    orphans = [
        (fw, lw)
        for fw, lw in seg_bounds
        if words[lw].end - words[fw].start < _MIN_SEGMENT_SEC
        and not any(wi in sel_words for wi in range(fw, lw + 1))
    ]
    for fw, lw in orphans:
        text = "".join(words[i].text for i in range(fw, lw + 1) if i in kept_idx)
        warnings.append(
            f"孤兒微段 [{words[fw].start:.1f}–{words[lw].end:.1f}s]「{text}」→ 丟棄併入 cut"
        )
        for i in range(fw, lw + 1):
            kept_idx.discard(i)
    if orphans:
        kept_sorted = sorted(kept_idx)
        seg_bounds = [b for b in seg_bounds if b not in orphans]

    kept_set = set(kept_sorted)
    segments: list[tuple[float, float]] = []
    for k, (fw, lw) in enumerate(seg_bounds):
        s = words[fw].start - (_HEAD_PAD_SEC if k == 0 else _TARGET_HEAD_SEC)
        e = words[lw].end + (_TAIL_PAD_SEC if k == len(seg_bounds) - 1 else _TARGET_TAIL_SEC)
        # pad 不得吃進相鄰的非保留字 — 懸空字可能與保留字零間隙相連
        # （實測「…在這個年代|我覺得」）
        if fw > 0 and (fw - 1) not in kept_set:
            s = max(s, words[fw - 1].end + 0.02)
        if lw + 1 < len(words) and (lw + 1) not in kept_set:
            e = min(e, words[lw + 1].start - 0.02)
        # pad 不得把拍手聲包進成品（容忍 word 時間戳 overshoot 0.25s）
        for t in clap_times:
            if words[lw].end - 0.25 <= t <= e:
                e = min(e, max(t - 0.12, words[lw].end - 0.25))
            if s <= t <= words[fw].start + 0.25:
                s = max(s, min(t + 0.10, words[fw].start))
        s = max(0.0, s)
        e = min(total_duration_sec, max(e, s + 0.1))
        segments.append((s, e))

    merged: list[tuple[float, float]] = []
    for s, e in segments:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    segments = merged

    cuts: list[CutPoint] = []
    cursor = 0.0
    for s, e in segments:
        if s > cursor + 1e-3:
            cuts.append(
                CutPoint(type="ripple-delete", start_sec=cursor, end_sec=s,
                         reason="coverage-v2", confidence=1.0)
            )
        cursor = e
    if cursor < total_duration_sec - 1e-3:
        cuts.append(
            CutPoint(type="ripple-delete", start_sec=cursor, end_sec=total_duration_sec,
                     reason="coverage-v2", confidence=1.0)
        )

    return CleanPlan(
        units=list(units),
        selected=list(selected),
        unmatched_units=unmatched_units,
        blocks=blocks,
        adlib_keep_spans=adlib_keep,
        adlib_internal_cuts=adlib_internal_cuts,
        kept_word_idx=kept_sorted,
        cuts=cuts,
        kept_segments=segments,
        warnings=warnings,
        words=list(words),
        damned_word_idx=damned,
        adjudications=adjudications,
    )


# ---------------------------------------------------------------------------
# 乾淨 timeline 映射（SRT 與驗證共用）
# ---------------------------------------------------------------------------


def map_clean_floor(segments: Sequence[tuple[float, float]], t: float) -> float:
    """t 或其前最近的保留時刻 → 乾淨 timeline（字尾/cue 尾用）。"""
    offset = 0.0
    result = 0.0
    for s, e in segments:
        if t >= s:
            result = offset + min(t, e) - s
        offset += e - s
    return result


def map_clean_ceil(segments: Sequence[tuple[float, float]], t: float) -> float:
    """t 或其後最近的保留時刻 → 乾淨 timeline（字頭/cue 頭用）。"""
    offset = 0.0
    for s, e in segments:
        if t <= e:
            return offset + max(t, s) - s
        offset += e - s
    return offset


# ---------------------------------------------------------------------------
# 字幕 — scripted cue 用稿上文字、ad-lib cue 用 ASR 文字
# ---------------------------------------------------------------------------


def build_srt(plan: CleanPlan, words: Sequence[Word] | None = None) -> str:
    """乾淨 timeline 的 SRT：文字以稿為準，ad-lib 段落用 ASR 文字。

    一律使用 ``plan.words``（時間戳修復後）— ``words`` 參數僅為舊介面
    相容，會被忽略。
    """
    from agents.brook.script_video.cleanup.script_align import (
        _clean_cue_text,
        _seconds_to_srt_ts,
        _split_script_cues,
    )

    words = plan.words
    stream = build_char_stream(words)
    segs = plan.kept_segments
    entries: list[tuple[float, float, str]] = []  # (clean_start, clean_end, text)

    # scripted cues：單元原文切 cue，時間按字元比例内插到 occurrence span
    for occ in plan.selected:
        unit = plan.units[occ.unit_idx]
        norm_unit, imap = normalize_text(unit.text)
        L = len(norm_unit)
        span = occ.c1 - occ.c0
        if L == 0 or span <= 0:
            continue
        orig_to_norm = {oi: j for j, oi in enumerate(imap)}
        cursor = 0
        for cue_text in _split_script_cues(unit.text):
            idx = unit.text.find(cue_text, cursor)
            if idx < 0:
                idx = cursor
            cursor = idx + len(cue_text)
            norm_pos = [
                orig_to_norm[i]
                for i in range(idx, idx + len(cue_text))
                if i in orig_to_norm
            ]
            display = _clean_cue_text(cue_text)
            if not norm_pos or not display:
                continue
            p0 = min(occ.c1 - 1, occ.c0 + int(norm_pos[0] * span / L))
            p1 = min(occ.c1 - 1, occ.c0 + int((norm_pos[-1] + 1) * span / L) - 1)
            t0 = map_clean_ceil(segs, stream[p0].start)
            t1 = map_clean_floor(segs, stream[max(p0, p1)].end)
            entries.append((t0, max(t0, t1), display))

    # ad-lib cues：保留的稿外字 → ASR 文字（轉繁），gap>0.6s 或超長換 cue
    sel_word_idx = set()
    for o in plan.selected:
        sel_word_idx.update(range(o.w0, o.w1 + 1))
    adlib_words = [wi for wi in plan.kept_word_idx if wi not in sel_word_idx]
    group: list[int] = []

    def flush(g: list[int]) -> None:
        if not g:
            return
        text = "".join(words[i].text for i in g)
        try:
            import opencc

            text = opencc.OpenCC("s2twp").convert(text)
        except Exception:  # pragma: no cover - opencc 缺席時保留原文
            pass
        t0 = map_clean_ceil(segs, words[g[0]].start)
        t1 = map_clean_floor(segs, words[g[-1]].end)
        entries.append((t0, max(t0, t1), text))

    for wi in adlib_words:
        if group and (
            words[wi].start - words[group[-1]].end > 0.6
            or len("".join(words[i].text for i in group)) >= _MAX_SUBTITLE_SPLIT
        ):
            flush(group)
            group = []
        group.append(wi)
    flush(group)

    entries.sort(key=lambda x: (x[0], x[1]))
    lines: list[str] = []
    prev_end = 0.0
    for i, (t0, t1, text) in enumerate(entries, 1):
        t0 = max(t0, prev_end)
        t1 = max(t1, t0 + 0.2)
        prev_end = t1
        lines.append(f"{i}\n{_seconds_to_srt_ts(t0)} --> {_seconds_to_srt_ts(t1)}\n{text}\n")
    return "\n".join(lines)


_MAX_SUBTITLE_SPLIT = 14  # ad-lib cue 換行門檻（同 house style _MAX_SUBTITLE_CHARS）


# ---------------------------------------------------------------------------
# Stage 7 — 驗證
# ---------------------------------------------------------------------------


def verify_plan(
    plan: CleanPlan,
    words: Sequence[Word] | None,
    ng_markers: Sequence[NgMarker],
    *,
    audio_path=None,
) -> dict:
    """DoD 驗證：覆蓋率、重複覆蓋、拍手去向、否決字殘留、殘餘 gap、切點能量。

    一律使用 ``plan.words``（時間戳修復後）— ``words`` 參數僅為舊介面
    相容，會被忽略。``audio_path`` 給定時檢查每個段界 ±80ms 的語音
    能量（切在語音正中間 = 可疑切點）。
    """
    from rapidfuzz import fuzz

    words = plan.words
    kept_norm = "".join(_match_norm(words[wi].text) for wi in plan.kept_word_idx)
    script_norm = "".join(u.norm for u in plan.units)
    coverage = float(fuzz.ratio(kept_norm, script_norm))

    # 稿側缺漏：全域 ratio 會吸收小段刪失（實測半句稿文被裁掉只讓
    # ratio 掉 0.1）— 用 opcodes 逐段抓「稿有、保留沒有」的實質缺漏
    import difflib

    missing_script: list[dict] = []
    sm_ops = difflib.SequenceMatcher(None, script_norm, kept_norm, autojunk=False)
    for tag, i1, i2, j1, j2 in sm_ops.get_opcodes():
        if tag == "delete" and i2 - i1 >= 4:
            missing_script.append({"missing": script_norm[i1:i2][:40]})
        elif tag == "replace" and (i2 - i1) >= (j2 - j1) + 4:
            missing_script.append(
                {"missing": script_norm[i1:i2][:40], "got": kept_norm[j1:j2][:40]}
            )

    duplicated: list[dict] = []
    sel_by_unit = {o.unit_idx: o for o in plan.selected}
    for ui in sel_by_unit:
        unit = plan.units[ui]
        if len(unit.norm) < 8:
            continue
        hits = 0
        L = len(unit.norm)
        step = max(1, L // 3)
        j = 0
        while j <= len(kept_norm) - L:
            if fuzz.ratio(kept_norm[j : j + L], unit.norm) >= 85:
                hits += 1
                j += L
            else:
                j += step
        if hits > 1:
            duplicated.append({"unit_idx": ui, "text": unit.text, "hits": hits})

    def in_cut(t: float) -> bool:
        return any(c.start_sec <= t <= c.end_sec for c in plan.cuts)

    clap_report = []
    for m in ng_markers:
        inside = all(in_cut(t) for t in m.clap_times)
        clap_report.append(
            {"claps": [round(t, 2) for t in m.clap_times], "all_inside_cut": inside}
        )
    claps_outside = [r for r in clap_report if not r["all_inside_cut"]]

    # 拍手貼身殘留：保留字結束在拍手前 3 秒內（且其間無其他保留字）—
    # 拍手極可能否決了它。合法情境（唯一 take、修修默許）也列出供覆核，
    # 一個都不靜默。
    clap_all = sorted(t for m in ng_markers for t in m.clap_times)
    kept_set = set(plan.kept_word_idx)
    kept_words_seq = [(wi, words[wi]) for wi in plan.kept_word_idx]
    kept_damned_groups: list[dict] = []
    for t in clap_all:
        before = [(wi, w) for wi, w in kept_words_seq if w.end <= t]
        if not before:
            continue
        wi, w = before[-1]
        if t - w.end > 3.0:
            continue
        # 拍手與保留字之間若有「被剪掉的字」→ 拍手否決的是那些字，
        # 保留內容無辜，不列
        intervening = any(
            x.end > w.end and x.start < t
            for xi, x in enumerate(words)
            if xi not in kept_set
        )
        if intervening:
            continue
        ctx = "".join(x.text for _, x in before[-8:])
        kept_damned_groups.append(
            {"clap": round(t, 2), "kept_tail": ctx, "delta": round(t - w.end, 2)}
        )

    # 切點能量：段界 ±80ms 的語音頻帶 RMS — 高能量 = 切在語音正中間
    hot_boundaries: list[dict] = []
    if audio_path is not None and plan.kept_segments:
        import numpy as np

        from agents.brook.script_video.cleanup.mistake_removal import (
            _load_wav,
            _lowpass_filter,
        )

        audio, sr = _load_wav(audio_path)
        lpf = _lowpass_filter(audio, sr, 3000.0)
        # ±30ms：只量切點瞬間 — 段首 pad 0.12s 後語音本來就開始，
        # 窗開太大會把正常的相鄰語音也算成 hot
        win = int(0.03 * sr)

        def rms_at(t: float) -> float:
            a = max(0, int(t * sr) - win)
            b = min(len(lpf), int(t * sr) + win)
            seg = lpf[a:b].astype(np.float64)
            return float(np.sqrt(np.mean(seg**2))) if len(seg) else 0.0

        boundaries: list[float] = []
        for i, (s, e) in enumerate(plan.kept_segments):
            if i > 0:
                boundaries.append(s)
            if i < len(plan.kept_segments) - 1:
                boundaries.append(e)
        for t in boundaries:
            r = rms_at(t)
            if r > 0.04:
                hot_boundaries.append({"t": round(t, 2), "rms": round(r, 3)})

    # 殘餘 gap：把每個保留字映射到乾淨 timeline（用 kept_segments 的
    # 累積偏移 — 可證明正確，不依賴 cut 消耗順序），取相鄰字最大間隔。
    # 字尾可能被段界 clamp（懸空字零間隙相連時），映射時夾回段內。
    segs = plan.kept_segments
    max_gap = 0.0
    prev_end_clean: float | None = None
    for wi in plan.kept_word_idx:
        cs = map_clean_ceil(segs, words[wi].start)
        ce = map_clean_floor(segs, words[wi].end)
        if prev_end_clean is not None:
            max_gap = max(max_gap, cs - prev_end_clean)
        prev_end_clean = max(ce, cs)

    return {
        "coverage_ratio": round(coverage, 1),
        "missing_script_content": missing_script,
        "unmatched_units": plan.unmatched_units,
        "duplicated_units": duplicated,
        "ng_markers": len(ng_markers),
        "claps_outside_cut": claps_outside,
        "kept_damned_content": kept_damned_groups,
        "hot_cut_boundaries": hot_boundaries,
        "max_clean_gap_sec": round(max_gap, 2),
        "kept_duration_sec": round(sum(e - s for s, e in plan.kept_segments), 1),
        "n_segments": len(plan.kept_segments),
        "n_cuts": len(plan.cuts),
    }
