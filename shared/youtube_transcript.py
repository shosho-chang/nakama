"""YouTube transcript fetcher — thin wrapper around ``youtube-transcript-api``.

Used by Nami ``youtube_transcript`` tool when船長丟一個 YouTube URL 要整理摘要 /
chapters / quotes 等。把 transcript 拉下來，後段交給 LLM 依需求轉成不同 format。

設計對齊 ``shared/pubmed_client.py`` 風格：單一檔、專屬 error class、模組級
logger、簡單可測試的純函式。

Dependency: ``youtube-transcript-api`` (requirements.txt)。Import 延遲在
``fetch_transcript`` 內，避免 module-import-time 失敗影響其他 nami tools。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from shared.log import get_logger

logger = get_logger("nakama.shared.youtube_transcript")

_VIDEO_ID_RE = re.compile(r"[a-zA-Z0-9_-]{11}")
_URL_VIDEO_ID_PATTERNS = (
    re.compile(r"(?:youtube\.com/watch\?(?:.*&)?v=)([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:youtu\.be/)([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com/(?:shorts|live|embed)/)([a-zA-Z0-9_-]{11})"),
)


class YouTubeTranscriptError(RuntimeError):
    """Transcript 抓取 / 解析失敗。"""


@dataclass
class TranscriptSegment:
    """單一字幕段落（對齊 youtube-transcript-api 的 dict 輸出）。"""

    text: str
    start: float  # 開始秒數
    duration: float


def parse_video_id(url_or_id: str) -> str:
    """Accept URL (watch / youtu.be / shorts / live / embed) or raw 11-char id.

    Raises ``YouTubeTranscriptError`` if can't extract a video id.
    """
    s = url_or_id.strip()
    if not s:
        raise YouTubeTranscriptError("輸入不能為空")

    for pat in _URL_VIDEO_ID_PATTERNS:
        m = pat.search(s)
        if m:
            return m.group(1)

    # 純 11 字元 id（無 URL 結構）
    if len(s) == 11 and _VIDEO_ID_RE.fullmatch(s):
        return s

    raise YouTubeTranscriptError(f"無法從輸入辨識 YouTube video id：{url_or_id!r}")


def fetch_transcript(
    url_or_id: str,
    *,
    languages: Iterable[str] = ("zh-TW", "zh-Hant", "zh", "en"),
) -> list[TranscriptSegment]:
    """Fetch transcript segments for a YouTube video.

    Args:
        url_or_id: YouTube URL or raw 11-char video id
        languages: 偏好語言序列（首選後備）。預設繁中優先，再 fallback 英文

    Returns:
        list[TranscriptSegment]; 通常數百段，每段 1-10s

    Raises:
        YouTubeTranscriptError: 無 transcript / 影片不存在 / 私人 / 套件未安裝
    """
    video_id = parse_video_id(url_or_id)

    try:
        from youtube_transcript_api import (
            YouTubeTranscriptApi,
        )
    except ImportError as e:
        raise YouTubeTranscriptError(
            "缺少依賴 ``youtube-transcript-api``。"
            "請 ``pip install youtube-transcript-api`` 後再試。"
        ) from e

    try:
        raw = YouTubeTranscriptApi.get_transcript(video_id, languages=list(languages))
    except Exception as e:  # noqa: BLE001 — 套件 raise 多種 exception class
        # 套件有多種 error class（TranscriptsDisabled / NoTranscriptFound / VideoUnavailable
        # / TooManyRequests 等）。最後 fallback：拿任何可用語言。
        logger.warning("preferred-lang transcript failed video=%s err=%s", video_id, e)
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            transcript = next(iter(transcript_list))
            raw = transcript.fetch()
            logger.info(
                "fell back to %s for video %s", transcript.language_code, video_id
            )
        except Exception as e2:  # noqa: BLE001
            raise YouTubeTranscriptError(
                f"無法取得 video {video_id} 的字幕（可能字幕被停用 / 影片不存在 / 被限制）：{e2}"
            ) from e2

    return [
        TranscriptSegment(
            text=item.get("text", ""),
            start=float(item.get("start", 0)),
            duration=float(item.get("duration", 0)),
        )
        for item in raw
    ]


def to_plain_text(segments: list[TranscriptSegment]) -> str:
    """串接成單一 plain text（清掉 [音樂] 之類的 placeholder）。"""
    parts: list[str] = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        # 刪 [音樂] / [掌聲] / (music) 之類
        text = re.sub(r"^\[[^\]]+\]$", "", text)
        text = re.sub(r"^\([^)]+\)$", "", text)
        if text:
            parts.append(text)
    return " ".join(parts)


def to_timestamped_text(segments: list[TranscriptSegment]) -> str:
    """``[mm:ss] text`` 一段一行，方便船長挑章節做摘要。"""
    lines: list[str] = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        m, s = divmod(int(seg.start), 60)
        if m >= 60:
            h, m = divmod(m, 60)
            ts = f"{h:d}:{m:02d}:{s:02d}"
        else:
            ts = f"{m:02d}:{s:02d}"
        lines.append(f"[{ts}] {text}")
    return "\n".join(lines)


def total_duration(segments: list[TranscriptSegment]) -> float:
    """估計影片總長度（最後段 start + duration）。空 list → 0.0。"""
    if not segments:
        return 0.0
    last = segments[-1]
    return last.start + last.duration
