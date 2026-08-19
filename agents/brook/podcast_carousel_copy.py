"""Evidence-backed Copy Spec generation for Podcast IG Carousel."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shared.schemas.podcast_carousel import (
    CarouselPage,
    CoverPage,
    CTAPage,
    EpisodeMetadata,
    HookPage,
    PodcastCarouselCopySpecV1,
    PointPage,
    QuotePage,
    TranscriptEvidence,
)

_MODEL = "claude-sonnet-4-6"
_SPEAKER_RE = re.compile(r"^\*\*(?P<speaker>[^*]+)\*\*：(?P<text>[\s\S]+)$")
_TIMECODE_RE = re.compile(
    r"(?P<h1>\d+):(?P<m1>\d+):(?P<s1>\d+)[,.](?P<ms1>\d+)\s+-->\s+"
    r"(?P<h2>\d+):(?P<m2>\d+):(?P<s2>\d+)[,.](?P<ms2>\d+)"
)


def _normalise(text: str) -> str:
    return "".join(re.findall(r"[\w\u4e00-\u9fff]", text, flags=re.UNICODE)).lower()


def _seconds(match: re.Match[str], side: int) -> float:
    return (
        int(match.group(f"h{side}")) * 3600
        + int(match.group(f"m{side}")) * 60
        + int(match.group(f"s{side}"))
        + int(match.group(f"ms{side}")) / (10 ** len(match.group(f"ms{side}")))
    )


@dataclass(frozen=True)
class TranscriptBlock:
    block_id: str
    index: int
    speaker: str
    text: str
    t0: float
    t1: float
    line_start: int
    line_end: int


class TranscriptIndex:
    """Exact sequential projection from corrected prose paragraphs to SRT time."""

    def __init__(self, *, source_path: Path, source_sha256: str, blocks: list[TranscriptBlock]):
        self.source_path = source_path
        self.source_sha256 = source_sha256
        self.blocks = blocks
        self.by_id = {block.block_id: block for block in blocks}

    def evidence(self, ids: Iterable[str]) -> list[TranscriptEvidence]:
        values: list[TranscriptEvidence] = []
        for block_id in ids:
            block = self.by_id.get(block_id)
            if block is None:
                raise ValueError(f"unknown transcript evidence block: {block_id}")
            values.append(
                TranscriptEvidence(
                    evidence_id=block.block_id,
                    source_path=str(self.source_path.resolve()),
                    source_sha256=self.source_sha256,
                    speaker=block.speaker,
                    text=block.text,
                    t0=block.t0,
                    t1=block.t1,
                    line_start=block.line_start,
                    line_end=block.line_end,
                    contiguous=True,
                )
            )
        if not values:
            raise ValueError("each page requires at least one evidence block")
        return values

    def quote_evidence(self, ids: list[str], *, guest: str, host: str) -> list[TranscriptEvidence]:
        evidence = self.evidence(ids)
        blocks = [self.by_id[value.evidence_id] for value in evidence]
        if any(block.speaker != guest for block in blocks):
            raise ValueError("guest quote evidence must belong to the guest")
        indexes = [block.index for block in blocks]
        if indexes != sorted(set(indexes)):
            raise ValueError("guest quote evidence IDs must be unique and ordered")
        selected = set(indexes)
        omitted = [
            self.blocks[index]
            for index in range(indexes[0], indexes[-1] + 1)
            if index not in selected
        ]
        if omitted:
            if len(omitted) != 1 or omitted[0].speaker != host:
                raise ValueError("guest quote evidence cannot stitch non-contiguous segments")
            if omitted[0].t1 - omitted[0].t0 > 15:
                raise ValueError("host interruption is too long to preserve one quote segment")
        return evidence

    def prompt_text(self) -> str:
        return "\n\n".join(
            f"[{block.block_id} {format_time(block.t0)}–{format_time(block.t1)}] "
            f"{block.speaker}：{block.text}"
            for block in self.blocks
        )


def format_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int(seconds % 3600 // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_srt(srt: str) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    for raw_block in re.split(r"\n\s*\n", srt.strip()):
        lines = raw_block.splitlines()
        if len(lines) < 3:
            continue
        match = _TIMECODE_RE.fullmatch(lines[1].strip())
        if match is None:
            continue
        cues.append((_seconds(match, 1), _seconds(match, 2), "".join(lines[2:])))
    if not cues:
        raise ValueError("SRT contains no valid cues")
    return cues


def build_transcript_index(prose_path: Path, srt_path: Path) -> TranscriptIndex:
    """Project every corrected speaker paragraph onto exact SRT cue boundaries.

    The prose pipeline only changes punctuation and paragraph grouping, so its
    alphanumeric/CJK character stream must remain an exact ordered substring of
    the corrected SRT. Any mismatch fails closed instead of guessing a time.
    """

    prose = prose_path.read_text(encoding="utf-8")
    cues = _parse_srt(srt_path.read_text(encoding="utf-8-sig"))
    stream_parts: list[str] = []
    cue_spans: list[tuple[int, int, float, float]] = []
    cursor = 0
    for t0, t1, text in cues:
        normalised = _normalise(text)
        if not normalised:
            continue
        stream_parts.append(normalised)
        cue_spans.append((cursor, cursor + len(normalised), t0, t1))
        cursor += len(normalised)
    stream = "".join(stream_parts)

    line_cursor = 1
    search_from = 0
    blocks: list[TranscriptBlock] = []
    for raw_paragraph in re.split(r"\n\s*\n", prose):
        paragraph = raw_paragraph.strip()
        consumed_lines = raw_paragraph.count("\n") + 2
        if not paragraph:
            line_cursor += consumed_lines
            continue
        match = _SPEAKER_RE.fullmatch(paragraph)
        if match is None:
            raise ValueError(f"prose paragraph at line {line_cursor} has no speaker prefix")
        speaker = match.group("speaker").strip()
        text = match.group("text").strip()
        needle = _normalise(text)
        if not needle:
            # Some cleaned transcripts retain punctuation-only speaker turns.
            # They carry no alignable semantic evidence, so skip them without
            # advancing the SRT search cursor or guessing a time range.
            line_cursor += consumed_lines
            continue
        start = stream.find(needle, search_from)
        if start < 0:
            raise ValueError(
                f"cannot align prose paragraph at line {line_cursor}; "
                "transcript evidence fails closed"
            )
        end = start + len(needle)
        overlapping = [span for span in cue_spans if span[0] < end and span[1] > start]
        if not overlapping:
            raise ValueError(f"aligned paragraph at line {line_cursor} has no time range")
        index = len(blocks)
        blocks.append(
            TranscriptBlock(
                block_id=f"B{index + 1:04d}",
                index=index,
                speaker=speaker,
                text=text,
                t0=overlapping[0][2],
                t1=overlapping[-1][3],
                line_start=line_cursor,
                line_end=line_cursor + raw_paragraph.count("\n"),
            )
        )
        search_from = end
        line_cursor += consumed_lines
    if not blocks:
        raise ValueError("prose transcript contains no speaker paragraphs")
    return TranscriptIndex(source_path=prose_path, source_sha256=_sha256(prose_path), blocks=blocks)


class _DraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _DraftPage(_DraftModel):
    page_id: str
    role: Literal["cover", "hook", "point", "quote", "cta"]
    evidence_ids: list[str] = Field(min_length=1)
    headline: str | None = None
    question: str | None = None
    bridge: str | None = None
    body: str | None = None
    text: str | None = None
    emphasis: str
    cutout: str | None = None
    variant: Literal["A", "B"] | None = None
    host_question: str | None = None
    host_question_evidence_ids: list[str] = Field(default_factory=list)
    host_cutout: str | None = None


class _DraftResponse(_DraftModel):
    episode_topic: str = Field(min_length=1)
    variant_override_reason: str | None = None
    pages: list[_DraftPage] = Field(min_length=5, max_length=20)


def _extract_json(raw: str) -> dict[str, Any]:
    matches = re.findall(r"```(?:json)?\s*([\s\S]+?)```", raw)
    return json.loads((matches[-1] if matches else raw).strip())


def _build_prompt(
    *,
    transcript: TranscriptIndex,
    episode: EpisodeMetadata,
    host: str,
    cutouts: list[str],
    editorial_direction: str | None,
    prior_spec: PodcastCarouselCopySpecV1 | None = None,
    revision_findings: list[dict[str, Any]] | None = None,
) -> str:
    direction = editorial_direction or (
        "沒有 social_brief。你必須自行找出能涵蓋整集訪談、適合 IG 受眾的 Episode "
        "Highlight Arc，不得因此停下。"
    )
    revision = ""
    if prior_spec is not None:
        revision = (
            "\n## 前一版 Copy Spec\n"
            + prior_spec.model_dump_json(indent=2)
            + "\n## 已查證 panel findings\n"
            + json.dumps(revision_findings or [], ensure_ascii=False, indent=2)
            + "\n請修正 findings；沒有被 finding 影響且仍然成立的內容應保持穩定。\n"
        )
    return f"""你是《張修修的不正常人類研究所》的資深社群經理。

