"""Bridge OPS — LLM model 控制面板（N531 slice 3）。

``GET /bridge/models`` 列出所有具名 (agent, task) call site 目前解析到的 model
（來自 ``llm_router.list_model_sites``），讓修修一頁看完 + 下拉改。``POST`` 寫/清
override store（``llm_router.set_override`` / ``clear_override``），即時生效、免重啟。

紅線：只動 model 路由設定，不碰任何 vault 內容；override 寫在 ``data/model_overrides.json``。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Cookie, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared import llm as llm_facade
from shared.llm_context import RUNTIME_GROUPS
from shared.llm_router import (
    KNOWN_MODELS,
    LANE_OPENROUTER,
    LANE_SUBSCRIPTION,
    _safe_provider,
    clear_override,
    get_auth_policy,
    lane_for_model,
    list_model_sites,
    set_override,
)
from shared.llm_transport import openrouter_enabled
from shared.log import get_logger
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.bridge_models")

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "bridge"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

router = APIRouter()

# 下拉選項：(model_id, provider) — 用 _safe_provider，未知 prefix 標 unknown 而非 startup 炸。
_MODEL_OPTIONS = [{"id": m, "provider": _safe_provider(m)} for m in KNOWN_MODELS]
_VALID_MODELS = frozenset(KNOWN_MODELS)

_ERR_MSGS = {"unknown_model": "未知的 model（不在允許清單內），未套用。"}


def _transport_for(site: dict) -> str:
    """這個 (agent, task) site 實際會走的 transport，給面板逐列標示。

    回 ``"subscription"``（L1）、``"openrouter"`` 或 ``"native"``。先套 ADR-070 D1：

    - ``vendor/model`` slug → ``openrouter``（L2）
    - Claude 別名 / ``claude-*``，且 ``shared.llm.L1_CUTOVER_GROUPS`` 不是空的 →
      ``subscription``（L1；只切了部分 runtime group 時，標籤會寫出是哪幾個）

    其餘照舊（ADR-049 transport 規則）：

    - transport 未啟用（含 per-agent ``LLM_TRANSPORT_<AGENT>`` override）→ native
    - xAI（grok-*）→ 恆 native（OpenRouter 無該 tier）
    - Anthropic 且 auth policy 非 ``api``（訂閱）→ native（claude -p Max Plan）
    - 其餘 anthropic(api) / google / openai → openrouter
    """
    lane = lane_for_model(site.get("model"))
    if lane == LANE_OPENROUTER:
        return "openrouter"
    if lane == LANE_SUBSCRIPTION and llm_facade.L1_CUTOVER_GROUPS:
        return "subscription"
    if not openrouter_enabled(agent=site.get("agent")):
        return "native"
    provider = site.get("provider")
    if provider == "xai":
        return "native"
    if provider == "anthropic":
        try:
            policy = get_auth_policy(agent=site["agent"], task=site["task"])
        except ValueError:
            policy = "api"  # 壞 AUTH_* 設定不該讓面板 500，保守當 api
        return "native" if policy != "api" else "openrouter"
    if provider in ("google", "openai"):
        return "openrouter"
    return "native"  # unknown / 未接 provider


def _transport_label(site: dict, transport: str) -> str:
    """面板 chip 上的文字。L1 / L2 用 ADR-070 的名字；舊路徑沿用原本的
    ``OpenRouter`` / ``native``（``L1_CUTOVER_GROUPS`` 為空時面板顯示跟以前一樣）。

    L1 只切了部分 runtime group 時（S1a–d 過渡期），標出是哪幾個 group 已經走 L1：
    一個 site 可能在好幾種 process 裡被呼叫（例如 Robin 在 cron 也在 Bridge），面板
    不假裝知道這一列一定走哪條。
    """
    if transport == "subscription":
        groups = llm_facade.L1_CUTOVER_GROUPS
        if groups >= RUNTIME_GROUPS:
            return "subscription (L1)"
        return f"subscription (L1 · {' / '.join(sorted(groups))})"
    if transport == "openrouter":
        if lane_for_model(site.get("model")) == LANE_OPENROUTER:
            return "openrouter (L2)"
        return "OpenRouter"
    return "native"


@router.get("/bridge/models", response_class=HTMLResponse)
async def models_page(
    request: Request,
    nakama_auth: str | None = Cookie(None),
    saved: str | None = None,
    err: str | None = None,
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/models", status_code=302)
    sites = list_model_sites()
    for s in sites:
        s["transport"] = _transport_for(s)
        s["transport_label"] = _transport_label(s, s["transport"])
    return _templates.TemplateResponse(
        request,
        "models.html",
        {
            "sites": sites,
            "model_options": _MODEL_OPTIONS,
            "transport_enabled": openrouter_enabled(),
            "saved_msg": "已更新並即時生效" if saved == "1" else None,
            "err_msg": _ERR_MSGS.get(err) if err else None,
        },
    )


@router.post("/bridge/models/set")
async def models_set(
    agent: str = Form(...),
    task: str = Form(...),
    model: str = Form(...),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/models", status_code=302)
    if model not in _VALID_MODELS:
        # 防呆：拒絕未知 model（避免把 router 設成無效值）。回頁帶錯。
        return RedirectResponse("/bridge/models?err=unknown_model", status_code=303)
    set_override(agent, task, model)
    logger.info("model override set: %s/%s -> %s", agent, task, model)
    return RedirectResponse("/bridge/models?saved=1", status_code=303)


@router.post("/bridge/models/reset")
async def models_reset(
    agent: str = Form(...),
    task: str = Form(...),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/models", status_code=302)
    clear_override(agent, task)
    logger.info("model override cleared: %s/%s", agent, task)
    return RedirectResponse("/bridge/models?saved=1", status_code=303)
