#!/usr/bin/env python3
"""Deterministic Memo Dual-Audit text consensus and arbitration runner.

Production uses ``merge-official`` and ``apply-official-arbitration`` to replay
two independent whole-transcript audits, accept only strict safe consensus, and
queue unresolved findings for the official major-risk audio stage. The legacy
``merge`` and ``apply-arbitration`` commands exist only to read or reproduce
historical degraded Step 7 artifacts; they are not production entry points.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_POLICY_VERSION = "degraded-simple-step7-consensus-v3"
_CONTRACT = "podcast-subtitle-v2-degraded-simple-step7-v1"
_ARBITRATION_CONTRACT = (
    "podcast-subtitle-v2-degraded-simple-step7-arbitration-c-v1"
)
_ARBITRATION_POLICY_VERSION = "degraded-simple-step7-arbitration-import-v2"
_ARBITRATION_ACCEPTED_DECISIONS = frozenset(
    {"accept_a", "accept_b", "accept_identical", "accept_single"}
)
_ARBITRATION_MIGRATED_FROM_SHA256 = (
    "bd2b5902c1b974c0b3d8ef0a5065ad7d48fe4c2407eeab782f3ca382beab4bb4"
)
_ARBITRATION_MIGRATION_REASON = "policy_v3_only_tightened_unresolved_categories"
_ARBITRATION_QUEUE_V2_SHA256 = (
    "0fb093a980af9df4650e04489f82cc244b8b2217d2494c4b771379bcba61b12f"
)
_FORBIDDEN_ARBITRATION_MARKERS = (
    "number",
    "numeric",
    "quantity",
    "date",
    "money",
    "salary",
    "rate",
    "percent",
    "multiple",
    "negation",
    "omission",
    "addition",
    "damaged",
    "proposed_missing",
    "數字",
    "數量",
    "日期",
    "金額",
    "薪資",
    "年薪",
    "倍數",
    "否定",
    "漏字",
    "增字",
    "損壞",
)
_TIMESTAMP_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")
_SAFE_CATEGORY_ALLOWLIST = frozenset(
    {
        "book_title_term",
        "brand",
        "brand_institution",
        "proper_noun",
        "proper_noun_brand",
        "proper_noun_term",
        "term",
        "人名",
        "人名+成語",
        "公司名",
        "同音詞",
        "固定詞",
        "固定說法",
        "學校名",
        "專名/職稱",
        "成語/框架",
        "書名",
        "書名/固定詞",
        "機構名",
        "產業術語",
        "科系名",
        "稱號",
        "節目名+人名",
        "術語",
        "跨詞誤辨",
        "金融術語",
    }
)

_OFFICIAL_POLICY_VERSION = "memo-dual-audit-text-arbitration-v1"
_OFFICIAL_BASE_CONTRACT = "podcast-subtitle-memo-dual-audit-text-base-v1"
_OFFICIAL_QUEUE_CONTRACT = "podcast-subtitle-memo-dual-audit-text-queue-v1"
_OFFICIAL_ARBITRATION_CONTRACT = "podcast-subtitle-memo-dual-audit-arbitration-v1"
_OFFICIAL_LEDGER_CONTRACT = "podcast-subtitle-memo-dual-audit-text-ledger-v1"
_OFFICIAL_UNRESOLVED_CONTRACT = (
    "podcast-subtitle-memo-dual-audit-unresolved-v1"
)
_OFFICIAL_MAJOR_CATEGORIES = frozenset(
    {
        "addition",
        "damaged",
        "date",
        "deletion",
        "money",
        "negation",
        "number",
        "numeric",
        "omission",
        "percent",
        "quantity",
        "rate",
        "salary",
        "unit",
        "日期",
        "漏字",
        "否定",
        "單位",
        "數字",
        "數量",
        "新增",
        "薪資",
        "金額",
    }
)


class SimpleStep7Error(ValueError):
    """An input or output violated the degraded merge contract."""


@dataclass(frozen=True, slots=True)
class Cue:
    number: int
    start: str
    end: str
    start_ms: int
    end_ms: int
    text_lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.text_lines)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    agent: str
    collection: str
    index: int
    cue_numbers: tuple[int, ...]
    payload: Mapping[str, Any]
    canonical_hash: str

    def lineage(self, *, audit_sha256: str) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "audit_sha256": audit_sha256,
            "collection": self.collection,
            "index": self.index,
            "finding_sha256": self.canonical_hash,
            "finding": dict(self.payload),
        }


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _timestamp_ms(value: str) -> int:
    match = _TIMESTAMP_RE.fullmatch(value)
    if match is None:
        raise SimpleStep7Error(f"invalid SRT timestamp: {value!r}")
    hours, minutes, seconds, milliseconds = (int(item) for item in match.groups())
    if minutes >= 60 or seconds >= 60:
        raise SimpleStep7Error(f"invalid SRT timestamp: {value!r}")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def _parse_srt(raw: bytes) -> tuple[Cue, ...]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SimpleStep7Error("SRT is not UTF-8") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise SimpleStep7Error("SRT is empty")
    cues: list[Cue] = []
    for block in re.split(r"\n{2,}", normalized):
        lines = block.split("\n")
        if len(lines) < 3:
            raise SimpleStep7Error("SRT cue lacks number, timing, or text")
        try:
            number = int(lines[0])
        except ValueError as exc:
            raise SimpleStep7Error("SRT cue number is invalid") from exc
        timing = lines[1].split(" --> ")
        if len(timing) != 2:
            raise SimpleStep7Error(f"cue {number} timing line is invalid")
        start, end = timing
        start_ms, end_ms = _timestamp_ms(start), _timestamp_ms(end)
        text_lines = tuple(lines[2:])
        if not text_lines or any(not line for line in text_lines):
            raise SimpleStep7Error(f"cue {number} has empty text")
        cues.append(Cue(number, start, end, start_ms, end_ms, text_lines))
    for expected, cue in enumerate(cues, start=1):
        if cue.number != expected:
            raise SimpleStep7Error("SRT cue numbers must be sequential from 1")
        if cue.end_ms <= cue.start_ms:
            raise SimpleStep7Error(f"cue {cue.number} has non-positive duration")
        if expected > 1 and cue.start_ms < cues[expected - 2].end_ms:
            raise SimpleStep7Error(f"cue {cue.number} overlaps the previous cue")
    return tuple(cues)


def _render_srt(cues: Sequence[Cue], replacements: Mapping[int, str]) -> bytes:
    blocks: list[str] = []
    for cue in cues:
        text = replacements.get(cue.number, cue.text)
        if not text or "\n\n" in text or "\r" in text:
            raise SimpleStep7Error(f"cue {cue.number} replacement is not valid SRT text")
        blocks.append(f"{cue.number}\n{cue.start} --> {cue.end}\n{text}")
    return ("\n\n".join(blocks) + "\n").encode("utf-8")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SimpleStep7Error(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _load_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                SimpleStep7Error(f"non-finite JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SimpleStep7Error(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise SimpleStep7Error(f"{label} must be one JSON object")
    return value


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _validate_record(
    payload: object,
    *,
    agent: str,
    collection: str,
    index: int,
    cues: Sequence[Cue],
) -> AuditRecord:
    if not isinstance(payload, dict):
        raise SimpleStep7Error(f"{agent} {collection}[{index}] must be an object")
    cue_numbers_raw = payload.get("cue_numbers")
    if (
        not isinstance(cue_numbers_raw, list)
        or not cue_numbers_raw
        or any(type(item) is not int for item in cue_numbers_raw)
    ):
        raise SimpleStep7Error(f"{agent} {collection}[{index}] cue_numbers are invalid")
    cue_numbers = tuple(cue_numbers_raw)
    if cue_numbers != tuple(range(cue_numbers[0], cue_numbers[-1] + 1)):
        raise SimpleStep7Error(
            f"{agent} {collection}[{index}] cross-cue range must be contiguous"
        )
    if cue_numbers[0] < 1 or cue_numbers[-1] > len(cues):
        raise SimpleStep7Error(f"{agent} {collection}[{index}] cites an unknown cue")
    selected = tuple(cues[number - 1] for number in cue_numbers)
    original = payload.get("original")
    expected_original = "\n".join(cue.text for cue in selected)
    if type(original) is not str or original != expected_original:
        raise SimpleStep7Error(
            f"{agent} {collection}[{index}] original differs from exact SRT text"
        )
    audit_start, audit_end = payload.get("start"), payload.get("end")
    if type(audit_start) is not str or type(audit_end) is not str:
        raise SimpleStep7Error(f"{agent} {collection}[{index}] timestamps are invalid")
    if audit_start != selected[0].start or audit_end != selected[-1].end:
        raise SimpleStep7Error(
            f"{agent} {collection}[{index}] timestamps differ from exact SRT cues"
        )
    proposed = payload.get("proposed")
    if proposed is not None and (type(proposed) is not str or not proposed.strip()):
        raise SimpleStep7Error(f"{agent} {collection}[{index}] proposed is invalid")
    if type(payload.get("category")) is not str or not payload["category"].strip():
        raise SimpleStep7Error(f"{agent} {collection}[{index}] category is invalid")
    return AuditRecord(
        agent=agent,
        collection=collection,
        index=index,
        cue_numbers=cue_numbers,
        payload=payload,
        canonical_hash=_sha256(_canonical_bytes(payload)),
    )


def _load_audit(
    raw: bytes,
    *,
    expected_agent: str,
    cues: Sequence[Cue],
) -> tuple[dict[str, Any], tuple[AuditRecord, ...]]:
    audit = _load_json(raw, label=f"audit {expected_agent}")
    if audit.get("agent") != expected_agent:
        raise SimpleStep7Error(f"audit identity must be {expected_agent!r}")
    if audit.get("cues_reviewed") != len(cues):
        raise SimpleStep7Error("each audit must cover the exact source SRT cue count")
    records: list[AuditRecord] = []
    for collection in ("findings", "risk_cues"):
        values = audit.get(collection, [])
        if not isinstance(values, list):
            raise SimpleStep7Error(f"audit {expected_agent} {collection} must be a list")
        for index, payload in enumerate(values):
            records.append(
                _validate_record(
                    payload,
                    agent=expected_agent,
                    collection=collection,
                    index=index,
                    cues=cues,
                )
            )
    seen = set()
    for record in records:
        if record.cue_numbers in seen:
            raise SimpleStep7Error(
                f"audit {expected_agent} repeats cue set {record.cue_numbers!r}"
            )
        seen.add(record.cue_numbers)
    return audit, tuple(records)


def _overlap_components(records: Sequence[AuditRecord]) -> tuple[tuple[AuditRecord, ...], ...]:
    remaining = set(range(len(records)))
    components: list[tuple[AuditRecord, ...]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        members = {seed}
        cue_ids = set(records[seed].cue_numbers)
        changed = True
        while changed:
            changed = False
            for index in sorted(remaining):
                if cue_ids.intersection(records[index].cue_numbers):
                    remaining.remove(index)
                    members.add(index)
                    cue_ids.update(records[index].cue_numbers)
                    changed = True
        components.append(tuple(records[index] for index in sorted(members)))
    return tuple(
        sorted(
            components,
            key=lambda group: (min(item.cue_numbers[0] for item in group), len(group)),
        )
    )


def _confidence_b(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    numeric = float(value)
    if not 0.0 <= numeric <= 1.0:
        return None
    return numeric


def _safe_category(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value).strip().replace("／", "/").replace("＋", "+")
    return normalized in _SAFE_CATEGORY_ALLOWLIST


def _acceptance(
    component: Sequence[AuditRecord],
) -> tuple[bool, str | None, tuple[str, ...]]:
    reasons: list[str] = []
    a_records = tuple(item for item in component if item.agent == "A")
    b_records = tuple(item for item in component if item.agent == "B")
    if len(a_records) != 1 or len(b_records) != 1:
        reasons.append("single_sided_or_overlapping_finding")
        return False, None, tuple(reasons)
    a, b = a_records[0], b_records[0]
    if a.cue_numbers != b.cue_numbers:
        reasons.append("single_sided_or_overlapping_finding")
        reasons.append("cue_set_conflict")
    if len(a.cue_numbers) != 1:
        reasons.append("cross_cue_without_per_cue_replacement")
    a_original = _normalize_text(str(a.payload["original"]))
    b_original = _normalize_text(str(b.payload["original"]))
    if a_original != b_original:
        reasons.append("normalized_original_conflict")
    a_proposed, b_proposed = a.payload.get("proposed"), b.payload.get("proposed")
    if a_proposed is None or b_proposed is None:
        reasons.append("proposed_missing")
        normalized_proposed = None
    else:
        normalized_a = _normalize_text(a_proposed)
        normalized_b = _normalize_text(b_proposed)
        normalized_proposed = normalized_a
        if normalized_a != normalized_b:
            reasons.append("normalized_proposal_conflict")
    if a.payload.get("confidence") != "high":
        reasons.append("agent_a_confidence_below_high")
    b_confidence = _confidence_b(b.payload.get("confidence"))
    if b_confidence is None or b_confidence < 0.97:
        reasons.append("agent_b_confidence_below_0_97")
    if not _safe_category(str(a.payload["category"])) or not _safe_category(
        str(b.payload["category"])
    ):
        reasons.append("category_requires_audio")
    if a.collection != "findings" or b.collection != "findings":
        reasons.append("risk_record_requires_audio")
    return not reasons, normalized_proposed, tuple(reasons)


def _write_deterministic(path: Path, payload: bytes) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    absolute.parent.mkdir(parents=True, exist_ok=True)
    if absolute.exists():
        if absolute.read_bytes() != payload:
            raise SimpleStep7Error(f"existing output differs; overwrite refused: {absolute}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{absolute.name}.", suffix=".tmp", dir=absolute.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, absolute)
    finally:
        temporary.unlink(missing_ok=True)


def _preflight_output_bundle(
    outputs: Sequence[tuple[Path, bytes]],
) -> tuple[tuple[Path, bytes], ...]:
    prepared = tuple(
        (Path(os.path.abspath(os.fspath(path))), payload) for path, payload in outputs
    )
    destinations = tuple(path for path, _ in prepared)
    if len(set(destinations)) != len(destinations):
        raise SimpleStep7Error("output destinations must be distinct")
    for path, payload in prepared:
        if not path.exists():
            continue
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise SimpleStep7Error(f"existing output is unreadable: {path}") from exc
        if existing != payload:
            raise SimpleStep7Error(f"existing output differs; overwrite refused: {path}")
    return prepared


def merge_simple_step7(
    *,
    srt_bytes: bytes,
    audit_a_bytes: bytes,
    audit_b_bytes: bytes,
) -> tuple[bytes, bytes, bytes]:
    cues = _parse_srt(srt_bytes)
    _, records_a = _load_audit(audit_a_bytes, expected_agent="A", cues=cues)
    _, records_b = _load_audit(audit_b_bytes, expected_agent="B", cues=cues)
    audit_hashes = {"A": _sha256(audit_a_bytes), "B": _sha256(audit_b_bytes)}
    replacements: dict[int, str] = {}
    accepted: list[dict[str, Any]] = []
    queued: list[dict[str, Any]] = []
    all_records = (*records_a, *records_b)
    for component in _overlap_components(all_records):
        accepted_component, proposed, reasons = _acceptance(component)
        cue_numbers = tuple(sorted({number for item in component for number in item.cue_numbers}))
        lineages = [
            item.lineage(audit_sha256=audit_hashes[item.agent])
            for item in sorted(
                component,
                key=lambda value: (value.agent, value.collection, value.index),
            )
        ]
        if accepted_component:
            assert proposed is not None and len(cue_numbers) == 1
            cue_number = cue_numbers[0]
            replacements[cue_number] = proposed
            accepted.append(
                {
                    "cue_numbers": [cue_number],
                    "original": cues[cue_number - 1].text,
                    "replacement": proposed,
                    "lineage": lineages,
                }
            )
            continue
        selected = tuple(cues[number - 1] for number in cue_numbers)
        queued.append(
            {
                "cue_numbers": list(cue_numbers),
                "start": selected[0].start,
                "end": selected[-1].end,
                "original": "\n".join(cue.text for cue in selected),
                "reasons": list(reasons),
                "lineage": lineages,
            }
        )
    output_srt = _render_srt(cues, replacements)
    reparsed = _parse_srt(output_srt)
    if len(reparsed) != len(cues):
        raise SimpleStep7Error("corrected SRT cue count drifted")
    for before, after in zip(cues, reparsed, strict=True):
        if (before.number, before.start, before.end) != (after.number, after.start, after.end):
            raise SimpleStep7Error("corrected SRT timing or cue identity drifted")
    accepted_ids = sorted(replacements)
    rejected_ids = sorted({number for item in queued for number in item["cue_numbers"]})
    needs_audio = {
        "schema_version": 1,
        "contract": f"{_CONTRACT}-needs-audio",
        "policy_version": _POLICY_VERSION,
        "input_hashes": {
            "srt_sha256": _sha256(srt_bytes),
            "audit_a_sha256": audit_hashes["A"],
            "audit_b_sha256": audit_hashes["B"],
        },
        "item_count": len(queued),
        "cue_ids": rejected_ids,
        "items": queued,
    }
    needs_audio_bytes = _canonical_bytes(needs_audio)
    ledger = {
        "schema_version": 1,
        "contract": f"{_CONTRACT}-ledger",
        "policy_version": _POLICY_VERSION,
        "provenance_status": "degraded_not_full_v2_not_checkpoint_eligible",
        "input_hashes": {
            "srt_sha256": _sha256(srt_bytes),
            "audit_a_sha256": audit_hashes["A"],
            "audit_b_sha256": audit_hashes["B"],
        },
        "output_hashes": {
            "corrected_srt_sha256": _sha256(output_srt),
            "needs_audio_sha256": _sha256(needs_audio_bytes),
        },
        "source_cue_count": len(cues),
        "accepted_count": len(accepted),
        "needs_audio_count": len(queued),
        "accepted_cue_ids": accepted_ids,
        "rejected_cue_ids": rejected_ids,
        "accepted": accepted,
        "rejected": queued,
    }
    return output_srt, _canonical_bytes(ledger), needs_audio_bytes


def _require_object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SimpleStep7Error(f"{label} must be an object")
    return value


def _require_items(value: object, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise SimpleStep7Error(f"{label} must be a list of objects")
    return value


def _cue_tuple(item: Mapping[str, Any], *, label: str, cue_count: int) -> tuple[int, ...]:
    raw = item.get("cue_numbers")
    if (
        not isinstance(raw, list)
        or not raw
        or any(type(number) is not int for number in raw)
    ):
        raise SimpleStep7Error(f"{label} cue_numbers are invalid")
    cue_numbers = tuple(raw)
    if cue_numbers != tuple(range(cue_numbers[0], cue_numbers[-1] + 1)):
        raise SimpleStep7Error(f"{label} cue_numbers must be contiguous")
    if cue_numbers[0] < 1 or cue_numbers[-1] > cue_count:
        raise SimpleStep7Error(f"{label} cites an unknown cue")
    return cue_numbers


def _validate_base_bundle(
    *,
    cues: Sequence[Cue],
    srt_bytes: bytes,
    audit_a_bytes: bytes,
    audit_b_bytes: bytes,
    base_corrected_bytes: bytes,
    base_ledger_bytes: bytes,
    base_needs_audio_bytes: bytes,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    tuple[Cue, ...],
    tuple[AuditRecord, ...],
    tuple[AuditRecord, ...],
]:
    _, records_a = _load_audit(audit_a_bytes, expected_agent="A", cues=cues)
    _, records_b = _load_audit(audit_b_bytes, expected_agent="B", cues=cues)
    fresh_corrected, fresh_ledger, fresh_needs = merge_simple_step7(
        srt_bytes=srt_bytes,
        audit_a_bytes=audit_a_bytes,
        audit_b_bytes=audit_b_bytes,
    )
    if fresh_corrected != base_corrected_bytes:
        raise SimpleStep7Error("base corrected SRT differs from fresh audit merge")
    if fresh_ledger != base_ledger_bytes:
        raise SimpleStep7Error("base ledger differs from fresh audit merge")
    if fresh_needs != base_needs_audio_bytes:
        raise SimpleStep7Error("base needs-audio differs from fresh audit merge")
    hashes = {
        "srt_sha256": _sha256(srt_bytes),
        "audit_a_sha256": _sha256(audit_a_bytes),
        "audit_b_sha256": _sha256(audit_b_bytes),
    }
    ledger = _load_json(base_ledger_bytes, label="base ledger")
    needs = _load_json(base_needs_audio_bytes, label="base needs-audio")
    if ledger.get("contract") != f"{_CONTRACT}-ledger":
        raise SimpleStep7Error("base ledger contract mismatch")
    if needs.get("contract") != f"{_CONTRACT}-needs-audio":
        raise SimpleStep7Error("base needs-audio contract mismatch")
    if ledger.get("schema_version") != 1 or needs.get("schema_version") != 1:
        raise SimpleStep7Error("base schema version mismatch")
    if ledger.get("policy_version") != _POLICY_VERSION or needs.get(
        "policy_version"
    ) != _POLICY_VERSION:
        raise SimpleStep7Error("base policy version mismatch")
    if ledger.get("input_hashes") != hashes or needs.get("input_hashes") != hashes:
        raise SimpleStep7Error("base input hashes mismatch")
    if ledger.get("source_cue_count") != len(cues):
        raise SimpleStep7Error("base source cue count mismatch")
    output_hashes = _require_object(ledger.get("output_hashes"), label="base output_hashes")
    if output_hashes.get("corrected_srt_sha256") != _sha256(base_corrected_bytes):
        raise SimpleStep7Error("base corrected SRT hash mismatch")
    if output_hashes.get("needs_audio_sha256") != _sha256(base_needs_audio_bytes):
        raise SimpleStep7Error("base needs-audio hash mismatch")
    base_cues = _parse_srt(base_corrected_bytes)
    if len(base_cues) != len(cues):
        raise SimpleStep7Error("base corrected SRT cue count mismatch")
    for source, corrected in zip(cues, base_cues, strict=True):
        if (source.number, source.start, source.end) != (
            corrected.number,
            corrected.start,
            corrected.end,
        ):
            raise SimpleStep7Error("base corrected SRT timing or cue identity drifted")
    accepted = _require_items(ledger.get("accepted"), label="base accepted")
    rejected = _require_items(ledger.get("rejected"), label="base rejected")
    needs_items = _require_items(needs.get("items"), label="base needs-audio items")
    if rejected != needs_items:
        raise SimpleStep7Error("base ledger rejected items differ from needs-audio items")
    accepted_ids = sorted(
        number
        for item in accepted
        for number in _cue_tuple(item, label="base accepted item", cue_count=len(cues))
    )
    rejected_ids = sorted(
        {
            number
            for item in rejected
            for number in _cue_tuple(item, label="base rejected item", cue_count=len(cues))
        }
    )
    if (
        ledger.get("accepted_count") != len(accepted)
        or ledger.get("needs_audio_count") != len(rejected)
        or ledger.get("accepted_cue_ids") != accepted_ids
        or ledger.get("rejected_cue_ids") != rejected_ids
        or needs.get("item_count") != len(needs_items)
        or needs.get("cue_ids") != rejected_ids
    ):
        raise SimpleStep7Error("base ledger or needs-audio counts/IDs mismatch")
    for item in accepted:
        numbers = _cue_tuple(item, label="base accepted item", cue_count=len(cues))
        replacement = item.get("replacement")
        if len(numbers) != 1 or type(replacement) is not str:
            raise SimpleStep7Error("base accepted replacement shape mismatch")
        if base_cues[numbers[0] - 1].text != replacement:
            raise SimpleStep7Error("base accepted replacement differs from corrected SRT")
    return ledger, needs, base_cues, records_a, records_b


def _component_has_forbidden_risk(item: Mapping[str, Any]) -> bool:
    values: list[str] = []
    reasons = item.get("reasons")
    if isinstance(reasons, list):
        values.extend(value for value in reasons if isinstance(value, str))
    lineage = item.get("lineage")
    if not isinstance(lineage, list):
        return True
    for entry in lineage:
        if not isinstance(entry, dict):
            return True
        finding = entry.get("finding")
        if not isinstance(finding, dict):
            return True
        proposed = finding.get("proposed")
        if proposed is None:
            return True
        category = finding.get("category")
        if isinstance(category, str):
            values.append(category)
    folded = " ".join(unicodedata.normalize("NFKC", value).casefold() for value in values)
    return any(marker in folded for marker in _FORBIDDEN_ARBITRATION_MARKERS)


def _derive_component_evidence(
    *,
    need: Mapping[str, Any],
    audit_hashes: Mapping[str, str],
    records: Mapping[tuple[str, str, int], AuditRecord],
) -> tuple[list[object], list[object], list[str]]:
    lineage = need.get("lineage")
    if not isinstance(lineage, list) or not lineage:
        raise SimpleStep7Error("base needs component lineage is invalid")
    proposals: dict[str, list[object]] = {"A": [], "B": []}
    b_risks: list[str] = []
    for entry in lineage:
        if not isinstance(entry, dict):
            raise SimpleStep7Error("base needs lineage entry is invalid")
        agent = entry.get("agent")
        collection = entry.get("collection")
        index = entry.get("index")
        if agent not in {"A", "B"} or type(collection) is not str or type(index) is not int:
            raise SimpleStep7Error("base needs lineage identity is invalid")
        record = records.get((agent, collection, index))
        if record is None:
            raise SimpleStep7Error("base needs lineage does not resolve to an audit record")
        if (
            entry.get("audit_sha256") != audit_hashes[agent]
            or entry.get("finding_sha256") != record.canonical_hash
            or entry.get("finding") != record.payload
        ):
            raise SimpleStep7Error("base needs lineage differs from exact audit record")
        proposals[agent].append(record.payload.get("proposed"))
        if agent == "B" and collection == "risk_cues":
            b_risks.append(str(record.payload["category"]))
    return proposals["A"], proposals["B"], b_risks


def _derived_replacement_candidates(
    proposals: Sequence[object], *, cue_count: int
) -> frozenset[str]:
    strings = tuple(value for value in proposals if type(value) is str and value.strip())
    candidates: set[str] = set()
    for value in strings:
        lines = value.splitlines()
        if len(lines) == cue_count and all(line.strip() for line in lines):
            candidates.add(value)
    if len(strings) == cue_count and all(
        "\n" not in value and "\r" not in value for value in strings
    ):
        candidates.add("\n".join(strings))
    return frozenset(candidates)


def apply_arbitration(
    *,
    srt_bytes: bytes,
    audit_a_bytes: bytes,
    audit_b_bytes: bytes,
    base_corrected_bytes: bytes,
    base_ledger_bytes: bytes,
    base_needs_audio_bytes: bytes,
    arbitration_bytes: bytes,
) -> tuple[bytes, bytes, bytes]:
    cues = _parse_srt(srt_bytes)
    base_ledger, base_needs, base_cues, records_a, records_b = _validate_base_bundle(
        cues=cues,
        srt_bytes=srt_bytes,
        audit_a_bytes=audit_a_bytes,
        audit_b_bytes=audit_b_bytes,
        base_corrected_bytes=base_corrected_bytes,
        base_ledger_bytes=base_ledger_bytes,
        base_needs_audio_bytes=base_needs_audio_bytes,
    )
    arbitration = _load_json(arbitration_bytes, label="arbitration C")
    if arbitration.get("contract") != _ARBITRATION_CONTRACT:
        raise SimpleStep7Error("arbitration contract mismatch")
    if arbitration.get("schema_version") != 1:
        raise SimpleStep7Error("arbitration schema version mismatch")
    if arbitration.get("migrated_from_arbitration_sha256") != (
        _ARBITRATION_MIGRATED_FROM_SHA256
    ) or arbitration.get("migration_reason") != _ARBITRATION_MIGRATION_REASON:
        raise SimpleStep7Error("arbitration migration fields mismatch")
    input_hashes = _require_object(
        arbitration.get("input_hashes"), label="arbitration input_hashes"
    )
    expected_inputs = {
        "srt_sha256": _sha256(srt_bytes),
        "audit_a_sha256": _sha256(audit_a_bytes),
        "audit_b_sha256": _sha256(audit_b_bytes),
        "strict_needs_audio_sha256": _sha256(base_needs_audio_bytes),
    }
    if any(input_hashes.get(key) != value for key, value in expected_inputs.items()):
        raise SimpleStep7Error("arbitration input hashes mismatch")
    if arbitration.get("queue_v3_sha256") != _sha256(base_needs_audio_bytes):
        raise SimpleStep7Error("arbitration queue_v3 hash mismatch")
    if arbitration.get("queue_v2_sha256") != _ARBITRATION_QUEUE_V2_SHA256:
        raise SimpleStep7Error("arbitration queue_v2 migration hash mismatch")
    if arbitration.get("queue_semantic_identity_verified_for_accepted") is not True:
        raise SimpleStep7Error("arbitration queue migration was not verified")
    needs_items = _require_items(base_needs.get("items"), label="base needs items")
    arbitration_items = _require_items(
        arbitration.get("items"), label="arbitration items"
    )
    needs_by_component: dict[tuple[int, ...], dict[str, Any]] = {}
    for item in needs_items:
        key = _cue_tuple(item, label="base needs item", cue_count=len(cues))
        if key in needs_by_component:
            raise SimpleStep7Error("base needs-audio repeats a component")
        needs_by_component[key] = item
    arbitration_by_component: dict[tuple[int, ...], dict[str, Any]] = {}
    for item in arbitration_items:
        key = _cue_tuple(item, label="arbitration item", cue_count=len(cues))
        if key in arbitration_by_component:
            raise SimpleStep7Error("arbitration repeats a component")
        arbitration_by_component[key] = item
    if set(arbitration_by_component) != set(needs_by_component):
        raise SimpleStep7Error("arbitration coverage differs from base needs components")

    base_accepted_ids = set(base_ledger["accepted_cue_ids"])
    audit_hashes = {
        "A": expected_inputs["audit_a_sha256"],
        "B": expected_inputs["audit_b_sha256"],
    }
    record_map = {
        (record.agent, record.collection, record.index): record
        for record in (*records_a, *records_b)
    }
    replacements: dict[int, str] = {}
    accepted_items: list[dict[str, Any]] = []
    unresolved_items: list[dict[str, Any]] = []
    for key in sorted(needs_by_component):
        need = needs_by_component[key]
        decision = arbitration_by_component[key]
        derived_a, derived_b, derived_b_risks = _derive_component_evidence(
            need=need,
            audit_hashes=audit_hashes,
            records=record_map,
        )
        if decision.get("a_proposals") != derived_a:
            raise SimpleStep7Error("arbitration A proposals differ from exact audits")
        if decision.get("b_proposals") != derived_b:
            raise SimpleStep7Error("arbitration B proposals differ from exact audits")
        if decision.get("b_risks") != derived_b_risks:
            raise SimpleStep7Error("arbitration risk metadata differs from exact audits")
        expected_original = "\n".join(cues[number - 1].text for number in key)
        if (
            need.get("original") != expected_original
            or decision.get("original") != expected_original
        ):
            raise SimpleStep7Error("arbitration original differs from exact source cues")
        decision_name = decision.get("decision")
        replacement = decision.get("replacement")
        if decision_name == "keep_unresolved":
            if replacement is not None:
                raise SimpleStep7Error("unresolved arbitration replacement must be null")
            unresolved_items.append({"base_component": need, "arbitration": decision})
            continue
        if decision_name not in _ARBITRATION_ACCEPTED_DECISIONS:
            raise SimpleStep7Error("arbitration decision is not approved")
        if decision.get("major_risk") is not False:
            raise SimpleStep7Error("major-risk arbitration item cannot be accepted")
        if decision.get("confidence") != "high":
            raise SimpleStep7Error("accepted arbitration confidence must be high")
        if type(replacement) is not str or not replacement.strip():
            raise SimpleStep7Error("accepted arbitration replacement must be nonblank")
        if _component_has_forbidden_risk(need):
            raise SimpleStep7Error("prohibited risk component cannot be accepted")
        if base_accepted_ids.intersection(key):
            raise SimpleStep7Error("arbitration overlaps a base accepted cue")
        lines = replacement.splitlines()
        if len(lines) != len(key) or any(not line.strip() for line in lines):
            raise SimpleStep7Error("arbitration replacement line count differs from cue count")
        a_candidates = _derived_replacement_candidates(derived_a, cue_count=len(key))
        b_candidates = _derived_replacement_candidates(derived_b, cue_count=len(key))
        if decision_name == "accept_a" and replacement not in a_candidates:
            raise SimpleStep7Error("accept_a replacement is not an A proposal")
        if decision_name == "accept_b" and replacement not in b_candidates:
            raise SimpleStep7Error("accept_b replacement is not a B proposal")
        if decision_name == "accept_identical" and not (
            replacement in a_candidates and replacement in b_candidates
        ):
            raise SimpleStep7Error("accept_identical replacement is not shared")
        if decision_name == "accept_single" and (
            (replacement in a_candidates) + (replacement in b_candidates) != 1
        ):
            raise SimpleStep7Error("accept_single replacement is not single-sided")
        for number, line in zip(key, lines, strict=True):
            replacements[number] = line
        accepted_items.append({"base_component": need, "arbitration": decision})

    if arbitration.get("accepted_count") != len(accepted_items) or arbitration.get(
        "unresolved_count"
    ) != len(unresolved_items):
        raise SimpleStep7Error("arbitration declared counts mismatch")
    final_srt = _render_srt(base_cues, replacements)
    final_cues = _parse_srt(final_srt)
    for source, final in zip(cues, final_cues, strict=True):
        if (source.number, source.start, source.end) != (
            final.number,
            final.start,
            final.end,
        ):
            raise SimpleStep7Error("final SRT timing or cue identity drifted")
    final_changed_ids = [
        source.number
        for source, final in zip(cues, final_cues, strict=True)
        if source.text != final.text
    ]
    arbitration_accepted_ids = sorted(replacements)
    unresolved_cue_ids = sorted(
        {
            number
            for item in unresolved_items
            for number in item["arbitration"]["cue_numbers"]
        }
    )
    common_inputs = {
        **expected_inputs,
        "base_corrected_srt_sha256": _sha256(base_corrected_bytes),
        "base_ledger_sha256": _sha256(base_ledger_bytes),
        "arbitration_sha256": _sha256(arbitration_bytes),
    }
    unresolved = {
        "schema_version": 1,
        "contract": f"{_CONTRACT}-arbitration-unresolved-v1",
        "policy_version": _ARBITRATION_POLICY_VERSION,
        "provenance_status": "degraded_not_full_v2_not_checkpoint_eligible",
        "input_hashes": common_inputs,
        "item_count": len(unresolved_items),
        "cue_ids": unresolved_cue_ids,
        "items": unresolved_items,
    }
    unresolved_bytes = _canonical_bytes(unresolved)
    ledger = {
        "schema_version": 1,
        "contract": f"{_CONTRACT}-arbitration-ledger-v1",
        "policy_version": _ARBITRATION_POLICY_VERSION,
        "provenance_status": "degraded_not_full_v2_not_checkpoint_eligible_not_upload_ready",
        "input_hashes": common_inputs,
        "output_hashes": {
            "final_srt_sha256": _sha256(final_srt),
            "final_unresolved_sha256": _sha256(unresolved_bytes),
        },
        "source_cue_count": len(cues),
        "base_accepted_component_count": base_ledger["accepted_count"],
        "arbitration_accepted_component_count": len(accepted_items),
        "final_corrected_component_count": base_ledger["accepted_count"]
        + len(accepted_items),
        "final_changed_cue_count": len(final_changed_ids),
        "base_accepted_cue_ids": base_ledger["accepted_cue_ids"],
        "arbitration_accepted_cue_ids": arbitration_accepted_ids,
        "final_changed_cue_ids": final_changed_ids,
        "unresolved_component_count": len(unresolved_items),
        "unresolved_cue_ids": unresolved_cue_ids,
        "arbitration_accepted": accepted_items,
        "unresolved": unresolved_items,
    }
    return final_srt, _canonical_bytes(ledger), unresolved_bytes


def _official_major_risk(item: Mapping[str, Any]) -> bool:
    lineage = item.get("lineage")
    if not isinstance(lineage, list) or not lineage:
        raise SimpleStep7Error("official queue component has no exact audit lineage")
    derived = False
    for index, entry in enumerate(lineage):
        if not isinstance(entry, dict) or not isinstance(entry.get("finding"), dict):
            raise SimpleStep7Error("official queue lineage is malformed")
        finding = entry["finding"]
        flag = finding.get("major_risk")
        if type(flag) is not bool:
            raise SimpleStep7Error(
                f"official audit finding {index} requires boolean major_risk"
            )
        category = finding.get("category")
        if not isinstance(category, str) or not category.strip():
            raise SimpleStep7Error(
                f"official audit finding {index} requires a closed category"
            )
        if category not in _SAFE_CATEGORY_ALLOWLIST | _OFFICIAL_MAJOR_CATEGORIES:
            raise SimpleStep7Error(
                f"official audit finding {index} has unknown category: {category}"
            )
        derived = derived or flag or category in _OFFICIAL_MAJOR_CATEGORIES
    return derived


def merge_official_text_audits(
    *, srt_bytes: bytes, audit_a_bytes: bytes, audit_b_bytes: bytes
) -> tuple[bytes, bytes, bytes]:
    """Build the generic production base/queue without episode constants."""

    corrected, legacy_ledger_raw, legacy_queue_raw = merge_simple_step7(
        srt_bytes=srt_bytes,
        audit_a_bytes=audit_a_bytes,
        audit_b_bytes=audit_b_bytes,
    )
    legacy_ledger = _load_json(legacy_ledger_raw, label="derived base ledger")
    legacy_queue = _load_json(legacy_queue_raw, label="derived base queue")
    queue_items = _require_items(legacy_queue.get("items"), label="derived queue items")
    official_items: list[dict[str, Any]] = []
    for item in queue_items:
        official_items.append({**item, "major_risk": _official_major_risk(item)})
    common_inputs = {
        "srt_sha256": _sha256(srt_bytes),
        "audit_a_sha256": _sha256(audit_a_bytes),
        "audit_b_sha256": _sha256(audit_b_bytes),
    }
    queue = {
        "schema_version": 1,
        "contract": _OFFICIAL_QUEUE_CONTRACT,
        "policy_version": _OFFICIAL_POLICY_VERSION,
        "input_hashes": common_inputs,
        "item_count": len(official_items),
        "cue_ids": legacy_queue["cue_ids"],
        "items": official_items,
    }
    queue_raw = _canonical_bytes(queue)
    ledger = {
        "schema_version": 1,
        "contract": _OFFICIAL_BASE_CONTRACT,
        "policy_version": _OFFICIAL_POLICY_VERSION,
        "input_hashes": common_inputs,
        "output_hashes": {
            "corrected_srt_sha256": _sha256(corrected),
            "needs_audio_sha256": _sha256(queue_raw),
        },
        "source_cue_count": legacy_ledger["source_cue_count"],
        "accepted_count": legacy_ledger["accepted_count"],
        "rejected_count": legacy_ledger["needs_audio_count"],
        "accepted_cue_ids": legacy_ledger["accepted_cue_ids"],
        "rejected_cue_ids": legacy_ledger["rejected_cue_ids"],
        "accepted": legacy_ledger["accepted"],
        "rejected": legacy_ledger["rejected"],
    }
    return corrected, _canonical_bytes(ledger), queue_raw


def _validate_official_base_bundle(
    *,
    srt_bytes: bytes,
    audit_a_bytes: bytes,
    audit_b_bytes: bytes,
    base_corrected_bytes: bytes,
    base_ledger_bytes: bytes,
    base_needs_audio_bytes: bytes,
) -> tuple[
    tuple[Cue, ...],
    dict[str, Any],
    dict[str, Any],
    tuple[AuditRecord, ...],
    tuple[AuditRecord, ...],
]:
    fresh = merge_official_text_audits(
        srt_bytes=srt_bytes,
        audit_a_bytes=audit_a_bytes,
        audit_b_bytes=audit_b_bytes,
    )
    supplied = (base_corrected_bytes, base_ledger_bytes, base_needs_audio_bytes)
    if fresh != supplied:
        raise SimpleStep7Error("official base bundle differs from fresh audit merge")
    cues = _parse_srt(srt_bytes)
    base_cues = _parse_srt(base_corrected_bytes)
    if [(cue.number, cue.start, cue.end) for cue in cues] != [
        (cue.number, cue.start, cue.end) for cue in base_cues
    ]:
        raise SimpleStep7Error("official base corrected SRT timing drifted")
    ledger = _load_json(base_ledger_bytes, label="official base ledger")
    queue = _load_json(base_needs_audio_bytes, label="official base queue")
    _, records_a = _load_audit(audit_a_bytes, expected_agent="A", cues=cues)
    _, records_b = _load_audit(audit_b_bytes, expected_agent="B", cues=cues)
    return base_cues, ledger, queue, records_a, records_b


def apply_official_arbitration(
    *,
    episode_id: str,
    srt_bytes: bytes,
    audit_a_bytes: bytes,
    audit_b_bytes: bytes,
    base_corrected_bytes: bytes,
    base_ledger_bytes: bytes,
    base_needs_audio_bytes: bytes,
    arbitration_bytes: bytes,
) -> tuple[bytes, bytes, bytes]:
    """Replay generic production arbitration with derived major-risk identity."""

    if not episode_id.strip():
        raise SimpleStep7Error("official arbitration episode_id must be non-empty")
    base_cues, base_ledger, base_queue, records_a, records_b = (
        _validate_official_base_bundle(
            srt_bytes=srt_bytes,
            audit_a_bytes=audit_a_bytes,
            audit_b_bytes=audit_b_bytes,
            base_corrected_bytes=base_corrected_bytes,
            base_ledger_bytes=base_ledger_bytes,
            base_needs_audio_bytes=base_needs_audio_bytes,
        )
    )
    arbitration = _load_json(arbitration_bytes, label="official arbitration")
    if arbitration.get("contract") != _OFFICIAL_ARBITRATION_CONTRACT:
        raise SimpleStep7Error("official arbitration contract mismatch")
    if arbitration.get("schema_version") != 1:
        raise SimpleStep7Error("official arbitration schema mismatch")
    if arbitration.get("policy_version") != _OFFICIAL_POLICY_VERSION:
        raise SimpleStep7Error("official arbitration policy mismatch")
    if arbitration.get("episode_id") != episode_id:
        raise SimpleStep7Error("official arbitration belongs to another episode")
    expected_inputs = {
        "srt_sha256": _sha256(srt_bytes),
        "audit_a_sha256": _sha256(audit_a_bytes),
        "audit_b_sha256": _sha256(audit_b_bytes),
        "base_queue_sha256": _sha256(base_needs_audio_bytes),
    }
    if arbitration.get("input_hashes") != expected_inputs:
        raise SimpleStep7Error("official arbitration input hashes mismatch")
    needs_items = _require_items(base_queue.get("items"), label="official queue items")
    decisions = _require_items(
        arbitration.get("items"), label="official arbitration items"
    )
    needs_by_component = {
        _cue_tuple(item, label="official queue item", cue_count=len(base_cues)): item
        for item in needs_items
    }
    decisions_by_component = {
        _cue_tuple(item, label="official arbitration item", cue_count=len(base_cues)): item
        for item in decisions
    }
    if len(needs_by_component) != len(needs_items) or len(decisions_by_component) != len(
        decisions
    ):
        raise SimpleStep7Error("official arbitration repeats a component")
    if set(needs_by_component) != set(decisions_by_component):
        raise SimpleStep7Error("official arbitration coverage differs from base queue")
    record_map = {
        (record.agent, record.collection, record.index): record
        for record in (*records_a, *records_b)
    }
    audit_hashes = {"A": _sha256(audit_a_bytes), "B": _sha256(audit_b_bytes)}
    base_accepted_ids = set(base_ledger["accepted_cue_ids"])
    replacements: dict[int, str] = {}
    accepted: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for component in sorted(needs_by_component):
        need = needs_by_component[component]
        decision = decisions_by_component[component]
        derived_major = need.get("major_risk")
        if type(derived_major) is not bool:
            raise SimpleStep7Error("official queue major_risk is not boolean")
        if type(decision.get("major_risk")) is not bool:
            raise SimpleStep7Error("official arbitration major_risk is required")
        if decision["major_risk"] is not derived_major:
            raise SimpleStep7Error("official arbitration major_risk differs from audits")
        expected_original = "\n".join(
            base_cues[number - 1].text for number in component
        )
        if need.get("original") != expected_original or decision.get(
            "original"
        ) != expected_original:
            raise SimpleStep7Error("official arbitration original differs from source")
        derived_a, derived_b, derived_risks = _derive_component_evidence(
            need=need, audit_hashes=audit_hashes, records=record_map
        )
        if decision.get("a_proposals") != derived_a:
            raise SimpleStep7Error("official arbitration A proposals differ from audits")
        if decision.get("b_proposals") != derived_b:
            raise SimpleStep7Error("official arbitration B proposals differ from audits")
        if decision.get("b_risks") != derived_risks:
            raise SimpleStep7Error("official arbitration risks differ from audits")
        name = decision.get("decision")
        replacement = decision.get("replacement")
        if name == "keep_unresolved":
            if replacement is not None:
                raise SimpleStep7Error("unresolved official replacement must be null")
            unresolved.append({**decision, "major_risk": derived_major})
            continue
        if name not in _ARBITRATION_ACCEPTED_DECISIONS:
            raise SimpleStep7Error("official arbitration decision is not approved")
        if derived_major:
            raise SimpleStep7Error("major-risk official item cannot be text-accepted")
        if decision.get("confidence") != "high":
            raise SimpleStep7Error("official accepted arbitration must be high confidence")
        if type(replacement) is not str or not replacement.strip():
            raise SimpleStep7Error("official accepted replacement must be nonblank")
        if base_accepted_ids.intersection(component):
            raise SimpleStep7Error("official arbitration overlaps base acceptance")
        lines = replacement.splitlines()
        if len(lines) != len(component) or any(not line.strip() for line in lines):
            raise SimpleStep7Error("official replacement line count mismatch")
        a_candidates = _derived_replacement_candidates(
            derived_a, cue_count=len(component)
        )
        b_candidates = _derived_replacement_candidates(
            derived_b, cue_count=len(component)
        )
        valid = {
            "accept_a": replacement in a_candidates,
            "accept_b": replacement in b_candidates,
            "accept_identical": (
                replacement in a_candidates and replacement in b_candidates
            ),
            "accept_single": (
                (replacement in a_candidates) + (replacement in b_candidates) == 1
            ),
        }
        if not valid[name]:
            raise SimpleStep7Error("official replacement is not an exact audit proposal")
        for number, line in zip(component, lines, strict=True):
            replacements[number] = line
        accepted.append({**decision, "major_risk": derived_major})
    if arbitration.get("accepted_count") != len(accepted) or arbitration.get(
        "unresolved_count"
    ) != len(unresolved):
        raise SimpleStep7Error("official arbitration declared counts mismatch")
    final_srt = _render_srt(base_cues, replacements)
    final_cues = _parse_srt(final_srt)
    if [(cue.number, cue.start, cue.end) for cue in final_cues] != [
        (cue.number, cue.start, cue.end) for cue in base_cues
    ]:
        raise SimpleStep7Error("official arbitration changed cue timing")
    common_inputs = {
        **expected_inputs,
        "base_corrected_srt_sha256": _sha256(base_corrected_bytes),
        "base_ledger_sha256": _sha256(base_ledger_bytes),
        "arbitration_sha256": _sha256(arbitration_bytes),
    }
    unresolved_payload = {
        "schema_version": 1,
        "contract": _OFFICIAL_UNRESOLVED_CONTRACT,
        "policy_version": _OFFICIAL_POLICY_VERSION,
        "episode_id": episode_id,
        "input_hashes": common_inputs,
        "item_count": len(unresolved),
        "cue_ids": sorted(
            {number for item in unresolved for number in item["cue_numbers"]}
        ),
        "items": unresolved,
    }
    unresolved_raw = _canonical_bytes(unresolved_payload)
    changed = [
        source.number
        for source, final in zip(_parse_srt(srt_bytes), final_cues, strict=True)
        if source.text != final.text
    ]
    ledger = {
        "schema_version": 1,
        "contract": _OFFICIAL_LEDGER_CONTRACT,
        "policy_version": _OFFICIAL_POLICY_VERSION,
        "episode_id": episode_id,
        "input_hashes": common_inputs,
        "output_hashes": {
            "final_srt_sha256": _sha256(final_srt),
            "final_unresolved_sha256": _sha256(unresolved_raw),
        },
        "source_cue_count": len(base_cues),
        "base_accepted_component_count": base_ledger["accepted_count"],
        "arbitration_accepted_component_count": len(accepted),
        "final_changed_cue_count": len(changed),
        "final_changed_cue_ids": changed,
        "unresolved_component_count": len(unresolved),
        "unresolved_cue_ids": unresolved_payload["cue_ids"],
        "accepted": accepted,
        "unresolved": unresolved,
    }
    return final_srt, _canonical_bytes(ledger), unresolved_raw


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    merge = subparsers.add_parser(
        "merge",
        help="historical degraded-only audit merge (legacy compatibility)",
    )
    merge.add_argument("--srt", required=True, type=Path)
    merge.add_argument("--audit-a", required=True, type=Path)
    merge.add_argument("--audit-b", required=True, type=Path)
    merge.add_argument("--output-srt", required=True, type=Path)
    merge.add_argument("--ledger-output", required=True, type=Path)
    merge.add_argument("--needs-audio-output", required=True, type=Path)
    official_merge = subparsers.add_parser(
        "merge-official",
        help="production: build the generic Memo Dual-Audit text base and queue",
    )
    official_merge.add_argument("--srt", required=True, type=Path)
    official_merge.add_argument("--audit-a", required=True, type=Path)
    official_merge.add_argument("--audit-b", required=True, type=Path)
    official_merge.add_argument("--output-srt", required=True, type=Path)
    official_merge.add_argument("--ledger-output", required=True, type=Path)
    official_merge.add_argument("--needs-audio-output", required=True, type=Path)
    arbitration = subparsers.add_parser(
        "apply-arbitration",
        help="historical degraded-only Arbitration C replay (legacy compatibility)",
    )
    arbitration.add_argument("--srt", required=True, type=Path)
    arbitration.add_argument("--audit-a", required=True, type=Path)
    arbitration.add_argument("--audit-b", required=True, type=Path)
    arbitration.add_argument("--base-corrected", required=True, type=Path)
    arbitration.add_argument("--base-ledger", required=True, type=Path)
    arbitration.add_argument("--base-needs-audio", required=True, type=Path)
    arbitration.add_argument("--arbitration", required=True, type=Path)
    arbitration.add_argument("--final-srt", required=True, type=Path)
    arbitration.add_argument("--final-ledger", required=True, type=Path)
    arbitration.add_argument("--final-unresolved", required=True, type=Path)
    official_arbitration = subparsers.add_parser(
        "apply-official-arbitration",
        help="production: apply source-bound arbitration without episode constants",
    )
    official_arbitration.add_argument("--episode-id", required=True)
    official_arbitration.add_argument("--srt", required=True, type=Path)
    official_arbitration.add_argument("--audit-a", required=True, type=Path)
    official_arbitration.add_argument("--audit-b", required=True, type=Path)
    official_arbitration.add_argument("--base-corrected", required=True, type=Path)
    official_arbitration.add_argument("--base-ledger", required=True, type=Path)
    official_arbitration.add_argument(
        "--base-needs-audio", required=True, type=Path
    )
    official_arbitration.add_argument("--arbitration", required=True, type=Path)
    official_arbitration.add_argument("--final-srt", required=True, type=Path)
    official_arbitration.add_argument("--final-ledger", required=True, type=Path)
    official_arbitration.add_argument(
        "--final-unresolved", required=True, type=Path
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in {"merge", "merge-official"}:
        merge_function = (
            merge_simple_step7
            if args.command == "merge"
            else merge_official_text_audits
        )
        output_srt, ledger, needs_audio = merge_function(
            srt_bytes=args.srt.read_bytes(),
            audit_a_bytes=args.audit_a.read_bytes(),
            audit_b_bytes=args.audit_b.read_bytes(),
        )
        outputs = _preflight_output_bundle(
            (
                (args.output_srt, output_srt),
                (args.needs_audio_output, needs_audio),
                (args.ledger_output, ledger),
            )
        )
        summary = json.loads(ledger)
        result = {"output_hashes": summary["output_hashes"]}
        if args.command == "merge":
            result.update(
                {
                    "accepted_count": summary["accepted_count"],
                    "needs_audio_count": summary["needs_audio_count"],
                }
            )
        else:
            result.update(
                {
                    "accepted_count": summary["accepted_count"],
                    "needs_audio_count": summary["rejected_count"],
                }
            )
    elif args.command == "apply-arbitration":
        output_srt, ledger, unresolved = apply_arbitration(
            srt_bytes=args.srt.read_bytes(),
            audit_a_bytes=args.audit_a.read_bytes(),
            audit_b_bytes=args.audit_b.read_bytes(),
            base_corrected_bytes=args.base_corrected.read_bytes(),
            base_ledger_bytes=args.base_ledger.read_bytes(),
            base_needs_audio_bytes=args.base_needs_audio.read_bytes(),
            arbitration_bytes=args.arbitration.read_bytes(),
        )
        outputs = _preflight_output_bundle(
            (
                (args.final_srt, output_srt),
                (args.final_unresolved, unresolved),
                (args.final_ledger, ledger),
            )
        )
        summary = json.loads(ledger)
        result = {
            "final_corrected_component_count": summary[
                "final_corrected_component_count"
            ],
            "final_changed_cue_count": summary["final_changed_cue_count"],
            "unresolved_component_count": summary["unresolved_component_count"],
            "output_hashes": summary["output_hashes"],
        }
    else:
        output_srt, ledger, unresolved = apply_official_arbitration(
            episode_id=args.episode_id,
            srt_bytes=args.srt.read_bytes(),
            audit_a_bytes=args.audit_a.read_bytes(),
            audit_b_bytes=args.audit_b.read_bytes(),
            base_corrected_bytes=args.base_corrected.read_bytes(),
            base_ledger_bytes=args.base_ledger.read_bytes(),
            base_needs_audio_bytes=args.base_needs_audio.read_bytes(),
            arbitration_bytes=args.arbitration.read_bytes(),
        )
        outputs = _preflight_output_bundle(
            (
                (args.final_srt, output_srt),
                (args.final_unresolved, unresolved),
                (args.final_ledger, ledger),
            )
        )
        summary = json.loads(ledger)
        result = {
            "final_changed_cue_count": summary["final_changed_cue_count"],
            "unresolved_component_count": summary["unresolved_component_count"],
            "output_hashes": summary["output_hashes"],
        }
    for path, payload in outputs:
        _write_deterministic(path, payload)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
