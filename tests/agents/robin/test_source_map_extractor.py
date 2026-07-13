"""Tests for agents.robin.source_map_extractor.LlmClaimExtractor (N519).

The LLM-backed ClaimExtractor behind ``NAKAMA_PROMOTION_MODE=llm``. All tests
inject a fake ``ask_fn`` so no network / API key is touched — they pin the
parse + clamp + locator + failure contract, not model behaviour.
"""

from __future__ import annotations

import json

import pytest

mod = pytest.importorskip("agents.robin.source_map_extractor")
LlmClaimExtractor = mod.LlmClaimExtractor

schema = pytest.importorskip("shared.schemas.source_map")
ClaimExtractionResult = schema.ClaimExtractionResult


def _fake_ask(response: str):
    """Build an ask_fn that records calls and returns a fixed response."""
    calls: list[dict] = []

    def ask_fn(prompt, *, system="", model=None, temperature=None, max_tokens=4096, **kw):
        calls.append(
            {"prompt": prompt, "system": system, "model": model, "temperature": temperature}
        )
        return response

    ask_fn.calls = calls  # type: ignore[attr-defined]
    return ask_fn


_CHAPTER = (
    "財富階梯共有六階，從月光族到影響力自由。作者主張你應該根據財富階層消費，"
    "而非根據收入。第一階的真正問題不在支出，而在收入。"
)


def _valid_payload() -> dict:
    return {
        "claims": [
            "應該根據財富階層消費，而非根據收入。",
            "第一階的真正問題不在支出，而在收入。",
        ],
        "key_numbers": ["六階"],
        "figure_summaries": [],
        "table_summaries": [],
        "short_quotes": [
            {"excerpt": "你應該根據財富階層消費，而非根據收入。", "confidence": 0.9},
        ],
        "extraction_confidence": 0.8,
    }


# ── Happy path ───────────────────────────────────────────────────────────────


def test_extract_returns_claim_extraction_result():
    ask_fn = _fake_ask(json.dumps(_valid_payload(), ensure_ascii=False))
    ex = LlmClaimExtractor(ask_fn=ask_fn)
    result = ex.extract(_CHAPTER, "第1章", "zh")
    assert isinstance(result, ClaimExtractionResult)
    assert "第一階的真正問題不在支出，而在收入。" in result.claims
    assert result.key_numbers == ["六階"]
    assert len(result.short_quotes) == 1
    assert result.extraction_confidence == pytest.approx(0.8)


def test_extract_passes_temperature_zero_and_system_prompt():
    ask_fn = _fake_ask(json.dumps(_valid_payload(), ensure_ascii=False))
    LlmClaimExtractor(ask_fn=ask_fn).extract(_CHAPTER, "第1章", "zh")
    assert ask_fn.calls, "ask_fn was never called"
    call = ask_fn.calls[0]
    assert call["temperature"] == 0.0
    assert "claim-dense" in call["system"]
    assert "第1章" in call["prompt"]


def test_verbatim_quote_gets_offset_locator():
    """A quote that is a verbatim substring of the chapter gets an offset
    locator; a paraphrased one falls back to 'chapter'."""
    payload = _valid_payload()
    payload["short_quotes"] = [
        {"excerpt": "你應該根據財富階層消費，而非根據收入。", "confidence": 0.9},  # verbatim
        {"excerpt": "這句是改寫過、原文沒有的句子。", "confidence": 0.5},  # not in chapter
    ]
    ex = LlmClaimExtractor(ask_fn=_fake_ask(json.dumps(payload, ensure_ascii=False)))
    result = ex.extract(_CHAPTER, "第1章", "zh")
    locators = [q.locator for q in result.short_quotes]
    assert any(loc.startswith("offset:") for loc in locators)
    assert "chapter" in locators


# ── Robust parsing ───────────────────────────────────────────────────────────


def test_extract_strips_markdown_fence_and_prose():
    body = json.dumps(_valid_payload(), ensure_ascii=False)
    wrapped = f"好的，以下是抽取結果：\n```json\n{body}\n```\n希望有幫助！"
    ex = LlmClaimExtractor(ask_fn=_fake_ask(wrapped))
    result = ex.extract(_CHAPTER, "第1章", "zh")
    assert result.claims, "should parse JSON embedded in prose + fence"


# ── Clamping / caps ──────────────────────────────────────────────────────────


def test_claims_truncated_confidence_clamped_and_deduped():
    payload = {
        "claims": ["X" * 500, "重複", "重複", "Y"],  # over-long + dup
        "short_quotes": [{"excerpt": "原文片段", "confidence": 7.5}],  # over-range conf
        "extraction_confidence": -3,  # under-range
    }
    ex = LlmClaimExtractor(ask_fn=_fake_ask(json.dumps(payload, ensure_ascii=False)))
    result = ex.extract("原文片段在這裡。", "t", "zh")
    assert all(len(c) <= 200 for c in result.claims)
    assert result.claims.count("重複") == 1, "duplicates collapsed"
    assert result.short_quotes[0].confidence == 1.0, "confidence clamped to [0,1]"
    assert result.extraction_confidence == 0.0, "clamped to [0,1]"


# ── Empty chapter — low yield, not failure ──────────────────────────────────


def test_empty_chapter_returns_empty_result_without_calling_llm():
    ask_fn = _fake_ask("{}")
    result = LlmClaimExtractor(ask_fn=ask_fn).extract("   ", "t", "zh")
    assert result.claims == []
    assert result.extraction_confidence == 0.0
    assert ask_fn.calls == [], "must not call the LLM on empty chapter text"


# ── Failure contract (builder catches ValueError / RuntimeError) ─────────────


def test_unparseable_response_raises_valueerror():
    ex = LlmClaimExtractor(ask_fn=_fake_ask("sorry, I can't help with that"))
    with pytest.raises(ValueError):
        ex.extract(_CHAPTER, "t", "zh")


def test_non_object_json_raises_valueerror():
    ex = LlmClaimExtractor(ask_fn=_fake_ask("[1, 2, 3]"))
    with pytest.raises(ValueError):
        ex.extract(_CHAPTER, "t", "zh")


def test_llm_call_failure_raises_runtimeerror():
    def boom(*a, **k):
        raise ConnectionError("provider down")

    ex = LlmClaimExtractor(ask_fn=boom)
    with pytest.raises(RuntimeError, match="llm_claim_extract_failed"):
        ex.extract(_CHAPTER, "t", "zh")


def test_programmer_error_propagates_not_wrapped():
    def type_bug(*a, **k):
        raise TypeError("bad call shape")

    ex = LlmClaimExtractor(ask_fn=type_bug)
    with pytest.raises(TypeError):
        ex.extract(_CHAPTER, "t", "zh")
