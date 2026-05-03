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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=_capture),
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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=_capture),
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
        patch("gateway.handlers.nami.ask_with_tools", return_value=fake),
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
        patch("gateway.handlers.nami.ask_with_tools", return_value=fake),
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
            "gateway.handlers.nami.ask_with_tools",
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
            "gateway.handlers.nami.ask_with_tools",
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
            "gateway.handlers.nami.ask_with_tools",
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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=_capture),
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
            "gateway.handlers.nami.ask_with_tools",
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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
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
    """預設 also_create_task=True，同時建 calendar + task。"""
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

    fake_created = _fake_cal_event(id_="evt42", title="跟 Angie 開會")
    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.create_event",
            return_value=fake_created,
        ) as mock_create,
        patch("gateway.handlers.nami.list_files", return_value=[]),
        patch("gateway.handlers.nami.write_page") as mock_write,
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
    # 驗證 task 同時建立，frontmatter 帶 calendar_event_id + scheduled_end（剝 tz）
    mock_write.assert_called_once()
    task_fm = mock_write.call_args.args[1]
    assert task_fm["calendar_event_id"] == "evt42"
    assert task_fm["scheduled"] == "2026-04-25T15:00:00"
    assert task_fm["scheduled_end"] == "2026-04-25T16:00:00"
    assert task_fm["title"] == "跟 Angie 開會"
    assert task_fm["status"] == "to-do"


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

    def _capture_and_respond(*, messages, tools, system, model, **kwargs):
        # 第二輪看 tool_result 是否標記 is_error=True
        for msg in messages:
            if isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        captured_tool_results.append(block)
        return iter_responses.pop(0)

    with (
        patch(
            "gateway.handlers.nami.ask_with_tools",
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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.create_event",
            return_value=_fake_cal_event(title="覆蓋排"),
        ) as mock_create,
        patch("gateway.handlers.nami.list_files", return_value=[]),
        patch("gateway.handlers.nami.write_page"),
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "強制排", "U1")

    assert mock_create.call_args.kwargs["check_conflict"] is False


def test_create_calendar_event_also_create_task_false_skips_task():
    """also_create_task=false 只建 calendar，不建 task。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "create_calendar_event",
                    {
                        "title": "Angie 生日",
                        "start": "2026-04-25T15:00:00",
                        "end": "2026-04-25T16:00:00",
                        "also_create_task": False,
                    },
                    id_="toolu_cce4",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("done")]),
    ]

    fake_created = _fake_cal_event(id_="evtBday", title="Angie 生日")
    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.create_event",
            return_value=fake_created,
        ),
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.list_files") as mock_list,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "排生日事件", "U1")

    mock_write.assert_not_called()
    mock_list.assert_not_called()


def test_create_calendar_event_task_title_conflict_aborts_before_calendar():
    """Task 撞名時 pre-check 失敗，不建 calendar（避免孤兒 event）。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "create_calendar_event",
                    {
                        "title": "讀書會",
                        "start": "2026-04-25T15:00:00",
                        "end": "2026-04-25T16:00:00",
                    },
                    id_="toolu_cce5",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("撞名處理完")]),
    ]

    # 偽造 vault 內已有「讀書會」task
    fake_task_file = SimpleNamespace(name="讀書會.md", stem="讀書會")
    existing_content = "---\ntitle: 讀書會\nstatus: to-do\ntags: [task]\n---\n"

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.create_event",
        ) as mock_create,
        patch("gateway.handlers.nami.list_files", return_value=[fake_task_file]),
        patch("gateway.handlers.nami.read_page", return_value=existing_content),
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.emit") as mock_emit,
    ):
        NamiHandler().handle("general", "讀書會排 25 號", "U1")

    # calendar 不應被建立（pre-check 前就 abort）
    mock_create.assert_not_called()
    mock_write.assert_not_called()
    mock_emit.assert_not_called()


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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
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
        patch("gateway.handlers.nami.list_files", return_value=[]),  # 無對應 task
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "讀書改到 26 號下午 2 點", "U1")

    mock_update.assert_called_once()
    assert mock_update.call_args.args[0] == "evt42"
    assert mock_emit.call_args[0][1] == "calendar_event_updated"


