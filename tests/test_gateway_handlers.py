"""gateway/handlers 單元測試。"""

import json
from unittest.mock import MagicMock, patch

from gateway.formatters import format_agent_response, format_event_message
from gateway.handlers import get_handler, list_agents
from gateway.handlers.nami import (
    PROJECT_BOOTSTRAP_FLOW,
    NamiHandler,
    _extract_frontmatter,
    _slugify,
)

# ── Handler registry ──


def test_get_handler_nami():
    handler = get_handler("nami")
    assert handler is not None
    assert handler.agent_name == "nami"


def test_get_handler_unknown():
    assert get_handler("chopper") is None


def test_list_agents():
    agents = list_agents()
    assert "nami" in agents


# ── BaseHandler ──


def test_can_handle_general():
    handler = NamiHandler()
    assert handler.can_handle("general") is True


def test_can_handle_supported():
    handler = NamiHandler()
    assert handler.can_handle("create_task") is True
    assert handler.can_handle("list_tasks") is True
    assert handler.can_handle("create_project") is True


def test_can_handle_unsupported():
    handler = NamiHandler()
    assert handler.can_handle("keyword_research") is False


def test_suggest_redirect():
    handler = NamiHandler()
    assert handler.suggest_redirect("keyword_research") == "zoro"
    assert handler.suggest_redirect("kb_search") == "robin"
    assert handler.suggest_redirect("create_task") is None


# ── Nami handler ──


