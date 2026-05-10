"""Promotion Commit Service (ADR-024 Slice 7 / issue #515).

Deterministic item-level partial commit service. Takes a
``PromotionManifest`` with ``human_decision`` set on the chosen
``item_ids`` (subset), validates each item via ``AcceptanceGate``, renders
markdown via ``shared.promotion_renderer``, and writes through the
caller-supplied ``KbWriteAdapter`` (default: filesystem-backed adapter
operating on ``vault_root: Path``).

Commit pipeline (Brief §4.2 — per item):

1. Locate item by ``item_id`` in ``manifest.items``. Missing → record skip.
2. Acceptance gate: ``AcceptanceGate.validate`` → on failure, defer + record.
3. Pre-write read-back: hash file (if exists) for TouchedFile.before_hash.
4. Backup: if file exists (operation=update), ``adapter.make_backup``.
5. Render: ``shared.promotion_renderer.render_*_page``.
6. Write: ``adapter.write_file(target, content, backup_path=...)``.
7. Post-write hash: ``adapter.hash_file(target)`` → after_hash.
8. Record TouchedFile (path, operation, before_hash, after_hash, backup_path).
9. Append to ``batch.approved_item_ids``.

After all items:

10. Compute ``promotion_status`` (complete / partial / failed).
11. Return ``CommitOutcome(batch=..., acceptance_results=..., error=...)``.

Hard invariants (Brief §4.3):

- G1 / G2 : All written paths under ``vault_root`` (gate + adapter).
- G3      : Only items with ``human_decision.decision="approve"`` reach approved_item_ids.
- G4      : ``hash_mismatch_pre_write`` finding → item NOT written.
- G7      : Calling ``commit()`` twice with same ``batch_id`` raises ``ValueError``.
- G8      : ``CommitBatch.promotion_status`` per #512 V11 (computed after items).
- G9      : Service NEVER imports ``shared.book_storage``, ``fastapi``,
            ``thousand_sunny.*``, ``agents.*``, LLM clients (subprocess gate).
- G10     : Service NEVER writes outside ``vault_root`` (filesystem-backed
            adapter enforces; tests assert).

Boundaries (Brief §6 boundaries 1-14):
- No real-vault writes — tests use ``tempfile.TemporaryDirectory()``.
- No LLM. No route handler. No template engine. No file-locking dep.
- Bare ``except Exception`` forbidden (#511 F5 lesson).
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from shared.log import get_logger
from shared.promotion_acceptance_gate import AcceptanceGate
from shared.promotion_renderer import render_concept_page, render_source_page
from shared.schemas.promotion_commit import (
    AcceptanceFinding,
    AcceptanceResult,
    CommitOutcome,
)
from shared.schemas.promotion_manifest import (
    CommitBatch,
    ConceptReviewItem,
    PromotionManifest,
    SourcePageReviewItem,
    TouchedFile,
    TouchedFileOperation,
)

_logger = get_logger("nakama.shared.promotion_commit")


# Documented failure modes for adapter calls — narrow tuples per #511 F5.
_ADAPTER_FAILURES: tuple[type[BaseException], ...] = (OSError, ValueError, KeyError)
"""``read_file`` / ``write_file`` / ``hash_file`` / ``make_backup`` may
raise OSError (filesystem) / ValueError (path validation) / KeyError
(mapping-backed adapters in tests). TypeError / AttributeError signal
programmer errors and propagate."""

_BACKUP_TIMESTAMP_FMT = "%Y%m%dT%H%M%SZ"
"""ISO-8601 compact timestamp for backup filenames (e.g.
``foo.md.bak.20260510T120000Z``). Module-level constant; not tunable in v=1
(adapter constructors take ``vault_root`` only)."""


def _now_iso_utc() -> str:
    """ISO-8601 UTC timestamp. Mirrors
    ``shared.schemas.promotion_manifest.now_iso_utc``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Caller-supplied protocol ──────────────────────────────────────────────────


