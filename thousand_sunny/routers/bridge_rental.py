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
from urllib.parse import quote
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
    for name in (
        "tokens.css",
        "bridge.css",
        "bridge-pages.css",
        "bridge-rental.css",
        "bridge-rental.js",
        "theme.js",
    ):
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


def _dup_view(item: rw.BoardItem) -> dict:
    """同一間的其他刊登：只列跟代表那筆會不同的（來源、租金、捷運、登記）。"""
    it = item.listing
    facts = [f"{rw.SOURCE_LABELS.get(it.source, it.source)}", f"{it.price:,}元"]
    if it.mrt_distance is not None:
        facts.append(f"{it.mrt_station or '捷運'} {it.mrt_distance}m")
    if it.registration:
        facts.append(it.registration)
    return {"title": it.title or it.listing_id, "url": it.url, "facts": facts}


def _view(item: rw.BoardItem, dups: list[rw.BoardItem]) -> dict:
    it, a = item.listing, item.assessment
    vs = a.vs_median_pct
    # 代表那筆沒照片（或只有信義／好房網的一張封面）→ 用同一間照片最多的那筆。
    photos = max([it.photos, *(d.listing.photos for d in dups)], key=len)
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
        "dups": [_dup_view(d) for d in dups],
        "thumb": rw.photo_url(photos[0], "thumb") if photos else None,
        "gallery": [rw.photo_url(u, "large") for u in photos],
    }


def _group(board: rw.Board) -> dict[str, list[dict]]:
    ordered = sorted(board.items, key=lambda i: sort_key(i.listing, i.assessment))
    # 同一間、多家仲介刊登 → 只顯示排序最前的那筆，其他收在它底下；標記一次套用整群。
    members: dict[str, list[rw.BoardItem]] = {}
    for item in ordered:
        members.setdefault(item.group or item.listing.key, []).append(item)
    groups: dict[str, list[dict]] = {
        "interested": [],
        "urgent": [],
        TIER_MATCH: [],
        TIER_CHECK: [],
        TIER_OUT: [],
        "pass": [],
    }
    for item, *dups in members.values():
        v = _view(item, dups)
        # 舊物件換一家仲介再刊不算「新」；首次看到取整群最早的。
        unit = [item, *dups]
        v["is_new"] = all(i.is_new for i in unit)
        v["first_seen"] = _fmt_time(min(i.first_seen for i in unit))
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


_RETURN_TO_RE = re.compile(r"^(r|sec)-[A-Za-z0-9-]{1,80}$")


def _feedback_msg(
    board: Optional[rw.Board], saved: Optional[str], key: Optional[str]
) -> Optional[str]:
    """標記後的提示寫出是哪一筆——那一筆已經移到別的區塊，畫面上看不到它了。"""
    if saved not in ("1", "0") or not key or not board:
        return None
    item = next((i for i in board.items if i.listing.key == key), None)
    if item is None:
        return None
    title = item.listing.title or item.listing.listing_id
    n = len(_unit_keys(board, key)) - 1
    what = f"「{title}」" + (f"與同一間另 {n} 筆刊登" if n else "")
    if saved == "0":
        return f"已取消{what}的標記"
    label = rw.VERDICTS.get(item.verdict or "", "")
    return f"已把{what}標為{label}" if label else None


def _unit_keys(board: Optional[rw.Board], key: str) -> list[str]:
    """這筆與同一間其他刊登的 key（伺服器端重算，不信表單）。"""
    item = next((i for i in board.items if i.listing.key == key), None) if board else None
    if item is None or not item.group:
        return [key]
    return [i.listing.key for i in board.items if i.group == item.group]


def _render(
    request: Request,
    *,
    saved: Optional[str] = None,
    marked_key: Optional[str] = None,
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
            "saved_msg": _feedback_msg(board, saved, marked_key) or _SAVED_MSGS.get(saved or ""),
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
    k: str | None = None,
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/rental", status_code=302)
    return _render(request, saved=saved, marked_key=k)


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
    return_to: str = Form(""),
    js: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/rental", status_code=302)
    if verdict != "clear" and verdict not in rw.VERDICTS:
        return RedirectResponse("/bridge/rental", status_code=303)
    db_path = rw.default_db_path()
    keys = _unit_keys(rw.load_board(db_path, rw.load_criteria(db_path=db_path)), key)
    if verdict == "clear":
        rw.set_feedback(db_path, keys, None)
        saved = "0"
    else:
        rw.set_feedback(db_path, keys, verdict, reason)
        saved = "1"
    logger.info("rental feedback %s -> %s", keys, verdict)
    # 回到「補上這筆位置的那一筆」，不是這筆本身——它已經移到有興趣（頁首）或
    # 不考慮（頁底收合區），錨點指它會把畫面整個拉走（修修 2026-10-23）。
    # 有 JS（bridge-rental.js 送 js=1）時不加錨點：錨點捲動會晚於 JS 的精確對齊而蓋掉它。
    anchor = return_to if js != "1" and _RETURN_TO_RE.match(return_to) else ""
    url = f"/bridge/rental?saved={saved}&k={quote(key, safe='')}"
    return RedirectResponse(url + (f"#{anchor}" if anchor else ""), status_code=303)
