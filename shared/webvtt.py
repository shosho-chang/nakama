"""WebVTT 解析與逐字稿正文化（起源於 thousand_sunny av_reader，ADR-035 §D6）。

原本住在 ``thousand_sunny/routers/robin.py``，其註解明載「av_reader 是唯一
consumer；若出現第二個 caller 就 promote 到 ``shared/``」。第二個 caller 已出現：
``agents/robin/ingest.py`` 要把 ``KB/Raw/Videos/{id}.vtt`` 洗成乾淨正文餵 LLM 摘要
（route E 影片 → KB ingest）。因此抽到 ``shared/``，由 av_reader 顯示路徑與 ingest
路徑共用同一份解析邏輯。

抽出（而非讓 ingest 反向 import web router）的關鍵原因：
``thousand_sunny/routers/robin.py`` 本身 ``from agents.robin.ingest import
IngestPipeline``，若 ``agents/robin/ingest.py`` 反過來 import router 會形成
circular import。本模組僅依賴 stdlib（``re`` / ``html``），無任何 nakama 內部相依，
任何層皆可安全 import。
"""

from __future__ import annotations

import html
import re

_VTT_TIME_RE = re.compile(
    r"^(\d{1,2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})\.(\d{3})"
)
_VTT_TAG_RE = re.compile(r"<[^>]+>")
_SENTENCE_END_RE = re.compile(r'[.!?][")\]]*\s*$')
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\'(\[])')
"""Split text on punctuation followed by whitespace + capital letter or
quote. Avoids splitting on common false positives like ``Dr.`` ``Mr.``
``e.g.`` because they're followed by a lowercase word."""
_SENTENCE_MAX_SECONDS = 30.0
"""Safety cap on coalesced-cue duration. Auto-caption punctuation can
miss sentence boundaries when the speaker doesn't pause; flush anyway
once a buffered cue group exceeds this many seconds so the cue list
never grows a single multi-minute brick."""


