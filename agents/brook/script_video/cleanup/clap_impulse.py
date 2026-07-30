"""Physical clap detection — 寬頻近滿刻度 impulse + 靜音上下文（cleanup v2）.

取代 ``mistake_removal.detect_single_claps`` 的 3kHz 高頻能量法作為拍手
marker 來源。v1 的失敗模式（2026-07-30「頻道復出」實測）：

- 高頻能量峰值把中文擦音/爆破音（ㄘㄔㄙㄕ）也當拍手 → 167 個候選裡
  ~120 個是語音誤判
- 誤判 marker 把 ``script_align`` 的回溯搜尋窗切碎成 1–5 秒，真拍手
  反而找不到失敗 take → 漏剪

v2 改用拍手的**物理特徵**（同一支素材上量測到的分離）：

- 振幅打到 limiter ceiling（實測 0.87–0.93；語音尖峰多在 0.5–0.7）
- 前後都是靜音（講錯 → 停 → 拍手 → 停 → 重講）；語音頻帶（<3kHz）
  RMS 在拍手前後 <0.01，語音爆破音前後 ~0.06+
- 極窄 impulse（<2ms above half-max）

輸出兩層：

- :func:`detect_claps` — 個別拍手事件（含特徵值，供 QC 報告）
- :func:`merge_ng_markers` — 之間無持續語音的連續拍手合併成單一
  NG 事件（一次 NG 可能連拍多下，實測一個 cluster 有 6 下）

拍手在 v2 pipeline 的角色是**交叉驗證與 ad-lib 裁決錨點**，不再直接
決定剪輯範圍 — 剪輯由 ``script_coverage`` 的逐字稿覆蓋演算法決定。
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import numpy as np

from agents.brook.script_video.cleanup.mistake_removal import (
    _highpass_filter,
    _load_wav,
    _lowpass_filter,
)

logger = logging.getLogger(__name__)

# 拍手候選最低樣本振幅（linear, full scale = 1.0）。實測拍手 0.87–0.93、
# 語音尖峰 p90=0.885 — 單靠振幅分不開，要配合靜音上下文。
_AMP_MIN = 0.75
# 「前後皆靜音」之外，允許單側靜音但振幅必須更強且高頻比例夠高
# （拍完立刻開口重講的 impatient case）。
_AMP_STRONG = 0.85
_HF_RATIO_MIN = 0.25
# 規則一（前後皆靜音）也要求最低高頻比例：被靜音包圍的孤立詞的爆破音
# （實測「掰掰」的ㄅ，hf=0.17）振幅一樣近滿刻度，只有頻譜分得開
# （真拍手實測 hf 0.27–0.74）。
_HF_RATIO_MIN_QUIET = 0.22
# 語音頻帶（LPF 3kHz）RMS 低於此值視為靜音。實測拍手上下文 <0.01、
# 語音上下文 ~0.06+，中間有一個數量級的分離帶。
_VOICE_CTX_MAX = 0.035
# 語音上下文量測窗：impulse 前後 [80ms, 450ms]（避開 impulse 本體與殘響）。
_CTX_NEAR_SEC = 0.08
_CTX_FAR_SEC = 0.45
# cluster 合併時的「持續語音」判定（同 mistake_removal VAD 慣例）。
_VOICE_RMS_THRESHOLD = 0.02
_VOICE_FRAME_SEC = 0.030
_VOICE_MIN_CONSECUTIVE = 3


@dataclasses.dataclass(frozen=True)
class ClapEvent:
    """單一拍手 impulse 與其判定特徵（QC 報告直接引用）。"""

    time_sec: float
    amplitude: float
    hf_ratio: float
    voice_before: float
    voice_after: float


@dataclasses.dataclass(frozen=True)
class NgMarker:
    """一次 NG 事件 = 之間無持續語音的一串拍手。"""

    clap_times: tuple[float, ...]

    @property
    def first_clap(self) -> float:
        return self.clap_times[0]

    @property
    def last_clap(self) -> float:
        return self.clap_times[-1]


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if len(x) else 0.0


def detect_claps(
    audio_path: Path,
    *,
    amp_min: float = _AMP_MIN,
    amp_strong: float = _AMP_STRONG,
    voice_ctx_max: float = _VOICE_CTX_MAX,
    hf_ratio_min: float = _HF_RATIO_MIN,
) -> list[ClapEvent]:
    """偵測拍手 impulse；回傳事件列表（時間遞增）。

    判定規則（or）：

    1. 前後皆靜音 + 振幅 ≥ ``amp_min`` — 標準「停下來拍手」
    2. 單側靜音 + 振幅 ≥ ``amp_strong`` + 高頻比例 ≥ ``hf_ratio_min``
       — 拍完立刻開講（或講到一半直接拍）的 case
    """
    from scipy.signal import find_peaks

    audio, sr = _load_wav(audio_path)
    absa = np.abs(audio)
    lpf = _lowpass_filter(audio, sr, 3000.0)
    hpf = _highpass_filter(audio, sr, 3000.0)

    peak_idx, _ = find_peaks(absa, height=amp_min, distance=int(0.05 * sr))

    near = int(_CTX_NEAR_SEC * sr)
    far = int(_CTX_FAR_SEC * sr)
    hf_win = int(0.010 * sr)

    events: list[ClapEvent] = []
    for p in peak_idx:
        amp = float(absa[p])
        vb = _rms(lpf[max(0, p - far) : max(0, p - near)])
        va = _rms(lpf[p + near : p + far])
        a, b = max(0, p - hf_win), p + hf_win
        denom = _rms(audio[a:b])
        hf = _rms(hpf[a:b]) / denom if denom > 0 else 0.0

        silent_b = vb < voice_ctx_max
        silent_a = va < voice_ctx_max
        is_clap = (silent_b and silent_a and hf >= _HF_RATIO_MIN_QUIET) or (
            amp >= amp_strong and (silent_b or silent_a) and hf >= hf_ratio_min
        )
        if is_clap:
            events.append(
                ClapEvent(
                    time_sec=p / sr,
                    amplitude=round(amp, 3),
                    hf_ratio=round(hf, 3),
                    voice_before=round(vb, 4),
                    voice_after=round(va, 4),
                )
            )

    logger.info(
        "detect_claps: %d impulse candidate(s) ≥%.2f → %d clap(s)",
        len(peak_idx),
        amp_min,
        len(events),
    )
    return events


def _has_sustained_voice(
    lpf: np.ndarray,
    sr: int,
    start_sec: float,
    end_sec: float,
    *,
    rms_threshold: float = _VOICE_RMS_THRESHOLD,
) -> bool:
    """[start, end] 內是否有 ≥3 個連續 30ms 語音窗（cluster 合併判定用）。"""
    frame = max(1, int(sr * _VOICE_FRAME_SEC))
    a = max(0, int(start_sec * sr))
    b = min(len(lpf), int(end_sec * sr))
    consec = 0
    pos = a
    while pos + frame <= b:
        if _rms(lpf[pos : pos + frame]) >= rms_threshold:
            consec += 1
            if consec >= _VOICE_MIN_CONSECUTIVE:
                return True
        else:
            consec = 0
        pos += frame
    return False


def merge_ng_markers(
    audio_path: Path,
    claps: list[ClapEvent],
) -> list[NgMarker]:
    """之間無持續語音的連續拍手合併成單一 NG 事件。

    實測一次 NG 修修可能連拍 2–6 下、間隔可到 5 秒（中間是靜音或
    嘆氣）；只要兩拍之間沒有「持續語音」（≥90ms 連續語音窗）就視為
    同一次 NG。
    """
    if not claps:
        return []
    audio, sr = _load_wav(audio_path)
    lpf = _lowpass_filter(audio, sr, 3000.0)

    markers: list[NgMarker] = []
    cluster: list[float] = [claps[0].time_sec]
    for prev, cur in zip(claps, claps[1:]):
        gap_has_voice = _has_sustained_voice(
            lpf, sr, prev.time_sec + 0.10, cur.time_sec - 0.10
        )
        if gap_has_voice:
            markers.append(NgMarker(clap_times=tuple(cluster)))
            cluster = [cur.time_sec]
        else:
            cluster.append(cur.time_sec)
    markers.append(NgMarker(clap_times=tuple(cluster)))

    logger.info("merge_ng_markers: %d clap(s) → %d NG marker(s)", len(claps), len(markers))
    return markers
