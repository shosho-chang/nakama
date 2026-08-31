"""gateway/handlers 單元測試（Nami agent-loop 版本）。"""

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gateway.formatters import format_agent_response, format_event_message
from gateway.handlers import get_handler, list_agents
from gateway.handlers.nami import (
    NAMI_AGENT_FLOW,
    NamiHandler,
    _build_context_preamble,
    _dated_title_suggestion,
    _extract_frontmatter,
    _slugify,
    _strip_context_preamble,
)

NAMI_PERSONA = Path(__file__).resolve().parents[1] / "prompts" / "nami" / "agent_system.md"

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


def test_create_project_tool_executes_and_writes(tmp_path):
    """LLM 呼叫 create_project（2 輪：tool_use → end_turn），ADR-068 極簡 stub 落盤。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("create_project", {"name": "超加工食品"}, id_="toolu_cp1")],
        ),
        _fake_response("end_turn", [_text_block("✅ 已開戰線「超加工食品」")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("shared.config.get_vault_path", return_value=tmp_path),
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("create_project", "開一條超加工食品的戰線", "U1")

    assert "超加工食品" in result.text
    assert result.continuation is not None  # thread 保持存活
    assert result.continuation.state["pending_tool_use_id"] is None
    stub = tmp_path / "Projects" / "超加工食品.md"
    assert stub.is_file()
    raw = stub.read_text(encoding="utf-8")
    assert "type: project" in raw
    assert "status: active" in raw
    mock_emit.assert_called_once()
    assert mock_emit.call_args[0][1] == "project_created"


def test_create_project_conflict_returns_error_to_loop(tmp_path):
    """同名戰線已存在 → tool_result 帶 is_error，loop 由 LLM 決定怎麼做。"""
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "X.md").write_text(
        "---\ntype: project\nstatus: active\n---\n", encoding="utf-8"
    )
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("create_project", {"name": "X"}, id_="toolu_cp2")],
        ),
        _fake_response("end_turn", [_text_block("這條戰線已經存在了，要改名嗎？")]),
    ]

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("shared.config.get_vault_path", return_value=tmp_path),
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        result = NamiHandler().handle("create_project", "新建 X", "U1")

    assert "已經存在" in result.text


# ── Agent loop: create_task tool ────────────────────────────────────


def test_create_task_tool_writes_page(tmp_path):
    # v3-G: Nami now delegates to the shared dual-write creator (the same path the
    # web 新增任務 buttons use), so assert on create_task's kwargs, not write_page.
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
        patch("shared.config.get_vault_path", return_value=tmp_path),
        patch("shared.project_writer.create_task") as mock_create,
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        mock_create.return_value = tmp_path / "TaskNotes" / "Tasks" / "看牙醫.md"
        result = NamiHandler().handle("create_task", "下週三看牙醫", "U1")

    assert "task" in result.text
    mock_create.assert_called_once()
    kwargs = mock_create.call_args.kwargs
    assert kwargs["task_name"] == "看牙醫"
    assert kwargs["scheduled"] == "2026-04-22"
    assert kwargs["project_slug"] is None
    mock_emit.assert_called_once()
    assert mock_emit.call_args[0][1] == "task_created"


def _create_task_via_nami(tmp_path, tool_input, *, sched_side_effect=None):
    """Drive _tool_create_task through the dispatcher with create_task +
    schedule_entry stubbed. Returns (result, mock_schedule_entry, mock_emit)."""
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("create_task", tool_input, id_="toolu_ctp")],
        ),
        _fake_response("end_turn", [_text_block("已建立 task")]),
    ]
    title = tool_input.get("title", "t")
    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("shared.config.get_vault_path", return_value=tmp_path),
        patch("shared.project_writer.create_task") as mock_create,
        patch(
            "shared.calendar_scheduler.schedule_entry", side_effect=sched_side_effect
        ) as mock_sched,
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        mock_create.return_value = tmp_path / "TaskNotes" / "Tasks" / f"{title}.md"
        result = NamiHandler().handle("create_task", "建任務", "U1")
    return result, mock_sched, mock_emit


def test_create_task_scheduled_projects_to_calendar(tmp_path):
    """item 4 forward-fix: 有排程日期 → 投影到 GCal（all_day）+ 連動。"""
    _, mock_sched, _ = _create_task_via_nami(
        tmp_path, {"title": "看牙醫", "scheduled": "2026-04-22"}
    )
    mock_sched.assert_called_once()
    args, kwargs = mock_sched.call_args
    assert args[1] == "看牙醫"  # slug = task file stem
    assert kwargs["all_day"] is True
    assert kwargs["title"] == "看牙醫"
    assert kwargs["pomodoros"] == 4
    assert kwargs["start"].date().isoformat() == "2026-04-22"
    assert kwargs["reason"] is None  # 2026-04-22 是週三（平日）→ 不留多餘理由


def test_create_task_weekend_passes_reason(tmp_path):
    """週末日期要帶 reason，否則 upsert_plan_entry 會擋（WeekendReasonRequired）。"""
    _, mock_sched, _ = _create_task_via_nami(
        tmp_path,
        {"title": "正課拍攝", "scheduled": "2026-04-25"},  # 週六
    )
    assert mock_sched.call_args.kwargs["reason"]  # 非空


def test_create_task_unscheduled_skips_projection(tmp_path):
    _, mock_sched, _ = _create_task_via_nami(tmp_path, {"title": "隨手記"})
    mock_sched.assert_not_called()


def test_create_task_calendar_failure_does_not_block(tmp_path):
    """行事曆掛了 → 建任務仍成功（best-effort，vault 為權威）。"""
    result, _, mock_emit = _create_task_via_nami(
        tmp_path,
        {"title": "看牙醫", "scheduled": "2026-04-22"},
        sched_side_effect=RuntimeError("gcal down"),
    )
    assert "task" in result.text
    mock_emit.assert_called_once()  # task_created 仍 emit


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


# ── Agent loop: 跨日續談時 context preamble 要重新產生 ────────────────
#
# 2026-07-29 事故：thread idle timeout 是 24h，前一天開的 thread 隔天續談時
# messages[0] 仍寫著「今天是 2026-07-28」，Nami 照著把行程排到昨天。

# 這裡刻意寫死形狀（而非呼叫 _build_date_context），才能驗證 _DATE_BLOCK_RE
# 真的認得 production 產出的樣子；形狀漂移由下面的 roundtrip 測試把關。
_STALE_DATE_BLOCK = (
    "## 今日資訊\n"
    "今天是 2026-07-28（週二）。\n"
    "\n"
    "未來 14 天日期對照表（直接查，不要自行推算）：\n"
    "  2026-07-28 週二（今天）\n"
    "  2026-07-29 週三（明天）\n"
    "  2026-07-30 週四"
)


def _today_taipei() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")


def _run_continue_flow(state, reply, *, memory=""):
    """跑一次 continue_flow，回傳送進 LLM 的 messages。"""
    captured = []

    def _capture(**kwargs):
        captured.append(list(kwargs["messages"]))
        return _fake_response("end_turn", [_text_block("好")])

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=_capture),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.agent_memory.format_as_context", return_value=memory),
    ):
        NamiHandler().continue_flow(NAMI_AGENT_FLOW, state, reply, "U1")
    return captured[-1]


def test_continue_flow_replaces_stale_date_block():
    """跨日續談：舊日期要被換掉，而不是留在 context 裡跟新日期打架。"""
    state = {
        "messages": [{"role": "user", "content": f"{_STALE_DATE_BLOCK}\n\n幫我安排今天下午兩點"}],
        "pending_tool_use_id": None,
    }

    first_msg = _run_continue_flow(state, "改成三點")[0]

    assert "2026-07-28（週二）" not in first_msg["content"]
    assert f"今天是 {_today_taipei()}" in first_msg["content"]
    # 只留一份日期事實 — 這是選 B 而非「每輪再附一份」的重點
    assert first_msg["content"].count("## 今日資訊") == 1
    # 使用者原話不能被吃掉
    assert "幫我安排今天下午兩點" in first_msg["content"]


def test_continue_flow_replaces_stale_memory_block():
    """記憶同樣只在 handle() 注入過；續談要重讀，不然新記憶整個 thread 都看不到。"""
    stale = "## 你記得關於使用者的事\n- [fact] 舊事：這條已經過期"
    state = {
        "messages": [{"role": "user", "content": f"{_STALE_DATE_BLOCK}\n\n{stale}\n\n原本的話"}],
        "pending_tool_use_id": None,
    }

    fresh = "## 你記得關於使用者的事\n- [fact] 新事：這條是新的"
    first_msg = _run_continue_flow(state, "嗯", memory=fresh)[0]["content"]

    assert "這條已經過期" not in first_msg
    assert "這條是新的" in first_msg
    assert first_msg.count("## 你記得關於使用者的事") == 1
    assert "原本的話" in first_msg


def test_continue_flow_injects_date_when_preamble_absent():
    """timeout 前就已在飛的舊對話（messages[0] 沒 preamble）也要拿到日期。"""
    state = {
        "messages": [{"role": "user", "content": "幫我建 project"}],
        "pending_tool_use_id": None,
    }

    first_msg = _run_continue_flow(state, "research")[0]

    assert f"今天是 {_today_taipei()}" in first_msg["content"]
    assert "幫我建 project" in first_msg["content"]


def test_continue_flow_leaves_block_list_content_alone():
    """messages[0] 不是字串（理論上不會發生）時安靜跳過，不要炸掉整個 thread。"""
    blocks = [{"type": "text", "text": "hi"}]
    state = {
        "messages": [{"role": "user", "content": blocks}],
        "pending_tool_use_id": None,
    }

    first_msg = _run_continue_flow(state, "嗯")[0]

    assert first_msg["content"] == blocks


def test_strip_context_preamble_roundtrips_real_output():
    """形狀漂移守門員：_build_context_preamble 產的東西必須被 strip 完整拆掉。

    _build_date_context / format_as_context 的輸出格式若改了而
    _DATE_BLOCK_RE / _MEMORY_BLOCK_RE 沒跟上，跨日 bug 會無聲復發。
    """
    memory = "## 你記得關於使用者的事\n- [fact] 船長：修修是船長\n- [pref] 番茄鐘：一顆 30 分鐘"
    with patch("gateway.handlers.nami.agent_memory.format_as_context", return_value=memory):
        parts = _build_context_preamble("U1")

    content = "\n\n".join([*parts, "使用者原本說的話"])
    assert _strip_context_preamble(content) == "使用者原本說的話"


def test_strip_context_preamble_without_memory_block():
    with patch("gateway.handlers.nami.agent_memory.format_as_context", return_value=""):
        parts = _build_context_preamble("U1")

    content = "\n\n".join([*parts, "只有日期"])
    assert _strip_context_preamble(content) == "只有日期"


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
                        "category": "work",
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
    # v3-D：task 帶 per-entry plan[]（date/pomodoros/start/end/calendar_event_id），
    # 不再寫 task 層級 scheduled 鏡像
    mock_write.assert_called_once()
    task_fm = mock_write.call_args.args[1]
    assert "scheduled" not in task_fm and "scheduled_end" not in task_fm
    assert "calendar_event_id" not in task_fm  # lives on the plan entry now
    assert task_fm["title"] == "跟 Angie 開會"
    assert task_fm["status"] == "to-do"
    assert task_fm["category"] == "work"  # LLM-judged category flows to the linked task
    assert task_fm["預估🍅"] == 2  # 60-min event → 2🍅 estimate, else task page shows "-"
    entry = task_fm["plan"][0]
    assert entry["calendar_event_id"] == "evt42"
    assert entry["date"] == "2026-04-25"
    assert entry["start"] == "2026-04-25T15:00:00+08:00"
    assert entry["end"] == "2026-04-25T16:00:00+08:00"
    assert entry["pomodoros"] == 2  # 60 min ÷ 30


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

    def _capture_and_respond(*, messages, tools, system, **kwargs):
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


def test_create_calendar_event_non_work_category_flows_to_task():
    """LLM 判斷的非 work 分類（如 growth）要寫進 calendar-linked task 的 frontmatter。
    回歸測試：先前 _write_calendar_linked_task 硬寫不含 category，導致排程任務一律無分類。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "create_calendar_event",
                    {
                        "title": "讀《原子習慣》",
                        "start": "2026-04-26T20:00:00",
                        "end": "2026-04-26T21:00:00",
                        "category": "growth",
                    },
                    id_="toolu_cce_growth",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("done")]),
    ]

    fake_created = _fake_cal_event(id_="evtRead", title="讀《原子習慣》")
    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.create_event",
            return_value=fake_created,
        ),
        patch("gateway.handlers.nami.list_files", return_value=[]),
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "明晚八點讀原子習慣", "U1")

    mock_write.assert_called_once()
    task_fm = mock_write.call_args.args[1]
    assert task_fm["category"] == "growth"


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


def test_find_calendar_event_date_anchors_window_and_disambiguates():
    """date 參數：搜尋窗收斂到該日附近，且同名週期事件回傳『當天』那一筆，
    而非 list_events 排序下最早的一筆。修遠期(>23天)搜不到 + 同名誤改兩個問題。"""
    jul10 = _fake_cal_event(id_="evt_0710", title="正課拍攝", start="2026-07-10T10:30:00+08:00")
    jul24 = _fake_cal_event(id_="evt_0724", title="正課拍攝", start="2026-07-24T10:30:00+08:00")
    with patch(
        "gateway.handlers.nami.google_calendar.find_events_by_title",
        return_value=[jul10, jul24],
    ) as mock_find:
        found = NamiHandler()._find_calendar_event_by_title("正課拍攝", date="2026-07-24")

    assert found is not None and found.id == "evt_0724"  # 當天那筆，不是最早的 7/10
    kwargs = mock_find.call_args.kwargs
    assert kwargs["time_min"].date().isoformat() == "2026-07-23"
    assert kwargs["time_max"].date().isoformat() == "2026-07-26"


def test_find_calendar_event_no_date_uses_widened_90day_window():
    """沒給 date 時退回 past 7 + future 90 的寬窗（原本只有 future 23，7 月底搜不到）。"""
    evt = _fake_cal_event(id_="e1", title="拍攝")
    with patch(
        "gateway.handlers.nami.google_calendar.find_events_by_title",
        return_value=[evt],
    ) as mock_find:
        found = NamiHandler()._find_calendar_event_by_title("拍攝")

    assert found is not None and found.id == "e1"
    kwargs = mock_find.call_args.kwargs
    assert (kwargs["time_max"] - kwargs["time_min"]).days == 97  # 7 past + 90 future