def test_update_calendar_event_syncs_linked_task():
    """Calendar event 改時段 → 有 calendar_event_id 的 task 也同步 scheduled/scheduled_end。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "update_calendar_event",
                    {
                        "title": "讀書會",
                        "start": "2026-04-26T14:00:00",
                        "end": "2026-04-26T15:00:00",
                    },
                    id_="toolu_uce_sync",
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

    fake_task_file = SimpleNamespace(name="讀書會.md", stem="讀書會")
    task_content = (
        "---\n"
        "title: 讀書會\n"
        "status: to-do\n"
        "calendar_event_id: evt42\n"
        "scheduled: 2026-04-25T15:00:00\n"
        "scheduled_end: 2026-04-25T16:00:00\n"
        "---\n"
    )

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.find_events_by_title",
            return_value=[existing],
        ),
        patch("gateway.handlers.nami.google_calendar.find_conflicts", return_value=[]),
        patch(
            "gateway.handlers.nami.google_calendar.update_event",
            return_value=updated,
        ),
        patch("gateway.handlers.nami.list_files", return_value=[fake_task_file]),
        patch("gateway.handlers.nami.read_page", return_value=task_content),
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "讀書會改到 26 號", "U1")

    mock_write.assert_called_once()
    new_fm = mock_write.call_args.args[1]
    assert new_fm["scheduled"] == "2026-04-26T14:00:00"
    assert new_fm["scheduled_end"] == "2026-04-26T15:00:00"
    assert new_fm["calendar_event_id"] == "evt42"


def test_update_calendar_event_no_linked_task_silent():
    """Calendar event 沒有對應 task 時，update 不應錯誤也不寫檔。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "update_calendar_event",
                    {"title": "讀書會", "start": "2026-04-26T14:00:00"},
                    id_="toolu_uce_no_task",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("done")]),
    ]

    existing = _fake_cal_event(id_="evt42", title="讀書會")
    updated = _fake_cal_event(id_="evt42", title="讀書會", start="2026-04-26T14:00:00+08:00")

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.find_events_by_title",
            return_value=[existing],
        ),
        patch("gateway.handlers.nami.google_calendar.find_conflicts", return_value=[]),
        patch(
            "gateway.handlers.nami.google_calendar.update_event",
            return_value=updated,
        ),
        patch("gateway.handlers.nami.list_files", return_value=[]),
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "讀書會改到 26 號", "U1")

    mock_write.assert_not_called()


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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.find_events_by_title",
            return_value=[existing],
        ),
        patch(
            "gateway.handlers.nami.google_calendar.delete_event",
        ) as mock_delete,
        patch("gateway.handlers.nami.list_files", return_value=[]),  # 沒對應 task
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("general", "刪掉舊會議", "U1")

    mock_delete.assert_called_once_with("evt99")
    assert "刪除" in result.text
    assert mock_emit.call_args[0][1] == "calendar_event_deleted"


def test_delete_calendar_event_also_deletes_linked_task():
    """Calendar event 刪除 → 有 calendar_event_id 的 task 也跟著刪。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("delete_calendar_event", {"title": "讀書會"}, id_="toolu_dce_sync")],
        ),
        _fake_response("end_turn", [_text_block("done")]),
    ]

    existing = _fake_cal_event(id_="evt42", title="讀書會")
    fake_task_file = SimpleNamespace(name="讀書會.md", stem="讀書會")
    task_content = "---\ntitle: 讀書會\nstatus: to-do\ncalendar_event_id: evt42\n---\n"

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.find_events_by_title",
            return_value=[existing],
        ),
        patch("gateway.handlers.nami.google_calendar.delete_event"),
        patch("gateway.handlers.nami.list_files", return_value=[fake_task_file]),
        patch("gateway.handlers.nami.read_page", return_value=task_content),
        patch("gateway.handlers.nami.delete_page", return_value=True) as mock_delete_page,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "刪讀書會", "U1")

    mock_delete_page.assert_called_once()
    deleted_path = mock_delete_page.call_args.args[0]
    assert "讀書會" in deleted_path


def test_delete_calendar_event_task_not_found_silent():
    """PRD: delete 時找不到對應 task → 靜默跳過，不視為錯誤。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "delete_calendar_event",
                    {"title": "孤兒事件"},
                    id_="toolu_dce_orphan",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("done")]),
    ]

    existing = _fake_cal_event(id_="evtOrphan", title="孤兒事件")

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.find_events_by_title",
            return_value=[existing],
        ),
        patch("gateway.handlers.nami.google_calendar.delete_event"),
        patch("gateway.handlers.nami.list_files", return_value=[]),
        patch("gateway.handlers.nami.delete_page") as mock_delete_page,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "刪孤兒事件", "U1")

    mock_delete_page.assert_not_called()