class KbWriteAdapter(Protocol):
    """Vault write adapter. Slice 7 ships ONE concrete implementation: a
    filesystem-backed adapter operating on a ``vault_root: Path``
    (:class:`FilesystemKbWriteAdapter`). Production callers / tests may
    swap for a more sophisticated adapter (e.g. version-controlled writer).

    All ``vault_path`` arguments are vault-relative strings (e.g.
    ``"KB/Wiki/Sources/foo/chapter-3.md"``). Adapter is responsible for
    refusing paths that escape ``vault_root`` (via ``Path.resolve()`` +
    ``commonpath`` containment check) — refuse with ``ValueError``.
    """

    def read_file(self, vault_path: str) -> bytes | None:
        """Return file bytes, or ``None`` if missing. Raises ``ValueError``
        on path-escape attempts. Raises ``OSError`` on IO failures."""
        ...

    def write_file(self, vault_path: str, content: bytes, *, backup_path: str | None) -> None:
        """Write ``content`` to ``vault_path`` atomically (write to
        ``<path>.tmp`` then ``os.replace``). If ``backup_path`` is
        non-None, the existing file at ``vault_path`` MUST already have
        been moved to ``backup_path`` (the caller invokes
        ``make_backup`` first). Raises ``ValueError`` on path escape,
        ``OSError`` on IO failure."""
        ...

    def hash_file(self, vault_path: str) -> str | None:
        """Return sha256 hex of the file, or ``None`` if missing.
        Raises ``ValueError`` on path escape, ``OSError`` on IO failure."""
        ...

    def make_backup(self, vault_path: str) -> str | None:
        """If a file exists at ``vault_path``, move it to a
        ``<basename>.bak.<timestamp>`` sibling and return the backup
        vault-relative path. Return ``None`` if no original exists.
        Raises ``ValueError`` on path escape, ``OSError`` on IO failure."""
        ...


# ── Filesystem adapter (default) ──────────────────────────────────────────────


