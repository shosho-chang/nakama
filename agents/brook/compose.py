"""Brook Compose — 多回合對話式文章撰寫助手。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from shared.anthropic_client import ask_claude_multi, set_current_agent
from shared.log import get_logger
from shared.prompt_loader import load_prompt
from shared.state import _get_conn

logger = get_logger("nakama.brook.compose")

# Sliding window: 保留前 N 則 + 最近 M 則
_ANCHOR_MESSAGES = 2  # 主題 + 大綱
_RECENT_MESSAGES = 40  # 最近 20 回合


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------


def _init_brook_tables() -> None:
    """建立 Brook 專用的 SQLite 表（如果不存在）。"""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS brook_conversations (
            id          TEXT PRIMARY KEY,
            topic       TEXT NOT NULL,
            phase       TEXT NOT NULL DEFAULT 'outline',
            kb_context  TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS brook_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES brook_conversations(id)
        );

        CREATE INDEX IF NOT EXISTS idx_brook_messages_conv
            ON brook_messages(conversation_id, id);
    """)
    conn.commit()


_tables_ready = False


def _ensure_tables() -> None:
    global _tables_ready
    if not _tables_ready:
        _init_brook_tables()
        _tables_ready = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_message(conversation_id: str, role: str, content: str) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT INTO brook_messages (conversation_id, role, content, created_at) "
        "VALUES (?, ?, ?, ?)",
        (conversation_id, role, content, _now()),
    )
    conn.execute(
        "UPDATE brook_conversations SET updated_at = ? WHERE id = ?",
        (_now(), conversation_id),
    )
    conn.commit()


def _load_messages(conversation_id: str) -> list[dict]:
    """載入對話的所有訊息，按 id 排序。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT role, content FROM brook_messages WHERE conversation_id = ? ORDER BY id",
        (conversation_id,),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def _build_messages_window(all_messages: list[dict]) -> list[dict]:
    """套用 sliding window：保留前 N 則 + 最近 M 則，中間截斷。"""
    total = len(all_messages)
    if total <= _ANCHOR_MESSAGES + _RECENT_MESSAGES:
        return all_messages

    anchor = all_messages[:_ANCHOR_MESSAGES]
    recent = all_messages[-_RECENT_MESSAGES:]
    skipped = total - _ANCHOR_MESSAGES - _RECENT_MESSAGES

    bridge = {
        "role": "assistant",
        "content": f"[... 中間省略 {skipped} 則訊息 ...]",
    }
    return anchor + [bridge] + recent


def _build_system_prompt(kb_context: str = "") -> str:
    """建構 Brook 的 system prompt。"""
    return load_prompt("brook", "compose", kb_context=kb_context or "（無）")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def start_conversation(
    topic: str,
    kb_context: str = "",
) -> dict:
    """開始新對話，產出文章大綱。

    Args:
        topic: 文章主題
        kb_context: Robin KB 搜尋結果（可選）

    Returns:
        {"conversation_id": str, "message": str, "phase": "outline"}
    """
    _ensure_tables()
    set_current_agent("brook")

    conv_id = str(uuid.uuid4())
    conn = _get_conn()
    now = _now()
    conn.execute(
        "INSERT INTO brook_conversations (id, topic, phase, kb_context, created_at, updated_at) "
        "VALUES (?, ?, 'outline', ?, ?, ?)",
        (conv_id, topic, kb_context, now, now),
    )
    conn.commit()

    # 初始 user message
    user_msg = f"我想寫一篇關於「{topic}」的文章。請先幫我產出一份大綱。"
    _save_message(conv_id, "user", user_msg)

    system = _build_system_prompt(kb_context)
    messages = [{"role": "user", "content": user_msg}]

    response = ask_claude_multi(messages, system=system, temperature=0.5)
    _save_message(conv_id, "assistant", response)

    logger.info(f"Brook 新對話：{conv_id} — {topic}")
    return {"conversation_id": conv_id, "message": response, "phase": "outline"}


def send_message(conversation_id: str, user_message: str) -> dict:
    """在既有對話中傳送訊息。

    Returns:
        {"message": str, "phase": str, "turn_count": int}
    """
    _ensure_tables()
    set_current_agent("brook")

    conn = _get_conn()
    conv = conn.execute(
        "SELECT topic, phase, kb_context FROM brook_conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if not conv:
        raise ValueError(f"Conversation not found: {conversation_id}")

    _save_message(conversation_id, "user", user_message)

    all_messages = _load_messages(conversation_id)
    messages = _build_messages_window(all_messages)

    system = _build_system_prompt(conv["kb_context"])
    response = ask_claude_multi(messages, system=system, temperature=0.5)
    _save_message(conversation_id, "assistant", response)

    turn_count = len(all_messages) // 2 + 1
    return {
        "message": response,
        "phase": conv["phase"],
        "turn_count": turn_count,
    }


def get_conversations(limit: int = 20) -> list[dict]:
    """列出最近的對話。"""
    _ensure_tables()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, topic, phase, created_at, updated_at "
        "FROM brook_conversations ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(conversation_id: str) -> dict | None:
    """載入完整對話（含所有訊息）。"""
    _ensure_tables()
    conn = _get_conn()
    conv = conn.execute(
        "SELECT id, topic, phase, kb_context, created_at, updated_at "
        "FROM brook_conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if not conv:
        return None

    messages = conn.execute(
        "SELECT role, content, created_at FROM brook_messages "
        "WHERE conversation_id = ? ORDER BY id",
        (conversation_id,),
    ).fetchall()

    return {
        **dict(conv),
        "messages": [dict(m) for m in messages],
    }


def export_draft(conversation_id: str) -> str:
    """整合對話為完整文章初稿。"""
    _ensure_tables()
    set_current_agent("brook")

    conv = get_conversation(conversation_id)
    if not conv:
        raise ValueError(f"Conversation not found: {conversation_id}")

    # 格式化對話內容
    conversation_text = ""
    for msg in conv["messages"]:
        role_label = "使用者" if msg["role"] == "user" else "Brook"
        conversation_text += f"### {role_label}\n{msg['content']}\n\n"

    prompt = load_prompt("brook", "export", conversation=conversation_text)
    messages = [{"role": "user", "content": prompt}]
    result = ask_claude_multi(messages, max_tokens=8192, temperature=0.3)

    # 更新 phase
    conn = _get_conn()
    conn.execute(
        "UPDATE brook_conversations SET phase = 'done', updated_at = ? WHERE id = ?",
        (_now(), conversation_id),
    )
    conn.commit()

    logger.info(f"Brook 匯出文章：{conversation_id}")
    return result
