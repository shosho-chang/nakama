"""Tests for shared/seo_audit/llm_review.py — Slice D.2 §附錄 C 12 條 LLM check.

LLM 全 mock 在 ``shared.llm`` facade（conftest ``mock_llm_response``；
2026-07-03 架構審計 — 業務測試不 mock provider client 內部）。覆蓋 happy
path / prompt 組裝 / JSON parse 變體 / API error / 缺值 / model level 切換 /
kb / compliance context 注入。

例外（刻意留在 client 層）：

- ``test_llm_client_mock_spec_blocks_nonexistent_methods`` — A6 spec= 防護
  的 self-contained regression。
- ``test_a3_llm_call_invokes_record_call_for_cost_tracking`` — 刻意穿過真
  facade → ask_claude → record_call 驗 cost tracking wiring（這正是實作層
  該有的整合測試）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from anthropic import Anthropic
from bs4 import BeautifulSoup

from shared.seo_audit.llm_review import _RULE_IDS, _RULES, review

# A6 follow-up (PR #192 deferred): mock Anthropic client with `spec=Anthropic`
# so calls to non-existent SDK methods raise AttributeError instead of silently
# returning truthy MagicMock — see feedback_mock_use_spec.md (PR #132 → #135 GSC
# `Credentials.authorize()` regression that bare MagicMock missed).
#
# B3 (firecrawl) intentionally not covered here: firecrawl 4.23 attaches `scrape`
# at instance-level via dynamic attribute, so `spec=FirecrawlApp` would block the
# real method call. Left in backlog until SDK exposes scrape on the class.

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _full_response_dict(default_status: str = "pass") -> dict:
    """Construct a complete `{L1..L12: {...}}` LLM response payload."""
    return {
        rid: {
            "status": default_status,
            "actual": f"actual {rid}",
            "fix_suggestion": f"fix {rid}",
        }
        for rid in _RULE_IDS
    }


def _sent_prompt(llm) -> str:
    """Return the user prompt the production code sent through the facade."""
    return llm.ask.call_args.args[0]


def test_llm_client_mock_spec_blocks_nonexistent_methods():
    """Regression for A6 — `spec=Anthropic` rejects calls to fake SDK methods.

    This is the property that bare `MagicMock()` lacked (PR #132 GSC bug:
    `Credentials.authorize()` was removed by google-auth 2.x but mock truthified
    every attr access, masking the AttributeError until production smoke).
    """
    client = MagicMock(spec=Anthropic)
    # Real method exists
    assert hasattr(client, "messages")
    # Fake method blocked
    with pytest.raises(AttributeError):
        client.fake_method_that_does_not_exist_on_anthropic()


@pytest.fixture
def soup_simple():
    html = (
        "<html><head><title>Zone 2 訓練指南</title></head>"
        "<body><h1>Zone 2 訓練</h1><p>本文章解釋有氧能量系統與 zone 2 訓練。</p></body></html>"
    )
    return BeautifulSoup(html, "html.parser"), html


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_returns_12_checks_in_rule_order(soup_simple, mock_llm_response):
    soup, html = soup_simple
    payload = _full_response_dict("pass")
    mock_llm_response(json.dumps(payload))

    checks = review(soup, html, focus_keyword="zone 2 訓練", url="https://shosho.tw/x")

    assert len(checks) == 12
    assert tuple(c.rule_id for c in checks) == _RULE_IDS
    assert all(c.status == "pass" for c in checks)
    # actual / fix_suggestion 從 LLM 回傳取
    assert checks[0].actual == "actual L1"
    assert checks[0].fix_suggestion == "fix L1"


def test_each_check_has_correct_metadata(soup_simple, mock_llm_response):
    soup, html = soup_simple
    payload = _full_response_dict("warn")
    mock_llm_response(json.dumps(payload))

    checks = review(soup, html, focus_keyword="zone 2", url="https://shosho.tw/x")

    for check, rule in zip(checks, _RULES):
        assert check.rule_id == rule["id"]
        assert check.name == rule["name"]
        assert check.category == "semantic"
        assert check.severity == rule["severity"]
        assert check.expected == rule["expected"]


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def test_prompt_includes_url_focus_keyword_and_all_rule_ids(soup_simple, mock_llm_response):
    soup, html = soup_simple
    llm = mock_llm_response(json.dumps(_full_response_dict()))

    review(soup, html, focus_keyword="zone 2 訓練", url="https://shosho.tw/zone-2")

    user_msg = _sent_prompt(llm)
    assert "https://shosho.tw/zone-2" in user_msg
    assert "zone 2 訓練" in user_msg
    for rid in _RULE_IDS:
        assert rid in user_msg


def test_focus_keyword_none_renders_skip_hint(soup_simple, mock_llm_response):
    soup, html = soup_simple
    llm = mock_llm_response(json.dumps(_full_response_dict()))

    review(soup, html, focus_keyword=None, url="https://shosho.tw/x")

    assert "未指定" in _sent_prompt(llm)


def test_kb_context_injected_into_prompt(soup_simple, mock_llm_response):
    soup, html = soup_simple
    kb_context = [
        {
            "type": "concept",
            "title": "有氧能量系統",
            "path": "KB/Wiki/Concepts/有氧能量系統",
            "relevance_reason": "zone 2 主要靠有氧系統",
        },
        {
            "type": "concept",
            "title": "磷酸肌酸系統",
            "path": "KB/Wiki/Concepts/磷酸肌酸系統",
            "relevance_reason": "對照短時間高強度",
        },
    ]
    llm = mock_llm_response(json.dumps(_full_response_dict()))

    review(soup, html, focus_keyword="zone 2", url="https://shosho.tw/x", kb_context=kb_context)

    user_msg = _sent_prompt(llm)
    assert "有氧能量系統" in user_msg
    assert "KB/Wiki/Concepts/有氧能量系統" in user_msg


def test_compliance_findings_injected_into_prompt(soup_simple, mock_llm_response):
    soup, html = soup_simple
    compliance = {
        "medical_claim": True,
        "absolute_assertion": False,
        "matched_terms": ["治癒癌症", "保證治好"],
    }
    llm = mock_llm_response(json.dumps(_full_response_dict()))

    review(
        soup,
        html,
        focus_keyword="zone 2",
        url="https://shosho.tw/x",
        compliance_findings=compliance,
    )

    user_msg = _sent_prompt(llm)
    assert "medical_claim=True" in user_msg
    assert "治癒癌症" in user_msg


def test_text_excerpt_capped_by_param(mock_llm_response):
    long_text = "段落 " * 5000
    html = f"<html><body><p>{long_text}</p></body></html>"
    soup = BeautifulSoup(html, "html.parser")
    llm = mock_llm_response(json.dumps(_full_response_dict()))

    review(
        soup,
        html,
        focus_keyword="x",
        url="https://shosho.tw/x",
        text_excerpt_chars=500,
    )

    user_msg = _sent_prompt(llm)
    # excerpt 段裡 "段落 " 重複數量 ≤ ~500/3 = 166；不該整段 5000 都進去
    assert user_msg.count("段落 ") < 200


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------


def test_model_sonnet_default(soup_simple, mock_llm_response):
    soup, html = soup_simple
    llm = mock_llm_response(json.dumps(_full_response_dict()))

    review(soup, html, focus_keyword="x", url="https://shosho.tw/x")
    assert llm.ask.call_args.kwargs["model"] == "claude-sonnet-4-6"


def test_model_haiku_when_level_haiku(soup_simple, mock_llm_response):
    soup, html = soup_simple
    llm = mock_llm_response(json.dumps(_full_response_dict()))

    review(soup, html, focus_keyword="x", url="https://shosho.tw/x", model="haiku")
    assert llm.ask.call_args.kwargs["model"] == "claude-haiku-4-5-20251001"


def test_model_none_skips_all_no_api_call(soup_simple, mock_llm_response):
    soup, html = soup_simple
    llm = mock_llm_response(json.dumps(_full_response_dict()))

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x", model="none")

    assert len(checks) == 12
    assert all(c.status == "skip" for c in checks)
    llm.ask.assert_not_called()


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_api_error_falls_back_to_all_skip(soup_simple, mock_llm_response):
    soup, html = soup_simple
    mock_llm_response(side_effect=RuntimeError("Anthropic API down"))

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x")

    assert len(checks) == 12
    assert all(c.status == "skip" for c in checks)
    assert "RuntimeError" in checks[0].actual


def test_invalid_json_falls_back_to_all_skip(soup_simple, mock_llm_response):
    soup, html = soup_simple
    mock_llm_response("Sorry, I cannot help with that.")

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x")

    assert all(c.status == "skip" for c in checks)
    assert "JSON parse" in checks[0].actual


def test_partial_response_fills_missing_with_skip(soup_simple, mock_llm_response):
    soup, html = soup_simple
    # 只給 L1 / L4 / L9
    payload = {
        "L1": {"status": "pass", "actual": "ok", "fix_suggestion": ""},
        "L4": {"status": "fail", "actual": "答非所問", "fix_suggestion": "重寫首段"},
        "L9": {"status": "warn", "actual": "1 個療效詞", "fix_suggestion": "刪掉"},
    }
    mock_llm_response(json.dumps(payload))

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x")
    by_id = {c.rule_id: c for c in checks}

    assert by_id["L1"].status == "pass"
    assert by_id["L4"].status == "fail"
    assert by_id["L9"].status == "warn"
    # 缺項 → skip + actual="LLM omitted"
    assert by_id["L2"].status == "skip"
    assert by_id["L2"].actual == "LLM omitted"


def test_invalid_status_coerced_to_skip(soup_simple, mock_llm_response):
    soup, html = soup_simple
    payload = _full_response_dict()
    payload["L3"] = {"status": "maybe", "actual": "ambiguous", "fix_suggestion": ""}
    mock_llm_response(json.dumps(payload))

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x")
    by_id = {c.rule_id: c for c in checks}
    assert by_id["L3"].status == "skip"


def test_response_with_markdown_fence_parses(soup_simple, mock_llm_response):
    soup, html = soup_simple
    payload = _full_response_dict("warn")
    mock_llm_response(f"```json\n{json.dumps(payload)}\n```")

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x")
    assert all(c.status == "warn" for c in checks)


def test_response_with_prose_prefix_then_json(soup_simple, mock_llm_response):
    soup, html = soup_simple
    payload = _full_response_dict("pass")
    mock_llm_response(f"Here is the audit:\n{json.dumps(payload)}\nDone.")

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x")
    assert all(c.status == "pass" for c in checks)


def test_soup_none_with_empty_html_short_circuits_skip(mock_llm_response):
    llm = mock_llm_response(json.dumps(_full_response_dict()))

    checks = review(None, "", focus_keyword="x", url="https://shosho.tw/x")

    assert all(c.status == "skip" for c in checks)
    llm.ask.assert_not_called()


def test_llm_unexpected_exception_falls_back_to_all_skip(soup_simple, mock_llm_response):
    """facade 拋非預期 exception（原場景：SDK response 缺 .content 的
    AttributeError）→ all skip，不 crash。"""
    soup, html = soup_simple
    mock_llm_response(side_effect=AttributeError("'SimpleNamespace' has no attribute 'content'"))

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x")
    assert all(c.status == "skip" for c in checks)


# ---------------------------------------------------------------------------
# Cost-control sanity
# ---------------------------------------------------------------------------


def test_single_call_for_all_12_rules(soup_simple, mock_llm_response):
    """12 條 single-call batch — 必須只打一次 API（成本控制）。"""
    soup, html = soup_simple
    llm = mock_llm_response(json.dumps(_full_response_dict()))

    review(soup, html, focus_keyword="x", url="https://shosho.tw/x")

    assert llm.ask.call_count == 1


# ---------------------------------------------------------------------------
# A4 follow-up — L9 SEED suffix instruction must be in system prompt
# ---------------------------------------------------------------------------


def test_l9_seed_caveat_instruction_in_system_prompt(soup_simple, mock_llm_response):
    """A4 follow-up: system prompt must instruct LLM to suffix L9 fix_suggestion
    with the SEED-scan caveat per references/check-rule-catalog.md."""
    soup, html = soup_simple
    llm = mock_llm_response(json.dumps(_full_response_dict()))

    review(soup, html, focus_keyword="x", url="https://shosho.tw/x")

    system = llm.ask.call_args.kwargs["system"]
    assert "L9" in system
    assert "SEED scan" in system
    assert "醫療" in system  # 詞庫升級中


# ---------------------------------------------------------------------------
# A3 follow-up — LLM call routes through ask_claude → record_call (cost tracking)
# ---------------------------------------------------------------------------


def test_a3_llm_call_invokes_record_call_for_cost_tracking(soup_simple, monkeypatch):
    """A3 follow-up: previous direct `client.messages.create` call skipped cost
    tracking; PR #192 routed through `shared.llm.ask` → `ask_claude` →
    `_record_anthropic_usage` → `record_call`. Assert `record_call` fires.

    刻意 mock 在 client 層（get_client / record_call）— 這條測的就是
    facade → wrapper → observability 的真 wiring，不能用 facade fixture。
    """
    soup, html = soup_simple
    payload = _full_response_dict("pass")
    usage = SimpleNamespace(
        input_tokens=1234,
        output_tokens=567,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    response = SimpleNamespace(
        content=[SimpleNamespace(text=json.dumps(payload))],
        usage=usage,
    )
    client = MagicMock(spec=Anthropic)
    client.messages.create.return_value = response
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    record_calls: list[dict] = []

    def _fake_record(**kwargs):
        record_calls.append(kwargs)

    monkeypatch.setattr("shared.anthropic_client.record_call", _fake_record)

    review(soup, html, focus_keyword="x", url="https://shosho.tw/x")

    assert len(record_calls) == 1
    captured = record_calls[0]
    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["input_tokens"] == 1234
    assert captured["output_tokens"] == 567
