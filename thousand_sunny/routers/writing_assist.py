"""Thousand Sunny routes for ADR-024 Writing Assist (issue #517).

Thin handlers that present a ``WritingAssistOutput`` rendered from a
``ReadingContextPackage``. Routes never compose prose, never call an LLM,
and never write to the vault — they ONLY surface scaffold structure +
pointers + question prompts so 修修 can hand-write Stage 4 atomic content.

Routes:

| Method | Path                                  |
|--------|---------------------------------------|
| GET    | ``/writing-assist/{source_id_b64}``   |

``source_id`` is base64url-encoded (no padding) per the #516 pattern; the
handler decodes but does NOT parse the ``ebook:`` / ``inbox:`` namespace
prefix (per #509 N3 contract).

The package + surface objects are provided via a service-injection
pattern that mirrors #516. The app entry point (``thousand_sunny/app.py``)
calls ``set_service`` with a production-wired service; tests reload the
module to swap in a fake. Without a wired service the routes return 503.

The handler maps boundary failures to HTTP statuses via :func:`_http_status_for`
— ``OSError`` (filesystem persistence failure) → 500; ``ValueError`` /
``KeyError`` (caller-supplied lookup miss) → 404. Mirrors the canonical
pattern from ``thousand_sunny/routers/promotion_review.py`` (recently added
to address #529 review).

Hard architectural invariant: this router MUST NOT generate prose, MUST NOT
ghostwrite Line 2 atomic content, MUST NOT call an LLM. All such enforcement
lives at the surface boundary (``shared.writing_assist_surface``) which
raises ``ValueError("ghostwriting detected: ...")`` if any W1-W7 rule is
violated; that error reaches the user as a 500.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.log import get_logger
from shared.schemas.reading_context_package import ReadingContextPackage
from shared.writing_assist_surface import WritingAssistSurface
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.writing_assist")
router = APIRouter(prefix="/writing-assist")

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "writing_assist"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# Documented HTTP-boundary failures for service calls. Narrow tuple per
# #511 F5 lesson — programmer errors propagate. The catch sites separate
# OSError (server-side IO failure → 5xx) from ValueError / KeyError
# (caller-supplied or lookup miss → 4xx) via :func:`_http_status_for`.
_HTTP_BOUNDARY_FAILURES: tuple[type[BaseException], ...] = (ValueError, KeyError, OSError)


def _http_status_for(exc: BaseException, *, lookup_status: int = 404) -> int:
    """Map a boundary failure to an HTTP status code.

    ``OSError`` (filesystem persistence failure) is server-side and maps to
    500. Everything else in ``_HTTP_BOUNDARY_FAILURES`` is treated as the
    caller-supplied ``lookup_status`` (404 for record-not-found semantics in
    ``load_package``).
    """
    if isinstance(exc, OSError):
        return 500
    return lookup_status


# ── Service protocol ──────────────────────────────────────────────────────────


class WritingAssistService(Protocol):
    """Persistence + surface facade. Production wiring loads packages from
    a manifest store; tests inject in-memory dict-backed fakes.

    The Protocol is intentionally minimal — Slice 9 ships only the read
    path; package authoring is the BUILDER's job, dispatched from another
    surface (out of scope for #517).
    """

    def load_package(self, source_id: str) -> ReadingContextPackage:
        """Return the persisted package for ``source_id``.

        Raises ``KeyError`` when the package is missing (mapped to 404 by
        the route handler), ``OSError`` on filesystem failure (mapped to
        500), ``ValueError`` on schema parse failure (mapped to 404 since
        from the user's perspective the package is unusable).
        """
        ...


class _DefaultWritingAssistService:
    """Default implementation that loads a JSON-encoded package from disk.

    Production wiring path: ``manifest_root / packages / {source_id_b64}.json``
    where the JSON is a model_dump_json() of a ``ReadingContextPackage``.
    Slice 9 ships this read-only path; the write side (package authoring +
    persistence) is out of scope and lives in the consumer surface that
    invokes the builder.
    """

    def __init__(self, *, package_root: Path) -> None:
        self._package_root = package_root

    def load_package(self, source_id: str) -> ReadingContextPackage:
        encoded = base64.urlsafe_b64encode(source_id.encode("utf-8")).decode("ascii").rstrip("=")
        path = self._package_root / f"{encoded}.json"
        if not path.exists():
            raise KeyError(f"package not found for source_id={source_id!r}")
        raw = path.read_text(encoding="utf-8")
        return ReadingContextPackage.model_validate_json(raw)


# ── Service registry ──────────────────────────────────────────────────────────


_service: WritingAssistService | None = None
_surface_factory: Callable[[], WritingAssistSurface] = WritingAssistSurface
"""Factory used to construct the surface per request. Indirection lets a
test substitute a stricter / mockable surface; production keeps it as the
default ``WritingAssistSurface``."""


def set_service(service: WritingAssistService) -> None:
    """Wire a service instance into the router. Called from app startup
    and from tests. Idempotent — last setter wins."""
    global _service
    _service = service


def get_service() -> WritingAssistService:
    """Return the wired service or raise if missing."""
    if _service is None:
        raise HTTPException(
            status_code=503,
            detail="Writing-assist service not configured for this deployment.",
        )
    return _service


# ── source_id encoding helpers ────────────────────────────────────────────────


def _decode_source_id(encoded: str) -> str:
    """base64url-decode ``encoded`` to the original opaque ``source_id``.
    Raises ``HTTPException(400)`` on malformed input. Mirrors the #516 helper.
    """
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid source_id encoding: {exc!s}")


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/{source_id_b64}", response_class=HTMLResponse)
async def render_scaffold(
    request: Request,
    source_id_b64: str,
    nakama_auth: str | None = Cookie(None),
):
    """Render the writing-assist scaffold for ``source_id_b64``.

    Loads the persisted ``ReadingContextPackage``, runs the surface to
    produce a ``WritingAssistOutput``, and renders the scaffold template.
    The template renders ONLY scaffold structure (headings + bullets +
    blockquoted excerpts); it does NOT generate prose. The surface's
    W1-W7 enforcement raises if any rule is violated; uncaught surface
    errors surface as 500.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/writing-assist/{source_id_b64}", status_code=302)
    service = get_service()
    source_id = _decode_source_id(source_id_b64)
    try:
        package = service.load_package(source_id)
    except _HTTP_BOUNDARY_FAILURES as exc:
        raise HTTPException(status_code=_http_status_for(exc, lookup_status=404), detail=str(exc))

    if package.error is not None:
        # Error envelope from the builder — surface it via the same template
        # using a flag so the user can read the error and choose to retry.
        return _templates.TemplateResponse(
            request,
            "scaffold.html",
            {
                "package": package,
                "output": None,
                "encoded_id": source_id_b64,
                "error_message": package.error,
            },
        )

    surface = _surface_factory()
    try:
        output = surface.render(package)
    except ValueError as exc:
        # Surface raised a W1-W7 violation. Per the architectural invariant
        # this is a server-side configuration / data failure (a future
        # LLM-backed enrichment producing prose-shaped output) → 500. The
        # error message preserves the exact W-rule code so review tools can
        # diagnose.
        logger.error(
            "writing-assist surface render rejected output",
            extra={
                "category": "writing_assist_render_rejected",
                "source_id": source_id,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail=str(exc))

    return _templates.TemplateResponse(
        request,
        "scaffold.html",
        {
            "package": package,
            "output": output,
            "encoded_id": source_id_b64,
            "error_message": None,
        },
    )


# ── Internal exposure for tests ──────────────────────────────────────────────


def _build_default_service(package_root: Path) -> WritingAssistService:
    """Helper for app wiring + tests that want the default disk-backed
    service. Tests typically inject their own fake instead."""
    return _DefaultWritingAssistService(package_root=package_root)
