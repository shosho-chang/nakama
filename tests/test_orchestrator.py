"""gateway.orchestrator — brainstorm P1 單元測試。

全部透過 mock `shared.llm.ask` 隔離真 LLM call。驗證 participant selection
+ views 聚合 + Nami synthesis 的編排行為，LLM 內容本身不是這層測試重點。
"""

from __future__ import annotations

from unittest.mock import patch

from gateway import orchestrator
from gateway.orchestrator import (
    BrainstormResult,
    format_brainstorm_blocks,
    run_brainstorm,
    select_participants,
)

# ── select_participants ───────────────────────────────────────────


def test_select_participants_defaults_when_no_keyword_hit() -> None:
    picked = select_participants("autophagy 是什麼")
    assert picked == ["sanji", "robin"]  # _DEFAULT_PARTICIPANTS


def test_select_participants_ranks_by_hit_count() -> None:
    # "飲食" + "習慣" → sanji 命中 2 次
    # "研究" → robin 命中 1 次
    picked = select_participants("聊一下 飲食 與 習慣 研究 的關係")
    assert picked[0] == "sanji"
    assert picked[1] == "robin"


def test_select_participants_respects_max_count() -> None:
    picked = select_participants("研究 文獻 趨勢 關鍵字 文章", max_count=2)
    assert len(picked) == 2


def test_select_participants_never_includes_nami() -> None:
    """Nami 永遠是 synthesizer，不進參與者清單。"""
    # 砸一個任何關鍵字不會命中 Nami profile 的 topic（Nami 根本沒 profile）
    picked = select_participants("飲食 研究 關鍵字 文章", max_count=4)
    assert "nami" not in picked


# ── run_brainstorm ─────────────────────────────────────────────────


def _fake_ask_factory(payloads: dict[str, str]):
    """依 thread-local agent 回傳對應文字；沒設定 agent 回 fallback。"""

    def fake_ask(prompt: str, **kwargs):
        from shared.llm_context import _local

        agent = getattr(_local, "agent", None) or "unknown"
        return payloads.get(agent, f"[{agent} view]")

    return fake_ask


def test_run_brainstorm_empty_topic_short_circuits() -> None:
    result = run_brainstorm("   ")
    assert result.topic == ""
    assert result.participants == []
    assert result.views == {}
    assert "brainstorm 需要一個主題" in result.synthesis


def test_run_brainstorm_calls_ask_for_each_participant_plus_synthesizer() -> None:
    payloads = {
        "sanji": "飲食要從小地方改起。",
        "robin": "autophagy 有多篇 meta-analysis 支持。",
        "nami": "共識：兩邊都同意習慣養成。Action：1... 2... 3...",
    }

    # "飲食 習慣" sanji 命中 2、"研究" robin 命中 1 → sanji 先
    with patch.object(orchestrator, "ask", side_effect=_fake_ask_factory(payloads)) as m:
        result = run_brainstorm("飲食 習慣 研究")

    # 2 participants + 1 synthesizer = 3 ask calls
    assert m.call_count == 3
    assert result.participants == ["sanji", "robin"]
    assert result.views["sanji"] == "飲食要從小地方改起。"
    assert result.views["robin"] == "autophagy 有多篇 meta-analysis 支持。"
    assert "共識" in result.synthesis


def test_run_brainstorm_default_participants_when_topic_not_matched() -> None:
    payloads = {"sanji": "X", "robin": "Y", "nami": "Z"}
    with patch.object(orchestrator, "ask", side_effect=_fake_ask_factory(payloads)):
        result = run_brainstorm("一個沒有任何 domain keyword 的主題")

    assert result.participants == ["sanji", "robin"]
    assert set(result.views.keys()) == {"sanji", "robin"}


def test_run_brainstorm_participant_ask_failure_fills_placeholder() -> None:
    """單一參與者失敗不該讓整個 brainstorm 掛掉。"""

    def fake_ask(prompt: str, **kwargs):
        from shared.llm_context import _local

        agent = getattr(_local, "agent", None)
        if agent == "sanji":
            raise RuntimeError("Grok 503")
        return f"[{agent} view]"

    # sanji 拿 2 hits、robin 拿 1 hit，確保兩者都入選
    with patch.object(orchestrator, "ask", side_effect=fake_ask):
        result = run_brainstorm("飲食 習慣 研究")

    assert "此次暫時沒給出觀點" in result.views["sanji"]
    # Robin 照樣出觀點
    assert result.views.get("robin", "").endswith("view]")


def test_run_brainstorm_synthesizer_failure_fills_placeholder() -> None:
    def fake_ask(prompt: str, **kwargs):
        from shared.llm_context import _local

        agent = getattr(_local, "agent", None)
        if agent == "nami":
            raise RuntimeError("Claude 500")
        return f"[{agent} view]"

    with patch.object(orchestrator, "ask", side_effect=fake_ask):
        result = run_brainstorm("飲食 研究")

    assert "整合階段出狀況" in result.synthesis


# ── format_brainstorm_blocks ───────────────────────────────────────


def test_format_brainstorm_blocks_empty_topic() -> None:
    result = BrainstormResult(topic="", participants=[], views={}, synthesis="請給主題")
    fallback, blocks = format_brainstorm_blocks(result)
    assert fallback == "請給主題"
    assert len(blocks) == 1


def test_format_brainstorm_blocks_structure() -> None:
    result = BrainstormResult(
        topic="autophagy",
        participants=["sanji", "robin"],
        views={"sanji": "S-view", "robin": "R-view"},
        synthesis="N-synth",
    )
    fallback, blocks = format_brainstorm_blocks(result)
    # header + 2 views + divider + synthesis = 5
    assert len(blocks) == 5
    assert blocks[0]["type"] == "header"
    assert "autophagy" in blocks[0]["text"]["text"]
    body_texts = [b.get("text", {}).get("text", "") for b in blocks]
    assert any("S-view" in t for t in body_texts)
    assert any("R-view" in t for t in body_texts)
    assert any("N-synth" in t for t in body_texts)
    assert "autophagy" in fallback
    assert "N-synth" in fallback


# ── set_current_agent 副作用驗證 ────────────────────────────────────


def test_run_brainstorm_sets_thread_local_agent_per_call() -> None:
    """每個參與者呼叫 ask 時，thread-local agent 必須是對應 agent（供 router / cost）。

    Participants 現在並行跑（feedback_parallel_sub_agents.md），所以 sanji/robin
    的 ask 哪個先 append 到 seen_agents 取決於 scheduler。Nami synthesizer 一定
    在兩個 participant future 都回來後才跑，所以穩定在最後。
    """
    import threading

    lock = threading.Lock()
    seen_agents: list[str] = []

    def fake_ask(prompt: str, **kwargs):
        from shared.llm_context import _local

        with lock:
            seen_agents.append(getattr(_local, "agent", None) or "unset")
        return "x"

    with patch.object(orchestrator, "ask", side_effect=fake_ask):
        run_brainstorm("飲食 習慣 研究")

    # participant 順序不固定，但必須是 sanji + robin；synthesizer 一定最後
    assert set(seen_agents[:2]) == {"sanji", "robin"}
    assert seen_agents[2] == "nami"
    assert len(seen_agents) == 3
