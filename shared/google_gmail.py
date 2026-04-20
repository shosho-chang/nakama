"""Google Gmail client — OAuth token 管理 + 信件讀取 / 草稿 / 發送。

使用 OAuth 2.0 Desktop app flow（user-consent，不用 service account）。
Token 持久化於 ``data/google_gmail_token.json``，refresh 時用 filelock 避免併發寫入。

注意：Gmail 帳號與 Google Calendar 帳號是**不同帳號**，token 檔分開。
共用同一個 ``data/google_oauth_credentials.json``（同 Cloud Project）。
"""

from __future__ import annotations

import base64
import email.mime.multipart
import email.mime.text
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from filelock import FileLock
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from shared.log import get_logger

logger = get_logger("nakama.google_gmail")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

_DATA_DIR = Path(os.environ.get("NAKAMA_DATA_DIR", "data"))
_TOKEN_PATH = _DATA_DIR / "google_gmail_token.json"
_CREDS_PATH = _DATA_DIR / "google_oauth_credentials.json"
_LOCK_PATH = _DATA_DIR / "google_gmail_token.lock"

_BODY_TRUNCATE = 3000  # 單封信件 body 最多送給 LLM 的字元數


class GoogleGmailAuthError(Exception):
    """授權失效，需使用者重跑 consent 流程。"""


# ── Credentials / Service ─────────────────────────────────────────


def _get_credentials() -> Credentials:
    """讀 token.json，expired 時自動 refresh + 寫回，回傳可用的 Credentials。"""
    if not _TOKEN_PATH.exists():
        raise GoogleGmailAuthError(
            f"Gmail Token 不存在於 {_TOKEN_PATH}。"
            " 請在本機執行 `python scripts/google_gmail_auth.py` 後把 token 搬到此位置。"
        )

    lock = FileLock(str(_LOCK_PATH), timeout=10)
    with lock:
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), SCOPES)
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                raise GoogleGmailAuthError(
                    f"Gmail Token refresh 失敗：{e}。"
                    " 請重跑 `python scripts/google_gmail_auth.py` 重新授權。"
                ) from e
            _TOKEN_PATH.write_text(creds.to_json())
            logger.info("Google Gmail token refreshed & persisted")
        elif not creds.valid:
            raise GoogleGmailAuthError("Gmail Token 無效且無 refresh_token。請重新跑 consent 流程。")
    return creds


def _get_service():
    """取得 Google Gmail API service client（每次呼叫新建，不 cache）。"""
    creds = _get_credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ── Public API ────────────────────────────────────────────────────


def list_messages(query: str = "is:unread", max_results: int = 10) -> list[dict]:
    """列出信件（支援 Gmail search syntax）。回傳精簡 metadata 列表。"""
    service = _get_service()
    result = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )

    msg_ids = [m["id"] for m in result.get("messages", [])]

    def _fetch_meta(msg_id: str) -> dict:
        # 每個 thread 建自己的 service，避免共用 SSL socket 導致 DECRYPTION_FAILED
        svc = _get_service()
        detail = (
            svc.users()
            .messages()
            .get(
                userId="me",
                id=msg_id,
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            )
            .execute()
        )
        headers = {
            h["name"]: h["value"]
            for h in detail.get("payload", {}).get("headers", [])
        }
        return {
            "id": detail["id"],
            "thread_id": detail.get("threadId", ""),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", "（無主旨）"),
            "date": headers.get("Date", ""),
            "snippet": detail.get("snippet", ""),
        }

    with ThreadPoolExecutor(max_workers=min(len(msg_ids), 10)) as pool:
        out = list(pool.map(_fetch_meta, msg_ids))
    return out


def get_message(message_id: str) -> dict:
    """取得單封信件完整內容（plain text body，超過 3000 字截斷）。"""
    service = _get_service()
    detail = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )

    headers = {
        h["name"]: h["value"]
        for h in detail.get("payload", {}).get("headers", [])
    }
    body = _extract_body(detail.get("payload", {}))
    if len(body) > _BODY_TRUNCATE:
        body = body[:_BODY_TRUNCATE] + "\n\n[…內容過長，已截斷]"

    return {
        "id": detail["id"],
        "thread_id": detail.get("threadId", ""),
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "cc": headers.get("Cc", ""),
        "subject": headers.get("Subject", "（無主旨）"),
        "date": headers.get("Date", ""),
        "snippet": detail.get("snippet", ""),
        "body": body,
    }


