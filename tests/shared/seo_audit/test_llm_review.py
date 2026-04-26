"""Tests for shared/seo_audit/llm_review.py — Slice D.2 §附錄 C 12 條 LLM check.

Anthropic client 全 mock（feedback_test_api_isolation.md）。覆蓋 happy path /
prompt 組裝 / JSON parse 變體 / API error / response shape 異常 / 缺值 / model
level 切換 / kb / compliance context 注入。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from bs4 import BeautifulSoup

from shared.seo_audit.llm_review import _RULE_IDS, _RULES, review

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


def _mock_client(text: str) -> MagicMock:
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(text=text)])
    return client


def _patch_client(monkeypatch, client) -> dict:
    """Patch get_client and return a captured-kwargs dict from messages.create."""
    captured: dict = {}
    original_create = client.messages.create

    def _capturing_create(**kwargs):
        captured["kwargs"] = kwargs
        return original_create.return_value if hasattr(original_create, "return_value") else None

    if hasattr(original_create, "side_effect") and original_create.side_effect is not None:
        # Caller wired its own side_effect (e.g. raises). Don't override.
        pass
    else:
        client.messages.create = MagicMock(side_effect=_capturing_create)
        client.messages.create.return_value = original_create.return_value
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)
    return captured


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


def test_happy_path_returns_12_checks_in_rule_order(soup_simple, monkeypatch):
    soup, html = soup_simple
    payload = _full_response_dict("pass")
    client = _mock_client(json.dumps(payload))
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    checks = review(soup, html, focus_keyword="zone 2 訓練", url="https://shosho.tw/x")

    assert len(checks) == 12
    assert tuple(c.rule_id for c in checks) == _RULE_IDS
    assert all(c.status == "pass" for c in checks)
    # actual / fix_suggestion 從 LLM 回傳取
    assert checks[0].actual == "actual L1"
    assert checks[0].fix_suggestion == "fix L1"


def test_each_check_has_correct_metadata(soup_simple, monkeypatch):
    soup, html = soup_simple
    payload = _full_response_dict("warn")
    client = _mock_client(json.dumps(payload))
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

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


def test_prompt_includes_url_focus_keyword_and_all_rule_ids(soup_simple, monkeypatch):
    soup, html = soup_simple
    captured = {}

    def _capturing_create(**kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(_full_response_dict()))])

    client = MagicMock()
    client.messages.create.side_effect = _capturing_create
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    review(soup, html, focus_keyword="zone 2 訓練", url="https://shosho.tw/zone-2")

    user_msg = captured["kwargs"]["messages"][0]["content"]
    assert "https://shosho.tw/zone-2" in user_msg
    assert "zone 2 訓練" in user_msg
    for rid in _RULE_IDS:
        assert rid in user_msg


def test_focus_keyword_none_renders_skip_hint(soup_simple, monkeypatch):
    soup, html = soup_simple
    captured = {}
    client = MagicMock()

    def _capture(**kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(_full_response_dict()))])

    client.messages.create.side_effect = _capture
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    review(soup, html, focus_keyword=None, url="https://shosho.tw/x")

    user_msg = captured["kwargs"]["messages"][0]["content"]
    assert "未指定" in user_msg


def test_kb_context_injected_into_prompt(soup_simple, monkeypatch):
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
    captured = {}
    client = MagicMock()

    def _capture(**kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(_full_response_dict()))])

    client.messages.create.side_effect = _capture
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    review(soup, html, focus_keyword="zone 2", url="https://shosho.tw/x", kb_context=kb_context)

    user_msg = captured["kwargs"]["messages"][0]["content"]
    assert "有氧能量系統" in user_msg
    assert "KB/Wiki/Concepts/有氧能量系統" in user_msg


def test_compliance_findings_injected_into_prompt(soup_simple, monkeypatch):
    soup, html = soup_simple
    compliance = {
        "medical_claim": True,
        "absolute_assertion": False,
        "matched_terms": ["治癒癌症", "保證治好"],
    }
    captured = {}
    client = MagicMock()

    def _capture(**kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(_full_response_dict()))])

    client.messages.create.side_effect = _capture
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    review(
        soup,
        html,
        focus_keyword="zone 2",
        url="https://shosho.tw/x",
        compliance_findings=compliance,
    )

    user_msg = captured["kwargs"]["messages"][0]["content"]
    assert "medical_claim=True" in user_msg
    assert "治癒癌症" in user_msg


def test_text_excerpt_capped_by_param(monkeypatch):
    long_text = "段落 " * 5000
    html = f"<html><body><p>{long_text}</p></body></html>"
    soup = BeautifulSoup(html, "html.parser")
    captured = {}
    client = MagicMock()

    def _capture(**kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(_full_response_dict()))])

    client.messages.create.side_effect = _capture
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    review(
        soup,
        html,
        focus_keyword="x",
        url="https://shosho.tw/x",
        text_excerpt_chars=500,
    )

    user_msg = captured["kwargs"]["messages"][0]["content"]
    # excerpt 段裡 "段落 " 重複數量 ≤ ~500/3 = 166；不該整段 5000 都進去
    assert user_msg.count("段落 ") < 200


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------


def test_model_sonnet_default(soup_simple, monkeypatch):
    soup, html = soup_simple
    captured = {}
    client = MagicMock()

    def _capture(**kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(_full_response_dict()))])

    client.messages.create.side_effect = _capture
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    review(soup, html, focus_keyword="x", url="https://shosho.tw/x")
    assert captured["kwargs"]["model"] == "claude-sonnet-4-6"


def test_model_haiku_when_level_haiku(soup_simple, monkeypatch):
    soup, html = soup_simple
    captured = {}
    client = MagicMock()

    def _capture(**kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(_full_response_dict()))])

    client.messages.create.side_effect = _capture
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    review(soup, html, focus_keyword="x", url="https://shosho.tw/x", model="haiku")
    assert captured["kwargs"]["model"] == "claude-haiku-4-5-20251001"


def test_model_none_skips_all_no_api_call(soup_simple, monkeypatch):
    soup, html = soup_simple
    client = MagicMock()
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x", model="none")

    assert len(checks) == 12
    assert all(c.status == "skip" for c in checks)
    client.messages.create.assert_not_called()


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_api_error_falls_back_to_all_skip(soup_simple, monkeypatch):
    soup, html = soup_simple
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("Anthropic API down")
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x")

    assert len(checks) == 12
    assert all(c.status == "skip" for c in checks)
    assert "RuntimeError" in checks[0].actual


def test_invalid_json_falls_back_to_all_skip(soup_simple, monkeypatch):
    soup, html = soup_simple
    client = _mock_client("Sorry, I cannot help with that.")
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x")

    assert all(c.status == "skip" for c in checks)
    assert "JSON parse" in checks[0].actual


def test_partial_response_fills_missing_with_skip(soup_simple, monkeypatch):
    soup, html = soup_simple
    # 只給 L1 / L4 / L9
    payload = {
        "L1": {"status": "pass", "actual": "ok", "fix_suggestion": ""},
        "L4": {"status": "fail", "actual": "答非所問", "fix_suggestion": "重寫首段"},
        "L9": {"status": "warn", "actual": "1 個療效詞", "fix_suggestion": "刪掉"},
    }
    client = _mock_client(json.dumps(payload))
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x")
    by_id = {c.rule_id: c for c in checks}

    assert by_id["L1"].status == "pass"
    assert by_id["L4"].status == "fail"
    assert by_id["L9"].status == "warn"
    # 缺項 → skip + actual="LLM omitted"
    assert by_id["L2"].status == "skip"
    assert by_id["L2"].actual == "LLM omitted"


def test_invalid_status_coerced_to_skip(soup_simple, monkeypatch):
    soup, html = soup_simple
    payload = _full_response_dict()
    payload["L3"] = {"status": "maybe", "actual": "ambiguous", "fix_suggestion": ""}
    client = _mock_client(json.dumps(payload))
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x")
    by_id = {c.rule_id: c for c in checks}
    assert by_id["L3"].status == "skip"


def test_response_with_markdown_fence_parses(soup_simple, monkeypatch):
    soup, html = soup_simple
    payload = _full_response_dict("warn")
    fenced = f"```json\n{json.dumps(payload)}\n```"
    client = _mock_client(fenced)
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x")
    assert all(c.status == "warn" for c in checks)


def test_response_with_prose_prefix_then_json(soup_simple, monkeypatch):
    soup, html = soup_simple
    payload = _full_response_dict("pass")
    text = f"Here is the audit:\n{json.dumps(payload)}\nDone."
    client = _mock_client(text)
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x")
    assert all(c.status == "pass" for c in checks)


def test_soup_none_with_empty_html_short_circuits_skip(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    checks = review(None, "", focus_keyword="x", url="https://shosho.tw/x")

    assert all(c.status == "skip" for c in checks)
    client.messages.create.assert_not_called()


def test_llm_response_object_shape_unexpected(soup_simple, monkeypatch):
    """response.content[0].text 不存在 → all skip。"""
    soup, html = soup_simple
    client = MagicMock()
    # response 缺 .content
    client.messages.create.return_value = SimpleNamespace()
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    checks = review(soup, html, focus_keyword="x", url="https://shosho.tw/x")
    assert all(c.status == "skip" for c in checks)


# ---------------------------------------------------------------------------
# Cost-control sanity
# ---------------------------------------------------------------------------


def test_single_call_for_all_12_rules(soup_simple, monkeypatch):
    """12 條 single-call batch — 必須只打一次 API（成本控制）。"""
    soup, html = soup_simple
    client = _mock_client(json.dumps(_full_response_dict()))
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    review(soup, html, focus_keyword="x", url="https://shosho.tw/x")

    assert client.messages.create.call_count == 1


# ---------------------------------------------------------------------------
# A4 follow-up — L9 SEED suffix instruction must be in system prompt
# ---------------------------------------------------------------------------


def test_l9_seed_caveat_instruction_in_system_prompt(soup_simple, monkeypatch):
    """A4 follow-up: system prompt must instruct LLM to suffix L9 fix_suggestion
    with the SEED-scan caveat per references/check-rule-catalog.md."""
    soup, html = soup_simple
    captured: dict = {}
    client = MagicMock()

    def _capture(**kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(_full_response_dict()))])

    client.messages.create.side_effect = _capture
    monkeypatch.setattr("shared.anthropic_client.get_client", lambda: client)

    review(soup, html, focus_keyword="x", url="https://shosho.tw/x")

    system = captured["kwargs"]["system"]
    assert "L9" in system
    assert "SEED scan" in system
    assert "醫療" in system  # 詞庫升級中
