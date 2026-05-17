"""Brook Line 1b Stage 1 extractor — interview SRT + closed-pool research_pack → typed brief.

ADR-027 §Decision 5 (2b architecture)
-------------------------------------
Line 1b is the **interview + research_pack** flow: 修修 reads a curated pack
of articles / books before the recording, conducts the interview, then sends
the SRT + pack here. A single LLM call produces a canonical
:class:`Line1bStage1Result` which the three channel renderers (blog / fb /
ig) share via ``brief`` for cross-channel voice consistency.

Why 2b (vs 2a one-extractor-per-renderer)?
- Three renderers each interpreting the brief separately drift in voice.
- Token cost ×3, the closed-pool red-line reminder must be repeated 3 times,
  and review surface for 修修 fragments.
- See ADR-027 §"Rejected: Stage 5 repurpose 2b LLM 重活分散到三個 renderer".

Pipeline
--------
1. Load the system prompt template from ``prompts/brook/line1b_extract.md``.
2. Substitute material list (research_pack slugs).
3. Build user message: SRT + closed-pool KB chunks + 修修 style profile.
4. Call Sonnet 4.6 via ``shared.llm.ask_multi``.
5. Strip markdown fences, parse JSON, validate against ``Line1bStage1Result``.
6. Post-process: scan each ``narrative_segment.text`` for citation markers;
   if neither ``[source: ...]`` nor ``[transcript@...]`` is present, set
   ``warning = "⚠️ no_citation"`` on that segment (reminder, not fail).
7. On parse / schema failure: retry once with corrective note; then raise
   ``ValueError`` — Line 1b new contract is fail-loudly per ADR-027.

Reminder discipline (ADR-027 §Decision 6)
-----------------------------------------
- Layer 1 (retrieval): closed_pool_search wrapper restricts KB to
  research_pack ∪ {transcript_slug}. Owned by caller; this extractor does NOT
  hit KB itself — it receives pre-retrieved chunks.
- Layer 2 (prompt): the system prompt explicitly states the knowledge
  restriction. See ``prompts/brook/line1b_extract.md``.
- Layer 3 (citation post-process): implemented here (step 6 above).

All three layers are REMINDERS, not enforcement. Cannot prevent the LLM
from leaking parametric memory; that red line is 修修 self-discipline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from agents.brook.repurpose_engine import EpisodeMetadata, Stage1Result
from shared.llm import ask_multi
from shared.log import get_logger
from shared.schemas.line1b import Line1bStage1Result, NarrativeSegment

logger = get_logger("nakama.brook.line1b_extractor")

_MODEL = "claude-sonnet-4-6"
_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "brook" / "line1b_extract.md"

_CITATION_RE = re.compile(r"\[source:\s*[^\]]+\]|\[transcript@[^\]]+\]")


# ---------------------------------------------------------------------------
# Input container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchPackChunk:
    """One pre-retrieved chunk from the closed-pool research_pack.

    Caller (e.g. orchestrator wiring 1b) is responsible for running
    :func:`shared.repurpose.closed_pool.closed_pool_search` and packaging
    hits into this shape. The extractor takes them as-is.
    """

    slug: str
    """KB path of the source (e.g. ``KB/Wiki/Sources/article-x``)."""

    title: str = ""
    heading: str = ""
    text: str = ""
    language: str = ""
    """ISO-639-1 if known (``zh`` / ``en`` / ...); empty = unknown / let LLM infer."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> str:
    """Strip markdown code fences and return the inner JSON string.

    If multiple fenced blocks exist, prefer the LAST one (LLM convention:
    examples first, real answer last). Falls back to raw text if no fences.

    Mirrors ``agents.brook.line1_extractor._extract_json`` — kept private here
    to avoid a cross-module import for a 5-line helper.
    """
    matches = re.findall(r"```(?:json)?\s*([\s\S]+?)```", text)
    if matches:
        return matches[-1].strip()
    return text.strip()


