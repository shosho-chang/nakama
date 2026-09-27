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

S1（ADR-070 D2 八項職責、D3）：本模組成為 **L1（Claude 訂閱）唯一的實作**。
公開入口是 :func:`run_text`（一次性文字 / 結構化輸出呼叫）；``shared.llm`` facade
只有在 ``L1_CUTOVER_GROUPS`` 含目前的 runtime group 時才會分派過來 —— S1 出貨時
那個集合是空的，所以當時沒有任何 production 路徑會走到這裡；S1a（2026-09-25）
把 ``gateway`` 加進來後，gateway process 的五個呼叫點（意圖分類、Sanji / Zoro
handler、orchestrator、記憶抽取）開始走這裡。

- D2-1 憑證：:func:`l1_child_env`。唯一來源 ``CLAUDE_CODE_OAUTH_TOKEN``（D3：
  ``NAMI_SDK_OAUTH_TOKEN`` 只當 deprecated 備援），並把會壓過它的變數全部清成空字串。
  子進程 env 是 ``{**os.environ, **options.env}``，只能覆寫不能刪；空字串在內附 CLI
  裡等同未設（證據見 :data:`L1_BLANKED_ENV` 的註解）。
- D2-2/3 一次性預設 ``tools=[]``、``setting_sources=[]``、``max_turns=1``；有 schema 時
  ``output_format``（draft-07）且 ``max_turns=3``。
- D2-4 timeout：``anyio.fail_after``（不用 ``asyncio.wait_for``，F17）。
- D2-5 機器層級併發：``state.db`` 的 ``llm_l1_leases`` 租約，上限
  :data:`L1_MACHINE_CONCURRENCY`；巢狀呼叫（contextvar 標記）不佔名額。
- D2-6 用量：每次呼叫一筆 ``api_calls``（``lane_actual="subscription"``、實際 model、
  token、最後一個 ``rate_limit_info``）；``cost_usd=None``（U2：SDK cost 是估計值）。
- D2-7 錯誤：:class:`SubscriptionExhausted` / :class:`SubscriptionAuthError`，
  ``shared.retry`` 不重試；SDK 回報的欄位原樣放在 ``details``。
- D2-8 async：目前 thread 已有 event loop 在跑時，改在專用 worker thread 執行
  （帶 context），絕不在 loop 裡 ``asyncio.run``。

S2a（issue #1321/#1322，D5 後端）：:func:`run_text` 呼叫前先看 lane 狀態
（``_dispatch_and_run``）。權威機器（VPS，``shared.llm_lane._is_lane_authority()``）
上：已經被擋的 family 直接 fail-fast 或改走 OpenRouter；剛好這次呼叫才踩到
額度上限的，抓到 :class:`SubscriptionExhausted` 後記一筆狀態轉換，
``interactive`` 在當日上限內就地自動改道重試一次。非權威機器（桌機）上：
只用 ``llm_lane.get_dispatch_state()`` 唯讀查詢這次呼叫是否被擋，被擋就直接
丟出 :class:`SubscriptionExhausted`——**桌機一律不改走 OpenRouter**，因為它的
花費記不進 VPS 的上限累計；沒被擋就照常走訂閱；呼叫本身丟出
:class:`SubscriptionExhausted` 時原樣往上丟、不寫任何狀態（VPS 自己的呼叫會
偵測到同一次額度用完），只記 warning。authority 判斷**不用 runtime group**，
見 ``shared/llm_lane.py`` 模組 docstring。S1a 起 gateway process 的五個呼叫點會
真的走到這個分派邏輯。
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import time
import uuid
import warnings
from collections.abc import Iterator
from contextlib import aclosing, contextmanager
from datetime import datetime, timezone
from typing import Any

from shared.llm_context import get_current_agent, spawn_thread

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
    "CALL_CLASSES",
    "L1_BLANKED_ENV",
    "L1_MACHINE_CONCURRENCY",
    "LANE_LOGGER_NAME",
    "AgentSdkError",
    "L1LeaseTimeout",
    "SubscriptionAuthError",
    "SubscriptionExhausted",
    "describe_sdk_message",
    "flatten_messages",
    "in_l1_session",
    "l1_child_env",
    "l1_session",
    "log_sdk_exception",
    "log_sdk_message",
    "run_text",
    "run_text_probe_subscription",
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

    ADR-070 S1 起這是**舊版** helper：只給既有的 SDK 呼叫點（Robin merger、Sanji
    judge、``scripts/llm_lane_probe.py``）用，行為刻意不動（S1 零行為改變）。
    L1 新路徑一律用 :func:`l1_child_env`；舊呼叫點在各自的切換 slice（S4）收斂。

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


# ── ADR-070 S1：L1 核心（D2 八項職責、D3）────────────────────────────────

