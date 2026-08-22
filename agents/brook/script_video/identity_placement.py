"""Hash-bound quorum contract for guest identity-card placement.

The contract deliberately does not claim speaker diarization.  Two independent
workers inspect the cut-local SRT and identify the first substantive guest cue.
Only exact agreement over an existing cue can be sealed.  Render/review code can
then fail closed when a guest namecard starts before that accepted cue.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from agents.brook.script_video.editorial_master import (
    EditorialMasterContractError,
    EditorialMasterSelection,
    verify_editorial_master,
)
from shared.highlight_materialization import (
    HighlightSource,
    materialization_path,
    verify_materialization_receipt,
)

CONTRACT = "podcast-identity-placement-v1"
WORKER_AUDIT_CONTRACT = "podcast-identity-placement-worker-audit-v1"
RECEIPT_NAME = "IDENTITY-PLACEMENT.json"
IDENTITY_ROOT = Path("highlights") / "identity-placement"
DEFAULT_MAX_INTRO_SEC = 180.0

_CUT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(
    r"^(?P<h>\d{2,}):(?P<m>[0-5]\d):(?P<s>[0-5]\d),(?P<ms>\d{3})$"
)
_TIMING_RE = re.compile(
    r"^(?P<start>\d{2,}:[0-5]\d:[0-5]\d,\d{3})\s+-->\s+"
    r"(?P<end>\d{2,}:[0-5]\d:[0-5]\d,\d{3})$"
)


class IdentityPlacementError(ValueError):
    """The identity-placement evidence is incomplete, stale, or contradictory."""


class IdentityPlacementConflictError(IdentityPlacementError):
    """An immutable receipt already exists with different bytes."""


@dataclass(frozen=True, slots=True)
class SrtCue:
    number: int
    start_sec: float
    end_sec: float
    text: str

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def identity(self) -> dict[str, object]:
        return {
            "number": self.number,
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "text": self.text,
            "text_sha256": self.text_sha256,
        }


@dataclass(frozen=True, slots=True)
class IdentityPlacementSelection:
    receipt_path: Path
    receipt: dict[str, object]

    @property
    def accepted_guest_cue(self) -> Mapping[str, object]:
        value = self.receipt.get("accepted_guest_cue")
        if not isinstance(value, dict):
            raise IdentityPlacementError("accepted_guest_cue must be an object")
        return value

    def identity(self) -> dict[str, object]:
        root = self.receipt_path.parents[3]
        return {
            "contract": CONTRACT,
            "episode_id": self.receipt["episode_id"],
            "cut_id": self.receipt["cut_id"],
            "content_hash": self.receipt["content_hash"],
            "receipt": self.receipt_path.relative_to(root).as_posix(),
        }


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _pretty_json(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_exact_keys(
    value: object, expected: set[str], label: str
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise IdentityPlacementError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        raise IdentityPlacementError(
            f"{label} fields mismatch: expected {sorted(expected)}, got {sorted(actual)}"
        )
    return value


def _validate_cut_id(cut_id: str) -> str:
    if not _CUT_ID_RE.fullmatch(cut_id):
        raise IdentityPlacementError(f"unsafe cut_id: {cut_id!r}")
    return cut_id


def _contained_file(root: Path, value: str | Path, label: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise IdentityPlacementError(f"{label} path escapes episode root") from error
    if not resolved.is_file():
        raise IdentityPlacementError(f"{label} does not exist: {resolved}")
    return resolved


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _seconds(value: str) -> float:
    match = _TIMESTAMP_RE.fullmatch(value)
    if match is None:
        raise IdentityPlacementError(f"invalid SRT timestamp: {value}")
    return (
        int(match["h"]) * 3600
        + int(match["m"]) * 60
        + int(match["s"])
        + int(match["ms"]) / 1000
    )


def parse_srt(path: Path) -> list[SrtCue]:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    if not text:
        raise IdentityPlacementError("cut SRT is empty")
    cues: list[SrtCue] = []
    for block_number, block in enumerate(re.split(r"\n{2,}", text), 1):
        lines = block.splitlines()
        if len(lines) < 3:
            raise IdentityPlacementError(f"malformed SRT block {block_number}")
        try:
            number = int(lines[0].strip())
        except ValueError as error:
            raise IdentityPlacementError(
                f"invalid cue number in SRT block {block_number}"
            ) from error
        timing = _TIMING_RE.fullmatch(lines[1].strip())
        if timing is None:
            raise IdentityPlacementError(f"invalid timing in SRT cue {number}")
        start_sec = _seconds(timing["start"])
        end_sec = _seconds(timing["end"])
        cue_text = "\n".join(lines[2:]).strip()
        if not cue_text or end_sec <= start_sec:
            raise IdentityPlacementError(f"invalid content/duration in SRT cue {number}")
        if cues and (number <= cues[-1].number or start_sec < cues[-1].start_sec):
            raise IdentityPlacementError("SRT cues must have increasing number and time")
        cues.append(SrtCue(number, start_sec, end_sec, cue_text))
    return cues


def _file_identity(root: Path, path: Path, **extra: object) -> dict[str, object]:
    result: dict[str, object] = {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }
    result.update(extra)
    return result


def _validate_file_identity(
    root: Path, value: object, label: str, *, required_parent: Path | None = None
) -> Path:
    identity = _require_exact_keys(
        value, {"path", "bytes", "sha256"}, label
    )
    raw_path = identity["path"]
    if not isinstance(raw_path, str) or Path(raw_path).is_absolute():
        raise IdentityPlacementError(f"{label}.path must be episode-relative")
    path = _contained_file(root, raw_path, label)
    if required_parent is not None and not path.is_relative_to(required_parent.resolve()):
        raise IdentityPlacementError(f"{label} must be stored under the cut directory")
    if not isinstance(identity["bytes"], int) or identity["bytes"] != path.stat().st_size:
        raise IdentityPlacementError(f"{label} byte size drift")
    digest = identity["sha256"]
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise IdentityPlacementError(f"{label}.sha256 must be lowercase SHA-256")
    if digest != _sha256_file(path):
        raise IdentityPlacementError(f"{label} hash drift")
    return path


_MASTER_IDENTITY_KEYS = {
    "contract",
    "episode_id",
    "content_hash",
    "master_media_sha256",
    "master_srt_sha256",
    "editorial_master_receipt",
}


def _validate_master_identity(value: object, expected: Mapping[str, object]) -> None:
    identity = _require_exact_keys(value, _MASTER_IDENTITY_KEYS, "editorial_master")
    if identity != dict(expected):
        raise IdentityPlacementError("Editorial Master identity is stale or mismatched")


_CUE_KEYS = {"number", "start_sec", "end_sec", "text", "text_sha256"}


def _cue_from_claim(value: object, cues: list[SrtCue], label: str) -> SrtCue:
    claim = _require_exact_keys(value, _CUE_KEYS, label)
    number = claim["number"]
    if not isinstance(number, int) or isinstance(number, bool):
        raise IdentityPlacementError(f"{label}.number must be an integer")
    cue = next((item for item in cues if item.number == number), None)
    if cue is None:
        raise IdentityPlacementError(f"{label} references a missing SRT cue")
    if claim != cue.identity():
        raise IdentityPlacementError(f"{label} timestamp/text identity drift")
    return cue


_AUDIT_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "worker_id",
    "editorial_master",
    "cut_srt",
    "accepted_guest_cue",
    "verdict",
    "rationale",
}


def _load_worker_audit(
    *,
    path: Path,
    cut_dir: Path,
    episode_id: str,
    cut_id: str,
    master_identity: Mapping[str, object],
    cut_srt_identity: Mapping[str, object],
    cues: list[SrtCue],
) -> tuple[dict[str, object], SrtCue]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IdentityPlacementError(f"worker audit is not strict UTF-8 JSON: {path}") from error
    audit = _require_exact_keys(raw, _AUDIT_KEYS, f"worker audit {path.name}")
    if audit["contract"] != WORKER_AUDIT_CONTRACT:
        raise IdentityPlacementError("worker audit contract mismatch")
    if audit["episode_id"] != episode_id or audit["cut_id"] != cut_id:
        raise IdentityPlacementError("worker audit belongs to another episode/cut")
    worker_id = audit["worker_id"]
    if not isinstance(worker_id, str) or not worker_id.strip() or worker_id != worker_id.strip():
        raise IdentityPlacementError("worker_id must be a non-empty trimmed string")
    if audit["verdict"] != "accept_first_substantive_guest_cue":
        raise IdentityPlacementError("worker audit does not accept a guest cue")
    if not isinstance(audit["rationale"], str) or not audit["rationale"].strip():
        raise IdentityPlacementError("worker audit rationale is required")
    _validate_master_identity(audit["editorial_master"], master_identity)
    claimed_srt = _require_exact_keys(
        audit["cut_srt"],
        {"path", "bytes", "sha256", "cue_count"},
        "worker audit cut_srt",
    )
    if claimed_srt != dict(cut_srt_identity):
        raise IdentityPlacementError("worker audit cut SRT identity is stale")
    # The audit file itself must be cut-local; this keeps evidence from another
    # episode/cut from being accidentally selected by an absolute path.
    if not path.resolve().is_relative_to(cut_dir.resolve()):
        raise IdentityPlacementError("worker audit must be stored under the cut directory")
    return audit, _cue_from_claim(
        audit["accepted_guest_cue"], cues, f"worker audit {path.name} cue"
    )


def _open_master(root: Path) -> EditorialMasterSelection:
    try:
        return verify_editorial_master(root, expected_episode_id=root.name)
    except EditorialMasterContractError as error:
        raise IdentityPlacementError(f"Editorial Master is not valid: {error}") from error


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IdentityPlacementError(f"{label} is missing or invalid JSON") from error
    if not isinstance(value, dict):
        raise IdentityPlacementError(f"{label} must be an object")
    return value


def _latest_tight_srt(root: Path, cut_id: str) -> Path:
    srt_dir = (root / "highlights" / "srt").resolve()
    revisions: list[tuple[int, Path]] = []
    if srt_dir.is_dir():
        for path in srt_dir.iterdir():
            match = re.fullmatch(
                rf"{re.escape(cut_id)}_tight_r(\d+)\.srt", path.name
            )
            if match and path.is_file():
                revisions.append((int(match.group(1)), path.resolve()))
    if not revisions:
        raise IdentityPlacementError(f"no canonical tight SRT exists for winner {cut_id}")
    revisions.sort(key=lambda item: (item[0], item[1].name))
    return revisions[-1][1]


def _verify_cut_context(
    root: Path,
    *,
    cut_id: str,
    srt_path: Path,
    master: EditorialMasterSelection,
) -> tuple[dict[str, object], dict[str, object]]:
    """Freshly bind winner documents, latest tight SRT, and materialization."""

    latest = _latest_tight_srt(root, cut_id)
    if srt_path.resolve() != latest:
        raise IdentityPlacementError(
            f"cut SRT is not the latest canonical revision: expected {latest.name}"
        )
    highlights = root / "highlights"
    candidates_path = highlights / "candidates.json"
    winners_path = highlights / "winners.json"
    candidates_doc = _load_json_object(candidates_path, "candidates.json")
    winners_doc = _load_json_object(winners_path, "winners.json")
    master_identity = master.identity()
    for label, document in (
        ("candidates.json", candidates_doc),
        ("winners.json", winners_doc),
    ):
        if document.get("editorial_master_lineage") != master_identity:
            raise IdentityPlacementError(f"{label} Editorial Master lineage is stale")
    candidates = candidates_doc.get("candidates")
    winners = winners_doc.get("winners")
    if not isinstance(candidates, list) or not isinstance(winners, list):
        raise IdentityPlacementError("candidates/winners arrays are required")
    matching_candidates = [
        value for value in candidates if isinstance(value, dict) and value.get("id") == cut_id
    ]
    matching_winners = [
        value for value in winners if isinstance(value, dict) and value.get("id") == cut_id
    ]
    if len(matching_candidates) != 1 or len(matching_winners) != 1:
        raise IdentityPlacementError("identity placement cut_id must be one exact winner")
    candidate = matching_candidates[0]
    winner = matching_winners[0]
    cut_format = candidate.get("format")
    start_sec = candidate.get("t_start")
    end_sec = candidate.get("t_end")
    if (
        cut_format not in {"long", "short"}
        or not isinstance(start_sec, (int, float))
        or isinstance(start_sec, bool)
        or not isinstance(end_sec, (int, float))
        or isinstance(end_sec, bool)
        or float(end_sec) <= float(start_sec)
    ):
        raise IdentityPlacementError("winner candidate format/source range is invalid")
    source = HighlightSource(
        srt_path=master.srt_path,
        media_path=master.media_path,
        lineage=master_identity,
    )
    try:
        materialization = verify_materialization_receipt(
            root,
            cut_id,
            source=source,
            expected_format=str(cut_format),
        )
    except EditorialMasterContractError as error:
        raise IdentityPlacementError(
            f"winner materialization is not valid: {error}"
        ) from error
    source_range = materialization.get("source_range")
    range_valid = isinstance(source_range, dict) and set(source_range) == {
        "start_sec",
        "end_sec",
        "start_frame",
        "end_frame",
    }
    if range_valid:
        range_valid = all(
            isinstance(source_range[key], (int, float))
            and not isinstance(source_range[key], bool)
            for key in source_range
        )
    if not range_valid or (
        float(source_range["start_sec"]) != float(start_sec)
        or float(source_range["end_sec"]) != float(end_sec)
    ):
        raise IdentityPlacementError(
            "winner materialization source range differs from candidate"
        )
    shortlist_identity: dict[str, object] = {
        "candidates": _file_identity(root, candidates_path),
        "winners": _file_identity(root, winners_path),
        "candidate": {
            "id": cut_id,
            "format": cut_format,
            "t_start": start_sec,
            "t_end": end_sec,
        },
        "winner": dict(winner),
    }
    materialization_file = materialization_path(root, cut_id)
    materialization_identity: dict[str, object] = {
        **_file_identity(root, materialization_file),
        "content_hash": materialization["content_hash"],
        "format": materialization["format"],
        "source_range": materialization["source_range"],
        "timeline": materialization["timeline"],
    }
    return shortlist_identity, materialization_identity


def accept_identity_placement(
    episode_root: str | Path,
    *,
    cut_id: str,
    cut_srt: str | Path,
    audit_a: str | Path,
    audit_b: str | Path,
    output: str | Path | None = None,
    max_intro_sec: float = DEFAULT_MAX_INTRO_SEC,
    editorial_master: EditorialMasterSelection | None = None,
) -> IdentityPlacementSelection:
    """Seal exact agreement from two independent cut-local worker audits."""

    root = Path(episode_root).resolve()
    if not root.is_dir():
        raise IdentityPlacementError(f"episode root does not exist: {root}")
    cut_id = _validate_cut_id(cut_id)
    if not isinstance(max_intro_sec, (int, float)) or not 0 < float(max_intro_sec) <= 900:
        raise IdentityPlacementError("max_intro_sec must be in (0, 900]")
    srt_path = _contained_file(root, cut_srt, "cut SRT")
    canonical_srt_dir = (root / "highlights" / "srt").resolve()
    if srt_path.parent != canonical_srt_dir or not re.fullmatch(
        rf"{re.escape(cut_id)}_tight_r\d+\.srt", srt_path.name
    ):
        raise IdentityPlacementError(
            "cut SRT must be the exact canonical highlights/srt/"
            f"{cut_id}_tight_rNNN.srt revision"
        )
    cut_dir = (root / IDENTITY_ROOT / cut_id).resolve()
    if not cut_dir.is_relative_to(root):
        raise IdentityPlacementError("identity-placement directory escapes episode root")
    master = editorial_master or _open_master(root)
    master_identity = master.identity()
    if master_identity.get("episode_id") != root.name:
        raise IdentityPlacementError("Editorial Master belongs to another episode")
    shortlist_identity, materialization_identity = _verify_cut_context(
        root,
        cut_id=cut_id,
        srt_path=srt_path,
        master=master,
    )
    cues = parse_srt(srt_path)
    cut_srt_identity = _file_identity(root, srt_path, cue_count=len(cues))
    audit_paths = [
        _contained_file(root, audit_a, "audit A"),
        _contained_file(root, audit_b, "audit B"),
    ]
    if audit_paths[0] == audit_paths[1]:
        raise IdentityPlacementError("two distinct worker audit paths are required")
    loaded = [
        _load_worker_audit(
            path=path,
            cut_dir=cut_dir,
            episode_id=root.name,
            cut_id=cut_id,
            master_identity=master_identity,
            cut_srt_identity=cut_srt_identity,
            cues=cues,
        )
        for path in audit_paths
    ]
    worker_ids = [str(item[0]["worker_id"]) for item in loaded]
    if len(set(worker_ids)) != 2:
        raise IdentityPlacementError("worker audits must have distinct worker_id values")
    accepted = loaded[0][1]
    if loaded[1][1].identity() != accepted.identity():
        raise IdentityPlacementError("worker quorum conflict: accepted cues differ")
    if accepted.start_sec > float(max_intro_sec):
        raise IdentityPlacementError(
            "accepted guest cue is outside the reasonable introduction window"
        )
    audit_identities = [
        {
            **_file_identity(root, path),
            "worker_id": worker_id,
        }
        for path, worker_id in zip(audit_paths, worker_ids, strict=True)
    ]
    if len({str(value["sha256"]) for value in audit_identities}) != 2:
        raise IdentityPlacementError("worker audit content hashes must be distinct")
    core: dict[str, object] = {
        "contract": CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "editorial_master": master_identity,
        "shortlist": shortlist_identity,
        "materialization": materialization_identity,
        "cut_srt": cut_srt_identity,
        "worker_audits": audit_identities,
        "accepted_guest_cue": accepted.identity(),
        "max_intro_sec": float(max_intro_sec),
        "acceptance": "agent-quorum",
    }
    core["content_hash"] = _sha256_bytes(_canonical_json(core))
    receipt_path = (Path(output) if output is not None else cut_dir / RECEIPT_NAME)
    if not receipt_path.is_absolute():
        receipt_path = root / receipt_path
    receipt_path = receipt_path.resolve()
    if receipt_path.parent != cut_dir or receipt_path.name != RECEIPT_NAME:
        raise IdentityPlacementError(
            f"receipt must be the canonical cut-local {RECEIPT_NAME}"
        )
    payload = _pretty_json(core)
    if receipt_path.exists():
        if receipt_path.read_bytes() != payload:
            raise IdentityPlacementConflictError(
                "immutable identity-placement receipt already differs"
            )
        return verify_identity_placement(
            root, cut_id=cut_id, editorial_master=master
        )
    temporary = receipt_path.with_name(f".{RECEIPT_NAME}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, receipt_path)
    return verify_identity_placement(root, cut_id=cut_id, editorial_master=master)


_RECEIPT_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "editorial_master",
    "shortlist",
    "materialization",
    "cut_srt",
    "worker_audits",
    "accepted_guest_cue",
    "max_intro_sec",
    "acceptance",
    "content_hash",
}


def verify_identity_placement(
    episode_root: str | Path,
    *,
    cut_id: str,
    guest_namecard_start: float | None = None,
    guest_namecard_end: float | None = None,
    editorial_master: EditorialMasterSelection | None = None,
) -> IdentityPlacementSelection:
    """Verify the receipt, every parent hash, and optionally one card event."""

    root = Path(episode_root).resolve()
    cut_id = _validate_cut_id(cut_id)
    cut_dir = (root / IDENTITY_ROOT / cut_id).resolve()
    if not cut_dir.is_relative_to(root) or not cut_dir.is_dir():
        raise IdentityPlacementError("canonical cut directory is missing")
    receipt_path = cut_dir / RECEIPT_NAME
    if not receipt_path.is_file():
        raise IdentityPlacementError(f"identity-placement receipt is missing: {receipt_path}")
    try:
        receipt = _require_exact_keys(
            json.loads(receipt_path.read_text(encoding="utf-8")),
            _RECEIPT_KEYS,
            "identity-placement receipt",
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IdentityPlacementError("identity-placement receipt is not strict JSON") from error
    if receipt["contract"] != CONTRACT:
        raise IdentityPlacementError("identity-placement contract mismatch")
    if receipt["episode_id"] != root.name or receipt["cut_id"] != cut_id:
        raise IdentityPlacementError("identity-placement receipt belongs to another episode/cut")
    content_hash = receipt["content_hash"]
    if not isinstance(content_hash, str) or not _SHA256_RE.fullmatch(content_hash):
        raise IdentityPlacementError("invalid identity-placement content_hash")
    unhashed = {key: value for key, value in receipt.items() if key != "content_hash"}
    if _sha256_bytes(_canonical_json(unhashed)) != content_hash:
        raise IdentityPlacementError("identity-placement receipt content hash mismatch")
    master = editorial_master or _open_master(root)
    master_identity = master.identity()
    _validate_master_identity(receipt["editorial_master"], master_identity)

    cut_srt_value = _require_exact_keys(
        receipt["cut_srt"], {"path", "bytes", "sha256", "cue_count"}, "cut_srt"
    )
    file_only = {key: cut_srt_value[key] for key in ("path", "bytes", "sha256")}
    srt_path = _validate_file_identity(root, file_only, "cut_srt")
    cues = parse_srt(srt_path)
    if cut_srt_value["cue_count"] != len(cues):
        raise IdentityPlacementError("cut_srt cue count drift")
    shortlist_identity, materialization_identity = _verify_cut_context(
        root,
        cut_id=cut_id,
        srt_path=srt_path,
        master=master,
    )
    if receipt["shortlist"] != shortlist_identity:
        raise IdentityPlacementError("shortlist identity drift")
    if receipt["materialization"] != materialization_identity:
        raise IdentityPlacementError("materialization identity drift")

    audit_values = receipt["worker_audits"]
    if not isinstance(audit_values, list) or len(audit_values) != 2:
        raise IdentityPlacementError("receipt requires exactly two worker audits")
    audit_paths: list[Path] = []
    receipt_workers: list[str] = []
    for index, value in enumerate(audit_values, 1):
        identity = _require_exact_keys(
            value, {"path", "bytes", "sha256", "worker_id"}, f"worker_audits[{index}]"
        )
        worker_id = identity["worker_id"]
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise IdentityPlacementError("stored worker_id is invalid")
        audit_paths.append(
            _validate_file_identity(
                root,
                {key: identity[key] for key in ("path", "bytes", "sha256")},
                f"worker_audits[{index}]",
                required_parent=cut_dir,
            )
        )
        receipt_workers.append(worker_id)
    if len(set(audit_paths)) != 2 or len(set(receipt_workers)) != 2:
        raise IdentityPlacementError("stored worker audits are not independent")
    if len({str(value["sha256"]) for value in audit_values}) != 2:
        raise IdentityPlacementError("stored worker audit hashes are not distinct")
    loaded = [
        _load_worker_audit(
            path=path,
            cut_dir=cut_dir,
            episode_id=root.name,
            cut_id=cut_id,
            master_identity=master_identity,
            cut_srt_identity=cut_srt_value,
            cues=cues,
        )
        for path in audit_paths
    ]
    loaded_workers = [str(value[0]["worker_id"]) for value in loaded]
    if loaded_workers != receipt_workers or len(set(loaded_workers)) != 2:
        raise IdentityPlacementError("worker identity drift")
    accepted = loaded[0][1]
    if loaded[1][1].identity() != accepted.identity():
        raise IdentityPlacementError("worker quorum conflict after seal")
    if receipt["accepted_guest_cue"] != accepted.identity():
        raise IdentityPlacementError("stored accepted guest cue drift")
    max_intro_sec = receipt["max_intro_sec"]
    if (
        not isinstance(max_intro_sec, (int, float))
        or isinstance(max_intro_sec, bool)
        or not 0 < float(max_intro_sec) <= 900
        or accepted.start_sec > float(max_intro_sec)
    ):
        raise IdentityPlacementError("accepted guest cue exceeds introduction window")
    if receipt["acceptance"] != "agent-quorum":
        raise IdentityPlacementError("identity placement was not accepted by agent quorum")

    if (guest_namecard_start is None) != (guest_namecard_end is None):
        raise IdentityPlacementError("guest namecard start/end must be provided together")
    if guest_namecard_start is not None and guest_namecard_end is not None:
        verify_guest_namecard_event(
            accepted,
            start_sec=guest_namecard_start,
            end_sec=guest_namecard_end,
            max_intro_sec=float(max_intro_sec),
        )
    return IdentityPlacementSelection(receipt_path=receipt_path, receipt=receipt)


def verify_guest_namecard_event(
    accepted_guest_cue: SrtCue,
    *,
    start_sec: float,
    end_sec: float,
    max_intro_sec: float = DEFAULT_MAX_INTRO_SEC,
) -> None:
    """Require the guest card to start inside the accepted first guest cue."""

    for label, value in (("start", start_sec), ("end", end_sec)):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise IdentityPlacementError(f"guest namecard {label} must be numeric")
    start = float(start_sec)
    end = float(end_sec)
    tolerance = 0.001
    if end <= start:
        raise IdentityPlacementError("guest namecard has non-positive duration")
    if start + tolerance < accepted_guest_cue.start_sec:
        raise IdentityPlacementError("guest namecard starts before accepted guest speech")
    if start - tolerance > accepted_guest_cue.end_sec:
        raise IdentityPlacementError("guest namecard start drifted beyond accepted guest cue")
    if start > max_intro_sec:
        raise IdentityPlacementError("guest namecard is outside the introduction window")


def _guest_recipe_path(root: Path, cut_id: str) -> Path:
    return root / "highlights" / "tighten" / f"{cut_id}_broll.json"


def emit_guest_namecard_recipe(
    episode_root: str | Path,
    *,
    cut_id: str,
    name: str,
    title: str,
    duration_sec: float = 5.2,
    style: str = "paper",
    editorial_master: EditorialMasterSelection | None = None,
) -> dict[str, object]:
    """Add one deterministic, renderer-supported guest-namecard recipe event."""

    root = Path(episode_root).resolve()
    cut_id = _validate_cut_id(cut_id)
    if not name.strip() or name != name.strip():
        raise IdentityPlacementError("guest name must be non-empty and trimmed")
    if not title.strip() or title != title.strip():
        raise IdentityPlacementError("guest title must be non-empty and trimmed")
    if style not in {"paper", "ink", "orange"}:
        raise IdentityPlacementError("guest namecard style must be paper, ink, or orange")
    if (
        not isinstance(duration_sec, (int, float))
        or isinstance(duration_sec, bool)
        or not 0.8 <= float(duration_sec) <= 7.7
    ):
        raise IdentityPlacementError("guest namecard duration must be in [0.8, 7.7]")
    selected = verify_identity_placement(
        root, cut_id=cut_id, editorial_master=editorial_master
    )
    cue = selected.accepted_guest_cue
    start = float(cue["start_sec"])
    end = round(start + float(duration_sec), 3)
    verify_identity_placement(
        root,
        cut_id=cut_id,
        guest_namecard_start=start,
        guest_namecard_end=end,
        editorial_master=editorial_master,
    )
    event: dict[str, object] = {
        "t0": start,
        "t1": end,
        "kind": "guest-namecard",
        "slug": "guest-namecard",
        "name": name,
        "title": title,
        "style": style,
        "identity_placement": selected.identity(),
    }
    path = _guest_recipe_path(root, cut_id)
    if path.exists():
        payload = _load_json_object(path, f"{cut_id} broll recipe")
        items = payload.get("items")
        if not isinstance(items, list):
            raise IdentityPlacementError("broll recipe items must be an array")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"items": []}
        items = payload["items"]
    existing = [
        item
        for item in items
        if isinstance(item, dict)
        and str(item.get("kind", "")).lower().replace("_", "-")
        == "guest-namecard"
    ]
    if existing:
        if len(existing) != 1 or existing[0] != event:
            raise IdentityPlacementConflictError(
                "existing guest-namecard recipe differs from accepted identity placement"
            )
        return event
    items.append(event)
    items.sort(
        key=lambda item: (
            float(item.get("t0", 0)) if isinstance(item, dict) else 0,
            str(item.get("kind", "")) if isinstance(item, dict) else "",
        )
    )
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(_pretty_json(payload))
    os.replace(temporary, path)
    return event


def verify_guest_namecard_recipe(
    episode_root: str | Path,
    *,
    cut_id: str,
    editorial_master: EditorialMasterSelection | None = None,
) -> IdentityPlacementSelection:
    """Verify the canonical recipe contains one sealed guest-namecard event."""

    root = Path(episode_root).resolve()
    cut_id = _validate_cut_id(cut_id)
    path = _guest_recipe_path(root, cut_id)
    payload = _load_json_object(path, f"{cut_id} broll recipe")
    items = payload.get("items")
    if not isinstance(items, list):
        raise IdentityPlacementError("broll recipe items must be an array")
    cards = [
        item
        for item in items
        if isinstance(item, dict)
        and str(item.get("kind", "")).lower().replace("_", "-")
        == "guest-namecard"
    ]
    if len(cards) != 1:
        raise IdentityPlacementError("broll recipe requires exactly one guest-namecard")
    event = cards[0]
    name = event.get("name")
    title = event.get("title")
    style = event.get("style")
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise IdentityPlacementError("guest-namecard recipe name must be non-empty and trimmed")
    if not isinstance(title, str) or not title.strip() or title != title.strip():
        raise IdentityPlacementError("guest-namecard recipe title must be non-empty and trimmed")
    if style not in {"paper", "ink", "orange"}:
        raise IdentityPlacementError("guest-namecard recipe style is invalid")
    try:
        event_start = float(event["t0"])
        event_end = float(event["t1"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IdentityPlacementError(
            "guest-namecard recipe timestamps must be numeric"
        ) from exc
    selected = verify_identity_placement(
        root,
        cut_id=cut_id,
        guest_namecard_start=event_start,
        guest_namecard_end=event_end,
        editorial_master=editorial_master,
    )
    if event.get("identity_placement") != selected.identity():
        raise IdentityPlacementError("guest-namecard recipe lineage is stale")
    return selected


def identity_placement_status(
    episode_root: str | Path, *, cut_id: str
) -> dict[str, object]:
    root = Path(episode_root).resolve()
    receipt = root / IDENTITY_ROOT / _validate_cut_id(cut_id) / RECEIPT_NAME
    if not receipt.is_file():
        return {
            "status": "missing",
            "episode_id": root.name,
            "cut_id": cut_id,
            "receipt": str(receipt),
        }
    try:
        selected = verify_identity_placement(root, cut_id=cut_id)
    except IdentityPlacementError as error:
        return {
            "status": "invalid",
            "episode_id": root.name,
            "cut_id": cut_id,
            "error": str(error),
        }
    return {
        "status": "ready",
        "episode_id": root.name,
        "cut_id": cut_id,
        "content_hash": selected.receipt["content_hash"],
        "accepted_guest_cue": selected.receipt["accepted_guest_cue"],
    }


__all__ = [
    "CONTRACT",
    "DEFAULT_MAX_INTRO_SEC",
    "IDENTITY_ROOT",
    "IdentityPlacementConflictError",
    "IdentityPlacementError",
    "IdentityPlacementSelection",
    "RECEIPT_NAME",
    "SrtCue",
    "WORKER_AUDIT_CONTRACT",
    "accept_identity_placement",
    "emit_guest_namecard_recipe",
    "identity_placement_status",
    "parse_srt",
    "verify_guest_namecard_event",
    "verify_guest_namecard_recipe",
    "verify_identity_placement",
]
