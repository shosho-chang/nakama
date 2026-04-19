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


def test_handle_injects_memory_context_when_available():
    """handle() 應在 user message 開頭附上 agent_memory.format_as_context 的結果。"""
    captured_messages = []

    def _capture(**kwargs):
        captured_messages.append(list(kwargs["messages"]))
        return _fake_response("end_turn", [_text_block("好的")])

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=_capture),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.agent_memory.format_as_context",
            return_value="## 你記得關於使用者的事\n- [fact] 船長：修修是船長",
        ),
    ):
        NamiHandler().handle("general", "嗨", "U1")

    first_user_msg = captured_messages[0][0]
    assert first_user_msg["role"] == "user"
    assert "## 你記得關於使用者的事" in first_user_msg["content"]
    assert "修修是船長" in first_user_msg["content"]


def test_handle_skips_memory_block_when_empty():
    """無記憶時不該出現空的記憶標題。"""
    captured_messages = []

    def _capture(**kwargs):
        captured_messages.append(list(kwargs["messages"]))
        return _fake_response("end_turn", [_text_block("好的")])

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=_capture),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.agent_memory.format_as_context", return_value=""),
    ):
        NamiHandler().handle("general", "嗨", "U1")

    first_user_msg = captured_messages[0][0]
    assert "## 你記得關於使用者的事" not in first_user_msg["content"]


def test_handle_returns_text_on_end_turn():
    """LLM 直接回文字（沒呼叫 tool），handler 返回該文字並保持 thread 存活。"""
    fake = _fake_response("end_turn", [_text_block("你好，我是 Nami！")])
    with (
        patch("gateway.handlers.nami.call_claude_with_tools", return_value=fake),
        patch("gateway.handlers.nami.set_current_agent"),
    ):
        result = NamiHandler().handle("general", "嗨", "U1")
    assert "Nami" in result.text
    # end_turn 後 thread 保持存活（pending_tool_use_id=None 表示等待下一個問題）
    assert result.continuation is not None
    assert result.continuation.state["pending_tool_use_id"] is None


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
    assert result.continuation is not None  # thread 保持存活
    assert result.continuation.state["pending_tool_use_id"] is None
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

    assert result.continuation is not None  # thread 保持存活
    assert result.continuation.state["pending_tool_use_id"] is None
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


# ── Agent loop: update_task tool ────────────────────────────────────


def test_update_task_changes_scheduled():
    """LLM 呼叫 update_task，handler 讀取現有檔案並寫回更新的 frontmatter。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "update_task",
                    {"title": "肌酸的妙用 - Pre-production", "scheduled": "2026-04-20T10:00:00"},
                    id_="toolu_ut1",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("✅ 已更新排程")]),
    ]

    fake_file = SimpleNamespace(
        name="肌酸的妙用---Pre-production.md", stem="肌酸的妙用---Pre-production"
    )
    fake_content = (
        "---\ntitle: 肌酸的妙用 - Pre-production\nstatus: to-do\npriority: normal\n---\n\n"
    )

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.list_files", return_value=[fake_file]),
        patch("gateway.handlers.nami.read_page", return_value=fake_content),
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("update_task", "把肌酸 Pre-production 排到週一早上十點", "U1")

    assert "更新" in result.text
    mock_write.assert_called_once()
    rel_path, fm, _ = mock_write.call_args[0]
    assert fm["scheduled"] == "2026-04-20T10:00:00"
    assert "Pre-production" in rel_path


def test_update_task_not_found_returns_error():
    """找不到 task 時，tool 回 is_error，LLM 應告知使用者。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("update_task", {"title": "不存在的任務"}, id_="toolu_ut2")],
        ),
        _fake_response("end_turn", [_text_block("找不到這個 task。")]),
    ]

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.list_files", return_value=[]),
    ):
        result = NamiHandler().handle("update_task", "改一個不存在的 task", "U1")

    assert "找不到" in result.text


