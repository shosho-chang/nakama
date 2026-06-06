"""Public methodology page for the Centaur Zettelkasten (`/centaur`).

Serves 修修's standalone methodology doc — "AI 增強的卡片盒 / The Compounding
Vault" — at a clean, shareable root URL instead of the raw
``/static/centaur_zettelkasten.html`` mount path.

Public route (no auth) AND indexable on purpose: it's meant to be iterated on
and shared, same posture as ``/crew``. Unlike ``crew.py`` / ``architecture.py``
there is no ``__ASSET_VERSION__`` cache-bust substitution: the document is fully
self-contained (inline ``<style>``, fonts from Google Fonts CDN, zero
``/static/*`` asset links), so there are no first-party asset URLs to version.

Iterate workflow: edit ``static/centaur/index.html`` → restart the service
(``_rendered_html`` is ``lru_cache``-d in-process, like the sibling pages).
"""

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_PAGE_PATH = Path(__file__).resolve().parent.parent / "static" / "centaur" / "index.html"


@lru_cache(maxsize=1)
def _rendered_html() -> str:
    return _PAGE_PATH.read_text(encoding="utf-8")


@router.api_route("/centaur", methods=["GET", "HEAD"], include_in_schema=False)
async def centaur_page() -> HTMLResponse:
    return HTMLResponse(_rendered_html(), media_type="text/html; charset=utf-8")
