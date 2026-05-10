"""Promotion Review Service facade (ADR-024 Slice 8 / issue #516).

Composes #511 ``PromotionPreflight`` + #513 ``SourceMapBuilder`` + #514
``ConceptPromotionEngine`` + #515 ``PromotionCommitService`` plus an
injectable ``ManifestStore`` for per-source manifest persistence.

Slice 8 ownership boundary (per ADR-024 §Decision):

- Robin / shared owns domain logic (preflight, build, propose, commit).
- Thousand Sunny owns presentation + human checkpoint UI.
- This service facade is the single seam routes must call. Routes never
  import #511/#513/#514/#515 directly — that is U1 in the Brief.

Hard invariants (Brief §4.4):

- U5  : Service NEVER imports ``shared.book_storage``, ``fastapi``,
        ``thousand_sunny.*``, ``agents.*``, LLM clients (subprocess gate
        T11 / T12 in tests).
- U6  : ``commit_approved`` errors are recorded as ``CommitOutcome.error``
        AND the failing ``CommitBatch`` is appended to the manifest with
        ``promotion_status="failed"``; service does NOT swallow.

LLM swappability: ``extractor`` / ``matcher`` / ``kb_index`` are protocols
declared by #513 / #514. Production wiring injects LLM-backed
implementations; tests inject deterministic in-memory fakes.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Protocol

from shared.concept_promotion_engine import ConceptMatcher, ConceptPromotionEngine, KBConceptIndex
from shared.log import get_logger
from shared.promotion_commit import PromotionCommitService
from shared.promotion_preflight import PromotionPreflight
from shared.schemas.preflight_report import PreflightReport
from shared.schemas.promotion_commit import CommitOutcome
from shared.schemas.promotion_manifest import (
    HumanDecision,
    HumanDecisionKind,
    ManifestStatus,
    PromotionManifest,
    RecommenderMetadata,
    now_iso_utc,
)
from shared.schemas.promotion_review_state import ManifestUiStatus, PromotionReviewState
from shared.schemas.reading_source import ReadingSource
from shared.source_map_builder import ClaimExtractor, SourceMapBuilder

_logger = get_logger("nakama.shared.promotion_review_service")


# Documented failure modes for cross-service calls. Narrow tuple so
# programmer errors (TypeError, AttributeError, KeyboardInterrupt)
# propagate per #511 F5 lesson.
_SERVICE_FAILURES: tuple[type[BaseException], ...] = (OSError, ValueError, KeyError)
"""``ManifestStore`` filesystem backends raise ``OSError`` on IO,
``ValueError`` on path validation. Mapping-backed stores (used in tests)
may raise ``KeyError`` on missing keys. Other exception types propagate
unchanged."""

_PREFLIGHT_PROCEED_ACTIONS = {"proceed_full_promotion", "proceed_with_warnings"}
"""``PreflightAction`` values that gate a source as eligible for the
review list. Other values (``defer`` / ``skip`` / ``annotation_only_sync``)
are filtered out by ``list_pending``."""

_PREFLIGHT_SUMMARY_MAX_CHARS = 200
"""Brief §4.2 schema sketch: ``preflight_summary`` ≤ 200 chars synopsis."""


# ── ManifestStore protocol + filesystem default ──────────────────────────────


class ManifestStore(Protocol):
    """Per-source manifest persistence abstraction.

    Tests inject in-memory dict-backed stores; production wires
    :class:`FilesystemManifestStore` rooted at a configured directory.
    """

    def load(self, source_id: str) -> PromotionManifest | None:
        """Return the manifest for ``source_id`` or ``None`` if absent."""
        ...

    def save(self, manifest: PromotionManifest) -> None:
        """Persist ``manifest`` keyed by ``manifest.source_id``."""
        ...

    def list_source_ids(self) -> list[str]:
        """Return all stored ``source_id`` strings (decoded). Order is
        unspecified; caller sorts as needed."""
        ...


def _encode_filename(source_id: str) -> str:
    """Encode ``source_id`` for use as a filesystem filename.

    ``source_id`` contains ``:`` and ``/`` (e.g. ``ebook:abc123`` or
    ``inbox:Inbox/kb/foo.md``); base64url with stripped padding produces a
    safe single-segment name across Windows + POSIX.
    """
    return base64.urlsafe_b64encode(source_id.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_filename(encoded: str) -> str:
    """Inverse of :func:`_encode_filename`. Re-pads the base64url string
    before decoding."""
    padded = encoded + "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


class FilesystemManifestStore:
    """Default filesystem-backed ``ManifestStore`` implementation.

    Manifests are written as ``{base64url(source_id)}.json`` files under
    ``manifest_root`` using ``model_dump_json()`` / ``model_validate_json()``
    (per Brief §6 boundary 10 — Pydantic, not YAML, not
    ``shared.utils.extract_frontmatter``).
    """

    def __init__(self, manifest_root: Path) -> None:
        self._root = Path(manifest_root)

    def load(self, source_id: str) -> PromotionManifest | None:
        path = self._path_for(source_id)
        if not path.is_file():
            return None
        return PromotionManifest.model_validate_json(path.read_bytes())

    def save(self, manifest: PromotionManifest) -> None:
        path = self._path_for(manifest.source_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic-ish write: write to sibling .tmp then os.replace.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    def list_source_ids(self) -> list[str]:
        if not self._root.is_dir():
            return []
        out: list[str] = []
        for entry in self._root.iterdir():
            if not entry.is_file() or entry.suffix != ".json":
                continue
            try:
                out.append(_decode_filename(entry.stem))
            except (ValueError, UnicodeDecodeError):
                # Skip non-base64url filenames silently — they are not ours.
                continue
        return out

    def _path_for(self, source_id: str) -> Path:
        return self._root / f"{_encode_filename(source_id)}.json"


# ── Reading-source list provider ─────────────────────────────────────────────


class ReadingSourceLister(Protocol):
    """Strategy for enumerating Reading Sources eligible for the list view.

    The Reading Source registry (#509) only resolves a single key at a time
    by design. The lister abstraction lets callers decide enumeration
    policy: production wiring may walk ``Inbox/kb/`` + ``books`` table;
    tests inject a deterministic in-memory list. Slice 8 ships ONLY the
    protocol — no concrete enumerator.
    """

    def list_sources(self) -> list[ReadingSource]:
        """Return all candidate Reading Sources for review listing."""
        ...


# ── Service facade ───────────────────────────────────────────────────────────


class PromotionReviewService:
    """Service facade composing #511/#513/#514/#515.

    Construction takes:

    - ``manifest_store``: persistence (load / save / list).
    - ``preflight``: #511 inspector (already constructed with blob_loader).
    - ``builder``: #513 source map builder (already constructed with blob_loader).
    - ``concept_engine``: #514 stateless engine.
    - ``commit_service``: #515 service.
    - ``extractor`` / ``matcher`` / ``kb_index``: injected protocols. Tests
      pass deterministic fakes; production wiring passes LLM-backed impls.
    - ``source_lister``: optional. Required for ``list_pending``; without it
      the list view operates only on already-stored manifests (treating each
      stored manifest as a pending entry).

    Service is stateless across method calls — no caching, no enumeration
    spelunking. Each method delegates to upstream services with deterministic
    parameters.
    """

    def __init__(
        self,
        *,
        manifest_store: ManifestStore,
        preflight: PromotionPreflight,
        builder: SourceMapBuilder,
        concept_engine: ConceptPromotionEngine,
        commit_service: PromotionCommitService,
        extractor: ClaimExtractor,
        matcher: ConceptMatcher,
        kb_index: KBConceptIndex,
        recommender_model_name: str = "claude-opus-4-7",
        recommender_model_version: str = "2026-04",
        source_lister: ReadingSourceLister | None = None,
        source_resolver: "SourceResolver | None" = None,
    ) -> None:
        self._manifest_store = manifest_store
        self._preflight = preflight
        self._builder = builder
        self._concept_engine = concept_engine
        self._commit_service = commit_service
        self._extractor = extractor
        self._matcher = matcher
        self._kb_index = kb_index
        self._recommender_model_name = recommender_model_name
        self._recommender_model_version = recommender_model_version
        self._source_lister = source_lister
        self._source_resolver = source_resolver

    # ── Public API ────────────────────────────────────────────────────────

    def list_pending(self) -> list[PromotionReviewState]:
        """Build the list view for all candidate sources.

        Filters to ``preflight_action ∈ {proceed_full_promotion,
        proceed_with_warnings}`` per Brief §1. Sorted by ``source_id`` for
        deterministic test output.

        Without an injected ``source_lister`` the method returns ``[]`` —
        deliberate fail-quiet so tests + tooling that only exercise per-source
        flows do not need to construct a global enumerator. Production wiring
        MUST inject a lister.
        """
        if self._source_lister is None:
            return []

        states: list[PromotionReviewState] = []
        sources = self._source_lister.list_sources()
        for rs in sources:
            try:
                report = self._preflight.run(rs)
            except _SERVICE_FAILURES:
                _logger.warning(
                    "preflight failed during list_pending",
                    extra={
                        "category": "promotion_review_list_preflight_failed",
                        "source_id": rs.source_id,
                    },
                )
                continue
            if report.recommended_action not in _PREFLIGHT_PROCEED_ACTIONS:
                continue
            state = self._build_state(rs, report)
            states.append(state)

        states.sort(key=lambda s: s.source_id)
        return states

    def start_review(self, source_id: str) -> PromotionManifest:
        """Run preflight → builder → engine on ``source_id`` and persist a
        fresh manifest in ``status="needs_review"``.

        Caller must already have a way to resolve ``source_id`` to a
        ``ReadingSource``; this is the ``SourceResolver`` injected at
        construction. Without one, ``start_review`` raises ``ValueError``.

        Raises ``ValueError`` when:
        - no resolver injected
        - resolver returns ``None`` for ``source_id``
        - an existing manifest for ``source_id`` already carries persisted
          ``human_decision``s or ``commit_batches`` (re-running ``/start``
          is treated as destructive; explicit replace flow with
          ``replaces_manifest_id`` is tracked as future-slice work)
        - preflight returns an action ∉ ``{proceed_full_promotion,
          proceed_with_warnings}``
        - builder returns ``error is not None``
        - engine returns ``error is not None``
        """
        if self._source_resolver is None:
            raise ValueError(
                "PromotionReviewService.start_review requires a source_resolver "
                "injected at construction"
            )
        rs = self._source_resolver.resolve(source_id)
        if rs is None:
            raise ValueError(f"source_id={source_id!r} did not resolve to a ReadingSource")

        # Brief §3 labels POST /start as "First-time start review". An existing
        # manifest with recorded decisions or commit batches is preserved
        # state — overwriting it would silently destroy human work and the
        # audit trail. ADR-024's replaces_manifest_id flow is intentionally
        # not wired here; surface a hard error so misclicks / reloads /
        # double-POSTs cannot lose data.
        existing = self._manifest_store.load(source_id)
        if existing is not None and (
            any(item.human_decision is not None for item in existing.items)
            or len(existing.commit_batches) > 0
        ):
            raise ValueError(
                f"start_review would overwrite a manifest with persisted "
                f"human decisions or commit batches for source_id="
                f"{source_id!r}; an explicit re-start / replaces_manifest_id "
                f"flow is not yet implemented (tracked as future-slice work)"
            )

        report = self._preflight.run(rs)
        if report.recommended_action not in _PREFLIGHT_PROCEED_ACTIONS:
            raise ValueError(
                f"source_id={source_id!r} preflight action="
                f"{report.recommended_action!r} is not eligible for promotion review"
            )

        # Builder is invariant-gated: requires has_evidence_track=True (B1).
        # The proceed_* preflight actions guarantee that, but catch the
        # ValueError defensively so we surface a useful error to the caller.
        try:
            build_result = self._builder.build(rs, self._extractor)
        except ValueError as exc:
            raise ValueError(f"source_map build precondition failed: {exc!s}") from exc
        if build_result.error is not None:
            raise ValueError(f"source_map build failed: {build_result.error}")

        promotion_result = self._concept_engine.propose(
            rs, build_result, self._kb_index, self._matcher
        )
        if promotion_result.error is not None:
            raise ValueError(f"concept promotion failed: {promotion_result.error}")

        manifest = self._compose_manifest(
            source_id=source_id,
            source_page_items=list(build_result.items),
            concept_items=list(promotion_result.items),
        )
        self._manifest_store.save(manifest)
        return manifest

    def load_review_session(self, source_id: str) -> PromotionManifest | None:
        """Return the persisted manifest for ``source_id`` or ``None``."""
        return self._manifest_store.load(source_id)

    def record_decision(
        self,
        source_id: str,
        item_id: str,
        decision: HumanDecisionKind,
        note: str | None = None,
        *,
        decided_by: str = "shosho",
    ) -> PromotionManifest:
        """Set ``human_decision`` on ``item_id`` within the manifest for
        ``source_id``. Persists the updated manifest. Returns the updated
        manifest.

        Raises ``ValueError`` when the manifest or item is not found.
        """
        manifest = self._manifest_store.load(source_id)
        if manifest is None:
            raise ValueError(f"no manifest stored for source_id={source_id!r}")

        # Items list is mutable per #512 schema (only inner value-objects are
        # frozen). We rebuild the matching item with a fresh HumanDecision.
        target_index: int | None = None
        for idx, item in enumerate(manifest.items):
            if item.item_id == item_id:
                target_index = idx
                break
        if target_index is None:
            raise ValueError(
                f"item_id={item_id!r} not found in manifest for source_id={source_id!r}"
            )

        target = manifest.items[target_index]
        new_decision = HumanDecision(
            decision=decision,
            decided_at=now_iso_utc(),
            decided_by=decided_by,
            note=note,
        )
        # ``human_decision`` is the only mutable field on review items; assign
        # via Pydantic's standard attribute setter.
        target.human_decision = new_decision

        self._manifest_store.save(manifest)
        return manifest

    def commit_approved(
        self,
        source_id: str,
        batch_id: str,
        vault_root: Path,
    ) -> CommitOutcome:
        """Commit all currently-approved items for ``source_id``.

        Caller responsibilities:
        - has already called ``record_decision`` for the items they want
          committed.
        - ``batch_id`` is unique within the manifest (G7 from #515).

        Returns ``CommitOutcome``. On systemic failure the outcome carries
        ``error != None`` and an empty ``approved_item_ids`` (per #515 F1
        schema invariant); the failing batch is appended to the manifest
        and the manifest's ``status`` updated to reflect the failure.

        U6 invariant: errors are RECORDED into ``manifest.commit_batches``
        and surfaced in the returned outcome — NOT swallowed.
        """
        manifest = self._manifest_store.load(source_id)
        if manifest is None:
            raise ValueError(f"no manifest stored for source_id={source_id!r}")

        approved_ids = [
            item.item_id
            for item in manifest.items
            if item.human_decision is not None and item.human_decision.decision == "approve"
        ]

        # Even when no items are approved we still call the commit service:
        # ``_compute_promotion_status`` returns ``"failed"`` for empty
        # ``item_ids`` (no approved + no deferred + no rejected), so the
        # batch is recorded as failed without short-circuiting here. The
        # CommitOutcome F1-analog invariant fires in the converse direction
        # (``error is not None ⇒ approved=[] AND status=failed``); for the
        # empty-batch path ``error`` stays ``None``.
        outcome = self._commit_service.commit(
            manifest,
            batch_id,
            approved_ids,
            vault_root,
        )

        # Append the batch to the manifest (whether success / partial / failed)
        # and update the manifest's top-level status to match per #512 V11.
        manifest.commit_batches.append(outcome.batch)
        manifest.status = _manifest_status_after_commit(manifest, outcome)

        self._manifest_store.save(manifest)
        return outcome

    def state_for(self, source_id: str) -> PromotionReviewState | None:
        """Build a ``PromotionReviewState`` for one source.

        Returns ``None`` when the source cannot be resolved. Used by the
        review surface header to reuse the same status pill rendering as
        the list view.
        """
        if self._source_resolver is None:
            return None
        rs = self._source_resolver.resolve(source_id)
        if rs is None:
            return None
        try:
            report = self._preflight.run(rs)
        except _SERVICE_FAILURES:
            _logger.warning(
                "preflight failed during state_for",
                extra={
                    "category": "promotion_review_state_preflight_failed",
                    "source_id": source_id,
                },
            )
            return None
        return self._build_state(rs, report)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _build_state(self, rs: ReadingSource, report: PreflightReport) -> PromotionReviewState:
        manifest = self._manifest_store.load(rs.source_id)
        manifest_status: ManifestUiStatus | None = None
        if manifest is not None:
            manifest_status = manifest.status
        summary = _summarize_preflight(report)
        return PromotionReviewState(
            source_id=rs.source_id,
            primary_lang=rs.primary_lang,
            preflight_action=report.recommended_action,
            preflight_summary=summary,
            has_existing_manifest=manifest is not None,
            manifest_status=manifest_status,
        )

    def _compose_manifest(
        self,
        *,
        source_id: str,
        source_page_items,
        concept_items,
    ) -> PromotionManifest:
        manifest_id = f"mfst_{source_id}_{now_iso_utc()}"
        recommender = RecommenderMetadata(
            model_name=self._recommender_model_name,
            model_version=self._recommender_model_version,
            run_params={},
            recommended_at=now_iso_utc(),
        )
        return PromotionManifest(
            schema_version=1,
            manifest_id=manifest_id,
            source_id=source_id,
            created_at=now_iso_utc(),
            status="needs_review",
            replaces_manifest_id=None,
            recommender=recommender,
            items=[*source_page_items, *concept_items],
            commit_batches=[],
            metadata={},
        )


class SourceResolver(Protocol):
    """Strategy for resolving a ``source_id`` string to a ``ReadingSource``.

    Production wiring uses #509's ``ReadingSourceRegistry``; tests inject a
    dict-backed resolver. Slice 8 ships ONLY the protocol.
    """

    def resolve(self, source_id: str) -> ReadingSource | None:
        """Return the ``ReadingSource`` for ``source_id`` or ``None``."""
        ...


# ── Module-level helpers ─────────────────────────────────────────────────────


def _summarize_preflight(report: PreflightReport) -> str:
    """Compose a ≤200 char preflight synopsis for the list view.

    Format: ``"{action} · {chapter_count}ch · {word_count}w[ · risks: ...]"``.
    Truncated to ``_PREFLIGHT_SUMMARY_MAX_CHARS`` with an ellipsis.
    """
    pieces = [
        report.recommended_action,
        f"{report.size.chapter_count}ch",
        f"{report.size.word_count_estimate}w",
    ]
    if report.risks:
        risk_codes = ", ".join(r.code for r in report.risks)
        pieces.append(f"risks: {risk_codes}")
    summary = " · ".join(pieces)
    if len(summary) > _PREFLIGHT_SUMMARY_MAX_CHARS:
        summary = summary[: _PREFLIGHT_SUMMARY_MAX_CHARS - 1] + "…"
    return summary


def _manifest_status_after_commit(
    manifest: PromotionManifest, outcome: CommitOutcome
) -> ManifestStatus:
    """Compute the manifest-level status after appending ``outcome.batch``.

    Mirrors #515's ``CommitBatch.promotion_status`` semantics but lifted
    to manifest-level granularity:

    - Any failed batch in history ⇒ ``failed`` (locks recovery posture).
    - All items decided AND latest batch ``complete`` ⇒ ``complete``.
    - Otherwise ⇒ ``partial``.
    """
    if outcome.batch.promotion_status == "failed" or any(
        b.promotion_status == "failed" for b in manifest.commit_batches
    ):
        return "failed"
    all_decided = all(item.human_decision is not None for item in manifest.items)
    if all_decided and outcome.batch.promotion_status == "complete":
        return "complete"
    return "partial"
