"""LLM-backed ``ClaimExtractor`` for ADR-024 Source Promotion (N519).

Implements the ``ClaimExtractor`` Protocol declared in
``agents.robin.promotion.source_map_builder`` — reads ONE chapter's plain text and returns a
claim-dense ``ClaimExtractionResult`` via a single ``shared.llm.ask`` call.
Wired behind ``NAKAMA_PROMOTION_MODE=llm`` in ``thousand_sunny.promotion_wiring``;
the deterministic twin used in ``dry_run`` mode is ``agents.robin.promotion.dry_run_extractor``.

**Scope.** This extractor serves ``ebook`` + ``inbox_document`` sources. The
``youtube_video`` kind never reaches here: it has a dedicated annotation-based
builder (``agents.robin.promotion.video_source_map_builder``) where the user's own annotations
are the evidence, so the ClaimExtractor pipeline does not apply (see
``agents.robin.promotion.source_map_builder._inspect`` youtube branch).

**Claim-dense, not mirror (ADR-024).** The model is asked for a small set of
factual claims the *source* states, key numbers, figure/table one-liners, and a
few VERBATIM short-quote anchors — never a re-paste of the chapter. The builder
additionally enforces a 30%-of-chapter excerpt budget (B4); this extractor keeps
its own per-field caps so a misbehaving model still yields a bounded result.

**Failure contract.** ``extract`` raises ``ValueError`` (unparseable / malformed
LLM payload) or ``RuntimeError`` (LLM call failed) on documented failures — both
are in the builder's ``_EXTRACTOR_FAILURES`` tuple, so the source routes to the
error/defer state instead of crashing the build. Programmer errors
(``TypeError`` / ``AttributeError``) propagate.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from shared.llm import ask
from shared.log import get_logger
from shared.schemas.source_map import ClaimExtractionResult, QuoteAnchor

logger = get_logger("nakama.robin.source_map_extractor")

# Per-field caps (caller invariants from the ClaimExtractionResult docstring).
# The builder owns the global 30% excerpt budget; these keep a single chapter's
# extraction bounded even if the model over-produces.
_MAX_CLAIM_CHARS = 200
_MAX_CLAIMS = 8
_MAX_KEY_NUMBER_CHARS = 50
_MAX_SUMMARIES = 8
_MAX_QUOTE_CHARS = 800
_MAX_QUOTES = 5
_DEFAULT_QUOTE_CONFIDENCE = 0.6

# Model is resolved by the router (agent/task) when None — mirrors the facade
# convention in ``agents/robin/ingest.py``. Callers may pin a model for tests
# or cost control.
_DEFAULT_MODEL: str | None = None

_SYSTEM_PROMPT = """你是 ADR-024 的「來源主張抽取器」（claim-dense source map extractor）。
你的工作：讀一個章節的純文字，抽出這個章節**作者/來源所陳述的事實主張**，產出精煉的結構化資料。

## 核心原則

- 抽「來源說了什麼」（作者的論點、事實、數據），**不是**讀者的個人觀點或評論。
- **claim-dense，不是全文鏡像**：只挑關鍵主張，不要把整章複述一遍。
- 每條 claim 是一句話、用**來源原本的語言**、聚焦單一論點。

## 輸出欄位

- `claims`：3-5 條關鍵事實主張，每條 ≤80 字、單一論點。
- `key_numbers`：章節中值得記下的數字/統計（逐字，含單位），無則空陣列。
- `figure_summaries` / `table_summaries`：若章節有圖/表，各一行摘述；無則空陣列。
- `short_quotes`：1-3 段**逐字**引文，用來佐證上面的主張。
  - **必須是章節文字裡一字不差的原文片段**（之後會用來定位），不要改寫、不要拼接。
  - 每段 ≤200 字，挑最能代表該主張的句子。
- `extraction_confidence`：0~1，你對這次抽取品質的整體信心。

## 嚴格要求

