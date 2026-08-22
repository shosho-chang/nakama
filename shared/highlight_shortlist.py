"""Shared ranking and durable writes for the highlight selection gate.

Both the command-line shortlist and the authenticated Bridge surface use this
module so a decision always produces the same ``winners.json`` contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCORERS = ("azhe", "kevin", "shufen")


class HighlightDataError(ValueError):
    """The pre-production highlight artefacts are absent or malformed."""


def _load_object(path: Path, *, required: bool = False) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise HighlightDataError(f"missing required highlight input: {path}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HighlightDataError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise HighlightDataError(f"highlight input must be a JSON object: {path}")
    return data


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> Path:
    """Write JSON through an adjacent temporary file, then atomically replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return path


def collect(hl_dir: Path, fmt: str) -> list[dict[str, Any]]:
    """Join candidates, persona scores and brand lens, ordered by median score."""
    candidates_path = hl_dir / "candidates.json"
    candidates_doc = _load_object(candidates_path, required=True)
    candidates_sha256 = hashlib.sha256(candidates_path.read_bytes()).hexdigest()
    candidates = candidates_doc.get("candidates")
    if not isinstance(candidates, list):
        raise HighlightDataError("candidates.json requires a candidates array")
    candidate_ids: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise HighlightDataError("candidates.json contains a non-object candidate")
        if candidate.get("format") != fmt:
            continue
        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise HighlightDataError("each displayed candidate requires a non-empty string id")
        if candidate_id in candidate_ids:
            raise HighlightDataError(f"duplicate candidate id: {candidate_id}")
        candidate_ids.append(candidate_id)
    if not candidate_ids:
        return []
    expected_ids = set(candidate_ids)

    scores: dict[str, dict[str, float]] = {}
    review_notes: dict[str, dict[str, str]] = {}
    for who in SCORERS:
        review_path = hl_dir / f"review_{who}.json"
        review = _load_object(review_path, required=True)
        if review.get("source_sha256") != candidates_sha256:
            raise HighlightDataError(
                f"review_{who}.json source_sha256 differs from candidates.json"
            )
        rows = review.get("scores")
        if not isinstance(rows, list):
            raise HighlightDataError(f"review_{who}.json scores must be an array")
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise HighlightDataError(f"review_{who}.json contains an invalid score row")
            candidate_id = row["id"]
            if candidate_id in seen:
                raise HighlightDataError(
                    f"review_{who}.json contains duplicate id: {candidate_id}"
                )
            seen.add(candidate_id)
            total = row.get("total")
            if (
                not isinstance(total, (int, float))
                or isinstance(total, bool)
                or not math.isfinite(float(total))
            ):
                raise HighlightDataError(
                    f"review_{who}.json total is invalid for {candidate_id}"
                )
            scores.setdefault(candidate_id, {})[who] = float(total)
            for field in ("rationale", "reason", "summary", "notes"):
                note = row.get(field)
                if isinstance(note, str) and note.strip():
                    review_notes.setdefault(candidate_id, {})[who] = note.strip()[:800]
                    break
        if seen != expected_ids:
            missing = sorted(expected_ids - seen)
            extra = sorted(seen - expected_ids)
            raise HighlightDataError(
                f"review_{who}.json candidate coverage drift; missing={missing}, extra={extra}"
            )

    brand: dict[str, dict[str, Any]] = {}
    lens_path = hl_dir / "lens_brand.json"
    lens = _load_object(lens_path, required=True)
    if lens.get("source_sha256") != candidates_sha256:
        raise HighlightDataError(
            "lens_brand.json source_sha256 differs from candidates.json"
        )
    findings = lens.get("findings")
    if not isinstance(findings, list):
        raise HighlightDataError("lens_brand.json findings must be an array")
    for finding in findings:
        if not isinstance(finding, dict) or not isinstance(finding.get("id"), str):
            raise HighlightDataError("lens_brand.json contains an invalid finding")
        candidate_id = finding["id"]
        if candidate_id in brand:
            raise HighlightDataError(
                f"lens_brand.json contains duplicate id: {candidate_id}"
            )
        brand[candidate_id] = finding
    brand_ids = set(brand)
    if brand_ids != expected_ids:
        missing = sorted(expected_ids - brand_ids)
        extra = sorted(brand_ids - expected_ids)
        raise HighlightDataError(
            f"lens_brand.json candidate coverage drift; missing={missing}, extra={extra}"
        )

    renee = _load_object(hl_dir / "lens_renee.json", required=True)
    if set(renee) != {"lens", "source_sha256", "findings"} or renee.get("lens") != "renee":
        raise HighlightDataError("lens_renee.json schema drift")
    if renee.get("source_sha256") != candidates_sha256:
        raise HighlightDataError(
            "lens_renee.json source_sha256 differs from candidates.json"
        )
    renee_findings = renee.get("findings")
    if not isinstance(renee_findings, list):
        raise HighlightDataError("lens_renee.json findings must be an array")
    renee_ids: set[str] = set()
    for finding in renee_findings:
        required_fields = {"id", "hook_risk", "retention_risk", "boundary_action"}
        if not isinstance(finding, dict) or set(finding) != required_fields:
            raise HighlightDataError("lens_renee.json contains an invalid finding")
        for field in required_fields:
            if not isinstance(finding[field], str):
                raise HighlightDataError(
                    f"lens_renee.json {field} must be a string"
                )
        candidate_id = finding["id"]
        if not candidate_id:
            raise HighlightDataError("lens_renee.json id must be a non-empty string")
        if candidate_id in renee_ids:
            raise HighlightDataError(
                f"lens_renee.json contains duplicate id: {candidate_id}"
            )
        renee_ids.add(candidate_id)
    if renee_ids != expected_ids:
        missing = sorted(expected_ids - renee_ids)
        extra = sorted(renee_ids - expected_ids)
        raise HighlightDataError(
            f"lens_renee.json candidate coverage drift; missing={missing}, extra={extra}"
        )

    result: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("format") != fmt:
            continue
        candidate_id = candidate.get("id")
        assert isinstance(candidate_id, str)
        candidate_scores = scores.get(candidate_id, {})
        values = list(candidate_scores.values())
        finding = brand.get(candidate_id, {})
        duration = candidate.get("duration_sec") or 0
        try:
            duration = round(float(duration), 1)
        except (TypeError, ValueError) as exc:
            raise HighlightDataError(
                f"candidate {candidate_id} has an invalid duration_sec"
            ) from exc
        try:
            t_start = float(candidate.get("t_start") or 0)
            t_end = float(candidate.get("t_end") or (t_start + duration))
        except (TypeError, ValueError) as exc:
            raise HighlightDataError(f"candidate {candidate_id} has invalid timecodes") from exc
        if not all(math.isfinite(value) for value in (duration, t_start, t_end)):
            raise HighlightDataError(f"candidate {candidate_id} has non-finite timing values")
        if duration < 0 or t_start < 0 or t_end <= t_start:
            raise HighlightDataError(f"candidate {candidate_id} has an invalid time range")
        result.append(
            {
                "id": candidate_id,
                "group": candidate.get("variant_group") or candidate_id,
                "title": str(candidate.get("title") or ""),
                "hook": str(candidate.get("hook") or ""),
                "rationale": str(candidate.get("rationale") or "")[:2000],
                "transcript": str(candidate.get("transcript") or "")[:12000],
                "t_start": t_start,
                "t_end": t_end,
                "duration_sec": duration,
                "median": statistics.median(values) if values else 0.0,
                "scores": {scorer: candidate_scores.get(scorer) for scorer in SCORERS},
                "review_notes": review_notes.get(candidate_id, {}),
                "brand_severity": str(finding.get("severity") or ""),
                "brand_issue": str(finding.get("issue") or "")[:160],
                "brand_mitigation": str(finding.get("mitigation") or "")[:160],
            }
        )
    result.sort(key=lambda row: -row["median"])
    seen_groups: set[str] = set()
    rank = 0
    for row in result:
        row["group_top"] = row["group"] not in seen_groups
        seen_groups.add(row["group"])
        if row["group_top"]:
            rank += 1
            row["rank"] = rank
        else:
            row["rank"] = None
    return result


