from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.podcast_subtitle_v2_simple_step7 import (
    _canonical_bytes,
    _parse_srt,
    _write_deterministic,
)

ALREADY_ADJUDICATED = {
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


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--source-srt", type=Path, required=True)
    parser.add_argument("--unresolved", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    cues = _parse_srt(args.source_srt.read_bytes())
    cue_by_number = {cue.number: cue for cue in cues}
    unresolved_raw = args.unresolved.read_bytes()
    unresolved = json.loads(unresolved_raw)
    pending = [
        item
        for item in unresolved["items"]
        if item["arbitration"]["major_risk"]
        and tuple(item["base_component"]["cue_numbers"]) not in ALREADY_ADJUDICATED
    ]
    if len(pending) != 23:
        raise ValueError(f"remaining major component count drifted: {len(pending)}")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[dict[str, object]] = []
    with wave.open(str(args.audio.resolve(strict=True)), "rb") as source:
        params = source.getparams()
        frame_count = source.getnframes()
        rate = source.getframerate()
        duration_ms = frame_count * 1_000 // rate
        for item in pending:
            cue_numbers = tuple(item["base_component"]["cue_numbers"])
            selected = [cue_by_number[number] for number in cue_numbers]
            start_ms = max(0, selected[0].start_ms - 5_000)
            end_ms = min(duration_ms, selected[-1].end_ms + 5_000)
            start_frame = round(start_ms * rate / 1_000)
            end_frame = round(end_ms * rate / 1_000)
            source.setpos(start_frame)
            pcm = source.readframes(end_frame - start_frame)
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as target:
                target.setparams(params)
                target.writeframes(pcm)
            clip_bytes = buffer.getvalue()
            stem = "major-cues-" + "-".join(str(number) for number in cue_numbers)
            clip_path = output_dir / f"{stem}.wav"
            _write_deterministic(clip_path, clip_bytes)
            jobs.append(
                {
                    "stem": stem,
                    "cue_numbers": list(cue_numbers),
                    "clip_path": str(clip_path),
                    "clip_sha256": _sha256(clip_bytes),
                    "clip_start_ms": start_ms,
                    "clip_end_ms": end_ms,
                    "target_start_ms": selected[0].start_ms - start_ms,
                    "target_end_ms": selected[-1].end_ms - start_ms,
                    "original": item["base_component"]["original"],
                    "a_proposals": item["arbitration"]["a_proposals"],
                    "b_proposals": item["arbitration"]["b_proposals"],
                    "reason": item["arbitration"]["reason"],
                }
            )

    manifest = {
        "schema_version": 1,
        "contract": "podcast-subtitle-v2-degraded-remaining-major-clips-v1",
        "episode_id": "20260814-moboo",
        "audio_path": str(args.audio.resolve()),
        "audio_sha256": _hash_file(args.audio),
        "source_srt_sha256": _hash_file(args.source_srt),
        "unresolved_sha256": _sha256(unresolved_raw),
        "context_before_ms": 5_000,
        "context_after_ms": 5_000,
        "job_count": len(jobs),
        "jobs": jobs,
    }
    _write_deterministic(args.manifest.resolve(), _canonical_bytes(manifest))
    print(f"jobs={len(jobs)} manifest={args.manifest.resolve()}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
