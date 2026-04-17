"""多模態仲裁層：對 ASR uncertain 片段用 Gemini 2.5 Pro audio 聽音檔做仲裁。

pipeline：
    SRT + uncertainties (from Opus pass 1)
      → 反查每個 uncertain line 的時間戳
      → audio_clip.extract_clip 切出 ±padding 片段
      → gemini_client.ask_gemini_audio 帶 Pydantic schema 仲裁
      → 回傳 list[ArbitrationVerdict]（按原順序）

設計原則：
- 單一 clip 失敗不中斷整批（產 uncertain verdict + confidence=0）
- tempfile 保證清除（try/finally）
- 平行度可用 GEMINI_MAX_WORKERS env 調整
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from shared.audio_clip import extract_clip
from shared.gemini_client import ask_gemini_audio, set_current_agent
from shared.log import get_logger

logger = get_logger("nakama.multimodal_arbiter")

_SRT_TS_LINE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)

# 短片段補 padding 的門檻（秒）
_SHORT_CLIP_THRESHOLD = 2.0
_LONG_CLIP_PADDING = 1.0
_SHORT_CLIP_PADDING = 2.0


class ArbitrationVerdict(BaseModel):
    """Gemini 聽完音訊後對一個 uncertain 片段的最終裁定。"""

    line: int = Field(description="SRT 序號")
    final_text: str = Field(description="仲裁後最終文字")
    verdict: Literal["keep_original", "accept_suggestion", "other", "uncertain"] = Field(
        description="keep_original=ASR 原文對；accept_suggestion=Opus 建議對；"
        "other=兩個都不對（final_text 是新版）；uncertain=聽不清楚"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0.0–1.0")
    reasoning: str = Field(description="≤50 字理由")


class _GeminiResponse(BaseModel):
    """送給 Gemini 的 schema（不含 line，由 caller 回填）。"""

    final_text: str
    verdict: Literal["keep_original", "accept_suggestion", "other", "uncertain"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


def _parse_ts(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _parse_srt_index(srt_content: str) -> dict[int, tuple[float, float, str]]:
    """解析 SRT → {line: (start_seconds, end_seconds, text)}。"""
    index: dict[int, tuple[float, float, str]] = {}
    lines = srt_content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.isdigit() and i + 1 < len(lines):
            seq = int(line)
            ts_match = _SRT_TS_LINE.search(lines[i + 1])
            if ts_match:
                start = _parse_ts(*ts_match.group(1, 2, 3, 4))
                end = _parse_ts(*ts_match.group(5, 6, 7, 8))
                text_lines = []
                j = i + 2
                while j < len(lines) and lines[j].strip():
                    text_lines.append(lines[j].strip())
                    j += 1
                index[seq] = (start, end, " ".join(text_lines))
                i = j
                continue
        i += 1
    return index


def _choose_padding(start: float, end: float) -> float:
    """短於 2 秒的片段補到 2 秒 padding，其他 1 秒。"""
    if end - start < _SHORT_CLIP_THRESHOLD:
        return _SHORT_CLIP_PADDING
    return _LONG_CLIP_PADDING


def _build_prompt(
    uncertain: dict,
    prev_text: str,
    next_text: str,
    start: float,
    end: float,
) -> tuple[str, str]:
    """組 system + user prompt。回傳 (system, user)。"""
    system = (
        "你是繁體中文（台灣）Podcast 字幕仲裁員。\n"
        "收到一小段音訊（含前後 padding 作為上下文）與兩個候選文字，\n"
        "請仔細聽音訊，判斷哪個候選最貼近實際語音；若兩者都不對，可另擬。\n"
        "\n原則：\n"
        "- 聽不清楚時選 uncertain + 保守採 ASR 原文\n"
        "- reasoning 必須 ≤50 字\n"
        "- 音訊是繁體中文（台灣腔），偶有英文專有名詞"
    )

    original = uncertain.get("original", "")
    suggestion = uncertain.get("suggestion", "")
    reason = uncertain.get("reason", "")

    user = (
        f"時間區段：{start:.2f}–{end:.2f}s（含 padding）\n"
        f"【前文】{prev_text or '（無）'}\n"
        f"【本句】請聽音訊\n"
        f"【後文】{next_text or '（無）'}\n"
        f"\n"
        f"候選 A（ASR 原文）：「{original}」\n"
        f"候選 B（Opus 建議）：「{suggestion}」\n"
        f"Opus 判斷理由：「{reason}」\n"
        f"\n"
        f"請聽音訊後裁定，輸出 JSON：final_text / verdict / confidence / reasoning"
    )
    return system, user


def _fail_verdict(line: int, original: str, reason: str) -> ArbitrationVerdict:
    """失敗時的保守 verdict：保留 ASR 原文、confidence=0。"""
    return ArbitrationVerdict(
        line=line,
        final_text=original,
        verdict="uncertain",
        confidence=0.0,
        reasoning=reason[:100],
    )


def _arbitrate_one(
    audio_path: Path,
    uncertain: dict,
    srt_index: dict[int, tuple[float, float, str]],
    *,
    model: str,
) -> ArbitrationVerdict | None:
    """對單一 uncertain 項目做仲裁。回 None 表示該項應 skip（line 找不到）。"""
    line = uncertain.get("line")
    original = uncertain.get("original", "")
    if line is None or line not in srt_index:
        logger.warning(f"uncertain line={line} 在 SRT 找不到，skip")
        return None

    start, end, _ = srt_index[line]
    prev_text = srt_index.get(line - 1, (0, 0, ""))[2]
    next_text = srt_index.get(line + 1, (0, 0, ""))[2]
    padding = _choose_padding(start, end)

    clip_path: Path | None = None
    try:
        clip_path = extract_clip(audio_path, start, end, padding=padding)
        system, user = _build_prompt(uncertain, prev_text, next_text, start, end)
        result: _GeminiResponse = ask_gemini_audio(
            clip_path,
            user,
            response_schema=_GeminiResponse,
            model=model,
            system=system,
            temperature=0.1,
        )
        return ArbitrationVerdict(
            line=line,
            final_text=result.final_text,
            verdict=result.verdict,
            confidence=result.confidence,
            reasoning=result.reasoning,
        )
    except Exception as e:
        logger.warning(f"line={line} 仲裁失敗：{type(e).__name__}: {e}")
        return _fail_verdict(line, original, f"仲裁失敗：{type(e).__name__}: {e}")
    finally:
        if clip_path is not None:
            clip_path.unlink(missing_ok=True)


def arbitrate_uncertain(
    audio_path: str | Path,
    srt_content: str,
    uncertainties: list[dict],
    *,
    model: str = "gemini-2.5-pro",
    run_id: int | None = None,
) -> list[ArbitrationVerdict]:
    """對所有 uncertain 片段執行多模態仲裁。

    Args:
        audio_path: 來源音檔
        srt_content: FunASR 產出的 SRT（含時間戳）
        uncertainties: Opus pass 1 產出的 [{line, original, suggestion, reason, risk}, ...]
        model: Gemini 模型（預設 gemini-2.5-pro）
        run_id: cost tracking 用

    Returns:
        list[ArbitrationVerdict]，按 uncertainties 原順序（skip 的不在內）。
    """
    if not uncertainties:
        return []

    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"音檔不存在：{audio_path}")

    srt_index = _parse_srt_index(srt_content)
    if not srt_index:
        logger.warning("SRT 解析結果為空，所有 uncertain 都會被 skip")
        return []

    set_current_agent("transcriber-arbiter", run_id=run_id)

    max_workers = int(os.environ.get("GEMINI_MAX_WORKERS", "3"))
    logger.info(f"多模態仲裁：{len(uncertainties)} 個片段，max_workers={max_workers}")

    results: list[ArbitrationVerdict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        # 保序：executor.map 按 iterable 順序回傳
        for verdict in pool.map(
            lambda u: _arbitrate_one(audio_path, u, srt_index, model=model),
            uncertainties,
        ):
            if verdict is not None:
                results.append(verdict)

    skipped = len(uncertainties) - len(results)
    logger.info(f"仲裁完成：{len(results)}/{len(uncertainties)}（skip {skipped}）")
    return results
