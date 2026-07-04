"""Script-anchored mistake removal + SRT 文字校正（ADR-015 §Q3 β 路線實現）.

修修 2026-07-04 拍攝工作流：
  - 講錯 → 單擊掌**一次** → 停頓 → 從稿上重講（retake 開頭大致照稿即可）
  - 除 raw video 外另供完整逐字稿（script）

與純音訊版（``mistake_removal.detect_clap_markers``，double-clap）的差異：
拍手在這裡只是「附近有一個 retake 邊界」的訊號，**刪除範圍由文字決定** —
retake 開頭的字元指紋往回模糊比對 WhisperX 字級 transcript，找到失敗 take
的起點。好處：

- 拍手誤偵測（關門聲、敲桌）前後找不到重複文字 → 不產 cut，天然過濾
- 失敗 take 之前的正確內容（即使只隔靜音）文字不同 → 不會被誤刪
- 講到一半發現錯、沒停頓直接拍手 → 音訊 VAD 無解，文字照樣切得開

第二個能力 ``correct_srt``：既然逐字稿存在，WhisperX 的辨識錯字全部換成
稿上的正確寫法 — 時間軸保留 ASR 對齊結果，文字以 script 為準。可選帶入
cuts 把時間戳重映射到 ripple-delete 後的乾淨 timeline。

字級 timestamps 由 ``scripts/run_whisperx_words.py`` 產出（GPU side，修修
本機執行）；本模組純 Python，CI 不碰 GPU。
"""

from __future__ import annotations

import dataclasses
import difflib
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Sequence

from agents.brook.script_video.cleanup.cuts import CutPoint
from shared.fcpxml import merge_ripple_segments

logger = logging.getLogger(__name__)

# retake 開頭指紋長度（normalized 字元數）。太短易誤匹配、太長吃進 retake
# 後面的新內容；10–14 字約當中文一個子句。
_FINGERPRINT_CHARS = 12
# 指紋模糊比對門檻（difflib ratio）。失敗 take 跟 retake 措辭可能小差，
# 但兩者都照稿講，0.7 容得下少量字差又擋得住無關文字。
_MATCH_THRESHOLD = 0.7
# 指紋至少要湊到這麼多字才進行比對（拍手後幾乎沒講話 → 跳過）。
_MIN_FINGERPRINT_CHARS = 6

# retake 前保留的靜音 lead-in（同 mistake_removal 慣例：4 frames @ 30fps）。
_LEAD_IN_FRAMES = 4

# SRT 行長慣例 — 對齊 shared/transcriber.py 的 _MAX_SUBTITLE_CHARS/_HARD。
_MAX_SUBTITLE_CHARS = 14
_MAX_SUBTITLE_HARD = 22

# 標點處理（house style 同 shared/transcriber.py：句中 → 空格、句尾 → 刪）。
_ZH_MID_PUNCTUATION = re.compile(
    "[，、；：（）《》【】…—～·,;:()\\[\\]<>\"'“”‘’「」『』]"
)
_ZH_END_PUNCTUATION = re.compile(r"[。！？!?]|(?<=\S)\.(?=\s|$)")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?\n])")
_CLAUSE_SPLIT = re.compile(r"(?<=[，、；：,;:])")


@dataclasses.dataclass(frozen=True)
class Word:
    """A WhisperX word-level token with absolute timestamps (seconds)."""

    text: str
    start: float
    end: float