- 只輸出**純 JSON**，不要 markdown 圍欄、不要任何解釋文字。
- `short_quotes[].excerpt` 一定要能在章節原文中找到一模一樣的子字串。
"""


def _build_user_prompt(chapter_text: str, chapter_title: str, primary_lang: str) -> str:
    title = chapter_title.strip() or "（無標題）"
    return (
        f"章節標題：{title}\n"
        f"來源主要語言：{primary_lang or '未知'}\n\n"
        "章節原文如下（在 <chapter> 標籤內）：\n"
        f"<chapter>\n{chapter_text}\n</chapter>\n\n"
        "請依系統指示，輸出純 JSON，欄位為："
        '{"claims": [...], "key_numbers": [...], "figure_summaries": [...], '
        '"table_summaries": [...], "short_quotes": [{"excerpt": "逐字原文", '
        '"confidence": 0.0~1.0}], "extraction_confidence": 0.0~1.0}'
    )


class LlmClaimExtractor:
    """LLM-backed ``ClaimExtractor`` (one ``shared.llm.ask`` call per chapter).

    Stateless apart from injected config. ``ask_fn`` is injectable so tests can
    drive deterministic responses without any network / API key.
    """

    def __init__(
        self,
        *,
        model: str | None = _DEFAULT_MODEL,
        ask_fn: Callable[..., str] = ask,
        max_tokens: int = 2048,
    ) -> None:
        self._model = model
        self._ask = ask_fn
        self._max_tokens = max_tokens

    def extract(
        self,
        chapter_text: str,
        chapter_title: str,
        primary_lang: str,
    ) -> ClaimExtractionResult:
        text = (chapter_text or "").strip()
        if not text:
            # Empty chapter is low yield, not a failure. The builder turns an
            # empty result into a low_signal_count risk + defer (not an error).
            return ClaimExtractionResult(extraction_confidence=0.0)

        prompt = _build_user_prompt(text, chapter_title, primary_lang)
        try:
            raw = self._ask(
                prompt,
                system=_SYSTEM_PROMPT,
                model=self._model,
                temperature=0.0,
                max_tokens=self._max_tokens,
            )
        except (TypeError, AttributeError):
            # Programmer error (bad call shape) — propagate per builder contract.
            raise
        except Exception as exc:  # noqa: BLE001 — surface as documented failure
            logger.warning(
                "claim extractor LLM call failed",
                extra={"category": "source_map_llm_call_failed", "title": chapter_title},
            )
            raise RuntimeError(f"llm_claim_extract_failed: {type(exc).__name__}: {exc}") from exc

        payload = _parse_json_object(raw)
        return _to_result(payload, chapter_text=chapter_text)


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Extract the first ``{...}`` block from an LLM response and parse it.

    Mirrors the house pattern in ``agents/robin/ingest.py``. Raises
    ``ValueError`` (a builder ``_EXTRACTOR_FAILURES`` member) when no JSON object
    is present or it does not parse / is not an object.
    """
    if not raw or not raw.strip():
        raise ValueError("empty LLM response")
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        raise ValueError("no JSON object found in LLM response")
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM response is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"LLM JSON payload is not an object: {type(parsed).__name__}")
    return parsed


def _clamp01(value: Any, default: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, f))


def _str_list(value: Any, *, max_chars: int, max_items: int) -> list[str]:
    """Coerce ``value`` to a clean list[str]: stringify, strip, truncate, drop
    empties, de-duplicate (order-preserving), cap count."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        s = str(item).strip()[:max_chars]
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _locate(excerpt: str, chapter_text: str) -> str:
    """Best-effort locator for a quote excerpt.

    The extractor cannot compute an EPUB CFI from chapter text alone (#515 owns
    concrete CFI semantics; schema treats the locator as opaque). When the
    excerpt is a verbatim substring we emit a character-offset range; otherwise
    a ``"chapter"`` fallback. Honest about precision rather than fabricating a
    CFI.
    """
    idx = chapter_text.find(excerpt)
    if idx >= 0:
        return f"offset:{idx}-{idx + len(excerpt)}"
    return "chapter"


def _to_result(payload: dict[str, Any], *, chapter_text: str) -> ClaimExtractionResult:
    claims = _str_list(payload.get("claims"), max_chars=_MAX_CLAIM_CHARS, max_items=_MAX_CLAIMS)
    key_numbers = _str_list(
        payload.get("key_numbers"), max_chars=_MAX_KEY_NUMBER_CHARS, max_items=_MAX_SUMMARIES
    )
    figure_summaries = _str_list(
        payload.get("figure_summaries"), max_chars=_MAX_CLAIM_CHARS, max_items=_MAX_SUMMARIES
    )
    table_summaries = _str_list(
        payload.get("table_summaries"), max_chars=_MAX_CLAIM_CHARS, max_items=_MAX_SUMMARIES
    )

    short_quotes: list[QuoteAnchor] = []
    raw_quotes = payload.get("short_quotes")
    if isinstance(raw_quotes, list):
        seen_excerpts: set[str] = set()
        for q in raw_quotes:
            if isinstance(q, dict):
                excerpt = str(q.get("excerpt", "")).strip()
                confidence = _clamp01(q.get("confidence"), _DEFAULT_QUOTE_CONFIDENCE)
            else:
                excerpt = str(q).strip()
                confidence = _DEFAULT_QUOTE_CONFIDENCE
            excerpt = excerpt[:_MAX_QUOTE_CHARS]
            if not excerpt or excerpt in seen_excerpts:
                continue
            seen_excerpts.add(excerpt)
            short_quotes.append(
                QuoteAnchor(
                    excerpt=excerpt,
                    locator=_locate(excerpt, chapter_text),
                    confidence=confidence,
                )
            )
            if len(short_quotes) >= _MAX_QUOTES:
                break

    return ClaimExtractionResult(
        claims=claims,
        key_numbers=key_numbers,
        figure_summaries=figure_summaries,
        table_summaries=table_summaries,
        short_quotes=short_quotes,
        extraction_confidence=_clamp01(payload.get("extraction_confidence"), 0.5),
    )
