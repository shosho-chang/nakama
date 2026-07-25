"""Promotion composition root（組裝工廠）— ADR-052.

Robin 擁有 Source Promotion 的 domain logic，**也擁有它的組裝成本**。
ADR-024 時代這 ~120 行 env → adapter → service 的組裝知識住在
``thousand_sunny/promotion_wiring.py``（presentation layer）——結果是
``PromotionReviewService`` 的有效 interface 比它的 implementation 還寬：
呼叫者必須知道 11+ 個 collaborator 的建構順序。CLI 或未來 agent 想重用
service 只能複製整段 wiring，違反 Robin CONTEXT.md 的
「CLI and future agents must be able to reuse the same Robin/shared service」。

本模組把組裝收回 Robin context。呼叫端（Sunny lifespan、CLI、agent）只需：

    config = load_promotion_config()
    service = build_promotion_review_service(config)

Env boundary（W6，承襲自 promotion_wiring）：所有 ``os.getenv`` 讀取只發生在
:func:`load_promotion_config`；adapter 一律經建構子收 resolved 值，
絕不自行讀 env。

Import 成本紀律：重量級協作者（sqlite registry、schema 解析）的 import 收在
:func:`build_promotion_review_service` 函式體內 — ``DISABLE_ROBIN=1`` 時
presentation 層 import 本模組不觸發 promotion pipeline 的載入成本。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from shared.config import get_vault_path
from shared.log import get_logger

if TYPE_CHECKING:
    from agents.robin.promotion.promotion_review_service import PromotionReviewService

_logger = get_logger("nakama.robin.promotion.factory")


@dataclass(frozen=True)
class PromotionConfig:
    """Resolved env-driven config for the ADR-024 promotion surfaces.

    All env reads happen in :func:`load_promotion_config`（W6 boundary）;
    adapter classes themselves never call ``os.getenv``.

    Frozen so accidental mutation after load can't invalidate the wiring
    contract mid-request.
    """

    vault_root: Path
    manifest_root: Path
    reading_context_package_root: Path
    promotion_mode: str  # "dry_run" | "llm"


def load_promotion_config() -> PromotionConfig:
    """Read vault + ``NAKAMA_*`` env vars + apply documented defaults.

    Vault path resolves via :func:`shared.config.get_vault_path` — the
    canonical, repo-wide resolver (``VAULT_PATH`` env override → else
    ``config.yaml`` ``vault_path``). Reading ``VAULT_PATH`` env *only* here was
    a bug: the VPS keeps ``vault_path`` in ``config.yaml`` and leaves the env
    unset, so this lone bypass of config.yaml crashed startup with a 502.

    Optional:
    - ``NAKAMA_PROMOTION_MANIFEST_ROOT`` (default ``{vault}/.promotion-manifests``)
    - ``NAKAMA_READING_CONTEXT_PACKAGE_ROOT`` (default ``{vault}/.reading-context-packages``)
    - ``NAKAMA_PROMOTION_MODE`` (default ``"dry_run"``)

    Raises ``RuntimeError`` when the vault is unresolvable (neither env nor
    config.yaml provides it) — startup must surface bad config loudly so
    operator visibility is preserved (W4 / brief §6 boundary 7).
    """
    try:
        vault_root = get_vault_path()
    except (KeyError, FileNotFoundError) as exc:
        raise RuntimeError(
            "Vault path unresolved: set vault_path in config.yaml or VAULT_PATH in .env "
            "(or set DISABLE_ROBIN=1 to skip Robin/promotion wiring)."
        ) from exc
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
    return PromotionConfig(
        vault_root=vault_root,
        manifest_root=manifest_root,
        reading_context_package_root=package_root,
        promotion_mode=mode,
    )


def build_promotion_review_service(config: PromotionConfig) -> "PromotionReviewService":
    """Construct the fully-wired :class:`PromotionReviewService`.

    One call replaces the 9-service collaborator graph assembly that every
    caller previously duplicated. Mode branching:

    - ``dry_run``（N518b）：deterministic dry-run extractor / matchers —
      end-to-end against fixture / vault data without any LLM call.
    - ``llm``（N519）：LLM-backed claim extraction for ebook /
      inbox_document; concept matcher + entity pipeline stay on dry-run /
      video bodies until later slices swap them independently. Safe because
      Promotion Review is HITL — nothing is written to KB until 修修
      approves each item.

    Raises ``RuntimeError`` for unknown ``promotion_mode``.
    """
    # Imports are local so the cost (sqlite-backed registry init, schema
    # parsing) stays out of cold-start when ``DISABLE_ROBIN=1``.
    from agents.robin.promotion.concept_promotion_engine import ConceptPromotionEngine
    from agents.robin.promotion.dry_run_entity_matcher import DryRunEntityMatcher
    from agents.robin.promotion.dry_run_extractor import DryRunClaimExtractor
    from agents.robin.promotion.dry_run_matcher import DryRunConceptMatcher
    from agents.robin.promotion.entity_promotion_engine import EntityPromotionEngine
    from agents.robin.promotion.kb_concept_index_default import VaultKBConceptIndex
    from agents.robin.promotion.kb_entity_index_default import VaultKBEntityIndex
    from agents.robin.promotion.promotion_commit import PromotionCommitService
    from agents.robin.promotion.promotion_preflight import PromotionPreflight
    from agents.robin.promotion.promotion_review_service import (
        FilesystemManifestStore,
        PromotionReviewService,
    )
    from agents.robin.promotion.source_map_builder import SourceMapBuilder
    from agents.robin.promotion.source_resolver import RegistrySourceResolver
    from agents.robin.promotion.video_speaker_entity_extractor import (
        VideoSpeakerEntityExtractor,
    )
    from shared.blob_loader import VaultBlobLoader
    from shared.book_storage import books_root as _books_root
    from shared.reading_source_lister import RegistryReadingSourceLister
    from shared.reading_source_registry import ReadingSourceRegistry

    if config.promotion_mode == "dry_run":
        extractor = DryRunClaimExtractor()
        matcher = DryRunConceptMatcher()
        # ADR-034 v2 PR4 — entity pipeline always uses dry-run placeholders
        # in this branch. When LLM-backed entity matcher / NER extractor
        # lands, the "llm" branch below can swap these out independently of
        # the concept side.
        # ADR-035 PR4 — VideoSpeakerEntityExtractor surfaces speaker chips
        # on youtube_video sources as Person EntityCandidates. Returns []
        # for ebook / inbox_document, so behavior on those kinds is
        # identical to DryRunEntityExtractor until LLM-backed NER lands.
        # When that LLM extractor arrives, compose the two (video path +
        # LLM path) rather than replacing this one.
        entity_extractor = VideoSpeakerEntityExtractor()
        entity_matcher = DryRunEntityMatcher()
    elif config.promotion_mode == "llm":
        # N519 — LLM-backed claim extraction for ebook / inbox_document. The
        # concept matcher + entity pipeline stay on their dry-run / video bodies
        # for now (swapped in independently by later slices, per the dry_run
        # branch comments above).
        from agents.robin.source_map_extractor import LlmClaimExtractor  # noqa: PLC0415

        extractor = LlmClaimExtractor()
        matcher = DryRunConceptMatcher()
        entity_extractor = VideoSpeakerEntityExtractor()
        entity_matcher = DryRunEntityMatcher()
    else:
        raise RuntimeError(
            f"Unknown NAKAMA_PROMOTION_MODE={config.promotion_mode!r}; expected 'dry_run' or 'llm'"
        )

    registry = ReadingSourceRegistry(vault_root=config.vault_root)
    # Books live outside the vault (cwd-relative or NAKAMA_BOOKS_DIR) —
    # see shared.book_storage docstring for the rationale (F06 fix
    # 2026-05-10). Both the lister (which enumerates {book_id}/ subdirs)
    # and the blob loader (which resolves data/books/{book_id}/... path
    # strings emitted by ReadingSourceRegistry) must source the same
    # books root, otherwise list-view + variant-read see different trees.
    books_root_path = _books_root()
    blob_loader = VaultBlobLoader(
        vault_root=config.vault_root,
        books_root=books_root_path,
    )
    source_resolver = RegistrySourceResolver(registry=registry)
    source_lister = RegistryReadingSourceLister(
        registry=registry,
        inbox_root=config.vault_root / "Inbox" / "web",
        books_root=books_root_path,
        watchlist_youtube_root=config.vault_root / "Watchlist" / "youtube",
    )
    kb_index = VaultKBConceptIndex(
        concepts_root=config.vault_root / "KB" / "Wiki" / "Concepts",
    )
    kb_entity_index = VaultKBEntityIndex(
        entities_root=config.vault_root / "KB" / "Wiki" / "Entities",
    )

    preflight = PromotionPreflight(blob_loader=blob_loader)
    builder = SourceMapBuilder(blob_loader=blob_loader)
    concept_engine = ConceptPromotionEngine()
    entity_engine = EntityPromotionEngine()
    commit_service = PromotionCommitService()
    manifest_store = FilesystemManifestStore(config.manifest_root)

    service = PromotionReviewService(
        manifest_store=manifest_store,
        preflight=preflight,
        builder=builder,
        concept_engine=concept_engine,
        commit_service=commit_service,
        # Placeholder-only pipeline until N519 — committing the dry-run
        # extractor's claims would pollute the vault, so the commit path is
        # disabled in every non-LLM mode.
        commit_enabled=(config.promotion_mode == "llm"),
        extractor=extractor,
        matcher=matcher,
        kb_index=kb_index,
        source_lister=source_lister,
        source_resolver=source_resolver,
        entity_engine=entity_engine,
        entity_extractor=entity_extractor,
        entity_matcher=entity_matcher,
        kb_entity_index=kb_entity_index,
    )
    _logger.info(
        "promotion review service built",
        extra={
            "category": "promotion_factory_built",
            "mode": config.promotion_mode,
            "vault_root": str(config.vault_root),
            "manifest_root": str(config.manifest_root),
        },
    )
    return service