def load_words(path: Path) -> list[Word]:
    """Load word-level timestamps from a WhisperX words JSON.

    接受三種形狀（缺 start/end 的 token 直接略過 — align 失敗的殘渣）：

    - ``{"words": [{"word"|"text", "start", "end"}, ...]}``
      （``scripts/run_whisperx_words.py`` 輸出）
    - bare list ``[{...}, ...]``
    - whisperx aligned result ``{"segments": [{"words": [...]}, ...]}``
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if "words" in data:
            raw = data["words"]
        elif "segments" in data:
            raw = [w for seg in data["segments"] for w in seg.get("words", [])]
        else:
            raise ValueError(f"{path}: dict JSON 需有 'words' 或 'segments' key")
    elif isinstance(data, list):
        raw = data
    else:
        raise ValueError(f"{path}: 不支援的 JSON 形狀 {type(data)}")

    words: list[Word] = []
    skipped = 0
    for entry in raw:
        text = (entry.get("word") or entry.get("text") or "").strip()
        start = entry.get("start")
        end = entry.get("end")
        if not text or start is None or end is None:
            skipped += 1
            continue
        words.append(Word(text=text, start=float(start), end=float(end)))
    if skipped:
        logger.info("load_words: %d token(s) 缺 timestamp，略過", skipped)
    words.sort(key=lambda w: w.start)
    return words


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------


def _norm_char(ch: str) -> str | None:
    """Normalize one char for matching; None = 丟棄（標點/空白/符號）."""
    ch = unicodedata.normalize("NFKC", ch).lower()
    if len(ch) != 1:  # NFKC 可能展開成多字元（罕見），保守丟棄
        return None
    if "一" <= ch <= "鿿" or ch.isalnum():
        return ch
    return None


def normalize_text(text: str) -> tuple[str, list[int]]:
    """Return (normalized string, map: normalized index → original index)."""
    chars: list[str] = []
    index_map: list[int] = []
    for i, ch in enumerate(text):
        norm = _norm_char(ch)
        if norm is not None:
            chars.append(norm)
            index_map.append(i)
    return "".join(chars), index_map


@dataclasses.dataclass(frozen=True)
class _TimedChar:
    """One normalized char of the ASR stream with interpolated timestamps."""

    char: str
    start: float
    end: float


def _word_char_stream(words: Sequence[Word]) -> list[_TimedChar]:
    """Flatten words into normalized chars; 每字時間在 word 區間內線性內插."""
    stream: list[_TimedChar] = []
    for w in words:
        norm, _ = normalize_text(w.text)
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
                )
            )
    return stream


# ---------------------------------------------------------------------------
# Cut detection — clap 定位 + retake 指紋回溯
# ---------------------------------------------------------------------------


def detect_script_anchored_cuts(
    words: Sequence[Word],
    clap_times: Sequence[float],
    *,
    fps: int = 30,
    lead_in_frames: int = _LEAD_IN_FRAMES,
    fingerprint_chars: int = _FINGERPRINT_CHARS,
    match_threshold: float = _MATCH_THRESHOLD,
) -> list[CutPoint]:
    """單擊掌 marker + WhisperX 字級 transcript → ripple-delete regions.

    每個拍手時間點 t：

    1. 取 t 之後前 ``fingerprint_chars`` 個 normalized 字元當 retake 指紋
    2. 在「上一個拍手（或 0 秒）→ t」的字元流上滑動視窗做模糊比對，
       找**最早**一個 ratio ≥ ``match_threshold`` 的位置 = 失敗 take 起點
    3. cut = [失敗 take 起點, retake 第一個字 − lead-in]（拍手聲必在區間內）

    找不到重複文字的拍手（誤偵測或特例）→ 不產 cut，log warning 留給
    DaVinci 人工處理。
    """
    lead_in_sec = lead_in_frames / fps
    cuts: list[CutPoint] = []
    claps = sorted(clap_times)

    for i, t in enumerate(claps):
        prev_boundary = claps[i - 1] if i > 0 else 0.0

        after = _word_char_stream([w for w in words if w.start > t])
        before = _word_char_stream(
            [w for w in words if w.end <= t and w.start >= prev_boundary]
        )

        fingerprint = "".join(c.char for c in after[:fingerprint_chars])
        if len(fingerprint) < _MIN_FINGERPRINT_CHARS:
            logger.warning(
                "clap @ %.2fs: 之後語音不足 %d 字，無法建立 retake 指紋 — 不產 cut",
                t,
                _MIN_FINGERPRINT_CHARS,
            )
            continue
        retake_onset = after[0].start

        # 全窗掃描取最佳 ratio（同分取最早）。「最早 ≥ threshold」的貪婪版
        # 會被跨段邊界的部分匹配（前段尾 + 失敗 take 頭湊出 ~0.75+）搶先，
        # 誤吃前面正確內容的尾巴；真正的失敗 take 開頭永遠是全域最佳。
        # 尾端允許短 slice（失敗 take 可能比指紋短 — 講一半就拍手）。
        window = len(fingerprint)
        last_start = max(0, len(before) - _MIN_FINGERPRINT_CHARS)
        match_idx: int | None = None
        match_ratio = 0.0
        for j in range(0, last_start + 1):
            slice_text = "".join(c.char for c in before[j : j + window])
            ratio = difflib.SequenceMatcher(None, slice_text, fingerprint, autojunk=False).ratio()
            if ratio > match_ratio:
                match_idx = j
                match_ratio = ratio
        if match_ratio < match_threshold:
            match_idx = None

        if match_idx is None:
            logger.warning(
                "clap @ %.2fs: 前方 %.1fs–%.1fs 找不到與 retake 開頭「%s」重複的"
                "文字 — 可能是誤偵測（關門/敲桌），不產 cut，留 DaVinci 人工確認",
                t,
                prev_boundary,
                t,
                fingerprint,
            )
            continue

        cut_start = before[match_idx].start
        cut_end = min(
            max(t + 1.0 / fps, retake_onset - lead_in_sec),
            retake_onset,
        )
        cuts.append(
            CutPoint(
                type="ripple-delete",
                start_sec=cut_start,
                end_sec=cut_end,
                reason="alignment-detected",
                confidence=round(match_ratio, 3),
            )
        )
        logger.info(
            "clap @ %.2fs: cut [%.2fs → %.2fs]（指紋「%s」ratio=%.2f）",
            t,
            cut_start,
            cut_end,
            fingerprint,
            match_ratio,
        )

    return cuts


# ---------------------------------------------------------------------------
# Corrected SRT — script 文字 × ASR 時間軸
# ---------------------------------------------------------------------------


def remap_words_through_cuts(
    words: Sequence[Word],
    cuts: Sequence[CutPoint],
    *,
    total_duration_sec: float | None = None,
) -> list[Word]:
    """Drop words inside ripple-delete regions；其餘時間戳映射到乾淨 timeline."""
    if not cuts:
        return list(words)
    total = total_duration_sec if total_duration_sec is not None else max(
        (w.end for w in words), default=0.0
    )
    kept = merge_ripple_segments(
        total, ((c.start_sec, c.end_sec) for c in cuts if c.type == "ripple-delete")
    )

    remapped: list[Word] = []
    cursor = 0.0  # 該 kept segment 在乾淨 timeline 的起點
    for seg_start, seg_end in kept:
        for w in words:
            mid = (w.start + w.end) / 2
            if seg_start <= mid < seg_end:
                shift = cursor - seg_start
                remapped.append(
                    Word(text=w.text, start=w.start + shift, end=w.end + shift)
                )
        cursor += seg_end - seg_start
    remapped.sort(key=lambda w: w.start)
    return remapped


def _split_script_cues(script_text: str) -> list[str]:
    """逐字稿 → 字幕 cue 文字列表（句拆 → 子句拆 → hard slice）."""
    cues: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(script_text):
        sentence = sentence.strip()
        if not sentence:
            continue
        pieces = [sentence]
        if len(sentence) > _MAX_SUBTITLE_CHARS:
            pieces = [p.strip() for p in _CLAUSE_SPLIT.split(sentence) if p.strip()]
        for piece in pieces:
            while len(piece) > _MAX_SUBTITLE_HARD:
                cues.append(piece[:_MAX_SUBTITLE_CHARS])
                piece = piece[_MAX_SUBTITLE_CHARS:]
            if piece:
                cues.append(piece)
    return cues


def _clean_cue_text(text: str) -> str:
    """House style：句中標點 → 空格（collapse）、句尾標點刪除."""
    text = _ZH_END_PUNCTUATION.sub("", text)
    text = _ZH_MID_PUNCTUATION.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _seconds_to_srt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    if ms == 1000:  # 進位保護
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def correct_srt(
    words: Sequence[Word],
    script_text: str,
    *,
    cuts: Sequence[CutPoint] | None = None,
    total_duration_sec: float | None = None,
) -> str:
    """WhisperX 字級時間 × 逐字稿 → 文字全對、時間軸沿用 ASR 的 SRT.

    對齊：ASR normalized 字元流 ↔ script normalized 字元流跑
    ``difflib.SequenceMatcher``；script 每字取對齊到的 ASR 字元時間，
    未對齊字元（ASR 漏字/錯字區）由鄰近字元內插。

    ``cuts`` 給定時：先把 cut 區域內的 words 丟棄並將其餘時間戳
    ripple 映射到乾淨 timeline — 輸出即為 DaVinci 出片後乾淨檔的字幕。
    """
    active_words = (
        remap_words_through_cuts(words, cuts, total_duration_sec=total_duration_sec)
        if cuts
        else list(words)
    )
    stream = _word_char_stream(active_words)
    asr_norm = "".join(c.char for c in stream)
    script_norm, script_map = normalize_text(script_text)
    if not asr_norm or not script_norm:
        raise ValueError("correct_srt: ASR words 或 script 為空，無法對齊")

    # script normalized index → (start, end)；未對齊 = None
    times: list[tuple[float, float] | None] = [None] * len(script_norm)
    sm = difflib.SequenceMatcher(None, asr_norm, script_norm, autojunk=False)
    matched = 0
    for block in sm.get_matching_blocks():
        for k in range(block.size):
            tc = stream[block.a + k]
            times[block.b + k] = (tc.start, tc.end)
        matched += block.size
    coverage = matched / len(script_norm)
    if coverage < 0.5:
        logger.warning(
            "correct_srt: script 對齊覆蓋率僅 %.0f%% — 逐字稿跟錄音內容差異大，"
            "輸出時間軸可信度低",
            coverage * 100,
        )

    # 內插未對齊字元：取前一個已知 end 與後一個已知 start 的線性插值
    prev_end = 0.0
    for j in range(len(times)):
        if times[j] is not None:
            prev_end = times[j][1]
            continue
        nxt = next((times[k] for k in range(j + 1, len(times)) if times[k]), None)
        nxt_start = nxt[0] if nxt else prev_end
        mid = (prev_end + max(prev_end, nxt_start)) / 2
        times[j] = (mid, mid)

    # 原始 script 索引 → normalized 索引（cue 邊界換算用）
    orig_to_norm: dict[int, int] = {orig: j for j, orig in enumerate(script_map)}

    lines: list[str] = []
    seq = 0
    cursor = 0  # 目前掃到的原始 script 位置
    prev_cue_end = 0.0
    for cue_text in _split_script_cues(script_text):
        idx = script_text.find(cue_text, cursor)
        if idx < 0:
            idx = cursor
        cursor = idx + len(cue_text)

        norm_indices = [
            orig_to_norm[i]
            for i in range(idx, idx + len(cue_text))
            if i in orig_to_norm
        ]
        display = _clean_cue_text(cue_text)
        if not norm_indices or not display:
            continue
        start = times[norm_indices[0]][0]
        end = times[norm_indices[-1]][1]
        start = max(start, prev_cue_end)  # 單調保護
        end = max(end, start)
        prev_cue_end = end

        seq += 1
        lines.append(
            f"{seq}\n{_seconds_to_srt_ts(start)} --> {_seconds_to_srt_ts(end)}\n{display}\n"
        )

    return "\n".join(lines)