def create_draft(
    *,
    to: list[str],
    subject: str,
    body: str,
    thread_id: str | None = None,
    in_reply_to_message_id: str | None = None,
) -> dict:
    """建立 Gmail 草稿，回傳 draft_id + gmail_web_link + body（供 Slack 預覽）。"""
    service = _get_service()

    msg = email.mime.multipart.MIMEMultipart()
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    if in_reply_to_message_id:
        msg["In-Reply-To"] = in_reply_to_message_id
        msg["References"] = in_reply_to_message_id

    msg.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    draft_body: dict = {"message": {"raw": raw}}
    if thread_id:
        draft_body["message"]["threadId"] = thread_id

    draft = service.users().drafts().create(userId="me", body=draft_body).execute()
    draft_id = draft["id"]
    message_id = draft.get("message", {}).get("id", "")

    gmail_web_link = f"https://mail.google.com/mail/u/0/#drafts/{message_id}"

    return {
        "draft_id": draft_id,
        "message_id": message_id,
        "gmail_web_link": gmail_web_link,
        "to": to,
        "subject": subject,
        "body": body,
    }


def update_draft(
    draft_id: str,
    *,
    to: list[str] | None = None,
    subject: str | None = None,
    body: str | None = None,
) -> dict:
    """更新既有草稿（未提供的欄位保留原值）。回傳同 create_draft。"""
    service = _get_service()

    existing = service.users().drafts().get(userId="me", id=draft_id).execute()
    existing_msg = existing.get("message", {})
    thread_id = existing_msg.get("threadId")

    detail = (
        service.users()
        .messages()
        .get(userId="me", id=existing_msg["id"], format="full")
        .execute()
    )
    existing_headers = {
        h["name"]: h["value"]
        for h in detail.get("payload", {}).get("headers", [])
    }

    final_to_raw = existing_headers.get("To", "")
    final_to = to if to is not None else [x.strip() for x in final_to_raw.split(",") if x.strip()]
    final_subject = subject if subject is not None else existing_headers.get("Subject", "")
    final_body = body if body is not None else _extract_body(detail.get("payload", {}))
    final_in_reply_to = existing_headers.get("In-Reply-To")
    final_references = existing_headers.get("References")

    msg = email.mime.multipart.MIMEMultipart()
    msg["To"] = ", ".join(final_to)
    msg["Subject"] = final_subject
    if final_in_reply_to:
        msg["In-Reply-To"] = final_in_reply_to
    if final_references:
        msg["References"] = final_references

    msg.attach(email.mime.text.MIMEText(final_body, "plain", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    draft_body_payload: dict = {"message": {"raw": raw}}
    if thread_id:
        draft_body_payload["message"]["threadId"] = thread_id

    updated = (
        service.users()
        .drafts()
        .update(userId="me", id=draft_id, body=draft_body_payload)
        .execute()
    )
    message_id = updated.get("message", {}).get("id", "")

    gmail_web_link = f"https://mail.google.com/mail/u/0/#drafts/{message_id}"

    return {
        "draft_id": draft_id,
        "message_id": message_id,
        "gmail_web_link": gmail_web_link,
        "to": final_to,
        "subject": final_subject,
        "body": final_body,
    }


def send_draft(draft_id: str) -> dict:
    """發送草稿，回傳 message_id + thread_id。"""
    service = _get_service()
    result = (
        service.users()
        .drafts()
        .send(userId="me", body={"id": draft_id})
        .execute()
    )
    return {
        "message_id": result.get("id", ""),
        "thread_id": result.get("threadId", ""),
        "label_ids": result.get("labelIds", []),
    }


# ── Helpers ───────────────────────────────────────────────────────


def _extract_body(payload: dict) -> str:
    """遞迴從 MIME payload 抽出 plain text body。"""
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text

    return ""