def test_nami_create_task():
    """測試 Nami 建立任務（mock Claude + vault）。"""
    task_json = json.dumps(
        {
            "title": "看牙醫",
            "scheduled": "2026-04-22",
            "priority": "normal",
            "notes": "",
        }
    )

    with (
        patch("gateway.handlers.nami.ask_claude", return_value=task_json),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.write_page") as mock_write,
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        handler = NamiHandler()
        result = handler.handle("create_task", "下週三看牙醫", "U123")

    assert "看牙醫" in result.text
    assert "2026-04-22" in result.text
    mock_write.assert_called_once()
    mock_emit.assert_called_once()

    # 驗證 emit payload
    emit_args = mock_emit.call_args
    assert emit_args[0][0] == "nami"
    assert emit_args[0][1] == "task_created"
    assert emit_args[0][2]["title"] == "看牙醫"


def test_nami_create_task_no_date():
    """無日期的任務。"""
    task_json = json.dumps({"title": "買牛奶", "scheduled": None, "priority": "low", "notes": ""})

    with (
        patch("gateway.handlers.nami.ask_claude", return_value=task_json),
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.write_page"),
        patch("gateway.handlers.nami.emit"),
        patch("gateway.handlers.nami.kb_log"),
    ):
        handler = NamiHandler()
        result = handler.handle("create_task", "買牛奶", "U123")

    assert "買牛奶" in result.text


def test_nami_create_task_parse_error():
    """Claude 回傳非 JSON 時的 graceful fallback。"""
    with (
        patch("gateway.handlers.nami.ask_claude", return_value="I cannot parse this"),
        patch("gateway.handlers.nami.set_current_agent"),
    ):
        handler = NamiHandler()
        result = handler.handle("create_task", "some weird input", "U123")

    assert "無法理解" in result.text


def test_nami_list_tasks_empty():
    """Vault 內無任務時。"""
    with (
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.list_files", return_value=[]),
    ):
        handler = NamiHandler()
        result = handler.handle("list_tasks", "", "U123")

    assert "沒有待辦" in result.text


def test_nami_list_tasks_with_data(tmp_path):
    """Vault 內有任務時。"""
    task_content = """\
---
title: 看牙醫
status: to-do
priority: high
scheduled: 2026-04-22
---

"""
    # 模擬 list_files 回傳
    mock_file = MagicMock()
    mock_file.name = "看牙醫.md"
    mock_file.stem = "看牙醫"

    with (
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.list_files", return_value=[mock_file]),
        patch("gateway.handlers.nami.read_page", return_value=task_content),
    ):
        handler = NamiHandler()
        result = handler.handle("list_tasks", "", "U123")

    assert "看牙醫" in result.text
    assert "1 項" in result.text


def test_nami_general_dispatch_list():
    """General intent 含「清單」時走 list_tasks。"""
    with (
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.list_files", return_value=[]),
    ):
        handler = NamiHandler()
        result = handler.handle("general", "列出清單", "U123")

    assert "沒有待辦" in result.text


# ── Nami project_bootstrap flow ──


def _parse_result(
    content_type=None,
    title="超加工食品",
    area=None,
    priority=None,
    search_topic=None,
):
    return json.dumps(
        {
            "title": title,
            "content_type": content_type,
            "area": area,
            "priority": priority,
            "search_topic": search_topic,
        }
    )


def test_nami_create_project_asks_content_type_when_unknown():
    """Parser 未能判斷 content_type 時，Nami 反問。"""
    with (
        patch("gateway.handlers.nami.ask_claude", return_value=_parse_result(content_type=None)),
        patch("gateway.handlers.nami.set_current_agent"),
    ):
        handler = NamiHandler()
        result = handler.handle("create_project", "幫我建立一個超加工食品的 project", "U1")

    assert result.continuation is not None
    assert result.continuation.flow_name == PROJECT_BOOTSTRAP_FLOW
    assert result.continuation.state["step"] == "awaiting_content_type"
    assert "youtube" in result.text and "blog" in result.text and "research" in result.text
    assert "超加工食品" in result.text


def test_nami_create_project_skips_to_confirm_when_content_type_known():
    """Parser 已判斷 content_type 時，直接進 confirm step。"""
    with (
        patch(
            "gateway.handlers.nami.ask_claude",
            return_value=_parse_result(content_type="research", area="health"),
        ),
        patch("gateway.handlers.nami.set_current_agent"),
    ):
        handler = NamiHandler()
        result = handler.handle("create_project", "關於超加工食品的 research project", "U1")

    assert result.continuation is not None
    assert result.continuation.state["step"] == "awaiting_confirm"
    assert result.continuation.state["content_type"] == "research"
    assert "Literature Review" in result.text
    assert "health" in result.text


def test_nami_continue_content_type_parses_reply():
    """continue_flow 收到 content_type 回覆後進 confirm step。"""
    with patch("gateway.handlers.nami.set_current_agent"):
        handler = NamiHandler()
        state = {
            "step": "awaiting_content_type",
            "title": "超加工食品",
            "area": "work",
            "priority": "medium",
            "search_topic": None,
        }
        result = handler.continue_flow(PROJECT_BOOTSTRAP_FLOW, state, "research", "U1")

    assert result.continuation.state["step"] == "awaiting_confirm"
    assert result.continuation.state["content_type"] == "research"


def test_nami_continue_content_type_rejects_garbage():
    """亂打時要重新問，state 保持。"""
    with patch("gateway.handlers.nami.set_current_agent"):
        handler = NamiHandler()
        state = {
            "step": "awaiting_content_type",
            "title": "X",
            "area": "work",
            "priority": "medium",
            "search_topic": None,
        }
        result = handler.continue_flow(PROJECT_BOOTSTRAP_FLOW, state, "嗯嗯", "U1")

    assert result.continuation is not None
    assert result.continuation.state["step"] == "awaiting_content_type"


def test_nami_continue_confirm_cancel_ends_flow():
    with patch("gateway.handlers.nami.set_current_agent"):
        handler = NamiHandler()
        state = {
            "step": "awaiting_confirm",
            "title": "X",
            "content_type": "research",
            "area": "work",
            "priority": "medium",
            "search_topic": None,
            "tasks": ["A", "B", "C"],
        }
        result = handler.continue_flow(PROJECT_BOOTSTRAP_FLOW, state, "取消", "U1")

    assert result.continuation is None
    assert "取消" in result.text


def test_nami_continue_confirm_executes_subprocess():
    """使用者回「確認」後 subprocess.run 會被呼叫並回 success。"""
    fake_stdout = json.dumps(
        {
            "project_path": "Projects/超加工食品.md",
            "task_paths": [
                "TaskNotes/Tasks/超加工食品 - Literature Review.md",
                "TaskNotes/Tasks/超加工食品 - Synthesis.md",
                "TaskNotes/Tasks/超加工食品 - Write-up.md",
            ],
            "content_type": "research",
            "vault_abs_project": "/tmp/vault/Projects/超加工食品.md",
            "obsidian_uri": "obsidian://open?vault=vault&file=Projects/%E8%B6%85",
        }
    )
    proc = MagicMock(returncode=0, stdout=fake_stdout, stderr="")

    with (
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.subprocess.run", return_value=proc) as mock_run,
        patch("gateway.handlers.nami.emit") as mock_emit,
        patch("gateway.handlers.nami.kb_log"),
    ):
        handler = NamiHandler()
        state = {
            "step": "awaiting_confirm",
            "title": "超加工食品",
            "content_type": "research",
            "area": "work",
            "priority": "medium",
            "search_topic": None,
            "tasks": ["Literature Review", "Synthesis", "Write-up"],
        }
        result = handler.continue_flow(PROJECT_BOOTSTRAP_FLOW, state, "確認", "U1")

    assert result.continuation is None
    assert "✅" in result.text
    assert "Projects/超加工食品.md" in result.text
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "--content-type" in args
    assert "research" in args
    assert "--tasks" in args
    mock_emit.assert_called_once()
    emit_args = mock_emit.call_args[0]
    assert emit_args[1] == "project_created"


def test_nami_continue_confirm_handles_conflict():
    """Subprocess exit code 2 (ProjectExistsError) → 友善錯誤訊息。"""
    proc = MagicMock(
        returncode=2,
        stdout='{"error": "ProjectExistsError", "detail": "..."}',
        stderr="",
    )
    with (
        patch("gateway.handlers.nami.set_current_agent"),
        patch("gateway.handlers.nami.subprocess.run", return_value=proc),
    ):
        handler = NamiHandler()
        state = {
            "step": "awaiting_confirm",
            "title": "X",
            "content_type": "research",
            "area": "work",
            "priority": "medium",
            "search_topic": None,
            "tasks": ["A", "B", "C"],
        }
        result = handler.continue_flow(PROJECT_BOOTSTRAP_FLOW, state, "確認", "U1")

    assert result.continuation is None
    assert "同名" in result.text or "已有" in result.text


def test_nami_unknown_flow_raises_not_implemented():
    handler = NamiHandler()
    try:
        handler.continue_flow("some_other_flow", {}, "hi", "U1")
    except NotImplementedError:
        pass
    else:
        raise AssertionError("expected NotImplementedError for unknown flow")


# ── Utility functions ──


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


# ── Formatters ──


def test_format_agent_response():
    fallback, blocks = format_agent_response("nami", "已建立任務", "create_task")
    assert "[nami]" in fallback
    assert "已建立任務" in fallback
    assert len(blocks) == 2
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
    # 應有 handoff block
    assert len(blocks) >= 2
    handoff_text = blocks[-1]["text"]["text"]
    assert "nami" in handoff_text.lower() or "Nami" in handoff_text
