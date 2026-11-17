"""Slack 格式 → 網站 Markdown（2026-11-17 AI Agent 活動：表情變代碼、問卷連結壞掉、粗體變斜體）。"""

from gateway.handlers.slack_markdown import emojize, to_markdown

FORM = "https://docs.google.com/forms/d/e/1FAIpQLSf/viewform"


def test_real_case_from_ai_agent_event():
    src = (
        ":ship: *自由艦隊船長集合！*\n"
        "• *第一小時｜搞懂 AI Agent*：從概念到應用。\n"
        f":clipboard:課前問卷：<{FORM}|docs.google.com/forms/…/viewform>\n"
        "帶上你想交給 AI 的工作就行。:wink:"
    )
    out = to_markdown(src)
    assert out == (
        "🚢 **自由艦隊船長集合！**\n"
        "• **第一小時｜搞懂 AI Agent**：從概念到應用。\n"
        f"📋課前問卷：[{FORM}]({FORM})\n"
        "帶上你想交給 AI 的工作就行。😉"
    )


def test_links_keep_custom_label_and_bare_urls():
    assert to_markdown("<https://a.tw/x|報名表>") == "[報名表](https://a.tw/x)"
    assert to_markdown("<https://a.tw/x>") == "[https://a.tw/x](https://a.tw/x)"
    assert to_markdown("<https://a.tw/x|a.tw/x>") == "[https://a.tw/x](https://a.tw/x)"
    assert to_markdown("<mailto:a@b.tw|a@b.tw>") == "a@b.tw"


def test_unknown_emoji_removed_mentions_dropped_entities_unescaped():
    assert emojize("嗨:custom_party_parrot:") == "嗨"
    assert to_markdown("<!here> 請看 <#C123|freedom-fleet>") == " 請看 #freedom-fleet"
    assert to_markdown("A &amp; B &lt;3") == "A & B <3"


def test_bold_next_to_chinese_and_strike():
    assert to_markdown("請*準時*參加") == "請**準時**參加"
    assert to_markdown("~取消~") == "~~取消~~"
    assert to_markdown("**已經是 Markdown**") == "**已經是 Markdown**"
    assert to_markdown("3 * 4 * 5") == "3 * 4 * 5"


def test_event_post_payload_is_converted():
    from gateway.handlers import sanji_event

    state = {
        "event": {
            "title": ":ship: AI Agent",
            "description": ":ship: *重點*：<https://a.tw/f|a.tw/f>",
            "start": "2026-11-19T19:00:00+08:00",
            "end": "2026-11-19T21:00:00+08:00",
            "is_online": True,
            "kind": "live",
        },
        "progress": {"cover": {"url": "https://x/c.jpg"}, "zoom": {"join_url": "https://zoom"}},
    }
    payload = sanji_event._post_payload(state, "topic-discussion")
    md = "🚢 **重點**：[https://a.tw/f](https://a.tw/f)"
    assert payload["message"] == md
    assert payload["event"]["description_md"] == md
    assert payload["event"]["title"] == "🚢 AI Agent"
