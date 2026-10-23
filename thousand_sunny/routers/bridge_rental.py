"""Bridge — 租屋追蹤（修修 2026-10-23：錄音室年底租約到期找新辦公室）。

``GET /bridge/rental`` 顯示 Franky ``rental-watch`` cron 抓到、目前在架的物件，
依 ``agents.franky.jobs.rental_assess`` 分層排序（建議儘快聯繫／符合／待確認／
不符合），每一筆附上判斷理由。``POST /bridge/rental/feedback`` 讓修修標「有興趣
／不考慮」＋原因，存進 ``rental_watch.db`` 的 ``feedback`` 表（State layer，
不碰 vault），之後用來調整排序權重。

資料由 cron 寫、頁面只讀（除了標記），所以頁面不會去打 591／信義／好房網。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agents.franky.jobs import rental_watch as rw
from agents.franky.jobs.rental_assess import TIER_CHECK, TIER_MATCH, TIER_OUT, sort_key
from shared.log import get_logger
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.bridge_rental")

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "bridge"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
_TAIPEI = ZoneInfo("Asia/Taipei")

page_router = APIRouter()


def _shosho_asset_version() -> str:
    """8-char hash of the CSS this page links — busts Cloudflare's /static/* cache."""
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for name in ("tokens.css", "bridge.css", "bridge-pages.css", "bridge-rental.css", "theme.js"):
        path = static_dir / name
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


def _anchor(key: str) -> str:
    return "r-" + re.sub(r"[^A-Za-z0-9]+", "-", key)