def test_create_calendar_event_rollback_on_task_write_failure():
    """Task 寫入失敗 → calendar event 自動 rollback（刪除），避免孤兒事件。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "create_calendar_event",
                    {
                        "title": "會議",
                        "start": "2026-04-25T15:00:00",
                        "end": "2026-04-25T16:00:00",
                    },
                    id_="toolu_rollback",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("已 rollback")]),
    ]

    fake_created = _fake_cal_event(id_="evtRollback", title="會議")
    captured_results = []

    def _capture(*, messages, tools, system, model, **kwargs):
        for msg in messages:
            if isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        captured_results.append(block)
        return iter_responses.pop(0)

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=_capture),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.create_event",
            return_value=fake_created,
        ),
        patch(
            "gateway.handlers.nami.google_calendar.delete_event",
        ) as mock_delete_event,
        patch("gateway.handlers.nami.list_files", return_value=[]),
        patch(
            "gateway.handlers.nami.write_page",
            side_effect=OSError("disk full"),
        ),
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "排會議", "U1")

    # 必須觸發 rollback
    mock_delete_event.assert_called_once_with("evtRollback")
    # tool_result 應標記 is_error 且提到 rollback
    assert any(r.get("is_error") and "rollback" in r.get("content", "") for r in captured_results)
    # 不應 emit created event
    mock_emit.assert_not_called()


def test_update_calendar_event_title_rename_write_before_delete():
    """Rename 分支：必須先寫新檔，再刪舊檔（避免 write 失敗時 task 遺失）。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "update_calendar_event",
                    {"title": "讀書會", "new_title": "讀書新會"},
                    id_="toolu_rename",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("done")]),
    ]

    existing = _fake_cal_event(id_="evt42", title="讀書會")
    updated = _fake_cal_event(
        id_="evt42",
        title="讀書新會",
        start="2026-04-25T15:00:00+08:00",
        end="2026-04-25T16:00:00+08:00",
    )

    fake_task_file = SimpleNamespace(name="讀書會.md", stem="讀書會")
    task_content = "---\ntitle: 讀書會\nstatus: to-do\ncalendar_event_id: evt42\n---\n"

    call_order: list[str] = []

    def _track_write(*args, **kwargs):
        call_order.append("write")

    def _track_delete(*args, **kwargs):
        call_order.append("delete")
        return True

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.find_events_by_title",
            return_value=[existing],
        ),
        patch(
            "gateway.handlers.nami.google_calendar.update_event",
            return_value=updated,
        ),
        patch("gateway.handlers.nami.list_files", return_value=[fake_task_file]),
        patch("gateway.handlers.nami.read_page", return_value=task_content),
        patch("gateway.handlers.nami.write_page", side_effect=_track_write),
        patch("gateway.handlers.nami.delete_page", side_effect=_track_delete),
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "讀書會改名成讀書新會", "U1")

    # 必須先 write，再 delete — 若 write 失敗舊檔還在
    assert call_order == ["write", "delete"], f"expected write-then-delete, got {call_order}"


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
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
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
            "gateway.handlers.nami.ask_with_tools",
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


# ── Vault note tools ────────────────────────────────────────────────


def test_write_vault_note_happy_path():
    """LLM 呼叫 write_vault_note，write_page 被呼叫，emit vault_note_written。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "write_vault_note",
                    {
                        "relative_path": "Nami/Notes/sales-kit-2026-04.md",
                        "title": "2026 Q2 報價記錄",
                        "body": "## 報價一覽\n- YouTube 影片：NT$50,000",
                        "tags": ["sales-kit", "quotes"],
                    },
                    id_="toolu_wvn1",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("✅ 筆記已存好")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.read_page", return_value=None),
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("general", "存成 sales kit 筆記", "U1")

    assert result.text
    mock_write.assert_called_once()
    call_args = mock_write.call_args
    assert call_args[0][0] == "Nami/Notes/sales-kit-2026-04.md"
    fm = call_args[0][1]
    assert fm["title"] == "2026 Q2 報價記錄"
    assert fm["tags"] == ["sales-kit", "quotes"]
    mock_emit.assert_called_once()
    assert mock_emit.call_args[0][1] == "vault_note_written"


def test_write_vault_note_rejects_forbidden_path():
    """LLM 嘗試寫 Journals/，VaultRuleViolation 被攔截，回 is_error。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "write_vault_note",
                    {
                        "relative_path": "Journals/secret.md",
                        "title": "不該寫",
                        "body": "test",
                    },
                    id_="toolu_wvn2",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("規則不允許")]),
    ]

    captured_results: list[dict] = []
    call_count = 0

    def _capture_tool_results(messages, **kwargs):
        nonlocal call_count
        resp = iter_responses[call_count]
        call_count += 1
        if call_count > 1:
            for m in messages:
                if m.get("role") == "user":
                    content = m.get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            captured_results.append(block)
        return resp

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=_capture_tool_results),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "存日記", "U1")

    assert any(r.get("is_error") for r in captured_results)
    assert any("Vault 規則違反" in r.get("content", "") for r in captured_results)