L1_MACHINE_CONCURRENCY = 2
"""D2 第 5 項：整台機器同時跑的 L1 CLI 子進程上限。

U4 實測每個 CLI 子進程峰值 RSS：VPS 288–292MB、桌機 347–358MB；VPS 可用約 1.7G，
兩個約 580MB → 初始值 2（ADR-070 S0 取證結果）。要調整就改這個常數（D4：政策寫在 code）。
"""

CALL_CLASSES: frozenset[str] = frozenset({"interactive", "batch"})
"""D5 的呼叫分類。S1 只驗證並記錄，額度用完時的分流在 S2。"""

_OAUTH_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
_LEGACY_OAUTH_ENV = "NAMI_SDK_OAUTH_TOKEN"  # D3：併入 CLAUDE_CODE_OAUTH_TOKEN，deprecated
_MAX_OUTPUT_TOKENS_ENV = "CLAUDE_CODE_MAX_OUTPUT_TOKENS"

L1_BLANKED_ENV: tuple[str, ...] = (
    # 會壓過 CLAUDE_CODE_OAUTH_TOKEN 的憑證（F4）
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
    # 把請求導離 Anthropic 第一方 API 的傳輸設定
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_UNIX_SOCKET",
    # 雲端 provider 旗標：內附 CLI 2.1.226 的 provider 判斷（``Kn()``）認得的全部
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_GATEWAY",
)
"""L1 子進程 env 裡一律設成空字串的變數（D2 第 1 項）。

SDK 組子進程 env 的方式是 ``{**os.environ, **options.env}``（SDK 0.2.134
``_internal/transport/subprocess_cli.py:791-797``），只能覆寫、不能刪。空字串在內附
CLI（2.1.226）裡等同未設，證據（2026-09-25 查證）：

- 讀 binary：憑證來源判斷 ``sC()`` 是 ``if(te.ANTHROPIC_AUTH_TOKEN&&…)`` →
  ``if(te.CLAUDE_CODE_OAUTH_TOKEN)``，全是 truthiness 判斷，空字串為 false；provider
  判斷 ``Kn()`` 同樣用 truthiness；旗標解析 ``_r(e){if(!e)return!1;…}`` 對空字串回
  false；base URL 走 ``ANTHROPIC_BASE_URL||預設值``；``ANTHROPIC_UNIX_SOCKET`` /
  ``CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR`` 也都是 truthiness 判斷。
- 實測：用 ``claude auth status``（純本機、不呼叫 LLM）配隔離的 ``CLAUDE_CONFIG_DIR``
  與假 token。只設 OAuth token、以及 OAuth token 加上本表全部設成空字串，兩者輸出
  完全相同（``authMethod=oauth_token``、``apiProvider=firstParty``、來源
  ``CLAUDE_CODE_OAUTH_TOKEN``、沒有 base URL）；對照組把 ``ANTHROPIC_AUTH_TOKEN``、
  ``ANTHROPIC_API_KEY``、``CLAUDE_CODE_USE_BEDROCK``、``CLAUDE_CODE_USE_VERTEX``、
  ``ANTHROPIC_BASE_URL`` 設成非空值時，輸出分別變成對應的來源 / provider / base URL。

所以不需要另外包一層「從 process env 刪掉」的 wrapper。SDK 升版（D10）後若 CLI 改了
判斷方式要重新查證；``tests/shared/test_agent_sdk_l1.py`` 鎖住這張表。
"""

_SITE = "l1.run_text"
_TEXT_MAX_TURNS = 1
_STRUCTURED_MAX_TURNS = 3  # D2 第 2 項：SDK 在輸出不合 schema 時要求重寫，需要額外回合
_LEASE_TTL_GRACE_S = 60.0  # timeout 之後子進程收尾的寬限；租約 TTL = timeout_s + 這個值
_LEASE_POLL_S = 0.5
_STDERR_TAIL_LINES = 50
_EXHAUSTED_ASSISTANT_ERRORS = frozenset({"rate_limit", "billing_error"})
_AUTH_ASSISTANT_ERRORS = frozenset({"authentication_failed"})

# D2 第 8 項：目前這段 context 是否已經在一個 L1 session 裡（持有名額）。巢狀呼叫
# （例如 S4 的 Nami session 裡 tool 又呼叫 L1）看到 True 就不再佔名額，避免外層占著
# 名額、又等自己裡面的呼叫而卡死。worker thread 用 spawn_thread 帶 context，標記會跟著走。
_l1_session_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "nakama_l1_session_active", default=False
)
_legacy_token_logged = False


