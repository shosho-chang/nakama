"""Thousand Sunny routes for ADR-024 Promotion Review (issue #516).

Thin handlers — every domain operation calls
``shared.promotion_review_service.PromotionReviewService``. Routes never
import ``shared.promotion_preflight`` / ``shared.source_map_builder`` /
``shared.concept_promotion_engine`` / ``shared.promotion_commit`` directly
(that's U1 in the Brief, asserted by the static-grep test RT9).

Routes:

| Method | Path                                                  |
|--------|-------------------------------------------------------|
| GET    | ``/robin/promotion/``                                 |
| GET    | ``/robin/promotion/source/{source_id_b64}``           |
| POST   | ``/robin/promotion/source/{source_id_b64}/decide/{item_id}`` |
| POST   | ``/robin/promotion/source/{source_id_b64}/commit``    |
| POST   | ``/robin/promotion/source/{source_id_b64}/start``     |

Legacy paths under ``/promotion-review/*`` are preserved as redirects:
GETs return 301 and POSTs return 308 (method+body preserving), both
pointing at the equivalent ``/robin/promotion/*`` URL. Per /architecture
v2 R1 + ADR-024:82-85 (ownership is Robin, not Brook).

``source_id`` is base64url-encoded (no padding) per Brief §3 — handlers
decode but do NOT parse the ``ebook:`` / ``inbox:`` namespace prefix
(per #509 N3 contract).

The service instance is provided via dependency injection through a
module-level holder (``set_service`` / ``get_service``). The app entry
point (``thousand_sunny/app.py``) calls ``set_service`` with a
production-wired service; tests reload the module to swap in a fake.
"""

from __future__ import annotations

import base64
from pathlib import Path

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from shared.config import get_vault_path
from shared.log import get_logger
from shared.promotion_review_service import PromotionReviewService
from shared.schemas.promotion_manifest import HumanDecisionKind
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.promotion_review")
router = APIRouter(prefix="/robin/promotion")
legacy_router = APIRouter(prefix="/promotion-review")


@legacy_router.get("/")
async def _legacy_list_redirect():
    return RedirectResponse("/robin/promotion/", status_code=301)


@legacy_router.get("/source/{source_id_b64}")
async def _legacy_review_redirect(source_id_b64: str):
    return RedirectResponse(f"/robin/promotion/source/{source_id_b64}", status_code=301)


@legacy_router.post("/source/{source_id_b64}/decide/{item_id}")
async def _legacy_decide_redirect(source_id_b64: str, item_id: str):
    # 308 preserves method+body so the form POST replays at the new URL.
    return RedirectResponse(
        f"/robin/promotion/source/{source_id_b64}/decide/{item_id}", status_code=308
    )


@legacy_router.post("/source/{source_id_b64}/commit")
async def _legacy_commit_redirect(source_id_b64: str):
    return RedirectResponse(f"/robin/promotion/source/{source_id_b64}/commit", status_code=308)


@legacy_router.post("/source/{source_id_b64}/start")
async def _legacy_start_redirect(source_id_b64: str):
    return RedirectResponse(f"/robin/promotion/source/{source_id_b64}/start", status_code=308)


_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "promotion_review"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _shosho_asset_version() -> str:
    """Return an 8-char hash of the Shosho design-system CSS files.

    Used to bust Cloudflare's /static/* edge cache when tokens.css or
    promotion-review.css change. Matches the asset-versioning pattern in
    ``robin.py`` / ``progress.py`` / ``architecture.py``.
    """
    import hashlib

    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for css in ("tokens.css", "promotion-review.css", "theme.js"):
        path = static_dir / css
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()

# Documented HTTP-boundary failures for service calls. Narrow tuple per
# #511 F5 lesson — programmer errors propagate. The catch sites separate
# OSError (server-side IO failure → 5xx) from ValueError / KeyError
# (client-supplied or lookup miss → 4xx) via :func:`_http_status_for`.
_HTTP_BOUNDARY_FAILURES: tuple[type[BaseException], ...] = (ValueError, KeyError, OSError)


def _http_status_for(exc: BaseException, *, lookup_status: int = 400) -> int:
    """Map a boundary failure to an HTTP status code.

    ``OSError`` (filesystem persistence failure) is server-side and maps to
    500. Everything else in ``_HTTP_BOUNDARY_FAILURES`` is treated as the
    caller-supplied ``lookup_status`` (400 for malformed input / duplicate
    batch_id, 404 for record-not-found semantics in ``decide_item``).
    """
    if isinstance(exc, OSError):
        return 500
    return lookup_status


