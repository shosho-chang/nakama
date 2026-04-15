"""gateway/router.py 單元測試。"""

from unittest.mock import patch

from gateway.router import (
    _match_agent_name,
    _match_intent_keywords,
    _strip_agent_name,
    route_mention,
    route_natural_language,
    route_slash_command,
)

# ── Slash command routing ──


def test_slash_command_nami():
    result = route_slash_command("/nami", "下週三看牙醫")
    assert result.agent == "nami"
    assert result.confidence == "exact"
    assert result.text == "下週三看牙醫"


def test_slash_command_zoro_with_intent():
    result = route_slash_command("/zoro", "間歇性斷食 趨勢")
    assert result.agent == "zoro"
    assert result.intent == "keyword_research"
    assert result.confidence == "exact"


def test_slash_command_nakama_routes_through():
    """'/nakama' 走自然語言路由。"""
    result = route_slash_command("/nakama", "Nami 幫我建個任務")
    assert result.agent == "nami"


# ── Agent name matching ──


def test_match_agent_english():
    assert _match_agent_name("nami help me") == "nami"


def test_match_agent_chinese_name():
    assert _match_agent_name("娜美，下週三看牙醫") == "nami"


def test_match_agent_title():
    assert _match_agent_name("航海士，今天有什麼任務") == "nami"
    assert _match_agent_name("劍士，關鍵字研究") == "zoro"


def test_match_agent_none():
    assert _match_agent_name("幫我查一下東西") is None


def test_match_agent_case_insensitive():
    assert _match_agent_name("ROBIN search") == "robin"
    assert _match_agent_name("Franky status") == "franky"


# ── Intent keyword matching ──


def test_match_intent_task():
    assert _match_intent_keywords("建個任務") == "create_task"


def test_match_intent_list():
    assert _match_intent_keywords("任務清單") == "list_tasks"


def test_match_intent_keyword_research():
    assert _match_intent_keywords("關鍵字研究 間歇性斷食") == "keyword_research"


def test_match_intent_kb():
    assert _match_intent_keywords("知識庫搜尋 NAD+") == "kb_search"


def test_match_intent_status():
    assert _match_intent_keywords("系統狀態") == "system_status"


def test_match_intent_none():
    assert _match_intent_keywords("你好") is None


def test_match_intent_longer_keyword_wins():
    """「任務清單」應優先於「任務」。"""
    result = _match_intent_keywords("列出任務清單")
    assert result == "list_tasks"


# ── Agent name stripping ──


def test_strip_agent_name():
    result = _strip_agent_name("Nami 下週三看牙醫", "nami")
    assert "下週三看牙醫" in result
    assert "nami" not in result.lower()


def test_strip_agent_chinese():
    result = _strip_agent_name("娜美，幫我建任務", "nami")
    assert "幫我建任務" in result


# ── Natural language routing ──


def test_nl_route_with_agent_and_intent():
    result = route_natural_language("Nami 建個任務 買菜")
    assert result.agent == "nami"
    assert result.intent == "create_task"
    assert result.confidence == "keyword"


def test_nl_route_agent_only():
    result = route_natural_language("Robin 你好")
    assert result.agent == "robin"
    assert result.intent == "general"
    assert result.confidence == "keyword"


def test_nl_route_intent_only():
    """有 intent 但沒有 agent 名稱 — 從 INTENT_TO_AGENT 推斷。"""
    result = route_natural_language("幫我查知識庫 NAD+")
    assert result.agent == "robin"
    assert result.intent == "kb_search"
    assert result.confidence == "keyword"


def test_nl_route_haiku_fallback():
    """無法匹配時 fallback 到 Haiku。"""
    mock_response = '{"agent": "franky", "intent": "system_status"}'
    with patch("shared.anthropic_client.ask_claude", return_value=mock_response):
        with patch("shared.anthropic_client.set_current_agent"):
            result = route_natural_language("伺服器還好嗎")
    assert result.agent == "franky"
    assert result.confidence == "haiku"


def test_nl_route_haiku_fallback_error():
    """Haiku 失敗時 default 到 nami。"""
    with patch("shared.anthropic_client.ask_claude", side_effect=Exception("API down")):
        with patch("shared.anthropic_client.set_current_agent"):
            result = route_natural_language("一些隨機的話")
    assert result.agent == "nami"
    assert result.confidence == "haiku"


# ── Mention routing ──


def test_route_mention_strips_user_id():
    result = route_mention("<@U12345ABC> Nami 建任務 買牛奶")
    assert result.agent == "nami"
    assert "U12345ABC" not in result.text
