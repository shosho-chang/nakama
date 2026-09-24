"""Slack 訊息附件 → agent 看得到的文字。

Gmail 的「傳到 Slack」（Slack for Gmail）不會把信放進訊息 text，而是附成一個
`filetype == "email"` 的檔案。event 裡的 file 物件常常只是殘缺版（沒有內文），
所以一律用 `files.info` 取完整內容：Slack 已經幫忙轉好 `plain_text`，加上
subject / from / date，不必自己 parse HTML。

信件的 PDF 附檔（例如訪綱）用 bot token 從 `attachments[].url` 下載後抽成文字；
其他類型的附檔只列檔名。

需要 bot token 有 `files:read` scope（見 docs/runbooks/add-agent-slack-bot.md）。

注意：信件內容只能接在 **routing 之後**。router 會在整段 text 裡找 agent 名稱與
intent 關鍵字、還會把 agent 名稱刪掉 — 信件內文不能參與 routing。
"""

from __future__ import annotations

from typing import Any

import httpx

from shared.log import get_logger
from shared.pdf_text import PdfTextError, pdf_to_text

logger = get_logger("nakama.gateway.slack_files")

# 一封信的內文上限。Nami 對話是整段塞進 LLM context 的，超長的轉寄串不能無上限。
MAX_EMAIL_BODY_CHARS = 8000
# 單一 PDF 附檔抽出文字的上限；整封信所有 PDF 合計的上限
MAX_PDF_CHARS = 8000
MAX_TOTAL_PDF_CHARS = 20000
# 超過就不下載（Slack 的 size 欄位是 bytes）
MAX_PDF_BYTES = 10 * 1024 * 1024
_DOWNLOAD_TIMEOUT_S = 30


def _format_people(people: list[dict[str, Any]] | None) -> str:
    return ", ".join(p.get("original") or p.get("address", "") for p in people or [])


def _is_pdf(attachment: dict[str, Any]) -> bool:
    name = str(attachment.get("filename", "")).lower()
    return attachment.get("mimetype") == "application/pdf" or name.endswith(".pdf")


def _download(url: str, token: str) -> bytes:
    resp = httpx.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=True,
        timeout=_DOWNLOAD_TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.content


def _read_pdf_attachment(attachment: dict[str, Any], token: str, budget: int) -> str:
    name = attachment.get("filename") or "(未命名 PDF)"
    size = attachment.get("size") or 0
    if size > MAX_PDF_BYTES:
        return f"[信件附檔 PDF：{name}] 檔案太大（{size // 1024} KB），沒有讀取。"
    url = attachment.get("url")
    if not url:
        return f"[信件附檔 PDF：{name}] Slack 沒提供下載連結，讀不到內容。"
    try:
        text = pdf_to_text(_download(url, token))
    except PdfTextError as e:
        return f"[信件附檔 PDF：{name}] 讀不到內容：{e}"
    except Exception as e:  # httpx 網路 / HTTP 錯誤
        logger.warning(f"下載信件 PDF 附檔失敗 {name}: {e}")
        return f"[信件附檔 PDF：{name}] 下載失敗，讀不到內容。"
    limit = min(MAX_PDF_CHARS, budget)
    if len(text) > limit:
        text = text[:limit] + "\n…（PDF 過長，後面已截斷）"
    return f"[信件附檔 PDF：{name}]\n{text}"


def _read_attachments(file: dict[str, Any], client: Any) -> tuple[list[str], list[str]]:
    """回 (PDF 抽出的文字區塊, 沒讀的其他附檔名)。"""
    blocks: list[str] = []
    unread: list[str] = []
    budget = MAX_TOTAL_PDF_CHARS
    token = getattr(client, "token", None)
    for att in file.get("attachments") or []:
        name = att.get("filename", "")
        if not _is_pdf(att):
            unread.append(name)
        elif not token:
            blocks.append(f"[信件附檔 PDF：{name}] 沒有 bot token，讀不到內容。")
        elif budget <= 0:
            blocks.append(f"[信件附檔 PDF：{name}] PDF 合計長度已達上限，沒有讀取。")
        else:
            block = _read_pdf_attachment(att, token, budget)
            budget -= len(block)
            blocks.append(block)
    return blocks, unread


def _format_email(
    file: dict[str, Any],
    pdf_blocks: list[str] | None = None,
    unread_attachments: list[str] | None = None,
) -> str:
    subject = file.get("subject") or file.get("title") or file.get("name") or "(無主旨)"
    lines = [f"[附件信件] 主旨：{subject}"]
    sender = _format_people(file.get("from"))
    if sender:
        lines.append(f"寄件人：{sender}")
    recipients = _format_people(file.get("to"))
    if recipients:
        lines.append(f"收件人：{recipients}")
    cc = _format_people(file.get("cc"))
    if cc:
        lines.append(f"副本：{cc}")
    date = (file.get("headers") or {}).get("date")
    if date:
        lines.append(f"日期：{date}")
    if unread_attachments is None:
        unread_attachments = [a.get("filename", "") for a in file.get("attachments") or []]
    if unread_attachments:
        lines.append(f"信件附檔（未讀取內容）：{', '.join(unread_attachments)}")

    body = (file.get("plain_text") or file.get("preview_plain_text") or "").strip()
    if len(body) > MAX_EMAIL_BODY_CHARS:
        body = body[:MAX_EMAIL_BODY_CHARS] + "\n…（信件過長，後面已截斷）"
    lines.append("內文：")
    lines.append(body or "（信件沒有文字內文）")
    for block in pdf_blocks or []:
        lines.append("")
        lines.append(block)
    return "\n".join(lines)


def _describe_email_file(file: dict[str, Any], client: Any) -> str:
    title = file.get("title") or file.get("name") or file.get("id", "")
    file_id = file.get("id")
    try:
        resp = client.files_info(file=file_id)
        full = resp["file"]
    except Exception as e:  # SlackApiError（missing_scope 等）/ 網路錯誤
        logger.warning(f"files.info 讀取信件失敗 file={file_id}: {e}")
        return (
            f"[附件信件：{title}] 使用者有附上這封信，但系統讀取內容失敗"
            "（可能是 Slack app 缺 files:read 權限）。請直接告訴使用者附件讀不到，"
            "不要說他沒附。"
        )
    pdf_blocks, unread = _read_attachments(full, client)
    return _format_email(full, pdf_blocks, unread)


def attachment_context(event: dict[str, Any], client: Any) -> str:
    """把 event 附的 email 檔轉成文字區塊；沒有 email 附件回空字串。

    其他類型的檔案（圖片、PDF 等）目前不處理。
    """
    files = event.get("files") or []
    blocks = [
        _describe_email_file(f, client)
        for f in files
        if f.get("filetype") == "email" or f.get("mode") == "email"
    ]
    return "\n\n".join(blocks)


def with_attachments(text: str, event: dict[str, Any], client: Any) -> str:
    """在使用者打的字後面接上附件信件內容。"""
    extra = attachment_context(event, client)
    if not extra:
        return text
    return f"{text}\n\n{extra}" if text else extra
