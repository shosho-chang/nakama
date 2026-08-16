"""Fail-closed local Hugging Face snapshot byte identity."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from ..hashing import hash_object, measure_regular_file


class SnapshotIdentityError(ValueError):
    """A local snapshot is incomplete, escaped, unstable, or non-regular."""


def measure_huggingface_snapshot(
    snapshot: str | Path,
) -> tuple[Path, tuple[tuple[str, int, str], ...], str]:
    """Hash actual file bytes while allowing only direct links into repo blobs."""

    root = Path(snapshot)
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise SnapshotIdentityError("snapshot is unavailable") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if root.is_symlink() or int(getattr(root_stat, "st_file_attributes", 0)) & reparse_flag:
        raise SnapshotIdentityError("snapshot root may not be a reparse point")
    if not root.is_dir():
        raise SnapshotIdentityError("snapshot root is not a directory")
    resolved_root = root.resolve(strict=True)
    repo_root = resolved_root.parent.parent
    blobs_candidate = repo_root / "blobs"
    blobs_root = (
        blobs_candidate.resolve(strict=True)
        if blobs_candidate.is_dir()
        else None
    )

    entries: list[tuple[str, int, str]] = []
    for candidate in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        try:
            candidate_stat = candidate.lstat()
        except OSError as exc:
            raise SnapshotIdentityError("snapshot entry disappeared") from exc
        attributes = int(getattr(candidate_stat, "st_file_attributes", 0))
        if candidate.is_symlink():
            if blobs_root is None:
                raise SnapshotIdentityError(
                    "snapshot link requires a model-cache blobs directory"
                )
            if candidate.is_dir():
                raise SnapshotIdentityError("snapshot may not contain linked directories")
            target_text = os.readlink(candidate)
            if Path(target_text).is_absolute():
                raise SnapshotIdentityError("snapshot link target must be cache-relative")
            target = (candidate.parent / target_text).resolve(strict=True)
            try:
                relative_target = target.relative_to(blobs_root)
            except ValueError as exc:
                raise SnapshotIdentityError("snapshot link escapes model-cache blobs") from exc
            # A direct blob file is the only allowed indirection.  This prevents
            # a directory symlink nested under blobs from becoming an escape.
            if len(relative_target.parts) != 1 or target.parent != blobs_root:
                raise SnapshotIdentityError("snapshot link is not a direct model-cache blob")
            try:
                digest, size = measure_regular_file(target)
            except (OSError, ValueError) as exc:
                raise SnapshotIdentityError("snapshot blob is not one stable regular file") from exc
        else:
            if attributes & reparse_flag:
                raise SnapshotIdentityError("snapshot contains an unsupported reparse point")
            if candidate.is_dir():
                continue
            try:
                digest, size = measure_regular_file(candidate)
            except (OSError, ValueError) as exc:
                raise SnapshotIdentityError(
                    "snapshot contains an unstable non-regular file"
                ) from exc
        entries.append((candidate.relative_to(root).as_posix(), size, digest))
    if not entries:
        raise SnapshotIdentityError("snapshot inventory is empty")
    inventory = tuple(entries)
    return resolved_root, inventory, hash_object({"snapshot_inventory": inventory})


__all__ = ["SnapshotIdentityError", "measure_huggingface_snapshot"]
