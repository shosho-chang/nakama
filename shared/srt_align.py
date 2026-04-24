"""SRT 字幕時間戳對齊工具。

用途：把時間戳跑掉的 SRT 字幕檔重新對齊到音檔。

支援兩種對齊策略：
1. 固定位移（shift）：`t_new = t_old + offset_s`
2. 線性變換（linear）：`t_new = a * t_old + b`（同時修常數偏移與速率漂移）

Auto 模式：呼叫 FunASR 重新辨識音檔，把 SRT 文字比對到 ASR 片段，
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


def run_asr_char_timeline(
    audio_path: str | Path,
    *,
    asr_model: str = "paraformer-zh",
    hotwords: list[str] | None = None,
):
    """跑 FunASR 並回傳字級時間軸 AsrCharTimeline（已簡轉繁）。

    用於 retime：每個 hand cue 可對應到 ASR 文字裡的精確字位。
    """
    from shared.transcriber import _get_asr_model, build_char_timeline

    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"音檔不存在：{audio_path}")

    model = _get_asr_model(asr_model)
    hotword_str = " ".join(hotwords) if hotwords else ""

    logger.info(f"ASR 辨識中（{audio_path.name}，模型 {asr_model}，char-level）")
    results = model.generate(input=str(audio_path), batch_size_s=300, hotword=hotword_str)
    timeline = build_char_timeline(results)
    logger.info(f"ASR 字級時間軸：{len(timeline.text)} chars，{timeline.duration_ms / 1000:.1f}s")
    return timeline


def run_asr_segments(
    audio_path: str | Path,
    *,
    asr_model: str = "paraformer-zh",
    hotwords: list[str] | None = None,
) -> list[AsrSegment]:
    """跑 FunASR 取得 per-sentence 時間戳與文字（已簡轉繁）。

    直接復用 shared.transcriber 的 model singleton。
    回傳的 text 經過 OpenCC s2t 轉為繁體中文，以免與手工字幕做 char-level 比對時被字形差拉低。
    """
    from shared.transcriber import (
        _funasr_char_to_ts_idx,
        _get_asr_model,
        _get_ts_values,
        _split_sentences,
        _to_traditional,
    )

    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"音檔不存在：{audio_path}")

    model = _get_asr_model(asr_model)
    hotword_str = " ".join(hotwords) if hotwords else ""

    logger.info(f"ASR 辨識中（{audio_path.name}，模型 {asr_model}）")
    results = model.generate(
        input=str(audio_path),
        batch_size_s=300,
        hotword=hotword_str,
    )

    segments: list[AsrSegment] = []
    for item in results:
        sentence_info = item.get("sentence_info")
        if sentence_info:
            for info in sentence_info:
                s_text = (info.get("text") or "").strip()
                if not s_text:
                    continue
                segments.append(
                    AsrSegment(
                        start_s=info["start"] / 1000.0,
                        end_s=info["end"] / 1000.0,
                        text=_to_traditional(s_text),
                    )
                )
            continue

        text = (item.get("text") or "").strip()
        if not text:
            continue
        timestamps = item.get("timestamp") or []
        if not timestamps:
            continue

        sentences = _split_sentences(text)
        is_char_level = len(timestamps) > len(sentences) * 2
        if is_char_level:
            # FunASR timestamp 只覆蓋可發音字，不能直接用 char_idx 查
            char_to_ts_idx = _funasr_char_to_ts_idx(text, len(timestamps))
            search_from = 0
            for sentence in sentences:
                if not sentence:
                    continue
                pos = text.find(sentence, search_from)
                if pos < 0:
                    stripped = sentence.strip()
                    pos = text.find(stripped, search_from) if stripped else -1
                    if pos >= 0:
                        sentence_len = len(stripped)
                    else:
                        pos = search_from
                        sentence_len = len(sentence)
                else:
                    sentence_len = len(sentence)

                start_char = pos
                end_char = min(pos + sentence_len - 1, len(text) - 1)
                search_from = pos + sentence_len

                ts_start = char_to_ts_idx[start_char]
                ts_end = char_to_ts_idx[end_char]
                start_ms, _ = _get_ts_values(timestamps[ts_start])
                _, end_ms = _get_ts_values(timestamps[ts_end])
                segments.append(
                    AsrSegment(start_ms / 1000.0, end_ms / 1000.0, _to_traditional(sentence))
                )
        elif len(sentences) == len(timestamps):
            for sentence, ts in zip(sentences, timestamps):
                start_ms, end_ms = _get_ts_values(ts)
                segments.append(
                    AsrSegment(start_ms / 1000.0, end_ms / 1000.0, _to_traditional(sentence))
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


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """正規化文字並保留 norm_idx → original_idx 映射。

    去 HTML、標點、空白，中英轉小寫。回傳 (normalized, orig_indices) 滿足
    `text[orig_indices[i]] 的正規化形式 == normalized[i]`。
    """
    text = _HTML_TAG_RE.sub(lambda m: " " * len(m.group(0)), text)
    norm_chars: list[str] = []
    orig_indices: list[int] = []
    for i, ch in enumerate(text):
        if _NORMALIZE_RE.match(ch):
            continue
        norm_chars.append(ch.lower())
        orig_indices.append(i)
    return "".join(norm_chars), orig_indices


def _time_to_norm_window(
    timeline_char_times: list[tuple[int, int]],
    norm_to_orig: list[int],
    center_s: float,
    radius_s: float,
) -> tuple[int, int]:
    """把 [center-radius, center+radius] 秒區間轉成 normalized text 索引範圍。

    用 binary search 在 norm_to_orig 上找對應 char 的時間。
    """
    lo_ms = max(0, int((center_s - radius_s) * 1000))
    hi_ms = int((center_s + radius_s) * 1000)

    # norm_to_orig 是單調的，但 timeline_char_times[orig_idx] 也是單調的
    # 線性掃描（n_cues * n_norm 上限可接受；若變瓶頸再換 bisect）
    lo = 0
    hi = len(norm_to_orig)
    for k, orig_i in enumerate(norm_to_orig):
        start_ms, _ = timeline_char_times[orig_i]
        if start_ms < lo_ms:
            lo = k + 1
        elif start_ms > hi_ms:
            hi = k
            break
    return lo, min(hi, len(norm_to_orig))


def retime_cues_from_asr(
    cues: list[Cue],
    timeline,
    *,
    ratio_threshold: float = 0.5,
    window_s: float = 15.0,
    min_cue_chars: int = 2,
    max_offset_deviation_s: float = 5.0,
) -> tuple[list[Cue], list[Match], RetimeStats]:
    """把 ASR 字級時間戳逐 cue 轉移到 hand SRT 上（char-level alignment）。

    timeline: AsrCharTimeline（`run_asr_char_timeline()` 產生）。

    演算法：
    1. 正規化 timeline.text 與每個 cue.text，保留索引映射。
    2. 每個 cue 在 hand 原 start_s ±window_s 的 normalized 切片裡找：
       - 先試 exact substring（多處出現時挑離 guess 最近的）
       - 否則 SequenceMatcher 的 matching blocks，要求覆蓋率 ≥ threshold
    3. 用 char_times 取對應字元的 (start_ms, end_ms) 當 cue 新時間。
    4. **Outlier filter**：計算全局 median offset，任何 match 的 offset 偏離
       median 超過 max_offset_deviation_s 的，丟棄該 match、改走內插。
       避免短語重複（「你知道」「對」）匹配到幾十秒外同樣短語的鬼影。
    5. 未匹配 cue 用前後鄰居線性內插；頭尾用最近 match 的 offset 平移。
    6. 強制單調遞增：new_start[i] ≥ new_start[i-1] + ε；必要時微調。

    與舊版 sentence-level 實作的關鍵差異：
    - 過去多個 hand cue 都 substring-match 同一個 ASR sentence，全部塌到 sentence.start。
    - 現在每個 cue 對應 ASR 文字裡不同的 char 位置，時間自然分開。
    """
    if not cues:
        return [], [], RetimeStats(0, 0, 0, 0, 0.0, 0.0, 0.0)

    # 基本檢查（避免讓 caller 失誤傳入舊版 list[AsrSegment]）
    if not hasattr(timeline, "text") or not hasattr(timeline, "char_times"):
        raise TypeError(
            "retime_cues_from_asr 已改為接受 AsrCharTimeline；請改用 run_asr_char_timeline()"
        )
    if not timeline.text:
        raise RuntimeError("ASR timeline 是空的，無法 retime")

    asr_norm, asr_norm_to_orig = _normalize_with_map(timeline.text)

    matches: dict[int, Match] = {}  # cue index → Match
    for i, cue in enumerate(cues):
        cue_norm, _ = _normalize_with_map(cue.text)
        if len(cue_norm) < min_cue_chars:
            continue

        lo, hi = _time_to_norm_window(timeline.char_times, asr_norm_to_orig, cue.start_s, window_s)
        if lo >= hi:
            continue
        search_slice = asr_norm[lo:hi]
        guess_ms = cue.start_s * 1000

        def _candidate_distance_ms(norm_pos_in_slice: int) -> float:
            orig_i = asr_norm_to_orig[lo + norm_pos_in_slice]
            return abs(timeline.char_times[orig_i][0] - guess_ms)

        # 1) Exact substring — 掃所有出現處，挑離 guess_time 最近的
        positions: list[int] = []
        search_from = 0
        while True:
            p = search_slice.find(cue_norm, search_from)
            if p < 0:
                break
            positions.append(p)
            search_from = p + 1

        if positions:
            best_pos = min(positions, key=_candidate_distance_ms)
            norm_start = lo + best_pos
            norm_end = norm_start + len(cue_norm) - 1
            ratio = 1.0
        else:
            # 2) Fuzzy：用 SequenceMatcher 的 matching blocks，取覆蓋率夠且最近 guess 的
            matcher = difflib.SequenceMatcher(None, cue_norm, search_slice, autojunk=False)
            blocks = matcher.get_matching_blocks()
            min_size = max(min_cue_chars, int(len(cue_norm) * ratio_threshold))
            viable = [b for b in blocks if b.size >= min_size]
            if not viable:
                continue
            # 優先選 size 大且離 guess 近的（size 先排、再距離平衡）
            best_block = min(
                viable,
                key=lambda b: (-b.size, _candidate_distance_ms(b.b)),
            )
            norm_start = lo + best_block.b
            norm_end = norm_start + best_block.size - 1
            ratio = best_block.size / len(cue_norm)

        orig_start = asr_norm_to_orig[norm_start]
        orig_end = asr_norm_to_orig[norm_end]
        start_ms, _ = timeline.char_times[orig_start]
        _, end_ms = timeline.char_times[orig_end]
        if end_ms <= start_ms:
            end_ms = start_ms + 100  # 保底 100ms

        # 用 AsrSegment 包裝以符合 Match 既有 schema（供 CLI/log 顯示）
        asr_like = AsrSegment(
            start_s=start_ms / 1000.0,
            end_s=end_ms / 1000.0,
            text=timeline.text[orig_start : orig_end + 1],
        )
        matches[i] = Match(cue=cue, asr=asr_like, ratio=ratio)

    if not matches:
        raise RuntimeError(
            f"完全沒有匹配（window=±{window_s}s, threshold={ratio_threshold}）。"
            f"試試增大 window 或降 threshold。"
        )

    # Outlier filter：丟掉偏離全局 median offset 超過門檻的 match
    # （通常是短常用語「你知道」「對」匹配到遠處同字）
    if max_offset_deviation_s > 0 and len(matches) >= 5:
        match_offsets = sorted(m.asr.start_s - m.cue.start_s for m in matches.values())
        median_offset = match_offsets[len(match_offsets) // 2]
        outliers = {
            i
            for i, m in matches.items()
            if abs((m.asr.start_s - m.cue.start_s) - median_offset) > max_offset_deviation_s
        }
        if outliers:
            logger.info(
                f"Outlier 過濾：{len(outliers)} 個 match 偏離 median {median_offset:+.2f}s "
                f"超過 ±{max_offset_deviation_s}s，改走內插"
            )
            for i in outliers:
                del matches[i]
        if not matches:
            raise RuntimeError(
                f"outlier 過濾後無剩餘 match（median={median_offset:+.2f}s, "
                f"dev±{max_offset_deviation_s}s）；試試放寬 max_offset_deviation_s"
            )

    old_starts = [c.start_s for c in cues]
    new_starts: list[float | None] = [None] * len(cues)
    for i, m in matches.items():
        new_starts[i] = m.asr.start_s

    matched_indices = sorted(matches.keys())
    first_matched = matched_indices[0]
    last_matched = matched_indices[-1]

    # 頭端：用第一個 match 的 offset 平移
    head_offset = new_starts[first_matched] - old_starts[first_matched]
    for i in range(first_matched):
        new_starts[i] = old_starts[i] + head_offset

    # 中段：相鄰 match 間依原 start 線性內插
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

    # 尾端
    tail_offset = new_starts[last_matched] - old_starts[last_matched]
    for i in range(last_matched + 1, len(cues)):
        new_starts[i] = old_starts[i] + tail_offset

    # 強制單調遞增（防禦性：若 matched 時間倒退或內插出現倒退，往前推）
    MIN_GAP = 0.05  # 50ms
    for i in range(1, len(new_starts)):
        if new_starts[i] is not None and new_starts[i - 1] is not None:
            min_allowed = new_starts[i - 1] + MIN_GAP
            if new_starts[i] < min_allowed:
                new_starts[i] = min_allowed

    # 組新 cue：保留原時長優先，不與下一 cue 重疊
    out: list[Cue] = []
    for i, cue in enumerate(cues):
        new_start = new_starts[i]
        duration = cue.end_s - cue.start_s
        if i in matches:
            asr_dur = matches[i].asr.end_s - matches[i].asr.start_s
            new_end = new_start + max(duration, asr_dur)
        else:
            new_end = new_start + duration
        if i + 1 < len(cues) and new_starts[i + 1] is not None:
            new_end = min(new_end, new_starts[i + 1] - 0.001)
        # 保底：單調確保 + 最小顯示時長
        if new_end <= new_start:
            new_end = new_start + 0.05
        out.append(cue.with_times(new_start, new_end))

    total = len(cues)
    n_match = len(matches)
    n_interp = total - n_match
    offsets_all = sorted(ns - os for ns, os in zip(new_starts, old_starts) if ns is not None)
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
    asr_model: str = "paraformer-zh",
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
