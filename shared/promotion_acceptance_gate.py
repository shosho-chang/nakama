"""Promotion Acceptance Gate (ADR-024 Slice 7 / issue #515).

Deterministic gate that validates one ``ReviewItem`` (per #512 schema)
against a vault snapshot before ``PromotionCommitService`` writes it.
Returns an ``AcceptanceResult`` value-object — gate NEVER writes; gate
NEVER mutates the item.

Defense-in-depth: the gate re-validates several invariants already
enforced by upstream schemas (e.g. #512 V10 ``CanonicalMatch.match_basis``
⇒ ``matched_concept_path`` rule). This is intentional — the gate is a
second line of defense that doesn't trust upstream construction.

Boundaries (Brief §6 / G9):
- Gate NEVER imports ``shared.book_storage``, ``fastapi``,
  ``thousand_sunny.*``, ``agents.*``, or LLM clients (subprocess gate
  tests assert).
- Gate NEVER parses ``ReadingSource.source_id`` (#509 N3 contract).
- Gate NEVER writes — read-only validation. Path resolution uses
  ``Path.resolve()`` with ``strict=False`` so non-existent target
  paths can still be checked for traversal.
- Gate uses narrow exception tuples at every boundary call (per #511 F5
  lesson). Bare ``except Exception`` is forbidden.

Hard invariants checked (Brief §4.3 G1-G7):

- G1 / G2 : ``target_kb_path`` non-empty, no ``..`` traversal, resolves under ``vault_root``.
- G4      : pre-write hash matches recorded prior batch ``after_hash`` (update path).
- G5      : ``EvidenceAnchor.locator`` non-empty (#512 V1 also enforces, defense in depth).
- G6      : Concept items where ``canonical_match.match_basis != "none"`` MUST have
            ``matched_concept_path`` set (#512 V10 cross-check, defense in depth).
- G7      : Within one ``commit()`` invocation, two items resolving to the same target
            path raise ``duplicate_target_in_batch`` on the second.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from shared.log import get_logger
from shared.schemas.promotion_commit import (
    AcceptanceFinding,
    AcceptanceFindingCode,
    AcceptanceResult,
)
from shared.schemas.promotion_manifest import (
    CommitBatch,
    ConceptReviewItem,
    PromotionManifest,
    SourcePageReviewItem,
)

_logger = get_logger("nakama.shared.promotion_acceptance_gate")


# Documented failure modes for adapter calls — narrow tuples so programmer
# errors (TypeError, AttributeError) propagate (per #511 F5 lesson).
_ADAPTER_FAILURES: tuple[type[BaseException], ...] = (OSError, ValueError, KeyError)
"""Adapter ``hash_file`` / ``read_file`` may raise OSError (filesystem
issues) / ValueError (path validation) / KeyError (mapping-backed adapters
in tests). TypeError / AttributeError signal programmer errors and
propagate to surface test feedback."""


class _GateAdapter(Protocol):
    """Subset of ``KbWriteAdapter`` the gate relies on. Read-only methods
    only — gate NEVER writes."""

    def read_file(self, vault_path: str) -> bytes | None: ...
    def hash_file(self, vault_path: str) -> str | None: ...


class AcceptanceGate:
    """Deterministic acceptance gate. Construct once per ``commit()`` call;
    gate state (``_seen_targets``) is per-instance to enforce
    ``duplicate_target_in_batch`` (G7) within one commit invocation.

    Usage:

        gate = AcceptanceGate()
        for item in items_to_commit:
            result = gate.validate(item, vault_root, manifest, write_adapter)
            if result.passed:
                # write item
                ...
    """

    def __init__(self) -> None:
        # Track resolved target paths within this gate's lifetime to detect
        # G7 duplicate_target_in_batch. Cleared by caller via re-construction
        # for each new commit batch (the service does this).
        self._seen_targets: set[str] = set()

    def validate(
        self,
        item: SourcePageReviewItem | ConceptReviewItem,
        vault_root: Path,
        manifest: PromotionManifest,
        write_adapter: _GateAdapter,
    ) -> AcceptanceResult:
        """Validate one item against the vault snapshot.

        Returns ``AcceptanceResult(passed: bool, findings: [...])``.
        ``passed=False`` ⇔ at least one ``error``-severity finding present.

        Side effect: when ``target_kb_path`` resolves cleanly, the resolved
        path is recorded in ``self._seen_targets`` so subsequent items with
        the same target raise ``duplicate_target_in_batch``.
        """
        findings: list[AcceptanceFinding] = []

        # G2 / G1 — target_kb_path presence and vault containment.
        target = item.target_kb_path if isinstance(item, SourcePageReviewItem) else None
        if isinstance(item, ConceptReviewItem):
            # Concept items: derive target_kb_path from canonical_match if
            # set (update path) or from a synthesized concept slug. The
            # commit service computes the final path; the gate accepts the
            # service-computed value via item.target_kb_path-equivalent
            # passed through ConceptReviewItem.
            cm = item.canonical_match
            if cm is not None and cm.matched_concept_path:
                target = cm.matched_concept_path
            else:
                # No matched_concept_path — service must synthesize. We surface
                # the missing path as a finding so the service skips this item.
                target = None

        if not target or not target.strip():
            findings.append(
                _finding(
                    "target_kb_path_missing",
                    "error",
                    f"item_id={item.item_id!r} has no target_kb_path",
                )
            )
        else:
            # G1 — Traversal check (literal '..' segment in unresolved path).
            if _has_traversal_segment(target):
                findings.append(
                    _finding(
                        "target_kb_path_traversal",
                        "error",
                        f"item_id={item.item_id!r} target_kb_path={target!r} "
                        f"contains '..' traversal segment",
                    )
                )
            else:
                resolved_target, escaped = _resolve_under_vault(vault_root, target)
                if escaped:
                    findings.append(
                        _finding(
                            "target_kb_path_outside_vault",
                            "error",
                            f"item_id={item.item_id!r} target_kb_path={target!r} "
                            f"resolves outside vault_root={str(vault_root)!r}",
                        )
                    )
                else:
                    # G7 — duplicate target within batch.
                    resolved_str = str(resolved_target)
                    if resolved_str in self._seen_targets:
                        findings.append(
                            _finding(
                                "duplicate_target_in_batch",
                                "error",
                                f"item_id={item.item_id!r} target_kb_path={target!r} "
                                f"already resolved by prior item in this batch",
                            )
                        )
                    else:
                        self._seen_targets.add(resolved_str)

                    # G4 — pre-write hash match against prior batch's after_hash.
                    mismatch = _check_prior_hash(item.item_id, target, manifest, write_adapter)
                    if mismatch is not None:
                        findings.append(mismatch)

        # G5 — Evidence anchor locator/excerpt non-empty.
        for anchor in item.evidence:
            if not anchor.locator or not anchor.locator.strip():
                findings.append(
                    _finding(
                        "evidence_anchor_locator_invalid",
                        "error",
                        f"item_id={item.item_id!r} has EvidenceAnchor with empty locator",
                    )
                )
            if not anchor.excerpt or not anchor.excerpt.strip():
                findings.append(
                    _finding(
                        "evidence_anchor_excerpt_empty",
                        "error",
                        f"item_id={item.item_id!r} has EvidenceAnchor with empty excerpt",
                    )
                )

        # G3 — human_decision presence + approve.
        if item.human_decision is None:
            findings.append(
                _finding(
                    "human_decision_missing",
                    "error",
                    f"item_id={item.item_id!r} has no human_decision",
                )
            )
        elif item.human_decision.decision != "approve":
            findings.append(
                _finding(
                    "human_decision_not_approve",
                    "error",
                    f"item_id={item.item_id!r} human_decision.decision="
                    f"{item.human_decision.decision!r} (expected 'approve')",
                )
            )

        # G6 — Concept canonical match defense in depth (#512 V10).
        if isinstance(item, ConceptReviewItem) and item.canonical_match is not None:
            cm = item.canonical_match
            if cm.match_basis != "none" and not cm.matched_concept_path:
                findings.append(
                    _finding(
                        "concept_canonical_match_path_invalid",
                        "error",
                        f"item_id={item.item_id!r} canonical_match.match_basis="
                        f"{cm.match_basis!r} requires matched_concept_path "
                        "(defense in depth for #512 V10)",
                    )
                )

        passed = not any(f.severity == "error" for f in findings)
        return AcceptanceResult(item_id=item.item_id, passed=passed, findings=findings)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _finding(code: AcceptanceFindingCode, severity: str, message: str) -> AcceptanceFinding:
    """Construct an ``AcceptanceFinding`` with frozen-dataclass safety."""
    return AcceptanceFinding(code=code, severity=severity, message=message)  # type: ignore[arg-type]


def _has_traversal_segment(path: str) -> bool:
    """True iff the unresolved path contains a literal ``..`` segment.

    We deliberately check before ``Path.resolve()`` because resolve()
    silently collapses ``..`` segments inside vault_root (e.g.
    ``vault/foo/../bar`` → ``vault/bar`` resolves cleanly), so the
    pre-resolve check is the primary G1 enforcement; the post-resolve
    containment check is the secondary line.
    """
    # Use os.sep + posix sep splits to be platform-agnostic; the test fixtures
    # may use forward slashes regardless of OS.
    parts = path.replace("\\", "/").split("/")
    return any(part == ".." for part in parts)


def _resolve_under_vault(vault_root: Path, target: str) -> tuple[Path, bool]:
    """Resolve ``target`` under ``vault_root``. Returns
    ``(resolved_path, escaped: bool)``.

    ``escaped=True`` when the resolved target is NOT under vault_root —
    either because the relative path resolves outside (rare on Linux but
    possible on Windows with junctions), or because the target was given as
    an absolute path outside the vault.
    """
    target_path = Path(target)
    if target_path.is_absolute():
        # Absolute targets must be within vault_root after resolve.
        try:
            resolved = target_path.resolve(strict=False)
        except _ADAPTER_FAILURES:
            return target_path, True
    else:
        try:
            resolved = (vault_root / target_path).resolve(strict=False)
        except _ADAPTER_FAILURES:
            return vault_root / target_path, True

    try:
        vault_resolved = vault_root.resolve(strict=False)
    except _ADAPTER_FAILURES:
        return resolved, True

    # Path.is_relative_to (3.9+) checks containment after resolve.
    try:
        escaped = not _is_within(resolved, vault_resolved)
    except ValueError:
        escaped = True
    return resolved, escaped


def _is_within(path: Path, root: Path) -> bool:
    """True iff ``path`` is ``root`` or a descendant. Uses commonpath so
    Windows-style junctions are handled the same way as POSIX paths."""
    try:
        common = os.path.commonpath([str(path), str(root)])
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(str(root))


def _check_prior_hash(
    item_id: str,
    target_kb_path: str,
    manifest: PromotionManifest,
    write_adapter: _GateAdapter,
) -> AcceptanceFinding | None:
    """G4 — Pre-write hash check.

    Walk ``manifest.commit_batches`` from most-recent backward; if a prior
    batch recorded a ``TouchedFile`` for the same path with operation in
    {create, update} and ``after_hash`` set, compare current hash against
    that recorded after_hash. Mismatch ⇒ finding.

    Missing-file path with prior after_hash recorded → mismatch (someone
    deleted the file out from under us).

    No prior batch for this path → no check (fresh write).
    """
    prior_after_hash = _last_recorded_after_hash(manifest.commit_batches, target_kb_path)
    if prior_after_hash is None:
        return None
    try:
        current = write_adapter.hash_file(target_kb_path)
    except _ADAPTER_FAILURES as exc:
        _logger.warning(
            "gate hash_file failed",
            extra={
                "category": "gate_hash_file_failed",
                "item_id": item_id,
                "target_kb_path": target_kb_path,
                "error": str(exc),
            },
        )
        return _finding(
            "hash_mismatch_pre_write",
            "error",
            f"item_id={item_id!r} hash_file failed: {exc!s}",
        )
    if current != prior_after_hash:
        return _finding(
            "hash_mismatch_pre_write",
            "error",
            f"item_id={item_id!r} target_kb_path={target_kb_path!r} "
            f"current_hash={current!r} differs from "
            f"prior_after_hash={prior_after_hash!r}",
        )
    return None


def _last_recorded_after_hash(batches: Iterable[CommitBatch], target_kb_path: str) -> str | None:
    """Walk batches in reverse order; return the most recently recorded
    ``after_hash`` for ``target_kb_path`` from a touched_file with
    operation in {create, update, skip}. Returns None when no prior record."""
    batches_list = list(batches)
    for batch in reversed(batches_list):
        for tf in batch.touched_files:
            if tf.path != target_kb_path:
                continue
            if tf.operation in {"create", "update", "skip"} and tf.after_hash is not None:
                return tf.after_hash
    return None
