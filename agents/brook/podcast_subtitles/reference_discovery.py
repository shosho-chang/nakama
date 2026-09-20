"""Read-only discovery of episode Reference Evidence candidates.

Discovery is deliberately separate from enrollment.  It reads filesystem
metadata only and cannot create a Reference Manifest or assign trust,
authority, kind, or version semantics to a candidate.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal, Sequence

from .adapters.reference import SUPPORTED_REFERENCE_SUFFIXES

ReferenceCandidateStatus = Literal["candidate_only_not_enrolled"]


class ReferenceDiscoveryError(ValueError):
    """An explicit discovery scope is missing, unsafe, or unreadable."""


def _is_ignored_name(name: str) -> bool:
    normalized = name.casefold()
    return name.startswith(".") or "_to_delete_" in normalized


def _has_windows_attribute(metadata: os.stat_result, attribute_name: str) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    attribute = getattr(stat, attribute_name, 0)
    return bool(attribute and attributes & attribute)


def _is_reparse_point(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or _has_windows_attribute(
        metadata, "FILE_ATTRIBUTE_REPARSE_POINT"
    )


def _is_hidden(metadata: os.stat_result) -> bool:
    return _has_windows_attribute(metadata, "FILE_ATTRIBUTE_HIDDEN")


def _safe_directory(path: Path, *, label: str) -> Path:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise ReferenceDiscoveryError(f"{label} is missing or unreadable: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or _is_reparse_point(metadata):
        raise ReferenceDiscoveryError(f"{label} must be a regular, non-reparse directory: {path}")
    if _is_hidden(metadata):
        raise ReferenceDiscoveryError(f"{label} must not be hidden: {path}")
    return path.resolve(strict=True)


def _walk_metadata_only(folder: Path) -> Iterator[tuple[Path, os.stat_result]]:
    """Walk regular descendants without following links or opening file bytes."""

    try:
        with os.scandir(folder) as entries:
            ordered = sorted(entries, key=lambda entry: (entry.name.casefold(), entry.name))
    except OSError as exc:
        raise ReferenceDiscoveryError(f"candidate folder is unreadable: {folder}") from exc
    for entry in ordered:
        if _is_ignored_name(entry.name):
            continue
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise ReferenceDiscoveryError(
                f"candidate metadata changed or became unreadable: {entry.path}"
            ) from exc
        if _is_reparse_point(metadata) or _is_hidden(metadata):
            continue
        path = Path(entry.path)
        if stat.S_ISDIR(metadata.st_mode):
            yield from _walk_metadata_only(path)
        elif stat.S_ISREG(metadata.st_mode):
            yield path, metadata


@dataclass(frozen=True, slots=True)
class ReferenceCandidate:
    """One supported regular file found below an explicit person folder."""

    absolute_path: Path
    relative_path: str
    suffix: str
    size_bytes: int
    modified_time_ns: int
    status: ReferenceCandidateStatus = "candidate_only_not_enrolled"


def discover_reference_candidates(
    *,
    allowed_root: str | Path,
    person_folder: str | Path,
) -> tuple[ReferenceCandidate, ...]:
    """List supported files below one explicit person folder, without reading bytes."""

    root_input = Path(allowed_root)
    folder_input = Path(person_folder)
    if not root_input.is_absolute() or not folder_input.is_absolute():
        raise ReferenceDiscoveryError(
            "allowed_root and person_folder must be explicit absolute paths"
        )
    if _is_ignored_name(folder_input.name):
        raise ReferenceDiscoveryError("person_folder must not be hidden or deletion-marked")
    root = _safe_directory(root_input, label="allowed_root")
    folder = _safe_directory(folder_input, label="person_folder")
    if folder == root or not folder.is_relative_to(root):
        raise ReferenceDiscoveryError("person_folder must be a specific descendant of allowed_root")

    candidates: list[ReferenceCandidate] = []
    for path, metadata in _walk_metadata_only(folder):
        relative = path.relative_to(folder)
        suffix = path.suffix.casefold()
        if suffix not in SUPPORTED_REFERENCE_SUFFIXES:
            continue
        candidates.append(
            ReferenceCandidate(
                absolute_path=path.absolute(),
                relative_path=relative.as_posix(),
                suffix=suffix,
                size_bytes=metadata.st_size,
                modified_time_ns=metadata.st_mtime_ns,
            )
        )
    return tuple(sorted(candidates, key=lambda item: item.relative_path.casefold()))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agents.brook.podcast_subtitles.reference_discovery",
        description=(
            "List metadata-only Reference Evidence candidates below one explicit "
            "person folder. This never enrolls sources or creates a manifest."
        ),
    )
    configured_root = os.environ.get("INTERVIEW_RESEARCH_ROOT", "").strip()
    parser.add_argument(
        "--allowed-root",
        type=Path,
        default=Path(configured_root) if configured_root else None,
        help=("interview research root; defaults to INTERVIEW_RESEARCH_ROOT when configured"),
    )
    parser.add_argument(
        "--person-folder",
        type=Path,
        required=True,
        help="one explicit episode/person data-collection folder below the allowed root",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.allowed_root is None:
        parser.error("--allowed-root or INTERVIEW_RESEARCH_ROOT is required")
    try:
        candidates = discover_reference_candidates(
            allowed_root=args.allowed_root,
            person_folder=args.person_folder,
        )
    except ReferenceDiscoveryError as exc:
        parser.error(str(exc))
    payload = {
        "schema_version": 1,
        "scope": "candidate_only_not_enrolled",
        "allowed_root": str(Path(args.allowed_root).resolve(strict=True)),
        "person_folder": str(Path(args.person_folder).resolve(strict=True)),
        "candidates": [
            {
                "absolute_path": str(item.absolute_path),
                "relative_path": item.relative_path,
                "suffix": item.suffix,
                "size_bytes": item.size_bytes,
                "modified_time_ns": item.modified_time_ns,
                "status": item.status,
            }
            for item in candidates
        ],
        "operator_notice": (
            "Candidate order is deterministic, not a ranking. Explicitly choose each "
            "source and declare version, kind, trust, authority, date, and content hash "
            "in an episode manifest before enrollment."
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


__all__ = [
    "ReferenceCandidate",
    "ReferenceCandidateStatus",
    "ReferenceDiscoveryError",
    "discover_reference_candidates",
    "main",
]


if __name__ == "__main__":  # pragma: no cover - exercised through ``main`` tests
    raise SystemExit(main())
