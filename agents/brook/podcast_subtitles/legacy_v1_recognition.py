"""Pinned legacy V1 WhisperX baseline composition for shadow forensics only."""

from __future__ import annotations

import importlib.metadata
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .adapters.recognition import WhisperXRecognizerAdapter
from .hashing import hash_file, hash_object, measure_regular_file, sha256_bytes
from .ports import AdapterInputError, RecognitionRequest

LEGACY_V1_MODEL_REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
LEGACY_V1_ALIGNER_REVISION = "99ccb2737be22b8bb50dcfcc39ad4d567fb90cfd"
LEGACY_V1_ALIGNER = "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn"


class LegacyV1Manifest(BaseModel):
    """Validated view over a historical gen_manifest.json; raw bytes remain truth."""

    model_config = ConfigDict(extra="allow", frozen=True, strict=True)

    stage: Literal["gen"]
    model: Literal["large-v3"]
    language: Literal["zh"]
    hotwords: tuple[str, ...] = Field(min_length=1)

    @field_validator("hotwords")
    @classmethod
    def _valid_hotwords(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or value != value.strip() for value in values):
            raise ValueError("legacy V1 hotwords must be non-blank and trimmed")
        return values


def load_legacy_v1_manifest(path: str | Path) -> tuple[LegacyV1Manifest, bytes, str]:
    candidate = Path(path)
    digest, size = measure_regular_file(candidate)
    raw = candidate.read_bytes()
    if (sha256_bytes(raw), len(raw)) != (digest, size):
        raise ValueError("legacy V1 manifest changed while being read")
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"legacy V1 manifest contains duplicate key: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"legacy V1 manifest contains non-finite number: {value}")
            ),
        )
        if not isinstance(payload, dict):
            raise ValueError("legacy V1 manifest root must be a JSON object")
        hotwords = payload.get("hotwords")
        if (
            not isinstance(hotwords, list)
            or not hotwords
            or any(not isinstance(value, str) for value in hotwords)
        ):
            raise ValueError("legacy V1 manifest hotwords must be a non-empty string array")
        payload["hotwords"] = tuple(hotwords)
        manifest = LegacyV1Manifest.model_validate(payload, strict=True)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("legacy V1 gen_manifest.json is malformed or incompatible") from exc
    return manifest, raw, digest


def legacy_v1_initial_prompt(manifest: LegacyV1Manifest) -> str:
    seen: set[str] = set()
    words = [
        word for word in manifest.hotwords[:30] if not (word in seen or seen.add(word))
    ]
    return "、".join(words)


def _slim_aligned_segments(aligned: object) -> list[dict[str, object]]:
    """Match V1's persisted aligned-word contract without silent token loss."""

    if not isinstance(aligned, Mapping):
        raise AdapterInputError("legacy V1 WhisperX alignment must be an object")
    segments = aligned.get("segments")
    if not isinstance(segments, list) or not segments:
        raise AdapterInputError("legacy V1 WhisperX alignment returned no segments")
    slim_segments: list[dict[str, object]] = []
    word_count = 0
    for segment_index, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            raise AdapterInputError(
                f"legacy V1 aligned segment {segment_index} must be an object"
            )
        words = segment.get("words")
        if not isinstance(words, list):
            raise AdapterInputError(
                f"legacy V1 aligned segment {segment_index} requires a words list"
            )
        slim_words: list[dict[str, object]] = []
        for word_index, word in enumerate(words):
            if not isinstance(word, Mapping):
                raise AdapterInputError(
                    f"legacy V1 aligned word {segment_index}:{word_index} must be an object"
                )
            text = word.get("word")
            if (
                not isinstance(text, str)
                or not text.strip()
                or word.get("start") is None
                or word.get("end") is None
            ):
                raise AdapterInputError(
                    "legacy V1 aligned word "
                    f"{segment_index}:{word_index} requires exact word/start/end"
                )
            slim_words.append(
                {"word": text.strip(), "start": word["start"], "end": word["end"]}
            )
            word_count += 1
        slim_segments.append({"words": slim_words})
    if word_count == 0:
        raise AdapterInputError("legacy V1 WhisperX alignment returned no aligned words")
    return slim_segments


