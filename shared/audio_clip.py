"""從音檔切出指定時間區間的片段。

用於多模態 LLM（Gemini 2.5 Pro audio）仲裁 ASR uncertain 片段。
依賴系統 ffmpeg（Auphonic pipeline 已依賴），無 Python 套件新增。

切出的片段為 16kHz mono WAV：
- Gemini 內部會降至 16kbps，先降低傳輸量
- Mono 降低 token 用量（雙聲道無助於單人說話片段辨識）
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from shared.log import get_logger

logger = get_logger("nakama.audio_clip")


def get_audio_duration(audio_path: str | Path) -> float:
    """用 ffprobe 取得音檔總長度（秒）。"""
    audio_path = Path(audio_path)
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def extract_clip(
    audio_path: str | Path,
    start_seconds: float,
    end_seconds: float,
    *,
    padding: float = 1.0,
    output_path: str | Path | None = None,
    sample_rate: int = 16000,
) -> Path:
    """從音檔切出 [start-padding, end+padding] 區間的 mono WAV 片段。

    Args:
        audio_path: 來源音檔（任何 ffmpeg 支援格式）
        start_seconds: 片段起點（秒）
        end_seconds: 片段終點（秒）
        padding: 前後各多抓幾秒上下文（預設 1.0 秒）
        output_path: 輸出 WAV 路徑；None 則寫到 tempfile
        sample_rate: 輸出取樣率（預設 16000，Gemini 最佳）

    Returns:
        切出的 WAV 檔路徑

    Raises:
        FileNotFoundError: 來源音檔不存在
        ValueError: end_seconds <= start_seconds
        subprocess.CalledProcessError: ffmpeg 執行失敗
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"音檔不存在：{audio_path}")

    if end_seconds <= start_seconds:
        raise ValueError(f"end_seconds ({end_seconds}) 必須大於 start_seconds ({start_seconds})")

    # 邊界保護
    duration = get_audio_duration(audio_path)
    clip_start = max(0.0, start_seconds - padding)
    clip_end = min(duration, end_seconds + padding)

    if output_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        output_path = Path(tmp.name)
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{clip_start:.3f}",
            "-to",
            f"{clip_end:.3f}",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "wav",
            str(output_path),
        ],
        check=True,
    )

    logger.info(f"切片：{audio_path.name} [{clip_start:.2f}–{clip_end:.2f}s] → {output_path.name}")
    return output_path