def test_write_vault_note_rejects_path_traversal():
    """LLM 傳含 .. 的路徑，VaultRuleViolation 被攔截。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "write_vault_note",
                    {
                        "relative_path": "Nami/Notes/../KB/Raw/steal.md",
                        "title": "偷跑",
                        "body": "test",
                    },
                    id_="toolu_wvn3",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("不行")]),
    ]

    captured_results: list[dict] = []
    call_count = 0

    def _capture(messages, **kwargs):
        nonlocal call_count
        resp = iter_responses[call_count]
        call_count += 1
        if call_count > 1:
            for m in messages:
                if m.get("role") == "user":
                    content = m.get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            captured_results.append(block)
        return resp

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=_capture),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "存", "U1")

    assert any(r.get("is_error") for r in captured_results)


def test_write_vault_note_no_overwrite_by_default():
    """檔案已存在且沒帶 overwrite=true，應回 is_error。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "write_vault_note",
                    {
                        "relative_path": "Nami/Notes/existing.md",
                        "title": "已存在",
                        "body": "new content",
                    },
                    id_="toolu_wvn4",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("已存在提示")]),
    ]

    captured_results: list[dict] = []
    call_count = 0

    def _capture(messages, **kwargs):
        nonlocal call_count
        resp = iter_responses[call_count]
        call_count += 1
        if call_count > 1:
            for m in messages:
                if m.get("role") == "user":
                    content = m.get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            captured_results.append(block)
        return resp

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=_capture),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.read_page", return_value="existing content"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "存", "U1")

    assert any(r.get("is_error") for r in captured_results)
    assert any("已存在" in r.get("content", "") for r in captured_results)


def test_read_vault_note_returns_content():
    """read_vault_note 正確讀取並回傳內容。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "read_vault_note",
                    {"relative_path": "Nami/Notes/sales-kit-2026-04.md"},
                    id_="toolu_rvn1",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("這是你之前的 sales kit")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.read_page", return_value="# 報價\n內容在這") as mock_read,
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("general", "翻舊 sales kit", "U1")

    assert result.text
    mock_read.assert_called_once_with("Nami/Notes/sales-kit-2026-04.md")


def test_read_vault_note_rejects_kb_path():
    """read_vault_note 嘗試讀 KB/Wiki/ 應被規則擋住。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "read_vault_note",
                    {"relative_path": "KB/Wiki/article.md"},
                    id_="toolu_rvn2",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("不能讀")]),
    ]

    captured_results: list[dict] = []
    call_count = 0

    def _capture(messages, **kwargs):
        nonlocal call_count
        resp = iter_responses[call_count]
        call_count += 1
        if call_count > 1:
            for m in messages:
                if m.get("role") == "user":
                    content = m.get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            captured_results.append(block)
        return resp

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=_capture),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "讀 KB", "U1")

    assert any(r.get("is_error") for r in captured_results)


def test_list_vault_notes_returns_files():
    """list_vault_notes 列出 Nami/Notes/ 下的檔案清單。"""
    from pathlib import Path

    fake_files = [Path("Nami/Notes/a.md"), Path("Nami/Notes/b.md")]

    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "list_vault_notes",
                    {"relative_dir": "Nami/Notes/"},
                    id_="toolu_lvn1",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("你有 2 個筆記")]),
    ]

    captured_results: list[dict] = []
    call_count = 0

    def _capture(messages, **kwargs):
        nonlocal call_count
        resp = iter_responses[call_count]
        call_count += 1
        if call_count > 1:
            for m in messages:
                if m.get("role") == "user":
                    content = m.get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            captured_results.append(block)
        return resp

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=_capture),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.list_files", return_value=fake_files),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "Nami/Notes 有什麼", "U1")

    assert any("a.md" in r.get("content", "") for r in captured_results)
    assert any("b.md" in r.get("content", "") for r in captured_results)


