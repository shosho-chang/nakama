"""gateway/handlers 單元測試（Nami agent-loop 版本）。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gateway.formatters import format_agent_response, format_event_message
from gateway.handlers import get_handler, list_agents
from gateway.handlers.nami import (
    NAMI_AGENT_FLOW,
    NamiHandler,
    _extract_frontmatter,
    _slugify,
)

# ── Helpers ─────────────────────────────────────────────────────────


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name, input_, id_="toolu_abc"):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def _fake_response(stop_reason, blocks):
    return SimpleNamespace(stop_reason=stop_reason, content=blocks)


# ── Handler registry ────────────────────────────────────────────────


def test_get_handler_nami():
    handler = get_handler("nami")
    assert handler is not None
    assert handler.agent_name == "nami"


def test_get_handler_unknown():
    assert get_handler("chopper") is None


def test_list_agents():
    assert "nami" in list_agents()


# ── BaseHandler surface ─────────────────────────────────────────────


def test_can_handle_general():
    assert NamiHandler().can_handle("general") is True


def test_can_handle_supported():
    handler = NamiHandler()
    assert handler.can_handle("create_task") is True
    assert handler.can_handle("list_tasks") is True
    assert handler.can_handle("create_project") is True


def test_can_handle_unsupported():
    assert NamiHandler().can_handle("keyword_research") is False


def test_suggest_redirect():
    handler = NamiHandler()
    assert handler.suggest_redirect("keyword_research") == "zoro"
    assert handler.suggest_redirect("kb_search") == "robin"
    assert handler.suggest_redirect("create_task") is None


# ── Agent loop: end_turn path ───────────────────────────────────────


def test_handle_returns_text_on_end_turn():
    """LLM 直接回文字（沒呼叫 tool），handler 返回該文字。"""
    fake = _fake_response("end_turn", [_text_block("你好，我是 Nami！")])
    with (
        patch("gateway.handlers.nami.call_claude_with_tools", return_value=fake),
        patch("gateway.handlers.nami.set_current_agent"),
    ):
        result = NamiHandler().handle("general", "嗨", "U1")
    assert "Nami" in result.text
    assert result.continuation is None


# ── Agent loop: ask_user pauses loop ────────────────────────────────


def test_ask_user_tool_triggers_continuation():
    """LLM 呼叫 ask_user，handler 應回 Continuation 並把問題給使用者。"""
    fake = _fake_response(
        "tool_use",
        [
            _tool_use_block(
                "ask_user",
                {
                    "question": "要建立什麼主題的 project？",
                    "options": ["超加工食品", "深度睡眠"],
                },
                id_="toolu_q1",
            )
        ],
    )
    with (
        patch("gateway.handlers.nami.call_claude_with_tools", return_value=fake),
        patch("gateway.handlers.nami.set_current_agent"),
    ):
        result = NamiHandler().handle("general", "幫我建立 project", "U1")

    assert result.continuation is not None
    assert result.continuation.flow_name == NAMI_AGENT_FLOW
    assert result.continuation.state["pending_tool_use_id"] == "toolu_q1"
    assert "主題" in result.text
    # options 應該被印出
    assert "超加工食品" in result.text


# ── Agent loop: create_project tool ─────────────────────────────────


def test_create_project_tool_executes_and_writes():
    """LLM 呼叫 create_project（2 輪：tool_use → end_turn），writer 被呼叫。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "create_project",
                    {
                        "topic": "超加工食品",
                        "content_type": "research",
                        "area": "health",
                        "priority": "medium",
                    },
                    id_="toolu_cp1",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("✅ 已建立「超加工食品」project")]),
    ]

    fake_writer_result = SimpleNamespace(
        project_path=MagicMock(parts=("vault", "Projects", "超加工食品.md"), name="超加工食品.md"),
        task_paths=[
            MagicMock(
                parts=(
                    "vault",
                    "TaskNotes",
                    "Tasks",
                    "超加工食品 - Literature Review.md",
                ),
                name="超加工食品 - Literature Review.md",
            )
        ],
    )

    with (
        patch(
            "gateway.handlers.nami.call_claude_with_tools",
            side_effect=iter_responses,
        ),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.create_project_with_tasks",
            return_value=fake_writer_result,
        ) as mock_writer,
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("create_project", "關於超加工食品的研究 project", "U1")

    assert "超加工食品" in result.text
    assert result.continuation is None
    mock_writer.assert_called_once()
    call_kwargs = mock_writer.call_args.kwargs
    assert call_kwargs["title"] == "超加工食品"
    assert call_kwargs["content_type"] == "research"
    assert call_kwargs["area"] == "health"
    mock_emit.assert_called_once()
    assert mock_emit.call_args[0][1] == "project_created"


def test_create_project_conflict_returns_error_to_loop():
    """若 writer raise ProjectExistsError，tool_result 帶 is_error，loop 由 LLM 決定怎麼做。"""
    from shared.lifeos_writer import ProjectExistsError

    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "create_project",
                    {"topic": "X", "content_type": "research"},
                    id_="toolu_cp2",
                )
            ],
        ),
        _fake_response(
            "end_turn",
            [_text_block("這個 project 已經存在了，要改標題嗎？")],
        ),
    ]

    with (
        patch(
            "gateway.handlers.nami.call_claude_with_tools",
            side_effect=iter_responses,
        ),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.create_project_with_tasks",
            side_effect=ProjectExistsError("already"),
        ),
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("create_project", "新建 X", "U1")

    assert "已經存在" in result.text


# ── Agent loop: create_task tool ────────────────────────────────────


