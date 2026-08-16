from __future__ import annotations

import pytest

from agents.brook.podcast_subtitles.hashing import hash_object
from agents.brook.podcast_subtitles.ports import RecognitionModelIdentity
from agents.brook.podcast_subtitles.recognition_policy import (
    PRODUCTION_RECOGNITION_INDEPENDENCE_POLICY,
)


def _identity(*, adapter: str, model: str, runtime: str, config: str) -> RecognitionModelIdentity:
    components = ((runtime, "1"),)
    return RecognitionModelIdentity(
        adapter_name=adapter,
        adapter_version="1",
        model=model,
        model_version="a" * 40,
        aligner="integrated",
        aligner_version="a" * 40,
        runtime_components=components,
        runtime_hash=hash_object({"runtime_components": components}),
        adapter_code_hash="b" * 64,
        config_hash=hash_object({"config": config}),
        execution_mode="fixture",
    )


def test_qwen_primary_and_faster_whisper_secondary_are_independent() -> None:
    receipt = PRODUCTION_RECOGNITION_INDEPENDENCE_POLICY.validate(
        (
            _identity(
                adapter="qwen3-asr-forced-alignment",
                model="Qwen/Qwen3-ASR-1.7B",
                runtime="qwen_asr",
                config="primary",
            ),
            _identity(
                adapter="faster-whisper-word-timestamps",
                model="Systran/faster-whisper-large-v3",
                runtime="ctranslate2",
                config="secondary",
            ),
        )
    )

    assert receipt.bindings[0].role == "primary"
    assert receipt.bindings[1].role == "corroborating"
    assert receipt.bindings[0].model_family == "qwen3-asr"
    assert receipt.bindings[1].model_family == "whisper"


def test_same_qwen_family_with_different_prompt_or_weights_is_not_independent() -> None:
    primary = _identity(
        adapter="qwen3-asr-forced-alignment",
        model="Qwen/Qwen3-ASR-1.7B",
        runtime="qwen_asr",
        config="empty-context",
    )
    correlated = _identity(
        adapter="qwen3-asr-forced-alignment",
        model="Qwen/Qwen3-ASR-0.6B",
        runtime="qwen_asr",
        config="traditional-prompt",
    )

    with pytest.raises(ValueError, match="roles must be"):
        PRODUCTION_RECOGNITION_INDEPENDENCE_POLICY.validate((primary, correlated))


def test_production_recognition_fails_closed_without_secondary() -> None:
    primary = _identity(
        adapter="qwen3-asr-forced-alignment",
        model="Qwen/Qwen3-ASR-1.7B",
        runtime="qwen_asr",
        config="empty-context",
    )

    with pytest.raises(ValueError, match="exactly one primary"):
        PRODUCTION_RECOGNITION_INDEPENDENCE_POLICY.validate((primary,))


def test_production_recognition_rejects_reversed_roles() -> None:
    qwen = _identity(
        adapter="qwen3-asr-forced-alignment",
        model="Qwen/Qwen3-ASR-1.7B",
        runtime="qwen_asr",
        config="primary",
    )
    whisper = _identity(
        adapter="faster-whisper-word-timestamps",
        model="Systran/faster-whisper-large-v3",
        runtime="ctranslate2",
        config="secondary",
    )

    with pytest.raises(ValueError, match="Qwen primary then Faster-Whisper"):
        PRODUCTION_RECOGNITION_INDEPENDENCE_POLICY.validate((whisper, qwen))


def test_production_recognition_rejects_duplicate_third_secondary() -> None:
    qwen = _identity(
        adapter="qwen3-asr-forced-alignment",
        model="Qwen/Qwen3-ASR-1.7B",
        runtime="qwen_asr",
        config="primary",
    )
    whisper = _identity(
        adapter="faster-whisper-word-timestamps",
        model="Systran/faster-whisper-large-v3",
        runtime="ctranslate2",
        config="secondary",
    )

    with pytest.raises(ValueError, match="exactly one primary"):
        PRODUCTION_RECOGNITION_INDEPENDENCE_POLICY.validate((qwen, whisper, whisper))