def test_update_calendar_event_syncs_linked_task():
    """v3-D: Calendar event 改時段 → plan[] 那一筆的 start/end/date 同步更新。"""
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
        "plan:\n"
        "  - date: 2026-04-25\n"
        "    pomodoros: 2\n"
        "    start: 2026-04-25T15:00:00+08:00\n"
        "    end: 2026-04-25T16:00:00+08:00\n"
        "    calendar_event_id: evt42\n"
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
    # v3-D: the plan entry moved; no task-level mirror left behind
    assert "scheduled" not in new_fm and "scheduled_end" not in new_fm
    assert "calendar_event_id" not in new_fm
    entry = new_fm["plan"][0]
    assert entry["calendar_event_id"] == "evt42"
    assert entry["date"] == "2026-04-26"
    assert entry["start"] == "2026-04-26T14:00:00+08:00"
    assert entry["end"] == "2026-04-26T15:00:00+08:00"


def test_update_calendar_event_links_by_title_fallback():
    """N544: event id 沒連到任何 task，但有同名任務（含裸 scheduled、無 event_id）→ 以
    title+date 補配對並寫回 calendar_event_id，而不是靜默跳過（0724/0731 根因）。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "update_calendar_event",
                    {"title": "讀書會", "end": "2026-04-26T15:00:00"},
                    id_="toolu_uce_fallback",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("done")]),
    ]

    existing = _fake_cal_event(id_="evtX", title="讀書會")
    updated = _fake_cal_event(
        id_="evtX",
        title="讀書會",
        start="2026-04-26T14:00:00+08:00",
        end="2026-04-26T15:00:00+08:00",
    )
    # task exists by (fuzzy) title but has only a bare `scheduled` — no plan[], no event id
    fake_task_file = SimpleNamespace(name="讀書會 上午場.md", stem="讀書會 上午場")
    task_content = "---\ntitle: 讀書會 上午場\nstatus: to-do\nscheduled: 2026-04-26T14:00:00\n---\n"

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.find_events_by_title", return_value=[existing]
        ),
        patch("gateway.handlers.nami.google_calendar.find_conflicts", return_value=[]),
        patch("gateway.handlers.nami.google_calendar.update_event", return_value=updated),
        patch("gateway.handlers.nami.list_files", return_value=[fake_task_file]),
        patch("gateway.handlers.nami.read_page", return_value=task_content),
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "讀書會結束時間改 15:00", "U1")

    mock_write.assert_called_once()
    new_fm = mock_write.call_args.args[1]
    assert "scheduled" not in new_fm  # bare mirror retired
    entry = new_fm["plan"][0]
    assert entry["calendar_event_id"] == "evtX"  # link healed
    assert entry["date"] == "2026-04-26"
    assert entry["end"] == "2026-04-26T15:00:00+08:00"


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


def test_delete_calendar_event_keeps_other_plan_entries():
    """v3-D multi-block: 刪掉某天的事件只拔掉那一筆 plan entry，同一個 task 的其他天
    必須保留（不能整檔刪掉）。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [_tool_use_block("delete_calendar_event", {"title": "寫稿"}, id_="toolu_dce_multi")],
        ),
        _fake_response("end_turn", [_text_block("done")]),
    ]

    existing = _fake_cal_event(id_="evt_a", title="寫稿")  # the 6/3 event
    fake_task_file = SimpleNamespace(name="寫稿.md", stem="寫稿")
    task_content = (
        "---\ntitle: 寫稿\nstatus: to-do\nplan:\n"
        "  - date: 2026-06-03\n    pomodoros: 2\n    start: 2026-06-03T09:00:00+08:00\n"
        "    end: 2026-06-03T10:00:00+08:00\n    calendar_event_id: evt_a\n"
        "  - date: 2026-06-05\n    pomodoros: 2\n    start: 2026-06-05T09:00:00+08:00\n"
        "    end: 2026-06-05T10:00:00+08:00\n    calendar_event_id: evt_b\n"
        "---\n"
    )

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
        patch("gateway.handlers.nami.delete_page") as mock_delete_page,
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "刪寫稿 6/3", "U1")

    mock_delete_page.assert_not_called()  # the task file survives — it still has 6/5
    mock_write.assert_called_once()
    new_fm = mock_write.call_args.args[1]
    remaining = new_fm["plan"]
    assert len(remaining) == 1
    assert remaining[0]["calendar_event_id"] == "evt_b"
    assert remaining[0]["date"] == "2026-06-05"


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

    def _capture(*, messages, tools, system, **kwargs):
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