def test_update_task_mark_done():
    """把 status 改成 done。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "update_task",
                    {"title": "看牙醫", "status": "done"},
                    id_="toolu_ut3",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("✅ 已完成")]),
    ]

    fake_file = SimpleNamespace(name="看牙醫.md", stem="看牙醫")
    fake_content = "---\ntitle: 看牙醫\nstatus: to-do\npriority: normal\n---\n\n"

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.list_files", return_value=[fake_file]),
        patch("gateway.handlers.nami.read_page", return_value=fake_content),
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("update_task", "看牙醫完成了", "U1")

    assert "完成" in result.text
    fm = mock_write.call_args[0][1]
    assert fm["status"] == "done"


# ── Agent loop: delete_task tool ─────────────────────────────────────


def test_delete_task_removes_file():
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("delete_task", {"title": "看牙醫"}, id_="toolu_dt1")],
        ),
        _fake_response("end_turn", [_text_block("🗑️ 已刪除")]),
    ]

    fake_file = SimpleNamespace(name="看牙醫.md", stem="看牙醫")
    fake_content = "---\ntitle: 看牙醫\nstatus: to-do\n---\n\n"

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.list_files", return_value=[fake_file]),
        patch("gateway.handlers.nami.read_page", return_value=fake_content),
        patch("gateway.handlers.nami.delete_page", return_value=True) as mock_del,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("delete_task", "把看牙醫刪掉", "U1")

    assert "刪除" in result.text
    mock_del.assert_called_once_with("TaskNotes/Tasks/看牙醫.md")


def test_delete_task_not_found():
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("delete_task", {"title": "不存在"}, id_="toolu_dt2")],
        ),
        _fake_response("end_turn", [_text_block("找不到這個 task")]),
    ]

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.list_files", return_value=[]),
    ):
        result = NamiHandler().handle("delete_task", "刪掉不存在的任務", "U1")

    assert "找不到" in result.text


# ── Agent loop: delete_project tool ──────────────────────────────────


def test_delete_project_with_tasks():
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "delete_project",
                    {"title": "建立專案", "include_tasks": True},
                    id_="toolu_dp1",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("🗑️ 已刪除 project")]),
    ]

    proj_file = SimpleNamespace(name="建立專案.md", stem="建立專案")
    proj_content = "---\ntitle: 建立專案\nstatus: active\n---\n\n"
    task_file = SimpleNamespace(name="建立專案---Filming.md", stem="建立專案---Filming")
    task_content = "---\ntitle: 建立專案 - Filming\nprojects:\n- '[[建立專案]]'\n---\n\n"

    def fake_list_files(dir_: str, suffix: str = ".md"):
        if "Projects" in dir_:
            return [proj_file]
        return [task_file]

    read_map = {
        "Projects/建立專案.md": proj_content,
        "TaskNotes/Tasks/建立專案---Filming.md": task_content,
    }

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.list_files", side_effect=fake_list_files),
        patch("gateway.handlers.nami.read_page", side_effect=lambda p: read_map.get(p)),
        patch("gateway.handlers.nami.delete_page", return_value=True) as mock_del,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("delete_project", "把建立專案給砍掉", "U1")

    assert "刪除" in result.text
    deleted_paths = {call[0][0] for call in mock_del.call_args_list}
    assert "Projects/建立專案.md" in deleted_paths
    assert "TaskNotes/Tasks/建立專案---Filming.md" in deleted_paths


# ── Agent loop: Google Calendar tools ───────────────────────────────


def _fake_cal_event(
    id_="evt1",
    title="讀書會",
    start="2026-04-25T15:00:00+08:00",
    end="2026-04-25T16:00:00+08:00",
    html_link="https://calendar.google.com/evt1",
):
    from shared.google_calendar import CalendarEvent

    return CalendarEvent(id=id_, title=title, start=start, end=end, html_link=html_link)


def test_create_calendar_event_happy_path():
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "create_calendar_event",
                    {
                        "title": "跟 Angie 開會",
                        "start": "2026-04-25T15:00:00",
                        "end": "2026-04-25T16:00:00",
                    },
                    id_="toolu_cce1",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("已建立")]),
    ]

    fake_created = _fake_cal_event(title="跟 Angie 開會")
    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.create_event",
            return_value=fake_created,
        ) as mock_create,
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("general", "排下週會議", "U1")

    assert "Calendar" in result.text or "建立" in result.text
    mock_create.assert_called_once()
    kwargs = mock_create.call_args.kwargs
    assert kwargs["title"] == "跟 Angie 開會"
    assert kwargs["check_conflict"] is True  # force 預設 false
    mock_emit.assert_called_once()
    assert mock_emit.call_args[0][1] == "calendar_event_created"


def test_create_calendar_event_conflict_returns_error():
    """衝突時回傳 list[CalendarEvent]，tool 應回 error outcome（讓 LLM ask_user）。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "create_calendar_event",
                    {
                        "title": "讀書",
                        "start": "2026-04-25T15:00:00",
                        "end": "2026-04-25T16:00:00",
                    },
                    id_="toolu_cce2",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("已回報衝突")]),
    ]

    conflict = _fake_cal_event(title="已有的會議")
    captured_tool_results = []

    def _capture_and_respond(*, messages, tools, system, model):
        # 第二輪看 tool_result 是否標記 is_error=True
        for msg in messages:
            if isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        captured_tool_results.append(block)
        return iter_responses.pop(0)

    with (
        patch(
            "gateway.handlers.nami.call_claude_with_tools",
            side_effect=_capture_and_respond,
        ),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.create_event",
            return_value=[conflict],
        ),
        patch("gateway.handlers.nami.emit") as mock_emit,
    ):
        NamiHandler().handle("general", "排讀書", "U1")

    # 衝突時不應 emit
    mock_emit.assert_not_called()
    # tool_result 應標記 is_error
    assert any(tr.get("is_error") for tr in captured_tool_results)
    assert any("衝突" in tr.get("content", "") for tr in captured_tool_results)


