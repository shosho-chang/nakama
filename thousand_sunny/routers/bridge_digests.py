"""Bridge digest viewer — Tier A first slice (vault-as-substrate read).

Surfaces:

- ``GET /bridge/digests`` — unified landing: today's PubMed + AI snapshot +
  last 7d timeline.
- ``GET /bridge/digests/{type}/{date}`` — detail view, renders the digest
  markdown via ``shared.markdown.render_markdown`` with a wikilink resolver
  that maps ``pubmed-{id}`` → external PubMed URL; other wikilinks render
  as ``.wikilink-broken`` (no Bridge source page yet — Issue #231 keeps
  Bridge read-only against vault).

Auth: HMAC cookie (mirrors bridge.page_router / projects). Read-only —
no form POSTs in this PR (the ``/ask`` Q&A endpoint is Tier A PR3).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.config import get_vault_path
from shared.digest_indexer import (
    DIGEST_TYPES,
    DigestIndexer,
    DigestNotFoundError,
    today_taipei,
)
from shared.log import get_logger
from shared.markdown import render_markdown
from shared.markdown_wikilinks import WikilinkResolver
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
    for css in ("tokens.css", "bridge.css", "bridge-pages.css", "theme.js"):
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
    timeline = idx.last_n_days(n=7)

    hero_cards = [
        {
            "type": t,
            "label": _TYPE_LABEL[t],
            "entry": latest[t],
        }
        for t in DIGEST_TYPES
    ]

    return _templates.TemplateResponse(
        request,
        "digests.html",
        {
            "hero_cards": hero_cards,
            "timeline": timeline,
            "today": today_taipei().isoformat(),
            "type_label": _TYPE_LABEL,
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


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

    body_html = render_markdown(body_md, wikilink_resolver=_RESOLVER)

    return _templates.TemplateResponse(
        request,
        "digest_detail.html",
        {
            "entry": entry,
            "body_html": body_html,
            "type_label": _TYPE_LABEL[type_],
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )
