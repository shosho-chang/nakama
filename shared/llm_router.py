"""LLM model router — 把 (agent, task) 映射到 model ID，並由 ID 反推 provider。

Resolution 優先序（高到低）：
1. Caller 顯式傳 `model=...`（router 不介入）
2. Override store（N531：Bridge /bridge/models 即時改，刻意優先於 env）
3. Env var `MODEL_<AGENT>_<TASK>`（例如 `MODEL_BROOK_TOOL_USE`）
4. Env var `MODEL_<AGENT>`（例如 `MODEL_BROOK`）
5. `MODEL_REGISTRY` 宣告的 (agent, task) 預設
6. `DEFAULT_MODELS[task]`

這是 production routing 層。Bench / eval 腳本請改走 LiteLLM（設計決策見
`memory/claude/project_multi_model_architecture.md`）。

Provider coverage：Anthropic + xAI + Google（gemini-）原生已 wire；OpenAI 經
OpenRouter transport（``LLM_TRANSPORT=openrouter``）可用，無原生 SDK。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from shared.log import get_logger

logger = get_logger("nakama.llm_router")

DEFAULT_MODELS: dict[str, str] = {
    # 2026-06-16: was claude-sonnet-4-20250514 (Sonnet 4.0) — Anthropic 已退役，
    # API 回 404 not_found，Franky news curate 先踩到（ADR-026 §237 早標的 drift）。
    # 對齊 feedback_cost_management「daily Sonnet 4.6」。
    "default": "claude-sonnet-4-6",
    "tool_use": "claude-haiku-4-5",
    # Translation is high-volume plain text — Sonnet 4.6 is the cost/quality
    # sweet spot. Caller can still override via MODEL_<AGENT>_TRANSLATE.
    "translate": "claude-sonnet-4-6",
    # ADR-033 D4 + D8: thumbnail pipeline uses Sonnet 4.6 with vision for
    # reference-library style transfer (brainstorm) and frame selection (funnel).
    "thumbnail_brainstorm": "claude-sonnet-4-6",
    "thumbnail_funnel": "claude-sonnet-4-6",
}


# N531 — Model registry: 集中宣告所有「具名」(agent, task) call site，給 Bridge
# /bridge/models 面板列舉與顯示用途。``default`` 必須等於該 call site 重接前的
# 既有 model（行為保留：Slice 2 把硬寫點改成 get_model(agent, task) 時不變行為）。
@dataclass(frozen=True)
class ModelSite:
    agent: str
    task: str
    default: str
    purpose: str  # 人讀用途（UI 顯示）


MODEL_REGISTRY: tuple[ModelSite, ...] = (
    ModelSite("robin", "ingest_summary", "claude-sonnet-4-6", "Ingest：Source 摘要"),
    ModelSite("robin", "concept_merge", "claude-opus-4-7", "Ingest：Concept diff-merge"),
    ModelSite("robin", "annotation_merge", "claude-opus-4-7", "註解 merge 進 Concept"),
    ModelSite("robin", "daily_review", "claude-sonnet-4-5-20250929", "每日回顧 P-1/P-2/清掃"),
    ModelSite("robin", "kb_search", "claude-haiku-4-5-20251001", "KB 檢索 relevance reason"),
    ModelSite("robin", "project_angle_scan", "claude-haiku-4-5-20251001", "專案 KB-hit 角度掃描"),
    ModelSite("robin", "project_mechanism", "claude-opus-4-7", "專案機制草稿生成"),
    ModelSite("nami", "default", "claude-sonnet-4-6", "Nami 對話 / 秘書任務"),
    ModelSite("zoro", "default", "claude-sonnet-4-6", "Scout 趨勢 / 關鍵字"),
    ModelSite("brook", "default", "claude-sonnet-4-6", "Composer 撰稿輔助"),
    ModelSite("sanji", "default", "claude-sonnet-4-6", "社群監控"),
)

_REGISTRY_BY_KEY: dict[tuple[str, str], ModelSite] = {(s.agent, s.task): s for s in MODEL_REGISTRY}


# N531 — Bridge /bridge/models 下拉的候選 model 清單。新增可選 model 在此加一行；
# provider 由前綴自動推（見 _PROVIDER_PREFIXES）。只列已 wire 的 provider（見 shared/llm.py）。
KNOWN_MODELS: tuple[str, ...] = (
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "grok-4-fast",
    # OpenAI — 只在 LLM_TRANSPORT=openrouter 時可用（facade 無原生 OpenAI SDK，走
    # OpenRouter BYOK）；native 時選到會 fail loud。slug 見 shared/openrouter_models.py。
    "gpt-5",
    "gpt-5-mini",
    "gpt-5.6-terra",
)


def registry_default(agent: str | None, task: str) -> str | None:
    """登記表中 (agent, task) 的預設 model；無登記 → None。"""
    if not agent:
        return None
    site = _REGISTRY_BY_KEY.get((agent.lower(), task))
    return site.default if site else None


# ── Override store（N531：可由 Bridge 即時改、router 每次 call 重讀）─────────────
# JSON 落點 anchor 到 package（同 book_storage 慣例），與其他 data/ 檔同處。
# 結構：{"<agent>": {"<task>": "<model-id>", ...}, ...}
_OVERRIDES_RELPATH = Path(__file__).resolve().parent.parent / "data" / "model_overrides.json"
_overrides_cache: dict[str, dict[str, str]] = {}
_overrides_mtime: float = -1.0


def _overrides_path() -> Path:
    env = os.environ.get("NAKAMA_MODEL_OVERRIDES")
    return Path(env) if env else _OVERRIDES_RELPATH


def _read_raw_overrides(path: Path) -> dict:
    """讀原始 JSON dict；不存在 / 壞檔 → ``{}``（set/clear 共用，壞檔不致 500）。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_overrides() -> dict[str, dict[str, str]]:
    """讀 override store，用 mtime cache 達成「改檔即生效、又不每次 IO」。"""
    global _overrides_cache, _overrides_mtime
    path = _overrides_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _overrides_cache, _overrides_mtime = {}, -1.0
        return {}
    if mtime != _overrides_mtime:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _overrides_cache = {
                str(a): {str(t): str(m) for t, m in tasks.items()}
                for a, tasks in data.items()
                if isinstance(tasks, dict)
            }
            _overrides_mtime = mtime  # 只在成功時記 mtime
        except (OSError, json.JSONDecodeError, AttributeError):
            # 壞檔：清快取但 mtime 設 -1，下次重試（不鎖死在空值直到 mtime 再變）。
            _overrides_cache = {}
            _overrides_mtime = -1.0
    return _overrides_cache


def get_override(agent: str | None, task: str) -> str | None:
    """override store 中 (agent, task) 的 model；無 → None。"""
    if not agent:
        return None
    return _load_overrides().get(agent.lower(), {}).get(task)


def set_override(agent: str, task: str, model: str) -> None:
    """寫入 (agent, task) → model 的 override（Bridge 面板用），即時生效。"""
    path = _overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_raw_overrides(path)
    data.setdefault(agent.lower(), {})[task] = model
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_override(agent: str, task: str) -> None:
    """移除一個 override（回退到 env / registry / DEFAULT_MODELS）。無此 override 則不寫檔。"""
    path = _overrides_path()
    data = _read_raw_overrides(path)
    tasks = data.get(agent.lower())
    if not isinstance(tasks, dict) or task not in tasks:
        return  # 沒這筆 → 不動檔，避免 spurious mtime 觸發無謂重載
    tasks.pop(task, None)
    if not tasks:
        data.pop(agent.lower())
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# tool_use 強制 api：CLI subprocess path 拿不到 raw tool-use JSON。
_VALID_AUTH_POLICIES: frozenset[str] = frozenset(
    {"api", "subscription_preferred", "subscription_required"}
)

DEFAULT_AUTH: dict[str, str] = {
    # 2026-08-19 flip（修修 2026-08-18 裁決「都改成預設使用訂閱額度」，ADR-026
    # §Amendment 2026-08-19）：預設 subscription_preferred —— 有 OAuth token +
    # CLI 就走訂閱，缺任一條件軟降 api 並記 fallback_reason（可稽核，非 silent）。
    # 舊預設 "api" 的理由（Codex audit §4「不該默默花錢」）在 2026-08-17 額度
    # 事故後反轉：現在「默默走 API 計費」才是要防的方向。要強制 API 計費的
    # caller 用 AUTH_<AGENT>=api 顯式 opt-out。
    "default": "subscription_preferred",
    # tool_use forced api: CLI subprocess can't carry raw tool-use JSON.
    # （tool-use 要吃訂閱走 Agent SDK 路徑 —— 見 annotation_merger S2。）
    "tool_use": "api",
}

# Prefix → provider。擴 provider 時在這裡加一行，`get_provider` 與
# `shared/llm.py` 的 dispatch 就自動吃到。
_PROVIDER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("claude-", "anthropic"),
    ("grok-", "xai"),
    ("gemini-", "google"),
    ("gpt-", "openai"),
    # 全帶 trailing hyphen 才一致，避免 "o1"/"o3" 的裸 prefix 誤吃
    # 未來無關模型（e.g. 非 openai 的 "o1something"、"o100-xyz"）
    ("o1-", "openai"),
    ("o3-", "openai"),
)


def get_model(agent: str | None = None, task: str = "default") -> str:
    """解析 (agent, task) 對應的 model ID。

    Args:
        agent: Agent 名稱（例如 "brook"、"robin"）。大小寫不敏感。
            None 代表沒有 agent 上下文，跳過 agent 層級覆寫並記 debug log
            （協助診斷忘了呼叫 `set_current_agent` 的 silent fallback）。
        task: 任務類型（"default" / "tool_use" / "translate" 等）。具名 (agent, task)
            call site 的 canonical 清單見 ``MODEL_REGISTRY``。

    Returns:
        Model ID 字串（例如 "claude-sonnet-4-6"、"grok-4-fast-non-reasoning"）。
    """
    if agent:
        # 1) Override store（Bridge /bridge/models，即時生效，刻意優先於 env，
        #    讓 UI 成為 live 控制面而非被靜態 env 蓋過）
        override = get_override(agent, task)
        if override:
            return override
        agent_upper = agent.upper()
        task_upper = task.upper()
        # 2) env MODEL_<AGENT>_<TASK>
        specific = os.environ.get(f"MODEL_{agent_upper}_{task_upper}")
        if specific:
            return specific
        # 3) env MODEL_<AGENT>
        agent_default = os.environ.get(f"MODEL_{agent_upper}")
        if agent_default:
            return agent_default
        # 4) registry 宣告的 (agent, task) 預設
        reg = registry_default(agent, task)
        if reg:
            return reg
    else:
        # 沒有 agent context — 可能是 caller 沒呼叫 set_current_agent。
        # debug 層級不影響正常輸出，但 `LOG_LEVEL=DEBUG` 時能看到。
        logger.debug(
            "get_model called without agent context; falling back to DEFAULT_MODELS[%s]",
            task,
        )
    # 5) task 級全域預設
    return DEFAULT_MODELS.get(task, DEFAULT_MODELS["default"])