def test_create_calendar_event_force_skips_conflict_check():
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "create_calendar_event",
                    {
                        "title": "覆蓋排",
                        "start": "2026-04-25T15:00:00",
                        "end": "2026-04-25T16:00:00",
                        "force": True,
                    },
                    id_="toolu_cce3",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("ok")]),
    ]

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.create_event",
            return_value=_fake_cal_event(title="覆蓋排"),
        ) as mock_create,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "強制排", "U1")

    assert mock_create.call_args.kwargs["check_conflict"] is False


def test_list_calendar_events_today():
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("list_calendar_events", {"range": "today"}, id_="toolu_lce")],
        ),
        _fake_response("end_turn", [_text_block("done")]),
    ]
    events = [
        _fake_cal_event(
            title="晨跑", start="2026-04-19T07:00:00+08:00", end="2026-04-19T08:00:00+08:00"
        ),
        _fake_cal_event(
            title="午餐", start="2026-04-19T12:00:00+08:00", end="2026-04-19T13:00:00+08:00"
        ),
    ]

    captured_kwargs = {}

    def capture_list(*, time_min, time_max, max_results=30):
        captured_kwargs["time_min"] = time_min
        captured_kwargs["time_max"] = time_max
        return events

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.list_events",
            side_effect=capture_list,
        ),
    ):
        NamiHandler().handle("general", "今天行程", "U1")

    # today range → time_min 是當日 0:00
    assert captured_kwargs["time_min"].hour == 0
    assert (captured_kwargs["time_max"] - captured_kwargs["time_min"]).days == 1


def test_update_calendar_event_by_title():
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "update_calendar_event",
                    {"title": "讀書", "start": "2026-04-26T14:00:00", "end": "2026-04-26T15:00:00"},
                    id_="toolu_uce",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("done")]),
    ]

    existing = _fake_cal_event(id_="evt42", title="讀書會")
    updated = _fake_cal_event(
        id_="evt42",
        title="讀書會",
        start="2026-04-26T14:00:00+08:00",
        end="2026-04-26T15:00:00+08:00",
    )

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.find_events_by_title",
            return_value=[existing],
        ),
        patch(
            "gateway.handlers.nami.google_calendar.find_conflicts",
            return_value=[],
        ),
        patch(
            "gateway.handlers.nami.google_calendar.update_event",
            return_value=updated,
        ) as mock_update,
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "讀書改到 26 號下午 2 點", "U1")

    mock_update.assert_called_once()
    assert mock_update.call_args.args[0] == "evt42"
    assert mock_emit.call_args[0][1] == "calendar_event_updated"


def test_update_calendar_event_not_found():
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "update_calendar_event",
                    {"title": "不存在事件", "start": "2026-04-26T14:00:00"},
                    id_="toolu_uce2",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("找不到")]),
    ]

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.find_events_by_title",
            return_value=[],
        ),
    ):
        result = NamiHandler().handle("general", "改不存在事件", "U1")

    assert "找不到" in result.text


def test_delete_calendar_event_happy_path():
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("delete_calendar_event", {"title": "舊會議"}, id_="toolu_dce")],
        ),
        _fake_response("end_turn", [_text_block("已刪除舊會議")]),
    ]

    existing = _fake_cal_event(id_="evt99", title="舊會議")

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.find_events_by_title",
            return_value=[existing],
        ),
        patch(
            "gateway.handlers.nami.google_calendar.delete_event",
        ) as mock_delete,
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("general", "刪掉舊會議", "U1")

    mock_delete.assert_called_once_with("evt99")
    assert "刪除" in result.text
    assert mock_emit.call_args[0][1] == "calendar_event_deleted"


def test_calendar_tool_auth_error_returns_graceful_message():
    """Token 失效時 tool 應回錯誤訊息，不崩 loop。"""
    from shared.google_calendar import GoogleCalendarAuthError

    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("list_calendar_events", {"range": "today"}, id_="toolu_auth")],
        ),
        _fake_response("end_turn", [_text_block("授權過期請重新登入")]),
    ]

    with (
        patch("gateway.handlers.nami.call_claude_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.list_events",
            side_effect=GoogleCalendarAuthError("Token expired"),
        ),
    ):
        result = NamiHandler().handle("general", "今天行程", "U1")

    # 處理流程不崩，回應文字裡有授權過期訊息
    assert result.text  # 有回訊息


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