class FilesystemKbWriteAdapter:
    """Default ``KbWriteAdapter`` backed by an on-disk vault root.

    All ``vault_path`` arguments are vault-relative; the adapter resolves
    them under ``vault_root`` and refuses any path that escapes
    (``ValueError``). Atomic write uses a sibling ``.tmp`` file +
    ``os.replace``. Backups use a ``.bak.<timestamp>`` sibling.

    G10 enforcement: every public method calls ``_resolve_under_vault`` and
    raises ``ValueError`` on escape attempts. This is in addition to the
    gate's pre-write G1/G2 check (defense in depth per the ADR-024
    "manifest is the recovery record" principle).
    """

    def __init__(self, vault_root: Path) -> None:
        if not vault_root.is_absolute():
            # Resolve to absolute so traversal checks have a stable anchor.
            vault_root = vault_root.resolve()
        self._vault_root = vault_root

    def read_file(self, vault_path: str) -> bytes | None:
        full = self._resolve_under_vault(vault_path)
        if not full.exists():
            return None
        return full.read_bytes()

    def write_file(self, vault_path: str, content: bytes, *, backup_path: str | None) -> None:
        full = self._resolve_under_vault(vault_path)
        full.parent.mkdir(parents=True, exist_ok=True)
        # Validate backup_path containment if provided (caller's responsibility,
        # but defense in depth).
        if backup_path is not None:
            self._resolve_under_vault(backup_path)
        # Atomic write via sibling .tmp.
        tmp = full.with_suffix(full.suffix + ".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, full)

    def hash_file(self, vault_path: str) -> str | None:
        full = self._resolve_under_vault(vault_path)
        if not full.exists():
            return None
        h = hashlib.sha256()
        with full.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def make_backup(self, vault_path: str) -> str | None:
        full = self._resolve_under_vault(vault_path)
        if not full.exists():
            return None
        # Generate a unique timestamp sibling. Loop with sub-second nudge in
        # case of collision (deterministic test environments may produce
        # same-second timestamps).
        for attempt in range(100):
            ts = time.strftime(_BACKUP_TIMESTAMP_FMT, time.gmtime())
            suffix = f".bak.{ts}" if attempt == 0 else f".bak.{ts}.{attempt}"
            backup_full = full.with_name(full.name + suffix)
            if not backup_full.exists():
                os.replace(full, backup_full)
                # Return as vault-relative posix-style string for portability.
                return _to_vault_relative(self._vault_root, backup_full)
            # Sleep a tick to avoid tight loop during 1s-resolution clock.
            time.sleep(0.01)
        raise OSError(f"could not generate unique backup path for {vault_path!r}")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _resolve_under_vault(self, vault_path: str) -> Path:
        """Resolve ``vault_path`` under ``self._vault_root`` and refuse
        traversal escapes with ``ValueError``."""
        if not vault_path or not vault_path.strip():
            raise ValueError(f"vault_path must be non-empty: {vault_path!r}")
        target_path = Path(vault_path)
        if target_path.is_absolute():
            resolved = target_path.resolve(strict=False)
        else:
            resolved = (self._vault_root / target_path).resolve(strict=False)
        vault_resolved = self._vault_root.resolve(strict=False)
        if not _is_within(resolved, vault_resolved):
            raise ValueError(
                f"vault_path={vault_path!r} resolves outside vault_root={str(self._vault_root)!r}"
            )
        return resolved


def _is_within(path: Path, root: Path) -> bool:
    """True iff ``path`` is ``root`` or a descendant."""
    try:
        common = os.path.commonpath([str(path), str(root)])
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(str(root))


def _to_vault_relative(vault_root: Path, full_path: Path) -> str:
    """Convert an absolute vault-rooted path back to a vault-relative
    posix-style string."""
    rel = full_path.resolve(strict=False).relative_to(vault_root.resolve(strict=False))
    return rel.as_posix()


# ── Service ───────────────────────────────────────────────────────────────────


class PromotionCommitService:
    """Deterministic item-level partial commit service.

    Construction takes optional ``acceptance_gate`` (default: a fresh
    ``AcceptanceGate`` per ``commit()`` call).

    Single public entry point: ``commit(manifest, batch_id, item_ids,
    vault_root, *, write_adapter=None) -> CommitOutcome``.

    Service is stateless across calls; ``commit()`` instantiates a fresh
    gate per invocation so duplicate-target tracking (G7) doesn't leak
    across batches.
    """

    def __init__(self) -> None:
        # No persistent state; reserved for future caller-tunable overrides
        # (e.g. backup timestamp formatter). Slice 7 has no constructor args.
        pass

    def commit(
        self,
        manifest: PromotionManifest,
        batch_id: str,
        item_ids: list[str],
        vault_root: Path,
        *,
        write_adapter: KbWriteAdapter | None = None,
    ) -> CommitOutcome:
        """Commit the named ``item_ids`` against ``vault_root``.

        Caller responsibilities:

        - Set ``human_decision`` on the items they want committed BEFORE
          calling ``commit()``. Items lacking ``human_decision="approve"``
          are recorded as deferred / rejected per the decision.
        - Choose ``batch_id`` (e.g. ``batch_{ulid}``).
        - Persist the updated manifest with the appended ``CommitBatch``
          (the service does NOT write the manifest back to disk).

        Hard invariants enforced here:

        - G7  duplicate ``batch_id`` raises ``ValueError`` at entry.
        - G3  only ``human_decision.decision="approve"`` items reach
              ``approved_item_ids``.
        - G8  ``promotion_status`` computed after all items per #512 V11.
        """
        # G7 — duplicate batch_id check at entry.
        if any(b.batch_id == batch_id for b in manifest.commit_batches):
            raise ValueError(
                f"duplicate batch_id={batch_id!r} (manifest already has a "
                f"commit_batch with this id)"
            )

        adapter = write_adapter or FilesystemKbWriteAdapter(vault_root)
        gate = AcceptanceGate()

        approved_ids: list[str] = []
        deferred_ids: list[str] = []
        rejected_ids: list[str] = []
        touched_files: list[TouchedFile] = []
        errors: list[str] = []
        acceptance_results: list[AcceptanceResult] = []

        # Index manifest items by id for O(1) lookup; preserve original order
        # for caller-supplied item_ids.
        items_by_id: dict[str, SourcePageReviewItem | ConceptReviewItem] = {
            item.item_id: item for item in manifest.items
        }

        systemic_error: str | None = None

        for item_id in item_ids:
            item = items_by_id.get(item_id)
            if item is None:
                # Step 1 — missing item.
                errors.append(f"item_id={item_id!r} not found in manifest")
                # Recorded as a synthetic acceptance failure so callers can
                # see one entry per requested id.
                acceptance_results.append(
                    AcceptanceResult(
                        item_id=item_id,
                        passed=False,
                        findings=[
                            AcceptanceFinding(
                                code="target_kb_path_missing",
                                severity="error",
                                message=f"item_id={item_id!r} not found in manifest",
                            )
                        ],
                    )
                )
                deferred_ids.append(item_id)
                continue

            # Step 2 — acceptance gate.
            result = gate.validate(item, vault_root, manifest, adapter)
            acceptance_results.append(result)
            if not result.passed:
                # Failed gate ⇒ defer (not rejected — explicit defer reason
                # surfaces in acceptance_results.findings).
                if item.human_decision is not None and item.human_decision.decision == "reject":
                    rejected_ids.append(item_id)
                else:
                    deferred_ids.append(item_id)
                # Surface the first error finding into batch.errors for log
                # readability.
                first_error = next((f for f in result.findings if f.severity == "error"), None)
                if first_error is not None:
                    errors.append(
                        f"item_id={item_id!r} gate {first_error.code}: {first_error.message}"
                    )
                continue

            # Step 2b — human_decision branch dispatch (G3). Even on gate
            # pass, only "approve" reaches the write path. The gate already
            # surfaces "human_decision_not_approve" / "human_decision_missing"
            # as error findings, so this branch is defensive — if we get here
            # with a non-approve decision, something is wrong upstream.
            if item.human_decision is None:
                # Should not reach here (gate emits human_decision_missing),
                # but defend against future schema changes.
                deferred_ids.append(item_id)
                continue
            if item.human_decision.decision == "reject":
                rejected_ids.append(item_id)
                continue
            if item.human_decision.decision == "defer":
                deferred_ids.append(item_id)
                continue
            # human_decision.decision == "approve" — fall through to write.

            # Step 3 — pre-write read-back.
            target = _resolve_target_path(item)
            if target is None:
                # Defensive: gate should have emitted target_kb_path_missing.
                deferred_ids.append(item_id)
                errors.append(f"item_id={item_id!r} target path resolution failed")
                continue

            try:
                before_hash = adapter.hash_file(target)
            except _ADAPTER_FAILURES as exc:
                errors.append(f"item_id={item_id!r} hash_file failed: {exc!s}")
                deferred_ids.append(item_id)
                continue

            operation: TouchedFileOperation = "create" if before_hash is None else "update"

            # Step 4 — backup (only on update).
            backup_path: str | None = None
            if operation == "update":
                try:
                    backup_path = adapter.make_backup(target)
                except _ADAPTER_FAILURES as exc:
                    errors.append(f"item_id={item_id!r} make_backup failed: {exc!s}")
                    deferred_ids.append(item_id)
                    continue

            # Step 5 — render.
            try:
                content_str = _render_for_item(item, manifest)
            except (KeyError, ValueError, TypeError) as exc:
                # KeyError / ValueError surface caller-data issues; TypeError
                # narrowly here is intentional for renderer-input shape bugs
                # (the renderer otherwise should not raise).
                errors.append(f"item_id={item_id!r} render failed: {exc!s}")
                deferred_ids.append(item_id)
                continue
            content = content_str.encode("utf-8")

            # Step 6 — write.
            try:
                adapter.write_file(target, content, backup_path=backup_path)
            except _ADAPTER_FAILURES as exc:
                errors.append(f"item_id={item_id!r} write_file failed: {exc!s}")
                deferred_ids.append(item_id)
                continue

            # Step 7 — post-write hash.
            try:
                after_hash = adapter.hash_file(target)
            except _ADAPTER_FAILURES as exc:
                errors.append(f"item_id={item_id!r} post-write hash_file failed: {exc!s}")
                # File written but hash unrecorded — this is a degraded state.
                # Record TouchedFile with after_hash=None so caller can
                # reconcile, and treat as defer to avoid claiming success.
                touched_files.append(
                    TouchedFile(
                        path=target,
                        operation=operation,
                        before_hash=before_hash,
                        after_hash=None,
                        backup_path=backup_path,
                    )
                )
                deferred_ids.append(item_id)
                continue

            # Step 8 — record TouchedFile.
            touched_files.append(
                TouchedFile(
                    path=target,
                    operation=operation,
                    before_hash=before_hash,
                    after_hash=after_hash,
                    backup_path=backup_path,
                )
            )

            # Step 9 — append to approved.
            approved_ids.append(item_id)

        # Step 10 — compute promotion_status.
        promotion_status = _compute_promotion_status(
            manifest=manifest,
            approved_ids=approved_ids,
            deferred_ids=deferred_ids,
            rejected_ids=rejected_ids,
            requested_ids=item_ids,
        )

        # If no items committed AND we have errors, bubble first error to
        # outcome.error for systemic-failure semantics. The schema's F1-analog
        # validator ensures error⇒approved=[]+failed.
        if promotion_status == "failed" and errors and approved_ids == []:
            systemic_error = errors[0]

        batch = CommitBatch(
            batch_id=batch_id,
            created_at=_now_iso_utc(),
            approved_item_ids=approved_ids,
            deferred_item_ids=deferred_ids,
            rejected_item_ids=rejected_ids,
            touched_files=touched_files,
            errors=errors,
            promotion_status=promotion_status,
        )

        # Step 11 — return outcome.
        return CommitOutcome(
            batch=batch,
            acceptance_results=acceptance_results,
            error=systemic_error,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _resolve_target_path(
    item: SourcePageReviewItem | ConceptReviewItem,
) -> str | None:
    """Resolve the vault-relative target path for an item.

    For ``SourcePageReviewItem``: ``item.target_kb_path`` directly.

    For ``ConceptReviewItem``: when ``canonical_match.matched_concept_path``
    is set (update path) use it; otherwise return ``None`` (gate should
    have emitted ``target_kb_path_missing``).

    Returns ``None`` when the item has no resolvable target — caller skips.
    """
    if isinstance(item, SourcePageReviewItem):
        if item.target_kb_path and item.target_kb_path.strip():
            return item.target_kb_path
        return None
    # ConceptReviewItem
    if item.canonical_match is not None and item.canonical_match.matched_concept_path:
        return item.canonical_match.matched_concept_path
    return None


def _render_for_item(
    item: SourcePageReviewItem | ConceptReviewItem,
    manifest: PromotionManifest,
) -> str:
    """Dispatch render call to the right helper."""
    if isinstance(item, SourcePageReviewItem):
        return render_source_page(item, manifest)
    return render_concept_page(item, manifest)


def _compute_promotion_status(
    *,
    manifest: PromotionManifest,
    approved_ids: list[str],
    deferred_ids: list[str],
    rejected_ids: list[str],
    requested_ids: list[str],
) -> TouchedFileOperation:  # actually CommitBatch.promotion_status Literal
    """Compute ``CommitBatch.promotion_status`` per Brief §4.2 step 10.

    - Zero approved + at least one requested → ``failed``.
    - Some approved + some failed → ``partial``.
    - All requested approved AND all manifest items have ``human_decision``
      (this batch + history) → ``complete`` if no failed batch elsewhere
      else ``partial``.
    - All requested approved BUT manifest still has undecided items →
      ``partial``.
    """
    if not approved_ids and (deferred_ids or rejected_ids):
        return "failed"  # type: ignore[return-value]
    if not approved_ids and not deferred_ids and not rejected_ids:
        # No items requested OR all-zero result. Treat as failed defensively.
        return "failed"  # type: ignore[return-value]
    # At least one approved.
    if deferred_ids or any(
        item_id not in (approved_ids + deferred_ids + rejected_ids) for item_id in requested_ids
    ):
        return "partial"  # type: ignore[return-value]
    # All requested items in approved/rejected; check manifest-wide closure.
    all_decided = all(item.human_decision is not None for item in manifest.items)
    # Any prior failed batches → can't be complete (V6).
    any_prior_failed = any(b.promotion_status == "failed" for b in manifest.commit_batches)
    if all_decided and not any_prior_failed:
        return "complete"  # type: ignore[return-value]
    return "partial"  # type: ignore[return-value]
