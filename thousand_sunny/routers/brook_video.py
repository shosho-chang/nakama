"""Brook Video Production Line storyboard Bridge UI Tier 2 (ADR-032 §7).

Renamed from foundry.py by ADR-050 D1; legacy /foundry/* GETs 301-redirect here.

GET  /brook/video/{episode_id}                    storyboard table
GET  /brook/video/{episode_id}/status              JSON poll (chips, every 2.5s)

POST /brook/video/{episode_id}/beat/{bid}/approve  text_approved=True + enqueue
POST /brook/video/{episode_id}/beat/{bid}/edit     layout/component/params edit
POST /brook/video/{episode_id}/beat/{bid}/replan   re-plan via LLM + edit_log

POST .../beat/{bid}/visual/approve             visual_approved=True
POST .../beat/{bid}/visual/edit                params-only re-render
POST .../beat/{bid}/visual/replan              re-plan + re-render + edit_log

POST .../batch/approve-all-text                bulk approve + enqueue
POST .../batch/render-approved                 re-enqueue text_approved + pending
POST .../batch/finalize-passing                bulk visual_approved

POST .../edit-log/{idx}/promote                write example yaml

Tier 2 invariants (守住 3 天估值):
- Polling JS only — no SSE.
- No inline <video> — file:// link only when render done.
- No 拆/合 beat — both deferred to Phase 1.5.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, BackgroundTasks, Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agents.brook.script_video import edit_log
from shared.log import get_logger
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.brook_video")

page_router = APIRouter(prefix="/brook/video", tags=["brook-video"])

# Legacy /foundry/* bookmarks → permanent redirect (ADR-050 D1; GET only —
# POSTs come from the page's own forms, which already point at the new prefix).
legacy_router = APIRouter(prefix="/foundry", include_in_schema=False)


@legacy_router.get("/{path:path}")
async def _legacy_foundry_redirect(path: str) -> RedirectResponse:
    return RedirectResponse(f"/brook/video/{path}", status_code=301)


_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "brook_video"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# Default data root — repo-root anchored (cwd-relative would break when
# uvicorn runs from another directory, ADR-050 D4); tests override via
# _set_data_root().
_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "script_video"


def _set_data_root(path: Path) -> None:
    """Test hook — point the router at a temporary data root."""
    global _DATA_ROOT
    _DATA_ROOT = Path(path)


def _episode_dir(episode_id: str) -> Path:
    return _DATA_ROOT / episode_id


def _storyboard_path(episode_id: str) -> Path:
    return _episode_dir(episode_id) / "storyboard.yaml"


def _load_storyboard(episode_id: str) -> list[dict[str, Any]]:
    path = _storyboard_path(episode_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"storyboard not found for {episode_id}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail="malformed storyboard.yaml")
    return data


def _save_storyboard(episode_id: str, storyboard: list[dict[str, Any]]) -> None:
    path = _storyboard_path(episode_id)
    path.write_text(
        yaml.dump(storyboard, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _find_beat(storyboard: list[dict[str, Any]], beat_id: int) -> dict[str, Any]:
    for beat in storyboard:
        if beat.get("beat_id") == beat_id:
            return beat
    raise HTTPException(status_code=404, detail=f"beat {beat_id} not found")


def _shosho_asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for css in ("tokens.css", "bridge.css", "bridge-pages.css", "theme.js"):
        path = static_dir / css
        if path.exists():
            h.update(path.read_bytes())
    storyboard_js = (
        Path(__file__).resolve().parent.parent / "static" / "brook_video" / "storyboard.js"
    )
    if storyboard_js.exists():
        h.update(storyboard_js.read_bytes())
    return h.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


# ── background render dispatch ───────────────────────────────────────────────


async def _default_render_beat(episode_id: str, beat_id: int) -> None:
    """Render a single beat via the dispatcher; update storyboard on completion.

    Module-level indirection so tests can monkeypatch _render_beat without
    touching the actual hyperframes subprocess.
    """
    from agents.brook.script_video.render_dispatcher import dispatch_beat

    ep_dir = _episode_dir(episode_id)
    storyboard = _load_storyboard(episode_id)
    beat = _find_beat(storyboard, beat_id)
    try:
        _mp4, cached_hash, _was_hit = await dispatch_beat(beat, ep_dir / "out")
        status = beat.setdefault("status", {})
        status["render_status"] = "done"
        status["cached_hash"] = cached_hash
    except Exception as exc:  # noqa: BLE001 — surface any worker failure
        logger.exception("render dispatch failed for beat %d: %s", beat_id, exc)
        beat.setdefault("status", {})["render_status"] = "failed"
    _save_storyboard(episode_id, storyboard)


# Indirection point used by all enqueue paths + monkeypatched in tests.
_render_beat = _default_render_beat


def _enqueue(background: BackgroundTasks, episode_id: str, beat_id: int) -> None:
    """Mark beat as rendering and schedule the background worker."""
    storyboard = _load_storyboard(episode_id)
    beat = _find_beat(storyboard, beat_id)
    beat.setdefault("status", {})["render_status"] = "rendering"
    _save_storyboard(episode_id, storyboard)
    background.add_task(_render_beat, episode_id, beat_id)


# ── auth helper ──────────────────────────────────────────────────────────────


def _auth_or_redirect(nakama_auth: str | None, episode_id: str) -> RedirectResponse | None:
    if check_auth(nakama_auth):
        return None
    return RedirectResponse(f"/login?next=/brook/video/{episode_id}", status_code=302)


# ── GET: storyboard page ─────────────────────────────────────────────────────


@page_router.get("/{episode_id}", response_class=HTMLResponse)
async def storyboard_page(
    request: Request,
    episode_id: str,
    nakama_auth: str | None = Cookie(None),
):
    redirect = _auth_or_redirect(nakama_auth, episode_id)
    if redirect:
        return redirect

    storyboard = _load_storyboard(episode_id)
    ep_dir = _episode_dir(episode_id)
    out_dir = ep_dir / "out"

    rows: list[dict[str, Any]] = []
    for beat in storyboard:
        status = beat.get("status") or {}
        bid = beat["beat_id"]
        # ADR-038 §D2: rendered mp4 is content-addressed via status.cached_hash.
        cached_hash = status.get("cached_hash")
        mp4_path = out_dir / f"b_roll_{cached_hash}.mp4" if cached_hash else None
        mp4_uri = mp4_path.resolve().as_uri() if mp4_path and mp4_path.exists() else None
        broll = beat.get("broll") or {}
        rows.append(
            {
                "beat_id": bid,
                "segment_text": (beat.get("start_quote") or "")[:80],
                "layout": beat.get("layout") or "—",
                "component": broll.get("component") if broll else "—",
                "params_json": json.dumps(broll.get("params") or {}, ensure_ascii=False),
                "text_approved": bool(status.get("text_approved")),
                "render_status": status.get("render_status") or "pending",
                "visual_approved": bool(status.get("visual_approved")),
                "mp4_uri": mp4_uri,
                "broll_decision": beat.get("broll_decision") or "none",
            }
        )

    edit_entries = edit_log.read_entries(episode_id)

    return _templates.TemplateResponse(
        request,
        "storyboard.html",
        {
            "episode_id": episode_id,
            "rows": rows,
            "edit_entries": edit_entries,
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


# ── GET: status poll ─────────────────────────────────────────────────────────


@page_router.get("/{episode_id}/status")
async def storyboard_status(
    episode_id: str,
    nakama_auth: str | None = Cookie(None),
) -> JSONResponse:
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    storyboard = _load_storyboard(episode_id)
    ep_dir = _episode_dir(episode_id)
    out_dir = ep_dir / "out"
    payload = []
    for beat in storyboard:
        bid = beat["beat_id"]
        status = beat.get("status") or {}
        cached_hash = status.get("cached_hash")
        mp4_path = out_dir / f"b_roll_{cached_hash}.mp4" if cached_hash else None
        mp4_uri = mp4_path.resolve().as_uri() if mp4_path and mp4_path.exists() else None
        payload.append(
            {
                "beat_id": bid,
                "text_approved": bool(status.get("text_approved")),
                "render_status": status.get("render_status") or "pending",
                "visual_approved": bool(status.get("visual_approved")),
                "mp4_uri": mp4_uri,
            }
        )
    return JSONResponse({"beats": payload})


# ── per-beat text-layer actions ──────────────────────────────────────────────


def _redirect_back(episode_id: str) -> RedirectResponse:
    return RedirectResponse(f"/brook/video/{episode_id}", status_code=303)


@page_router.post("/{episode_id}/beat/{beat_id}/approve")
async def beat_approve(
    episode_id: str,
    beat_id: int,
    background: BackgroundTasks,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    storyboard = _load_storyboard(episode_id)
    beat = _find_beat(storyboard, beat_id)
    beat.setdefault("status", {})["text_approved"] = True
    _save_storyboard(episode_id, storyboard)
    if beat.get("broll_decision") == "cutaway":
        _enqueue(background, episode_id, beat_id)
    return _redirect_back(episode_id)


@page_router.post("/{episode_id}/beat/{beat_id}/edit")
async def beat_edit(
    episode_id: str,
    beat_id: int,
    background: BackgroundTasks,
    layout: str = Form(...),
    component: str = Form(...),
    params: str = Form("{}"),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        params_obj = json.loads(params or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"params must be JSON: {exc}") from exc

    storyboard = _load_storyboard(episode_id)
    beat = _find_beat(storyboard, beat_id)
    beat["layout"] = layout
    if beat.get("broll_decision") == "cutaway":
        beat.setdefault(
            "broll",
            {
                "render_target": "hyperframes",
                "component": component,
                "params": params_obj,
                "transitions": {"in_transition": None, "out_transition": None},
            },
        )
        beat["broll"]["component"] = component
        beat["broll"]["params"] = params_obj
    beat.setdefault("status", {})["text_approved"] = True
    _save_storyboard(episode_id, storyboard)
    # edit-fields explicitly does NOT write edit_log (ADR-032 §9)
    if beat.get("broll_decision") == "cutaway":
        _enqueue(background, episode_id, beat_id)
    return _redirect_back(episode_id)


@page_router.post("/{episode_id}/beat/{beat_id}/replan")
async def beat_replan(
    episode_id: str,
    beat_id: int,
    background: BackgroundTasks,
    note: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    storyboard = _load_storyboard(episode_id)
    beat = _find_beat(storyboard, beat_id)
    before = {
        "layout": beat.get("layout"),
        "broll": beat.get("broll"),
    }
    storyboard_before = [dict(b) for b in storyboard]
    # ADR-038 §D3 + §D6: dispatch the LLM tool-call agent and apply the
    # returned BeatEdit list via the pure-functional engine. The agent is
    # bounded (max 5 iterations + token budget); if it errors or returns no
    # edits we still record the user note and reset render_status so the
    # operator can re-issue.
    from agents.brook.script_video import beat_editor, replan_agent

    edit_ops_payload: list[dict] = []
    try:
        result = replan_agent.run(storyboard, beat_id, note or "")
        if result.edits:
            storyboard = beat_editor.apply_edits(storyboard, result.edits)
            beat = _find_beat(storyboard, beat_id)
            edit_ops_payload = [e.model_dump() for e in result.edits]
    except Exception as exc:  # noqa: BLE001
        logger.exception("replan_agent failed for ep=%s beat=%d: %s", episode_id, beat_id, exc)
    beat.setdefault("status", {})["render_status"] = "pending"
    beat["status"]["text_approved"] = False
    beat.setdefault("user_notes", []).append(
        {
            "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "note": note or "(replan, no note)",
        }
    )
    _save_storyboard(episode_id, storyboard)
    edit_log.append_entry(
        episode_id=episode_id,
        beat_id=beat_id,
        action="replan",
        before=before,
        after={"layout": beat.get("layout"), "broll": beat.get("broll")},
        user_note=note or None,
        storyboard_before=storyboard_before,
        storyboard_after=[dict(b) for b in storyboard],
        edit_ops=edit_ops_payload,
    )
    return _redirect_back(episode_id)


# ── per-beat visual-layer actions (post-render) ──────────────────────────────


@page_router.post("/{episode_id}/beat/{beat_id}/visual/approve")
async def beat_visual_approve(
    episode_id: str,
    beat_id: int,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    storyboard = _load_storyboard(episode_id)
    beat = _find_beat(storyboard, beat_id)
    if (beat.get("status") or {}).get("render_status") != "done":
        raise HTTPException(status_code=400, detail="cannot visual-approve before render done")
    beat["status"]["visual_approved"] = True
    _save_storyboard(episode_id, storyboard)
    return _redirect_back(episode_id)


@page_router.post("/{episode_id}/beat/{beat_id}/visual/edit")
async def beat_visual_edit(
    episode_id: str,
    beat_id: int,
    background: BackgroundTasks,
    params: str = Form("{}"),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        params_obj = json.loads(params or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"params must be JSON: {exc}") from exc
    storyboard = _load_storyboard(episode_id)
    beat = _find_beat(storyboard, beat_id)
    if beat.get("broll_decision") != "cutaway":
        raise HTTPException(status_code=400, detail="visual edit only valid for cutaway beats")
    beat.setdefault("broll", {})["params"] = params_obj
    beat.setdefault("status", {})["visual_approved"] = False
    _save_storyboard(episode_id, storyboard)
    _enqueue(background, episode_id, beat_id)
    return _redirect_back(episode_id)


@page_router.post("/{episode_id}/beat/{beat_id}/visual/replan")
async def beat_visual_replan(
    episode_id: str,
    beat_id: int,
    background: BackgroundTasks,
    note: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    storyboard = _load_storyboard(episode_id)
    beat = _find_beat(storyboard, beat_id)
    before = {"layout": beat.get("layout"), "broll": beat.get("broll")}
    storyboard_before = [dict(b) for b in storyboard]
    from agents.brook.script_video import beat_editor, replan_agent

    edit_ops_payload: list[dict] = []
    try:
        result = replan_agent.run(storyboard, beat_id, note or "")
        if result.edits:
            storyboard = beat_editor.apply_edits(storyboard, result.edits)
            beat = _find_beat(storyboard, beat_id)
            edit_ops_payload = [e.model_dump() for e in result.edits]
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "replan_agent (visual) failed for ep=%s beat=%d: %s", episode_id, beat_id, exc
        )
    beat.setdefault("status", {})["render_status"] = "pending"
    beat["status"]["visual_approved"] = False
    beat.setdefault("user_notes", []).append(
        {
            "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "note": (note or "(visual replan, no note)"),
        }
    )
    _save_storyboard(episode_id, storyboard)
    edit_log.append_entry(
        episode_id=episode_id,
        beat_id=beat_id,
        action="replan-visual",
        before=before,
        after={"layout": beat.get("layout"), "broll": beat.get("broll")},
        user_note=note or None,
        storyboard_before=storyboard_before,
        storyboard_after=[dict(b) for b in storyboard],
        edit_ops=edit_ops_payload,
    )
    return _redirect_back(episode_id)


# ── batch actions (ADR §4 v2) ────────────────────────────────────────────────


@page_router.post("/{episode_id}/batch/approve-all-text")
async def batch_approve_all_text(
    episode_id: str,
    background: BackgroundTasks,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    storyboard = _load_storyboard(episode_id)
    enqueue_ids: list[int] = []
    for beat in storyboard:
        status = beat.setdefault("status", {})
        if not status.get("text_approved"):
            status["text_approved"] = True
            if beat.get("broll_decision") == "cutaway":
                status["render_status"] = "rendering"
                enqueue_ids.append(beat["beat_id"])
    _save_storyboard(episode_id, storyboard)
    for bid in enqueue_ids:
        background.add_task(_render_beat, episode_id, bid)
    return _redirect_back(episode_id)


@page_router.post("/{episode_id}/batch/render-approved")
async def batch_render_approved(
    episode_id: str,
    background: BackgroundTasks,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    storyboard = _load_storyboard(episode_id)
    enqueue_ids: list[int] = []
    for beat in storyboard:
        if beat.get("broll_decision") != "cutaway":
            continue
        status = beat.setdefault("status", {})
        if status.get("text_approved") and status.get("render_status") in {"pending", "failed"}:
            status["render_status"] = "rendering"
            enqueue_ids.append(beat["beat_id"])
    _save_storyboard(episode_id, storyboard)
    for bid in enqueue_ids:
        background.add_task(_render_beat, episode_id, bid)
    return _redirect_back(episode_id)


@page_router.post("/{episode_id}/batch/finalize-passing")
async def batch_finalize_passing(
    episode_id: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    storyboard = _load_storyboard(episode_id)
    for beat in storyboard:
        status = beat.setdefault("status", {})
        if status.get("render_status") == "done" and not status.get("visual_approved"):
            status["visual_approved"] = True
    _save_storyboard(episode_id, storyboard)
    return _redirect_back(episode_id)


# ── promote-to-example ───────────────────────────────────────────────────────


# ADR-050 遷移漏網（Codex panel 2026-07-05 抓出）：原指 agents/foundry/examples
# （死目錄），planner 讀的是 agents/brook/script_video/examples — promote 的
# example 永遠到不了 few-shot 5 檔門檻。
_EXAMPLES_DIR = (
    Path(__file__).resolve().parents[2] / "agents" / "brook" / "script_video" / "examples"
)


@page_router.post("/{episode_id}/edit-log/{entry_idx}/promote")
async def promote_to_example(
    episode_id: str,
    entry_idx: int,
    tag: str = Form(...),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    if "/" in tag or "\\" in tag or not tag.strip():
        raise HTTPException(status_code=400, detail="tag must be a non-empty plain slug")
    entries = edit_log.read_entries(episode_id)
    if entry_idx < 0 or entry_idx >= len(entries):
        raise HTTPException(status_code=404, detail="edit log entry not found")
    entry = entries[entry_idx]

    safe_tag = "".join(c for c in tag if c.isalnum() or c in "-_").lower()[:48]
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    example_name = f"{stamp}-{episode_id}-beat{entry['beat_id']}-{safe_tag}.yaml"
    _EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    example_path = _EXAMPLES_DIR / example_name
    example_path.write_text(
        yaml.dump(
            {
                "source": {
                    "episode_id": episode_id,
                    "beat_id": entry["beat_id"],
                    "promoted_at": stamp,
                    "tag": safe_tag,
                    "user_note": entry.get("user_note"),
                },
                "before": entry["before"],
                "after": entry["after"],
            },
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    index_path = _EXAMPLES_DIR / "_index.yaml"
    index = yaml.safe_load(index_path.read_text(encoding="utf-8")) if index_path.exists() else None
    if not isinstance(index, dict):
        index = {"examples": []}
    index.setdefault("examples", []).append({"file": example_name, "tag": safe_tag})
    index_path.write_text(
        yaml.dump(index, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    return _redirect_back(episode_id)
