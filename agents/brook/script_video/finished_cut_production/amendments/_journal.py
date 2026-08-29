"""Typed reader for the Finished Cut amendment journal.

An amendment is a mechanical, non-semantic transform applied to an already
sealed :class:`FinishedCutRelease`.  It reuses the base Release's whole
``AcceptedStage`` chain and replaces only the ``MaterializationPlan``; it can
neither mint an acceptance nor dispatch a semantic worker.

The journal is the durable, version-controlled record of every such amendment
that shaped a current Release.  It exists because ADR-066 has no
``request_amendment`` command yet: the L04 amendments were performed by
episode-local operations, so without this record the current Release could not
be re-derived from anything under version control.  The follow-up that turns
the transform vocabulary into a public command supersedes this module; the
journal schema is deliberately the shape that command must accept.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

JOURNAL_SCHEMA = "nakama.finished_cut_amendment_journal.v1"

AmendmentKind = Literal["suppress_components", "replace_component_assets"]
_AMENDMENT_KINDS: frozenset[str] = frozenset(("suppress_components", "replace_component_assets"))


class AmendmentJournalError(ValueError):
    """The journal does not describe a reconstructible amendment chain."""


@dataclass(frozen=True, slots=True)
class ReleaseSide:
    """One end of an amendment: the Release before or after the transform."""

    release_id: str
    materialization_plan_id: str
    component_count: int
    event_count: int
    preview_sha256: str
    preview_bytes: int
    release_record_sha256: str


@dataclass(frozen=True, slots=True)
class SemanticAuthority:
    """The acceptance chain an amendment must carry over untouched."""

    run_id: str
    command_id: str
    director_acceptance_id: str
    dp_acceptance_id: str
    visual_acceptance_id: str
    editorial_master_id: str
    winner_id: str
    tight_cut_id: str


@dataclass(frozen=True, slots=True)
class ReferenceOperation:
    """The pinned episode-local operation that performed this amendment."""

    path: str
    sha256: str

    def verify(self, repo_root: Path) -> None:
        """Fail closed when the pinned operation is missing or has drifted."""
        operation = repo_root / self.path
        if not operation.is_file():
            raise AmendmentJournalError(f"reference operation is missing: {self.path}")
        digest = hashlib.sha256()
        with operation.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != self.sha256:
            raise AmendmentJournalError(f"reference operation digest differs: {self.path}")


@dataclass(frozen=True, slots=True)
class Amendment:
    amendment_id: str
    sequence: int
    cut_id: str
    format: Literal["long", "short"]
    kind: AmendmentKind
    operation: Mapping[str, object]
    base: ReleaseSide
    result: ReleaseSide
    semantic_authority_unchanged: SemanticAuthority
    transaction_receipt_id: str
    rollback_ref: str
    reference_operation: ReferenceOperation


@dataclass(frozen=True, slots=True)
class AmendmentJournal:
    episode_id: str
    amendments: tuple[Amendment, ...]
    current_release_id: str
    current_manifest_id: str
    current_cut_id: str

    def chain_for(self, cut_id: str) -> tuple[Amendment, ...]:
        return tuple(item for item in self.amendments if item.cut_id == cut_id)


def load_journal(path: Path) -> AmendmentJournal:
    """Read one episode journal, proving the chain is contiguous and closed."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AmendmentJournalError(f"journal is unreadable: {path}") from error
    if not isinstance(document, dict):
        raise AmendmentJournalError("journal is not an object")
    if document.get("schema") != JOURNAL_SCHEMA:
        raise AmendmentJournalError("journal schema is unsupported")

    episode_id = _text(document, "episode_id")
    rows = document.get("amendments")
    if not isinstance(rows, list) or not rows:
        raise AmendmentJournalError("journal records no amendment")

    amendments = tuple(_amendment(row) for row in rows)
    for index, item in enumerate(amendments, start=1):
        if item.sequence != index:
            raise AmendmentJournalError("amendment sequence is not contiguous")
        if index > 1:
            previous = amendments[index - 2]
            if previous.result != item.base:
                raise AmendmentJournalError("amendment base does not equal the previous result")
    if len({item.amendment_id for item in amendments}) != len(amendments):
        raise AmendmentJournalError("amendment identity is ambiguous")

    # A mechanical amendment carries the base Release's acceptance chain over
    # untouched.  Two amendments on one cut that disagree about that chain
    # describe a semantic revision wearing an amendment's clothes, so the
    # journal must refuse them rather than record them as provenance.
    for cut_id in {item.cut_id for item in amendments}:
        authorities = {
            item.semantic_authority_unchanged for item in amendments if item.cut_id == cut_id
        }
        if len(authorities) != 1:
            raise AmendmentJournalError(
                f"amendment chain changes the acceptance chain for cut: {cut_id}"
            )

    current = document.get("current")
    if not isinstance(current, dict):
        raise AmendmentJournalError("journal records no current pointer")
    current_release_id = _text(current, "release_id")
    if amendments[-1].result.release_id != current_release_id:
        raise AmendmentJournalError("the last amendment result is not the current Release")
    return AmendmentJournal(
        episode_id=episode_id,
        amendments=amendments,
        current_release_id=current_release_id,
        current_manifest_id=_text(current, "manifest_id"),
        current_cut_id=_text(current, "cut_id"),
    )


def _amendment(value: object) -> Amendment:
    if not isinstance(value, dict):
        raise AmendmentJournalError("amendment is not an object")
    operation = value.get("operation")
    if not isinstance(operation, dict):
        raise AmendmentJournalError("amendment operation is not an object")
    kind = operation.get("kind")
    if kind not in _AMENDMENT_KINDS:
        raise AmendmentJournalError(f"amendment kind is unsupported: {kind!r}")
    if not operation.get("target_event_ids"):
        raise AmendmentJournalError("amendment names no target event")

    fmt = _text(value, "format")
    if fmt not in ("long", "short"):
        raise AmendmentJournalError(f"amendment format is invalid: {fmt!r}")
    sequence = value.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise AmendmentJournalError("amendment sequence is invalid")

    resolve = value.get("resolve")
    if not isinstance(resolve, dict):
        raise AmendmentJournalError("amendment records no Resolve transaction")
    reference = value.get("reference_operation")
    if not isinstance(reference, dict):
        raise AmendmentJournalError("amendment pins no reference operation")

    base = _side(value.get("base"), "base")
    result = _side(value.get("result"), "result")
    if base.release_id == result.release_id:
        raise AmendmentJournalError("amendment base and result are the same Release")
    if base.materialization_plan_id == result.materialization_plan_id:
        raise AmendmentJournalError("amendment did not replace the materialization plan")
    if base.event_count != result.event_count:
        raise AmendmentJournalError("a mechanical amendment cannot change event count")
    if base.preview_sha256 == result.preview_sha256:
        raise AmendmentJournalError("amendment preview bytes are unchanged")

    return Amendment(
        amendment_id=_text(value, "amendment_id"),
        sequence=sequence,
        cut_id=_text(value, "cut_id"),
        format=fmt,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        operation=dict(operation),
        base=base,
        result=result,
        semantic_authority_unchanged=_authority(value.get("semantic_authority_unchanged")),
        transaction_receipt_id=_text(resolve, "transaction_receipt_id"),
        rollback_ref=_text(resolve, "rollback_ref"),
        reference_operation=ReferenceOperation(
            path=_text(reference, "path"),
            sha256=_sha256(reference, "sha256"),
        ),
    )


def _side(value: object, label: str) -> ReleaseSide:
    if not isinstance(value, dict):
        raise AmendmentJournalError(f"amendment {label} is not an object")
    counts = {}
    for key in ("component_count", "event_count", "preview_bytes"):
        count = value.get(key)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise AmendmentJournalError(f"amendment {label} {key} is invalid")
        counts[key] = count
    return ReleaseSide(
        release_id=_text(value, "release_id"),
        materialization_plan_id=_text(value, "materialization_plan_id"),
        component_count=counts["component_count"],
        event_count=counts["event_count"],
        preview_sha256=_sha256(value, "preview_sha256"),
        preview_bytes=counts["preview_bytes"],
        release_record_sha256=_sha256(value, "release_record_sha256"),
    )


def _authority(value: object) -> SemanticAuthority:
    if not isinstance(value, dict):
        raise AmendmentJournalError("amendment records no semantic authority")
    return SemanticAuthority(
        run_id=_text(value, "run_id"),
        command_id=_text(value, "command_id"),
        director_acceptance_id=_text(value, "director_acceptance_id"),
        dp_acceptance_id=_text(value, "dp_acceptance_id"),
        visual_acceptance_id=_text(value, "visual_acceptance_id"),
        editorial_master_id=_text(value, "editorial_master_id"),
        winner_id=_text(value, "winner_id"),
        tight_cut_id=_text(value, "tight_cut_id"),
    )


def _text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise AmendmentJournalError(f"journal field is missing or empty: {key}")
    return item


def _sha256(value: Mapping[str, object], key: str) -> str:
    item = _text(value, key)
    if len(item) != 64 or any(character not in "0123456789abcdef" for character in item):
        raise AmendmentJournalError(f"journal field is not a sha256: {key}")
    return item