def test_dated_title_suggestion_weekly_recurring_uses_iso_week():
    """「寫電子報」是週期性任務（修修 2026-08-10 裁決）→ 帶 ISO 週號，不帶日期。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime(2026, 8, 10, 11, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    assert _dated_title_suggestion("寫電子報", now) == "寫電子報 26W33"


def test_dated_title_suggestion_year_boundary_week():
    """跨年週（ISO week 可能落在前一年最後一週或後一年第一週）算法要用
    isocalendar() 的 ISO year，不是曆年 year。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    # 2027-01-01 是週五，屬於 ISO 2026 年第 53 週
    now = datetime(2027, 1, 1, 9, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    assert _dated_title_suggestion("寫電子報", now) == "寫電子報 26W53"


def test_dated_title_suggestion_non_recurring_uses_date():
    """不在 allowlist 的任務 → 維持原本的 YYYY-MM-DD 格式。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime(2026, 8, 10, 11, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    assert _dated_title_suggestion("訪問某人", now) == "訪問某人 2026-08-10"


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
                        "relative_path": "AgentOutputs/nami/notes/sales-kit-2026-04.md",
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
    assert call_args[0][0] == "AgentOutputs/nami/notes/sales-kit-2026-04.md"
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
                        "relative_path": "AgentOutputs/nami/notes/../KB/Raw/steal.md",
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
                        "relative_path": "AgentOutputs/nami/notes/existing.md",
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
                    {"relative_path": "AgentOutputs/nami/notes/sales-kit-2026-04.md"},
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
    mock_read.assert_called_once_with("AgentOutputs/nami/notes/sales-kit-2026-04.md")


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
    """list_vault_notes 列出 AgentOutputs/nami/notes/ 下的檔案清單。"""
    from pathlib import Path

    fake_files = [Path("AgentOutputs/nami/notes/a.md"), Path("AgentOutputs/nami/notes/b.md")]

    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "list_vault_notes",
                    {"relative_dir": "AgentOutputs/nami/notes/"},
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
        NamiHandler().handle("general", "AgentOutputs/nami/notes 有什麼", "U1")

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
                        "relative_path": "AgentOutputs/nami/notes/Research/2026-04-21-melatonin.md",
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
    assert "AgentOutputs/nami/notes/Research/" in written_path


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


# ── Persona regression（Bug 2 round 3：ask_zoro escape hatch + social heat + 數字 source） ──
#
# Round 3 補三條規則進 prompts/nami/agent_system.md，這些 test 守住規則不被未來 prompt
# refactor 默默拔掉。Test 只 assert 字串子串存在，不 assert 完整段落 — 給 prompt 風格演進
# 留 wiggle room、但禁忌詞清單與「same-process」概念必須留下。


def _persona_text() -> str:
    return NAMI_PERSONA.read_text(encoding="utf-8")


def test_persona_forbids_zoro_offline_escape_hatch():
    """Bug 2 變形：「Zoro 偵察線掛掉所以我自己抓 Reddit」→ prompt 必須點名禁演此 pattern。"""
    text = _persona_text()
    assert "ask_zoro 失敗 / 不調用時的禁演" in text
    assert "same-process" in text.lower()
    assert "偵察線" in text
    assert "Zoro 不會" in text


def test_persona_lists_social_heat_forbidden_phrases():
    """sans ask_zoro(social_listening) 不可用 social heat 詞包裝 web_search 結果。"""
    text = _persona_text()
    assert "Social heat 描述禁忌詞" in text
    for phrase in ("廣傳", "引發熱議", "獲得大量討論", "現在燒的", "聲量"):
        assert phrase in text, f"social heat 禁忌詞清單缺『{phrase}』"


def test_persona_requires_source_for_numbers():
    """具體數字必附 source / 標 [推測] / 改 vague 寫法 — 防 vague 引用 + 具體數字 hallucination。"""
    text = _persona_text()
    assert "數字 / 統計數據" in text
    assert "vague 引用" in text.lower() or "vague 引用" in text
    # 三條退路都要在 prompt 裡明寫
    assert "附 source" in text
    assert "[推測]" in text
    assert "降級寫法" in text or "降低風險" in text


def test_persona_keeps_taiwan_voice_anchor_from_round1():
    """確保 round 1 (PR #329) 既有的 Taiwan voice anchor + 簡中 leak sentinel 沒被 round 3 壓掉。"""
    text = _persona_text()
    assert "刺胳針" in text
    assert "柳葉刀" in text  # 反向 sentinel — leak 警示對照
    assert "科學人" in text or "Hello醫師" in text  # positive identity anchor


def test_persona_keeps_epistemic_three_label_taxonomy():
    """確保 round 1 三類標籤（事實 / 推測 / 不知道）框架完整保留。"""
    text = _persona_text()
    assert "事實 / 推測 / 不知道" in text
    assert "**[推測]**" in text
    # 三類各自至少出現一次
    for label in ("**事實**", "**推測**", "**不知道**"):
        assert label in text, f"epistemic 標籤『{label}』被壓掉"


# ── /Persona regression ─────────────────────────────────────────────


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


# ── schedule_task_entry (ADR-041 v3-F: Nami executes a Bridge-escalated clash) ──


def test_schedule_task_entry_schedules_existing_task():
    """v3-F: Nami's schedule_task_entry schedules an EXISTING task's plan entry via
    the shared calendar_scheduler (not create_calendar_event), and reports success."""
    from shared import calendar_scheduler

    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "schedule_task_entry",
                    {"task_slug": "寫稿", "date": "2026-06-05", "time": "15:00"},
                    id_="toolu_ste",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("已排好")]),
    ]
    fake_task = SimpleNamespace(title="寫稿", est_pomodoros=4)
    outcome = SimpleNamespace(calendar_status=calendar_scheduler.CREATED, event_id="evt_s")
    seen = {}

    def fake_schedule_entry(vault, slug, **kw):
        seen.update({"slug": slug, **kw})
        return outcome

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("shared.config.get_vault_path", return_value=Path("/tmp/vault")),
        patch(
            "shared.weekly_indexer.WeeklyIndexer",
            return_value=SimpleNamespace(find_task=lambda s: fake_task),
        ),
        patch.object(calendar_scheduler, "schedule_entry", side_effect=fake_schedule_entry),
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "改 15:00", "U1")

    assert seen["slug"] == "寫稿"
    assert seen["all_day"] is False and seen["pomodoros"] == 4
    assert seen["start"].hour == 15
    assert seen["title"] == "寫稿"  # derived from the task, not a client field
    assert mock_emit.call_args[0][1] == "calendar_event_created"


