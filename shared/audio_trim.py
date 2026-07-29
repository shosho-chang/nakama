"""頭尾靜音偵測與裁切（ffmpeg silencedetect）。

audio-prep pipeline 的一環：Auphonic normalize + Jingle 裁切之後，
把錄音開頭/結尾殘留的靜音段（開錄等待、收尾空白）裁掉。
只動頭尾，不碰中間的停頓——中間靜音是內容的一部分，
且字幕時間軸必須與 ASR 輸入完全一致。

決策邏輯（`decide_trim`）與 silencedetect 輸出解析（`parse_silencedetect`）
是純函式；只有 `trim_edge_silence` 實際呼叫 ffmpeg。
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from shared.auphonic import _get_audio_duration

logger = logging.getLogger(__name__)

# silencedetect 參數預設：Auphonic normalize（-16 LUFS + denoise）後的
# 靜音底噪遠低於 -40 dB；短於 0.8s 的空隙視為語句間停頓不算靜音段。
DEFAULT_NOISE_DB = -40.0
DEFAULT_MIN_SILENCE = 0.8
# 裁切時在語音邊界外保留的緩衝，避免切到起音/尾音
DEFAULT_PADDING = 0.25
# 靜音段起點/終點距離檔案頭/尾多近才視為「邊緣靜音」
EDGE_TOLERANCE = 1.0

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")


def parse_silencedetect(stderr: str) -> list[tuple[float, float | None]]:
    """解析 ffmpeg silencedetect 的 stderr，回傳 (start, end) 列表。

    檔案在靜音中結束時，最後一段只有 silence_start 沒有 silence_end，
    end 以 None 表示。
    """
    spans: list[tuple[float, float | None]] = []
    pending_start: float | None = None
    for line in stderr.splitlines():
        m_start = _SILENCE_START_RE.search(line)
        if m_start:
            pending_start = float(m_start.group(1))
            continue
        m_end = _SILENCE_END_RE.search(line)
        if m_end and pending_start is not None:
            spans.append((pending_start, float(m_end.group(1))))
            pending_start = None
    if pending_start is not None:
        spans.append((pending_start, None))
    return spans


def decide_trim(
    spans: list[tuple[float, float | None]],
    duration: float,
    *,
    padding: float = DEFAULT_PADDING,
    edge_tolerance: float = EDGE_TOLERANCE,
) -> tuple[float, float]:
    """從靜音段列表決定裁切點，回傳 (start, end) 秒。

    - 頭：第一段靜音若從檔案開頭（±edge_tolerance）開始 → 裁到該段結束前 padding
    - 尾：最後一段靜音若延伸到檔案結尾（±edge_tolerance，或 end=None）→ 裁到該段開始後 padding
    - 沒有邊緣靜音 → 回傳 (0, duration) 原樣
    """
    start = 0.0
    end = duration

    if spans:
        first_start, first_end = spans[0]
        if first_start <= edge_tolerance and first_end is not None:
            start = max(0.0, first_end - padding)

        last_start, last_end = spans[-1]
        reaches_tail = last_end is None or last_end >= duration - edge_tolerance
        if reaches_tail and last_start > start:
            end = min(duration, last_start + padding)

    if end <= start:
        logger.warning(f"靜音裁切點異常（start={start:.2f} end={end:.2f}），跳過裁切")
        return 0.0, duration
    return start, end


def detect_edge_silence(
    audio_path: str | Path,
    *,
    noise_db: float = DEFAULT_NOISE_DB,
    min_silence: float = DEFAULT_MIN_SILENCE,
    padding: float = DEFAULT_PADDING,
) -> tuple[float, float, float]:
    """偵測頭尾靜音，回傳 (trim_start, trim_end, duration) 秒，不動檔案。"""
    audio_path = Path(audio_path)
    duration = _get_audio_duration(audio_path)
    cmd = [
        "ffmpeg",
        "-i",
        str(audio_path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    spans = parse_silencedetect(result.stderr or "")
    trim_start, trim_end = decide_trim(spans, duration, padding=padding)
    return trim_start, trim_end, duration


def trim_edge_silence(
    audio_path: str | Path,
    *,
    output_path: str | Path | None = None,
    noise_db: float = DEFAULT_NOISE_DB,
    min_silence: float = DEFAULT_MIN_SILENCE,
    padding: float = DEFAULT_PADDING,
) -> Path:
    """裁掉頭尾靜音，回傳輸出檔路徑。

    沒偵測到可裁的邊緣靜音時直接回傳原檔路徑（不複製）。
    WAV/PCM 用 stream copy 裁切（樣本級精度足夠、零重編碼）。
    """
    audio_path = Path(audio_path)
    trim_start, trim_end, duration = detect_edge_silence(
        audio_path, noise_db=noise_db, min_silence=min_silence, padding=padding
    )

    if trim_start == 0.0 and trim_end >= duration:
        logger.info(f"頭尾無靜音段，跳過裁切: {audio_path.name}")
        return audio_path

    out = (
        Path(output_path) if output_path else audio_path.with_stem(f"{audio_path.stem}_desilenced")
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-ss",
        f"{trim_start:.3f}",
        "-to",
        f"{trim_end:.3f}",
        "-c",
        "copy",
        str(out),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    logger.info(f"靜音裁切完成: 頭 {trim_start:.2f}s / 尾 {duration - trim_end:.2f}s → {out.name}")
    return out