# ── Formatters ──────────────────────────────────────────────────────


def test_format_agent_response():
    fallback, blocks = format_agent_response("nami", "已建立任務", "create_task")
    assert "[nami]" in fallback
    assert "已建立任務" in fallback
    assert len(blocks) == 1
    assert blocks[0]["type"] == "section"
    assert "已建立任務" in blocks[0]["text"]["text"]


# ── Web research tools ──────────────────────────────────────────────


def test_web_search_happy_path():
    """LLM 呼叫 web_search，firecrawl_search 被呼叫，回傳格式化候選清單。"""
    fake_results = [
        {"title": "睡眠研究A", "url": "https://example.com/a", "description": "說明A"},
        {"title": "睡眠研究B", "url": "https://example.com/b", "description": ""},
    ]
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("web_search", {"query": "睡眠 研究"}, id_="toolu_ws1")],
        ),
        _fake_response("end_turn", [_text_block("找到以下結果")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("shared.firecrawl_search.firecrawl_search", return_value=fake_results) as mock_search,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("general", "幫我搜尋睡眠研究", "U1")

    assert result.text
    mock_search.assert_called_once_with("睡眠 研究", num_results=10)


def test_web_search_empty_query():
    """web_search 傳入空 query 應回 is_error=True，不呼叫 firecrawl_search。"""
    outcome = NamiHandler()._tool_web_search({"query": "  "})
    assert outcome.is_error is True
    assert "空" in outcome.content


def test_fetch_url_happy_path():
    """LLM 呼叫 fetch_url，scrape_url 被呼叫，回傳內文。"""
    fake_content = "# 睡眠研究\n\n這是內文。" * 10
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("fetch_url", {"url": "https://example.com/a"}, id_="toolu_fu1")],
        ),
        _fake_response("end_turn", [_text_block("讀到了")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("shared.web_scraper.scrape_url", return_value=fake_content) as mock_scrape,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("general", "讀這個 URL", "U1")

    assert result.text
    mock_scrape.assert_called_once_with("https://example.com/a", mode="auto")


def test_fetch_url_truncation():
    """fetch_url 回傳 >20000 字元時應截斷並附上截斷提示。"""
    long_content = "x" * 25000
    with patch("shared.web_scraper.scrape_url", return_value=long_content):
        outcome = NamiHandler()._tool_fetch_url({"url": "https://example.com/long"})

    assert outcome.is_error is False
    assert len(outcome.content) < 25000
    assert "截斷" in outcome.content
    assert outcome.event["payload"]["truncated"] is True


def test_deep_research_flow():
    """端到端 research flow：web_search → fetch_url × 2 → write_vault_note → end_turn。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("web_search", {"query": "褪黑激素 睡眠"}, id_="toolu_dr1")],
        ),
        _fake_response(
            "tool_use",
            [_tool_use_block("fetch_url", {"url": "https://example.com/study1"}, id_="toolu_dr2")],
        ),
        _fake_response(
            "tool_use",
            [_tool_use_block("fetch_url", {"url": "https://example.com/study2"}, id_="toolu_dr3")],
        ),
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "write_vault_note",
                    {
                        "relative_path": "Nami/Notes/Research/2026-04-21-melatonin.md",
                        "title": "褪黑激素與睡眠研究",
                        "body": "## 研究結論\n...",
                        "tags": ["research"],
                    },
                    id_="toolu_dr4",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("✅ 報告存好了")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "shared.firecrawl_search.firecrawl_search",
            return_value=[
                {"title": "Study1", "url": "https://example.com/study1", "description": "desc1"},
            ],
        ),
        patch("shared.web_scraper.scrape_url", return_value="研究內文"),
        patch("gateway.handlers.nami.read_page", return_value=None),
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("general", "幫我做褪黑激素的深度研究", "U1")

    assert result.text
    mock_write.assert_called_once()
    written_path = mock_write.call_args[0][0]
    assert "Nami/Notes/Research/" in written_path


# ── pubmed_lookup tool ──────────────────────────────────────────────


def _pubmed_record(pmid="38945123", title="Test", first_author="Smith J"):
    return {
        "pmid": pmid,
        "title": title,
        "authors": [first_author, "Doe A", "Lee K", "Chen W"],
        "first_author": first_author,
        "journal": "JAMA Internal Medicine",
        "year": "2024",
        "pubdate": "2024 Aug 12",
        "doi": "10.1001/test.2024.1234",
        "pmcid": "PMC12345678",
        "pubtypes": ["Journal Article", "Randomized Controlled Trial"],
    }


def test_pubmed_lookup_tool_renders_markdown():
    """pubmed_lookup tool 接 lookup() 結果，render markdown 含 PubMed/PMC/doi 連結。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "pubmed_lookup",
                    {"query": "intermittent fasting", "max_results": 2, "since_year": 2024},
                    id_="toolu_pl1",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("已找到兩篇")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "shared.pubmed_client.lookup",
            return_value=[
                _pubmed_record(pmid="38945123", title="Time-restricted eating: an RCT"),
                _pubmed_record(pmid="38821456", title="Single-author study", first_author="Solo P"),
            ],
        ) as mock_lookup,
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("general", "查 IF 文獻", "U1")

    mock_lookup.assert_called_once_with("intermittent fasting", max_results=2, since_year=2024)
    # Tool 觸發了 pubmed_lookup event
    event_calls = [c for c in mock_emit.call_args_list if c[0][1] == "pubmed_lookup"]
    assert len(event_calls) == 1
    assert event_calls[0][0][2]["hits"] == 2

    assert result.text == "已找到兩篇"


def test_pubmed_lookup_tool_empty_query_returns_error():
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("pubmed_lookup", {"query": ""}, id_="toolu_pl2")],
        ),
        _fake_response("end_turn", [_text_block("query 不能為空")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("shared.pubmed_client.lookup") as mock_lookup,
    ):
        NamiHandler().handle("general", "查文獻", "U1")

    mock_lookup.assert_not_called()


def test_pubmed_lookup_tool_no_results_returns_friendly_message():
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("pubmed_lookup", {"query": "xxxyyy"}, id_="toolu_pl3")],
        ),
        _fake_response("end_turn", [_text_block("沒找到，換關鍵字")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("shared.pubmed_client.lookup", return_value=[]),
    ):
        result = NamiHandler().handle("general", "查 xxxyyy 文獻", "U1")

    assert result.text == "沒找到，換關鍵字"


def test_pubmed_lookup_tool_propagates_client_error():
    """PubMedClientError → tool_result is_error=True，agent loop 由 LLM 回應。"""
    from shared.pubmed_client import PubMedClientError

    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("pubmed_lookup", {"query": "x"}, id_="toolu_pl4")],
        ),
        _fake_response("end_turn", [_text_block("查詢服務暫時不通，等下再試")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("shared.pubmed_client.lookup", side_effect=PubMedClientError("HTTP 503")),
    ):
        result = NamiHandler().handle("general", "查文獻", "U1")

    assert "服務暫時不通" in result.text


def test_pubmed_lookup_tool_caps_max_results_at_20():
    """max_results > 20 應被夾到 20 才丟給 lookup()。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("pubmed_lookup", {"query": "x", "max_results": 999}, id_="toolu_pl5")],
        ),
        _fake_response("end_turn", [_text_block("ok")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("shared.pubmed_client.lookup", return_value=[]) as mock_lookup,
    ):
        NamiHandler().handle("general", "查文獻", "U1")

    mock_lookup.assert_called_once()
    assert mock_lookup.call_args.kwargs["max_results"] == 20


# ── /pubmed_lookup ──────────────────────────────────────────────────


# ── ask_zoro tool（inter-agent delegation） ─────────────────────────


def test_ask_zoro_trend_check_happy_path():
    """ask_zoro(trend_check) → 呼叫 trends_api.get_trends 並 render 摘要。"""
    fake_trends = {
        "trend_direction": "rising",
        "related_top": [
            {"query": "intermittent fasting", "value": 100},
            {"query": "16:8 fasting", "value": 80},
        ],
        "related_rising": [{"query": "circadian fasting", "value": "+250%"}],
    }
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "ask_zoro",
                    {"query": "fasting", "capability": "trend_check"},
                    id_="toolu_az1",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("斷食最近 3 個月在升")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("agents.zoro.trends_api.get_trends", return_value=fake_trends) as mock_trends,
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("general", "fasting 趨勢如何", "U1")

    mock_trends.assert_called_once_with("fasting")
    event_calls = [c for c in mock_emit.call_args_list if c[0][1] == "ask_zoro"]
    assert len(event_calls) == 1
    assert event_calls[0][0][2]["capability"] == "trend_check"
    assert "斷食" in result.text


def test_ask_zoro_social_listening_uses_health_subreddits_first():
    """social_listening 先試 hot_in_health_subreddits，title 比對成功就用，不退到 fallback。"""
    matched_post = {
        "title": "Daily creatine for cognitive aging",
        "url": "https://reddit.com/r/longevity/abc",
        "score": 312,
        "num_comments": 86,
        "subreddit": "longevity",
    }
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "ask_zoro",
                    {"query": "creatine", "capability": "social_listening"},
                    id_="toolu_az2",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("Reddit 上 creatine 最熱的是這篇")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "agents.zoro.reddit_api.hot_in_health_subreddits",
            return_value=[matched_post],
        ) as mock_hot,
        patch("agents.zoro.reddit_api.search_reddit_posts") as mock_search,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "Reddit 上 creatine 紅嗎", "U1")

    mock_hot.assert_called_once()
    mock_search.assert_not_called()  # 比對到就不退 fallback


def test_ask_zoro_social_listening_falls_back_to_search_when_no_health_match():
    """hot_in_health_subreddits 沒匹配時退到全 Reddit search。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "ask_zoro",
                    {"query": "rare-keyword", "capability": "social_listening"},
                    id_="toolu_az3",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("找到全 Reddit search 結果")]),
    ]
    fallback_post = {
        "title": "rare-keyword discussion",
        "url": "https://reddit.com/r/random/xyz",
        "score": 5,
        "num_comments": 2,
        "subreddit": "random",
    }

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("agents.zoro.reddit_api.hot_in_health_subreddits", return_value=[]),
        patch(
            "agents.zoro.reddit_api.search_reddit_posts",
            return_value={"posts": [fallback_post]},
        ) as mock_search,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "Reddit 上 rare-keyword 紅嗎", "U1")

    mock_search.assert_called_once_with("rare-keyword", max_results=10)