## 任務
根據完整訪談產生一份、且只有一份 Podcast IG Carousel 主版本。這是可獨立發布的
channel-native asset，不是逐字稿摘要，也不是把模板 placeholder 換字。

## Episode
- EP{episode.number}
- 來賓：{episode.guest_name}／{episode.guest_title}
- 主持人：{host}
- 可用 cutouts：{json.dumps(cutouts, ensure_ascii=False)}

## Editorial direction
{direction}

## 硬規則
- 結構：cover → 一個 hook → ordered points → quote → cta。
- 中段重點數量不鎖 4/6，不湊數；全份最多 20 頁。
- v1 不使用 Re-hook；開頭 Hook 之後直接依閱讀順序列出受眾會感興趣的 points。
- 非引言頁用社群編輯聲音；不要假裝成主持人或來賓未說過的第一人稱。
- 每頁 evidence_ids 只能填下方 transcript block ID；每個主張都要有 evidence。
- emphasis 必須是同頁文案的完整原字串，每頁一處。
- point 的 emphasis 必須出現在 headline；body 不承擔 emphasis。
- EP{episode.number} 預設 quote variant {'A' if episode.number % 2 else 'B'}。
  B 必須使用直接相連的主持人問題與來賓回答；找不到才降級 A，
  並填 variant_override_reason。