# Allowed human-decision values, mirrored from #512 Literal so the route
# layer can reject unknown form values with 400 before hitting the service.
_DECISION_KINDS: set[str] = {"approve", "reject", "defer"}


# ── Service registry ──────────────────────────────────────────────────────────

_service: PromotionReviewService | None = None


def set_service(service: PromotionReviewService) -> None:
    """Wire a service instance into the router. Called from app startup
    and from tests. Idempotent — last setter wins."""
    global _service
    _service = service


def get_service() -> PromotionReviewService:
    """Return the wired service or raise if missing."""
    if _service is None:
        raise HTTPException(
            status_code=503,
            detail="Promotion review service not configured for this deployment.",
        )
    return _service


# ── source_id encoding helpers ────────────────────────────────────────────────


def _encode_source_id(source_id: str) -> str:
    """base64url-encode (no padding) ``source_id`` for use in URL paths."""
    return base64.urlsafe_b64encode(source_id.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_source_id(encoded: str) -> str:
    """Inverse of :func:`_encode_source_id`. Raises ``HTTPException(400)``
    on malformed input."""
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid source_id encoding: {exc!s}")


# ── View-model helpers ────────────────────────────────────────────────────────


def _approved_count(manifest) -> int:
    return sum(
        1
        for item in manifest.items
        if item.human_decision is not None and item.human_decision.decision == "approve"
    )


def _decision_counts(manifest) -> dict[str, int]:
    counts = {"approve": 0, "reject": 0, "defer": 0, "undecided": 0}
    for item in manifest.items:
        if item.human_decision is None:
            counts["undecided"] += 1
        else:
            counts[item.human_decision.decision] += 1
    return counts


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def list_pending(
    request: Request,
    nakama_auth: str | None = Cookie(None),
):
    """List preflighted Reading Sources awaiting promotion review."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/robin/promotion/", status_code=302)
    service = get_service()
    states = service.list_pending()
    rows = [
        {
            "state": state,
            "encoded_id": _encode_source_id(state.source_id),
        }
        for state in states
    ]
    return _templates.TemplateResponse(
        request,
        "list.html",
        {"rows": rows, "asset_version": _SHOSHO_ASSET_VERSION},
    )


@router.get("/source/{source_id_b64}", response_class=HTMLResponse)
async def review_source(
    request: Request,
    source_id_b64: str,
    nakama_auth: str | None = Cookie(None),
):
    """Render the per-source review surface."""
    if not check_auth(nakama_auth):
        return RedirectResponse(
            f"/login?next=/robin/promotion/source/{source_id_b64}", status_code=302
        )
    service = get_service()
    source_id = _decode_source_id(source_id_b64)
    manifest = service.load_review_session(source_id)
    if manifest is None:
        # Manifest doesn't exist yet — surface the start affordance via the
        # same template, with manifest=None so the template renders the empty
        # state with a "Start review" button.
        state = service.state_for(source_id)
        return _templates.TemplateResponse(
            request,
            "review.html",
            {
                "manifest": None,
                "state": state,
                "encoded_id": source_id_b64,
                "approved_count": 0,
                "decision_counts": {"approve": 0, "reject": 0, "defer": 0, "undecided": 0},
                "commit_enabled": service.commit_enabled,
                "asset_version": _SHOSHO_ASSET_VERSION,
            },
        )

    state = service.state_for(source_id)
    return _templates.TemplateResponse(
        request,
        "review.html",
        {
            "manifest": manifest,
            "state": state,
            "encoded_id": source_id_b64,
            "approved_count": _approved_count(manifest),
            "decision_counts": _decision_counts(manifest),
            "commit_enabled": service.commit_enabled,
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@router.post("/source/{source_id_b64}/decide/{item_id}", response_class=HTMLResponse)
async def decide_item(
    request: Request,
    source_id_b64: str,
    item_id: str,
    decision: str = Form(...),
    note: str = Form(default=""),
    nakama_auth: str | None = Cookie(None),
):
    """Record a human decision on a single review item.

    Returns the updated ``_item_card.html`` partial when called by an HTMX
    request (``HX-Request: true`` header); otherwise redirects back to the
    review surface so a plain form post round-trips through the full page.
    Brief §4.3 calls for HTMX swap; the redirect fallback keeps the gate
    fully usable without JS (progressive enhancement).
    """
    if not check_auth(nakama_auth):
        return Response(status_code=403)
    if decision not in _DECISION_KINDS:
        raise HTTPException(status_code=400, detail=f"invalid decision: {decision!r}")
    service = get_service()
    source_id = _decode_source_id(source_id_b64)
    note_value: str | None = note.strip() if note and note.strip() else None
    try:
        manifest = service.record_decision(
            source_id=source_id,
            item_id=item_id,
            decision=_coerce_decision(decision),
            note=note_value,
        )
    except _HTTP_BOUNDARY_FAILURES as exc:
        raise HTTPException(status_code=_http_status_for(exc, lookup_status=404), detail=str(exc))

    # HTMX in-place swap — return the partial.
    if request.headers.get("HX-Request") == "true":
        item = next((it for it in manifest.items if it.item_id == item_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail=f"item not found: {item_id!r}")
        return _templates.TemplateResponse(
            request,
            "_item_card.html",
            {
                "item": item,
                "encoded_id": source_id_b64,
            },
        )

    # Plain form post — redirect back to the review surface (303 so the
    # browser GETs and the back button doesn't replay the POST).
    return RedirectResponse(
        f"/robin/promotion/source/{source_id_b64}",
        status_code=303,
    )


@router.post("/source/{source_id_b64}/commit", response_class=HTMLResponse)
async def commit_source(
    request: Request,
    source_id_b64: str,
    batch_id: str = Form(...),
    nakama_auth: str | None = Cookie(None),
):
    """Trigger a commit of all currently-approved items."""
    if not check_auth(nakama_auth):
        return Response(status_code=403)
    service = get_service()
    source_id = _decode_source_id(source_id_b64)
    # Guard before touching the commit service: until N519 the pipeline only
    # produces placeholder claims, so committing is disabled and must not
    # write to the vault. The form is also disabled in the template; this is
    # the server-side backstop for a stale/forged POST.
    outcome = None
    if service.commit_enabled:
        try:
            outcome = service.commit_approved(
                source_id=source_id,
                batch_id=batch_id,
                vault_root=get_vault_path(),
            )
        except _HTTP_BOUNDARY_FAILURES as exc:
            raise HTTPException(
                status_code=_http_status_for(exc, lookup_status=400), detail=str(exc)
            )

    manifest = service.load_review_session(source_id)
    state = service.state_for(source_id)
    return _templates.TemplateResponse(
        request,
        "review.html",
        {
            "manifest": manifest,
            "state": state,
            "encoded_id": source_id_b64,
            "approved_count": _approved_count(manifest) if manifest is not None else 0,
            "decision_counts": (
                _decision_counts(manifest)
                if manifest is not None
                else {"approve": 0, "reject": 0, "defer": 0, "undecided": 0}
            ),
            "last_commit_outcome": outcome,
            "commit_enabled": service.commit_enabled,
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@router.post("/source/{source_id_b64}/start")
async def start_review(
    source_id_b64: str,
    nakama_auth: str | None = Cookie(None),
):
    """Run preflight → builder → engine and persist a fresh manifest.

    Per Brief §6 boundary 11, real LLM-backed extractor / matcher should
    NOT block this handler in production. Slice 8 ships with deterministic
    fakes; production wiring (out of scope for #516) must dispatch the
    chain to a background worker. See PR body for the dispatch decision.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(
            f"/login?next=/robin/promotion/source/{source_id_b64}", status_code=302
        )
    service = get_service()
    source_id = _decode_source_id(source_id_b64)
    try:
        service.start_review(source_id)
    except _HTTP_BOUNDARY_FAILURES as exc:
        raise HTTPException(status_code=_http_status_for(exc, lookup_status=400), detail=str(exc))

    return RedirectResponse(f"/robin/promotion/source/{source_id_b64}", status_code=303)


# ── Internal type narrowing ──────────────────────────────────────────────────


def _coerce_decision(decision: str) -> HumanDecisionKind:
    """Narrow a validated string to the typed Literal. The set check above
    is the source of truth; this helper just satisfies the type checker."""
    # Validation already happened at the route boundary.
    return decision  # type: ignore[return-value]
