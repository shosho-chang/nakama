"""Claude Agent SDK 共用件 — 訂閱認證覆寫（ADR-026 tool-use 限制的 SDK 出口）。

ADR-026 的 CLI 訂閱路徑（``shared/claude_cli_client.py``）載不動 tool-use；
Agent SDK 路徑可以（in-process MCP tools），且實測可走訂閱額度 —
見 ``memory/claude/reference_agent_sdk_supports_oauth.md``（2026-08-18）。

⚠️ 為什麼這個 helper 是承重牆不是語法糖
（``docs/research/2026-08-18-merger-sdk-spike-findings.md`` §操作性發現）：

SDK 子進程繼承整份 process env，而 CLI 的認證優先序是
**``ANTHROPIC_API_KEY`` 壓過 ``CLAUDE_CODE_OAUTH_TOKEN``**（2026-08-18 實測）。
call site 忘傳 ``env=subscription_env()`` 不是「退回 API 計費」，而是在
API 額度空時**秒死**於難以自我解釋的 ``error result: success``
（與 2026-08-17 Nami 額度事故同簽名）。所以：

- 每個 SDK call site 都必須把本函式的回傳傳給 ``ClaudeAgentOptions(env=...)``
- 測試必須鎖住「有傳 env」這件事（比照
  ``tests/gateway/test_nami_sdk_loop.py`` 的行為鎖定測試）

Nami 的 ``gateway/handlers/nami.py::_sdk_auth_env``（讀 ``NAMI_SDK_OAUTH_TOKEN``）
是本函式的前身；S4 收斂時它會 delegate 到這裡（migration 計畫
``docs/plans/2026-08-18-annotation-merger-agent-sdk-plan.md`` S4）。

S0 取證（ADR-070 D2 第 7 項、U1）：``log_sdk_message`` / ``log_sdk_exception``
把 SDK stream 裡的額度訊號、錯誤與成功摘要原樣寫進 logger ``nakama.llm_lane``。
只記 log，不改任何呼叫點的控制流程。
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

# SDK 型別只用來做 isinstance 判斷。import 失敗（本機沒裝 SDK、或 VPS 的舊版
# 0.2.128 沒有某個類別）時退回用類別名稱判斷 —— 絕不能因此讓本模組 import 失敗。
try:
    from claude_agent_sdk import AssistantMessage as _AssistantMessage
    from claude_agent_sdk import ResultMessage as _ResultMessage
except Exception:  # noqa: BLE001
    _AssistantMessage = None  # type: ignore[assignment,misc]
    _ResultMessage = None  # type: ignore[assignment,misc]
try:
    from claude_agent_sdk import RateLimitEvent as _RateLimitEvent
except Exception:  # noqa: BLE001
    _RateLimitEvent = None  # type: ignore[assignment,misc]

__all__ = [
    "LANE_LOGGER_NAME",
    "describe_sdk_message",
    "log_sdk_exception",
    "log_sdk_message",
    "sdk_message_kind",
    "subscription_env",
]

LANE_LOGGER_NAME = "nakama.llm_lane"
_TEXT_LIMIT = 2000
_MAX_ERRORS = 20

_KIND_RATE_LIMIT = "RateLimitEvent"
_KIND_ASSISTANT = "AssistantMessage"
_KIND_RESULT = "ResultMessage"

# RateLimitInfo 的欄位（SDK 0.2.134 types.py:1281-1303）。0.2.134 把它們放在
# ``RateLimitEvent.rate_limit_info`` 底下；舊版若是平鋪在 event 上也讀得到。
_RATE_LIMIT_FIELDS = (
    "status",
    "rate_limit_type",
    "resets_at",
    "utilization",
    "overage_status",
    "overage_resets_at",
    "overage_disabled_reason",
)

# 一次 SDK 呼叫裡看過的 AssistantMessage.model（實際跑的 model id），依 site 分開。
# 值是 ``(擁有者 task 的 id, {site: models})``，而且只做 copy-on-write：
# 新 task 會繼承建立者的 context（淺複製），若直接改 dict，就會把上一個呼叫的
# model 帶進下一個呼叫。擁有者不是目前的 task 時一律當作空的。
# 收到 ResultMessage 或例外時取出並清掉。
_seen_models: contextvars.ContextVar[tuple[int | None, dict[str, tuple[str, ...]]] | None] = (
    contextvars.ContextVar("nakama_llm_lane_seen_models", default=None)
)


def subscription_env() -> dict[str, str]:
    """SDK 子進程的訂閱認證覆寫（給 ``ClaudeAgentOptions(env=...)`` 用）。

    設了 ``CLAUDE_CODE_OAUTH_TOKEN``（修修 2026-08-18 裁決 #4 的 process-wide
    token）→ 子進程走 Claude 訂閱額度，**並同時把 ``ANTHROPIC_API_KEY`` 清空**：
    兩憑證並存時 CLI 的優先序是 API key 贏（實測，非文件保證），留著等於把
    「走訂閱」變成賭局。清空只影響 SDK 子進程 —— 同 process 內其他
    ``shared.llm`` 呼叫仍讀得到原本的 key。

    未設 token → 回空 dict，行為與不傳 env 逐位元相同（本機測試 / 未部署
    token 的環境不受影響）。
    """
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not token:
        return {}
    return {"CLAUDE_CODE_OAUTH_TOKEN": token, "ANTHROPIC_API_KEY": ""}


# ── S0 取證：SDK stream 訊號原樣進 log（ADR-070 U1）──────────────────────


def _lane_logger() -> logging.Logger:
    # lazy：import 本模組不觸發 shared.log 的初始化（.env 載入、handler 掛載）
    from shared.log import get_logger  # noqa: PLC0415

    return get_logger(LANE_LOGGER_NAME)


def sdk_message_kind(msg: Any) -> str | None:
    """``"RateLimitEvent"`` / ``"AssistantMessage"`` / ``"ResultMessage"``，其他回 ``None``。

    先用 SDK 類別做 isinstance；類別 import 不到（舊版 SDK）或是測試用的假物件時，
    退回比對類別名稱。
    """
    for cls, kind in (
        (_RateLimitEvent, _KIND_RATE_LIMIT),
        (_AssistantMessage, _KIND_ASSISTANT),
        (_ResultMessage, _KIND_RESULT),
    ):
        if cls is not None and isinstance(msg, cls):
            return kind
    name = type(msg).__name__
    if name in (_KIND_RATE_LIMIT, _KIND_ASSISTANT, _KIND_RESULT):
        return name
    return None


def _truncate(value: Any, limit: int = _TEXT_LIMIT) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"…[truncated {len(value) - limit} chars]"
    return value


def _epoch_to_iso(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _assistant_text(msg: Any) -> str | None:
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return _truncate(content)
    if not isinstance(content, list):
        return None
    parts = [block.text for block in content if isinstance(getattr(block, "text", None), str)]
    return _truncate("\n".join(parts)) if parts else None


def _compact_usage(usage: Any) -> Any:
    """成功那一行只留 token 數（數值欄位）和 service_tier，避免 log 行過長。"""
    if not isinstance(usage, dict):
        return usage
    keep = {
        k: v for k, v in usage.items() if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    if "service_tier" in usage:
        keep["service_tier"] = usage["service_tier"]
    return keep


def _rate_limit_record(msg: Any) -> dict[str, Any]:
    info = getattr(msg, "rate_limit_info", None)
    src = info if info is not None else msg
    rec: dict[str, Any] = {f: getattr(src, f, None) for f in _RATE_LIMIT_FIELDS}
    rec["resets_at_iso"] = _epoch_to_iso(rec["resets_at"])
    rec["overage_resets_at_iso"] = _epoch_to_iso(rec["overage_resets_at"])
    rec["raw"] = getattr(src, "raw", None)
    rec["session_id"] = getattr(msg, "session_id", None)
    rec["uuid"] = getattr(msg, "uuid", None)
    return rec


def _assistant_record(msg: Any) -> dict[str, Any]:
    return {
        "error": getattr(msg, "error", None),
        "model": getattr(msg, "model", None),
        "text": _assistant_text(msg),
        "stop_reason": getattr(msg, "stop_reason", None),
        "usage": getattr(msg, "usage", None),
        "session_id": getattr(msg, "session_id", None),
        "message_id": getattr(msg, "message_id", None),
    }


def _result_is_error(msg: Any) -> bool:
    return bool(getattr(msg, "is_error", False)) or getattr(msg, "subtype", None) != "success"


def _result_record(msg: Any, *, full: bool) -> dict[str, Any]:
    model_usage = getattr(msg, "model_usage", None)
    rec: dict[str, Any] = {
        "subtype": getattr(msg, "subtype", None),
        "is_error": getattr(msg, "is_error", None),
        "session_id": getattr(msg, "session_id", None),
        "num_turns": getattr(msg, "num_turns", None),
        "duration_ms": getattr(msg, "duration_ms", None),
        "total_cost_usd": getattr(msg, "total_cost_usd", None),
        "model_usage_models": sorted(model_usage) if isinstance(model_usage, dict) else None,
    }
    if not full:
        rec["usage"] = _compact_usage(getattr(msg, "usage", None))
        return rec
    errors = getattr(msg, "errors", None)
    if isinstance(errors, list):
        errors = [_truncate(e) if isinstance(e, str) else e for e in errors[:_MAX_ERRORS]]
    rec.update(
        {
            "api_error_status": getattr(msg, "api_error_status", None),
            "errors": errors,
            "terminal_reason": getattr(msg, "terminal_reason", None),
            "stop_reason": getattr(msg, "stop_reason", None),
            "result": _truncate(getattr(msg, "result", None)),
            "usage": getattr(msg, "usage", None),
            "model_usage": model_usage,
            "duration_api_ms": getattr(msg, "duration_api_ms", None),
        }
    )
    return rec


def describe_sdk_message(msg: Any) -> tuple[str, dict[str, Any]] | None:
    """把一個 SDK stream message 轉成 ``(event, 欄位 dict)``；不需要記的回 ``None``。

    純函式、不寫 log —— ``scripts/llm_lane_probe.py`` 也用它收集證據。
    每個屬性都用 ``getattr(..., None)`` 讀：VPS 的 SDK 0.2.128 可能缺欄位。

    - ``RateLimitEvent`` → ``sdk_rate_limit``（每一個都記）
    - ``AssistantMessage`` 且 ``error`` 不是 None → ``sdk_assistant_error``
    - ``ResultMessage`` 且 ``is_error`` 或 ``subtype != "success"`` → ``sdk_result_error``
    - 其餘 ``ResultMessage`` → ``sdk_result_ok``（精簡欄位）
    """
    kind = sdk_message_kind(msg)
    if kind == _KIND_RATE_LIMIT:
        return "sdk_rate_limit", _rate_limit_record(msg)
    if kind == _KIND_ASSISTANT:
        if getattr(msg, "error", None) is None:
            return None
        return "sdk_assistant_error", _assistant_record(msg)
    if kind == _KIND_RESULT:
        if _result_is_error(msg):
            return "sdk_result_error", _result_record(msg, full=True)
        return "sdk_result_ok", _result_record(msg, full=False)
    return None


def _context_owner() -> int | None:
    try:
        task = asyncio.current_task()
    except RuntimeError:  # 沒有 running loop（同步呼叫）
        return None
    return id(task) if task is not None else None


def _models_state() -> dict[str, tuple[str, ...]]:
    current = _seen_models.get()
    if current is None or current[0] != _context_owner():
        return {}
    return current[1]


def _remember_model(site: str, model: Any) -> None:
    if not isinstance(model, str) or not model:
        return
    state = _models_state()
    models = state.get(site, ())
    if model not in models:
        _seen_models.set((_context_owner(), {**state, site: (*models, model)}))


def _pop_models(site: str) -> list[str]:
    state = _models_state()
    if site not in state:
        return []
    remaining = dict(state)
    models = remaining.pop(site)
    _seen_models.set((_context_owner(), remaining))
    return list(models)


def _emit(level: int, event: str, site: str, fields: dict[str, Any]) -> None:
    payload = json.dumps(
        {"site": site, **fields}, ensure_ascii=False, default=str, separators=(",", ":")
    )
    _lane_logger().log(
        level, "%s %s", event, payload, extra={"llm_lane_event": event, "llm_lane_site": site}
    )


def log_sdk_message(site: str, msg: Any) -> None:
    """SDK stream 的每個 message 都丟進來；只記額度訊號、錯誤與成功摘要。

    呼叫點只要一行（``async for`` 迴圈的第一行）：``log_sdk_message("nami", message)``。
    ``AssistantMessage.model`` 會暫存在 contextvar，等 ``ResultMessage`` 來時一起
    寫進 ``models``（別名 ``haiku`` 實際解析成哪個 id，只有這裡看得到）。

    **絕不 raise**：log 失敗只會吞掉，不影響呼叫點的控制流程。
    """
    try:
        kind = sdk_message_kind(msg)
        if kind == _KIND_ASSISTANT:
            _remember_model(site, getattr(msg, "model", None))
        described = describe_sdk_message(msg)
        if described is None:
            return
        event, fields = described
        if kind == _KIND_RESULT:
            fields["models"] = _pop_models(site)
            level = logging.WARNING if event == "sdk_result_error" else logging.INFO
        elif kind == _KIND_RATE_LIMIT:
            level = logging.INFO if fields.get("status") == "allowed" else logging.WARNING
        else:
            level = logging.WARNING
        _emit(level, event, site, fields)
    except Exception:  # noqa: BLE001 — 取證 log 絕不能影響主流程
        logging.getLogger(LANE_LOGGER_NAME).debug("log_sdk_message failed", exc_info=True)


def log_sdk_exception(site: str, exc: BaseException) -> None:
    """SDK 呼叫丟出例外時，把例外文字原樣記進同一個 logger。

    SDK 在 CLI 回報 error result 後會 raise ``Exception("Claude Code returned an
    error result: …")``（SDK 0.2.134 ``_internal/query.py:385-398``、``:957-958``），
    這段文字就是 U1 要的原文。呼叫點照舊處理例外（re-raise 或降級），這裡只記 log。
    **絕不 raise**。
    """
    try:
        fields: dict[str, Any] = {
            "exc_type": type(exc).__name__,
            "exc_text": _truncate(str(exc)),
            "models": _pop_models(site),
        }
        for attr in ("exit_code", "stderr"):
            value = getattr(exc, attr, None)
            if value is not None:
                fields[attr] = _truncate(value) if isinstance(value, str) else value
        _emit(logging.WARNING, "sdk_exception", site, fields)
    except Exception:  # noqa: BLE001 — 取證 log 絕不能影響主流程
        logging.getLogger(LANE_LOGGER_NAME).debug("log_sdk_exception failed", exc_info=True)
