"""Brook Line 1 Stage 1 extractor — diarized SRT → structured narrative JSON.

Input:  Diarized SRT text with [SPEAKER_XX] labels + EpisodeMetadata
Output: Stage1Result with pydantic-validated JSON matching the 8-segment narrative schema

Pipeline:
1. Build prompt embedding the people.md style profile + SRT text
2. Call Sonnet 4.6 via shared.llm.ask_multi
3. Strip markdown code fences from response
4. Validate JSON with pydantic _Stage1Schema
5. On ValidationError or JSONDecodeError: retry once, then raise ValueError
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from agents.brook.repurpose_engine import EpisodeMetadata, Stage1Result
from shared.llm import ask_multi
from shared.log import get_logger

logger = get_logger("nakama.brook.line1_extractor")

_MODEL = "claude-sonnet-4-6"
_PEOPLE_MD_PATH = Path(__file__).parent / "style-profiles" / "people.md"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class _Quote(BaseModel):
    text: str
    timestamp: str
    speaker: str


class _Stage1Schema(BaseModel):
    hooks: list[str] = Field(min_length=3)
    identity_sketch: str
    origin: str
    turning_point: str
    rebirth: str
    present_action: str
    ending_direction: str
    quotes: list[_Quote] = Field(min_length=5)
    title_candidates: list[str] = Field(min_length=3)
    meta_description: str = Field(min_length=80, max_length=200)
    episode_type: Literal["narrative_journey", "myth_busting", "framework", "listicle"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> str:
    """Strip markdown code fences and return the inner JSON string.

    If multiple fenced blocks exist (e.g. LLM emits an example fence followed
    by the real JSON fence), prefer the LAST one — that is conventionally the
    final answer.  Falls back to the raw text if no fences are present.
    """
    matches = re.findall(r"```(?:json)?\s*([\s\S]+?)```", text)
    if matches:
        return matches[-1].strip()
    return text.strip()


def _build_messages(
    srt_text: str,
    host: str,
    guest: str | None,
    people_md: str,
) -> list[dict]:
    guest_note = (
        f"來賓姓名：{guest}"
        if guest
        else "來賓姓名：（請從 SRT 上下文推斷，並在 speaker 欄位填入推斷的姓名）"
    )
    content = f"""## 主持人：{host}
{guest_note}

---

## 人物文風格側寫

{people_md}

---

## 任務

從以下 Podcast 訪談 SRT 字幕中萃取 Stage 1 結構化敘事素材。

SRT 中有 [SPEAKER_XX] 標籤，請依對話內容判斷哪個 SPEAKER 是主持人（通常問問題），
哪個是來賓（通常回答問題）。在 quotes.speaker 欄位填入真實姓名，不要填 [SPEAKER_XX]。

輸出**純 JSON**（不加 markdown fence 或任何說明文字），符合以下 schema：

{{
  "hooks": ["候選1", "候選2", "候選3"],
  "identity_sketch": "人物身份/成就速寫，建立「這人值得聽」",
  "origin": "追溯起點：童年、原生家庭、求學伏筆",
  "turning_point": "轉折點：一個具體事件/契機/挫敗",
  "rebirth": "重生與突破：如何從谷底爬起、找到定位",
  "present_action": "現在的行動：正在做什麼、影響了誰",
  "ending_direction": "結尾方向：留白+引向 podcast 的方向建議",
  "quotes": [
    {{"text": "來賓原話（短句有力）", "timestamp": "HH:MM:SS", "speaker": "真實姓名"}}
  ],
  "title_candidates": [
    "🧠不正常人類研究所 EP?｜姓名：一句話魅力總結"
  ],
  "meta_description": "150-160 字，含人名、核心主題、podcast CTA",
  "episode_type": "narrative_journey"
}}

