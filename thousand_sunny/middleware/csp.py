"""Content-Security-Policy middleware for the Reader surface.

Defense-in-depth alongside ``shared.epub_sanitizer``: even if a ``<script>``
slips past the sanitizer, ``script-src 'self'`` blocks inline + remote script
execution inside the foliate-js iframe. Scoped to ``/robin/books/*`` and
``/robin/api/books/*`` (canonical, per R5) plus the legacy ``/books/*`` and
``/api/books/*`` prefixes (preserved so redirect responses also carry the
CSP header until Phase 2 alias drop). Other Robin / Bridge routes — which
legitimately use inline ``<script>`` blocks — are unaffected.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    # foliate-js rewrites each chapter's <link rel="stylesheet"> to a blob:
    # URL of the EPUB's CSS (epub.js Loader.createURL → URL.createObjectURL).
    # Without blob: here the browser refuses the book's stylesheet, so every
    # book renders with zero publisher CSS — headings tagged <h5>/<h6> then
    # collapse to the UA default (0.83em/0.67em), smaller than 1em body text.
    "style-src 'self' 'unsafe-inline' blob:; "
    "img-src 'self' data: blob:; "
    # foliate-js's paginator loads chapter HTML into iframes whose src is a
    # blob: URL of the same origin. 'none' blocks them and the reader can't
    # render anything. 'self' covers srcdoc fallback; blob: covers the
    # paginator's primary path.
    "frame-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'"
)

_GUARDED_PREFIXES = (
    "/robin/books",
    "/robin/api/books",
    # Legacy prefixes — kept so 301/308 redirect responses also carry the
    # CSP header. Drop once Phase 2 retires the aliases.
    "/books",
    "/api/books",
)


def _is_guarded(path: str) -> bool:
    for prefix in _GUARDED_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


class _CSPMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        if _is_guarded(request.url.path):
            response.headers["Content-Security-Policy"] = _CSP_POLICY
        return response


def add_csp_middleware(app: FastAPI) -> None:
    """Register the Reader CSP middleware on ``app``."""
    app.add_middleware(_CSPMiddleware)
