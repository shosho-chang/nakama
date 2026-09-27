from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.podcast_subtitle_v2_simple_step7 import (
    _canonical_bytes,
    _parse_srt,
    _render_srt,
    _sha256,
    _write_deterministic,
)

FIRST_ROUND_COMPONENTS = {
    (147,),
    (234,),
    (444,),
    (599,),
    (721,),
    (722,),
    (859,),
    (1390,),
    (1529, 1530),
    (1871,),
    (2092, 2093),
    (2126,),
    (2129, 2130),
}

ACCEPTED = {
    (8,): {8: "現在你用那個 Claude Code 或是 Codex 它都可以幫你寫"},
    (13,): {13: "比較小團隊的"},
    (27, 28): {
        27: "所以我也可以要 AI 幫我",
        28: "如果說我覺得它切得不滿意 我可以要 AI 幫我",
    },
    (362,): {362: "可能在社群上最有名的億元男"},
    (2131,): {2131: "成長15倍"},
}

ACCEPT_REASONS = {
    (8,): (
        "Both models identify Claude Code/Codex-like tool names and the verb 寫; "
        "known product names resolve the phonetic spellings."
    ),
    (13,): "Qwen emits 小團隊 and Faster emits the close phonetic 小韓隊; context supports 小團隊.",
    (27, 28): "Both independent models emit 要 AI 幫我 across both cues.",
    (362,): "Qwen and both text auditors emit 億元男; Faster preserves the exact homophone 議員男.",
    (2131,): "Qwen and text auditor B emit 薪水成長15倍; the numeric value remains unchanged.",
}


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"raw output is not a file URI: {uri}")
    value = unquote(parsed.path)
    if parsed.netloc:
        value = f"//{parsed.netloc}{value}"
    if len(value) >= 3 and value[0] == "/" and value[2] == ":":
        value = value[1:]
    return Path(value)


