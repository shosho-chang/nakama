"""Bridge routes — memory management + cost dashboard (Phase 4).

V1 scope（見 ``docs/prds/phase-4-bridge-ui.md``）：

- Memory：列出 / 編輯 / 刪除 ``user_memories``（Phase 1-3 Nami 對修修的記憶）。
  Tier 3 ``memories`` 表（agent run 日記）**不在本頁範疇**。
- Cost：近 N 天 ``api_calls`` 的 agent × model 統計 + 時間序列 + USD 估算。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from shared import agent_memory, state
from shared.pricing import calc_cost, get_pricing
from thousand_sunny.auth import check_auth, require_auth_or_key

router = APIRouter(prefix="/bridge", dependencies=[Depends(require_auth_or_key)])

# HTML 頁面走 cookie → /login redirect，不跟 API 共用 403 行為
page_router = APIRouter(prefix="/bridge")
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)


@page_router.get("", response_class=HTMLResponse)
@page_router.get("/", response_class=HTMLResponse)
async def bridge_index(request: Request, nakama_auth: str | None = Cookie(None)):
    """Hub 首頁：列出可跳轉的 Bridge 工具 + 其他 Agent UI。"""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge", status_code=302)
    return _templates.TemplateResponse(
        request,
        "index.html",
        {"robin_enabled": not os.getenv("DISABLE_ROBIN")},
    )


@page_router.get("/memory", response_class=HTMLResponse)
async def memory_page(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/memory", status_code=302)
    return _templates.TemplateResponse(request, "memory.html", {})


@page_router.get("/cost", response_class=HTMLResponse)
async def cost_page(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/cost", status_code=302)
    return _templates.TemplateResponse(request, "cost.html", {})


# ---------------------------------------------------------------------------
# Memory API
# ---------------------------------------------------------------------------


def _default_user_id() -> str:
    """單一使用者專案用 env 決定 user_id。"""
    return os.environ.get("SLACK_USER_ID_SHOSHO") or os.environ.get(
        "NAKAMA_DEFAULT_USER_ID", "shosho"
    )


class MemoryUpdate(BaseModel):
    """PATCH payload — 只傳有要改的欄位。"""

    type: Optional[str] = Field(None, description="preference / fact / decision / project")
    subject: Optional[str] = None
    content: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


@router.get("/api/memory/agents")
def memory_agents() -> dict:
    """列出目前有記憶資料的 agent（給前端 tab 用）。"""
    return {"agents": agent_memory.list_agents_with_memory()}


@router.get("/api/memory")
def memory_list(
    agent: str = Query(..., min_length=1),
    user_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    """列出該 agent 的所有 user_memories（不更新 last_accessed_at）。"""
    uid = user_id or _default_user_id()
    rows = agent_memory.list_all(agent=agent, user_id=uid, limit=limit)
    return {
        "agent": agent,
        "user_id": uid,
        "memories": [
            {
                "id": m.id,
                "agent": m.agent,
                "user_id": m.user_id,
                "type": m.type,
                "subject": m.subject,
                "content": m.content,
                "confidence": m.confidence,
                "source_thread": m.source_thread,
                "created_at": m.created_at,
                "last_accessed_at": m.last_accessed_at,
            }
            for m in rows
        ],
    }


@router.patch("/api/memory/{memory_id}")
def memory_update(memory_id: int, payload: MemoryUpdate) -> dict:
    """編輯一筆記憶。傳入的 None 欄位不動。"""
    try:
        updated = agent_memory.update(
            memory_id,
            type=payload.type,
            subject=payload.subject,
            content=payload.content,
            confidence=payload.confidence,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if updated is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return {
        "id": updated.id,
        "agent": updated.agent,
        "user_id": updated.user_id,
        "type": updated.type,
        "subject": updated.subject,
        "content": updated.content,
        "confidence": updated.confidence,
        "source_thread": updated.source_thread,
        "created_at": updated.created_at,
        "last_accessed_at": updated.last_accessed_at,
    }


@router.delete("/api/memory/{memory_id}")
def memory_delete(memory_id: int) -> dict:
    """刪除一筆記憶。"""
    if not agent_memory.forget(memory_id):
        raise HTTPException(status_code=404, detail="memory not found")
    return {"ok": True, "id": memory_id}


# ---------------------------------------------------------------------------
# Cost API
# ---------------------------------------------------------------------------


_ALLOWED_RANGES: dict[str, tuple[int, str]] = {
    # key -> (days, bucket)
    "24h": (1, "hour"),
    "7d": (7, "day"),
    "30d": (30, "day"),
}


@router.get("/api/cost")
def cost_overview(
    range: str = Query("7d"),
    agent: Optional[str] = Query(None),
) -> dict:
    """回傳 summary + timeseries + pricing 給前端一次畫圖。"""
    if range not in _ALLOWED_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"range must be one of {list(_ALLOWED_RANGES)}",
        )
    days, bucket = _ALLOWED_RANGES[range]

    summary_rows = state.get_cost_summary(agent=agent, days=days)
    timeseries_rows = state.get_cost_timeseries(agent=agent, days=days, bucket=bucket)

    def _enrich(row: dict) -> dict:
        cost = calc_cost(
            row["model"],
            input_tokens=row.get("input_tokens") or 0,
            output_tokens=row.get("output_tokens") or 0,
            cache_read_tokens=row.get("cache_read_tokens") or 0,
            cache_write_tokens=row.get("cache_write_tokens") or 0,
        )
        return {**row, "cost_usd": round(cost, 6)}

    summary = [_enrich(r) for r in summary_rows]
    timeseries = [_enrich(r) for r in timeseries_rows]

    total_cost = round(sum(r["cost_usd"] for r in summary), 6)

    # 列出這批資料實際用到的 models 的 pricing（前端 tooltip 用）
    models_seen = {r["model"] for r in summary_rows}
    pricing_map = {m: get_pricing(m).to_dict() for m in sorted(models_seen)}

    return {
        "range": range,
        "days": days,
        "bucket": bucket,
        "agent_filter": agent,
        "total_cost_usd": total_cost,
        "summary": summary,
        "timeseries": timeseries,
        "pricing": pricing_map,
    }
