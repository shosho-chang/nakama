"""SRT 字幕時間戳對齊工具。

用途：把時間戳跑掉的 SRT 字幕檔重新對齊到音檔。

支援兩種對齊策略：
1. 固定位移（shift）：`t_new = t_old + offset_s`
2. 線性變換（linear）：`t_new = a * t_old + b`（同時修常數偏移與速率漂移）

Auto 模式：呼叫 WhisperX 重新辨識音檔，把 SRT 文字比對到 ASR 片段，
用最小平方法解線性變換參數。文字比對用 char-level SequenceMatcher。
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from shared.log import get_logger

logger = get_logger("nakama.srt_align")


_TS_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)
_SRT_TS_FMT = "{h:02d}:{m:02d}:{s:02d},{ms:03d}"


@dataclass
class Cue:
    """SRT 中的一個字幕區段。"""

    seq: int
    start_s: float
    end_s: float
    text: str  # 可能含換行

    def with_times(self, start_s: float, end_s: float) -> Cue:
        return Cue(self.seq, max(0.0, start_s), max(0.0, end_s), self.text)


def _ts_to_seconds(h: int, m: int, s: int, ms: int) -> float:
    return h * 3600 + m * 60 + s + ms / 1000.0


def _seconds_to_srt_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    h = total_ms // 3_600_000
    m = (total_ms // 60_000) % 60
    s = (total_ms // 1000) % 60
    ms = total_ms % 1000
    return _SRT_TS_FMT.format(h=h, m=m, s=s, ms=ms)


def parse_srt(srt_content: str) -> list[Cue]:
    """解析 SRT 內容 → Cue 清單。容忍空行與 BOM。"""
    if srt_content.startswith("﻿"):
        srt_content = srt_content.lstrip("﻿")

    cues: list[Cue] = []
    lines = srt_content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.isdigit() and i + 1 < len(lines):
            match = _TS_RE.search(lines[i + 1])
            if match:
                seq = int(line)
                start_s = _ts_to_seconds(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                    int(match.group(4).ljust(3, "0")),
                )
                end_s = _ts_to_seconds(
                    int(match.group(5)),
                    int(match.group(6)),
                    int(match.group(7)),
                    int(match.group(8).ljust(3, "0")),
                )
                text_lines: list[str] = []
                j = i + 2
                while j < len(lines) and lines[j].strip():
                    text_lines.append(lines[j])
                    j += 1
                cues.append(Cue(seq, start_s, end_s, "\n".join(text_lines)))
                i = j + 1
                continue
        i += 1
    return cues


def format_srt(cues: list[Cue]) -> str:
    """把 Cue 清單序列化回 SRT 字串。"""
    parts: list[str] = []
    for c in cues:
        parts.append(str(c.seq))
        parts.append(f"{_seconds_to_srt_ts(c.start_s)} --> {_seconds_to_srt_ts(c.end_s)}")
        parts.append(c.text)
        parts.append("")
    return "\n".join(parts)


def apply_linear(cues: list[Cue], a: float, b: float) -> list[Cue]:
    """對每個 cue 套用 `t_new = a * t_old + b`。"""
    out: list[Cue] = []
    for c in cues:
        out.append(c.with_times(a * c.start_s + b, a * c.end_s + b))
    return out


def apply_shift(cues: list[Cue], offset_s: float) -> list[Cue]:
    """固定位移（等同於 apply_linear(a=1, b=offset_s)）。"""
    return apply_linear(cues, 1.0, offset_s)


# ─── Auto-detect：以 ASR 為真值 ──────────────────────────────────────

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_NORMALIZE_RE = re.compile(r"[\s，、。！？；：,.!?;:\"'「」『』（）()《》【】…—~～·]+")


def _normalize_for_match(text: str) -> str:
    """比對前正規化：去 HTML 標籤、標點、空白，保留中英數字元。"""
    text = _HTML_TAG_RE.sub("", text)
    return _NORMALIZE_RE.sub("", text).lower()


@dataclass
class AsrSegment:
    start_s: float
    end_s: float
    text: str


def run_asr_segments(
    audio_path: str | Path,
    *,
    asr_model: str = "large-v3",
    hotwords: list[str] | None = None,
) -> list[AsrSegment]:
    """跑 WhisperX 取得 per-sentence 時間戳與文字（已簡轉繁）。

    直接復用 shared.transcriber 的 model singleton。
    回傳的 text 經過 OpenCC 轉為繁體中文，以免與手工字幕做 char-level 比對時被字形差拉低。
    """
    from shared.transcriber import (
        _build_initial_prompt,
        _get_asr_model,
        _to_traditional,
    )

    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"音檔不存在：{audio_path}")

    import whisperx

    initial_prompt = _build_initial_prompt(hotwords or [], None)
    model = _get_asr_model(asr_model, initial_prompt=initial_prompt)

    logger.info(f"ASR 辨識中（{audio_path.name}，模型 {asr_model}）")
    audio = whisperx.load_audio(str(audio_path))
    result = model.transcribe(audio, batch_size=16)

    segments: list[AsrSegment] = []
    for seg in result.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        segments.append(
            AsrSegment(
                start_s=float(seg["start"]),
                end_s=float(seg["end"]),
                text=_to_traditional(text),
            )
        )

    logger.info(f"ASR 取得 {len(segments)} 個片段")
    return segments


@dataclass
class Match:
    cue: Cue
    asr: AsrSegment
    ratio: float


def _substring_ratio(short: str, long: str) -> float:
    """Hand cue 常是 ASR segment 的子字串。用最長共同子字串覆蓋率評分。

    ratio = LCS(short, long) / len(short)。對 hand-in-asr 情境特別準。
    短字串 < 2 字直接回 0（無意義）。
    """
    if len(short) < 2 or not long:
        return 0.0
    matcher = difflib.SequenceMatcher(None, short, long, autojunk=False)
    m = matcher.find_longest_match(0, len(short), 0, len(long))
    return m.size / len(short)


def _best_match(
    cue_norm: str,
    asr_segs: list[AsrSegment],
    asr_norms: list[str],
    guess_center_s: float,
    window_s: float,
) -> tuple[int, float] | None:
    """在 [center - window, center + window] 區間內找文字最相似的 ASR 片段。

    評分：max(SequenceMatcher.ratio, substring_coverage)。
    短 hand cue 幾乎必是 ASR segment 子字串，所以子字串覆蓋率更準。
    """
    best_idx = -1
    best_ratio = 0.0
    for i, seg in enumerate(asr_segs):
        if seg.start_s < guess_center_s - window_s:
            continue
        if seg.start_s > guess_center_s + window_s:
            break
        asr_norm = asr_norms[i]
        if not asr_norm:
            continue
        seq_ratio = difflib.SequenceMatcher(None, cue_norm, asr_norm).ratio()
        # 子字串覆蓋率：短的塞進長的，看多少比例有 match
        if len(cue_norm) <= len(asr_norm):
            sub_ratio = _substring_ratio(cue_norm, asr_norm)
        else:
            sub_ratio = _substring_ratio(asr_norm, cue_norm)
        ratio = max(seq_ratio, sub_ratio)
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = i
    if best_idx < 0:
        return None
    return best_idx, best_ratio


def match_cues_to_asr(
    cues: list[Cue],
    asr_segs: list[AsrSegment],
    *,
    ratio_threshold: float = 0.7,
    window_s: float = 45.0,
) -> list[Match]:
    """把每個 SRT cue 對應到文字最像的 ASR 片段（在時間視窗內搜尋）。

    window_s：搜尋視窗半徑。視窗中心以目前推估的線性變換預測。
    先用中位數偏移做初估，再用回歸精化。
    """
    if not cues or not asr_segs:
        return []

    asr_norms = [_normalize_for_match(s.text) for s in asr_segs]
    cue_norms = [_normalize_for_match(c.text) for c in cues]

    # Pass 1：先用大視窗（不設中心偏移），抓高信心錨點估初始偏移
    anchors: list[tuple[float, float]] = []  # (cue_start, asr_start)
    for cue, cue_norm in zip(cues, cue_norms):
        if len(cue_norm) < 4:
            continue
        result = _best_match(
            cue_norm,
            asr_segs,
            asr_norms,
            guess_center_s=cue.start_s,
            window_s=window_s * 2,
        )
        if result is None:
            continue
        idx, ratio = result
        if ratio >= 0.85:
            anchors.append((cue.start_s, asr_segs[idx].start_s))

    if anchors:
        offsets = sorted(a[1] - a[0] for a in anchors)
        median_offset = offsets[len(offsets) // 2]
        logger.info(f"Pass 1 錨點 {len(anchors)} 個，初估中位偏移 {median_offset:+.2f}s")
    else:
        median_offset = 0.0
        logger.warning("Pass 1 找不到高信心錨點，初估偏移設為 0")

    # Pass 2：以初估偏移為中心，在較小視窗內收集所有可用匹配
    matches: list[Match] = []
    for cue, cue_norm in zip(cues, cue_norms):
        if len(cue_norm) < 2:
            continue
        result = _best_match(
            cue_norm,
            asr_segs,
            asr_norms,
            guess_center_s=cue.start_s + median_offset,
            window_s=window_s,
        )
        if result is None:
            continue
        idx, ratio = result
        if ratio < ratio_threshold:
            continue
        matches.append(Match(cue=cue, asr=asr_segs[idx], ratio=ratio))

    logger.info(f"Pass 2 匹配 {len(matches)} 對（threshold={ratio_threshold}）")
    return matches


@dataclass
class FitResult:
    a: float  # slope
    b: float  # intercept (seconds)
    n: int
    r_squared: float
    residual_std_s: float

    @property
    def is_pure_shift(self) -> bool:
        """slope 很接近 1 就當成純位移。"""
        return abs(self.a - 1.0) < 5e-4  # <0.05% 速率差


def fit_linear(matches: list[Match]) -> FitResult:
    """最小平方法：asr_start = a * cue_start + b。"""
    if len(matches) < 2:
        raise ValueError(f"匹配數 {len(matches)} < 2，無法擬合")

    xs = [m.cue.start_s for m in matches]
    ys = [m.asr.start_s for m in matches]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    a = sxy / sxx if sxx else 1.0
    b = mean_y - a * mean_x

    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    residuals = [y - (a * x + b) for x, y in zip(xs, ys)]
    ss_res = sum(r * r for r in residuals)
    r_squared = 1 - ss_res / ss_tot if ss_tot else 1.0
    residual_std_s = (ss_res / n) ** 0.5

    return FitResult(
        a=a,
        b=b,
        n=n,
        r_squared=r_squared,
        residual_std_s=residual_std_s,
    )


@dataclass
class RetimeStats:
    total: int
    matched: int
    interpolated: int
    unchanged: int
    offset_min_s: float
    offset_median_s: float
    offset_max_s: float


def retime_cues_from_asr(
    cues: list[Cue],
    asr_segs: list[AsrSegment],
    *,
    ratio_threshold: float = 0.5,
    window_s: float = 8.0,
    min_cue_chars: int = 2,
) -> tuple[list[Cue], list[Match], RetimeStats]:
    """把 ASR 的時間戳逐 cue 轉移到 hand SRT 上。

    演算法：
    1. 對每個 hand cue，在 ±window_s 內找文字最像（正規化+去簡繁差後）的 ASR segment。
       相似度 ≥ threshold → 標記為 matched，採用 ASR 的 start/end。
       結束時間取 `min(asr.end, start + original_duration, next_cue.start - 1ms)`，
       保證每個 cue 的顯示長度與原本接近、且不與後續 cue 重疊。
    2. 沒匹配到的 cue → 用前後最近的 matched cue 做線性內插（依原 start 比例）。
    3. 序列末尾若還有未匹配者，用「最後一個 matched offset」平移。
    """
    if not cues:
        return [], [], RetimeStats(0, 0, 0, 0, 0.0, 0.0, 0.0)

    asr_norms = [_normalize_for_match(s.text) for s in asr_segs]
    matches: dict[int, Match] = {}  # cue index → Match

    for i, cue in enumerate(cues):
        cue_norm = _normalize_for_match(cue.text)
        if len(cue_norm) < min_cue_chars:
            continue
        result = _best_match(
            cue_norm,
            asr_segs,
            asr_norms,
            guess_center_s=cue.start_s,
            window_s=window_s,
        )
        if result is None:
            continue
        idx, ratio = result
        if ratio >= ratio_threshold:
            matches[i] = Match(cue=cue, asr=asr_segs[idx], ratio=ratio)

    if not matches:
        raise RuntimeError(
            f"完全沒有匹配（window=±{window_s}s, threshold={ratio_threshold}）。"
            f"試試增大 window 或降 threshold。"
        )

    # 收集每個 matched cue 的 new start（直接採 ASR start）
    # offsets[i] = new_start - old_start
    old_starts = [c.start_s for c in cues]
    new_starts: list[float | None] = [None] * len(cues)
    for i, m in matches.items():
        new_starts[i] = m.asr.start_s

    # 前後鄰居線性內插
    matched_indices = sorted(matches.keys())
    first_matched = matched_indices[0]
    last_matched = matched_indices[-1]

    # 頭端：第一個 match 之前的 cue，用第一個 match 的 offset 平移
    head_offset = new_starts[first_matched] - old_starts[first_matched]
    for i in range(first_matched):
        new_starts[i] = old_starts[i] + head_offset

    # 中段：相鄰兩個 match 之間的 cue，用兩端 offset 依 old_start 線性內插
    for a, b in zip(matched_indices, matched_indices[1:]):
        if b - a <= 1:
            continue
        off_a = new_starts[a] - old_starts[a]
        off_b = new_starts[b] - old_starts[b]
        span = old_starts[b] - old_starts[a]
        if span <= 0:
            for i in range(a + 1, b):
                new_starts[i] = old_starts[i] + off_a
            continue
        for i in range(a + 1, b):
            t = (old_starts[i] - old_starts[a]) / span
            off = off_a + (off_b - off_a) * t
            new_starts[i] = old_starts[i] + off

    # 尾端：最後 match 之後的 cue，用最後 match 的 offset 平移
    tail_offset = new_starts[last_matched] - old_starts[last_matched]
    for i in range(last_matched + 1, len(cues)):
        new_starts[i] = old_starts[i] + tail_offset

    # 組新 cue：end = min(matched-ASR-end OR start+original_duration, next_start - 1ms)
    out: list[Cue] = []
    for i, cue in enumerate(cues):
        new_start = new_starts[i]
        duration = cue.end_s - cue.start_s
        if i in matches:
            # 取原時長與 ASR 時長較大者，但不超出下一 cue
            asr_dur = matches[i].asr.end_s - matches[i].asr.start_s
            new_end = new_start + max(duration, asr_dur)
        else:
            new_end = new_start + duration
        if i + 1 < len(cues) and new_starts[i + 1] is not None:
            new_end = min(new_end, new_starts[i + 1] - 0.001)
        if new_end < new_start:
            new_end = new_start
        out.append(cue.with_times(new_start, new_end))

    # 統計
    total = len(cues)
    n_match = len(matches)
    n_interp = total - n_match
    offsets_all = [ns - os for ns, os in zip(new_starts, old_starts) if ns is not None]
    offsets_all.sort()
    n = len(offsets_all)
    stats = RetimeStats(
        total=total,
        matched=n_match,
        interpolated=n_interp,
        unchanged=0,
        offset_min_s=offsets_all[0] if n else 0.0,
        offset_median_s=offsets_all[n // 2] if n else 0.0,
        offset_max_s=offsets_all[-1] if n else 0.0,
    )

    match_list = [matches[i] for i in matched_indices]
    return out, match_list, stats


def detect_transform(
    srt_path: str | Path,
    audio_path: str | Path,
    *,
    asr_model: str = "large-v3",
    ratio_threshold: float = 0.7,
    window_s: float = 45.0,
    hotwords: list[str] | None = None,
) -> tuple[FitResult, list[Match]]:
    """端到端：讀 SRT + 跑 ASR + 匹配 + 擬合。"""
    srt_content = Path(srt_path).read_text(encoding="utf-8")
    cues = parse_srt(srt_content)
    if not cues:
        raise ValueError(f"無法解析 SRT 或內容為空：{srt_path}")
    logger.info(f"載入 {len(cues)} 個 SRT cue")

    asr_segs = run_asr_segments(audio_path, asr_model=asr_model, hotwords=hotwords)
    matches = match_cues_to_asr(
        cues,
        asr_segs,
        ratio_threshold=ratio_threshold,
        window_s=window_s,
    )
    if len(matches) < 2:
        raise RuntimeError(
            f"可用匹配只有 {len(matches)} 個，不足以擬合。"
            f"試試降低 --ratio-threshold 或增大 --window。"
        )

    fit = fit_linear(matches)
    return fit, matches