def _format_pack_chunk(idx: int, chunk: ResearchPackChunk) -> str:
    head = f"### [{idx}] {chunk.slug}"
    meta_bits: list[str] = []
    if chunk.title:
        meta_bits.append(f"title: {chunk.title}")
    if chunk.heading:
        meta_bits.append(f"section: {chunk.heading}")
    if chunk.language:
        meta_bits.append(f"lang: {chunk.language}")
    meta = " — " + " | ".join(meta_bits) if meta_bits else ""
    return f"{head}{meta}\n\n{chunk.text}".rstrip()


def _build_material_list(transcript_slug: str, pack: list[ResearchPackChunk]) -> str:
    """Render the 'your knowledge is restricted to these N materials' list."""
    seen: list[str] = []
    seen_set: set[str] = set()
    # transcript first
    seen.append(f"- transcript: {transcript_slug}")
    for c in pack:
        if c.slug in seen_set:
            continue
        seen_set.add(c.slug)
        label = c.title or c.slug
        seen.append(f"- {c.slug} — {label}")
    return "\n".join(seen)


def _build_user_message(
    *,
    srt_text: str,
    pack: list[ResearchPackChunk],
    style_profile_body: str,
    host: str,
    guest: str | None,
    transcript_slug: str,
) -> str:
    guest_line = (
        f"來賓姓名：{guest}"
        if guest
        else "來賓姓名：（請從 SRT 上下文推斷，並在 quotes.speaker 填入推斷姓名）"
    )
    pack_block = (
        "\n\n".join(_format_pack_chunk(i + 1, c) for i, c in enumerate(pack))
        if pack
        else "（本集無 research_pack — 純訪談）"
    )
    material_list = _build_material_list(transcript_slug, pack)

    return f"""## 主持人：{host}
{guest_line}
## Transcript slug：{transcript_slug}

---

## 素材清單（你的知識被限制在以下 N 份）

{material_list}

---

## 修修風格側寫 (style profile)

{style_profile_body}

---

## Closed-pool research_pack chunks

{pack_block}

---

## 訪談 SRT

{srt_text}

---

請依系統提示輸出純 JSON `Line1bStage1Result`。
"""


def _post_process_citations(result: Line1bStage1Result) -> Line1bStage1Result:
    """Scan each narrative_segment.text; flag any without citation markers.

    Mutates in place via Pydantic (model_copy) and returns. Best-effort — does
    not raise. Per ADR-027 §Decision 6 Layer 3: this is a reminder.
    """
    flagged = 0
    new_segments: list[NarrativeSegment] = []
    for seg in result.narrative_segments:
        if _CITATION_RE.search(seg.text):
            new_segments.append(seg)
        else:
            new_segments.append(
                seg.model_copy(update={"warning": "⚠️ no_citation"})
            )
            flagged += 1
    if flagged:
        logger.info(
            "line1b post-process: flagged %d/%d narrative_segments with no_citation",
            flagged,
            len(result.narrative_segments),
        )
    return result.model_copy(update={"narrative_segments": new_segments})


