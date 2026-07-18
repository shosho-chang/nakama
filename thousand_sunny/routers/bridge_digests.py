"""Bridge digest viewer — Tier A (vault-as-substrate read + ask).

Surfaces:

- ``GET /bridge/digests`` — unified landing: today's PubMed + AI snapshot +
  last 7d timeline.
- ``GET /bridge/digests/{type}/{date}`` — detail view, renders the digest
  markdown via ``shared.markdown.render_markdown`` with a wikilink resolver
  that maps ``pubmed-{id}`` → external PubMed URL; other wikilinks render
  as ``.wikilink-broken`` (no Bridge source page yet — Issue #231 keeps
  Bridge read-only against vault).
- ``GET/POST /bridge/digests/ask`` — LLM-over-vault Q&A: concat in-scope
  digests, dispatch to Claude (Sonnet 4.6), render the answer with source
  citations back to detail pages. Cost is bounded by ``MAX_DAYS`` and
  ``MAX_CONTEXT_CHARS`` in ``shared.digest_ask``.

Auth: HMAC cookie (mirrors bridge.page_router / projects).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.config import get_vault_path
from shared.digest_ask import (
    DEFAULT_DAYS,
    MAX_DAYS,
    AskValidationError,
    ask,
    parse_request,
)
from shared.digest_indexer import (
    DIGEST_TYPES,
    DigestIndexer,
    DigestNotFoundError,
    today_taipei,
)
from shared.digest_study_detail import load_study_detail
from shared.log import get_logger
from shared.markdown import render_markdown
from shared.markdown_wikilinks import WikilinkResolver
from shared.pubmed_score_rubric import build_score_rows
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.bridge_digests")

page_router = APIRouter(prefix="/bridge", tags=["bridge-digests"])

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "bridge"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# Pretty labels for the canonical type slugs.
_TYPE_LABEL = {"pubmed": "PubMed", "ai": "AI News"}


def _shosho_asset_version() -> str:
    """8-char hash of the design-system CSS this page links. Busts CF cache."""
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for css in ("tokens.css", "bridge.css", "bridge-pages.css", "bridge-digests.css", "theme.js"):
        path = static_dir / css
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


def _build_resolver() -> WikilinkResolver:
    r = WikilinkResolver()

    def _pubmed(target: str) -> str | None:
        if target.startswith("pubmed-") and target[7:].isdigit():
            return f"https://pubmed.ncbi.nlm.nih.gov/{target[7:]}/"
        return None

    r.register(_pubmed)
    return r


_RESOLVER = _build_resolver()


def _indexer() -> DigestIndexer:
    return DigestIndexer(get_vault_path())


# ── routes ───────────────────────────────────────────────────────────────────


@page_router.get("/digests", response_class=HTMLResponse)
async def digests_landing(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/digests", status_code=302)

    idx = _indexer()
    latest = idx.latest_per_type()
    # Hero already surfaces today's digests; skip today in the timeline so
    # the past-7-day list doesn't repeat the same content.
    timeline_by_date = idx.last_n_days_by_date(n=7, skip_today=True)
    conflicts = idx.list_conflict_files()

    hero_cards = []
    for t in DIGEST_TYPES:
        entry = latest[t]
        preview: list = []
        if entry is not None:
            try:
                # Show all entries — user wants the full topic list on the
                # hero card, not just a top-3 preview.
                preview = idx.load_studies(t, entry.date)
            except Exception:  # noqa: BLE001 — parser issue shouldn't break landing
                logger.exception("load_studies failed: type=%s date=%s", t, entry.date)
        hero_cards.append(
            {
                "type": t,
                "label": _TYPE_LABEL[t],
                "entry": entry,
                "preview": preview,
            }
        )

    return _templates.TemplateResponse(
        request,
        "digests.html",
        {
            "hero_cards": hero_cards,
            "timeline_by_date": timeline_by_date,
            "conflicts": conflicts,
            "today": today_taipei().isoformat(),
            "type_label": _TYPE_LABEL,
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


def _ask_form_context(**overrides) -> dict:
    base = {
        "question": "",
        "days": DEFAULT_DAYS,
        "selected_types": list(DIGEST_TYPES),
        "all_types": list(DIGEST_TYPES),
        "type_label": _TYPE_LABEL,
        "max_days": MAX_DAYS,
        "result": None,
        "answer_html": None,
        "error": None,
        "asset_version": _SHOSHO_ASSET_VERSION,
    }
    base.update(overrides)
    return base


@page_router.get("/digests/ask", response_class=HTMLResponse)
async def digest_ask_get(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/digests/ask", status_code=302)
    return _templates.TemplateResponse(request, "digest_ask.html", _ask_form_context())


@page_router.post("/digests/ask", response_class=HTMLResponse)
async def digest_ask_post(
    request: Request,
    nakama_auth: str | None = Cookie(None),
    question: str = Form(""),
    days: str = Form(""),
    types: list[str] | None = Form(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/digests/ask", status_code=302)

    try:
        req = parse_request(question=question, days=days or None, types=types or None)
    except AskValidationError as exc:
        ctx = _ask_form_context(
            question=question,
            days=days or DEFAULT_DAYS,
            selected_types=types or list(DIGEST_TYPES),
            error=str(exc),
        )
        return _templates.TemplateResponse(request, "digest_ask.html", ctx)

    try:
        result = ask(req, _indexer())
    except Exception as exc:  # noqa: BLE001 — surface any LLM/IO failure inline
        logger.exception("digest_ask LLM dispatch failed")
        ctx = _ask_form_context(
            question=req.question,
            days=req.days,
            selected_types=list(req.types),
            error=f"查詢失敗：{exc.__class__.__name__}",
        )
        return _templates.TemplateResponse(request, "digest_ask.html", ctx)

    answer_html = render_markdown(result.answer, wikilink_resolver=_RESOLVER)
    ctx = _ask_form_context(
        question=req.question,
        days=req.days,
        selected_types=list(req.types),
        result=result,
        answer_html=answer_html,
    )
    return _templates.TemplateResponse(request, "digest_ask.html", ctx)


@page_router.get("/digests/{type_}/{date_}", response_class=HTMLResponse)
async def digest_detail(
    request: Request,
    type_: str,
    date_: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/digests/{type_}/{date_}", status_code=302)

    idx = _indexer()
    try:
        entry = idx.get(type_, date_)
        body_md = idx.load_text(type_, date_)
    except DigestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Parse structured studies for card rendering. Editor's note + section
    # framing come from the markdown intro (before first ## section); render
    # that block to HTML so we can keep its wikilinks + emphasis without
    # dumping the per-entry markdown (the card layout supersedes that).
    try:
        studies = idx.load_studies(type_, date_)
    except Exception:  # noqa: BLE001 — parser issue → empty card list
        logger.exception("load_studies failed: type=%s date=%s", type_, date_)
        studies = []

    # Extract the intro (everything before the first H2) so we can render
    # the editor's blockquote / counts line as proper markdown rather than
    # losing it. If parser found no studies, fall back to full body.
    if studies:
        intro_md = re.split(r"^##\s", body_md, maxsplit=1, flags=re.MULTILINE)[0]
    else:
        intro_md = body_md
    intro_html = render_markdown(intro_md, wikilink_resolver=_RESOLVER)

    return _templates.TemplateResponse(
        request,
        "digest_detail.html",
        {
            "entry": entry,
            "type_": type_,
            "intro_html": intro_html,
            "studies": studies,
            "type_label": _TYPE_LABEL[type_],
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@page_router.get("/digests/pubmed/{date_}/{pmid}", response_class=HTMLResponse)
async def digest_study(
    request: Request,
    date_: str,
    pmid: str,
    nakama_auth: str | None = Cookie(None),
):
    """Per-study detail: PubMed abstract (fetched + cached) + zh-TW translation.

    PubMed-only — AI digest entries are blog posts whose card title links out
    to the source directly, so they have no station-side detail page.
    """
    next_url = f"/bridge/digests/pubmed/{date_}/{pmid}"
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next={next_url}", status_code=302)

    if not pmid.isdigit():
        raise HTTPException(status_code=404, detail=f"invalid PMID: {pmid!r}")

    idx = _indexer()
    try:
        # Validates type/date shape + existence; 404 for a missing digest day.
        entry = idx.get("pubmed", date_)
    except DigestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    detail = load_study_detail(idx, date_, pmid)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"PMID {pmid} 不在 {date_} 的 PubMed digest 中",
        )

    return _templates.TemplateResponse(
        request,
        "digest_study_detail.html",
        {
            "entry": entry,
            "detail": detail,
            "s": detail.study,
            "score_rows": build_score_rows(detail.study),
            "type_label": _TYPE_LABEL["pubmed"],
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )
