"""Bridge 畫面規劃表 — YouTube 照稿影片 Director 交出的畫面，由修修審。

規格 §4：`docs/plans/2026-11-15-youtube-script-video-rough-cut-and-director.md`。
集數資料夾在 `YOUTUBE_VIDEO_EPISODES_ROOT/<episode>/visual_plan.json`。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Body, Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.categories import load_categories
from thousand_sunny.auth import check_auth

page_router = APIRouter(prefix="/bridge/video-plan", tags=["bridge-video-plan"])
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)
ROOT_ENV = "YOUTUBE_VIDEO_EPISODES_ROOT"

FLAG_LABELS = {
    "replanning": "重排處理中",
    "awaiting_material": "等你提供素材",
    "pending_build": "待製作",
    "on_timeline": "已上時間軸",
}
FIELD_LABELS = {
    "category": "分類",
    "description": "畫面描述",
    "dp_instruction": "給 DP 的指示",
    "onscreen_text": "畫面上的字",
    "source": "出處",
}


def _asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    digest = hashlib.sha1()
    for name in (
        "tokens.css",
        "bridge.css",
        "bridge-pages.css",
        "bridge-video-plan.css",
        "bridge-video-plan.js",
    ):
        asset = static_dir / name
        if asset.is_file():
            digest.update(asset.read_bytes())
    return digest.hexdigest()[:8]


_ASSET_VERSION = _asset_version()


def _root() -> Path:
    value = os.environ.get(ROOT_ENV, "").strip()
    if not value:
        raise HTTPException(status_code=503, detail=f"{ROOT_ENV} is not configured")
    root = Path(value)
    if not root.is_dir():
        raise HTTPException(status_code=503, detail=f"{ROOT_ENV} is not a directory")
    return root


def _episode_dir(episode: str) -> Path:
    if (
        not episode
        or len(episode) > 120
        or episode in {".", ".."}
        or any(character in episode for character in ("/", "\\", ":", "\x00"))
    ):
        raise HTTPException(status_code=404, detail="invalid episode")
    root = _root()
    candidate = root / episode
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="invalid episode") from exc
    if not candidate.is_dir():
        raise HTTPException(status_code=404, detail=f"episode not found: {episode}")
    return candidate


def _require_auth(nakama_auth: str | None) -> None:
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")


def _row_view(plan: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    categories = load_categories()
    category = categories.get(row.get("category") or "")
    if row["kind"] == "insert":
        cue = vp.cue_lookup(plan)[row["before_cue"]]
        time_label = f"{vp.fmt_time(cue['start'])} 前插入"
        covered_text = cue["text"]
        span = f"{row.get('insert_sec') or 0:g} 秒"
    else:
        cues = vp.cue_range(plan, row["start_cue"], row["end_cue"])
        time_label = f"{vp.fmt_time(cues[0]['start'])}–{vp.fmt_time(cues[-1]['end'])}"
        covered_text = " ".join(cue["text"] for cue in cues)
        span = f"{cues[-1]['end'] - cues[0]['start']:.0f} 秒"
    flags = vp.row_flags(row)
    return {
        "row": row,
        "category": category,
        "time_label": time_label,
        "span": span,
        "covered_text": covered_text,
        "missing": vp.missing_required(row),
        "flags": [(flag, FLAG_LABELS[flag]) for flag in flags],
        "layout_fixed": bool(category and category.fixed_layout),
    }


def _context(episode: str, plan: dict[str, Any] | None) -> dict[str, Any]:
    categories = load_categories()
    context: dict[str, Any] = {
        "episode": episode,
        "asset_version": _ASSET_VERSION,
        "plan": plan,
        "field_labels": FIELD_LABELS,
    }
    if plan is None:
        return context
    entries = []
    for kind, item in vp.timeline(plan):
        if kind == "row":
            entries.append(("row", _row_view(plan, item)))
        else:
            cue_map = vp.cue_lookup(plan)
            entries.append(
                (
                    "gap",
                    {
                        "gap": item,
                        "label": f"{vp.fmt_time(item.start)}–{vp.fmt_time(item.end)}",
                        "cues": [cue_map[cue_id] for cue_id in item.cue_ids],
                    },
                )
            )
    stats = vp.stats(plan)
    longest = stats["longest_gap"]
    context.update(
        entries=entries,
        stats=stats,
        longest_anchor=f"gap-{longest.cue_ids[0]}" if longest else None,
        longest_label=(
            f"{longest.seconds:.0f} 秒 · {vp.fmt_time(longest.start)}" if longest else None
        ),
        overlay_categories=[c for c in categories.values() if not c.auto and c.kind == "overlay"],
        insert_categories=[c for c in categories.values() if not c.auto and c.kind == "insert"],
        cues_json=json.dumps(
            [
                {"id": cue["id"], "t": vp.fmt_time(cue["start"]), "text": cue["text"]}
                for cue in plan["cues"]
            ],
            ensure_ascii=False,
        ),
        fmt_time=vp.fmt_time,
    )
    return context


def _stats_payload(plan: dict[str, Any]) -> dict[str, Any]:
    stats = vp.stats(plan)
    return {
        "approved": stats["approved"],
        "pending": stats["pending"],
        "rearrange": stats["rearrange"],
        "total": stats["total"],
    }


def _mutate(episode: str, operation) -> JSONResponse:
    path = vp.plan_path(_episode_dir(episode))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="這一集還沒有畫面規劃表")
    with vp.plan_lock(path):
        plan = vp.load_plan(path)
        try:
            result = operation(plan)
        except vp.PlanError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        vp.save_plan(path, plan)
    payload: dict[str, Any] = {"stats": _stats_payload(plan)}
    if isinstance(result, dict):
        view = _row_view(plan, result)
        payload["row"] = {
            "id": result["id"],
            "status": result.get("status"),
            "layout": result.get("layout"),
            "time_label": view["time_label"],
            "span": view["span"],
            "covered_text": view["covered_text"],
            "missing": view["missing"],
            "flags": view["flags"],
            "layout_fixed": view["layout_fixed"],
        }
    return JSONResponse(payload)


# ── pages ───────────────────────────────────────────────────────────────


@page_router.get("", response_class=HTMLResponse)
async def video_plan_index(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/video-plan", status_code=302)
    root = _root()
    episodes = sorted((path.parent.name for path in root.glob(f"*/{vp.PLAN_FILE}")), reverse=True)
    return _templates.TemplateResponse(
        request,
        "video_plan_index.html",
        {"episodes": episodes, "asset_version": _ASSET_VERSION},
    )


@page_router.get("/{episode}", response_class=HTMLResponse)
async def video_plan_page(request: Request, episode: str, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next={quote(request.url.path, safe='/')}", status_code=302)
    path = vp.plan_path(_episode_dir(episode))
    plan = vp.load_plan(path) if path.is_file() else None
    return _templates.TemplateResponse(request, "video_plan.html", _context(episode, plan))


# ── autosave API ────────────────────────────────────────────────────────


@page_router.patch("/{episode}/rows/{row_id}")
async def video_plan_update_row(
    episode: str,
    row_id: str,
    changes: dict[str, Any] = Body(...),
    nakama_auth: str | None = Cookie(None),
):
    _require_auth(nakama_auth)
    return _mutate(episode, lambda plan: vp.update_row(plan, row_id, changes))


@page_router.post("/{episode}/rows")
async def video_plan_add_row(
    episode: str,
    body: dict[str, Any] = Body(...),
    nakama_auth: str | None = Cookie(None),
):
    _require_auth(nakama_auth)
    return _mutate(
        episode, lambda plan: vp.add_row(plan, body.get("start_cue"), body.get("end_cue"))
    )


@page_router.delete("/{episode}/rows/{row_id}")
async def video_plan_delete_row(episode: str, row_id: str, nakama_auth: str | None = Cookie(None)):
    _require_auth(nakama_auth)
    return _mutate(episode, lambda plan: vp.delete_row(plan, row_id))


@page_router.post("/{episode}/rows/{row_id}/restore")
async def video_plan_restore_row(episode: str, row_id: str, nakama_auth: str | None = Cookie(None)):
    _require_auth(nakama_auth)
    return _mutate(episode, lambda plan: vp.restore_row(plan, row_id))
