"""Packaging gate — `/bridge/packaging` (ADR-054 D10/D11, issue #1071).

穩態唯一人工介入點：修修看每支長片的 3 個 package（標題×封面綁定）後 approve。
資料面 SoT = vault `Attachments/packaging/<episode_slug>/`（Syncthing 同步）：

- packages.json  — packaging skill 寫（titles + packages），本 router 只讀
- approval.json  — 本 router 唯一寫入者（ApprovalFileV1；與 packages.json 分檔
  縮小 Syncthing conflict 破壞面 — ADR-054 A10③）

**UI 零 LLM**（D11）：無 brainstorm / reroll 按鈕；重抽路徑 = 回 Cowork 跑
thumbnail-brainstorm。短片標題可改字（LLM 直出僅是初稿 — D4），寫回 packages.json
是本 router 對該檔唯一的寫入例外，且整檔過 PackagesFileV1 驗證後才落盤。

Syncthing conflict policy（docs/VAULT-LAYOUT.md）：目錄內出現 `*.sync-conflict-*`
即 fail loud — 列表頁該集標錯、board 拒開，不靜默讀任一版本。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.requests import Request

from shared.config import get_vault_path
from shared.log import get_logger
from shared.release_store import (
    ensure_target,
    get_release,
    register_release,
    update_target,
)
from shared.schemas.packaging import (
    ApprovalFileV1,
    ApprovalV1,
    PackagesFileV1,
    parse_approval_file,
    parse_packages,
)
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.packaging")

page_router = APIRouter(prefix="/bridge/packaging", tags=["bridge-packaging"])
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)

_EP_SLUG_MAX = 80
_TITLE_MAX = 200
_NOTE_MAX = 2000


def _shosho_asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for name in ("tokens.css", "bridge.css", "bridge-pages.css"):
        p = static_dir / name
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


def _packaging_root() -> Path:
    return get_vault_path() / "Attachments" / "packaging"


def _sync_conflicts(ep_dir: Path) -> list[str]:
    return sorted(p.name for p in ep_dir.glob("*sync-conflict*"))


def _load_brief(ep_dir: Path, cut_id: str) -> dict | None:
    """讀該支的內容速覽（`briefs/<cut_id>.json`），沒有就回 None。

    修修 2026-07-30：「審 util-L4 的時候我不太清楚這支影片在講什麼，所以我也
    沒辦法判斷。」board 原本只給封面＋標題文字，零內容脈絡 → 判斷不了是必然的。

    **UI 零 LLM 不變**（ADR-054 D11）：速覽由 Cowork 端 `packaging_brief.py`
    從該支 SRT 生成後落檔，本 router 只讀。壞檔不擋 board——速覽是輔助資訊，
    不該讓它的問題阻擋裁決；改為在該區塊顯示錯誤。
    """
    path = ep_dir / "briefs" / f"{cut_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"error": f"brief 壞檔：{exc}"}
    if not isinstance(data, dict):
        return {"error": "brief 格式錯誤：頂層必須是物件"}
    return data


def _load_approvals(ep_dir: Path, episode: str) -> ApprovalFileV1:
    path = ep_dir / "approval.json"
    if not path.is_file():
        return ApprovalFileV1(episode=episode, approvals=[])
    return parse_approval_file(path)


def _scan_episodes() -> list[dict]:
    """列表頁資料：每個 episode 目錄一列；壞檔/conflict 標 error，不吞。"""
    root = _packaging_root()
    rows: list[dict] = []
    if not root.is_dir():
        return rows
    for ep_dir in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        row: dict = {
            "slug": ep_dir.name,
            "episode": ep_dir.name,
            "error": None,
            "cuts_total": 0,
            "long_total": 0,
            "approved": 0,
        }
        conflicts = _sync_conflicts(ep_dir)
        if conflicts:
            row["error"] = f"Syncthing conflict：{', '.join(conflicts)} — 先解 conflict 再進 gate"
            rows.append(row)
            continue
        pkg_path = ep_dir / "packages.json"
        if not pkg_path.is_file():
            row["error"] = "缺 packages.json"
            rows.append(row)
            continue
        try:
            pkg = parse_packages(pkg_path)
            approvals = _load_approvals(ep_dir, pkg.episode)
        except (ValidationError, json.JSONDecodeError) as exc:
            row["error"] = f"檔案驗證失敗：{type(exc).__name__} — {str(exc)[:200]}"
            rows.append(row)
            continue
        row["episode"] = pkg.episode
        row["cuts_total"] = len(pkg.cuts)
        row["long_total"] = sum(1 for c in pkg.cuts if c.format == "long")
        row["approved"] = sum(1 for a in approvals.approvals if a.approved)
        rows.append(row)
    return rows


def _board_context(episode_slug: str) -> dict:
    """Board 頁資料。壞檔 → HTTPException（fail loud，不渲染半套 board）。"""
    ep_dir = _packaging_root() / episode_slug
    if not ep_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"packaging episode not found: {episode_slug}")
    conflicts = _sync_conflicts(ep_dir)
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail=f"Syncthing conflict in {episode_slug}: {', '.join(conflicts)} — "
            "先在 vault 解掉 *.sync-conflict-* 檔再進 gate（VAULT-LAYOUT conflict policy）",
        )
    try:
        pkg = parse_packages(ep_dir / "packages.json")
        approvals = _load_approvals(ep_dir, pkg.episode)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"packages.json missing: {exc}") from exc
    except (ValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=422, detail=f"packages/approval 驗證失敗：{str(exc)[:500]}"
        ) from exc

    approval_by_cut = {a.cut_id: a for a in approvals.approvals}
    cuts = []
    for cut in pkg.cuts:
        titles_by_rank = {t.rank: t for t in cut.titles}
        view = {
            "cut": cut,
            "approval": approval_by_cut.get(cut.cut_id),
            "packages": [
                {"pkg": p, "title": titles_by_rank.get(p.title_rank)} for p in cut.packages
            ],
            "runners_up": [t for t in cut.titles if t.rank >= 4],
            "brief": _load_brief(ep_dir, cut.cut_id),
        }
        cuts.append(view)
    return {"episode_slug": episode_slug, "pkg": pkg, "cuts": cuts}


def _publish_url(episode: str, cut_id: str) -> str:
    return f"/bridge/publish/{quote(episode, safe='')}/{quote(cut_id, safe='')}"


def _ensure_publish_prep(episode: str, cut_id: str) -> None:
    """Start or resume the full-resolution export for an approved package.

    Finished Review normally starts this worker early.  Packaging remains the
    terminal gate, though, so it must recover when that earlier worker exited
    (for example because its Resolve Python runtime crashed) instead of polling
    a stale ``rendering`` receipt forever.
    """
    root_value = os.environ.get("PODCAST_EPISODES_ROOT", "").strip()
    if not root_value:
        raise HTTPException(status_code=503, detail="PODCAST_EPISODES_ROOT 未設定")
    root = Path(root_value).resolve()
    episode_dir = (root / episode).resolve()
    try:
        episode_dir.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=403, detail="episode 路徑超出 PODCAST_EPISODES_ROOT"
        ) from exc
    if not episode_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"episode 不存在: {episode}")

    # Local import avoids coupling router import order; highlight_review owns
    # the process registry and deduplicates an already-running export.
    from thousand_sunny.routers.highlight_review import _start_publish_prep

    _start_publish_prep(episode_dir, cut_id)


def _release_from_receipt(episode: str, cut_id: str) -> dict | None:
    """Register a completed Resolve render using the Web App's Python runtime.

    Resolve's scripting DLL and the Web dependencies can require different
    Python builds on Windows.  The render worker therefore writes a small
    receipt; this side validates it and performs the DB transition.
    """
    release = get_release(episode, cut_id)
    if release is not None:
        return release
    root_value = os.environ.get("PODCAST_EPISODES_ROOT", "").strip()
    if not root_value:
        return None
    root = Path(root_value).resolve()
    episode_dir = (root / episode).resolve()
    try:
        episode_dir.relative_to(root)
    except ValueError:
        return None
    receipt = episode_dir / "highlights" / "exports" / f".publish_prep_{cut_id}.json"
    if not receipt.is_file():
        return None
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("status") != "rendered" or payload.get("episode") != episode:
        return None
    rows = [row for row in payload.get("cuts", []) if row.get("cut_id") == cut_id]
    if len(rows) != 1:
        raise HTTPException(status_code=409, detail="publish_prep receipt 缺少唯一 cut")
    row = rows[0]
    file_path = Path(str(row.get("file", ""))).resolve()
    exports_dir = (episode_dir / "highlights" / "exports").resolve()
    try:
        file_path.relative_to(exports_dir)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="publish_prep receipt 成品路徑越界") from exc
    if not file_path.is_file() or file_path.stat().st_size != int(row.get("file_bytes", -1)):
        raise HTTPException(status_code=409, detail="publish_prep receipt 與成品檔不一致")
    release_id = register_release(
        episode,
        cut_id,
        str(row.get("format", "")),
        str(file_path),
        work_title=str(row.get("work_title", "")),
        file_bytes=file_path.stat().st_size,
        duration_sec=float(row.get("duration_sec", 0)),
    )
    ensure_target(release_id, "youtube")
    logger.info("publish_prep receipt registered: %s/%s", episode, cut_id)
    return get_release(episode, cut_id)


def _apply_packaging_to_release(
    pkg: PackagesFileV1,
    cut_id: str,
    primary_package: int,
    release: dict,
) -> None:
    cut = next((row for row in pkg.cuts if row.cut_id == cut_id), None)
    if cut is None:
        raise HTTPException(status_code=404, detail=f"cut not found: {cut_id}")
    target = next((row for row in release["targets"] if row["platform"] == "youtube"), None)
    if target is None:
        raise HTTPException(status_code=409, detail="youtube release target 不存在")
    if target["status"] in {"uploading", "uploaded", "published"}:
        raise HTTPException(status_code=409, detail="影片已進入上傳流程，不能更換 Packaging")
    title_by_rank = {row.rank: row.text for row in cut.titles}
    if primary_package not in title_by_rank:
        raise HTTPException(status_code=409, detail="primary package 的標題不存在")
    fields = {"title": title_by_rank[primary_package]}
    if cut.format == "long":
        selected_package = next(
            (row for row in cut.packages if row.title_rank == primary_package), None
        )
        if selected_package is None:
            raise HTTPException(status_code=409, detail="primary package 不存在")
        fields["thumbnail_path"] = selected_package.thumbnail_png
    update_target(target["id"], **fields)


@page_router.get("", response_class=HTMLResponse)
async def packaging_list(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/packaging", status_code=302)
    return _templates.TemplateResponse(
        request,
        "packaging_list.html",
        {"rows": _scan_episodes(), "asset_version": _SHOSHO_ASSET_VERSION},
    )


@page_router.get("/{episode_slug}/thumbnail/{filename}")
async def packaging_thumbnail(
    episode_slug: str,
    filename: str,
    nakama_auth: str | None = Cookie(None),
) -> FileResponse:
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    if filename != Path(filename).name or not filename.lower().endswith(".png"):
        raise HTTPException(status_code=403, detail="僅限 Packaging PNG")
    ctx = _board_context(episode_slug)
    refs = {
        package.thumbnail_png
        for cut in ctx["pkg"].cuts
        for package in cut.packages
        if Path(package.thumbnail_png).name == filename
    }
    if len(refs) != 1:
        raise HTTPException(status_code=404, detail="Packaging 縮圖不存在或不唯一")
    path = get_vault_path() / refs.pop()
    try:
        path.resolve().relative_to((_packaging_root() / episode_slug).resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Packaging 縮圖超出 episode 目錄") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Packaging 縮圖檔不存在")
    return FileResponse(path, media_type="image/png")


@page_router.get("/{episode_slug}", response_class=HTMLResponse)
async def packaging_board(
    request: Request,
    episode_slug: str,
    edited: str | None = None,
    cut: str | None = None,
    release_pending: str | None = None,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/packaging/{episode_slug}", status_code=302)
    ctx = _board_context(episode_slug)
    if cut:
        focused = [view for view in ctx["cuts"] if view["cut"].cut_id == cut]
        if not focused:
            raise HTTPException(status_code=404, detail=f"cut not found: {cut}")
        ctx["cuts"] = focused
    if release_pending and cut:
        release = _release_from_receipt(ctx["pkg"].episode, cut)
        if release is not None:
            approval = ctx["cuts"][0]["approval"]
            if approval is None or not approval.approved:
                raise HTTPException(status_code=409, detail="Packaging 尚未核准")
            _apply_packaging_to_release(
                ctx["pkg"], cut, approval.primary_package, release
            )
            return RedirectResponse(_publish_url(ctx["pkg"].episode, cut), status_code=303)
    ctx["asset_version"] = _SHOSHO_ASSET_VERSION
    # 剛改完字的那支：改字區保持展開（見 packaging_edit_title 的 redirect 註解）
    ctx["edited_cut"] = edited
    ctx["focused_cut"] = cut
    ctx["release_pending"] = bool(release_pending)
    return _templates.TemplateResponse(request, "packaging_board.html", ctx)


@page_router.post("/{episode_slug}/approve")
async def packaging_approve(
    episode_slug: str,
    cut_id: str = Form(..., max_length=_EP_SLUG_MAX),
    decision: str = Form(...),
    primary_package: int | None = Form(None),
    reject_note: str = Form("", max_length=_NOTE_MAX),
    nakama_auth: str | None = Cookie(None),
):
    """寫 approval.json（upsert 該 cut 的裁決）— 本 router 唯一寫這個檔。"""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/packaging", status_code=302)
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail=f"unknown decision: {decision!r}")
    approved = decision == "approve"
    if approved and primary_package is None:
        raise HTTPException(status_code=400, detail="approve 需要指定 primary_package (1–3)")

    ctx = _board_context(episode_slug)  # 同時擋 conflict / 壞檔
    pkg: PackagesFileV1 = ctx["pkg"]
    cut = next((c for c in pkg.cuts if c.cut_id == cut_id), None)
    if cut is None:
        raise HTTPException(status_code=404, detail=f"cut not found: {cut_id}")
    if approved and cut.format == "long" and not any(p.title_rank for p in cut.packages):
        raise HTTPException(status_code=409, detail="長片尚無 package，先跑 thumbnail-brainstorm")

    entry = ApprovalV1(
        cut_id=cut_id,
        approved=approved,
        primary_package=primary_package if approved else 1,
        reject_note=reject_note.strip() or None,
        decided_at=datetime.now(timezone.utc),
    )
    ep_dir = _packaging_root() / episode_slug
    existing = _load_approvals(ep_dir, pkg.episode)
    others = [a for a in existing.approvals if a.cut_id != cut_id]
    updated = ApprovalFileV1(episode=pkg.episode, approvals=[*others, entry])
    (ep_dir / "approval.json").write_text(
        updated.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    logger.info("packaging approve: %s/%s approved=%s", episode_slug, cut_id, approved)
    focused_board = f"/bridge/packaging/{quote(episode_slug, safe='')}?cut={quote(cut_id, safe='')}"
    if not approved:
        return RedirectResponse(focused_board, status_code=303)

    release = _release_from_receipt(pkg.episode, cut_id)
    if release is None:
        _ensure_publish_prep(pkg.episode, cut_id)
        return RedirectResponse(f"{focused_board}&release_pending=1", status_code=303)
    _apply_packaging_to_release(pkg, cut_id, primary_package or 1, release)
    logger.info("packaging -> publish handoff: %s/%s", pkg.episode, cut_id)
    return RedirectResponse(_publish_url(pkg.episode, cut_id), status_code=303)


@page_router.post("/{episode_slug}/title")
async def packaging_edit_title(
    episode_slug: str,
    cut_id: str = Form(..., max_length=_EP_SLUG_MAX),
    title_text: str = Form(..., max_length=_TITLE_MAX),
    rank: int = Form(1, ge=1, le=5),
    nakama_auth: str | None = Cookie(None),
):
    """標題手改字 — **長短片皆可**（修修 2026-07-30 裁決）。整檔重驗後才落盤。

    ADR-054 D11 的「UI 零 LLM」硬約束原文是「VPS FastAPI 呼叫不到桌機 Cowork」，
    禁的是 LLM **生成**（所以沒有重抽按鈕），不禁人工編輯。舊版把長片擋掉是實作
    時自己加的限制，ADR 無此決定；修修是品味的最終裁決者
    （feedback_hitl_gate_serves_subjective_taste），為一個用字繞回 Cowork 是純
    摩擦（feedback_minimize_manual_friction）。

    首次改字把原文存進 `original_text` 並蓋 `edited_at`——archetype_id /
    angle_combo / cite / payoff 描述的是**原句**，沒有這欄推導鏈就會謊稱手改後
    的文字是 panel 跑出來的。重複改字保留最初的 original_text。
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/packaging", status_code=302)
    text = title_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="標題不可為空")

    ctx = _board_context(episode_slug)
    pkg: PackagesFileV1 = ctx["pkg"]
    cut = next((c for c in pkg.cuts if c.cut_id == cut_id), None)
    if cut is None:
        raise HTTPException(status_code=404, detail=f"cut not found: {cut_id}")
    if cut.format == "short":
        rank = 1  # 短片只有一條標題，忽略傳入的 rank

    ep_dir = _packaging_root() / episode_slug
    path = ep_dir / "packages.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    edited = False
    for raw_cut in data["cuts"]:
        if raw_cut["cut_id"] != cut_id:
            continue
        target = next((t for t in raw_cut["titles"] if t.get("rank") == rank), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"{cut_id} 沒有 rank {rank} 的標題")
        if target["text"] != text:
            target.setdefault("original_text", target["text"])
            target["edited_at"] = datetime.now(timezone.utc).isoformat()
            target["text"] = text
            edited = True
        break

    if edited:
        PackagesFileV1.model_validate(data)  # 驗證失敗即不落盤
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info("packaging title edit: %s/%s rank=%s", episode_slug, cut_id, rank)
    # 帶回 `?edited=<cut_id>`：改完一條後 <details> 會因為重載而收起，要再點一次
    # 才能改下一條（2026-07-30 browser UAT 抓到）。template 靠這個把該支的改字區
    # 保持展開並 scroll 回來。
    return RedirectResponse(
        f"/bridge/packaging/{episode_slug}?edited={cut_id}#title-edit-{cut_id}",
        status_code=303,
    )