def get_auth_policy(agent: str | None = None, task: str = "default") -> str:
    """解析 (agent, task) 對應的 auth policy（ADR-026）。

    Resolution 優先序（高到低）：
    1. `NAKAMA_REQUIRE_MAX_PLAN=1` → 強制 `subscription_required`
       （process-wide hard-lock override，保留給 textbook ingest / sandcastle
       這種 100% 必須走 Max Plan 的場景）
    2. Env var `AUTH_<AGENT>_<TASK>`
    3. Env var `AUTH_<AGENT>`
    4. `DEFAULT_AUTH[task]`（預設 ``api``）

    Args:
        agent: Agent 名稱（大小寫不敏感）。``None`` 跳過 agent 層覆寫。
        task: 任務類型（"default" / "tool_use" / "translate" 等）。

    Returns:
        ``"api"`` / ``"subscription_preferred"`` / ``"subscription_required"`` 之一。

    Raises:
        ValueError: env 設了未知值（拼錯保護，避免 silent 走預設）。
    """
    if os.environ.get("NAKAMA_REQUIRE_MAX_PLAN") == "1":
        return "subscription_required"

    if agent:
        agent_upper = agent.upper()
        task_upper = task.upper()
        specific = os.environ.get(f"AUTH_{agent_upper}_{task_upper}")
        if specific:
            return _validate_auth_policy(specific, f"AUTH_{agent_upper}_{task_upper}")
        agent_default = os.environ.get(f"AUTH_{agent_upper}")
        if agent_default:
            return _validate_auth_policy(agent_default, f"AUTH_{agent_upper}")
    else:
        logger.debug(
            "get_auth_policy called without agent context; falling back to DEFAULT_AUTH[%s]",
            task,
        )
    return DEFAULT_AUTH.get(task, DEFAULT_AUTH["default"])


def _validate_auth_policy(value: str, source: str) -> str:
    if value not in _VALID_AUTH_POLICIES:
        raise ValueError(
            f"Invalid auth policy '{value}' from {source}; "
            f"must be one of {sorted(_VALID_AUTH_POLICIES)}"
        )
    return value


def get_provider(model: str) -> str:
    """由 model ID 推出 provider（"anthropic" / "xai" / "google" / "openai"）。

    Raises:
        ValueError: 無法辨識的 model ID prefix。
    """
    for prefix, provider in _PROVIDER_PREFIXES:
        if model.startswith(prefix):
            return provider
    raise ValueError(
        f"Unknown model provider for '{model}'. "
        f"Known prefixes: {[p for p, _ in _PROVIDER_PREFIXES]}"
    )


def _safe_provider(model: str) -> str:
    """get_provider 的不丟版（UI 列舉時未知 prefix 標 unknown 而非 500）。"""
    try:
        return get_provider(model)
    except ValueError:
        return "unknown"


def list_model_sites() -> list[dict]:
    """N531 — 列舉所有登記的 (agent, task) call site + 目前解析到的 model，給 Bridge 面板。

    每筆：agent / task / purpose / model（解析後）/ provider / source / default。
    ``source`` ∈ {override, env, registry, default}，讓 UI 標示這格目前由誰決定。
    也納入 override store 內但不在 registry 的 (agent, task)（手動加的格子）。
    """

    def _source(agent: str, task: str) -> str:
        if get_override(agent, task):
            return "override"
        au, tu = agent.upper(), task.upper()
        if os.environ.get(f"MODEL_{au}_{tu}") or os.environ.get(f"MODEL_{au}"):
            return "env"
        if registry_default(agent, task):
            return "registry"
        return "default"

    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    for site in MODEL_REGISTRY:
        model = get_model(agent=site.agent, task=site.task)
        rows.append(
            {
                "agent": site.agent,
                "task": site.task,
                "purpose": site.purpose,
                "model": model,
                "provider": _safe_provider(model),
                "source": _source(site.agent, site.task),
                "default": site.default,
            }
        )
        seen.add((site.agent, site.task))

    for agent, tasks in _load_overrides().items():
        for task in tasks:
            if (agent, task) in seen:
                continue
            model = get_model(agent=agent, task=task)
            rows.append(
                {
                    "agent": agent,
                    "task": task,
                    "purpose": "(手動 override)",
                    "model": model,
                    "provider": _safe_provider(model),
                    "source": "override",
                    "default": "",
                }
            )
    return rows
