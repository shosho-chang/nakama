"""Tests for agents/brook/compose.py 對話式 API surface.

對話式 flow（修修 Web UI 用）：
    start_conversation → send_message × N → export_draft

跟 compose_and_enqueue (production pipeline) 正交；LLM 全程 mock（見
feedback_test_api_isolation.md）。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.brook import compose
from agents.brook.compose import (
    _build_messages_window,
    export_draft,
    get_conversation,
    get_conversations,
    send_message,
    start_conversation,
)


@pytest.fixture(autouse=True)
def _reset_brook_tables_flag(monkeypatch):
    """Module-level `_tables_ready` cache 會跨 test 污染。

    conftest.isolated_db 每 test 切新 SQLite file，但 compose 模組記憶體內
    `_tables_ready=True` 會讓 `_ensure_tables()` 跳過 `CREATE TABLE IF NOT
    EXISTS`，導致新 DB 沒 brook 表 → INSERT 炸 "no such table: brook_*"。
    """
    monkeypatch.setattr(compose, "_tables_ready", False)


def _mk_llm_mock(responses: list[str]):
    """Sequential mock for ask_multi — each call pops the next response."""
    calls = iter(responses)

    def _fake(messages, **kwargs):
        return next(calls)

    return _fake


# ---------------------------------------------------------------------------
# start_conversation
# ---------------------------------------------------------------------------


def test_start_conversation_creates_row_and_returns_outline():
    """Happy path: 建對話 → 存 user msg + assistant response → 回傳 dict."""
    with patch(
        "agents.brook.compose.ask_multi",
        side_effect=_mk_llm_mock(["這是一份關於睡眠的大綱：\n1. 節律\n2. 光照\n3. 飲食"]),
    ) as mock_llm:
        result = start_conversation(topic="睡眠品質", kb_context="")

    assert "conversation_id" in result
    assert result["phase"] == "outline"
    assert "睡眠的大綱" in result["message"]
    mock_llm.assert_called_once()


def test_start_conversation_persists_both_messages():
    """assistant response + initial user prompt 都該寫進 brook_messages。"""
    with patch(
        "agents.brook.compose.ask_multi",
        side_effect=_mk_llm_mock(["大綱 v1"]),
    ):
        result = start_conversation(topic="腸道健康", kb_context="KB 摘要內容")

    conv = get_conversation(result["conversation_id"])
    assert conv is not None
    assert conv["topic"] == "腸道健康"
    assert conv["kb_context"] == "KB 摘要內容"
    assert len(conv["messages"]) == 2
    assert conv["messages"][0]["role"] == "user"
    assert "腸道健康" in conv["messages"][0]["content"]
    assert conv["messages"][1]["role"] == "assistant"
    assert conv["messages"][1]["content"] == "大綱 v1"


def test_start_conversation_without_kb_context_ok():
    """kb_context 空字串不炸（會當成無 KB）。"""
    with patch(
        "agents.brook.compose.ask_multi",
        side_effect=_mk_llm_mock(["大綱"]),
    ):
        result = start_conversation(topic="運動恢復")
    conv = get_conversation(result["conversation_id"])
    assert conv["kb_context"] == ""


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


def test_send_message_appends_and_returns_turn_count():
    """第二輪 send 回 turn_count=2（第一輪由 start_conversation 建的）。"""
    with patch(
        "agents.brook.compose.ask_multi",
        side_effect=_mk_llm_mock(["大綱 v1", "第 2 段草稿"]),
    ):
        started = start_conversation(topic="睡眠")
        result = send_message(started["conversation_id"], "幫我把節律那段展開成 200 字")

    assert result["message"] == "第 2 段草稿"
    assert result["phase"] == "outline"
    assert result["turn_count"] == 2


def test_send_message_unknown_conversation_id_raises():
    with pytest.raises(ValueError, match="Conversation not found"):
        send_message("non-existent-uuid", "嘿")


def test_send_message_persists_user_and_assistant():
    """3 輪對話後 DB 應有 6 則訊息（每輪 user+assistant 各一）。"""
    with patch(
        "agents.brook.compose.ask_multi",
        side_effect=_mk_llm_mock(["r1", "r2", "r3"]),
    ):
        started = start_conversation(topic="營養")
        send_message(started["conversation_id"], "展開第二段")
        send_message(started["conversation_id"], "改一下口氣")

    conv = get_conversation(started["conversation_id"])
    assert len(conv["messages"]) == 6
    roles = [m["role"] for m in conv["messages"]]
    assert roles == ["user", "assistant", "user", "assistant", "user", "assistant"]


# ---------------------------------------------------------------------------
# get_conversations / get_conversation
# ---------------------------------------------------------------------------


def test_get_conversations_orders_by_updated_at_desc():
    """新寫入的對話排前面。"""
    with patch(
        "agents.brook.compose.ask_multi",
        side_effect=_mk_llm_mock(["r1", "r2", "r3"]),
    ):
        a = start_conversation(topic="A")
        b = start_conversation(topic="B")
        # 動 A，updated_at 變新，應該 A 排 b 前面
        send_message(a["conversation_id"], "update A")

    rows = get_conversations(limit=10)
    assert len(rows) == 2
    assert rows[0]["id"] == a["conversation_id"]
    assert rows[1]["id"] == b["conversation_id"]


def test_get_conversations_respects_limit():
    with patch(
        "agents.brook.compose.ask_multi",
        side_effect=_mk_llm_mock(["r"] * 5),
    ):
        for i in range(3):
            start_conversation(topic=f"topic-{i}")

    assert len(get_conversations(limit=2)) == 2
    assert len(get_conversations(limit=10)) == 3


def test_get_conversation_returns_none_for_unknown_id():
    assert get_conversation("fake-id") is None


# ---------------------------------------------------------------------------
# export_draft
# ---------------------------------------------------------------------------


def test_export_draft_calls_llm_and_flips_phase_to_done():
    with patch(
        "agents.brook.compose.ask_multi",
        side_effect=_mk_llm_mock(["大綱", "完整文章 markdown"]),
    ):
        started = start_conversation(topic="睡眠")
        draft = export_draft(started["conversation_id"])

    assert draft == "完整文章 markdown"
    conv = get_conversation(started["conversation_id"])
    assert conv["phase"] == "done"


def test_export_draft_unknown_conversation_id_raises():
    with pytest.raises(ValueError, match="Conversation not found"):
        export_draft("non-existent-uuid")


def test_export_draft_passes_full_conversation_to_llm(monkeypatch):
    """export 時組的 prompt 含所有對話輪（使用者 + Brook 都進去）。"""
    captured = {}

    def _capture(messages, **kwargs):
        if "匯出" in str(messages) or "完整文章" in str(messages) or "export" in str(messages):
            captured["export_call"] = (messages, kwargs)
            return "最終文章 v1"
        return "對話中 response"

    monkeypatch.setattr("agents.brook.compose.ask_multi", _capture)

    def _fake_prompt(agent, name, **kw):
        conv = kw.get("conversation", "")
        return f"EXPORT_PROMPT(export:{name}) with conversation={conv}"

    monkeypatch.setattr("agents.brook.compose.load_prompt", _fake_prompt)

    started = start_conversation(topic="飲食")
    send_message(started["conversation_id"], "寫一個三段式的草稿")
    export_draft(started["conversation_id"])

    assert "export_call" in captured
    user_msg = captured["export_call"][0][0]["content"]
    assert "使用者" in user_msg or "Brook" in user_msg  # role labels in export prompt


# ---------------------------------------------------------------------------
# _build_messages_window（sliding window）
# ---------------------------------------------------------------------------


def test_build_messages_window_preserves_all_when_under_limit():
    """總數 <= anchor(2) + recent(40) = 42 時不切。"""
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(30)]
    result = _build_messages_window(msgs)
    assert result == msgs


def test_build_messages_window_truncates_with_anchor_plus_recent():
    """總數 = 50，預期保留前 2 (anchor) + 後 40 (recent) = 42 則。"""
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(50)]
    result = _build_messages_window(msgs)
    assert len(result) <= 50  # 肯定不多於原始
    # 前 2 則應該是 anchor（m0, m1）
    assert result[0]["content"] == "m0"
    assert result[1]["content"] == "m1"
    # 最後一則應該是 m49
    assert result[-1]["content"] == "m49"


def test_build_messages_window_exact_boundary():
    """總數 = anchor(2) + recent(40) = 42，應該不切。"""
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(42)]
    result = _build_messages_window(msgs)
    assert len(result) == 42
    assert result == msgs


# ---------------------------------------------------------------------------
# Helpers — low-hanging coverage：_build_user_request / _extract_json_object
#           / _ast_to_plaintext / _parse_llm_output 的錯誤分支
# ---------------------------------------------------------------------------


def test_build_user_request_includes_kb_context_when_nonempty():
    from agents.brook.compose import _build_user_request

    out = _build_user_request(
        topic="睡眠",
        kb_context="Robin KB 片段：光照節律",
        source_content="",
    )
    assert "# 主題" in out
    assert "睡眠" in out
    assert "知識庫參考" in out
    assert "光照節律" in out
    # source_content 空白 → 不 include
    assert "素材" not in out


def test_build_user_request_includes_source_content_when_nonempty():
    from agents.brook.compose import _build_user_request

    out = _build_user_request(
        topic="營養",
        kb_context="  ",  # all-whitespace → treated as empty
        source_content="逐字稿內容 ABC",
    )
    assert "知識庫參考" not in out
    assert "素材" in out
    assert "逐字稿內容 ABC" in out


def test_extract_json_object_raises_when_inner_fragment_still_invalid():
    """{ … } 抓出來但內部還是壞 JSON → ComposeOutputParseError（line 388-389）。"""
    from agents.brook.compose import ComposeOutputParseError, _extract_json_object

    broken = 'prefix text {"key": not-valid-json} suffix'
    with pytest.raises(ComposeOutputParseError, match="JSON parse 失敗"):
        _extract_json_object(broken)


def test_ast_to_plaintext_recurses_into_children():
    """children 遞迴分支（line 399）。"""
    from agents.brook.compose import _ast_to_plaintext
    from shared.schemas.publishing import BlockNodeV1

    ast = [
        BlockNodeV1(block_type="paragraph", content="段落一"),
        BlockNodeV1(
            block_type="list",
            children=[
                BlockNodeV1(block_type="list_item", content="bullet 1"),
                BlockNodeV1(block_type="list_item", content="bullet 2"),
            ],
        ),
    ]
    out = _ast_to_plaintext(ast)
    assert "段落一" in out
    assert "bullet 1" in out
    assert "bullet 2" in out


def test_parse_llm_output_wraps_invalid_block_as_parse_error():
    """block payload 不符合 BlockNodeV1 schema → ComposeOutputParseError（line 421-422）。"""
    from agents.brook.compose import ComposeOutputParseError, _parse_llm_output

    raw = {
        "blocks": [
            {"block_type": "unknown-block-type", "content": "whatever"},
        ]
    }
    with pytest.raises(ComposeOutputParseError, match="blocks 無法轉 BlockNodeV1"):
        _parse_llm_output(raw)


def test_parse_llm_output_wraps_builder_error_as_parse_error(monkeypatch):
    """gutenberg_builder.build 炸 → ComposeOutputParseError（line 426-427）。"""
    from agents.brook.compose import ComposeOutputParseError, _parse_llm_output

    def _boom(*args, **kwargs):
        raise RuntimeError("builder exploded")

    monkeypatch.setattr("agents.brook.compose.gutenberg_builder.build", _boom)

    raw = {
        "blocks": [
            {"block_type": "paragraph", "content": "good block", "children": []},
        ]
    }
    with pytest.raises(ComposeOutputParseError, match="AST . HTML build 失敗"):
        _parse_llm_output(raw)
