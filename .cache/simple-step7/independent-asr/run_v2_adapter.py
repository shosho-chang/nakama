from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.brook.podcast_subtitles.adapters.faster_whisper_recognition import (
    FasterWhisperRecognizerAdapter,
)
from agents.brook.podcast_subtitles.adapters.recognition import Qwen3ASRRecognizerAdapter
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_file, sha256_bytes
from agents.brook.podcast_subtitles.ports import RecognitionRequest
from agents.brook.podcast_subtitles.recognition_run import RecognitionRunRepository


def _file_uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise RuntimeError(f"raw output is not a file URI: {uri}")
    path = unquote(parsed.path)
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return Path(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("faster", "qwen"), required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--invocation-id", required=True)
    parser.add_argument("--run-repository", type=Path)
    args = parser.parse_args()

    audio = args.audio.resolve(strict=True)
    request = RecognitionRequest(
        episode_id=args.episode_id,
        invocation_id=args.invocation_id,
        normalized_audio=audio,
        expected_normalized_audio_hash=hash_file(audio),
        raw_output_dir=args.raw_dir.resolve(),
        language_hint="zh-Hant-TW",
    )
    if args.engine == "faster":
        repository = (
            RecognitionRunRepository(args.run_repository.resolve())
            if args.run_repository is not None
            else None
        )
        adapter = FasterWhisperRecognizerAdapter(
            model_revision="edaa852ec7e145841d8ffdb056a99866b5f0a478",
            local_files_only=True,
            recognition_run_repository=repository,
            logical_namespace="degraded-simple-step7-independent-audio"
            if repository is not None
            else None,
        )
    else:
        if args.run_repository is not None:
            parser.error("--run-repository is currently supported only for Faster-Whisper")
        adapter = Qwen3ASRRecognizerAdapter(
            model_revision="7278e1e70fe206f11671096ffdd38061171dd6e5",
            forced_aligner_revision="c7cbfc2048c462b0d63a45797104fc9db3ad62b7",
            local_files_only=True,
        )

    evidence = adapter.recognize(request)
    raw_path = _file_uri_path(evidence.raw_output.uri)
    raw_bytes = raw_path.read_bytes()
    replayed = adapter.verify(evidence, request=request, raw_output=raw_bytes)
    if replayed != evidence:
        raise RuntimeError("adapter verification replay changed Recognition Evidence")

    evidence_bytes = canonical_json_bytes(evidence.model_dump(mode="json"))
    output = args.evidence_output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if output.read_bytes() != evidence_bytes:
            raise RuntimeError(f"existing evidence differs: {output}")
    else:
        output.write_bytes(evidence_bytes)
    print(
        f"engine={args.engine} audio_sha256={request.expected_normalized_audio_hash} "
        f"raw_sha256={sha256_bytes(raw_bytes)} evidence_sha256={sha256_bytes(evidence_bytes)} "
        f"tokens={len(evidence.tokens)} output={output}"
    )
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
