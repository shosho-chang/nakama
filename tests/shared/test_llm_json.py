"""Tests for shared/llm_json.py — tolerant LLM JSON extraction.

涵蓋 2026-06-24 抓到的真實失敗樣態：``max_tokens`` 截斷、```json fence、reasoning
preamble——這些原本讓 daily_review P-1/P-2 的天真 regex 回空（每日回顧 0 候選）。
"""

from __future__ import annotations

from shared.llm_json import extract_json_array, extract_json_object, strip_code_fence


# ── array ────────────────────────────────────────────────────────────────
def test_bare_array():
    assert extract_json_array('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]


def test_fenced_array():
    # gemini / claude 常把答案包進 ```json fence
    t = '```json\n[{"suggested_title": "X", "why": "..."}]\n```'
    assert extract_json_array(t) == [{"suggested_title": "X", "why": "..."}]


def test_array_with_reasoning_preamble():
    # reasoning model 即使被禁止仍會加前言（ADR-047 實測樣態）
    t = '我來分析這份清單...\n\n[{"a": 1}]'
    assert extract_json_array(t) == [{"a": 1}]


def test_truncated_array_returns_empty():
    # max_tokens 砍掉收尾 ] → 不誤拼，回 []（這正是每日回顧 0 候選的元兇）
    t = '```json\n[\n  {"suggested_title": "X", "source_quote": "I just knew'
    assert extract_json_array(t) == []


def test_brackets_inside_string_dont_break_matching():
    t = '[{"quote": "see [1] and [2]", "ok": true}]'
    assert extract_json_array(t) == [{"quote": "see [1] and [2]", "ok": True}]


def test_array_keeps_non_objects_dict_filter_is_caller_job():
    assert extract_json_array('[1, {"a": 2}, "x"]') == [1, {"a": 2}, "x"]


def test_array_object_shell_returns_empty():
    # 給的是 object 不是 array → []
    assert extract_json_array('{"a": 1}') == []


# ── object ───────────────────────────────────────────────────────────────
def test_bare_object():
    assert extract_json_object('{"supports": [], "refutes": []}') == {
        "supports": [],
        "refutes": [],
    }


def test_fenced_object_with_nested_array():
    assert extract_json_object('```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}


def test_truncated_object_returns_empty():
    assert extract_json_object('{"a": 1, "b": "unclosed') == {}


# ── edges ────────────────────────────────────────────────────────────────
def test_empty_and_garbage():
    assert extract_json_array("") == []
    assert extract_json_array("no json here") == []
    assert extract_json_object(None) == {}  # type: ignore[arg-type]
    assert extract_json_object("nope") == {}


def test_strip_code_fence():
    assert strip_code_fence("```json\n[1]\n```") == "[1]"
    assert strip_code_fence("```\n{}\n```") == "{}"
    assert strip_code_fence("[1]") == "[1]"
    assert strip_code_fence(None) == ""  # type: ignore[arg-type]
