"""Durable upstream authority for human-approved derivative cuts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from ..editorial_master import EditorialMasterContractError, verify_editorial_master
from ._commands import ApprovedCutCommand
from ._context import CanonicalSection, CueAnchor, CutSourceRange, EditorialCutContext

_STORE_SCHEMA = "nakama.finished-cut-approved-cuts.v1"


class ApprovedCutRegistrationError(ValueError):
    """A proposed approval cannot become Finished Cut authority."""


@dataclass(frozen=True, slots=True)
class VerifiedEditorialMaster:
    """Path-free facts returned after ADR-064 verification succeeds."""

    episode_id: str
    content_hash: str
    duration_sec: float


class EditorialMasterVerifier(Protocol):
    def verify(
        self,
        *,
        episode_id: str,
        editorial_master_id: str,
    ) -> VerifiedEditorialMaster: ...


class FilesystemEditorialMasterVerifier:
    """Production Adapter over ADR-064's fail-closed verifier."""

    def __init__(self, episodes_root: str | Path) -> None:
        self._episodes_root = Path(episodes_root).resolve()

    def verify(
        self,
        *,
        episode_id: str,
        editorial_master_id: str,
    ) -> VerifiedEditorialMaster:
        if not _identity(episode_id):
            raise ApprovedCutRegistrationError("Editorial Master episode identity is invalid")
        try:
            selection = verify_editorial_master(
                self._episodes_root / episode_id,
                expected_episode_id=episode_id,
                expected_content_hash=editorial_master_id,
            )
            timeline = _require_row(selection.receipt.get("timeline"))
            duration_sec = _number(timeline, "duration_sec")
        except (EditorialMasterContractError, ApprovedCutRegistrationError) as error:
            raise ApprovedCutRegistrationError("Editorial Master verification failed") from error
        return VerifiedEditorialMaster(
            episode_id=episode_id,
            content_hash=selection.content_hash,
            duration_sec=duration_sec,
        )


@dataclass(frozen=True, slots=True)
class ApprovedCutRegistration:
    """Human approval plus normalized cut facts; never a stage payload."""

    episode_id: str
    cut_id: str
    format: Literal["long", "short"]
    editorial_master_id: str
    winner_id: str
    tight_cut_id: str
    source_ranges: tuple[CutSourceRange, ...]
    cues: tuple[CueAnchor, ...]
    sections: tuple[CanonicalSection, ...]
    human_approved: bool
    approved_by: str
    approved_at: str
    editorial_feedback: tuple[str, ...] = ()


class ApprovedCutAuthority:
    """Atomically register and resolve ApprovedCut commands and exact contexts."""

    def __init__(
        self,
        root: str | Path,
        *,
        master_verifier: EditorialMasterVerifier,
    ) -> None:
        self._root = Path(root)
        self._path = self._root / "approved-cuts.v1.json"
        self._master_verifier = master_verifier

    def register(self, registration: ApprovedCutRegistration) -> str:
        identities = (
            registration.episode_id,
            registration.cut_id,
            registration.winner_id,
            registration.tight_cut_id,
        )
        if (
            registration.format not in {"long", "short"}
            or not all(_identity(value) for value in identities)
            or not re.fullmatch(r"[0-9a-f]{64}", registration.editorial_master_id)
        ):
            raise ApprovedCutRegistrationError("ApprovedCut identity is invalid")
        _validate_editorial_feedback(registration.editorial_feedback)
        if (
            registration.human_approved is not True
            or not registration.approved_by.strip()
            or not registration.approved_at.strip()
        ):
            raise ApprovedCutRegistrationError("explicit human approval is required")
        master = self._master_verifier.verify(
            episode_id=registration.episode_id,
            editorial_master_id=registration.editorial_master_id,
        )
        if (
            master.episode_id != registration.episode_id
            or master.content_hash != registration.editorial_master_id
            or not math.isfinite(master.duration_sec)
            or master.duration_sec <= 0
        ):
            raise ApprovedCutRegistrationError("verified Editorial Master identity is invalid")
        _validate_source_ranges(registration.source_ranges, master.duration_sec)
        duration_sec = sum(source.t1 - source.t0 for source in registration.source_ranges)
        if registration.format == "long" and duration_sec < 480.0:
            raise ApprovedCutRegistrationError("Long ApprovedCut must be at least eight minutes")
        if registration.format == "long" and not registration.sections:
            raise ApprovedCutRegistrationError("Long ApprovedCut requires canonical sections")
        if not registration.cues:
            raise ApprovedCutRegistrationError("ApprovedCut requires valid tight subtitle cues")
        _validate_sections(registration.sections, duration_sec)
        _validate_cues(registration.cues, registration.sections, duration_sec)
        context = EditorialCutContext(
            episode_id=registration.episode_id,
            cut_id=registration.cut_id,
            format=registration.format,
            editorial_master_id=registration.editorial_master_id,
            tight_cut_id=registration.tight_cut_id,
            duration_sec=duration_sec,
            source_ranges=registration.source_ranges,
            cues=registration.cues,
            sections=registration.sections,
            editorial_feedback=registration.editorial_feedback,
        )
        row = _registration_row(registration, context)
        identity = hashlib.sha256(_canonical_json(row)).hexdigest()[:32]
        command_id = f"approved-cut:{identity}"
        payload = self._read_payload()
        prior = payload["approved_cuts"].get(command_id)
        if prior is not None:
            if prior != row:
                raise ApprovedCutRegistrationError("ApprovedCut identity has conflicting facts")
            return command_id
        payload["approved_cuts"][command_id] = row
        self._atomic_write(payload)
        return command_id

    def resolve(self, command_id: str) -> ApprovedCutCommand | None:
        row = self._read_payload()["approved_cuts"].get(command_id)
        if row is None:
            return None
        return _command_from_row(command_id, row)

    def resolve_context(
        self,
        *,
        episode_id: str,
        cut_id: str,
        editorial_master_id: str,
        tight_cut_id: str,
    ) -> EditorialCutContext | None:
        for command_id, row in self._read_payload()["approved_cuts"].items():
            command = _command_from_row(command_id, row)
            if (
                command.episode_id,
                command.cut_id,
                command.editorial_master_id,
                command.tight_cut_id,
            ) == (episode_id, cut_id, editorial_master_id, tight_cut_id):
                return _context_from_row(row)
        return None

    def _read_payload(self) -> dict[str, object]:
        if not self._path.exists():
            return {"schema": _STORE_SCHEMA, "approved_cuts": {}}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ApprovedCutRegistrationError("ApprovedCut authority is unreadable") from error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema", "approved_cuts"}
            or payload.get("schema") != _STORE_SCHEMA
            or not isinstance(payload.get("approved_cuts"), dict)
        ):
            raise ApprovedCutRegistrationError("ApprovedCut authority contract is invalid")
        return cast(dict[str, object], payload)

    def _atomic_write(self, payload: dict[str, object]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        staging = self._path.with_name(f".{self._path.name}.staging")
        encoded = _canonical_json(payload) + b"\n"
        try:
            with staging.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staging, self._path)
        except OSError as error:
            raise ApprovedCutRegistrationError("ApprovedCut authority write failed") from error
        finally:
            staging.unlink(missing_ok=True)


class ApprovedCutAuthorityContextResolver:
    """Narrow Adapter exposing only the EditorialCutContext resolver seam."""

    def __init__(self, authority: ApprovedCutAuthority) -> None:
        self._authority = authority

    def resolve(
        self,
        *,
        episode_id: str,
        cut_id: str,
        editorial_master_id: str,
        tight_cut_id: str,
    ) -> EditorialCutContext | None:
        return self._authority.resolve_context(
            episode_id=episode_id,
            cut_id=cut_id,
            editorial_master_id=editorial_master_id,
            tight_cut_id=tight_cut_id,
        )


def _registration_row(
    registration: ApprovedCutRegistration,
    context: EditorialCutContext,
) -> dict[str, object]:
    return {
        "episode_id": registration.episode_id,
        "cut_id": registration.cut_id,
        "format": registration.format,
        "editorial_master_id": registration.editorial_master_id,
        "winner_id": registration.winner_id,
        "tight_cut_id": registration.tight_cut_id,
        "human_approval": {
            "human_approved": registration.human_approved,
            "approved_by": registration.approved_by,
            "approved_at": registration.approved_at,
        },
        "context": {
            "duration_sec": context.duration_sec,
            "source_ranges": [
                {"t0": source.t0, "t1": source.t1} for source in context.source_ranges
            ],
            "cues": [
                {
                    "cue_id": cue.cue_id,
                    "text": cue.text,
                    "t0": cue.t0,
                    "t1": cue.t1,
                    "section_id": cue.section_id,
                }
                for cue in context.cues
            ],
            "sections": [
                {
                    "section_id": section.section_id,
                    "chapter_title": section.chapter_title,
                    "t0": section.t0,
                    "transition_before": section.transition_before,
                    "transition_title": section.transition_title,
                }
                for section in context.sections
            ],
            "editorial_feedback": list(context.editorial_feedback),
        },
    }


def _command_from_row(command_id: str, value: object) -> ApprovedCutCommand:
    row = _require_row(value)
    return ApprovedCutCommand(
        command_id=command_id,
        episode_id=_text(row, "episode_id"),
        cut_id=_text(row, "cut_id"),
        format=cast(Literal["long", "short"], _text(row, "format")),
        editorial_master_id=_text(row, "editorial_master_id"),
        winner_id=_text(row, "winner_id"),
        tight_cut_id=_text(row, "tight_cut_id"),
    )


