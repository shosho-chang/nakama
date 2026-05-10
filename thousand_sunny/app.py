"""Thousand Sunny — Nakama web server entry point."""

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

# Windows uvicorn inherits cp1252 stdout/stderr → any 中文 log message would
# raise UnicodeEncodeError per-record (logging then floods stderr with stack
# traces and silently drops the message). Force UTF-8 BEFORE any router
# import — routers create module-level loggers via shared.log.get_logger,
# which captures sys.stdout at handler-attach time.
from shared.log import force_utf8_console, get_logger

force_utf8_console()

from thousand_sunny.middleware.csp import add_csp_middleware  # noqa: E402
from thousand_sunny.routers import (  # noqa: E402
    auth,
    bridge,
    bridge_zoro,
    brook,
    franky,
    projects,
    promotion_review,
    repurpose,
    writing_assist,
    zoro,
)

_logger = get_logger("nakama.web.app")


# ── ADR-024 Promotion wiring config (N518a / issue #540) ────────────────────


@dataclass(frozen=True)
class _PromotionWiringConfig:
    """Resolved env-driven config for the ADR-024 promotion surfaces.

    All env reads happen here at startup (W6 / N518 brief §6 boundary 5);
    adapter classes themselves never call ``os.getenv``.
    """

    vault_root: Path
    manifest_root: Path
    reading_context_package_root: Path
    promotion_mode: str  # "dry_run" | "llm"


def _load_promotion_wiring_config() -> _PromotionWiringConfig:
    """Read ``NAKAMA_*`` env vars + apply documented defaults.

    Required:
    - ``NAKAMA_VAULT_ROOT`` — absolute path to the Obsidian vault root.

    Optional:
    - ``NAKAMA_PROMOTION_MANIFEST_ROOT`` (default ``{vault}/.promotion-manifests``)
    - ``NAKAMA_READING_CONTEXT_PACKAGE_ROOT`` (default ``{vault}/.reading-context-packages``)
    - ``NAKAMA_PROMOTION_MODE`` (default ``"dry_run"``)

    Raises ``RuntimeError`` when ``NAKAMA_VAULT_ROOT`` is missing — startup
    must surface bad config loudly so operator visibility is preserved
    (W4 / brief §6 boundary 7).
    """
    vault_raw = os.environ.get("NAKAMA_VAULT_ROOT")
    if not vault_raw:
        raise RuntimeError(
            "NAKAMA_VAULT_ROOT is required when Robin/promotion wiring is enabled. "
            "Set it in .env or unset DISABLE_ROBIN to skip wiring."
        )
    vault_root = Path(vault_raw)
    manifest_root = Path(
        os.environ.get(
            "NAKAMA_PROMOTION_MANIFEST_ROOT",
            str(vault_root / ".promotion-manifests"),
        )
    )
    package_root = Path(
        os.environ.get(
            "NAKAMA_READING_CONTEXT_PACKAGE_ROOT",
            str(vault_root / ".reading-context-packages"),
        )
    )
    mode = os.environ.get("NAKAMA_PROMOTION_MODE", "dry_run")
    return _PromotionWiringConfig(
        vault_root=vault_root,
        manifest_root=manifest_root,
        reading_context_package_root=package_root,
        promotion_mode=mode,
    )


def _wire_promotion_surfaces(config: _PromotionWiringConfig) -> None:
    """Construct adapters + services and inject them into both routers.

    Called from the FastAPI lifespan when Robin is enabled. After this
    helper returns, both ``promotion_review`` and ``writing_assist``
    routers have a wired service and will return 200 (not 503).

    Per N518a scope: claim extractor and concept matcher are STUBS that
    satisfy the Protocol shape but raise ``NotImplementedError`` when
    called. The full deterministic dry-run bodies land in N518b. This
    means service construction succeeds and ``GET`` routes work, but
    ``POST /promotion-review/.../start`` will surface a clear 500.

    Raises ``RuntimeError`` for ``NAKAMA_PROMOTION_MODE=llm`` — the LLM
    adapter is N519, not N518a.
    """
    # Imports are local so the cost (sqlite-backed registry init, schema
    # parsing) stays out of cold-start when ``DISABLE_ROBIN=1``.
    from shared.blob_loader import VaultBlobLoader
    from shared.concept_promotion_engine import ConceptPromotionEngine
    from shared.dry_run_extractor import DryRunClaimExtractor
    from shared.dry_run_matcher import DryRunConceptMatcher
    from shared.kb_concept_index_default import VaultKBConceptIndex
    from shared.promotion_commit import PromotionCommitService
    from shared.promotion_preflight import PromotionPreflight
    from shared.promotion_review_service import (
        FilesystemManifestStore,
        PromotionReviewService,
    )
    from shared.reading_source_lister import RegistryReadingSourceLister
    from shared.reading_source_registry import ReadingSourceRegistry
    from shared.source_map_builder import SourceMapBuilder
    from shared.source_resolver import RegistrySourceResolver

    if config.promotion_mode == "dry_run":
        extractor = DryRunClaimExtractor()
        matcher = DryRunConceptMatcher()
    elif config.promotion_mode == "llm":
        # N518a-only: explicit failure rather than silent fallback. N519
        # implements the LLM-backed adapter behind this same gate.
        raise RuntimeError(
            "LLM mode not yet wired; set NAKAMA_PROMOTION_MODE=dry_run or wait for N519"
        )
    else:
        raise RuntimeError(
            f"Unknown NAKAMA_PROMOTION_MODE={config.promotion_mode!r}; expected 'dry_run' or 'llm'"
        )

    registry = ReadingSourceRegistry(vault_root=config.vault_root)
    blob_loader = VaultBlobLoader(vault_root=config.vault_root)
    source_resolver = RegistrySourceResolver(registry=registry)
    source_lister = RegistryReadingSourceLister(
        registry=registry,
        inbox_root=config.vault_root / "Inbox" / "kb",
        books_root=config.vault_root / "data" / "books",
    )
    kb_index = VaultKBConceptIndex(
        concepts_root=config.vault_root / "KB" / "Wiki" / "Concepts",
    )

    preflight = PromotionPreflight(blob_loader=blob_loader)
    builder = SourceMapBuilder(blob_loader=blob_loader)
    concept_engine = ConceptPromotionEngine()
    commit_service = PromotionCommitService()
    manifest_store = FilesystemManifestStore(config.manifest_root)

    review_service = PromotionReviewService(
        manifest_store=manifest_store,
        preflight=preflight,
        builder=builder,
        concept_engine=concept_engine,
        commit_service=commit_service,
        extractor=extractor,
        matcher=matcher,
        kb_index=kb_index,
        source_lister=source_lister,
        source_resolver=source_resolver,
    )
    promotion_review.set_service(review_service)

    writing_assist_service = writing_assist._build_default_service(
        package_root=config.reading_context_package_root,
    )
    writing_assist.set_service(writing_assist_service)

    _logger.info(
        "promotion surfaces wired",
        extra={
            "category": "promotion_wiring_ready",
            "mode": config.promotion_mode,
            "vault_root": str(config.vault_root),
            "manifest_root": str(config.manifest_root),
            "package_root": str(config.reading_context_package_root),
        },
    )


