"""從對話抽取記憶（Phase 2 of agent memory system）。

設計：
- 對話 end_turn 後背景呼叫 Haiku 4.5（便宜、快）
- 要 LLM 從訊息中找出值得長期記住的「關於使用者」的事實/偏好/決策
- 結果 upsert 到 ``user_memories`` table

為什麼背景執行：抽取 ~1-2 秒，不該 block 使用者的下一輪互動。
為什麼用 Haiku：Sonnet 過於昂貴，這種結構化抽取任務 Haiku 足夠。
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

from shared import agent_memory
from shared.agent_memory import VALID_TYPES
from shared.llm import ask
from shared.log import get_logger

logger = get_logger("nakama.memory_extractor")

_EXTRACTOR_MODEL = "claude-haiku-4-5"
_MAX_MESSAGES = 30  # 對話超長時，只看最近 N 則

__all__ = ["VALID_TYPES", "extract_from_messages", "extract_in_background"]

_EXTRACTOR_SYSTEM_PROMPT = """你是對話記憶抽取器。
你的工作是從對話中找出值得**長期記住**關於「使用者」的資訊。

## 抽取類型

- `preference`：使用者的偏好或習慣（例：喜歡研究型內容、不喜歡短影片、習慣早上做深度工作）
- `fact`：關於使用者的客觀事實（例：身分、家庭、工作、正在進行的 project）
- `decision`：使用者做過的決定（例：決定禮拜一早上 10 點錄製影片、放棄某個 project）
- `context`：對未來任務有用的背景（例：某個 project 的動機、某個 task 的原因）

## 忽略不抽取

- 閒聊、問候、感謝
- 一次性任務指令本身（例：「幫我建立 task」只是觸發動作，不是記憶）
- 系統層資訊（例：日期表、debug 訊息）
- 已經被撤回或改變的決定（只記當下仍有效的）

## subject 欄位規則

- **短**（2-6 個字的名詞片語），用來**去重**
- **優先重用** prompt 結尾列出的「已有記憶」——
  如果新資訊屬於某個既有主題，直接用那個 subject（即使字面不完全相同）
  - 例：已有「工作時段」，新對話提到「下午兩點前深度工作」→ 仍用 `工作時段`
  - 例：已有「專業領域」，對話提到「研究健康長壽」→ 仍用 `專業領域`
- 只有在新主題跟所有既有 subject 都不相關時，才發明新的
- 繁體中文

## content 欄位規則（重要！）

- **直接寫事實**，不要加主詞（使用者身分已知）
  - 錯：「修修船長早上頭腦最清楚」
  - 對：「早上頭腦最清楚，深度工作排在下午兩點前」
- 不要重複 subject 內容，補充細節即可

## content 合併規則（重複用既有 subject 時必讀）

當你重用一個既有 subject，**content 必須是 merged 版本**，包含舊資訊 + 新資訊：

- 既有 `專業領域 = 主攻健康長壽，重點是睡眠/飲食/運動/情緒`
- 對話新增：「最近在研究蛋白質攝取量」
- 正確 content：`主攻健康長壽（睡眠/飲食/運動/情緒），目前深入研究蛋白質攝取量`
- **錯誤** content：`正在研究蛋白質攝取量`（舊資訊丟失）

例外：使用者**明確取代**舊事實（例：「我改去讀哈佛了」取代原本的 NTNU）→ 才覆蓋。
無明確取代 → 一律 merge 不遺漏。

## 輸出格式

純 JSON 陣列，不要有 markdown code fence，不要任何解釋文字：

```
[
  {"type": "preference", "subject": "工作時段",
   "content": "修修習慣早上做深度工作", "confidence": 0.9}
]
```