def test_schedule_task_entry_blank_time_is_all_day():
    """v3-F: blank time → all_day=True passed to the scheduler."""
    from shared import calendar_scheduler

    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "schedule_task_entry",
                    {"task_slug": "寫稿", "date": "2026-06-05"},
                    id_="toolu_ste2",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("整天排好")]),
    ]
    fake_task = SimpleNamespace(title="寫稿", est_pomodoros=2)
    outcome = SimpleNamespace(calendar_status=calendar_scheduler.CREATED, event_id="evt_s2")
    seen = {}

    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("shared.config.get_vault_path", return_value=Path("/tmp/vault")),
        patch(
            "shared.weekly_indexer.WeeklyIndexer",
            return_value=SimpleNamespace(find_task=lambda s: fake_task),
        ),
        patch.object(
            calendar_scheduler,
            "schedule_entry",
            side_effect=lambda vault, slug, **kw: (seen.update(kw), outcome)[1],
        ),
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "排整天", "U1")

    assert seen["all_day"] is True


def test_schedule_task_entry_archived_duplicate_blocks_write():
    """schedule_task_entry 找到的 task 若跟 Archive/ 裡「同一份」（dateCreated 相同）
    已完成的紀錄撞了，代表 Tasks/ 殘留檔——擋下不准往上加 plan，不然會再踩一次
    「vault sync 衝突、calendar 有 vault 沒有」（2026-08-10 寫電子報事故）。"""
    from shared import calendar_scheduler

    task_content = (
        "---\ntitle: 寫電子報\nstatus: to-do\ndateCreated: '2026-06-17T03:41:33.278Z'\n---\n"
    )
    archive_content = (
        "---\ntitle: 寫電子報\nstatus: done\ndateCreated: '2026-06-17T03:41:33.278Z'\n---\n"
    )

    def fake_read_page(rel):
        if rel == "TaskNotes/Tasks/寫電子報.md":
            return task_content
        if rel == "TaskNotes/Archive/寫電子報.md":
            return archive_content
        return None

    fake_task = SimpleNamespace(title="寫電子報", est_pomodoros=4)
    archive_file = SimpleNamespace(name="寫電子報.md", stem="寫電子報")

    with (
        patch("gateway.handlers.nami.read_page", side_effect=fake_read_page),
        patch(
            "gateway.handlers.nami.list_files",
            side_effect=lambda d: [archive_file] if d == "TaskNotes/Archive" else [],
        ),
        patch("shared.config.get_vault_path", return_value=Path("/tmp/vault")),
        patch(
            "shared.weekly_indexer.WeeklyIndexer",
            return_value=SimpleNamespace(find_task=lambda s: fake_task),
        ),
        patch.object(calendar_scheduler, "schedule_entry") as mock_schedule,
    ):
        out = NamiHandler()._execute_tool(
            "schedule_task_entry",
            {"task_slug": "寫電子報", "date": "2026-08-10", "time": "10:30"},
        )
    assert out.is_error
    assert "同一份筆記" in out.content
    assert "create_calendar_event" in out.content
    mock_schedule.assert_not_called()