- quote 可以縮短順句但不得改原意；不可拼接不同時間的回答。
- hook 使用 question + emphasis + bridge；point 使用 headline + emphasis + body。
- cover 使用 headline + emphasis + guest cutout；quote 使用 text + emphasis + guest cutout，
  B 另填 host_question、host_question_evidence_ids、host_cutout。
- cta 使用 episode_topic + emphasis；三平台由 renderer 固定，不輸出留言互動行。
- 所有 template 示例文字一律不可用。

## 輸出
只輸出純 JSON：
{{
  "episode_topic": "整份 Carousel 的社群編輯主題",
  "variant_override_reason": null,
  "pages": [
    {{"page_id":"cover","role":"cover","headline":"...","emphasis":"...","cutout":"guest_x.png","evidence_ids":["B0001"]}},
    {{"page_id":"hook","role":"hook","question":"...","emphasis":"...","bridge":"...","evidence_ids":["B0001"]}},
    {{"page_id":"point-topic","role":"point","headline":"...","emphasis":"...","body":"...","evidence_ids":["B0001"]}},
    {{"page_id":"quote","role":"quote","variant":"B","text":"...","emphasis":"...","cutout":"guest_x.png","host_question":"...","host_question_evidence_ids":["B0001"],"host_cutout":"host_x.png","evidence_ids":["B0002"]}},
    {{"page_id":"cta","role":"cta","emphasis":"...","evidence_ids":["B0001"]}}
  ]
}}
{revision}
## 完整 evidence transcript
{transcript.prompt_text()}
"""


def _required(value: str | None, field: str, role: str) -> str:
    if not value:
        raise ValueError(f"{role} page requires {field}")
    return value


def _materialise_page(
    draft: _DraftPage,
    *,
    transcript: TranscriptIndex,
    episode: EpisodeMetadata,
    host: str,
) -> CarouselPage:
    evidence = transcript.evidence(draft.evidence_ids)
    common = {"page_id": draft.page_id, "evidence": evidence}
    if draft.role == "cover":
        return CoverPage(
            **common,
            headline=_required(draft.headline, "headline", draft.role),
            emphasis=draft.emphasis,
            guest_name=episode.guest_name,
            guest_title=episode.guest_title,
            cutout=_required(draft.cutout, "cutout", draft.role),
        )
    if draft.role == "hook":
        return HookPage(
            **common,
            question=_required(draft.question, "question", draft.role),
            emphasis=draft.emphasis,
            bridge=_required(draft.bridge, "bridge", draft.role),
        )
    if draft.role == "point":
        return PointPage(
            **common,
            headline=_required(draft.headline, "headline", draft.role),
            emphasis=draft.emphasis,
            body=_required(draft.body, "body", draft.role),
        )
    if draft.role == "quote":
        variant = draft.variant or ("A" if episode.number % 2 else "B")
        guest_evidence = transcript.quote_evidence(
            draft.evidence_ids,
            guest=episode.guest_name,
            host=host,
        )
        kwargs: dict[str, Any] = {}
        if variant == "B":
            host_evidence = transcript.evidence(draft.host_question_evidence_ids)
            if any(value.speaker != host for value in host_evidence):
                raise ValueError("host_question evidence must belong to the host")
            kwargs = {
                "host_question": _required(draft.host_question, "host_question", draft.role),
                "host_question_evidence": host_evidence,
                "host_cutout": _required(draft.host_cutout, "host_cutout", draft.role),
            }
        return QuotePage(
            page_id=draft.page_id,
            variant=variant,
            text=_required(draft.text, "text", draft.role),
            emphasis=draft.emphasis,
            guest_name=episode.guest_name,
            guest_cutout=_required(draft.cutout, "cutout", draft.role),
            evidence=guest_evidence,
            **kwargs,
        )
    return CTAPage(
        **common,
        episode_topic=episode.topic,
        emphasis=draft.emphasis,
    )


def generate_copy_spec(
    *,
    transcript: TranscriptIndex,
    episode_id: str,
    episode: EpisodeMetadata,
    host: str,
    cutouts: list[str],
    revision: str = "r001",
    editorial_direction: str | None = None,
    editorial_direction_path: str | None = None,
    prior_spec: PodcastCarouselCopySpecV1 | None = None,
    revision_findings: list[dict[str, Any]] | None = None,
    llm_call: Callable[[str], str] | None = None,
) -> PodcastCarouselCopySpecV1:
    """Optionally generate and evidence-materialise one primary Copy Spec.

    The canonical agent workflow constructs a fully materialised Copy Spec
    before invoking the deterministic runner. Falling back to Nakama's
    configured external LLM is retained only for callers that explicitly use
    this library API without injecting ``llm_call``. Importing transcript and
    validation utilities therefore never imports an LLM provider.
    """

    prompt = _build_prompt(
        transcript=transcript,
        episode=episode,
        host=host,
        cutouts=cutouts,
        editorial_direction=editorial_direction,
        prior_spec=prior_spec,
        revision_findings=revision_findings,
    )
    if llm_call is None:
        from shared.llm import ask_multi

        def call(value: str) -> str:
            return ask_multi(
                [{"role": "user", "content": value}],
                system=(
                    "你是 Podcast 社群內容編輯。輸出只能是符合指定 schema 的純 JSON；"
                    "不可使用模板 placeholder，不可虛構逐字稿 evidence。"
                ),
                model=_MODEL,
                max_tokens=16384,
            )
    else:
        call = llm_call
    last_error: Exception | None = None
    raw = ""
    for _attempt in range(2):
        attempt_prompt = prompt
        if last_error is not None:
            attempt_prompt = f"{prompt}\n\n前次錯誤：{last_error}\n請修正後重出純 JSON。"
        raw = call(attempt_prompt)
        try:
            draft = _DraftResponse.model_validate(_extract_json(raw))
            resolved_episode = episode.model_copy(update={"topic": draft.episode_topic})
            materialised = [
                _materialise_page(
                    page,
                    transcript=transcript,
                    episode=resolved_episode,
                    host=host,
                )
                for page in draft.pages
            ]
            return PodcastCarouselCopySpecV1(
                episode_id=episode_id,
                revision=revision,
                episode=resolved_episode,
                editorial_direction_path=editorial_direction_path,
                pages=materialised,
                publish_compatibility=(
                    "api_compatible" if len(materialised) <= 10 else "manual_only"
                ),
                variant_override_reason=draft.variant_override_reason,
            )
        except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
            last_error = exc
    raise ValueError(f"Podcast Carousel Copy Spec failed validation: {last_error}; raw={raw[:500]}")
