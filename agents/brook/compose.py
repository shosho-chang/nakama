"""Brook Compose — 多回合對話式文章撰寫助手 + 一次性 production 流水線。

兩條 entry point：
- 對話式（`start_conversation` / `send_message` / `export_draft`）：修修 Web UI 用
- Production（`compose_and_enqueue`）：topic → DraftV1 → approval_queue，Usopp claim 後發文
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import AwareDatetime, ValidationError

from agents.brook.compliance_scan import scan_draft_compliance, scan_publish_gate
from agents.brook.style_profile_loader import (
    StyleProfile,
    detect_category,
    load_style_profile,
)
from shared import approval_queue, gutenberg_builder
from shared.anthropic_client import ask_claude_multi, set_current_agent
from shared.log import get_logger
from shared.prompt_loader import load_prompt
from shared.schemas.approval import PublishWpPostV1
from shared.schemas.publishing import (
    BlockNodeV1,
    DraftV1,
    GutenbergHTMLV1,
    PublishComplianceGateV1,
    SEOContextV1,
    TargetSite,
)
from shared.state import _get_conn
from shared.tag_filter import filter_tags

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


# ---------------------------------------------------------------------------
# Production pipeline: topic → DraftV1 → approval_queue
# ---------------------------------------------------------------------------
#
# 和對話式流水線正交：這條路徑給定 topic 與可選素材（kb_context / source_content），
# 透過 style profile + Claude structured output 產出 DraftV1，落 approval_queue，等
# Usopp claim 後發 WordPress。Phase 1 單回合 compose；對話式產稿走 start_conversation。


class ComposeOutputParseError(ValueError):
    """LLM 的結構化輸出 JSON parse 失敗或缺必要欄位。"""


Category = Literal["book-review", "people", "science"]


_COMPOSE_OUTPUT_SCHEMA = """{
  "title": "<5-120 字標題>",
  "slug_candidates": ["<3-80 字 a-z 0-9 連字號的 url slug>", ...],
  "excerpt": "<20-300 字摘要>",
  "focus_keyword": "<2-60 字主關鍵字>",
  "meta_description": "<50-155 字 SEO meta>",
  "secondary_categories": ["<slug, 最多 2 個>"],
  "tags": ["<slug, 最多 10 個>"],
  "blocks": [
    {"block_type": "heading", "attrs": {"level": 2}, "content": "段落標題", "children": []},
    {"block_type": "paragraph", "attrs": {}, "content": "段落內文", "children": []}
  ]
}"""


def _build_compose_system_prompt(
    profile: StyleProfile,
    seo_context: SEOContextV1 | None = None,
) -> str:
    """把風格側寫整份塞進 system，緊接著結構化輸出規範。

    Phase 1 單類別單檔；依 _extraction-notes.md §3.2「不要三類都塞進 context」。

    `seo_context=None`（預設）路徑 byte-identical 於 SEO 整合前的輸出（regression 保護）。
    給定 `SEOContextV1` 時在 prompt 尾端 append 一段繁中 SEO 數據區塊（ADR-009 §D5）。
    """
    emoji_rule = (
        "本類別嚴禁 emoji（書評硬規則）。" if profile.forbid_emoji else "emoji 依風格側寫內指引。"
    )

    header = (
        f"你是 Brook，為修修代筆的文章撰寫 agent。"
        f"以下為本類別（{profile.category}）的完整風格側寫，"
        "請嚴格遵守聲音指紋與禁止事項。"
    )
    block_types = "paragraph、heading、list、list_item、quote、image、code、separator"
    word_range = f"{profile.word_count_min} – {profile.word_count_max}"
    base = f"""{header}

{profile.body}

---

# 輸出規範（覆寫以上 markdown 範本的輸出格式）

1. 你的輸出 **必須是單一 JSON 物件**，無 markdown code fence、無前後說明文字。
2. {emoji_rule}
3. slug 僅用小寫 a-z / 0-9 / 連字號，3-80 字。
4. blocks 是 Gutenberg AST 陣列；block_type 僅可為：{block_types}。
5. heading 的 attrs.level 僅允許 2-4。
6. list 的 children 全為 list_item。
7. content 為該 block 的純文字，不要帶 HTML 標籤。
8. 全文字數控制在 {word_range} 字之間。

