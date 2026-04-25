"""Bridge routes — memory management + cost dashboard (Phase 4).

V1 scope（見 ``docs/prds/phase-4-bridge-ui.md``）：

- Memory：列出 / 編輯 / 刪除 ``user_memories``（Phase 1-3 Nami 對修修的記憶）。
  Tier 3 ``memories`` 表（agent run 日記）**不在本頁範疇**。
- Cost：近 N 天 ``api_calls`` 的 agent × model 統計 + 時間序列 + USD 估算。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError

from shared import agent_memory, approval_queue, state
from shared.pricing import calc_cost, get_pricing
from shared.schemas.approval import ApprovalPayloadV1Adapter, PublishWpPostV1, UpdateWpPostV1
from thousand_sunny.auth import check_auth, require_auth_or_key

# ── Agent roster ─────────────────────────────────────────────────────────────
# Static config for all 9 agents. "default_state" is the fallback when there
# are no api_calls today; agents with "offline" are never promoted.
AGENT_ROSTER = [
    {
        "code": "R-01",
        "key": "robin",
        "role": "知識管理",
        "en": "Knowledge",
        "model": "claude-sonnet-4-6",
        "default_state": "online",
    },  # noqa: E501
    {
        "code": "N-02",
        "key": "nami",
        "role": "日常秘書",
        "en": "Secretary",
        "model": "claude-haiku-4-5",
        "default_state": "online",
    },  # noqa: E501
    {
        "code": "Z-03",
        "key": "zoro",
        "role": "情報蒐集",
        "en": "Scout",
        "model": "claude-sonnet-4-6",
        "default_state": "online",
    },  # noqa: E501
    {
        "code": "B-04",
        "key": "brook",
        "role": "內容撰寫",
        "en": "Composer",
        "model": "claude-opus-4-7",
        "default_state": "online",
    },  # noqa: E501
    {
        "code": "S-05",
        "key": "sanji",
        "role": "社群營運",
        "en": "Community",
        "model": "claude-haiku-4-5",
        "default_state": "online",
    },  # noqa: E501
    {
        "code": "F-06",
        "key": "franky",
        "role": "系統監測",
        "en": "Systems",
        "model": "claude-haiku-4-5",
        "default_state": "online",
    },  # noqa: E501
    {
        "code": "U-07",
        "key": "usopp",
        "role": "發布管線",
        "en": "Publisher",
        "model": "claude-sonnet-4-6",
        "default_state": "hold",
    },  # noqa: E501
    {
        "code": "C-08",
        "key": "chopper",
        "role": "健康顧問",
        "en": "Counsel",
        "model": "—",
        "default_state": "offline",
    },  # noqa: E501
    {
        "code": "D-09",
        "key": "sunny",
        "role": "整合甲板",
        "en": "Deck",
        "model": "—",
        "default_state": "offline",
    },  # noqa: E501
]

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
        {
            "robin_enabled": not os.getenv("DISABLE_ROBIN"),
            "drafts_pending_count": len(approval_queue.list_by_status("pending")),
        },
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
# Drafts — HITL approval queue UI（read-only scaffolding, ADR-006）
# ---------------------------------------------------------------------------


def _summarize_draft_row(row: dict[str, Any]) -> dict[str, Any]:
    """Decorate a raw approval_queue row with a parsed-payload summary for the UI.

    Bad payloads (schema drift / corrupted JSON) get `parse_error` set instead
    of crashing the whole list — same soft-fail philosophy as
    `claim_approved_drafts()` (ADR-006 borderline #2.5).
    """
    summary: dict[str, Any] = {
        "id": row["id"],
        "status": row["status"],
        "source_agent": row["source_agent"],
        "target_platform": row.get("target_platform"),
        "target_site": row.get("target_site"),
        "action_type": row.get("action_type"),
        "title_snippet": row.get("title_snippet") or "",
        "operation_id": row.get("operation_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "priority": row.get("priority", 50),
        "compliance_ack": bool(row.get("reviewer_compliance_ack")),
        "parse_error": None,
        "compliance_flagged": False,
    }
    raw = row.get("payload")
    if not raw:
        return summary
    try:
        payload = ApprovalPayloadV1Adapter.validate_python(json.loads(raw))
    except (json.JSONDecodeError, ValidationError, TypeError) as e:
        summary["parse_error"] = f"{type(e).__name__}: {e}"
        return summary
    flags = payload.compliance_flags
    summary["compliance_flagged"] = bool(flags.medical_claim or flags.absolute_assertion)
    return summary


@page_router.get("/drafts", response_class=HTMLResponse)
async def drafts_page(request: Request, nakama_auth: str | None = Cookie(None)):
    """List drafts in the HITL approval queue. Read-only in this scaffolding."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/drafts", status_code=302)

    pending_rows = approval_queue.list_by_status("pending")
    in_review_rows = approval_queue.list_by_status("in_review")
    drafts = [_summarize_draft_row(r) for r in (pending_rows + in_review_rows)]

    return _templates.TemplateResponse(
        request,
        "drafts.html",
        {
            "drafts": drafts,
            "pending_count": len(pending_rows),
            "in_review_count": len(in_review_rows),
        },
    )


@page_router.get("/drafts/{draft_id:int}", response_class=HTMLResponse)
async def draft_detail_page(
    draft_id: int, request: Request, nakama_auth: str | None = Cookie(None)
):
    """Single draft detail — payload preview + stub action buttons (Phase 2)."""
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/drafts/{draft_id}", status_code=302)

    row = approval_queue.get_by_id(draft_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"draft {draft_id} not found")

    summary = _summarize_draft_row(row)
    parsed_payload: ApprovalPayloadV1Adapter | None = None
    payload_pretty: str | None = None
    headline_fields: list[tuple[str, str]] = []

    raw = row.get("payload")
    if raw:
        try:
            payload = ApprovalPayloadV1Adapter.validate_python(json.loads(raw))
            parsed_payload = payload
            payload_pretty = json.dumps(
                json.loads(payload.model_dump_json()),
                ensure_ascii=False,
                indent=2,
            )
            if isinstance(payload, PublishWpPostV1):
                headline_fields = [
                    ("title", payload.draft.title),
                    ("target_site", payload.target_site),
                    ("scheduled_at", str(payload.scheduled_at) if payload.scheduled_at else "—"),
                ]
            elif isinstance(payload, UpdateWpPostV1):
                headline_fields = [
                    ("change_summary", payload.change_summary),
                    ("target_site", payload.target_site),
                    ("wp_post_id", str(payload.wp_post_id)),
                ]
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            summary["parse_error"] = f"{type(e).__name__}: {e}"
            payload_pretty = raw  # show raw text so reviewer can triage manually

    return _templates.TemplateResponse(
        request,
        "draft_detail.html",
        {
            "draft": summary,
            "raw_row": row,
            "headline_fields": headline_fields,
            "payload_pretty": payload_pretty,
            "has_parsed_payload": parsed_payload is not None,
        },
    )


# ---------------------------------------------------------------------------
# Agent roster API
# ---------------------------------------------------------------------------


@router.get("/api/agents")
def agents_list() -> dict:
    """回傳 9 個 agent 的定義 + 今日 token / run 統計。

    State 邏輯：
    - default_state == "offline" → 永遠 offline
    - default_state == "hold"    → 永遠 hold
    - 有今日 api_calls            → "online"
    - 否則                        → "idle"
    """
    today_rows = state.get_cost_summary(days=1)

    # Aggregate by agent key (sum across models)
    today_by_agent: dict[str, dict] = {}
    for row in today_rows:
        key = row["agent"]
        if key not in today_by_agent:
            today_by_agent[key] = {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        today_by_agent[key]["calls"] += row.get("calls") or 0
        today_by_agent[key]["input_tokens"] += row.get("input_tokens") or 0
        today_by_agent[key]["output_tokens"] += row.get("output_tokens") or 0
        today_by_agent[key]["cache_read_tokens"] += row.get("cache_read_tokens") or 0
        today_by_agent[key]["cache_write_tokens"] += row.get("cache_write_tokens") or 0

    result = []
    for a in AGENT_ROSTER:
        key = a["key"]
        usage = today_by_agent.get(key, {})
        tok_today = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        runs_today = usage.get("calls") or 0

        # Derive state
        if a["default_state"] in ("offline", "hold"):
            agent_state = a["default_state"]
        elif runs_today > 0:
            agent_state = "online"
        else:
            agent_state = "idle"

        # Cost estimate for today
        cost_today = round(
            calc_cost(
                a["model"],
                input_tokens=usage.get("input_tokens") or 0,
                output_tokens=usage.get("output_tokens") or 0,
                cache_read_tokens=usage.get("cache_read_tokens") or 0,
                cache_write_tokens=usage.get("cache_write_tokens") or 0,
            ),
            4,
        )

        result.append(
            {
                "code": a["code"],
                "key": key,
                "role": a["role"],
                "en": a["en"],
                "model": a["model"],
                "state": agent_state,
                "tok_today": tok_today,
                "runs_today": runs_today,
                "cost_today": cost_today,
            }
        )

    return {"agents": result}


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

    type: Optional[str] = Field(None, description="preference / fact / decision / context")
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
