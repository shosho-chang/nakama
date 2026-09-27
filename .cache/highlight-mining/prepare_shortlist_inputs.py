from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def load_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write(path: Path, value: object) -> dict[str, object]:
    raw = canonical_bytes(value)
    path.write_bytes(raw)
    return {"path": path.name, "sha256": sha256(raw), "size_bytes": len(raw)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--azhe", type=Path, required=True)
    parser.add_argument("--kevin", type=Path, required=True)
    parser.add_argument("--shufen", type=Path, required=True)
    parser.add_argument("--brand", type=Path, required=True)
    parser.add_argument("--renee", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    candidates_raw = args.candidates.read_bytes()
    candidates_sha = sha256(candidates_raw)
    candidates = load_object(args.candidates)
    long_ids = {
        item["id"]
        for item in candidates.get("candidates", [])
        if isinstance(item, dict) and item.get("format") == "long"
    }
    if not long_ids:
        raise ValueError("no long candidates")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, object]] = []
    for key, path in (("azhe", args.azhe), ("kevin", args.kevin), ("shufen", args.shufen)):
        review = load_object(path)
        if str(review.get("source_sha256", "")).lower() != candidates_sha:
            raise ValueError(f"{key} source hash drift")
        scores = review.get("scores")
        if not isinstance(scores, list) or {item.get("id") for item in scores} != long_ids:
            raise ValueError(f"{key} coverage drift")
        review["source_sha256"] = candidates_sha
        files.append(write(args.output_dir / f"review_{key}.json", review))

    brand = load_object(args.brand)
    if str(brand.get("source_sha256", "")).lower() != candidates_sha:
        raise ValueError("brand source hash drift")
    findings = brand.get("findings")
    if not isinstance(findings, list):
        raise ValueError("brand findings are invalid")
    grouped: dict[str, list[dict]] = {}
    for finding in findings:
        if not isinstance(finding, dict) or finding.get("id") not in long_ids:
            raise ValueError("brand finding identity drift")
        grouped.setdefault(finding["id"], []).append(finding)
    severity_rank = {"caution": 1, "veto": 2}
    consolidated = []
    for candidate_id, rows in sorted(grouped.items()):
        primary = max(rows, key=lambda row: severity_rank.get(row.get("severity"), 0))
        if primary.get("severity") not in severity_rank:
            raise ValueError(f"invalid brand severity for {candidate_id}")
        consolidated.append(
            {
                "id": candidate_id,
                "severity": primary["severity"],
                "issue": primary["issue"],
                "mitigation": primary["mitigation"],
                "evidence": primary.get("evidence", []),
                "all_findings": rows,
            }
        )
    brand_output = {
        "lens": brand.get("lens"),
        "source_sha256": candidates_sha,
        "inspected_ids": sorted(long_ids),
        "not_assessed": brand.get("not_assessed", []),
        "findings": consolidated,
    }
    files.append(write(args.output_dir / "lens_brand.json", brand_output))

    renee = load_object(args.renee)
    if str(renee.get("source_sha256", "")).lower() != candidates_sha:
        raise ValueError("Renee source hash drift")
    renee["source_sha256"] = candidates_sha
    files.append(write(args.output_dir / "lens_renee.json", renee))

    verification = load_object(args.verification)
    verdict = verification.get("final_verdict")
    if not isinstance(verdict, dict) or verdict.get("status") != "PASS":
        raise ValueError("persona verification did not pass")
    files.append(write(args.output_dir / "persona-review-verification.json", verification))

    manifest = {
        "schema_version": 1,
        "contract": "podcast-highlight-long-shortlist-inputs-v1",
        "candidates_sha256": candidates_sha,
        "long_candidate_count": len(long_ids),
        "files": files,
    }
    write(args.output_dir / "PREP-MANIFEST.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
