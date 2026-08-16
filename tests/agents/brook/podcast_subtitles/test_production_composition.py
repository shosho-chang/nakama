from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.adapters import MemoRecognizerAdapter
from agents.brook.podcast_subtitles.composition import FactoryContextV1
from agents.brook.podcast_subtitles.module import PodcastSubtitleV2
from agents.brook.podcast_subtitles.production import (
    ProductionConfigurationError,
    build_production,
    load_production_config,
)
from tests.agents.brook.podcast_subtitles.test_memo_first_production import _memo_fixture


def _valid_environment(tmp_path: Path) -> dict[str, str]:
    _memo_fixture(tmp_path)
    return {
        "PODCAST_SUBTITLE_V2_NORMALIZED_HANDOFF_MANIFEST": str(tmp_path / "handoff.json"),
        "PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_MANIFEST": str(tmp_path / "memo-recognition.json"),
        "PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_SOURCE_EXPORT": str(tmp_path / "memo.stdout"),
        "PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_ACCEPTANCE_RECEIPT": str(tmp_path / "recognition-acceptance.json"),
        "PODCAST_SUBTITLE_V2_MEMO_CUE_MANIFEST": str(tmp_path / "memo-cues.json"),
        "PODCAST_SUBTITLE_V2_MEMO_CUE_SOURCE_EXPORT": str(tmp_path / "memo-gui.srt"),
        "PODCAST_SUBTITLE_V2_MEMO_CUE_ACCEPTANCE_RECEIPT": str(tmp_path / "cue-acceptance.json"),
        "PODCAST_SUBTITLE_V2_TEXT_AUDIT_MODEL": "gpt-5.6-sol",
        "PODCAST_SUBTITLE_V2_TEXT_AUDIT_MODEL_VERSION": "2026-08-12",
        "PODCAST_SUBTITLE_V2_SEMANTIC_MODEL": "gpt-5.6-sol",
        "PODCAST_SUBTITLE_V2_SEMANTIC_MODEL_VERSION": "2026-08-12",
        "PODCAST_SUBTITLE_V2_AUDIO_AUDIT_MODEL": "gemini-3.6-flash",
        "PODCAST_SUBTITLE_V2_AUDIO_AUDIT_MODEL_VERSION": "2026-07-22",
    }


def test_production_requires_explicit_memo_and_upstream_handoff_paths(tmp_path: Path) -> None:
    environment = _valid_environment(tmp_path)
    del environment["PODCAST_SUBTITLE_V2_MEMO_CUE_MANIFEST"]
    with pytest.raises(ProductionConfigurationError, match="MEMO_CUE_MANIFEST"):
        load_production_config(environment)


def test_production_defaults_to_memo_only_and_no_auphonic_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = _valid_environment(tmp_path)
    monkeypatch.setattr("agents.brook.podcast_subtitles.production.os.environ", environment)
    before = {name for name in sys.modules if ".adapters.auphonic" in name}
    module = build_production(FactoryContextV1(1, tmp_path / "episode", None))
    after = {name for name in sys.modules if ".adapters.auphonic" in name}
    assert isinstance(module, PodcastSubtitleV2)
    assert len(module._recognizers) == 1
    assert isinstance(module._recognizers[0], MemoRecognizerAdapter)
    assert module._recognition_independence_policy is None
    assert module._memo_boundary_authority_factory is not None
    assert type(module._normalizer).__name__ == "VerifiedNormalizedAudioHandoffAdapter"
    assert after == before


@pytest.mark.parametrize(
    ("toggle", "required"),
    [
        ("PODCAST_SUBTITLE_V2_ENABLE_QWEN_CORROBORATION", "QWEN_MODEL_REVISION"),
        (
            "PODCAST_SUBTITLE_V2_ENABLE_FASTER_WHISPER_CORROBORATION",
            "FASTER_WHISPER_MODEL_REVISION",
        ),
    ],
)
def test_corroborators_are_opt_in_and_require_pinned_revisions(
    tmp_path: Path, toggle: str, required: str
) -> None:
    environment = _valid_environment(tmp_path)
    environment[toggle] = "true"
    with pytest.raises(ProductionConfigurationError, match=required):
        load_production_config(environment)


def test_optional_corroborators_never_become_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = _valid_environment(tmp_path)
    environment.update(
        {
            "PODCAST_SUBTITLE_V2_ENABLE_QWEN_CORROBORATION": "true",
            "PODCAST_SUBTITLE_V2_QWEN_MODEL_REVISION": "1" * 40,
            "PODCAST_SUBTITLE_V2_QWEN_ALIGNER_REVISION": "2" * 40,
            "PODCAST_SUBTITLE_V2_ENABLE_FASTER_WHISPER_CORROBORATION": "true",
            "PODCAST_SUBTITLE_V2_FASTER_WHISPER_MODEL_REVISION": "3" * 40,
        }
    )
    monkeypatch.setattr("agents.brook.podcast_subtitles.production.os.environ", environment)
    module = build_production(FactoryContextV1(1, tmp_path / "episode", None))
    assert isinstance(module._recognizers[0], MemoRecognizerAdapter)
    assert len(module._recognizers) == 3
    assert module._recognition_independence_policy is not None


def test_unknown_or_legacy_paid_setting_fails_closed(tmp_path: Path) -> None:
    environment = _valid_environment(tmp_path)
    environment["PODCAST_SUBTITLE_V2_ALLOW_PAID_GEMINI"] = "true"
    with pytest.raises(ProductionConfigurationError, match="unknown.*ALLOW_PAID_GEMINI"):
        load_production_config(environment)


def test_factory_does_not_import_gpu_runtimes_in_memo_only_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = _valid_environment(tmp_path)
    monkeypatch.setattr("agents.brook.podcast_subtitles.production.os.environ", environment)
    names = ("torch", "qwen_asr", "faster_whisper", "ctranslate2")
    before = {name for name in sys.modules if name == names[0] or name.startswith(names[1:])}
    build_production(FactoryContextV1(1, tmp_path / "episode", None))
    after = {name for name in sys.modules if name == names[0] or name.startswith(names[1:])}
    assert after == before
