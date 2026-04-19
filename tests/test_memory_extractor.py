"""Tests for shared.memory_extractor."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from shared import agent_memory, memory_extractor


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path, monkeypatch):
    """Each test gets its own SQLite DB."""
    db_path = tmp_path / "test.db"
    import shared.state as state

    monkeypatch.setattr(state, "get_db_path", lambda: db_path)

    if state._conn is not None:
        try:
            state._conn.close()
        except sqlite3.Error:
            pass
    state._conn = None
    agent_memory._SCHEMA_INITIALIZED = False

    yield db_path

    if state._conn is not None:
        try:
            state._conn.close()
        except sqlite3.Error:
            pass
        state._conn = None


def test_parse_extraction_response_plain_json():
    raw = '[{"type": "fact", "subject": "X", "content": "Y"}]'
    items = memory_extractor._parse_extraction_response(raw)
    assert len(items) == 1
    assert items[0]["subject"] == "X"


def test_parse_extraction_response_with_code_fence():
    raw = '```json\n[{"type": "fact", "subject": "X", "content": "Y"}]\n```'
    items = memory_extractor._parse_extraction_response(raw)
    assert len(items) == 1


def test_parse_extraction_response_empty_array():
    assert memory_extractor._parse_extraction_response("[]") == []


def test_parse_extraction_response_invalid_json():
    assert memory_extractor._parse_extraction_response("not json at all") == []


def test_parse_extraction_response_non_list():
    assert memory_extractor._parse_extraction_response('{"foo": "bar"}') == []


def test_validate_rejects_missing_fields():
    assert memory_extractor._validate_and_normalize({}) is None
    assert memory_extractor._validate_and_normalize({"type": "fact"}) is None
    assert memory_extractor._validate_and_normalize({"type": "fact", "subject": "X"}) is None


def test_validate_rejects_invalid_type():
    item = {"type": "garbage", "subject": "X", "content": "Y"}
    assert memory_extractor._validate_and_normalize(item) is None


def test_validate_clamps_confidence():
    item = {"type": "fact", "subject": "X", "content": "Y", "confidence": 2.5}
    result = memory_extractor._validate_and_normalize(item)
    assert result["confidence"] == 1.0

    item["confidence"] = -0.5
    result = memory_extractor._validate_and_normalize(item)
    assert result["confidence"] == 0.0


def test_validate_default_confidence():
    item = {"type": "fact", "subject": "X", "content": "Y"}
    result = memory_extractor._validate_and_normalize(item)
    assert result["confidence"] == 0.8


def test_format_messages_handles_text_and_tool_blocks():
    messages = [
        {"role": "user", "content": "你好"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "哈囉"},
                {"type": "tool_use", "name": "create_task", "input": {"title": "X"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "content": "✅ 建立成功"}],
        },
    ]
    text = memory_extractor._format_messages_for_extraction(messages)
    assert "[user] 你好" in text
    assert "哈囉" in text
    assert "create_task" in text
    assert "建立成功" in text


def test_extract_from_messages_saves_memories():
    fake_response = json.dumps(
        [
            {
                "type": "preference",
                "subject": "工作時段",
                "content": "修修習慣早上做深度工作",
                "confidence": 0.9,
            },
            {
                "type": "fact",
                "subject": "角色",
                "content": "修修是船長",
                "confidence": 1.0,
            },
        ]
    )
    messages = [{"role": "user", "content": "我早上頭腦最清楚，你叫我船長就好"}]

    with patch("shared.memory_extractor.ask_claude", return_value=fake_response):
        ids = memory_extractor.extract_from_messages("nami", "U1", messages)

    assert len(ids) == 2
    saved = agent_memory.list_all("nami", "U1")
    assert len(saved) == 2
    subjects = {m.subject for m in saved}
    assert subjects == {"工作時段", "角色"}


def test_extract_from_messages_skips_invalid_items():
    fake_response = json.dumps(
        [
            {"type": "fact", "subject": "有效", "content": "這筆有效"},
            {"type": "bogus", "subject": "無效類型", "content": "X"},
            {"type": "fact", "subject": "", "content": "無 subject"},
            {"type": "fact"},  # 缺 content
        ]
    )
    with patch("shared.memory_extractor.ask_claude", return_value=fake_response):
        ids = memory_extractor.extract_from_messages(
            "nami", "U1", [{"role": "user", "content": "x"}]
        )

    assert len(ids) == 1
    saved = agent_memory.list_all("nami", "U1")
    assert saved[0].subject == "有效"


def test_extract_from_messages_llm_failure_returns_empty():
    """LLM 失敗時應記 warning 並回空 list，不拋例外。"""
    with patch("shared.memory_extractor.ask_claude", side_effect=RuntimeError("API down")):
        ids = memory_extractor.extract_from_messages(
            "nami", "U1", [{"role": "user", "content": "x"}]
        )
    assert ids == []


def test_extract_from_messages_empty_messages():
    ids = memory_extractor.extract_from_messages("nami", "U1", [])
    assert ids == []


def test_extract_in_background_returns_thread():
    fake_response = "[]"
    with patch("shared.memory_extractor.ask_claude", return_value=fake_response):
        t = memory_extractor.extract_in_background(
            "nami", "U1", [{"role": "user", "content": "hi"}]
        )
        t.join(timeout=5.0)
    assert not t.is_alive()


def test_extract_dedup_via_subject():
    """同一 subject 第二次抽取應 update 現有記憶，不是新增。"""
    first_response = json.dumps(
        [{"type": "preference", "subject": "X", "content": "舊內容", "confidence": 0.8}]
    )
    second_response = json.dumps(
        [{"type": "preference", "subject": "X", "content": "新內容", "confidence": 0.9}]
    )
    messages = [{"role": "user", "content": "x"}]

    with patch("shared.memory_extractor.ask_claude", return_value=first_response):
        memory_extractor.extract_from_messages("nami", "U1", messages)
    with patch("shared.memory_extractor.ask_claude", return_value=second_response):
        memory_extractor.extract_from_messages("nami", "U1", messages)

    saved = agent_memory.list_all("nami", "U1")
    assert len(saved) == 1
    assert saved[0].content == "新內容"
    assert saved[0].confidence == 0.9