def _context_from_row(value: object) -> EditorialCutContext:
    row = _require_row(value)
    context = _require_row(row.get("context"))
    sources = _rows(context, "source_ranges")
    cues = _rows(context, "cues")
    sections = _rows(context, "sections")
    return EditorialCutContext(
        episode_id=_text(row, "episode_id"),
        cut_id=_text(row, "cut_id"),
        format=cast(Literal["long", "short"], _text(row, "format")),
        editorial_master_id=_text(row, "editorial_master_id"),
        tight_cut_id=_text(row, "tight_cut_id"),
        duration_sec=_number(context, "duration_sec"),
        source_ranges=tuple(
            CutSourceRange(_number(source, "t0"), _number(source, "t1")) for source in sources
        ),
        cues=tuple(
            CueAnchor(
                _text(cue, "cue_id"),
                _text(cue, "text"),
                _number(cue, "t0"),
                _number(cue, "t1"),
                _optional_text(cue, "section_id"),
            )
            for cue in cues
        ),
        sections=tuple(
            CanonicalSection(
                _text(section, "section_id"),
                _text(section, "chapter_title"),
                _number(section, "t0"),
                bool(section.get("transition_before")),
                _optional_text(section, "transition_title"),
            )
            for section in sections
        ),
        editorial_feedback=tuple(_text_items(context, "editorial_feedback")),
    )


def _require_row(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ApprovedCutRegistrationError("ApprovedCut authority row is invalid")
    return value


def _rows(value: dict[str, object], key: str) -> tuple[dict[str, object], ...]:
    rows = value.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ApprovedCutRegistrationError(f"ApprovedCut {key} rows are invalid")
    return tuple(cast(dict[str, object], row) for row in rows)


def _text_items(value: dict[str, object], key: str) -> tuple[str, ...]:
    items = value.get(key)
    if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
        raise ApprovedCutRegistrationError(f"ApprovedCut {key} is invalid")
    return tuple(items)


def _text(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ApprovedCutRegistrationError(f"ApprovedCut {key} is invalid")
    return item


def _optional_text(value: dict[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str):
        raise ApprovedCutRegistrationError(f"ApprovedCut {key} is invalid")
    return item


def _number(value: dict[str, object], key: str) -> float:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
        raise ApprovedCutRegistrationError(f"ApprovedCut {key} is invalid")
    return float(item)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ApprovedCutRegistrationError("ApprovedCut facts are not canonical JSON") from error


def _identity(value: object) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= 256
        and not any(character in value for character in "/\\{}[]\r\n\t")
    )


def _validate_source_ranges(
    source_ranges: tuple[CutSourceRange, ...],
    master_duration_sec: float,
) -> None:
    if not source_ranges:
        raise ApprovedCutRegistrationError("ApprovedCut source range is required")
    previous_end = -1.0
    for source in source_ranges:
        values = (source.t0, source.t1)
        if (
            any(isinstance(value, bool) or not math.isfinite(value) for value in values)
            or source.t0 < 0
            or source.t0 >= source.t1
            or source.t1 > master_duration_sec
            or source.t0 < previous_end
        ):
            raise ApprovedCutRegistrationError("ApprovedCut source range is invalid")
        previous_end = source.t1


def _validate_editorial_feedback(feedback: tuple[str, ...]) -> None:
    forbidden = (
        "asset_ref",
        "asset-sha256:",
        "recipe_identity",
        "run_id",
        "request_id",
        "acceptance_id",
        "state.json",
        "visual-pipeline",
    )
    for item in feedback:
        lowered = item.lower() if isinstance(item, str) else ""
        if (
            not isinstance(item, str)
            or item != item.strip()
            or not item
            or len(item) > 2_000
            or any(ord(character) < 32 and character not in "\n" for character in item)
            or re.search(r"[A-Za-z]:[\\/]", item)
            or "\\\\" in item
            or any(marker in lowered for marker in forbidden)
        ):
            raise ApprovedCutRegistrationError("editorial feedback must be sanitized text only")


def _validate_sections(
    sections: tuple[CanonicalSection, ...],
    duration_sec: float,
) -> None:
    prior_t0 = -1.0
    seen: set[str] = set()
    for section in sections:
        if (
            not _identity(section.section_id)
            or section.section_id in seen
            or not isinstance(section.chapter_title, str)
            or not section.chapter_title.strip()
            or not math.isfinite(section.t0)
            or section.t0 < 0
            or section.t0 >= duration_sec
            or section.t0 <= prior_t0
            or (section.transition_title is not None and not section.transition_title.strip())
        ):
            raise ApprovedCutRegistrationError("canonical sections are invalid")
        prior_t0 = section.t0
        seen.add(section.section_id)


def _validate_cues(
    cues: tuple[CueAnchor, ...],
    sections: tuple[CanonicalSection, ...],
    duration_sec: float,
) -> None:
    section_ids = {section.section_id for section in sections}
    seen: set[str] = set()
    prior_end = -1.0
    for cue in cues:
        if (
            not _identity(cue.cue_id)
            or cue.cue_id in seen
            or not isinstance(cue.text, str)
            or not cue.text.strip()
            or not math.isfinite(cue.t0)
            or not math.isfinite(cue.t1)
            or cue.t0 < 0
            or cue.t0 >= cue.t1
            or cue.t1 > duration_sec
            or cue.t0 < prior_end
            or (section_ids and cue.section_id not in section_ids)
        ):
            raise ApprovedCutRegistrationError("ApprovedCut tight subtitle cues are invalid")
        prior_end = cue.t1
        seen.add(cue.cue_id)