def test_schedule_task_entry_same_title_different_dateCreated_is_not_blocked():
    """同標題但 dateCreated 不同（合法的不同輪次任務，不是殘留檔）→ 不擋。"""
    from shared import calendar_scheduler

    task_content = (
        "---\ntitle: 寫電子報\nstatus: to-do\ndateCreated: '2026-08-01T00:00:00.000Z'\n---\n"
    )
    archive_content = (
        "---\ntitle: 寫電子報\nstatus: done\ndateCreated: '2026-06-17T03:41:33.278Z'\n---\n"
    )

    def fake_read_page(rel):
        if rel == "TaskNotes/Tasks/寫電子報.md":
            return task_content
        if rel == "TaskNotes/Archive/寫電子報.md":
            return archive_content
        return None

    fake_task = SimpleNamespace(title="寫電子報", est_pomodoros=4)
    archive_file = SimpleNamespace(name="寫電子報.md", stem="寫電子報")
    outcome = SimpleNamespace(calendar_status=calendar_scheduler.CREATED, event_id="evt_ok")

    with (
        patch("gateway.handlers.nami.read_page", side_effect=fake_read_page),
        patch(
            "gateway.handlers.nami.list_files",
            side_effect=lambda d: [archive_file] if d == "TaskNotes/Archive" else [],
        ),
        patch("shared.config.get_vault_path", return_value=Path("/tmp/vault")),
        patch(
            "shared.weekly_indexer.WeeklyIndexer",
            return_value=SimpleNamespace(find_task=lambda s: fake_task),
        ),
        patch.object(calendar_scheduler, "schedule_entry", return_value=outcome) as mock_schedule,
    ):
        out = NamiHandler()._execute_tool(
            "schedule_task_entry",
            {"task_slug": "寫電子報", "date": "2026-08-10", "time": "10:30"},
        )
    assert not out.is_error
    mock_schedule.assert_called_once()