def write_winners(
    hl_dir: Path,
    rows: list[dict[str, Any]],
    picks: list[str],
    *,
    picked_by: str = "修修 (gate)",
) -> Path:
    """Write the established winners schema without dropping excluded groups."""
    candidates_doc = _load_object(hl_dir / "candidates.json", required=True)
    subtitle_lineage = candidates_doc.get("subtitle_lineage")
    if subtitle_lineage is not None and not isinstance(subtitle_lineage, dict):
        raise HighlightDataError("candidates.json subtitle_lineage must be an object")
    editorial_master_lineage = candidates_doc.get("editorial_master_lineage")
    if editorial_master_lineage is not None and not isinstance(
        editorial_master_lineage, dict
    ):
        raise HighlightDataError(
            "candidates.json editorial_master_lineage must be an object"
        )
    by_id = {row["id"]: row for row in rows}
    missing = [candidate_id for candidate_id in picks if candidate_id not in by_id]
    if missing:
        raise HighlightDataError(f"candidate ids are not in the shortlist: {missing}")
    vetoed = [
        {"id": row["id"], "reason": f"brand-lens veto：{row['brand_issue']}"}
        for row in rows
        if row["brand_severity"] == "veto"
    ]
    payload: dict[str, Any] = {
        "winners": [
            {
                "id": candidate_id,
                "rank": index + 1,
                "score": int(by_id[candidate_id]["median"]),
                "title": by_id[candidate_id]["title"],
            }
            for index, candidate_id in enumerate(picks)
        ],
        "vetoed": vetoed,
        "picked_by": picked_by,
    }
    existing = _load_object(hl_dir / "winners.json")
    if existing.get("excluded_group") is not None:
        payload["excluded_group"] = existing["excluded_group"]
    if subtitle_lineage is not None:
        payload["subtitle_lineage"] = subtitle_lineage
    if editorial_master_lineage is not None:
        payload["editorial_master_lineage"] = editorial_master_lineage
    return _atomic_json_write(hl_dir / "winners.json", payload)


def load_review_feedback(hl_dir: Path) -> dict[str, Any]:
    """Load feedback audit data; malformed existing history must stop the gate."""
    payload = _load_object(hl_dir / "review_feedback.json")
    decisions = payload.get("decisions", [])
    if not isinstance(decisions, list):
        raise HighlightDataError("review_feedback.json decisions must be an array")
    return {"decisions": decisions}


def append_review_feedback(
    hl_dir: Path,
    *,
    selected_ids: list[str],
    feedback: dict[str, str],
    overridden_veto_ids: list[str],
) -> Path:
    """Append a timestamped decision; prior feedback is never discarded."""
    payload = load_review_feedback(hl_dir)
    payload["decisions"].append(
        {
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "selected_ids": selected_ids,
            "feedback": feedback,
            "override_veto_ids": overridden_veto_ids,
        }
    )
    return _atomic_json_write(hl_dir / "review_feedback.json", payload)