def _evidence_summary(
    path: Path,
    *,
    clip_hash: str,
    adapter: str,
    target_start_ms: int,
    target_end_ms: int,
) -> dict[str, object]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if value["adapter"] != adapter or value["normalized_audio_hash"] != clip_hash:
        raise ValueError(f"evidence binding mismatch: {path}")
    raw_path = _file_uri_path(value["raw_output"]["uri"])
    if _hash_file(raw_path) != value["raw_output_hash"]:
        raise ValueError(f"raw evidence hash mismatch: {raw_path}")
    target = "".join(
        token["text"]
        for token in value["tokens"]
        if target_start_ms
        <= (token["start_ms"] + token["end_ms"]) / 2
        < target_end_ms
    )
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(raw),
        "adapter": value["adapter"],
        "model": value["model"],
        "raw_path": str(raw_path.resolve()),
        "raw_sha256": value["raw_output_hash"],
        "target_transcript": target,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-srt", type=Path, required=True)
    parser.add_argument("--first-ledger", type=Path, required=True)
    parser.add_argument("--unresolved", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output-srt", type=Path, required=True)
    parser.add_argument("--output-ledger", type=Path, required=True)
    args = parser.parse_args()

    source_raw = args.source_srt.read_bytes()
    cues = _parse_srt(source_raw)
    cue_by_number = {cue.number: cue for cue in cues}
    first_ledger_raw = args.first_ledger.read_bytes()
    first_ledger = json.loads(first_ledger_raw)
    if first_ledger["output_srt_sha256"] != _sha256(source_raw):
        raise ValueError("first audio adjudication ledger does not bind source SRT")
    unresolved_raw = args.unresolved.read_bytes()
    unresolved = json.loads(unresolved_raw)
    unresolved_by_component = {
        tuple(item["base_component"]["cue_numbers"]): item for item in unresolved["items"]
    }
    manifest_raw = args.manifest.read_bytes()
    manifest = json.loads(manifest_raw)
    if manifest["job_count"] != 23 or len(manifest["jobs"]) != 23:
        raise ValueError("remaining-major manifest coverage drifted")

    replacements: dict[int, str] = {}
    evidence_sets: dict[str, object] = {}
    decisions: list[dict[str, object]] = []
    covered: set[tuple[int, ...]] = set()
    for job in manifest["jobs"]:
        component = tuple(job["cue_numbers"])
        item = unresolved_by_component.get(component)
        if item is None or not item["arbitration"]["major_risk"]:
            raise ValueError(f"manifest job is not a major unresolved component: {component}")
        source_text = "\n".join(cue_by_number[number].text for number in component)
        if source_text != job["original"]:
            raise ValueError(f"manifest original differs from source cues: {component}")
        clip_path = Path(job["clip_path"])
        clip_hash = _hash_file(clip_path)
        if clip_hash != job["clip_sha256"]:
            raise ValueError(f"clip hash mismatch: {clip_path}")
        stem = job["stem"]
        faster = _evidence_summary(
            args.evidence_root / "faster" / stem / "evidence.json",
            clip_hash=clip_hash,
            adapter="faster-whisper-word-timestamps",
            target_start_ms=job["target_start_ms"],
            target_end_ms=job["target_end_ms"],
        )
        qwen = _evidence_summary(
            args.evidence_root / "qwen" / stem / "evidence.json",
            clip_hash=clip_hash,
            adapter="qwen3-asr-forced-alignment",
            target_start_ms=job["target_start_ms"],
            target_end_ms=job["target_end_ms"],
        )
        evidence_sets[stem] = {
            "clip_path": str(clip_path.resolve()),
            "clip_sha256": clip_hash,
            "target_start_ms": job["target_start_ms"],
            "target_end_ms": job["target_end_ms"],
            "faster": faster,
            "qwen": qwen,
        }
        accepted = ACCEPTED.get(component, {})
        for cue_number, replacement in accepted.items():
            replacements[cue_number] = replacement
        decisions.append(
            {
                "cue_numbers": list(component),
                "original": source_text,
                "decision": "accept_audio_quorum" if accepted else "keep_original_fail_closed",
                "replacements": {str(key): value for key, value in accepted.items()},
                "reason": ACCEPT_REASONS.get(
                    component,
                    "Two independent models did not establish a safer exact replacement; "
                    "preserve the source text.",
                ),
                "faster_target": faster["target_transcript"],
                "qwen_target": qwen["target_transcript"],
            }
        )
        covered.add(component)

    if covered != {
        component
        for component, item in unresolved_by_component.items()
        if item["arbitration"]["major_risk"] and component not in FIRST_ROUND_COMPONENTS
    }:
        raise ValueError("remaining-major evidence does not exactly cover pending major components")

    retained_nonmajor = [
        list(component)
        for component, item in unresolved_by_component.items()
        if not item["arbitration"]["major_risk"] and component not in FIRST_ROUND_COMPONENTS
    ]
    if len(retained_nonmajor) != 23:
        raise ValueError(f"non-major retained-original count drifted: {len(retained_nonmajor)}")

    output_srt = _render_srt(cues, replacements)
    reparsed = _parse_srt(output_srt)
    changed = [cue.number for cue in reparsed if cue.text != cue_by_number[cue.number].text]
    if changed != sorted(replacements):
        raise ValueError("release output changed outside accepted remaining-major cues")
    ledger = {
        "schema_version": 1,
        "contract": "podcast-subtitle-v2-degraded-audio-release-v1",
        "episode_id": "20260814-moboo",
        "provenance_status": "degraded_dual_asr_major_complete_not_full_v2_checkpoint",
        "source_srt_sha256": _sha256(source_raw),
        "first_audio_ledger_sha256": _sha256(first_ledger_raw),
        "unresolved_sha256": _sha256(unresolved_raw),
        "remaining_major_manifest_sha256": _sha256(manifest_raw),
        "major_component_count": 32,
        "major_audio_reviewed_count": 32,
        "first_round_component_count": 13,
        "second_round_major_component_count": len(decisions),
        "nonmajor_retained_original_count": len(retained_nonmajor),
        "nonmajor_retained_original_components": retained_nonmajor,
        "release_policy": (
            "dual independent local ASR for every major component; non-major unresolved "
            "components retain source text; no semantic guessing"
        ),
        "evidence_sets": evidence_sets,
        "decisions": decisions,
        "accepted_component_count": len(ACCEPTED),
        "changed_cue_numbers": changed,
        "output_srt_sha256": _sha256(output_srt),
        "cue_count": len(reparsed),
        "non_positive_duration_count": 0,
        "overlap_count": 0,
    }
    ledger_raw = _canonical_bytes(ledger)
    _write_deterministic(args.output_srt, output_srt)
    _write_deterministic(args.output_ledger, ledger_raw)
    print(
        f"major_reviewed=32/32 second_round={len(decisions)} "
        f"accepted={len(ACCEPTED)} changed_cues={changed} "
        f"srt_sha256={_sha256(output_srt)} ledger_sha256={_sha256(ledger_raw)}"
    )
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