class AgentSdkError(RuntimeError):
    """L1 呼叫失敗。``details`` 原樣保存 SDK 回報的欄位（D2 第 7 項、reliability §9）：

    ``models``、``rate_limit``（最後一個 ``rate_limit_info``）、``rate_limit_rejected``、
    ``assistant_errors``、``result``（完整 ResultMessage 欄位）、``exception``、
    ``stderr_tail``、``timed_out``。D5（S2）的狀態機讀這些欄位做判斷。
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details: dict[str, Any] = dict(details or {})


class SubscriptionExhausted(AgentSdkError):
    """訂閱額度用完：``rate_limit_info.status == "rejected"``，或
    ``AssistantMessage.error`` 是 ``rate_limit`` / ``billing_error``（F15）。

    不可重試（``shared.retry`` 看 ``nakama_non_retryable``）。S1 只丟出，不改道；
    通知與「要不要切 OpenRouter」是 S2 的 D5 狀態機。
    """

    nakama_non_retryable = True

    @property
    def rate_limit_type(self) -> str | None:
        return self.details.get("rate_limit_type")

    @property
    def resets_at(self) -> int | None:
        return self.details.get("resets_at")


class SubscriptionAuthError(AgentSdkError):
    """訂閱憑證不能用：``AssistantMessage.error == "authentication_failed"``，或這台
    機器根本沒設 ``CLAUDE_CODE_OAUTH_TOKEN``。不可重試。D5 把它歸為 ``auth_error``。
    """

    nakama_non_retryable = True


class L1LeaseTimeout(TimeoutError):
    """在等待時間內拿不到機器層級名額（D2 第 5 項）。是 ``TimeoutError``，可重試。"""


# ── D2 第 1 項 / D3：憑證 ─────────────────────────────────────────────


def _log_legacy_token_once() -> None:
    global _legacy_token_logged
    if _legacy_token_logged:
        return
    _legacy_token_logged = True
    _lane_logger().warning(
        "L1 正在用 deprecated 的 %s（ADR-070 D3）；請改設 %s", _LEGACY_OAUTH_ENV, _OAUTH_ENV
    )


def _resolve_oauth_token() -> str | None:
    token = os.environ.get(_OAUTH_ENV, "").strip()
    if token:
        return token
    legacy = os.environ.get(_LEGACY_OAUTH_ENV, "").strip()
    if legacy:
        warnings.warn(
            f"{_LEGACY_OAUTH_ENV} 已併入 {_OAUTH_ENV}（ADR-070 D3），請改設 {_OAUTH_ENV}",
            DeprecationWarning,
            stacklevel=3,
        )
        _log_legacy_token_once()
        return legacy
    return None


def l1_child_env(*, max_output_tokens: int | None = None) -> dict[str, str]:
    """L1 子進程的 env 覆寫（給 ``ClaudeAgentOptions(env=...)``）。

    - 注入 ``CLAUDE_CODE_OAUTH_TOKEN``；沒有時退回 deprecated 的
      ``NAMI_SDK_OAUTH_TOKEN``（發 ``DeprecationWarning``）。
    - :data:`L1_BLANKED_ENV` 全部設成空字串（等同未設，證據見該常數）。
    - ``max_output_tokens`` → ``CLAUDE_CODE_MAX_OUTPUT_TOKENS``（SDK 沒有 max_tokens，F2）。

    兩個 token 都沒有 → :class:`SubscriptionAuthError`。**不退回** CLI 自己的登入檔：
    那可能是 Console 登入（API 計費），D2 第 1 項規定唯一來源是 OAuth token。
    """
    token = _resolve_oauth_token()
    if not token:
        raise SubscriptionAuthError(
            f"L1 需要 {_OAUTH_ENV}（在這台機器跑 `claude setup-token`，寫進 .env）；"
            "沒有時不改用任何其他憑證，避免默默走 API key 計費",
            details={"reason": "no_oauth_token"},
        )
    env = dict.fromkeys(L1_BLANKED_ENV, "")
    env[_OAUTH_ENV] = token
    if max_output_tokens is not None:
        if isinstance(max_output_tokens, bool) or int(max_output_tokens) <= 0:
            raise ValueError(f"max_output_tokens 必須是正整數，收到 {max_output_tokens!r}")
        env[_MAX_OUTPUT_TOKENS_ENV] = str(int(max_output_tokens))
    return env


def flatten_messages(messages: list[dict]) -> str:
    """把多輪 ``messages`` 攤平成單一 prompt（D2：沿用 ``claude_cli_client`` 的做法）。

    格式是 ``[USER]\\n…\\n\\n[ASSISTANT]\\n…``；``role="system"`` 略過（system 由 caller
    另外傳）；content 是 block list 時只取 text block。現有 ``ask_multi`` 呼叫點都是
    「失敗就補一句提醒再試一次」的一到兩輪形狀，攤平不會失真。
    """
    parts: list[str] = []
    for msg in messages:
        role = str(msg.get("role", "user")).upper()
        if role == "SYSTEM":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


# ── D2 第 5 / 8 項：機器層級名額與巢狀呼叫 ──────────────────────────────


def in_l1_session() -> bool:
    """目前的 context 是否已經在一個持有名額的 L1 session 裡。"""
    return _l1_session_active.get()


def _acquire_l1_slot(*, call_class: str, ttl_s: float, wait_s: float) -> str:
    from shared import state  # noqa: PLC0415 — lazy：import 本模組不碰 DB

    lease_id = uuid.uuid4().hex
    deadline = time.monotonic() + wait_s
    agent = get_current_agent()
    while True:
        if state.try_acquire_l1_lease(
            lease_id,
            limit=L1_MACHINE_CONCURRENCY,
            ttl_s=ttl_s,
            pid=os.getpid(),
            agent=agent,
            call_class=call_class,
        ):
            return lease_id
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise L1LeaseTimeout(
                f"等了 {wait_s:g}s 仍拿不到 L1 名額（機器上限 {L1_MACHINE_CONCURRENCY}）"
            )
        time.sleep(min(_LEASE_POLL_S, remaining))


def _release_l1_slot(lease_id: str) -> None:
    from shared import state  # noqa: PLC0415

    try:
        state.release_l1_lease(lease_id)
    except Exception:  # noqa: BLE001 — 放不掉就等 TTL 過期，不能蓋掉呼叫本身的結果
        _lane_logger().warning("L1 lease release failed: %s", lease_id, exc_info=True)


@contextmanager
def l1_session(*, call_class: str, ttl_s: float, wait_s: float) -> Iterator[str | None]:
    """持有一個機器層級 L1 名額的區段；回傳 lease id，巢狀時回 ``None``。

    - 外層：從 ``state.db`` 租一個名額（最多等 ``wait_s`` 秒，否則
      :class:`L1LeaseTimeout`），TTL ``ttl_s`` 秒；離開時歸還。
    - 巢狀（目前 context 已在 L1 session 裡）：不佔名額，直接執行。

    :func:`run_text` 每次呼叫都走這裡；S4 的 agentic session 也要用它包住整個 session，
    session 裡的 tool 再呼叫 L1 時就不會重複佔名額。
    """
    if _l1_session_active.get():
        yield None
        return
    lease_id = _acquire_l1_slot(call_class=call_class, ttl_s=ttl_s, wait_s=wait_s)
    token = _l1_session_active.set(True)
    try:
        yield lease_id
    finally:
        _l1_session_active.reset(token)
        _release_l1_slot(lease_id)


# ── D2 第 2–4、6、7 項：一次性呼叫 ──────────────────────────────────────


class _CallOutcome:
    """一次 SDK 呼叫收到的所有訊號（原樣保存，最後才分類）。

    刻意不用 ``@dataclass``：測試會用 ``spec_from_file_location`` 在「沒有 SDK」的
    情境下另外載入本檔，那種載入方式沒註冊進 ``sys.modules``，dataclass 會炸。
    """

    def __init__(self) -> None:
        self.result: Any = None  # 第一個 ResultMessage
        self.extra_results = 0
        self.models: list[str] = []
        self.assistant_errors: list[dict[str, Any]] = []
        self.rate_limit: dict[str, Any] | None = None  # 最後一個 rate_limit_info
        self.rate_limit_rejected: dict[str, Any] | None = None  # 任何一個 status == rejected
        self.exception: BaseException | None = None
        self.timed_out = False
        self.stderr_tail: list[str] = []

    def collect(self, msg: Any) -> None:
        kind = sdk_message_kind(msg)
        if kind == _KIND_RATE_LIMIT:
            rec = _rate_limit_record(msg)
            self.rate_limit = rec
            if rec.get("status") == "rejected":
                self.rate_limit_rejected = rec
        elif kind == _KIND_ASSISTANT:
            model = getattr(msg, "model", None)
            if isinstance(model, str) and model and model not in self.models:
                self.models.append(model)
            if getattr(msg, "error", None) is not None:
                self.assistant_errors.append(_assistant_record(msg))
        elif kind == _KIND_RESULT:
            if self.result is None:
                self.result = msg
            else:
                self.extra_results += 1

    def assistant_error_codes(self) -> set[str]:
        return {e["error"] for e in self.assistant_errors if isinstance(e.get("error"), str)}

    def details(self) -> dict[str, Any]:
        exc = self.exception
        return {
            "models": list(self.models),
            "rate_limit": self.rate_limit,
            "rate_limit_rejected": self.rate_limit_rejected,
            "assistant_errors": list(self.assistant_errors),
            "result": _result_record(self.result, full=True) if self.result is not None else None,
            "extra_results": self.extra_results,
            "exception": (
                {"type": type(exc).__name__, "text": _truncate(str(exc))}
                if exc is not None
                else None
            ),
            "stderr_tail": list(self.stderr_tail),
            "timed_out": self.timed_out,
        }


async def _run_text_async(
    prompt: str, options_kwargs: dict[str, Any], timeout_s: float
) -> _CallOutcome:
    import anyio  # noqa: PLC0415
    from claude_agent_sdk import ClaudeAgentOptions, query  # noqa: PLC0415

    outcome = _CallOutcome()

    def _on_stderr(line: str) -> None:
        if len(outcome.stderr_tail) < _STDERR_TAIL_LINES:
            outcome.stderr_tail.append(line[:500])

    options = ClaudeAgentOptions(**options_kwargs, stderr=_on_stderr)
    try:
        # D2 第 4 項：anyio 的 cancel scope 會讓 SDK 走完子進程 terminate / kill
        # （F17：asyncio.wait_for 可能跳過）。aclosing 確保 stream 在 scope 內關閉。
        with anyio.fail_after(timeout_s):
            async with aclosing(query(prompt=prompt, options=options)) as stream:
                async for msg in stream:
                    log_sdk_message(_SITE, msg)
                    outcome.collect(msg)
    except TimeoutError as exc:
        outcome.timed_out = True
        outcome.exception = exc
        log_sdk_exception(_SITE, exc)
    except Exception as exc:  # noqa: BLE001 — 原樣收進 outcome，由 _outcome_value 分類
        outcome.exception = exc
        log_sdk_exception(_SITE, exc)
    return outcome


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _record_usage(outcome: _CallOutcome, *, model: str, latency_ms: int) -> None:
    """D2 第 6 項：有真的跟 API 講到話（收到 ResultMessage 或額度訊號）就記一筆。"""
    if outcome.result is None and outcome.rate_limit is None:
        return
    try:
        from shared.llm_observability import record_call  # noqa: PLC0415

        usage = getattr(outcome.result, "usage", None)
        usage = usage if isinstance(usage, dict) else {}
        rl = outcome.rate_limit or {}
        resets_at = rl.get("resets_at")
        record_call(
            model=model,
            input_tokens=_as_int(usage.get("input_tokens")),
            output_tokens=_as_int(usage.get("output_tokens")),
            cache_read_tokens=_as_int(usage.get("cache_read_input_tokens")),
            cache_write_tokens=_as_int(usage.get("cache_creation_input_tokens")),
            latency_ms=latency_ms,
            auth_actual="subscription",
            cost_usd=None,  # U2：訂閱模式的 total_cost_usd 是 API 等值估計，不入帳
            lane_actual="subscription",
            model_actual=outcome.models[-1] if outcome.models else None,
            rate_limit_status=rl.get("status"),
            rate_limit_type=rl.get("rate_limit_type"),
            rate_limit_resets_at=_as_int(resets_at) if resets_at is not None else None,
        )
    except Exception:  # noqa: BLE001 — 用量紀錄不能影響主流程
        logging.getLogger(LANE_LOGGER_NAME).debug("L1 usage record failed", exc_info=True)


def _failure_text(outcome: _CallOutcome) -> str:
    """錯誤訊息裡的一句原文：優先 assistant 文字，其次 result / errors，最後例外文字。"""
    for rec in outcome.assistant_errors:
        if rec.get("text"):
            return _truncate(str(rec["text"]), 300)
    result = outcome.result
    if result is not None:
        text = getattr(result, "result", None)
        if isinstance(text, str) and text:
            return _truncate(text, 300)
        errors = getattr(result, "errors", None)
        if isinstance(errors, list) and errors:
            return _truncate("; ".join(str(e) for e in errors), 300)
        return f"subtype={getattr(result, 'subtype', None)}"
    if outcome.exception is not None:
        return _truncate(str(outcome.exception), 300)
    return "（沒有原文）"


def _outcome_value(outcome: _CallOutcome, *, model: str, structured: bool, timeout_s: float) -> Any:
    """把收集到的訊號分類成回傳值或例外（D2 第 7 項）。

    順序：憑證失效 → 額度用完 → timeout → 其他例外 → 沒結果 / error result →
    取值。額度與憑證的判斷用 SDK 的結構化欄位（F15），不比對錯誤文字。
    """
    details = outcome.details()
    cause = outcome.exception
    codes = outcome.assistant_error_codes()
    if codes & _AUTH_ASSISTANT_ERRORS:
        raise SubscriptionAuthError(
            f"L1 訂閱憑證失效（model={model}）：{_failure_text(outcome)}", details=details
        ) from cause
    if outcome.rate_limit_rejected is not None or codes & _EXHAUSTED_ASSISTANT_ERRORS:
        rl = outcome.rate_limit_rejected or outcome.rate_limit or {}
        details["rate_limit_type"] = rl.get("rate_limit_type")
        details["resets_at"] = rl.get("resets_at")
        raise SubscriptionExhausted(
            f"L1 訂閱額度用完（model={model}、rate_limit_type={rl.get('rate_limit_type')}、"
            f"resets_at={rl.get('resets_at_iso') or rl.get('resets_at')}）："
            f"{_failure_text(outcome)}",
            details=details,
        ) from cause
    if outcome.timed_out:
        raise TimeoutError(f"L1 呼叫超過 {timeout_s:g}s（model={model}）") from cause
    if cause is not None:
        raise AgentSdkError(
            f"L1 呼叫失敗（model={model}）：{type(cause).__name__}: {_truncate(str(cause), 500)}",
            details=details,
        ) from cause
    result = outcome.result
    if result is None:
        raise AgentSdkError(f"L1 呼叫沒有收到 ResultMessage（model={model}）", details=details)
    if _result_is_error(result):
        raise AgentSdkError(
            f"L1 呼叫回報錯誤（model={model}）：{_failure_text(outcome)}", details=details
        )
    if structured:
        value = getattr(result, "structured_output", None)
        if value is None:
            raise AgentSdkError(f"L1 structured output 沒有值（model={model}）", details=details)
        return value
    text = getattr(result, "result", None)
    if not isinstance(text, str):
        raise AgentSdkError(f"L1 ResultMessage 沒有 result 文字（model={model}）", details=details)
    return text


def _check_schema(output_schema: Any) -> None:
    if not isinstance(output_schema, dict):
        raise TypeError(f"output_schema 必須是 JSON schema dict，收到 {type(output_schema)}")
    declared = output_schema.get("$schema")
    if declared is not None and "draft-07" not in str(declared):
        # U10：宣告 2020-12 的 schema 會讓 CLI 以 exit code 1 結束
        raise ValueError(f"L1 structured output 只收 draft-07 schema，收到 $schema={declared}")


def _run_text_blocking(
    prompt: str,
    *,
    system: str,
    model: str,
    output_schema: dict[str, Any] | None,
    max_output_tokens: int | None,
    timeout_s: float,
    call_class: str,
) -> Any:
    import anyio  # noqa: PLC0415

    options_kwargs: dict[str, Any] = {
        "model": model,
        "system_prompt": system,
        "tools": [],  # F9：沒設時成本是 3 倍
        "setting_sources": [],  # 不讀機器上的 settings / CLAUDE.md
        "max_turns": _STRUCTURED_MAX_TURNS if output_schema is not None else _TEXT_MAX_TURNS,
        # CLI 預設會 thinking，thinking token 也算進 CLAUDE_CODE_MAX_OUTPUT_TOKENS：
        # 呼叫點照 API 語意給的小 max_tokens（例如意圖分類 100）會直接變成 CLI 錯誤。
        # API 路徑從沒開 thinking，關掉才一致（2026-09-26 S1a 上線實測）。
        "thinking": {"type": "disabled"},
        "env": l1_child_env(max_output_tokens=max_output_tokens),
    }
    if output_schema is not None:
        options_kwargs["output_format"] = {"type": "json_schema", "schema": output_schema}

    with l1_session(call_class=call_class, ttl_s=timeout_s + _LEASE_TTL_GRACE_S, wait_s=timeout_s):
        t0 = time.perf_counter()
        outcome = anyio.run(_run_text_async, prompt, options_kwargs, timeout_s)
        latency_ms = int((time.perf_counter() - t0) * 1000)
    if get_current_agent() is None:
        # D9：沒帶 agent context 照樣執行，但 api_calls 會記成 unknown → 發 warning 追缺口
        _lane_logger().warning(
            "L1 call without agent context (model=%s); api_calls 記成 unknown", model
        )
    _record_usage(outcome, model=model, latency_ms=latency_ms)
    return _outcome_value(
        outcome, model=model, structured=output_schema is not None, timeout_s=timeout_s
    )


def _event_loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _call_in_worker_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """D2 第 8 項：在專用 worker thread 跑 ``fn``（帶 context），阻塞等結果。

    呼叫端本來就是在 async 函式裡同步呼叫 LLM（F19），它原本就會卡住那個 loop；
    這裡只是讓 SDK 有自己的 event loop，不在正在跑的 loop 裡 ``asyncio.run``。
    """
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 — 原樣帶回呼叫端的 thread 再丟
            box["error"] = exc

    thread = spawn_thread(_target, name="nakama-l1-sdk")
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


# ── ADR-070 S2a（D5）：lane 狀態分派 ─────────────────────────────────────


def _run_via_openrouter(
    prompt: str,
    *,
    system: str,
    model: str,
    output_schema: dict[str, Any] | None,
    max_output_tokens: int | None,
    call_class: str,
) -> Any:
    """已經在 ``openrouter_auto`` / ``openrouter_approved`` 狀態：一次性文字呼叫改走
    L2（``shared.openrouter_client``），並把實際花費累加回 ``llm_lane`` 的上限。

    ``allow_fallbacks=False``（BYOK 安全，D6）；``OPENROUTER_API_KEY`` 沒設一律
    fail closed（``ask_openrouter`` 內部 ``os.environ["OPENROUTER_API_KEY"]``
    直接 ``KeyError``，這裡只是換成 caller 看得懂的訊息）。structured output
    這條路徑還沒支援（D6：「L2 目前支援文字和多輪對話」），有 schema 就 fail loud，
    不要默默丟掉 schema 硬送文字。
    """
    if output_schema is not None:
        raise AgentSdkError(
            f"L1 額度用完，{call_class} 已改走 OpenRouter，但這條路徑還不支援"
            f" structured output（model={model}）"
        )
    from shared import llm_lane  # noqa: PLC0415
    from shared.openrouter_client import ask_openrouter  # noqa: PLC0415

    cost_box: list[float | None] = []
    try:
        text = ask_openrouter(
            prompt,
            system=system,
            model=model,
            max_tokens=max_output_tokens or 4096,
            allow_fallbacks=False,
            on_cost=cost_box.append,
        )
    except KeyError as exc:
        raise AgentSdkError(
            f"L1 額度用完，{call_class} 需要改走 OpenRouter，但 OPENROUTER_API_KEY 未設置"
            f"（fail closed，不會默默改用其他憑證，model={model}）"
        ) from exc
    llm_lane.record_openrouter_spend(call_class, cost_usd=cost_box[0] if cost_box else 0.0)
    return text


def _dispatch_and_run(
    prompt: str,
    *,
    system: str,
    model: str,
    output_schema: dict[str, Any] | None,
    max_output_tokens: int | None,
    timeout_s: float,
    call_class: str,
) -> Any:
    """D5 狀態機的分派入口。權威機器（VPS）先看本機 ``state.db`` 的 lane 狀態，
    已經被擋的 family 直接 fail-fast 或改走 OpenRouter，不浪費一次注定失敗的 SDK
    呼叫；剛好在這次呼叫才踩到額度上限的，抓到 :class:`SubscriptionExhausted`
    後記一筆狀態轉換，``interactive`` 當日上限內就地自動改道重試一次。非權威機器
    （桌機）唯讀查詢 VPS 狀態，被擋就 fail-fast（不改走 OpenRouter），沒被擋就照常
    走訂閱；呼叫本身踩到額度用完時原樣往上丟、不寫任何狀態。唯一真相在 VPS，見
    ``shared.llm_lane`` 模組 docstring。
    """
    from shared import llm_lane  # noqa: PLC0415

    family = llm_lane.model_family(model)

    if not llm_lane._is_lane_authority():  # noqa: SLF001
        cls = llm_lane.get_dispatch_state().for_class(call_class)
        if cls.blocks(family):
            raise SubscriptionExhausted(
                f"L1 訂閱額度用完，{call_class} 目前擋下（model={model}、"
                f"rate_limit_type={cls.rate_limit_type}）；桌機不改走 OpenRouter",
                details={
                    "rate_limit_type": cls.rate_limit_type,
                    "resets_at": cls.resets_at,
                    "lane_status": cls.status,
                },
            )
        try:
            return _run_text_blocking(
                prompt,
                system=system,
                model=model,
                output_schema=output_schema,
                max_output_tokens=max_output_tokens,
                timeout_s=timeout_s,
                call_class=call_class,
            )
        except SubscriptionExhausted as exc:
            _lane_logger().warning(
                "llm_lane %s exhausted on non-authority machine (model=%s "
                "rate_limit_type=%s); not writing state, VPS-side calls will detect it",
                call_class,
                model,
                exc.rate_limit_type,
            )
            raise

    lane_state = llm_lane.get_state()
    cls = lane_state.for_class(call_class)

    if cls.blocks(family):
        if cls.status == "exhausted":
            raise SubscriptionExhausted(
                f"L1 訂閱額度用完，{call_class} 目前擋下（model={model}、"
                f"rate_limit_type={cls.rate_limit_type}）",
                details={
                    "rate_limit_type": cls.rate_limit_type,
                    "resets_at": cls.resets_at,
                    "lane_status": cls.status,
                },
            )
        return _run_via_openrouter(
            prompt,
            system=system,
            model=model,
            output_schema=output_schema,
            max_output_tokens=max_output_tokens,
            call_class=call_class,
        )

    try:
        return _run_text_blocking(
            prompt,
            system=system,
            model=model,
            output_schema=output_schema,
            max_output_tokens=max_output_tokens,
            timeout_s=timeout_s,
            call_class=call_class,
        )
    except SubscriptionExhausted as exc:
        new_state = llm_lane.record_exhausted(
            call_class, model=model, rate_limit_type=exc.rate_limit_type, resets_at=exc.resets_at
        )
        new_cls = new_state.for_class(call_class)
        if new_cls.status in ("openrouter_auto", "openrouter_approved"):
            return _run_via_openrouter(
                prompt,
                system=system,
                model=model,
                output_schema=output_schema,
                max_output_tokens=max_output_tokens,
                call_class=call_class,
            )
        raise


def run_text(
    prompt: str,
    *,
    system: str,
    model: str,
    output_schema: dict[str, Any] | None = None,
    max_output_tokens: int | None = None,
    timeout_s: float,
    call_class: str,
) -> Any:
    """L1 一次性呼叫：Claude 訂閱（Agent SDK），回傳文字；給了 schema 則回傳結構化輸出。

    Args:
        prompt: 使用者訊息（多輪請先用 :func:`flatten_messages` 攤平）。
        system: system prompt（空字串 = 不給）。
        model: Claude 別名（``opus`` / ``sonnet`` / ``haiku`` / ``fable``）或 ``claude-*`` id，
            原樣交給 CLI 解析；實際跑的 model 記在 ``api_calls.model_actual``。
        output_schema: draft-07 JSON schema dict。給了就走 ``output_format``、
            ``max_turns=3``，回傳 ``ResultMessage.structured_output``。
        max_output_tokens: 經 ``CLAUDE_CODE_MAX_OUTPUT_TOKENS`` 傳入（SDK 沒有 max_tokens）。
            沒有 temperature：SDK 不支援，D2 決定直接丟掉。
        timeout_s: 必填（reliability §7）。SDK 呼叫本身的 wall-clock 上限；等名額另外
            最多等同樣秒數（:class:`L1LeaseTimeout`）。
        call_class: ``interactive`` 或 ``batch``（D5；S1 只驗證）。

    Raises:
        SubscriptionExhausted: 額度用完（不可重試）。
        SubscriptionAuthError: 憑證失效或沒設 token（不可重試）。
        TimeoutError: 超過 ``timeout_s``（含 :class:`L1LeaseTimeout`）。
        AgentSdkError: 其他 SDK / CLI 失敗；``details`` 有原樣欄位。
        ValueError / TypeError: 參數不合法（不是 Claude model、schema 不是 draft-07 等）。
    """
    from shared.llm_router import is_claude_model  # noqa: PLC0415

    if not is_claude_model(model):
        raise ValueError(f"L1 只跑 Claude model（別名或 claude-*），收到 '{model}'")
    if call_class not in CALL_CLASSES:
        raise ValueError(f"call_class 必須是 {sorted(CALL_CLASSES)} 之一，收到 '{call_class}'")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise ValueError(f"timeout_s 必須是正數，收到 {timeout_s!r}")
    if output_schema is not None:
        _check_schema(output_schema)

    kwargs: dict[str, Any] = {
        "system": system,
        "model": model,
        "output_schema": output_schema,
        "max_output_tokens": max_output_tokens,
        "timeout_s": float(timeout_s),
        "call_class": call_class,
    }
    if _event_loop_running():
        return _call_in_worker_thread(_dispatch_and_run, prompt, **kwargs)
    return _dispatch_and_run(prompt, **kwargs)


def run_text_probe_subscription(
    prompt: str, *, model: str, timeout_s: float = 30.0, call_class: str = "batch"
) -> str:
    """繞過 D5 lane 分派、直接試訂閱 —— Franky 的 llm_lane 復原探針專用。

    復原探針要測的正是「訂閱本身好了沒」；透過 :func:`run_text` 測不到，因為
    目前還沒解除的 lane 狀態會在 pre-check 就把呼叫擋下（fail-fast 或改走
    OpenRouter），永遠碰不到真的訂閱 SDK。呼叫者要自己接住失敗（代表還沒恢復），
    成功才代表訂閱真的可以用了 —— 呼叫端負責接著呼叫
    ``shared.llm_lane.switch_to_subscription``。
    """
    kwargs: dict[str, Any] = {
        "system": "",
        "model": model,
        "output_schema": None,
        "max_output_tokens": None,
        "timeout_s": float(timeout_s),
        "call_class": call_class,
    }
    if _event_loop_running():
        return _call_in_worker_thread(_run_text_blocking, prompt, **kwargs)
    return _run_text_blocking(prompt, **kwargs)