def test_calendar_task_conflict_active_task_suggests_schedule_entry():
    """撞到進行中 task → 引導 schedule_task_entry 附 slug，不再引導 silent 降級（PR #1122）。"""
    fake_task_file = SimpleNamespace(name="讀書會.md", stem="讀書會")
    content = "---\ntitle: 讀書會\nstatus: to-do\ntags: [task]\n---\n"
    with (
        patch("gateway.handlers.nami.list_files", return_value=[fake_task_file]),
        patch("gateway.handlers.nami.read_page", return_value=content),
        patch("gateway.handlers.nami.google_calendar.create_event") as mock_create,
    ):
        out = NamiHandler()._execute_tool(
            "create_calendar_event",
            {"title": "讀書會", "start": "2026-04-25T15:00:00", "end": "2026-04-25T16:00:00"},
        )
    assert out.is_error
    assert "schedule_task_entry" in out.content
    assert "讀書會" in out.content
    assert "also_create_task" not in out.content
    mock_create.assert_not_called()


def test_calendar_task_conflict_done_task_suggests_dated_title():
    """撞到已完成 task → 新一輪工作，引導帶日期的新標題（PR #1122，寫電子報事故）。
    「寫電子報」是週期性任務（修修 2026-08-10 裁決），新標題用 ISO 週號而非日期。"""
    fake_task_file = SimpleNamespace(name="寫電子報.md", stem="寫電子報")
    content = "---\ntitle: 寫電子報\nstatus: done\ntags: [task]\n---\n"
    with (
        patch("gateway.handlers.nami.list_files", return_value=[fake_task_file]),
        patch("gateway.handlers.nami.read_page", return_value=content),
        patch("gateway.handlers.nami.google_calendar.create_event") as mock_create,
    ):
        out = NamiHandler()._execute_tool(
            "create_calendar_event",
            {"title": "寫電子報", "start": "2026-07-30T14:00:00", "end": "2026-07-30T16:00:00"},
        )
    assert out.is_error
    assert "已完成" in out.content
    assert re.search(r"寫電子報 \d{2}W\d{2}", out.content)  # 帶 ISO 週號的新標題示例
    assert "schedule_task_entry" not in out.content
    mock_create.assert_not_called()


def test_calendar_task_conflict_with_archived_task_suggests_dated_title():
    """撞到 Archive/ 內的同名 task（歸檔的都是 done）→ 帶日期新標題（PR #1127）。
    「寫電子報」是週期性任務，新標題用 ISO 週號。"""

    def fake_list_files(directory):
        if directory == "TaskNotes/Archive":
            return [SimpleNamespace(name="寫電子報.md", stem="寫電子報")]
        return []  # Tasks/ 已被歸檔清空

    content = "---\ntitle: 寫電子報\nstatus: done\ntags: [task]\n---\n"
    with (
        patch("gateway.handlers.nami.list_files", side_effect=fake_list_files),
        patch("gateway.handlers.nami.read_page", return_value=content),
        patch("gateway.handlers.nami.google_calendar.create_event") as mock_create,
    ):
        out = NamiHandler()._execute_tool(
            "create_calendar_event",
            {"title": "寫電子報", "start": "2026-07-30T14:00:00", "end": "2026-07-30T16:00:00"},
        )
    assert out.is_error
    assert "已完成" in out.content
    assert re.search(r"寫電子報 \d{2}W\d{2}", out.content)
    mock_create.assert_not_called()


def test_calendar_task_conflict_non_recurring_title_keeps_date_suffix():
    """非 allowlist 的一次性撞名任務 → 維持原本的 YYYY-MM-DD 帶日期新標題，
    不要被「寫電子報」的週號規則誤套用到所有任務（修修 2026-08-10 裁決：只對
    明確週期性的任務生效）。"""
    fake_task_file = SimpleNamespace(name="訪問某人.md", stem="訪問某人")
    content = "---\ntitle: 訪問某人\nstatus: done\ntags: [task]\n---\n"
    with (
        patch("gateway.handlers.nami.list_files", return_value=[fake_task_file]),
        patch("gateway.handlers.nami.read_page", return_value=content),
        patch("gateway.handlers.nami.google_calendar.create_event") as mock_create,
    ):
        out = NamiHandler()._execute_tool(
            "create_calendar_event",
            {"title": "訪問某人", "start": "2026-07-30T14:00:00", "end": "2026-07-30T16:00:00"},
        )
    assert out.is_error
    assert re.search(r"訪問某人 \d{4}-\d{2}-\d{2}", out.content)
    mock_create.assert_not_called()