class _LazyPinnedWhisperXRunner:
    def __init__(self, initial_prompt: str) -> None:
        self._initial_prompt = initial_prompt
        self._whisperx = None
        self._model = None
        self._align_model = None
        self._align_metadata = None

    def __call__(self, audio_path: Path, _request: RecognitionRequest) -> Mapping[str, object]:
        if self._whisperx is None:
            import whisperx
            from huggingface_hub import snapshot_download

            model_snapshot = snapshot_download(
                "Systran/faster-whisper-large-v3",
                revision=LEGACY_V1_MODEL_REVISION,
                local_files_only=True,
            )
            aligner_snapshot = snapshot_download(
                LEGACY_V1_ALIGNER,
                revision=LEGACY_V1_ALIGNER_REVISION,
                local_files_only=True,
            )
            if Path(model_snapshot).resolve().name != LEGACY_V1_MODEL_REVISION:
                raise RuntimeError("resolved legacy V1 model snapshot revision drift")
            if Path(aligner_snapshot).resolve().name != LEGACY_V1_ALIGNER_REVISION:
                raise RuntimeError("resolved legacy V1 aligner snapshot revision drift")
            self._whisperx = whisperx
            asr_options: dict[str, object] = {
                "condition_on_previous_text": False,
                "compression_ratio_threshold": 2.4,
                "no_speech_threshold": 0.6,
                "initial_prompt": self._initial_prompt,
            }
            self._model = whisperx.load_model(
                model_snapshot,
                device="cuda",
                compute_type="float16",
                language="zh",
                asr_options=asr_options,
                local_files_only=True,
            )
            self._align_model, self._align_metadata = whisperx.load_align_model(
                language_code="zh",
                device="cuda",
                model_name=aligner_snapshot,
                model_cache_only=True,
            )
        assert self._whisperx is not None
        assert self._model is not None
        audio = self._whisperx.load_audio(str(audio_path))
        transcription = self._model.transcribe(audio, batch_size=16, language="zh")
        segments = transcription.get("segments") or []
        if not segments:
            raise AdapterInputError("legacy V1 WhisperX returned no recognition segments")
        aligned = self._whisperx.align(
            segments,
            self._align_model,
            self._align_metadata,
            audio,
            "cuda",
            return_char_alignments=False,
        )
        return {"language": "zh", "segments": _slim_aligned_segments(aligned)}


def _package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(f"legacy V1 runtime package is unavailable: {distribution}") from exc


def build_legacy_v1_whisperx_adapter(
    manifest_path: str | Path,
    *,
    runner: Callable[[Path, RecognitionRequest], Mapping[str, object]] | None = None,
    runner_runtime_components: Mapping[str, str] | None = None,
    runner_code_hash: str | None = None,
    runner_execution_mode: Literal["fixture", "local", "other"] | None = None,
) -> WhisperXRecognizerAdapter:
    manifest, _raw, manifest_hash = load_legacy_v1_manifest(manifest_path)
    prompt = legacy_v1_initial_prompt(manifest)
    prompt_hash = sha256_bytes(prompt.encode("utf-8"))
    source_hash = hash_file(Path(__file__))
    historical_source_hash = hash_file(Path(__file__).parents[3] / "shared" / "transcriber.py")
    if runner is None:
        runner = _LazyPinnedWhisperXRunner(prompt)
        runner_runtime_components = {
            "ctranslate2": _package_version("ctranslate2"),
            "faster-whisper": _package_version("faster-whisper"),
            "huggingface-hub": _package_version("huggingface-hub"),
            "legacy_initial_prompt_sha256": prompt_hash,
            "legacy_manifest_sha256": manifest_hash,
            "torch": _package_version("torch"),
            "whisperx": _package_version("whisperx"),
        }
        runner_code_hash = source_hash
        runner_execution_mode = "local"
    elif (
        runner_runtime_components is None
        or runner_code_hash is None
        or runner_execution_mode is None
    ):
        raise ValueError("fixture legacy runner requires explicit runtime/code/execution identity")
    runtime = dict(runner_runtime_components)
    runtime.update(
        {
            "legacy_initial_prompt_sha256": prompt_hash,
            "legacy_manifest_sha256": manifest_hash,
            "legacy_runner_source_sha256": source_hash,
            "legacy_transcriber_source_sha256": historical_source_hash,
        }
    )
    composite_runner_hash = hash_object(
        {
            "custom_runner_code_hash": runner_code_hash,
            "legacy_runner_source_sha256": source_hash,
            "legacy_transcriber_source_sha256": historical_source_hash,
            "legacy_manifest_sha256": manifest_hash,
            "legacy_initial_prompt_sha256": prompt_hash,
        }
    )
    return WhisperXRecognizerAdapter(
        model="large-v3",
        device="cuda",
        compute_type="float16",
        batch_size=16,
        runner=runner,
        model_version=LEGACY_V1_MODEL_REVISION,
        aligner=LEGACY_V1_ALIGNER,
        aligner_version=LEGACY_V1_ALIGNER_REVISION,
        runner_runtime_components=runtime,
        runner_code_hash=composite_runner_hash,
        runner_execution_mode=runner_execution_mode,
    )


__all__ = [
    "LEGACY_V1_ALIGNER",
    "LEGACY_V1_ALIGNER_REVISION",
    "LEGACY_V1_MODEL_REVISION",
    "LegacyV1Manifest",
    "build_legacy_v1_whisperx_adapter",
    "legacy_v1_initial_prompt",
    "load_legacy_v1_manifest",
]
