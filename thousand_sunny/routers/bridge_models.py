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

from shared.llm_router import (
    KNOWN_MODELS,
    clear_override,
    get_provider,
    list_model_sites,
    set_override,
)
from shared.log import get_logger
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.bridge_models")

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "bridge"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

router = APIRouter()

# 下拉選項：(model_id, provider) — 讓 UI 標出每個選項屬哪家。
_MODEL_OPTIONS = [{"id": m, "provider": get_provider(m)} for m in KNOWN_MODELS]
_VALID_MODELS = frozenset(KNOWN_MODELS)


@router.get("/bridge/models", response_class=HTMLResponse)
async def models_page(
    request: Request, nakama_auth: str | None = Cookie(None), saved: str | None = None
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/models", status_code=302)
    sites = list_model_sites()
    return _templates.TemplateResponse(
        request,
        "models.html",
        {
            "sites": sites,
            "model_options": _MODEL_OPTIONS,
            "saved_msg": "已更新並即時生效" if saved == "1" else None,
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