def test_ask_zoro_keyword_research_invokes_zoro_orchestrator():
    """keyword_research → 呼叫 research_keywords()，render keywords + titles + sources。"""
    fake_kr = {
        "keywords": ["fasting", "intermittent fasting", "16:8"],
        "blog_titles": ["斷食的科學基礎", "16:8 一週實踐心得"],
        "sources_used": ["trends", "reddit", "youtube"],
        "sources_failed": [],
        "analysis_summary": "斷食關鍵字機會主要在中文長尾",
    }
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "ask_zoro",
                    {"query": "斷食", "capability": "keyword_research"},
                    id_="toolu_az4",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("Zoro 提了三個方向")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "agents.zoro.keyword_research.research_keywords",
            return_value=fake_kr,
        ) as mock_kr,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "斷食關鍵字研究", "U1")

    mock_kr.assert_called_once_with("斷食", content_type="blog")


def test_ask_zoro_invalid_capability_returns_error():
    """無效 capability → is_error=True，不 import 任何 zoro module。"""
    outcome = NamiHandler()._tool_ask_zoro({"query": "x", "capability": "wrong_cap"})
    assert outcome.is_error is True
    assert "capability 必須是" in outcome.content


def test_ask_zoro_empty_query_returns_error():
    outcome = NamiHandler()._tool_ask_zoro({"query": "  ", "capability": "trend_check"})
    assert outcome.is_error is True
    assert "query" in outcome.content


def test_ask_zoro_zoro_failure_propagates_to_loop():
    """Zoro module 內 raise → 包成 is_error=True 的 _ToolOutcome（不炸 loop）。"""

    def boom(*_a, **_kw):
        raise RuntimeError("Zoro down")

    with patch("agents.zoro.trends_api.get_trends", side_effect=boom):
        outcome = NamiHandler()._tool_ask_zoro({"query": "x", "capability": "trend_check"})

    assert outcome.is_error is True
    assert "Zoro" in outcome.content


# ── /ask_zoro ───────────────────────────────────────────────────────


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