JSON schema 範例：
{_COMPOSE_OUTPUT_SCHEMA}
"""
    if seo_context is None:
        return base

    from agents.brook.seo_block import build_seo_block

    return base + "\n---\n\n" + build_seo_block(seo_context)


def _build_user_request(topic: str, kb_context: str, source_content: str) -> str:
    parts = [f"# 主題\n{topic}"]
    if kb_context.strip():
        parts.append(f"# 知識庫參考（Robin 查詢結果）\n{kb_context}")
    if source_content.strip():
        parts.append(f"# 素材（書摘 / 逐字稿 / 研究筆記）\n{source_content}")
    parts.append("請依系統 prompt 的結構化輸出規範，回傳單一 JSON 物件。")
    return "\n\n".join(parts)


def _extract_json_object(text: str) -> dict[str, Any]:
    """抽 LLM 回應的 JSON：先試整段，失敗則抓第一個 {…} 區塊。"""
    stripped = text.strip()
    # 去 markdown fence
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    # 抓第一個平衡的 {…}
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ComposeOutputParseError("LLM 回應不含 JSON object") from None
    try:
        return json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as e:
        raise ComposeOutputParseError(f"JSON parse 失敗：{e}") from e


def _ast_to_plaintext(ast: list[BlockNodeV1]) -> str:
    """把 AST 攤平成純文字給 compliance scan 用。"""
    chunks: list[str] = []
    for node in ast:
        if node.content:
            chunks.append(node.content)
        if node.children:
            chunks.append(_ast_to_plaintext(node.children))
    return "\n".join(chunks)


def _new_draft_id(now: datetime) -> str:
    """draft_YYYYMMDDTHHMMSS_xxxxxx（DraftV1.draft_id pattern）。"""
    return f"draft_{now.strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(3)}"


def _new_operation_id() -> str:
    return f"op_{uuid.uuid4().hex[:8]}"


def _parse_llm_output(
    raw: dict[str, Any],
) -> tuple[dict[str, Any], GutenbergHTMLV1]:
    """把 LLM JSON 分離成 metadata 區塊 + 已 build 的 Gutenberg AST。"""
    blocks_raw = raw.get("blocks")
    if not isinstance(blocks_raw, list) or not blocks_raw:
        raise ComposeOutputParseError("LLM 輸出缺少 blocks 陣列或為空")
    try:
        ast = [BlockNodeV1.model_validate(b) for b in blocks_raw]
    except Exception as e:
        raise ComposeOutputParseError(f"blocks 無法轉 BlockNodeV1：{e}") from e

    try:
        content = gutenberg_builder.build(ast)
    except Exception as e:
        raise ComposeOutputParseError(f"AST → HTML build 失敗：{e}") from e
    return raw, content


def compose_and_enqueue(
    *,
    topic: str,
    category: Category | None = None,
    kb_context: str = "",
    source_content: str = "",
    target_site: TargetSite = "wp_shosho",
    scheduled_at: AwareDatetime | None = None,
    primary_category_override: str | None = None,
    model: str | None = None,
    max_tokens: int = 8192,
    seo_context: SEOContextV1 | None = None,
    core_keywords: list[str] | None = None,
) -> dict[str, Any]:
    """主題 → Claude 產 AST → DraftV1 → approval_queue row。

    Args:
        topic: 文章主題
        category: 強制使用的風格類別；None 時由 detect_category 判斷，再 None 則 raise
        kb_context: Robin 查詢的背景資訊（科普類常用）
        source_content: 原始素材（書摘 / podcast 逐字稿 / 研究筆記）
        target_site: 目標 WordPress 站台（wp_shosho / wp_fleet）
        scheduled_at: 排程發佈時間；None 表立即發（Usopp claim 後送）
        primary_category_override: 覆寫 style profile 的預設 primary_category
        model: Claude 模型；None 走 llm_router
        max_tokens: LLM 最大輸出 token
        seo_context: 可選的 SEOContextV1（site-wide，由 seo-keyword-enrich skill 產出）。
            非 None 時會先跑 `narrow_to_topic` 過濾成 topic 相關子集再 append 到 system prompt。
            `None` 時整段邏輯路徑 byte-identical 於 SEO 整合前。
        core_keywords: 給 narrow LLM 的額外 anchor（通常從 keyword-research markdown 讀進來）；
            僅在 `seo_context` 非 None 時生效。

    Returns:
        {
            "queue_row_id": int,
            "draft_id": str,
            "operation_id": str,
            "category": str,
            "title": str,
            "compliance_flags": PublishComplianceGateV1,
            "tag_filter_rejected": list[tuple[str, str]],
        }
    """
    set_current_agent("brook")

    resolved_category: str | None = category or detect_category(topic, source_content)
    if resolved_category is None:
        raise ValueError(
            '無法自動判斷文章類別，請顯式指定 category="book-review" | "people" | "science"'
        )

    profile = load_style_profile(resolved_category)

    narrowed_seo: SEOContextV1 | None = None
    if seo_context is not None:
        from agents.brook.seo_narrow import narrow_to_topic

        narrowed_seo = narrow_to_topic(seo_context, topic, core_keywords)

    system_prompt = _build_compose_system_prompt(profile, narrowed_seo)
    user_msg = _build_user_request(topic, kb_context, source_content)

    raw_text = ask_claude_multi(
        [{"role": "user", "content": user_msg}],
        system=system_prompt,
        model=model,
        max_tokens=max_tokens,
        temperature=0.4,
    )
    payload = _extract_json_object(raw_text)
    metadata, content = _parse_llm_output(payload)

    plaintext = _ast_to_plaintext(content.ast)
    gate_flags: PublishComplianceGateV1 = scan_publish_gate(plaintext)
    draft_compliance = scan_draft_compliance(plaintext)

    tag_candidates = [str(t) for t in metadata.get("tags") or []]
    if not tag_candidates:
        tag_candidates = list(profile.default_tag_hints)
    tag_result = filter_tags(tag_candidates)

    now = datetime.now(timezone.utc)
    operation_id = _new_operation_id()
    draft_id = _new_draft_id(now)

    # LLM 可能回傳語法合法 JSON 但違反 DraftV1 長度 / pattern 限制，或漏掉必要欄位。
    # 全部收斂成 ComposeOutputParseError，維持模組對外的單一 parse-class 例外契約。
    try:
        draft = DraftV1(
            draft_id=draft_id,
            created_at=now,
            agent="brook",
            operation_id=operation_id,
            title=metadata["title"],
            slug_candidates=list(metadata["slug_candidates"]),
            content=content,
            excerpt=metadata["excerpt"],
            primary_category=primary_category_override or profile.primary_category,
            secondary_categories=list(metadata.get("secondary_categories") or []),
            tags=tag_result.accepted,
            focus_keyword=metadata["focus_keyword"],
            meta_description=metadata["meta_description"],
            featured_image_brief=None,
            compliance=draft_compliance,
            style_profile_id=profile.profile_id,
        )
        approval_payload = PublishWpPostV1(
            action_type="publish_post",
            target_site=target_site,
            draft=draft,
            compliance_flags=gate_flags,
            reviewer_compliance_ack=False,
            scheduled_at=scheduled_at,
        )
    except (ValidationError, KeyError, TypeError) as e:
        raise ComposeOutputParseError(f"LLM 輸出違反 DraftV1 / PublishWpPostV1 契約：{e}") from e

    queue_row_id = approval_queue.enqueue(
        source_agent="brook",
        payload_model=approval_payload,
        operation_id=operation_id,
    )
    logger.info(
        "Brook compose_and_enqueue %s topic=%r category=%s queue_row=%d",
        draft_id,
        topic,
        resolved_category,
        queue_row_id,
    )

    if tag_result.rejected:
        logger.info("tag filter rejected: %s", tag_result.rejected)

    return {
        "queue_row_id": queue_row_id,
        "draft_id": draft_id,
        "operation_id": operation_id,
        "category": resolved_category,
        "title": draft.title,
        "compliance_flags": gate_flags,
        "tag_filter_rejected": tag_result.rejected,
    }