沒有值得記的就回 `[]`。"""


def _format_messages_for_extraction(messages: list[dict]) -> str:
    """把 Claude 格式的 messages 轉成純文字對話，供抽取器 LLM 讀。"""
    lines: list[str] = []
    for msg in messages[-_MAX_MESSAGES:]:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    parts.append(f"[呼叫 tool: {block.get('name')} {block.get('input')}]")
                elif btype == "tool_result":
                    parts.append(f"[tool 結果: {block.get('content')}]")
            text = "\n".join(p for p in parts if p)
        else:
            text = str(content)
        lines.append(f"[{role}] {text}")
    return "\n\n".join(lines)


def _parse_extraction_response(raw: str) -> list[dict]:
    """容錯地解析 LLM 回的 JSON 陣列。LLM 有時會加 markdown code fence。"""
    raw = raw.strip()
    # 去掉可能的 ```json ... ``` 包裝
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Extractor returned invalid JSON: {raw[:200]}")
        return []
    if not isinstance(data, list):
        logger.warning(f"Extractor returned non-list: {type(data).__name__}")
        return []
    return data


def _validate_and_normalize(item: Any) -> dict | None:
    """檢查單個抽取結果的欄位，不合規就丟掉。"""
    if not isinstance(item, dict):
        return None
    type_ = item.get("type")
    subject = item.get("subject")
    content = item.get("content")
    if type_ not in VALID_TYPES:
        return None
    if not isinstance(subject, str) or not subject.strip():
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    confidence = item.get("confidence", 0.8)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.8
    confidence = max(0.0, min(1.0, confidence))
    return {
        "type": type_,
        "subject": subject.strip(),
        "content": content.strip(),
        "confidence": confidence,
    }


def extract_from_messages(
    agent: str,
    user_id: str,
    messages: list[dict],
    *,
    source_thread: str | None = None,
) -> list[int]:
    """同步抽取並存入 user_memories。回傳新增/更新的 memory ids。

    抽取失敗時回傳空 list 並記 warning，不拋例外（避免影響主對話流程）。
    """
    if not messages:
        return []

    conversation = _format_messages_for_extraction(messages)

    existing = agent_memory.list_subjects_with_content(agent, user_id)
    if existing:
        subjects_block = (
            "\n\n## 已有記憶（重用 subject 時 content 必須是 merged 版本，包含舊資訊）\n"
            + "\n".join(f"- `{s}` = {c}" for s, c in existing)
        )
    else:
        subjects_block = ""

    prompt = f"對話紀錄：\n\n{conversation}{subjects_block}\n\n請抽取記憶（JSON 陣列）。"

    try:
        raw = ask(
            prompt=prompt,
            system=_EXTRACTOR_SYSTEM_PROMPT,
            model=_EXTRACTOR_MODEL,
            max_tokens=1024,
        )
    except Exception as e:
        logger.warning(f"Memory extraction LLM call failed: {e}")
        return []

    items = _parse_extraction_response(raw)
    if not items:
        return []

    saved_ids: list[int] = []
    for item in items:
        normalized = _validate_and_normalize(item)
        if normalized is None:
            continue
        try:
            mid = agent_memory.add(
                agent=agent,
                user_id=user_id,
                type=normalized["type"],
                subject=normalized["subject"],
                content=normalized["content"],
                confidence=normalized["confidence"],
                source_thread=source_thread,
            )
            saved_ids.append(mid)
        except Exception as e:
            logger.warning(f"Memory add failed for subject={normalized['subject']}: {e}")

    logger.info(
        f"Extracted {len(saved_ids)} memories for {agent}/{user_id} (from {len(messages)} messages)"
    )
    return saved_ids


def extract_in_background(
    agent: str,
    user_id: str,
    messages: list[dict],
    *,
    source_thread: str | None = None,
) -> threading.Thread:
    """在 daemon thread 中執行抽取。回傳 thread 物件（測試可 join）。"""
    t = threading.Thread(
        target=extract_from_messages,
        args=(agent, user_id, list(messages)),
        kwargs={"source_thread": source_thread},
        daemon=True,
        name=f"memory-extractor-{agent}-{user_id}",
    )
    t.start()
    return t
