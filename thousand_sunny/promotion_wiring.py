"""ADR-024 promotion-surface wiring (extracted from ``thousand_sunny.app``).

This module owns the env → adapter construction → service injection path
for the Promotion Review (#516) and Writing Assist (#517) surfaces.
``thousand_sunny.app`` calls :func:`load_promotion_wiring_config` and
:func:`wire_promotion_surfaces` from its FastAPI ``lifespan`` so cold-start
remains a single function call from the top-level module's perspective.

**Why this lives here, not in ``app.py``** (N518b C2 carry-over): the
wiring concern grew to ~150 LOC during N518a. Keeping it inside ``app.py``
made the file hard to read and impossible to test in isolation without
spinning up the entire FastAPI lifespan. Moving it to its own module:

- gives the wiring its own home for unit testing the config loader
  + service constructor independently of the lifespan context manager;
- makes ``app.py`` focus on routing + middleware, matching its name;
- mirrors the pattern other Thousand Sunny services use
  (e.g. ``thousand_sunny.helpers``).

Public surface (no leading underscore — these are intended to be imported
from ``app.py`` and exercised by tests):

- :class:`PromotionWiringConfig` — frozen dataclass with the resolved env
  values.
- :func:`load_promotion_wiring_config` — reads env + applies defaults.
- :func:`wire_promotion_surfaces` — constructs adapters + services and
  injects them via ``set_service`` on both routers.

Boundary 5 (W6 / brief §6): all ``os.environ`` / ``os.getenv`` reads happen
inside :func:`load_promotion_wiring_config`. Adapters NEVER call
``os.getenv`` themselves; they receive resolved values via constructor
arguments. The lifespan calls the loader once at startup; tests can call
the loader directly with a monkeypatched env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from shared.log import get_logger
from thousand_sunny.routers import promotion_review, writing_assist

_logger = get_logger("nakama.web.promotion_wiring")


@dataclass(frozen=True)
class PromotionWiringConfig:
    """Resolved env-driven config for the ADR-024 promotion surfaces.

    All env reads happen in :func:`load_promotion_wiring_config` (W6 /
    N518 brief §6 boundary 5); adapter classes themselves never call
    ``os.getenv``.

    Frozen so accidental mutation in the lifespan body can't invalidate
    the wiring contract mid-request.
    """

    vault_root: Path
    manifest_root: Path
    reading_context_package_root: Path
    promotion_mode: str  # "dry_run" | "llm"


def load_promotion_wiring_config() -> PromotionWiringConfig:
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
    # TODO(N518c-or-decision): confirm with 修修 whether the manifest +
    # reading-context-package roots should remain under the vault
    # (current default: {vault}/.promotion-manifests and
    # {vault}/.reading-context-packages) or move to a sibling
    # ``data/promotion-manifests`` / ``data/reading-context-packages``
    # alongside ``data/books``. Vault-local keeps everything in one tree
    # (good for backup); ``data/`` keeps non-content state out of the
    # Obsidian sync surface (good for index hygiene). Surfaced as an
    # open question in PR #540 — do not change defaults without an
    # explicit decision.
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
    return PromotionWiringConfig(
        vault_root=vault_root,
        manifest_root=manifest_root,
        reading_context_package_root=package_root,
        promotion_mode=mode,
    )


def wire_promotion_surfaces(config: PromotionWiringConfig) -> None:
    """Construct adapters + services and inject them into both routers.

    Called from the FastAPI lifespan when Robin is enabled. After this
    helper returns, both ``promotion_review`` and ``writing_assist``
    routers have a wired service and will return 200 (not 503).

    N518b: claim extractor + concept matcher are the deterministic
    dry-run bodies in ``shared.dry_run_extractor`` /
    ``shared.dry_run_matcher``. ``POST /promotion-review/.../start`` runs
    end-to-end against fixture / vault data without any LLM call.

    Raises ``RuntimeError`` for ``NAKAMA_PROMOTION_MODE=llm`` — the LLM
    adapter is N519, not N518.
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
        # Boundary: explicit failure rather than silent fallback. N519
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