def test_create_task_tool_writes_page():
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "create_task",
                    {
                        "title": "看牙醫",
                        "scheduled": "2026-04-22",
                        "priority": "normal",
                    },
                    id_="toolu_ct1",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("已建立 task")]),
    ]

    with (
        patch(
            "gateway.handlers.nami.call_claude_with_tools",
            side_effect=iter_responses,
        ),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("create_task", "下週三看牙醫", "U1")

    assert "task" in result.text
    mock_write.assert_called_once()
    fm = mock_write.call_args[0][1]
    assert fm["title"] == "看牙醫"
    assert fm["scheduled"] == "2026-04-22"
    mock_emit.assert_called_once()
    assert mock_emit.call_args[0][1] == "task_created"


# ── Agent loop: continue_flow with user reply ───────────────────────


def test_continue_flow_feeds_user_reply_as_tool_result():
    """使用者在 thread 回覆 → 當成 ask_user 的 tool_result 塞回 loop → 繼續。"""
    fake_followup = _fake_response("end_turn", [_text_block("好的，要幫你建立 research project")])

    state = {
        "messages": [
            {"role": "user", "content": "幫我建 project"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_q1",
                        "name": "ask_user",
                        "input": {"question": "要什麼類型？"},
                    }
                ],
            },
        ],
        "pending_tool_use_id": "toolu_q1",
    }

    captured_messages = []

    def _capture(**kwargs):
        captured_messages.append(list(kwargs["messages"]))
        return fake_followup

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=_capture),
        patch("gateway.handlers.nami.set_current_agent"),
    ):
        result = NamiHandler().continue_flow(NAMI_AGENT_FLOW, state, "research", "U1")

    assert result.continuation is None
    assert "research" in result.text
    # 驗證塞回去的 tool_result
    last_messages = captured_messages[-1]
    last_user_msg = last_messages[-1]
    assert last_user_msg["role"] == "user"
    assert last_user_msg["content"][0]["type"] == "tool_result"
    assert last_user_msg["content"][0]["tool_use_id"] == "toolu_q1"
    assert last_user_msg["content"][0]["content"] == "research"


def test_continue_flow_unknown_raises_not_implemented():
    try:
        NamiHandler().continue_flow("some_other_flow", {}, "hi", "U1")
    except NotImplementedError:
        return
    raise AssertionError("expected NotImplementedError for unknown flow")


def test_continue_flow_missing_state_returns_graceful_error():
    """state 沒 messages / pending_id 時返回友善訊息，不 crash。"""
    with patch("gateway.handlers.nami.set_current_agent"):
        result = NamiHandler().continue_flow(NAMI_AGENT_FLOW, {}, "hi", "U1")
    assert "異常" in result.text or "重置" in result.text


# ── Agent loop: list_tasks tool ─────────────────────────────────────


def test_list_tasks_tool_empty():
    iter_responses = [
        _fake_response("tool_use", [_tool_use_block("list_tasks", {}, id_="toolu_lt1")]),
        _fake_response("end_turn", [_text_block("目前沒有待辦任務。")]),
    ]
    with (
        patch(
            "gateway.handlers.nami.call_claude_with_tools",
            side_effect=iter_responses,
        ),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.list_files", return_value=[]),
    ):
        result = NamiHandler().handle("list_tasks", "今天有什麼", "U1")
    assert "沒有待辦" in result.text


# ── Agent loop: max iters safety ────────────────────────────────────


def test_max_iters_safety_break():
    """若 LLM 一直 tool_use 不收尾，handler 達 max iters 會終止而非無限迴圈。"""
    # 一直回 list_tasks（safe tool，不會 side-effect）
    infinite_tool_use = _fake_response(
        "tool_use", [_tool_use_block("list_tasks", {}, id_="toolu_x")]
    )
    with (
        patch(
            "gateway.handlers.nami.call_claude_with_tools",
            return_value=infinite_tool_use,
        ),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.list_files", return_value=[]),
    ):
        result = NamiHandler().handle("general", "列表", "U1")

    assert "最大" in result.text or "迴圈" in result.text


# ── Utility functions ───────────────────────────────────────────────


def test_slugify_chinese():
    assert _slugify("看牙醫") == "看牙醫"


def test_slugify_mixed():
    slug = _slugify("NAD+ 研究報告！")
    assert "NAD" in slug
    assert "!" not in slug


def test_slugify_long():
    long_title = "這是一個非常非常長的標題" * 10
    assert len(_slugify(long_title)) <= 60


def test_extract_frontmatter_valid():
    content = "---\ntitle: test\nstatus: to-do\n---\n\nbody"
    fm = _extract_frontmatter(content)
    assert fm["title"] == "test"
    assert fm["status"] == "to-do"


def test_extract_frontmatter_empty():
    assert _extract_frontmatter("no frontmatter") == {}


def test_extract_frontmatter_incomplete():
    assert _extract_frontmatter("---\ntitle: test") == {}


# ── Formatters ──────────────────────────────────────────────────────


def test_format_agent_response():
    fallback, blocks = format_agent_response("nami", "已建立任務", "create_task")
    assert "[nami]" in fallback
    assert "已建立任務" in fallback
    assert len(blocks) == 1
    assert blocks[0]["type"] == "section"
    assert "Nami" in blocks[0]["text"]["text"]


def test_format_event_message():
    payload = {"title": "研究完成", "path": "reports/intel.md"}
    fallback, blocks = format_event_message("zoro", "intel_ready", payload)
    assert "zoro" in fallback
    assert "intel_ready" in fallback


def test_format_event_message_with_handoff():
    payload = {
        "title": "研究完成",
        "suggest_handoff": {"target": "nami", "reason": "建議建立任務"},
    }
    _, blocks = format_event_message("zoro", "intel_ready", payload)
    assert len(blocks) >= 2
    handoff_text = blocks[-1]["text"]["text"]
    assert "nami" in handoff_text.lower() or "Nami" in handoff_text