def vtt_time_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def format_cue_label(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def split_sentences(text: str) -> list[str]:
    """Break a paragraph into sentence-sized chunks."""
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def coalesce_to_sentences(cues: list[dict]) -> list[dict]:
    """Merge consecutive cues until each ends on a sentence-final mark.

    YouTube auto-caption breaks speech every ~2-3s on audio chunks, not
    on sentence boundaries. Coalescing by punctuation gives the cue list
    one entry per complete sentence — easier to read, easier to anchor
    annotations against. Falls back to flushing on ``_SENTENCE_MAX_SECONDS``
    when punctuation never arrives (rambling speech, missing periods).
    """
    if not cues:
        return []
    merged: list[dict] = []
    buf_start: float | None = None
    buf_end: float | None = None
    buf_parts: list[str] = []

    def flush() -> None:
        nonlocal buf_start, buf_end, buf_parts
        if buf_parts and buf_start is not None and buf_end is not None:
            text = " ".join(buf_parts).strip()
            # Split the buffered text into sentences and proportionally
            # distribute the [buf_start, buf_end] window by character
            # count. yt-dlp's word-level ``<HH:MM:SS.ms>`` tags would give
            # exact per-word timing — punt on that until annotation needs
            # it (PR2 #766); linear interpolation is good enough for
            # cue-click seeking.
            sentences = split_sentences(text)
            total_chars = sum(len(s) for s in sentences) or 1
            window = buf_end - buf_start
            cursor = buf_start
            for i, sentence in enumerate(sentences):
                portion = len(sentence) / total_chars
                end = buf_end if i == len(sentences) - 1 else cursor + window * portion
                merged.append(
                    {
                        "start": cursor,
                        "end": end,
                        "label": format_cue_label(cursor),
                        "text": sentence,
                    }
                )
                cursor = end
        buf_start = None
        buf_end = None
        buf_parts = []

    for c in cues:
        if buf_start is None:
            buf_start = c["start"]
        buf_end = c["end"]
        buf_parts.append(c["text"])
        joined = " ".join(buf_parts).strip()
        duration = (buf_end or 0.0) - (buf_start or 0.0)
        if _SENTENCE_END_RE.search(joined) or duration >= _SENTENCE_MAX_SECONDS:
            flush()
    flush()
    return merged


def parse_webvtt(text: str) -> list[dict]:
    """Parse a WebVTT document into a clean cue stream.

    YouTube auto-captions emit a two-cue-per-spoken-line rhythm:
    a 10ms "ghost" cue showing only the carry-over text from the previous
    spoken line, followed by a "real" cue whose body has the carry-over on
    earlier lines and the newly-spoken words (with word-level ``<HH:MM:SS.ms><c>``
    timing tags) on the LAST line.

    To keep the cue list reading like a transcript stream rather than a
    karaoke loop, we take only the last non-empty line of each cue body
    and drop adjacent duplicates.
    """
    cues: list[dict] = []
    cur_start: float | None = None
    cur_end: float | None = None
    cur_lines: list[str] = []

    def flush() -> None:
        nonlocal cur_start, cur_end, cur_lines
        if cur_start is not None and cur_lines:
            last_clean = ""
            for line in reversed(cur_lines):
                # Strip the word-level ``<HH:MM:SS.ms><c>`` timing tags first
                # (they use literal angle brackets), then un-escape WebVTT HTML
                # entities. WebVTT stores ``>`` ``<`` ``&`` as ``&gt;`` ``&lt;``
                # ``&amp;`` in cue bodies — notably the ``&gt;&gt;`` speaker-change
                # marker. Without this the literal ``&gt;&gt;`` survives into
                # ``cue.text`` and Jinja double-escapes it to a visible ``&gt;&gt;``.
                stripped = html.unescape(_VTT_TAG_RE.sub("", line)).strip()
                if stripped:
                    last_clean = stripped
                    break
            if last_clean:
                cues.append(
                    {
                        "start": cur_start,
                        "end": cur_end,
                        "label": format_cue_label(cur_start),
                        "text": last_clean,
                    }
                )
        cur_start = None
        cur_end = None
        cur_lines = []

    for raw in text.splitlines():
        line = raw.rstrip()
        m = _VTT_TIME_RE.match(line)
        if m:
            flush()
            cur_start = vtt_time_to_seconds(m.group(1), m.group(2), m.group(3), m.group(4))
            cur_end = vtt_time_to_seconds(m.group(5), m.group(6), m.group(7), m.group(8))
            continue
        if cur_start is None:
            continue
        if line.strip() == "":
            flush()
            continue
        if line.startswith("WEBVTT") or line.startswith("NOTE") or "-->" in line:
            continue
        cur_lines.append(line)
    flush()

    deduped: list[dict] = []
    for c in cues:
        if deduped and deduped[-1]["text"] == c["text"]:
            deduped[-1]["end"] = c["end"]
            continue
        deduped.append(c)
    return coalesce_to_sentences(deduped)


def webvtt_to_prose(text: str, *, paragraph_chars: int = 600) -> str:
    """把 WebVTT 字幕洗成段落化乾淨正文，供 LLM 摘要 / 概念抽取使用。

    ``parse_webvtt`` 先把字幕合併成「一句一 cue」的乾淨串流（去時間碼、去 karaoke
    重複、un-escape HTML entity）；這裡再把句子累積成約 ``paragraph_chars`` 字的段落、
    以空行（``\\n\\n``）分隔。空行給下游 chunker 自然切點，段落化也讓 LLM 讀得順，
    避免把整支逐字稿塞成單行巨磚。

    malformed / 空字幕 → ``parse_webvtt`` 回空 list → 本函式回空字串，呼叫端可自行
    fallback 原始文字（``webvtt_to_prose(content) or content``）。
    """
    paragraphs: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for cue in parse_webvtt(text):
        sentence = cue["text"].strip()
        if not sentence:
            continue
        buf.append(sentence)
        buf_len += len(sentence) + 1
        if buf_len >= paragraph_chars:
            paragraphs.append(" ".join(buf))
            buf = []
            buf_len = 0
    if buf:
        paragraphs.append(" ".join(buf))
    return "\n\n".join(paragraphs)


def webvtt_to_transcript_markdown(text: str, *, paragraph_chars: int = 600) -> str:
    """把 WebVTT 洗成「時間碼段落」Markdown：每段開頭 ``**[H:MM:SS]**`` + 該段 prose。

    跟 :func:`webvtt_to_prose` 一樣按 ``paragraph_chars`` 把句子聚成段落，但保留每段
    第一個 cue 的時間碼當前綴 —— 人讀可定位影片時刻，LLM 也拿到時間碼當引用錨點
    （Centaur provenance）。用於落地的人讀逐字稿 ``KB/Raw/Videos/{id}.md``。

    malformed / 空字幕 → ``parse_webvtt`` 回空 list → 回空字串。
    """
    paragraphs: list[str] = []
    buf: list[str] = []
    buf_len = 0
    buf_label: str | None = None
    for cue in parse_webvtt(text):
        sentence = cue["text"].strip()
        if not sentence:
            continue
        if not buf:
            buf_label = cue["label"]
        buf.append(sentence)
        buf_len += len(sentence) + 1
        if buf_len >= paragraph_chars:
            paragraphs.append(f"**[{buf_label}]** " + " ".join(buf))
            buf = []
            buf_len = 0
            buf_label = None
    if buf:
        paragraphs.append(f"**[{buf_label}]** " + " ".join(buf))
    return "\n\n".join(paragraphs)