@asynccontextmanager
async def _lifespan(app_: FastAPI):
    """FastAPI lifespan that wires ADR-024 promotion surfaces at startup.

    Honours ``DISABLE_ROBIN=1`` (mirrors the existing routes-level guard
    below) — when set, the promotion services are NOT constructed and the
    routers fall through to their 503 default. The ``/`` redirect path
    elsewhere still works.

    Startup failures (missing ``NAKAMA_VAULT_ROOT``, unknown promotion
    mode) propagate as ``RuntimeError`` so uvicorn / systemd surface the
    crash to the operator (W4) — silent fallback would mask the misconfig.
    """
    if not os.getenv("DISABLE_ROBIN"):
        config = _load_promotion_wiring_config()
        _wire_promotion_surfaces(config)
    yield
    # No teardown wired in N518a — services hold no per-request state.


app = FastAPI(docs_url=None, redoc_url=None, lifespan=_lifespan)

# Reader CSP must be installed BEFORE routes so middleware wraps everything.
add_csp_middleware(app)

app.include_router(auth.router)
app.include_router(bridge.router)
app.include_router(bridge.page_router)
app.include_router(bridge_zoro.page_router)
app.include_router(repurpose.page_router)
# Franky /healthz must be mounted unconditionally — UptimeRobot probes this regardless of
# DISABLE_ROBIN or any other feature flag (ADR-007 §2).
app.include_router(franky.router)
app.include_router(franky.page_router)

# /static must mount unconditionally — /projects/{slug} (issue #458) ships with
# Robin disabled (VPS) too, and pulls /static/projects/{tokens,review}.css/js.
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    app.mount(
        "/static",
        StaticFiles(directory=str(_static_dir)),
        name="static",
    )

# Robin（KB ingest + reader）僅本機執行，VPS 設 DISABLE_ROBIN=1 跳過
if not os.getenv("DISABLE_ROBIN"):
    from thousand_sunny.routers import books, robin

    app.include_router(robin.router)
    app.include_router(books.router)

    # foliate-js must be served from the same origin as /books/* so CSP
    # ``script-src 'self'`` allows it. Mount the vendored submodule under
    # /vendor/foliate-js/ as static files. Missing dir (fresh checkout
    # forgot ``git submodule update --init``) → skip the mount and log;
    # the reader page will fail to load JS but /books library still works.
    _foliate_dir = Path(__file__).resolve().parent.parent / "vendor" / "foliate-js"
    if _foliate_dir.is_dir():
        app.mount(
            "/vendor/foliate-js",
            StaticFiles(directory=str(_foliate_dir)),
            name="foliate-js",
        )
else:

    @app.get("/")
    async def root_redirect():
        return RedirectResponse("/brook/chat", status_code=302)


app.include_router(zoro.router)
app.include_router(brook.router)
app.include_router(projects.router)
app.include_router(projects.page_router)

# Promotion Review UI (ADR-024 Slice 8 / issue #516). Production service
# wiring lives in the ``_lifespan`` context manager above (N518a / #540).
# Without DISABLE_ROBIN the lifespan calls ``set_service`` so requests are
# served. With DISABLE_ROBIN=1 the lifespan skips wiring and the routes
# fall through to their 503 default (which the VPS deployment expects).
# Tests reload the module to inject a fake service.
app.include_router(promotion_review.router)

# Writing Assist scaffold (ADR-024 Slice 9 / issue #517). Same dependency-
# injection pattern as #516 — service wired by ``_lifespan`` above. The
# route NEVER composes prose — only renders scaffold structure; the surface
# enforces W1-W7 no-ghostwriting invariants.
app.include_router(writing_assist.router)
