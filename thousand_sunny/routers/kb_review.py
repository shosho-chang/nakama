"""Centaur 每日回顧 Web UI + 開卡 endpoint（N523）.

Centaur Zettelkasten 規格 v0.2 §5（每日回顧）§6（Web UI 架構）§8（Phase 5 善後）。
Thousand Sunny 上 pilot 的核心人機介面：把 N522 ``DailyReviewBundle`` 端到使用者
面前，提供「開卡 / 略過 / 之後再說」三個動作，並在開卡存檔後跑 Phase 5 善後。

路由（全部掛在 ``/kb`` 前綴下）：
  - ``GET  /kb/review``        — 每日回顧頁（消費 N522 bundle，照 prototype v2 三段）
  - ``POST /kb/api/permanent`` — **全系統唯一 Permanent body 寫入口**（human-authoring；
       寫入帶 ``author: human``，組裝 v0.2 §3 frontmatter + 正文 + typed edges inline）
  - ``POST /kb/api/review/skip``  — 略過候選（寫 N522 讀的 state 檔，永不再現）
  - ``POST /kb/api/review/later`` — 之後再說（state 檔記今日，14 天過期由 N522 job 歸檔）

紅線對齊（v0.2 §7 紅線 1）：``update_permanent_bookkeeping`` 只記帳不寫正文，
``assert_not_permanent_target`` 擋 agent/promotion 路徑。本 human endpoint 是規格
明訂的合法 Permanent 正文寫入口，直接建檔（帶 ``author: human``），不走 bookkeeping、
不觸 tripwire。

CSP 紀律：``/kb*`` 比照 Reader（``script-src 'self'`` 慣例），**禁 inline ``<script>``
/ ``onclick`` / ``onerror``**，所有 JS 走 ``/static/kb_review.js``。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from shared.config import get_vault_path
from shared.log import get_logger
from shared.permanent_layer import PERMANENT_DIR
from shared.schemas.daily_review import DailyReviewBundle
from shared.utils import extract_frontmatter, slugify
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.kb_review")

router = APIRouter(prefix="/kb")

_TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=[str(_TEMPLATE_ROOT / "kb")])

# typed-edge 中文 label（v0.2 §3 inline field）。
_EDGE_LABELS: dict[str, str] = {"support": "支持", "refute": "反駁", "extend": "延伸"}
_VALID_EDGE_TYPES = frozenset(_EDGE_LABELS)


def _shosho_asset_version() -> str:
    """8-char sha1 of this surface's design-system assets — busts CF edge cache.

    Hash tokens.css + theme.js + kb_review.css + kb_review.js so any asset edit
    forces a fresh ``?v=<slug>`` on service restart（design-system.md §Asset versioning）。
    """
    static_dir = Path(__file__).resolve().parent.parent / "static"
    h = hashlib.sha1()
    for rel in (
        "shosho/tokens.css",
        "shosho/theme.js",
        "kb_review.css",
        "kb_review.js",
    ):
        p = static_dir / rel
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


# ---------------------------------------------------------------------------
# Daily-review bundle seam (UI 資料源)
# ---------------------------------------------------------------------------
#
# N522 job 算完 bundle 後 ping Nami，不落地 JSON。本 UI 在開頁時即時 compute
# 一份 bundle（``notify=False``，不重複打擾）。獨立成函式讓測試 monkeypatch，
# 不打 LLM。


def _compute_bundle(*, weekly: bool = False) -> DailyReviewBundle:
    """產生今天的 :class:`DailyReviewBundle`（即時 compute，不發 Nami 通知）。"""
    from agents.robin.daily_review import run_daily_review

    return run_daily_review(weekly=weekly, notify=False)


# ---------------------------------------------------------------------------
# State 檔回寫（skip / later）——對齊 N522 ``load_review_state`` / ``save_review_state``
# ---------------------------------------------------------------------------


def _update_review_state(candidate_id: str, action: str) -> None:
    """把 skip / later 動作寫進 ``KB/.centaur/daily_review_state.json``。

    格式對齊 N522 ``load_review_state`` 讀取假設：
        {"skipped": [id, ...], "deferred": {id: "YYYY-MM-DD", ...}}
    N522 只讀+過期，寫入端是本 endpoint。
    """
    from agents.robin.daily_review import load_review_state, save_review_state

    vault = get_vault_path()
    state = load_review_state(vault)
    skipped: list[str] = list(state.get("skipped") or [])
    deferred: dict[str, str] = dict(state.get("deferred") or {})

    if action == "skip":
        if candidate_id not in skipped:
            skipped.append(candidate_id)
        # 略過優先於之後再說——若曾 defer 過，移出 deferred 佇列。
        deferred.pop(candidate_id, None)
    elif action == "later":
        deferred[candidate_id] = date.today().isoformat()
    else:  # pragma: no cover - guarded by caller
        raise ValueError(f"unknown review action: {action!r}")

    save_review_state(vault, {"skipped": skipped, "deferred": deferred})


# ---------------------------------------------------------------------------
# POST /kb/api/permanent — human-authoring 寫入口（紅線唯一合法 Permanent body 寫入）
# ---------------------------------------------------------------------------


class TypedEdgeIn(BaseModel):
    """開卡 drawer 收回的一條 typed edge（人採用 chip / 搜尋後加上理由）。"""

    edge_type: str  # support | refute | extend
    target: str  # 目標卡標題或 KB path（[[...]] 內文字）
    reason: str = ""  # 理由（人的判斷；可空——但 placeholder 引導必填）


class SourceRefIn(BaseModel):
    """一條溯源 source_ref（drawer 預填、原樣回傳）。"""

    literature_path: str = ""  # KB/Literature/{slug}
    anchor: str = ""  # ^cfi-… / ^p-N / t=…
    raw: str = ""  # 自由文字 ref（fleeting 等無 literature_path 時用）


class CreatePermanentIn(BaseModel):
    """``POST /kb/api/permanent`` 請求體。"""

    title: str  # 一句宣告句（= 檔名 = 卡 ID）
    body: str  # 正文（人寫；空 → 422）
    edges: list[TypedEdgeIn] = Field(default_factory=list)
    source_refs: list[SourceRefIn] = Field(default_factory=list)

    # Phase 5 善後 hook 的可選 context（drawer 帶上）：
    candidate_id: str = ""  # 開卡來源候選 id（skip/later 對齊；fleeting 為空）
    literature_slug: str = ""  # 回填 mined_concepts 的 Literature 檔 stem
    fleeting_path: str = ""  # 開自 fleeting 時的原檔 KB path（善後翻 status + 回收桶）


def _edge_target_link(target: str) -> str:
    """把使用者給的 target 正規化成 ``[[...]]`` 內文字（取 path stem，不含副檔名）。"""
    t = target.strip()
    if not t:
        return ""
    # 容忍使用者貼進完整 KB path（KB/Permanent/xxx）或 [[xxx]]——一律取葉節點。
    t = t.replace("[[", "").replace("]]", "")
    if "/" in t:
        t = t.rsplit("/", 1)[-1]
    if t.endswith(".md"):
        t = t[: -len(".md")]
    return t.strip()


def _assemble_permanent_markdown(payload: CreatePermanentIn, *, today: str) -> str:
    """組裝 v0.2 §3 永久卡：frontmatter（author: human）+ 正文 + typed edges inline。

    typed edges 用 Dataview inline field（``支持:: [[卡]] — 理由``），連結+理由同一行；
    方向定死「本卡 → 對方」，反向由 backlinks 免費取得。
    """
    # frontmatter（全部由本 endpoint 寫；source_refs 是初始溯源，日後記帳走 N520）。
    fm_lines = ["---", "type: permanent", "status: seedling", "author: human"]
    fm_lines.append(f"created: {today}")
    fm_lines.append(f"modified: {today}")
    if payload.source_refs:
        fm_lines.append("source_refs:")
        for ref in payload.source_refs:
            if ref.literature_path:
                val = f"[[{_strip_kb_prefix(ref.literature_path)}]]"
                if ref.anchor:
                    anchor = ref.anchor if ref.anchor.startswith("^") else f"^{ref.anchor}"
                    val = f"{val} {anchor}"
            else:
                val = ref.raw
            if val.strip():
                fm_lines.append(f'  - "{_yaml_escape(val)}"')
    else:
        fm_lines.append("source_refs: []")
    fm_lines.append("aliases: []")
    fm_lines.append("---")

    body = payload.body.strip()

    edge_lines: list[str] = []
    for e in payload.edges:
        if e.edge_type not in _VALID_EDGE_TYPES:
            continue
        link = _edge_target_link(e.target)
        if not link:
            continue
        label = _EDGE_LABELS[e.edge_type]
        reason = e.reason.strip()
        line = f"{label}:: [[{link}]]"
        if reason:
            line += f" — {reason}"
        edge_lines.append(line)

    parts = ["\n".join(fm_lines), "", body]
    if edge_lines:
        parts.append("")
        parts.append("\n".join(edge_lines))
    return "\n".join(parts) + "\n"


def _strip_kb_prefix(path: str) -> str:
    """``KB/Literature/xxx`` → ``Literature/xxx``（wikilink 內慣例不帶 KB/ 前綴）。"""
    p = path.strip().replace("[[", "").replace("]]", "")
    if p.startswith("KB/"):
        p = p[len("KB/") :]
    if p.endswith(".md"):
        p = p[: -len(".md")]
    return p


def _yaml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


@router.post("/api/permanent")
async def create_permanent(
    payload: CreatePermanentIn,
    request: Request,
    nakama_auth: str | None = Cookie(None),
):
    """建立一張永久卡（human-authoring；全系統唯一 Permanent body 寫入口）。

    - 空正文 → 422（紅線：正文是人的工作，不可空白存檔）。
    - 寫入帶 ``author: human``（provenance：人層權威，非 agent/promotion）。
    - typed edges 組進 body（``支持:: [[...]]`` inline field）。
    - 存檔後跑 Phase 5 善後（mined_concepts 回填 / fleeting 善後 / index / log）。
    """
    if not check_auth(nakama_auth):
        raise HTTPException(403, detail="not authenticated")

    title = payload.title.strip()
    if not title:
        raise HTTPException(422, detail="檔名不能是空的——需要一句宣告句")
    if not payload.body.strip():
        # 紅線內側：正文要人自己寫。空正文一律擋。
        raise HTTPException(422, detail="正文不能是空白——這段是紅線內側，要你自己寫")

    vault = get_vault_path()
    slug = slugify(title) or title
    rel = f"{PERMANENT_DIR}/{slug}.md"
    dest = vault / rel
    if dest.exists():
        raise HTTPException(409, detail=f"永久卡已存在：{rel}（換個宣告句或先去編輯既有卡）")

    today = date.today().isoformat()
    markdown = _assemble_permanent_markdown(payload, today=today)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(markdown, encoding="utf-8")
    logger.info("created permanent card %s (author=human)", rel)

    # Phase 5 善後（best-effort：失敗只記 warning，不讓開卡整體失敗）。
    phase5 = _run_phase5(payload, card_rel=rel, card_title=title)

    return JSONResponse(
        {
            "ok": True,
            "path": rel,
            "title": title,
            "author": "human",
            "edges": len(
                [e for e in payload.edges if e.edge_type in _VALID_EDGE_TYPES and e.target.strip()]
            ),
            "phase5": phase5,
        }
    )


# ---------------------------------------------------------------------------
# Phase 5 善後（v0.2 §8）——只沿人親手寫的連結傳播，不代建 Permanent 側連結
# ---------------------------------------------------------------------------


def _run_phase5(payload: CreatePermanentIn, *, card_rel: str, card_title: str) -> dict:
    """存檔後善後：① Literature mined_concepts+status ② fleeting status+回收桶
    ③ index ④ log append。每項獨立 try，回報結果供 UI / log。

    鏡像規則（§8）：只回填使用者親手帶的 ``literature_slug`` 的 mined_concepts；
    語意對應（哪個 Concept）不代建 Permanent 側連結（那是日後 Concept 頁 defer 標記
    的工作，非本 endpoint）。
    """
    result: dict[str, object] = {
        "literature_backfilled": False,
        "fleeting_processed": False,
        "index_updated": False,
        "log_appended": False,
        "warnings": [],
    }
    warnings: list[str] = result["warnings"]  # type: ignore[assignment]

    # ① Literature mined_concepts + status: mined（沿人寫的 source_ref 連結）。
    if payload.literature_slug:
        try:
            _backfill_literature_mined(payload.literature_slug, card_title)
            result["literature_backfilled"] = True
        except Exception as exc:  # noqa: BLE001 — 善後 best-effort
            warnings.append(f"Literature 回填失敗（{payload.literature_slug}）：{exc}")

    # ② fleeting：翻 status: processed + 原檔送回收桶。
    if payload.fleeting_path:
        try:
            _process_fleeting(payload.fleeting_path)
            result["fleeting_processed"] = True
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"fleeting 善後失敗（{payload.fleeting_path}）：{exc}")

    # ③ KB/index.md 更新（Permanent 區補一行）。
    try:
        _append_index_permanent(card_title)
        result["index_updated"] = True
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"index 更新失敗：{exc}")

    # ④ KB/log.md append。
    try:
        _append_log_open_card(card_rel, card_title)
        result["log_appended"] = True
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"log append 失敗：{exc}")

    return result


def _backfill_literature_mined(slug: str, card_title: str) -> None:
    """把新永久卡標題加進 Literature 檔 frontmatter 的 ``mined_concepts`` 並設 status: mined。

    做法：直接 patch 既有 Literature 檔的 frontmatter（in-place，逐字保留正文），
    再呼叫 ``write_literature_note`` re-render（idempotent，會從檔讀回 bookkeeping）。
    若 Literature 檔不存在則 no-op（pilot 容忍：候選來自 annotation，Literature 可能
    尚未 render）。
    """
    vault = get_vault_path()
    lit_path = vault / "KB" / "Literature" / f"{slug}.md"
    if not lit_path.exists():
        return

    content = lit_path.read_text(encoding="utf-8")
    fm, _body = extract_frontmatter(content)
    mined = list(fm.get("mined_concepts") or [])
    if card_title not in mined:
        mined.append(card_title)
    _patch_literature_frontmatter(lit_path, mined_concepts=mined, status="mined")

    # idempotent re-render——保留記帳區 + 只重畫劃線內容；從檔讀回我們剛 patch 的值。
    try:
        from shared.literature_writer import write_literature_note

        write_literature_note(slug, source_kind="book")
    except Exception:  # noqa: BLE001 — frontmatter patch 已生效；re-render 失敗不致命
        logger.exception("literature re-render after mined backfill failed slug=%s", slug)


def _patch_literature_frontmatter(path: Path, *, mined_concepts: list[str], status: str) -> None:
    """In-place 改 Literature 檔 frontmatter 的 ``mined_concepts`` / ``status`` 兩 key。

    其餘 frontmatter key 與正文逐字保留（line-based replace，不 re-dump 整塊）。
    缺 key 則補在 frontmatter 尾端。
    """
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return
    lines = content.splitlines(keepends=True)
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            end_idx = i
            break
    if end_idx is None:
        return

    fm_lines = lines[1:end_idx]
    body_block = "".join(lines[end_idx + 1 :])

    new_fm: list[str] = []
    skip_block = False
    seen_mined = seen_status = False
    for ln in fm_lines:
        stripped = ln.rstrip("\r\n")
        # 跳過舊 mined_concepts 區塊（含其 `  - ` 子項）。
        if skip_block:
            if stripped.startswith("  ") or stripped.startswith("\t") or stripped.startswith("- "):
                continue
            skip_block = False
        if stripped.startswith("mined_concepts:"):
            seen_mined = True
            new_fm.extend(_dump_mined_block(mined_concepts))
            # 若同行已含 inline `[]`，無子項；否則跳過後續子項。
            if stripped.strip() == "mined_concepts:":
                skip_block = True
            continue
        if stripped.startswith("status:"):
            seen_status = True
            new_fm.append(f"status: {status}\n")
            continue
        new_fm.append(ln)

    if not seen_mined:
        new_fm.extend(_dump_mined_block(mined_concepts))
    if not seen_status:
        new_fm.append(f"status: {status}\n")

    new_content = "---\n" + "".join(new_fm) + "---\n" + body_block
    path.write_text(new_content, encoding="utf-8")


def _dump_mined_block(mined_concepts: list[str]) -> list[str]:
    if not mined_concepts:
        return ["mined_concepts: []\n"]
    out = ["mined_concepts:\n"]
    for c in mined_concepts:
        out.append(f'  - "{_yaml_escape(c)}"\n')
    return out


def _process_fleeting(fleeting_path: str) -> None:
    """fleeting 善後：翻 ``status: processed`` 後把原檔送回收桶（v0.2 §4）。

    AI 不改正文字——只翻 status（記帳），然後整檔送回收桶。順序：先寫 status
    （留痕，萬一回收桶失敗仍可見已處理），再送回收桶。
    """
    from shared.discard_service import _send_to_recycle_bin

    vault = get_vault_path()
    rel = fleeting_path.strip()
    if rel.startswith("KB/"):
        abs_path = vault / rel
    else:
        abs_path = vault / "KB" / "Fleeting" / Path(rel).name
    if not abs_path.exists():
        return

    content = abs_path.read_text(encoding="utf-8")
    fm, body = extract_frontmatter(content)
    # 翻 status: open → processed（line-based，不動正文）。
    new_content = _set_fleeting_status_processed(content)
    abs_path.write_text(new_content, encoding="utf-8")
    _send_to_recycle_bin(abs_path)


def _set_fleeting_status_processed(content: str) -> str:
    if not content.startswith("---"):
        return content
    lines = content.splitlines(keepends=True)
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            end_idx = i
            break
    if end_idx is None:
        return content
    seen = False
    for i in range(1, end_idx):
        if lines[i].rstrip("\r\n").startswith("status:"):
            lines[i] = "status: processed\n"
            seen = True
            break
    if not seen:
        lines.insert(end_idx, "status: processed\n")
    return "".join(lines)


def _append_index_permanent(card_title: str) -> None:
    """在 KB/index.md 的 Permanent 區補一行（找不到區段則 append 到檔尾）。"""
    from shared.obsidian_writer import append_to_file, read_page

    entry = f"- [[Permanent/{card_title}]] 🌱\n"
    existing = read_page("KB/index.md")
    if existing is None:
        append_to_file("KB/index.md", f"## Permanent\n{entry}")
        return
    if f"[[Permanent/{card_title}]]" in existing:
        return  # 已有，不重複
    append_to_file("KB/index.md", entry)


def _append_log_open_card(card_rel: str, card_title: str) -> None:
    """KB/log.md append 一筆開卡紀錄（append-only）。"""
    from shared.obsidian_writer import append_to_file

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"- {ts} [centaur:open_card] author=human card={card_rel} title={card_title}\n"
    append_to_file("KB/log.md", line)


# ---------------------------------------------------------------------------
# POST /kb/api/review/{skip,later} — 動作回寫 state 檔
# ---------------------------------------------------------------------------


class ReviewActionIn(BaseModel):
    candidate_id: str


@router.post("/api/review/skip")
async def review_skip(payload: ReviewActionIn, nakama_auth: str | None = Cookie(None)):
    """略過候選——寫進 state 檔 ``skipped``，N522 永不再現。"""
    if not check_auth(nakama_auth):
        raise HTTPException(403, detail="not authenticated")
    cid = payload.candidate_id.strip()
    if not cid:
        raise HTTPException(422, detail="candidate_id 不可空")
    _update_review_state(cid, "skip")
    return {"ok": True, "candidate_id": cid, "action": "skip"}


@router.post("/api/review/later")
async def review_later(payload: ReviewActionIn, nakama_auth: str | None = Cookie(None)):
    """之後再說——寫進 state 檔 ``deferred``（記今日），14 天後由 N522 job 過期歸檔。"""
    if not check_auth(nakama_auth):
        raise HTTPException(403, detail="not authenticated")
    cid = payload.candidate_id.strip()
    if not cid:
        raise HTTPException(422, detail="candidate_id 不可空")
    _update_review_state(cid, "later")
    return {"ok": True, "candidate_id": cid, "action": "later"}


# ---------------------------------------------------------------------------
# GET /kb/review — 每日回顧頁
# ---------------------------------------------------------------------------


def _bundle_for_template(bundle: DailyReviewBundle) -> dict:
    """把 bundle 攤平成 template 友善 dict（含開卡 drawer 需要的 edge 分組）。"""
    weekday_zh = "一二三四五六日"
    try:
        d = date.fromisoformat(bundle.review_date)
        prev = d.fromordinal(d.toordinal() - 1)
        weekday = weekday_zh[d.weekday()]
        review_label = f"{bundle.review_date}（{weekday}） · 回顧 {prev.strftime('%m-%d')}"
    except ValueError:
        review_label = bundle.review_date

    candidates = []
    for c in bundle.candidates:
        edge_groups: dict[str, list[dict]] = {"support": [], "refute": [], "extend": []}
        for e in c.edges:
            if e.edge_type in edge_groups:
                edge_groups[e.edge_type].append(
                    {
                        "target_card": e.target_card,
                        "target_title": e.target_title or e.target_card.rsplit("/", 1)[-1],
                        "direction": e.direction,
                    }
                )
        candidates.append(
            {
                "candidate_id": c.candidate_id,
                "suggested_title": c.suggested_title,
                "why": c.why,
                "strong_signal": c.strong_signal,
                "source_refs": [r.model_dump() for r in c.source_refs],
                "primary_ref": c.source_refs[0].model_dump() if c.source_refs else None,
                "edge_groups": edge_groups,
            }
        )

    return {
        "review_label": review_label,
        "review_date": bundle.review_date,
        "weekly_sweep": bundle.weekly_sweep,
        "candidates": candidates,
        "fleeting": [f.model_dump() for f in bundle.fleeting],
        "sweep": [s.model_dump() for s in bundle.sweep],
        "warnings": bundle.warnings,
        "n_open": len(bundle.candidates) + len(bundle.fleeting),
    }


@router.get("/review", response_class=HTMLResponse)
async def daily_review_page(request: Request, nakama_auth: str | None = Cookie(None)):
    """每日回顧頁——三段（fleeting / 候選 / 清掃）+ 開卡 drawer（照 prototype v2）。"""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/kb/review", status_code=302)

    today_weekly = date.today().weekday() == 0  # 週一當每週清掃日（pilot 慣例）
    try:
        bundle = _compute_bundle(weekly=today_weekly)
    except Exception as exc:  # noqa: BLE001 — UI 不應因 job 出錯而 500
        logger.exception("daily-review bundle compute failed")
        bundle = DailyReviewBundle(
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            review_date=date.today().isoformat(),
            warnings=[f"每日回顧資料產生失敗：{exc}"],
        )

    ctx = _bundle_for_template(bundle)
    return templates.TemplateResponse(
        request,
        "daily_review.html",
        {
            **ctx,
            "bundle_json": json.dumps(ctx, ensure_ascii=False),
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )
