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

DECISIONS = (
    {
        "stem": "cues-147",
        "cues": (147,),
        "expected": {147: "你要壓住自己的性質"},
        "replacements": {},
        "decision": "keep_original_model_conflict",
        "reason": "Faster supports 性質; Qwen supports 性子. No independent audio quorum.",
    },
    {
        "stem": "cues-234",
        "cues": (234,),
        "expected": {234: "數變太多"},
        "replacements": {},
        "decision": "keep_original_model_conflict",
        "reason": "Faster emits 輸變 and Qwen emits 宿變; neither resolves the word.",
    },
    {
        "stem": "cues-444",
        "cues": (444,),
        "expected": {444: "其實35000萬"},
        "replacements": {444: "其實三千、五千萬"},
        "decision": "accept_audio_quorum",
        "reason": (
            "Both independent models emit 三千五千萬; punctuation only separates "
            "the two amounts."
        ),
    },
    {
        "stem": "cues-599",
        "cues": (599,),
        "expected": {599: "薪水可能年薪就是半倍"},
        "replacements": {599: "薪水可能年薪就是翻倍"},
        "decision": "accept_audio_quorum",
        "reason": "Both independent models emit 翻倍; the minimal edit preserves the cue wording.",
    },
    {
        "stem": "cues-721-722",
        "cues": (721,),
        "expected": {721: "WECS的"},
        "replacements": {},
        "decision": "keep_original_model_unresolved",
        "reason": "Both independent models also emit WECS; CS versus EECS is not resolved.",
    },
    {
        "stem": "cues-721-722",
        "cues": (722,),
        "expected": {722: "我不是WECS"},
        "replacements": {},
        "decision": "keep_original_model_unresolved",
        "reason": "Both independent models also emit WECS; CS versus EECS is not resolved.",
    },
    {
        "stem": "cues-859",
        "cues": (859,),
        "expected": {859: "我用自己是一個好的動作"},
        "replacements": {859: "毋庸置疑是一個好的動作"},
        "decision": "accept_audio_quorum",
        "reason": "Both independent models emit 毋庸置疑 in the exact cue context.",
    },
    {
        "stem": "cues-1390",
        "cues": (1390,),
        "expected": {1390: "NVIDIA 華為新手公司"},
        "replacements": {1390: "NVIDIA 黃仁勳的公司"},
        "decision": "accept_audio_plus_reference",
        "reason": (
            "Qwen emits 黃仁勳; Faster emits a close phonetic name; NVIDIA company "
            "context uniquely corroborates 黃仁勳."
        ),
    },
    {
        "stem": "cues-1529-1530",
        "cues": (1529, 1530),
        "expected": {1529: "他們鐵定是一般人", 1530: "不能承受競爭的壓力"},
        "replacements": {1529: "他們鐵定是承受一般人"},
        "decision": "accept_audio_quorum_minimal_edit",
        "reason": (
            "Both independent models contain 承受一般人不能承受; only the omitted "
            "verb is restored."
        ),
    },
    {
        "stem": "cues-1871",
        "cues": (1871,),
        "expected": {1871: "你賺三四年可能不夠"},
        "replacements": {},
        "decision": "keep_original_audio_quorum",
        "reason": "Both independent models emit 三四年, rejecting the proposed 三四十年.",
    },
    {
        "stem": "cues-2092-2093",
        "cues": (2092, 2093),
        "expected": {2092: "他說日本沒有去", 2093: "最後要去美國"},
        "replacements": {2092: "他說日本不用去"},
        "decision": "accept_audio_quorum_minimal_edit",
        "reason": (
            "Both independent models emit 日本不用去; cue 2093 remains unchanged "
            "because 就要 versus 最後 differs."
        ),
    },
    {
        "stem": "cues-2126-2130",
        "cues": (2126,),
        "expected": {2126: "現在200多萬的"},
        "replacements": {},
        "decision": "keep_original_audio_quorum",
        "reason": "Both independent models emit 200多萬, rejecting the proposed 600多萬.",
    },
    {
        "stem": "cues-2126-2130",
        "cues": (2129, 2130),
        "expected": {2129: "但是她的那一級", 2130: "我們叫做她的薪水"},
        "replacements": {},
        "decision": "keep_original_model_conflict",
        "reason": "The models disagree on 成倍/那一級 and do not establish a safe replacement.",
    },
)


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


def _load_evidence(path: Path, *, clip_hash: str, expected_adapter: str) -> dict[str, object]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if value["adapter"] != expected_adapter or value["normalized_audio_hash"] != clip_hash:
        raise ValueError(f"evidence binding mismatch: {path}")
    raw_path = _file_uri_path(value["raw_output"]["uri"])
    if _hash_file(raw_path) != value["raw_output_hash"]:
        raise ValueError(f"raw evidence hash mismatch: {raw_path}")
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(raw),
        "adapter": value["adapter"],
        "model": value["model"],
        "raw_path": str(raw_path.resolve()),
        "raw_sha256": value["raw_output_hash"],
        "transcript": "".join(token["text"] for token in value["tokens"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-srt", type=Path, required=True)
    parser.add_argument("--unresolved", type=Path, required=True)
    parser.add_argument("--clips-dir", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output-srt", type=Path, required=True)
    parser.add_argument("--output-ledger", type=Path, required=True)
    args = parser.parse_args()

    source_raw = args.source_srt.read_bytes()
    cues = _parse_srt(source_raw)
    cue_by_number = {cue.number: cue for cue in cues}
    unresolved_raw = args.unresolved.read_bytes()
    unresolved = json.loads(unresolved_raw)
    unresolved_sets = {
        tuple(item["base_component"]["cue_numbers"]) for item in unresolved["items"]
    }

    evidence_sets: dict[str, object] = {}
    replacements: dict[int, str] = {}
    ledger_decisions: list[dict[str, object]] = []
    for decision in DECISIONS:
        stem = str(decision["stem"])
        cue_numbers = tuple(decision["cues"])
        if cue_numbers not in unresolved_sets:
            raise ValueError(f"decision does not bind an unresolved component: {cue_numbers}")
        for cue_number, expected in decision["expected"].items():
            if cue_by_number[cue_number].text != expected:
                raise ValueError(f"source cue {cue_number} differs from adjudication input")
        for cue_number, replacement in decision["replacements"].items():
            if cue_number in replacements and replacements[cue_number] != replacement:
                raise ValueError(f"conflicting replacement for cue {cue_number}")
            replacements[cue_number] = replacement

        if stem not in evidence_sets:
            clip = (args.clips_dir / f"{stem}.wav").resolve(strict=True)
            clip_hash = _hash_file(clip)
            faster = _load_evidence(
                args.evidence_root / "faster" / stem / "evidence.json",
                clip_hash=clip_hash,
                expected_adapter="faster-whisper-word-timestamps",
            )
            qwen = _load_evidence(
                args.evidence_root / "qwen" / stem / "evidence.json",
                clip_hash=clip_hash,
                expected_adapter="qwen3-asr-forced-alignment",
            )
            if faster["model"] == qwen["model"]:
                raise ValueError(f"independent models collapsed for {stem}")
            evidence_sets[stem] = {
                "clip_path": str(clip),
                "clip_sha256": clip_hash,
                "faster": faster,
                "qwen": qwen,
            }
        ledger_decisions.append(
            {
                "stem": stem,
                "cue_numbers": list(cue_numbers),
                "original": {str(key): value for key, value in decision["expected"].items()},
                "replacements": {
                    str(key): value for key, value in decision["replacements"].items()
                },
                "decision": decision["decision"],
                "reason": decision["reason"],
            }
        )

    output_srt = _render_srt(cues, replacements)
    reparsed = _parse_srt(output_srt)
    if len(reparsed) != len(cues):
        raise ValueError("output cue count changed")
    changed = [cue.number for cue in reparsed if cue.text != cue_by_number[cue.number].text]
    if changed != sorted(replacements):
        raise ValueError("output changed outside the accepted cue set")

    ledger = {
        "schema_version": 1,
        "contract": "podcast-subtitle-v2-degraded-priority-audio-adjudication-v1",
        "episode_id": "20260814-moboo",
        "provenance_status": "degraded_independent_audio_quorum_not_full_v2_checkpoint",
        "source_srt": {
            "path": str(args.source_srt.resolve()),
            "sha256": _sha256(source_raw),
        },
        "source_unresolved": {
            "path": str(args.unresolved.resolve()),
            "sha256": _sha256(unresolved_raw),
        },
        "evidence_sets": evidence_sets,
        "decisions": ledger_decisions,
        "decision_count": len(ledger_decisions),
        "accepted_component_count": sum(bool(item["replacements"]) for item in DECISIONS),
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
        f"decisions={len(ledger_decisions)} accepted={ledger['accepted_component_count']} "
        f"changed_cues={changed} srt_sha256={_sha256(output_srt)} "
        f"ledger_sha256={_sha256(ledger_raw)}"
    )
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