def _fmt_time(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    return datetime.fromisoformat(iso).astimezone(_TAIPEI).strftime("%m/%d %H:%M")


def _same_unit_hints(items: list[rw.BoardItem]) -> dict[str, list[str]]:
    """不同網站刊同一間：同區、同租金、坪數差 ≤ 0.5 → 互相標「可能同一間」。

    只是提示（各站坪數四捨五入不同，例如 591 寫 32 坪、信義寫 31.62 坪），不合併資料。
    """
    hints: dict[str, list[str]] = {}
    for a in items:
        for b in items:
            la, lb = a.listing, b.listing
            if la.source == lb.source or la.district != lb.district or la.price != lb.price:
                continue
            if la.area is None or lb.area is None or abs(la.area - lb.area) > 0.5:
                continue
            hints.setdefault(la.key, []).append(rw.SOURCE_LABELS.get(lb.source, lb.source))
    return {k: sorted(set(v)) for k, v in hints.items()}


def _view(item: rw.BoardItem, hints: dict[str, list[str]]) -> dict:
    it, a = item.listing, item.assessment
    vs = a.vs_median_pct
    return {
        "key": it.key,
        "anchor": _anchor(it.key),
        "title": it.title or it.listing_id,
        "url": it.url,
        "facts": rw.listing_facts(it),
        "price": it.price,
        "prev_price": item.prev_price if item.prev_price and item.prev_price > it.price else None,
        "ppp": a.price_per_ping,
        "vs_pct": vs,
        "chips": a.chips,
        "tier": a.tier,
        "urgent": a.urgent,
        "is_new": item.is_new,
        "is_basement": it.is_basement,
        "verdict": item.verdict,
        "reason": item.reason,
        "first_seen": _fmt_time(item.first_seen),
        "same_unit": hints.get(it.key, []),
    }


def _group(board: rw.Board) -> dict[str, list[dict]]:
    hints = _same_unit_hints(board.items)
    ordered = sorted(board.items, key=lambda i: sort_key(i.listing, i.assessment))
    groups: dict[str, list[dict]] = {
        "interested": [],
        "urgent": [],
        TIER_MATCH: [],
        TIER_CHECK: [],
        TIER_OUT: [],
        "pass": [],
    }
    for item in ordered:
        v = _view(item, hints)
        if item.verdict in ("interested", "pass"):
            groups[item.verdict].append(v)
        elif item.assessment.urgent:
            groups["urgent"].append(v)
        else:
            groups[item.assessment.tier].append(v)
    return groups


_SAVED_MSGS = {
    "1": "已標記",
    "0": "已取消標記",
    "criteria": "條件已儲存：頁面已照新條件篩選；放寬條件的新物件要等下一輪掃描（每小時 :05）",
    "criteria-reset": "已恢復設定檔的預設條件，下一輪掃描套用",
}


def _form_values(criteria: rw.Criteria) -> dict:
    """表單預填值：目前生效的條件。"""
    return {
        "districts": list(criteria.districts),
        "bonus_districts": list(criteria.bonus_districts),
        "price_min": criteria.price_min,
        "price_max": criteria.price_max,
        "area_min": f"{criteria.area_min:g}",
        "area_max": f"{criteria.area_max:g}",
        "mrt_max_m": criteria.mrt_max_m,
        "exclude_basement": criteria.exclude_basement,
    }


def _render(
    request: Request,
    *,
    saved: Optional[str] = None,
    criteria_errors: Optional[list[str]] = None,
    submitted: Optional[dict] = None,
    status_code: int = 200,
):
    db_path = rw.default_db_path()
    criteria = rw.load_criteria(db_path=db_path)
    board = rw.load_board(db_path, criteria)
    groups = _group(board) if board else None
    return _templates.TemplateResponse(
        request,
        "rental.html",
        {
            "asset_version": _SHOSHO_ASSET_VERSION,
            "criteria": criteria,
            "board": board,
            "groups": groups,
            "total": len(board.items) if board else 0,
            "last_scan": _fmt_time(board.last_scan_at) if board else "—",
            "sources": board.sources if board else [],
            "saved_msg": _SAVED_MSGS.get(saved or ""),
            "taipei_districts": rw.TAIPEI_DISTRICTS,
            "form": submitted or _form_values(criteria),
            "criteria_errors": criteria_errors or [],
            "override_active": rw.get_criteria_override(db_path) is not None,
        },
        status_code=status_code,
    )


@page_router.get("/bridge/rental", response_class=HTMLResponse)
async def rental_page(
    request: Request,
    nakama_auth: str | None = Cookie(None),
    saved: str | None = None,
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/rental", status_code=302)
    return _render(request, saved=saved)


@page_router.post("/bridge/rental/criteria", response_class=HTMLResponse)
async def rental_criteria(request: Request, nakama_auth: str | None = Cookie(None)):
    """修修在面板改搜尋條件（存 rental_watch.db，蓋過 config/rental-watch.yaml）。"""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/rental", status_code=302)
    form = await request.form()
    if form.get("action") == "reset":
        rw.save_criteria_override(rw.default_db_path(), None)
        logger.info("rental criteria reset to config defaults")
        return RedirectResponse("/bridge/rental?saved=criteria-reset", status_code=303)

    data = {
        "districts": form.getlist("districts"),
        "bonus_districts": form.getlist("bonus_districts"),
        **{
            k: form.get(k, "")
            for k in ("price_min", "price_max", "area_min", "area_max", "mrt_max_m")
        },
        "exclude_basement": form.get("exclude_basement") == "1",
    }
    override, errors = rw.parse_criteria_form(data)
    if errors:
        return _render(request, criteria_errors=errors, submitted=data, status_code=422)
    rw.save_criteria_override(rw.default_db_path(), override)
    logger.info("rental criteria saved: %s", override)
    return RedirectResponse("/bridge/rental?saved=criteria", status_code=303)


@page_router.post("/bridge/rental/feedback")
async def rental_feedback(
    key: str = Form(...),
    verdict: str = Form(...),
    reason: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/rental", status_code=302)
    if verdict == "clear":
        rw.set_feedback(rw.default_db_path(), key, None)
        saved = "0"
    elif verdict in rw.VERDICTS:
        rw.set_feedback(rw.default_db_path(), key, verdict, reason)
        saved = "1"
    else:
        return RedirectResponse("/bridge/rental", status_code=303)
    logger.info("rental feedback %s -> %s", key, verdict)
    return RedirectResponse(f"/bridge/rental?saved={saved}#{_anchor(key)}", status_code=303)
