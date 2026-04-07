"""Email 通知。"""

import os
import smtplib
from email.mime.text import MIMEText

from shared.log import get_logger

logger = get_logger()


def send_email(subject: str, body: str) -> None:
    """發送 Email 通知給 Owner。"""
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    notify_to = os.environ.get("NOTIFY_TO", "")

    if not all([smtp_host, smtp_user, smtp_pass, notify_to]):
        logger.warning("Email 設定不完整，跳過通知")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[Nakama] {subject}"
    msg["From"] = smtp_user
    msg["To"] = notify_to

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        logger.info(f"已發送通知信：{subject}")
    except Exception as e:
        logger.error(f"發送通知信失敗：{e}")