約束：
- hooks：3-5 個，每個遵守五種開場 pattern 之一，結尾必有懸念
- quotes：至少 5 個來賓原話，附時間戳，speaker 填真實姓名
- title_candidates：至少 3 個，套用公式 🧠不正常人類研究所 EP?｜{{姓名}}：{{一句話}}
- meta_description：80-200 字，含人名、核心主題、podcast CTA
- episode_type 必須是以下四選一（依本集主敘事結構判斷）：
  - `narrative_journey`：完整人生敘事弧（起點 → 轉折 → 重生 → 現在）— 最常見
  - `myth_busting`：破除誤解或迷思（主敘事是「一般人以為 X，其實 Y」）
  - `framework`：受訪者提出 N 步驟方法論 / 概念架構（敘事繞著一個 framework）
  - `listicle`：列舉清單（N 個秘訣 / 個案 / 心法）— 最少見於人物訪談

---

## SRT 字幕

{srt_text}
"""
    return [{"role": "user", "content": content}]


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class Line1Extractor:
    """Stage 1 extractor for Line 1 (podcast interview) repurpose pipeline.

    Converts diarized SRT with [SPEAKER_XX] labels → structured narrative JSON
    via Sonnet 4.6. Implements the Stage1Extractor Protocol.

    Speaker attribution: host + guest names from EpisodeMetadata are injected
    into the prompt so the LLM can resolve [SPEAKER_XX] to real names in quotes.

    Schema mismatch → auto-retry once, then raises ValueError.
    Cost tracking is automatic via ask_multi → _record_anthropic_usage.
    """

    def __init__(self, *, people_md: str | None = None) -> None:
        self._people_md = (
            people_md if people_md is not None else _PEOPLE_MD_PATH.read_text(encoding="utf-8")
        )

    def extract(self, source_input: str, metadata: EpisodeMetadata) -> Stage1Result:
        """Extract structured narrative materials from diarized SRT.

        Args:
            source_input: Diarized SRT text with [SPEAKER_XX] labels.
            metadata: EpisodeMetadata — host from metadata.host,
                      guest from metadata.extra.get("guest").

        Returns:
            Stage1Result with pydantic-validated JSON data.

        Raises:
            ValueError: If JSON parsing or schema validation fails after 1 retry.
        """
        guest = metadata.extra.get("guest")
        if guest is None:
            logger.warning(
                "guest name not provided in metadata.extra['guest']; "
                "LLM will infer speaker from SRT context"
            )

        messages = _build_messages(source_input, metadata.host, guest, self._people_md)
        system = (
            "你是 Brook，不正常人類研究所 Podcast 的 AI 寫作助手。"
            "任務是從 SRT 字幕萃取結構化敘事素材。輸出只能是純 JSON，不加任何解說文字。"
        )

        last_error: Exception | None = None
        attempt_messages = list(messages)
        for attempt in range(2):
            if attempt > 0 and last_error is not None:
                logger.warning(
                    "Stage 1 schema validation failed (attempt %d/2); retrying with note",
                    attempt + 1,
                )
                # Tell the model exactly what failed so it doesn't repeat the mistake.
                corrective = (
                    "前一次回應未通過 schema 驗證，錯誤："
                    f"{last_error}\n"
                    "請只輸出純 JSON（不加 markdown fence、不加說明文字），"
                    "嚴格遵守上述 schema 與 episode_type 四選一的定義，"
                    "並確保 meta_description 在 80-200 字。"
                )
                attempt_messages = [*messages, {"role": "user", "content": corrective}]
            raw = ask_multi(attempt_messages, system=system, model=_MODEL, max_tokens=4096)
            try:
                data = self._parse_and_validate(raw)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
                continue
            return Stage1Result(
                data=data,
                source_repr=f"<srt {len(source_input)} chars>",
            )

        raise ValueError(
            f"Stage 1 extraction failed after 2 attempts: {last_error}"
        ) from last_error

    def _parse_and_validate(self, raw: str) -> dict:
        """Parse the LLM response as JSON and validate against _Stage1Schema."""
        json_str = _extract_json(raw)
        data = json.loads(json_str)
        schema = _Stage1Schema.model_validate(data)
        return schema.model_dump()
