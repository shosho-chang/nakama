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
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Cookie, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.requests import Request

from shared.config import get_vault_path
from shared.log import get_logger
from shared.schemas.packaging import (
    ApprovalFileV1,
    ApprovalV1,
    PackagesFileV1,
    RenderRequestV1,
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


def _load_cutout_choices(episode_slug: str) -> dict[str, list[dict]]:
    """列出這集可選的 cutout（vault `Attachments/cutouts/podcast/<slug>/`）。

    只讀 `cutouts_manifest.json` 的 validated 清單——中間迭代檔（`_iterations/`、
    未定稿的版本）不該出現在挑選器裡（skill v2.2 教訓 15：cutout 資料夾是迭代
    歷史不是素材庫）。manifest 不在就退回列頂層 PNG，但標記 unvalidated。
    """
    d = get_vault_path() / "Attachments" / "cutouts" / "podcast" / episode_slug
    out: dict[str, list[dict]] = {"host": [], "guest": []}
    if not d.is_dir():
        return out
    manifest = d / "cutouts_manifest.json"
    names: list[str]
    validated = True
    if manifest.is_file():
        try:
            names = sorted(json.loads(manifest.read_text(encoding="utf-8")).get("validated", {}))
        except json.JSONDecodeError:
            names, validated = sorted(p.name for p in d.glob("*.png")), False
    else:
        names, validated = sorted(p.name for p in d.glob("*.png")), False
    for name in names:
        if not (d / name).is_file():
            continue
        role = "host" if name.startswith("host") else "guest" if name.startswith("guest") else None
        if role is None:
            continue
        parts = name.removesuffix(".png").split("_")
        out[role].append(
            {
                "file": name,
                "vault_path": f"Attachments/cutouts/podcast/{episode_slug}/{name}",
                "emotion": parts[-1] if len(parts) >= 3 else "",
                "validated": validated,
            }
        )
    return out


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
    return {
        "episode_slug": episode_slug,
        "pkg": pkg,
        "cuts": cuts,
        "cutouts": _load_cutout_choices(episode_slug),
    }


@page_router.get("", response_class=HTMLResponse)
async def packaging_list(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/packaging", status_code=302)
    return _templates.TemplateResponse(
        request,
        "packaging_list.html",
        {"rows": _scan_episodes(), "asset_version": _SHOSHO_ASSET_VERSION},
    )


@page_router.get("/{episode_slug}", response_class=HTMLResponse)
async def packaging_board(
    request: Request,
    episode_slug: str,
    edited: str | None = None,
    composed: str | None = None,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/packaging/{episode_slug}", status_code=302)
    ctx = _board_context(episode_slug)
    ctx["asset_version"] = _SHOSHO_ASSET_VERSION
    # 剛改完字的那支：改字區保持展開（見 packaging_edit_title 的 redirect 註解）
    ctx["edited_cut"] = edited
    # 剛存完配方那支：組封面區保持展開（同 edited 的理由 — <details> 會因重載收起）
    ctx["composed_cut"] = composed
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

    ep_dir = _packaging_root() / episode_slug
    existing = _load_approvals(ep_dir, pkg.episode)
    prev = next((a for a in existing.approvals if a.cut_id == cut_id), None)
    entry = ApprovalV1(
        cut_id=cut_id,
        approved=approved,
        primary_package=primary_package if approved else 1,
        reject_note=reject_note.strip() or None,
        decided_at=datetime.now(timezone.utc),
        decision=decision,
        # 挑臉／打大字是另一支 form 寫的，approve 不可以把它們洗掉
        # （2026-08-14 browser UAT 抓到：勾完變體再 approve，選擇整個不見）。
        selected_variant=prev.selected_variant if prev else None,
        bigtext_request=prev.bigtext_request if prev else None,
    )
    others = [a for a in existing.approvals if a.cut_id != cut_id]
    updated = ApprovalFileV1(episode=pkg.episode, approvals=[*others, entry])
    (ep_dir / "approval.json").write_text(
        updated.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    logger.info("packaging approve: %s/%s approved=%s", episode_slug, cut_id, approved)
    return RedirectResponse(f"/bridge/packaging/{episode_slug}", status_code=303)


@page_router.post("/{episode_slug}/variant")
async def packaging_select_variant(
    episode_slug: str,
    cut_id: str = Form(..., max_length=_EP_SLUG_MAX),
    selected_variant: str = Form("", max_length=_EP_SLUG_MAX),
    bigtext_request: str = Form("", max_length=_NOTE_MAX),
    nakama_auth: str | None = Cookie(None),
):
    """勾封面變體／打封面大字（修修 2026-08-14）— 只寫 approval.json，不動 packages.json。

    勾選是即時生效的（變體 PNG 桌機端已經 render 完，gate 純挑）；打字則是**請求**
    ——VPS 叫不到桌機的 render（ADR-054 D11），桌機端 thumbnail-brainstorm 讀到
    `bigtext_request` 後才重出一張新變體。UI 上要講清楚這個時間差，不要讓修修
    以為打完字圖就變了。

    不改 approved／primary_package／reject_note：挑臉跟拍板是兩件事，挑到一半
    不該被當成已核准。
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/packaging", status_code=302)

    ctx = _board_context(episode_slug)
    pkg: PackagesFileV1 = ctx["pkg"]
    cut = next((c for c in pkg.cuts if c.cut_id == cut_id), None)
    if cut is None:
        raise HTTPException(status_code=404, detail=f"cut not found: {cut_id}")

    variant = selected_variant.strip() or None
    if variant is not None:
        known = {v.variant_id for p in cut.packages for v in p.variants}
        if variant not in known:
            raise HTTPException(
                status_code=404,
                detail=f"{cut_id} 沒有 variant {variant!r}（已知：{sorted(known)}）",
            )

    ep_dir = _packaging_root() / episode_slug
    existing = _load_approvals(ep_dir, pkg.episode)
    prev = next((a for a in existing.approvals if a.cut_id == cut_id), None)
    entry = ApprovalV1(
        cut_id=cut_id,
        approved=prev.approved if prev else False,
        primary_package=prev.primary_package if prev else 1,
        reject_note=prev.reject_note if prev else None,
        decided_at=datetime.now(timezone.utc),
        # 挑變體不是裁決：沒按過 Approve/Reject 就維持 None，board 才不會把
        # 「挑到一半」顯示成 REJECTED（2026-08-14 browser UAT 抓到）。
        decision=prev.decision if prev else None,
        selected_variant=(
            variant if variant is not None else (prev.selected_variant if prev else None)
        ),
        bigtext_request=bigtext_request.strip() or None,
    )
    others = [a for a in existing.approvals if a.cut_id != cut_id]
    updated = ApprovalFileV1(episode=pkg.episode, approvals=[*others, entry])
    (ep_dir / "approval.json").write_text(
        updated.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "packaging variant: %s/%s variant=%s bigtext=%s",
        episode_slug,
        cut_id,
        entry.selected_variant,
        bool(entry.bigtext_request),
    )
    return RedirectResponse(f"/bridge/packaging/{episode_slug}#cut-{cut_id}", status_code=303)


@page_router.post("/{episode_slug}/compose")
async def packaging_compose(
    episode_slug: str,
    cut_id: str = Form(..., max_length=_EP_SLUG_MAX),
    title_rank: int = Form(..., ge=1, le=5),
    host_cutout: str = Form(..., max_length=_EP_SLUG_MAX * 4),
    guest_cutout: str = Form(..., max_length=_EP_SLUG_MAX * 4),
    big_text_1: str = Form("", max_length=40),
    big_text_2: str = Form("", max_length=40),
    big_text_3: str = Form("", max_length=40),
    highlight_text: str = Form("", max_length=40),
    nakama_auth: str | None = Cookie(None),
):
    """存「封面配方」：哪張臉、什麼大字、哪個詞加橘框（修修 2026-08-14 裁決）。

    **gate 不 render**（D11 不變）——這裡只寫配方，桌機端 thumbnail-brainstorm
    讀 `render_request` 後**出圖一次**。這比預先窮舉變體省：cutout × 大字的組合
    數是乘法，而他真正想要的那一組往往不在預先 render 的清單裡。
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/packaging", status_code=302)

    ctx = _board_context(episode_slug)
    pkg: PackagesFileV1 = ctx["pkg"]
    cut = next((c for c in pkg.cuts if c.cut_id == cut_id), None)
    if cut is None:
        raise HTTPException(status_code=404, detail=f"cut not found: {cut_id}")

    choices = ctx["cutouts"]
    known = {c["vault_path"] for c in choices["host"]} | {c["vault_path"] for c in choices["guest"]}
    for label, val in (("host_cutout", host_cutout), ("guest_cutout", guest_cutout)):
        if val not in known:
            raise HTTPException(status_code=404, detail=f"{label} 不在本集 cutout 清單：{val}")

    lines = [ln.strip() for ln in (big_text_1, big_text_2, big_text_3) if ln.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="封面大字不可為空")
    hl = highlight_text.strip()
    if hl and hl not in "".join(lines):
        raise HTTPException(
            status_code=400, detail=f"橘框詞「{hl}」不在大字裡，render 出來不會有框"
        )

    try:
        req = RenderRequestV1(
            title_rank=title_rank,
            host_cutout=host_cutout,
            guest_cutout=guest_cutout,
            big_text=lines,
            highlight_text=hl,
            requested_at=datetime.now(timezone.utc),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"配方驗證失敗：{str(exc)[:300]}") from exc

    ep_dir = _packaging_root() / episode_slug
    existing = _load_approvals(ep_dir, pkg.episode)
    prev = next((a for a in existing.approvals if a.cut_id == cut_id), None)
    entry = ApprovalV1(
        cut_id=cut_id,
        approved=prev.approved if prev else False,
        primary_package=prev.primary_package if prev else 1,
        reject_note=prev.reject_note if prev else None,
        decided_at=datetime.now(timezone.utc),
        decision=prev.decision if prev else None,
        selected_variant=prev.selected_variant if prev else None,
        bigtext_request=prev.bigtext_request if prev else None,
        render_request=req,
    )
    others = [a for a in existing.approvals if a.cut_id != cut_id]
    updated = ApprovalFileV1(episode=pkg.episode, approvals=[*others, entry])
    (ep_dir / "approval.json").write_text(
        updated.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "packaging compose: %s/%s rank=%s host=%s guest=%s text=%s",
        episode_slug,
        cut_id,
        title_rank,
        Path(host_cutout).name,
        Path(guest_cutout).name,
        lines,
    )
    return RedirectResponse(
        f"/bridge/packaging/{episode_slug}?composed={cut_id}#compose-{cut_id}", status_code=303
    )


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
            # setdefault 不夠：TitleV1 序列化後 original_text 這個 key 是存在的
            # （值 null），setdefault 看到 key 就不寫 → 推導鏈斷掉（2026-08-14
            # browser UAT 抓到：改完字 original_text 還是 null）。
            if not target.get("original_text"):
                target["original_text"] = target["text"]
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
