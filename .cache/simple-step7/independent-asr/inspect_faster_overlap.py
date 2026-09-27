from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.brook.podcast_subtitles.adapters.faster_whisper_recognition import (
    FasterWhisperRecognizerAdapter,
)
from agents.brook.podcast_subtitles.recognition_run import (
    build_recognition_audio_binding,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--run-repository", type=Path, required=True)
    args = parser.parse_args()

    audio_path = args.audio.resolve(strict=True)
    adapter = FasterWhisperRecognizerAdapter(
        model_revision="edaa852ec7e145841d8ffdb056a99866b5f0a478",
        local_files_only=True,
    )
    audio = build_recognition_audio_binding(audio_path)
    plans = adapter._plan_chunks(audio)
    observation_paths = sorted(args.run_repository.rglob("adapter-observation.json"))
    if len(observation_paths) != len(plans):
        raise RuntimeError(
            f"observation/plan count mismatch: {len(observation_paths)} != {len(plans)}"
        )

    owned_words: list[dict[str, object]] = []
    receipts = []
    for plan, observation_path in zip(plans, observation_paths, strict=True):
        receipt = adapter._chunk_receipt(
            plan=plan,
            audio=audio,
            # The overlap diagnostic uses only grouped word topology. The
            # authenticated run already sealed each exact derived-chunk digest.
            chunk_bytes=b"diagnostic-not-a-replay",
            provider_output=json.loads(observation_path.read_text("utf-8")),
        )
        receipts.append(receipt)
        for word in adapter._words_in_range(
            receipt,
            start_sample=receipt.owned_start_sample,
            end_sample=receipt.owned_end_sample,
        ):
            owned_words.append(
                {
                    "chunk": receipt.index,
                    "word_index": word.index,
                    "text": word.word,
                    "start_sample": word.global_start_sample,
                    "end_sample": word.global_end_sample,
                    "midpoint_sample": (
                        word.global_start_sample + word.global_end_sample
                    )
                    // 2,
                }
            )

    overlaps: list[dict[str, object]] = []
    for left, right in zip(owned_words, owned_words[1:]):
        overlap_samples = int(left["end_sample"]) - int(right["start_sample"])
        if overlap_samples > 0:
            overlaps.append(
                {
                    "overlap_samples": overlap_samples,
                    "overlap_ms": overlap_samples * 1_000 / audio.sample_rate_hz,
                    "left": left,
                    "right": right,
                }
            )
    seams: list[dict[str, object]] = []
    for left, right in zip(receipts, receipts[1:]):
        comparison_start = max(left.inference_start_sample, right.inference_start_sample)
        comparison_end = min(left.inference_end_sample, right.inference_end_sample)
        left_text = "".join(
            word.word
            for word in adapter._words_in_range(
                left, start_sample=comparison_start, end_sample=comparison_end
            )
        )
        right_text = "".join(
            word.word
            for word in adapter._words_in_range(
                right, start_sample=comparison_start, end_sample=comparison_end
            )
        )
        seams.append(
            {
                "left_chunk": left.index,
                "right_chunk": right.index,
                "matched": adapter._seam_text(left_text) == adapter._seam_text(right_text),
                "left_text": left_text,
                "right_text": right_text,
            }
        )
    print(
        json.dumps(
            {
                "word_count": len(owned_words),
                "overlaps": overlaps,
                "conflicting_seams": [seam for seam in seams if not seam["matched"]],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
