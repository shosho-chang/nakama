"""gateway/handlers/orchestrator.py — OrchestratorHandler adapter 測試。"""

from __future__ import annotations

from unittest.mock import patch

from gateway.handlers.orchestrator import OrchestratorHandler, _extract_topic
from gateway.orchestrator import BrainstormResult


def test_orchestrator_handler_registered():
    from gateway.handlers import get_handler

    handler = get_handler("orchestrator")
    assert isinstance(handler, OrchestratorHandler)


def test_orchestrator_supports_brainstorm_intent():
    handler = OrchestratorHandler()
    assert handler.can_handle("brainstorm")
    assert handler.can_handle("general")


def test_extract_topic_strips_english_prefix():
    assert _extract_topic("brainstorm 如何戒宵夜") == "如何戒宵夜"
    assert _extract_topic("Brainstorm: autophagy 機制") == "autophagy 機制"


def test_extract_topic_strips_chinese_prefix():
    assert _extract_topic("腦力激盪 如何戒宵夜") == "如何戒宵夜"
    assert _extract_topic("討論一下：autophagy") == "autophagy"


def test_extract_topic_leaves_plain_text_alone():
    # 沒前綴的文字全部當 topic
    assert _extract_topic("如何戒宵夜") == "如何戒宵夜"


def test_handle_calls_run_brainstorm_with_extracted_topic():
    handler = OrchestratorHandler()
    fake_result = BrainstormResult(
        topic="如何戒宵夜",
        participants=["sanji", "robin"],
        views={"sanji": "S", "robin": "R"},
        synthesis="N",
    )

    with patch(
        "gateway.handlers.orchestrator.run_brainstorm",
        return_value=fake_result,
    ) as m:
        response = handler.handle(intent="brainstorm", text="brainstorm 如何戒宵夜", user_id="U1")

    m.assert_called_once_with("如何戒宵夜")
    assert response.blocks is not None
    # header + 2 participant sections + divider + synthesis = 5
    assert len(response.blocks) == 5
    assert "如何戒宵夜" in response.text
