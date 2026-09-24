"""gateway/slack_files.py + bot.py 附件信件接線測試。

情境：修修用 Gmail「傳到 Slack」把信轉給 Nami。Slack 訊息 text 只有他打的字，
信件本體是 filetype=email 的附檔；gateway 以前只讀 text，Nami 看不到信。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gateway.slack_files import MAX_EMAIL_BODY_CHARS, attachment_context, with_attachments

# files.info 回傳結構照 2026-09-24 實際 Gmail 分享檔（F0C3XEJC293）的欄位，內容簡化
EMAIL_FILE_FULL = {
    "id": "F0EMAIL",
    "filetype": "email",
    "mode": "email",
    "title": "Podcast《來PD的客廳坐坐》訪綱",
    "subject": "Podcast《來PD的客廳坐坐》訪綱",
    "from": [
        {
            "address": "producer@example.com",
            "name": "製作人",
            "original": "製作人 <producer@example.com>",
        }
    ],
    "to": [{"address": "shosho@shosho.tw", "name": "", "original": "shosho@shosho.tw"}],
    "cc": [],
    "headers": {"date": "Wed, 23 Sep 2026 20:43:06 +0800"},
    "attachments": [{"filename": "S1EP03.pdf"}, {"filename": "節目簡介.pdf"}],
    "plain_text": "Hi, 修修\n10/5（一）下午五點 google meet 上見",
}

# message event 裡的 file 物件是殘缺版，沒有內文
EMAIL_FILE_IN_EVENT = {
    "id": "F0EMAIL",
    "filetype": "email",
    "mode": "email",
    "title": EMAIL_FILE_FULL["title"],
}


def _client(file: dict | None = None, error: Exception | None = None) -> MagicMock:
    client = MagicMock()
    if error is not None:
        client.files_info.side_effect = error
    else:
        client.files_info.return_value = {"ok": True, "file": file or EMAIL_FILE_FULL}
    return client


def test_email_attachment_rendered_with_headers_and_body():
    client = _client()
    out = attachment_context({"files": [EMAIL_FILE_IN_EVENT]}, client)

    client.files_info.assert_called_once_with(file="F0EMAIL")
    assert "主旨：Podcast《來PD的客廳坐坐》訪綱" in out
    assert "寄件人：製作人 <producer@example.com>" in out
    assert "日期：Wed, 23 Sep 2026 20:43:06 +0800" in out
    assert "S1EP03.pdf" in out
    assert "10/5（一）下午五點 google meet 上見" in out


def test_no_files_means_no_context_and_no_api_call():
    client = _client()
    assert attachment_context({"text": "hi"}, client) == ""
    assert with_attachments("hi", {"text": "hi"}, client) == "hi"
    client.files_info.assert_not_called()


def test_non_email_files_ignored():
    client = _client()
    event = {"files": [{"id": "F1", "filetype": "png", "mode": "hosted"}]}
    assert attachment_context(event, client) == ""
    client.files_info.assert_not_called()


def test_read_failure_tells_agent_the_attachment_exists():
    """讀不到時不能讓 agent 回「你沒附」— 要明講附件讀取失敗。"""
    client = _client(error=RuntimeError("missing_scope"))
    out = attachment_context({"files": [EMAIL_FILE_IN_EVENT]}, client)
    assert "Podcast《來PD的客廳坐坐》訪綱" in out
    assert "讀取內容失敗" in out


def test_long_body_truncated():
    long_file = {**EMAIL_FILE_FULL, "plain_text": "字" * (MAX_EMAIL_BODY_CHARS + 500)}
    out = attachment_context({"files": [EMAIL_FILE_IN_EVENT]}, _client(long_file))
    assert "字" * MAX_EMAIL_BODY_CHARS in out
    assert "字" * (MAX_EMAIL_BODY_CHARS + 1) not in out
    assert "已截斷" in out


def test_with_attachments_appends_after_user_text():
    out = with_attachments("增加這個行程", {"files": [EMAIL_FILE_IN_EVENT]}, _client())
    assert out.startswith("增加這個行程\n\n[附件信件]")


# ── bot.py 接線 ─────────────────────────────────────────────────────────


def _dm_event(text: str) -> dict:
    return {
        "text": text,
        "user": "U1",
        "channel": "D1",
        "channel_type": "im",
        "ts": "1.0",
        "files": [EMAIL_FILE_IN_EVENT],
    }


def _run_dm(text: str, route_text: str | None = None):
    from gateway import bot

    handler = MagicMock()
    handler.handle.return_value = SimpleNamespace(text="ok", continuation=None)
    store = MagicMock()
    store.get.return_value = None
    store.get_latest_for_user_and_agent.return_value = None
    route = SimpleNamespace(
        agent="nami", intent="general", text=route_text or text, confidence="keyword"
    )

    with (
        patch.object(bot, "get_store", return_value=store),
        patch.object(bot, "get_handler", return_value=handler),
        patch.object(bot, "route_mention", return_value=route) as route_mock,
    ):
        bot._make_thread_message_handler("nami")(_dm_event(text), MagicMock(), _client())
    return handler, route_mock


def test_dm_passes_email_to_agent_but_routes_on_user_text_only():
    handler, route_mock = _run_dm("增加這個行程")

    route_mock.assert_called_once_with("增加這個行程")
    intent, body, user_id = handler.handle.call_args.args
    assert body.startswith("增加這個行程\n\n[附件信件]")
    assert "10/5（一）下午五點 google meet 上見" in body


def test_dm_with_only_forwarded_email_still_reaches_agent():
    """沒打字、只轉信 → 以前會被 `if not text: return` 丟掉。"""
    handler, route_mock = _run_dm("")

    route_mock.assert_not_called()
    intent, body, _ = handler.handle.call_args.args
    assert intent == "general"
    assert body.startswith("[附件信件]")


def test_mention_passes_email_to_agent():
    from gateway import bot

    handler = MagicMock()
    handler.handle.return_value = SimpleNamespace(text="ok", continuation=None)
    route = SimpleNamespace(
        agent="nami", intent="general", text="增加這個行程", confidence="keyword"
    )
    event = {**_dm_event("<@U0NAMI> 增加這個行程"), "channel": "C1"}

    with (
        patch.object(bot, "get_handler", return_value=handler),
        patch.object(bot, "route_mention", return_value=route),
        patch.object(bot, "_register_continuation"),
    ):
        bot._make_mention_handler("nami")(event, MagicMock(), _client())

    _, body, _ = handler.handle.call_args.args
    assert "[附件信件] 主旨：Podcast《來PD的客廳坐坐》訪綱" in body


# ── 信件的 PDF 附檔（訪綱）─────────────────────────────────────────────


def _email_with(attachments: list[dict]) -> dict:
    return {**EMAIL_FILE_FULL, "attachments": attachments}


PDF_ATT = {
    "filename": "S1EP03.pdf",
    "mimetype": "application/pdf",
    "size": 127801,
    "url": "https://files-origin.slack.com/files-email-priv/x/s1ep03.pdf",
}


def _pdf_client(file: dict) -> MagicMock:
    client = _client(file)
    client.token = "xoxb-test"
    return client


def test_pdf_attachment_text_is_included():
    client = _pdf_client(
        _email_with([PDF_ATT, {"filename": "photo.jpg", "mimetype": "image/jpeg"}])
    )
    with (
        patch("gateway.slack_files._download", return_value=b"%PDF") as dl,
        patch("gateway.slack_files.pdf_to_text", return_value="時間：2026 年 10 月 16 日（五）"),
    ):
        out = attachment_context({"files": [EMAIL_FILE_IN_EVENT]}, client)

    dl.assert_called_once_with(PDF_ATT["url"], "xoxb-test")
    assert "[信件附檔 PDF：S1EP03.pdf]\n時間：2026 年 10 月 16 日（五）" in out
    # 非 PDF 附檔只列檔名，PDF 不再出現在「未讀取」清單
    assert "信件附檔（未讀取內容）：photo.jpg" in out


def test_pdf_download_failure_is_stated_not_silent():
    client = _pdf_client(_email_with([PDF_ATT]))
    with patch("gateway.slack_files._download", side_effect=RuntimeError("403")):
        out = attachment_context({"files": [EMAIL_FILE_IN_EVENT]}, client)
    assert "[信件附檔 PDF：S1EP03.pdf] 下載失敗，讀不到內容。" in out


def test_oversized_pdf_is_skipped_without_download():
    big = {**PDF_ATT, "size": 50 * 1024 * 1024}
    client = _pdf_client(_email_with([big]))
    with patch("gateway.slack_files._download") as dl:
        out = attachment_context({"files": [EMAIL_FILE_IN_EVENT]}, client)
    dl.assert_not_called()
    assert "檔案太大" in out


def test_long_pdf_text_truncated():
    from gateway.slack_files import MAX_PDF_CHARS

    client = _pdf_client(_email_with([PDF_ATT]))
    with (
        patch("gateway.slack_files._download", return_value=b"%PDF"),
        patch("gateway.slack_files.pdf_to_text", return_value="訪" * (MAX_PDF_CHARS + 100)),
    ):
        out = attachment_context({"files": [EMAIL_FILE_IN_EVENT]}, client)
    assert "訪" * MAX_PDF_CHARS in out
    assert "訪" * (MAX_PDF_CHARS + 1) not in out
    assert "PDF 過長" in out