def _load_prompt_template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class Line1bExtractor:
    """Stage 1 extractor for Line 1b (interview + research_pack) repurpose pipeline.

    Implements the ``Stage1Extractor`` Protocol from
    :mod:`agents.brook.repurpose_engine`. The returned ``Stage1Result.data``
    is the ``model_dump()`` of a validated :class:`Line1bStage1Result`; channel
    renderers running in 1b mode should call
    :meth:`Line1bExtractor.parse_result` (or
    ``Line1bStage1Result.model_validate(stage1.data)``) to get the typed view
    back. See :mod:`shared.schemas.line1b` for the typed schema.

    Pre-condition: ``source_input`` is the raw SRT text. The research_pack
    chunks + style profile + transcript_slug are passed via constructor (this
    extractor is stateful by design — one instance per run is fine and is
    what the orchestrator constructs).

    Cost: one Sonnet 4.6 call per ``extract()``, plus at most one retry on
    parse / schema failure. Then raises (fail-loudly per ADR-027 §Decision 5).
    """

    def __init__(
        self,
        *,
        research_pack: list[ResearchPackChunk],
        style_profile_body: str,
        transcript_slug: str,
        prompt_template: str | None = None,
        model: str | None = None,
    ) -> None:
        self._pack = list(research_pack)
        self._style_profile_body = style_profile_body
        self._transcript_slug = transcript_slug
        self._prompt_template = prompt_template if prompt_template is not None else _load_prompt_template()
        self._model = model or _MODEL

    def extract(self, source_input: str, metadata: EpisodeMetadata) -> Stage1Result:
        """Extract Line 1b typed brief from SRT + pre-loaded research_pack.

        Args:
            source_input: Diarized SRT text.
            metadata: ``EpisodeMetadata``. ``metadata.extra['guest']`` recommended.

        Returns:
            ``Stage1Result`` with ``data = Line1bStage1Result.model_dump()``.

        Raises:
            ValueError: If JSON parsing or schema validation fails after 1 retry.
        """
        guest = metadata.extra.get("guest")
        if guest is None:
            logger.warning(
                "Line 1b: guest name not provided in metadata.extra['guest']; "
                "LLM will infer from SRT context"
            )

        material_list = _build_material_list(self._transcript_slug, self._pack)
        system = self._prompt_template.replace("{material_list}", material_list)

        user_msg = _build_user_message(
            srt_text=source_input,
            pack=self._pack,
            style_profile_body=self._style_profile_body,
            host=metadata.host,
            guest=guest,
            transcript_slug=self._transcript_slug,
        )
        messages: list[dict] = [{"role": "user", "content": user_msg}]

        last_error: Exception | None = None
        attempt_messages = list(messages)
        for attempt in range(2):
            if attempt > 0 and last_error is not None:
                logger.warning(
                    "Line 1b Stage 1 validation failed (attempt %d/2); retrying with corrective note",
                    attempt + 1,
                )
                corrective = (
                    "前一次回應未通過 schema 驗證，錯誤："
                    f"{last_error}\n"
                    "請只輸出純 JSON（不加 markdown fence、不加說明），"
                    "嚴格遵守 Line1bStage1Result schema，包含 narrative_segments / quotes / "
                    "titles / book_context / cross_refs / brief 六個 top-level keys，"
                    "且 narrative_segments 每段尾部附 [source: ...] 或 [transcript@...] citation marker。"
                )
                attempt_messages = [*messages, {"role": "user", "content": corrective}]

            raw = ask_multi(attempt_messages, system=system, model=self._model, max_tokens=8192)
            try:
                typed = self._parse_and_validate(raw)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
                continue

            typed = _post_process_citations(typed)
            return Stage1Result(
                data=typed.model_dump(),
                source_repr=(
                    f"<srt {len(source_input)} chars, pack {len(self._pack)} chunks, "
                    f"slug {self._transcript_slug!r}>"
                ),
            )

        raise ValueError(
            f"Line 1b Stage 1 extraction failed after 2 attempts: {last_error}"
        ) from last_error

    def _parse_and_validate(self, raw: str) -> Line1bStage1Result:
        json_str = _extract_json(raw)
        data = json.loads(json_str)
        return Line1bStage1Result.model_validate(data)

    @staticmethod
    def parse_result(stage1: Stage1Result) -> Line1bStage1Result:
        """Round-trip ``Stage1Result.data`` back to the typed view for renderers.

        Renderers running in Line 1b mode call this once at the top of
        ``render()`` to access ``brief`` and other typed fields safely.
        Raises ``ValidationError`` if the data was not produced by this
        extractor (defensive: catches mis-wired engines).
        """
        return Line1bStage1Result.model_validate(stage1.data)