def test_list_tasks_includes_doing_status():
    """TaskNotes plugin 的進行中狀態值是 doing —— 不能被過濾掉（PR #1127）。"""
    fake_task_file = SimpleNamespace(name="進行中的事.md", stem="進行中的事")
    content = "---\ntitle: 進行中的事\nstatus: doing\ntags: [task]\n---\n"
    with (
        patch("gateway.handlers.nami.list_files", return_value=[fake_task_file]),
        patch("gateway.handlers.nami.read_page", return_value=content),
    ):
        out = NamiHandler()._execute_tool("list_tasks", {})
    assert not out.is_error
    assert "進行中的事" in out.content
    assert "[進行中]" in out.content


def test_calendar_only_event_result_discloses_missing_task():
    """also_create_task=false 的工具結果必須自帶「沒建 task」揭露（2026-07-30 事故：
    session 歷史讓模型慣性帶 false 並繞過撞名檢查，只靠 prompt 壓不住）。"""
    fake_event = SimpleNamespace(
        id="ev1",
        title="拍攝影片",
        start="2026-07-30T14:00:00+08:00",
        end="2026-07-30T16:00:00+08:00",
        html_link="https://cal/ev1",
    )
    with (
        patch("gateway.handlers.nami.google_calendar.create_event", return_value=fake_event),
        patch("gateway.handlers.nami.list_files", return_value=[]),
    ):
        out = NamiHandler()._execute_tool(
            "create_calendar_event",
            {
                "title": "拍攝影片",
                "start": "2026-07-30T14:00:00",
                "end": "2026-07-30T16:00:00",
                "also_create_task": False,
            },
        )
    assert not out.is_error
    assert "沒有建 vault task" in out.content
    assert "必須註明" in out.content


def test_create_calendar_event_links_project():
    """修修 2026-08-29 稽核 A：行事曆建任務要能歸屬專案（雙寫：檔名前綴 + projects）。

    這條路以前完全沒有專案概念，且檔名走 _slugify（空格→連字號），連備援的
    「{專案} - 」檔名比對都對不上 —— 實測 vault 26 個任務 0 個歸得到專案。"""
    iter_responses = [
        _fake_response(
            "tool_use",
            [
                _tool_use_block(
                    "create_calendar_event",
                    {
                        "title": "寫訪綱 - 程世嘉",
                        "start": "2026-04-25T15:00:00",
                        "end": "2026-04-25T16:00:00",
                        "category": "work",
                        "project": "肌酸的妙用",
                    },
                    id_="toolu_ccp1",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("已建立")]),
    ]
    fake_created = _fake_cal_event(id_="evt77", title="寫訪綱 - 程世嘉")
    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.google_calendar.create_event", return_value=fake_created),
        patch("gateway.handlers.nami.list_files", return_value=[]),
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "排訪綱時間", "U1")

    rel_path = mock_write.call_args.args[0]
    task_fm = mock_write.call_args.args[1]
    # 檔名：「{專案} - {任務}.md」，空格保留（不再 slug 化成 寫訪綱---程世嘉.md）
    assert rel_path == "TaskNotes/Tasks/肌酸的妙用 - 寫訪綱 - 程世嘉.md"
    assert "---" not in rel_path.split("/")[-1].replace(" - ", "")
    # frontmatter：projects wikilink + 前綴過的 title（與 create_task 同一個 renderer）
    assert task_fm["projects"] == ["[[肌酸的妙用]]"]
    assert task_fm["title"] == "肌酸的妙用 - 寫訪綱 - 程世嘉"
    assert task_fm["category"] == "work"
    assert task_fm["plan"][0]["calendar_event_id"] == "evt77"


def test_create_calendar_event_without_project_stays_standalone():
    """沒帶 project → 維持獨立任務：不寫 projects 鍵、檔名不加前綴（且空格保留）。"""
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
                    id_="toolu_ccp2",
                )
            ],
        ),
        _fake_response("end_turn", [_text_block("已建立")]),
    ]
    with (
        patch("gateway.handlers.nami.ask_with_tools", side_effect=iter_responses),
        patch("gateway.handlers.nami.set_current_agent"),
        patch(
            "gateway.handlers.nami.google_calendar.create_event",
            return_value=_fake_cal_event(id_="evt78", title="跟 Angie 開會"),
        ),
        patch("gateway.handlers.nami.list_files", return_value=[]),
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        NamiHandler().handle("general", "排會議", "U1")

    assert mock_write.call_args.args[0] == "TaskNotes/Tasks/跟 Angie 開會.md"
    assert "projects" not in mock_write.call_args.args[1]
