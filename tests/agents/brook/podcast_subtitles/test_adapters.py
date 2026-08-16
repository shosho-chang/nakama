from __future__ import annotations

import hashlib
import importlib
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.adapters import (
    FixtureArbiterAdapter,
    FixtureAudioAuditorAdapter,
    FixtureCorrectorAdapter,
    FixtureNormalizerAdapter,
    FixtureRecognizerAdapter,
    FixtureReferenceRetrieverAdapter,
    FixtureSemanticAnalyzerAdapter,
    WhisperXRecognizerAdapter,
    WordsJsonRecognizerAdapter,
)
from agents.brook.podcast_subtitles.ports import (
    AdapterInputError,
    AdapterIntegrityError,
    AdapterUnavailableError,
    Arbiter,
    ArbitrationRequest,
    ArbitrationVerdict,
    AudioAuditor,
    AudioAuditRequest,
    CorrectionProposal,
    CorrectionRequest,
    Corrector,
    Normalizer,
    NormalizeRequest,
    RecognitionRequest,
    Recognizer,
    ReferenceArtifact,
    ReferenceRetrievalRequest,
    ReferenceRetrievalResult,
    ReferenceRetriever,
    ReferenceSnippet,
    SemanticAnalysisRequest,
    SemanticAnalyzer,
)
try:
    from agents.brook.podcast_subtitles.adapters.auphonic import AuphonicNormalizerAdapter
    from shared.auphonic import (
        AuphonicAlignmentResult,
        AuphonicNormalizationResult,
        AuphonicSubmittedParameters,
    )
except ImportError:
    # Subtitle V2 no longer owns or changes the mature upstream Auphonic seam.
    # These retained-adapter regression tests only run in a checkout that also
    # carries the separate shared.auphonic implementation revision.
    AuphonicNormalizerAdapter = None  # type: ignore[assignment,misc]
    AuphonicAlignmentResult = None  # type: ignore[assignment,misc]
    AuphonicNormalizationResult = None  # type: ignore[assignment,misc]
    AuphonicSubmittedParameters = None  # type: ignore[assignment,misc]


requires_legacy_auphonic_adapter = pytest.mark.skipif(
    AuphonicNormalizerAdapter is None,
    reason="legacy Auphonic adapter is outside the Memo-first Subtitle V2 boundary",
)
from shared.schemas.podcast_subtitles_v2 import (
    EMPTY_REFERENCE_EVIDENCE_HASH,
    ArtifactDigest,
    AudioClockMap,
    CanonicalSpan,
    CanonicalToken,
    CanonicalTranscript,
    NormalizationParameter,
    NormalizationReceipt,
    ReferenceLocator,
    ReferenceLocatorPart,
    ReferenceQueryContext,
    ReferenceQueryContextSlice,
    ReferenceRetrievalHit,
    ReferenceRetrievalPolicySnapshot,
    ReviewIssue,
    SemanticUnit,
    canonical_content_hash,
    normalization_settings_hash,
    reference_retrieval_policy_hash,
)
from tests.agents.brook.podcast_subtitles.reference_authority_fixtures import (
    reference_authority_fixture,
)

H0 = "0" * 64
H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _audio(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _request(audio: Path, *, invocation_id: str = "recognition-run-1") -> RecognitionRequest:
    return RecognitionRequest(
        episode_id="episode-1",
        invocation_id=invocation_id,
        normalized_audio=audio,
        raw_output_dir=audio.parent / "raw-evidence",
        language_hint="zh",
    )


def _write_words(path: Path, words: list[dict[str, object]], **metadata: object) -> Path:
    payload = {"audio": "legacy.wav", "model": "large-v3", "words": words, **metadata}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _words_manifest_payload(
    audio: Path,
    words_path: Path,
    *,
    duration_ms: int = 10_000,
    **overrides: object,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "normalized_audio_sha256": _sha(audio.read_bytes()),
        "normalized_audio_size_bytes": audio.stat().st_size,
        "normalized_audio_duration_ms": duration_ms,
        "asr_adapter": "whisperx",
        "asr_model": "large-v3",
        "asr_config_hash": H1,
        "words_artifact_sha256": _sha(words_path.read_bytes()),
        "timestamp_unit": "seconds",
        **overrides,
    }


def _write_words_manifest(
    audio: Path,
    words_path: Path,
    *,
    duration_ms: int = 10_000,
    **overrides: object,
) -> Path:
    path = words_path.with_suffix(words_path.suffix + ".import-manifest.json")
    path.write_text(
        json.dumps(
            _words_manifest_payload(
                audio,
                words_path,
                duration_ms=duration_ms,
                **overrides,
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _words_adapter(
    words_path: Path,
    audio: Path,
    *,
    duration_ms: int = 10_000,
    **kwargs: object,
) -> WordsJsonRecognizerAdapter:
    return WordsJsonRecognizerAdapter(
        words_path,
        _write_words_manifest(audio, words_path, duration_ms=duration_ms),
        **kwargs,
    )


def _whisper_adapter(
    runner,
    *,
    runtime: str = "whisperx-fixture-runtime-v1",
    runner_code_hash: str = "a" * 64,
    **kwargs: object,
) -> WhisperXRecognizerAdapter:
    return WhisperXRecognizerAdapter(
        runner=runner,
        model_version="model-snapshot-20260812",
        aligner="fixture-whisperx-aligner",
        aligner_version="aligner-snapshot-20260812",
        runner_runtime_components={"fixture_runner": runtime},
        runner_code_hash=runner_code_hash,
        runner_execution_mode="fixture",
        **kwargs,
    )


def _with_raw_digest(evidence, raw_output: bytes):
    digest = _sha(raw_output)
    return evidence.model_copy(
        update={
            "raw_output": evidence.raw_output.model_copy(
                update={"sha256": digest, "size_bytes": len(raw_output)}
            ),
            "raw_output_hash": digest,
        }
    )


def _receipt(source: Path, normalized: Path, *, accepted: bool = False) -> NormalizationReceipt:
    parameters = (NormalizationParameter(scope="adapter", name="fixture", value=True),)
    return NormalizationReceipt(
        status="accepted" if accepted else "draft",
        provider="fixture",
        production_id="fixture-production" if accepted else None,
        production_source="created" if accepted else "legacy_unknown",
        source_identity_verified=accepted,
        source_binding_method=("upload_in_current_request" if accepted else "legacy_unknown"),
        provider_outcome="completed" if accepted else "unknown",
        source=ArtifactDigest(
            uri=source.resolve().as_uri(),
            sha256=_sha(source.read_bytes()),
            size_bytes=source.stat().st_size,
        ),
        normalized=ArtifactDigest(
            uri=normalized.resolve().as_uri(),
            sha256=_sha(normalized.read_bytes()),
            size_bytes=normalized.stat().st_size,
        ),
        source_duration_ms=1000 if accepted else None,
        normalized_duration_ms=1000 if accepted else None,
        request_started_at=(datetime(2026, 8, 12, tzinfo=timezone.utc) if accepted else None),
        completed_at=(datetime(2026, 8, 12, 0, 1, tzinfo=timezone.utc) if accepted else None),
        requested_parameters=parameters,
        submitted_parameters=parameters if accepted else None,
        settings_hash=(normalization_settings_hash(parameters, preset=None) if accepted else H0),
        clock_map=AudioClockMap(
            source_origin_ms=0,
            normalized_origin_ms=0,
            verified=accepted,
            drift_ms=0.0,
        ),
        alignment_method="identity" if accepted else "legacy_unverified",
    )


def _transcript() -> CanonicalTranscript:
    tokens = (
        CanonicalToken(
            id="ct-1",
            text="學業",
            start_ms=100,
            end_ms=500,
            speaker="guest",
            evidence_ids=("ev-1",),
        ),
        CanonicalToken(
            id="ct-2",
            text="經歷",
            start_ms=500,
            end_ms=900,
            speaker="guest",
            evidence_ids=("ev-2",),
        ),
    )
    return CanonicalTranscript(
        episode_id="episode-1",
        generation_id="gen-1",
        revision=1,
        status="draft",
        source_audio_hash=H0,
        normalized_audio_hash=H0,
        normalization_receipt_hash=H0,
        evidence_hash=H1,
        reference_evidence_hash=EMPTY_REFERENCE_EVIDENCE_HASH,
        ledger_hash=H2,
        policy_hash=H3,
        acceptance_policy={"permit_unresolved_low_risk": True},
        tokens=tokens,
        spans=(
            CanonicalSpan(
                id="span-1", token_ids=("ct-1", "ct-2"), start_ms=100, end_ms=900
            ),
        ),
        review_issues=(
            ReviewIssue(
                id="issue-1",
                risk="text",
                severity="high",
                code="fixture_target",
                span_ids=("span-1",),
                audio_evidence_ids=("ev-1",),
            ),
        ),
        content_hash=canonical_content_hash(tokens),
    )


@requires_legacy_auphonic_adapter
def test_auphonic_adapter_wraps_retained_implementation_in_draft_receipt(tmp_path: Path) -> None:
    source = _audio(tmp_path, "source.wav", b"source-audio")
    calls: list[tuple[Path, dict[str, object]]] = []

    def fake_normalize(path: Path, **kwargs: object) -> Path:
        calls.append((path, kwargs))
        output = Path(kwargs["output_dir"]) / "normalized.wav"
        output.write_bytes(b"normalized-audio")
        return output

    adapter = AuphonicNormalizerAdapter(
        settings={"loudness_target": -16, "denoise": True},
        normalize_fn=fake_normalize,
    )
    result = adapter.normalize(NormalizeRequest(source_audio=source, output_dir=tmp_path))

    assert calls == [
        (
            source,
            {"loudness_target": -16, "denoise": True, "output_dir": tmp_path},
        )
    ]
    assert result.receipt.source.sha256 == _sha(b"source-audio")
    assert result.receipt.source.size_bytes == len(b"source-audio")
    assert result.receipt.normalized.sha256 == _sha(b"normalized-audio")
    assert result.receipt.normalized.size_bytes == len(b"normalized-audio")
    assert result.receipt.provider == "auphonic"
    assert result.receipt.production_id is None
    assert result.receipt.status == "draft"
    assert result.receipt.clock_map.verified is False


@requires_legacy_auphonic_adapter
def test_auphonic_adapter_records_verified_clock_but_remains_draft_without_provider_receipt(
    tmp_path: Path,
) -> None:
    source = _audio(tmp_path, "source.wav", b"source")
    normalized = _audio(tmp_path, "normalized.wav", b"normalized")
    adapter = AuphonicNormalizerAdapter(
        normalize_fn=lambda _path, **_kwargs: normalized,
        clock_map_verifier=lambda _source, _normalized: AudioClockMap(
            source_origin_ms=0,
            normalized_origin_ms=0,
            verified=True,
            drift_ms=0.4,
        ),
    )
    result = adapter.normalize(NormalizeRequest(source_audio=source))
    assert result.receipt.status == "draft"
    assert result.receipt.production_id is None
    assert result.receipt.clock_map.verified is True
    assert result.receipt.clock_map.drift_ms == 0.4


@requires_legacy_auphonic_adapter
def test_auphonic_adapter_checks_source_hash_before_calling_provider(tmp_path: Path) -> None:
    source = _audio(tmp_path, "source.wav", b"tampered")
    called = False

    def fake_normalize(_path: Path, **_kwargs: object) -> Path:
        nonlocal called
        called = True
        return source

    adapter = AuphonicNormalizerAdapter(normalize_fn=fake_normalize)
    with pytest.raises(AdapterIntegrityError, match="source audio hash"):
        adapter.normalize(NormalizeRequest(source_audio=source, expected_source_hash=H0))
    assert called is False


@pytest.mark.parametrize(
    ("production_source", "source_binding_method"),
    [
        ("created", "upload_in_current_request"),
        ("reused", "provider_checksum"),
    ],
)
@requires_legacy_auphonic_adapter
def test_auphonic_detailed_adapter_accepts_only_cryptographically_bound_sources(
    tmp_path: Path, production_source: str, source_binding_method: str
) -> None:
    source = _audio(tmp_path, "source.wav", b"source")
    normalized = _audio(tmp_path, "normalized.wav", b"normalized")
    parameters = AuphonicSubmittedParameters(
        algorithms=(("denoise", True), ("loudnesstarget", -16.0)),
        output_file=(("format", "wav"),),
    )
    alignment = AuphonicAlignmentResult(
        output_path=normalized,
        method="cross_correlation",
        verified=True,
        requested_jingle_seconds=6.0,
        head_offset_seconds=6.409,
        head_correlation=0.97,
        mid_correlation=0.96,
        drift_seconds=0.012,
    )
    detailed = AuphonicNormalizationResult(
        output_path=normalized,
        production_uuid="prod-1",
        production_source=production_source,
        provider_outcome="completed",
        provider_status_code=3,
        provider_status="Done",
        request_started_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 12, 0, 2, tzinfo=timezone.utc),
        provider_created_at="2026-08-12T00:00:00Z",
        provider_completed_at="2026-08-12T00:01:00Z",
        requested_parameters=parameters,
        submitted_parameters=parameters,
        preset="podcast-v2",
        source_duration_seconds=10.0,
        normalized_duration_seconds=10.0,
        alignment=alignment,
        source_identity_verified=True,
        source_binding_method=source_binding_method,
        reuse_reason=(
            "matched immutable provider record" if production_source == "reused" else None
        ),
        original_production_uuid=("prod-1" if production_source == "reused" else None),
    )
    calls: list[tuple[Path, dict[str, object]]] = []

    def fake_detailed(path: Path, **kwargs: object) -> AuphonicNormalizationResult:
        calls.append((path, kwargs))
        return detailed

    result = AuphonicNormalizerAdapter(
        settings={"denoise": True},
        normalize_detailed_fn=fake_detailed,
    ).normalize(NormalizeRequest(source_audio=source, output_dir=tmp_path))

    assert calls == [(source, {"denoise": True, "output_dir": tmp_path})]
    assert result.receipt.status == "accepted"
    assert result.receipt.production_source == production_source
    assert result.receipt.production_id == "prod-1"
    assert result.receipt.submitted_parameters is not None
    assert result.receipt.alignment_method == "cross_correlation"
    assert result.receipt.clock_map.drift_ms == pytest.approx(12.0)
    assert result.receipt.provider_created_at == datetime(2026, 8, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("status", [None, 0, 1, 2, 4, "3", True, False])
@requires_legacy_auphonic_adapter
def test_auphonic_detailed_adapter_never_accepts_unproven_provider_status(
    tmp_path: Path, status: object
) -> None:
    source = _audio(tmp_path, "source.wav", b"source")
    normalized = _audio(tmp_path, "normalized.wav", b"normalized")
    parameters = AuphonicSubmittedParameters(
        algorithms=(("denoise", True),), output_file=(("format", "wav"),)
    )
    detailed = AuphonicNormalizationResult(
        output_path=normalized,
        production_uuid="prod-1",
        production_source="created",
        provider_outcome="completed",
        provider_status_code=3,
        provider_status="Done",
        request_started_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 12, 0, 1, tzinfo=timezone.utc),
        provider_created_at=None,
        provider_completed_at=None,
        requested_parameters=parameters,
        submitted_parameters=parameters,
        preset=None,
        source_duration_seconds=10.0,
        normalized_duration_seconds=10.0,
        alignment=AuphonicAlignmentResult(
            output_path=normalized,
            method="identity",
            verified=True,
            requested_jingle_seconds=0.0,
        ),
        source_identity_verified=True,
        source_binding_method="upload_in_current_request",
    )
    unproven = replace(detailed, provider_status_code=status)
    result = AuphonicNormalizerAdapter(
        normalize_detailed_fn=lambda _source, **_kwargs: unproven
    ).normalize(NormalizeRequest(source_audio=source))
    assert result.receipt.status == "draft"
    assert result.receipt.provider_outcome == "unknown"


@requires_legacy_auphonic_adapter
def test_auphonic_reuse_without_original_submitted_parameters_stays_draft(
    tmp_path: Path,
) -> None:
    source = _audio(tmp_path, "source.wav", b"source")
    normalized = _audio(tmp_path, "normalized.wav", b"normalized")
    requested = AuphonicSubmittedParameters(
        algorithms=(("denoise", True),),
        output_file=(("format", "wav"),),
    )
    detailed = AuphonicNormalizationResult(
        output_path=normalized,
        production_uuid="old-prod",
        production_source="reused",
        provider_outcome="completed",
        provider_status_code=3,
        provider_status="Done",
        request_started_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 12, 0, 1, tzinfo=timezone.utc),
        provider_created_at=None,
        provider_completed_at=None,
        requested_parameters=requested,
        submitted_parameters=None,
        preset=None,
        source_duration_seconds=10.0,
        normalized_duration_seconds=10.0,
        alignment=AuphonicAlignmentResult(
            output_path=normalized,
            method="cross_correlation",
            verified=True,
            requested_jingle_seconds=6.0,
            head_correlation=0.9,
            mid_correlation=0.9,
            drift_seconds=0.01,
        ),
        source_identity_verified=False,
        source_binding_method="heuristic_filename_duration",
        reuse_reason="insufficient_credits_matched_filename_and_duration",
        original_production_uuid="old-prod",
    )
    result = AuphonicNormalizerAdapter(
        normalize_detailed_fn=lambda _source, **_kwargs: detailed
    ).normalize(NormalizeRequest(source_audio=source))
    assert result.receipt.status == "draft"
    assert result.receipt.submitted_parameters is None
    assert result.receipt.requested_parameters

    # Even complete provider settings and a claimed verification bit cannot
    # turn the filename+duration heuristic into byte-level source identity.
    heuristic_with_settings = replace(
        detailed,
        submitted_parameters=requested,
        source_identity_verified=True,
    )
    result = AuphonicNormalizerAdapter(
        normalize_detailed_fn=lambda _source, **_kwargs: heuristic_with_settings
    ).normalize(NormalizeRequest(source_audio=source))
    assert result.receipt.status == "draft"
    assert result.receipt.source_binding_method == "heuristic_filename_duration"


def test_words_json_import_preserves_fields_and_has_deterministic_token_ids(
    tmp_path: Path,
) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    words_path = _write_words(
        tmp_path / "words.json",
        [
            {
                "word": " 心理",
                "start": 1.001,
                "end": 1.5,
                "score": 0.93,
                "speaker": "guest",
            },
            {
                "word": "健康",
                "start": 1.5,
                "end": 2.001,
                "confidence": 0.81,
                "speaker": "guest",
            },
        ],
    )
    adapter = _words_adapter(words_path, audio, expected_model="large-v3")

    first = adapter.recognize(_request(audio))
    second = adapter.recognize(_request(audio))

    assert first == second
    assert [token.text for token in first.tokens] == [" 心理", "健康"]
    assert [(token.start_ms, token.end_ms) for token in first.tokens] == [
        (1001, 1500),
        (1500, 2001),
    ]
    assert [token.confidence for token in first.tokens] == [0.93, 0.81]
    assert [token.speaker for token in first.tokens] == ["guest", "guest"]
    assert all(token.id.startswith("ev_") for token in first.tokens)
    assert first.raw_output_hash == _sha(words_path.read_bytes())
    assert first.raw_output.sha256 == first.raw_output_hash
    assert first.raw_output.uri == words_path.resolve().as_uri()
    assert first.raw_output.size_bytes == words_path.stat().st_size
    assert first.language == "zh"
    assert first.normalized_audio_hash == _sha(audio.read_bytes())


def test_words_json_satisfies_recognizer_identity_and_fresh_raw_replay(
    tmp_path: Path,
) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    words_path = _write_words(
        tmp_path / "words.json",
        [{"word": "identity", "start": 0.1, "end": 0.5}],
    )
    manifest_path = _write_words_manifest(audio, words_path)
    request = _request(audio)
    adapter = WordsJsonRecognizerAdapter(words_path, manifest_path)

    assert isinstance(adapter, Recognizer)
    identity = adapter.identity
    evidence = adapter.recognize(request)
    raw_output = words_path.read_bytes()

    assert identity.adapter_name == evidence.adapter == "words-json-v1-import"
    assert identity.model == evidence.model == "large-v3"
    assert identity.model_version == "unverified-import-manifest-v1"
    assert identity.aligner_version == "unverified-import-manifest-v1"
    assert identity.config_hash == evidence.config_hash
    assert adapter.verify(evidence, request=request, raw_output=raw_output) == evidence

    fresh = WordsJsonRecognizerAdapter(words_path, manifest_path)
    assert fresh.identity == identity
    assert fresh.verify(evidence, request=request, raw_output=raw_output) == evidence


def test_words_json_replay_rejects_raw_tamper_cross_audio_and_runtime_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    foreign_audio = _audio(tmp_path, "foreign.wav", b"foreign")
    words_path = _write_words(
        tmp_path / "words.json",
        [{"word": "bound", "start": 0.1, "end": 0.5}],
    )
    adapter = _words_adapter(words_path, audio)
    request = _request(audio)
    evidence = adapter.recognize(request)
    raw_output = words_path.read_bytes()

    with pytest.raises(AdapterIntegrityError, match="raw output digest"):
        adapter.verify(evidence, request=request, raw_output=raw_output + b" ")
    with pytest.raises(AdapterIntegrityError, match="does not match words import manifest"):
        adapter.verify(
            evidence,
            request=_request(foreign_audio),
            raw_output=raw_output,
        )

    monkeypatch.setattr(
        "agents.brook.podcast_subtitles.adapters.recognition.platform.python_version",
        lambda: "runtime-drift",
    )
    with pytest.raises(AdapterIntegrityError, match="executable identity changed"):
        adapter.verify(evidence, request=request, raw_output=raw_output)


def test_words_json_replay_rejects_exact_manifest_byte_drift(tmp_path: Path) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    words_path = _write_words(
        tmp_path / "words.json",
        [{"word": "manifest", "start": 0.1, "end": 0.5}],
    )
    manifest_path = _write_words_manifest(audio, words_path)
    adapter = WordsJsonRecognizerAdapter(words_path, manifest_path)
    request = _request(audio)
    evidence = adapter.recognize(request)
    raw_output = words_path.read_bytes()

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with pytest.raises(AdapterIntegrityError, match="import manifest.*changed"):
        adapter.verify(evidence, request=request, raw_output=raw_output)
    fresh = WordsJsonRecognizerAdapter(words_path, manifest_path)
    with pytest.raises(AdapterIntegrityError, match="does not re-derive"):
        fresh.verify(evidence, request=request, raw_output=raw_output)


def test_words_json_rejects_malformed_raw_even_when_manifest_binds_it(
    tmp_path: Path,
) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    words_path = tmp_path / "malformed.json"
    words_path.write_bytes(b'{"words":[')
    manifest_path = _write_words_manifest(audio, words_path)

    with pytest.raises(AdapterInputError, match="malformed recognition artifact JSON"):
        WordsJsonRecognizerAdapter(words_path, manifest_path).recognize(_request(audio))


def test_words_json_tamper_is_rejected_against_bound_manifest(tmp_path: Path) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    path = _write_words(
        tmp_path / "words.json",
        [{"word": "學業", "start": 1.0, "end": 1.5}],
    )
    manifest = _write_words_manifest(audio, path)
    adapter = WordsJsonRecognizerAdapter(path, manifest)
    before = adapter.recognize(_request(audio))

    _write_words(path, [{"word": "學歷", "start": 1.0, "end": 1.5}])
    with pytest.raises(AdapterIntegrityError, match="words artifact does not match"):
        adapter.recognize(_request(audio))
    assert before.tokens[0].text == "學業"


def test_words_json_manifest_rejects_same_timings_for_different_audio(tmp_path: Path) -> None:
    bound_audio = _audio(tmp_path, "bound.wav", b"audio-one")
    foreign_audio = _audio(tmp_path, "foreign.wav", b"audio-two")
    path = _write_words(
        tmp_path / "words.json",
        [{"word": "學業", "start": 0.1, "end": 0.5}],
    )
    manifest = _write_words_manifest(bound_audio, path)

    with pytest.raises(AdapterIntegrityError, match="does not match words import manifest"):
        WordsJsonRecognizerAdapter(path, manifest).recognize(_request(foreign_audio))


def test_words_json_requires_strict_complete_import_manifest(tmp_path: Path) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    path = _write_words(
        tmp_path / "words.json",
        [{"word": "學業", "start": 0.1, "end": 0.5}],
    )
    missing_path = tmp_path / "missing.import-manifest.json"
    with pytest.raises(AdapterInputError, match="words import manifest is not a file"):
        WordsJsonRecognizerAdapter(path, missing_path).recognize(_request(audio))

    missing_field = _words_manifest_payload(audio, path)
    del missing_field["asr_config_hash"]
    missing_field_path = tmp_path / "missing-field.import-manifest.json"
    missing_field_path.write_text(json.dumps(missing_field), encoding="utf-8")
    with pytest.raises(AdapterInputError, match="missing required fields"):
        WordsJsonRecognizerAdapter(path, missing_field_path).recognize(_request(audio))

    unknown_field = _words_manifest_payload(audio, path, unexpected=True)
    unknown_field_path = tmp_path / "unknown-field.import-manifest.json"
    unknown_field_path.write_text(json.dumps(unknown_field), encoding="utf-8")
    with pytest.raises(AdapterInputError, match="unsupported fields"):
        WordsJsonRecognizerAdapter(path, unknown_field_path).recognize(_request(audio))

    valid_json = json.dumps(_words_manifest_payload(audio, path))
    duplicate_key_json = valid_json.replace(
        '"schema_version": 1',
        '"schema_version": 1, "schema_version": 1',
        1,
    )
    duplicate_key_path = tmp_path / "duplicate-key.import-manifest.json"
    duplicate_key_path.write_text(duplicate_key_json, encoding="utf-8")
    with pytest.raises(AdapterInputError, match="duplicate JSON key"):
        WordsJsonRecognizerAdapter(path, duplicate_key_path).recognize(_request(audio))


def test_words_json_manifest_binds_duration_and_asr_config_identity(tmp_path: Path) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    path = _write_words(
        tmp_path / "words.json",
        [{"word": "學業", "start": 0.1, "end": 0.5}],
    )
    short_manifest = _write_words_manifest(audio, path, duration_ms=499)
    with pytest.raises(AdapterIntegrityError, match="beyond manifest normalized audio duration"):
        WordsJsonRecognizerAdapter(path, short_manifest).recognize(_request(audio))

    first_manifest = _write_words_manifest(audio, path, asr_config_hash=H1)
    first = WordsJsonRecognizerAdapter(path, first_manifest).recognize(_request(audio))
    second_manifest = _write_words_manifest(audio, path, asr_config_hash=H2)
    second = WordsJsonRecognizerAdapter(path, second_manifest).recognize(_request(audio))
    assert first.config_hash != second.config_hash
    assert first.tokens == second.tokens


@pytest.mark.parametrize(
    "payload,match",
    [
        ({"unknown": []}, "unsupported fields"),
        ({"words": []}, "at least one word"),
        ({"words": [{"word": "字", "start": 0.0}]}, "requires both start and end"),
        (
            {"words": [{"word": "字", "start": 0.0, "end": 0.5, "mystery": 1}]},
            "unsupported fields",
        ),
        ({"words": [{"word": "字", "start": 0.0, "end": 0.0001}]}, "rounded range"),
    ],
)
def test_words_json_unknown_or_malformed_input_fails_loud(
    tmp_path: Path, payload: object, match: str
) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    path = tmp_path / "words.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(AdapterInputError, match=match):
        _words_adapter(path, audio).recognize(_request(audio))


def test_words_json_rejects_overlapping_tokens_instead_of_reordering(tmp_path: Path) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    path = _write_words(
        tmp_path / "words.json",
        [
            {"word": "前", "start": 1.0, "end": 2.0},
            {"word": "後", "start": 1.9, "end": 2.5},
        ],
    )
    with pytest.raises(AdapterInputError, match="monotonic and non-overlapping"):
        _words_adapter(path, audio).recognize(_request(audio))


def test_words_json_accepts_whisperx_segment_envelope(tmp_path: Path) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    path = tmp_path / "aligned.json"
    path.write_text(
        json.dumps(
            {
                "language": "zh",
                "segments": [
                    {
                        "start": 0.1,
                        "end": 1.0,
                        "text": "學業經歷",
                        "words": [
                            {"word": "學業", "start": 0.1, "end": 0.5},
                            {"word": "經歷", "start": 0.5, "end": 1.0},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    evidence = _words_adapter(path, audio).recognize(_request(audio))
    assert [token.text for token in evidence.tokens] == ["學業", "經歷"]


def test_words_json_checks_normalized_audio_hash(tmp_path: Path) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    path = _write_words(
        tmp_path / "words.json",
        [{"word": "字", "start": 0.0, "end": 0.5}],
    )
    request = RecognitionRequest(
        episode_id="episode-1",
        invocation_id="recognition-run-1",
        normalized_audio=audio,
        raw_output_dir=tmp_path / "raw-evidence",
        language_hint="zh",
        expected_normalized_audio_hash=H0,
    )
    with pytest.raises(AdapterIntegrityError, match="normalized audio hash"):
        _words_adapter(path, audio).recognize(request)


def test_whisperx_adapter_is_lazy_and_runner_output_uses_same_contract(tmp_path: Path) -> None:
    sys.modules.pop("whisperx", None)
    importlib.import_module("agents.brook.podcast_subtitles.adapters")
    assert "whisperx" not in sys.modules

    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    calls: list[Path] = []

    def runner(path: Path, _request: RecognitionRequest) -> dict[str, object]:
        calls.append(path)
        return {
            "language": "zh",
            "segments": [
                {
                    "words": [
                        {"word": "人生", "start": 0.1, "end": 0.6, "score": 0.8},
                        {"word": "樣貌", "start": 0.6, "end": 1.2, "score": 0.9},
                    ]
                }
            ],
        }

    adapter = _whisper_adapter(runner)
    evidence = adapter.recognize(_request(audio))
    assert calls == [audio]
    assert isinstance(adapter, Recognizer)
    assert evidence.adapter == "whisperx"
    assert evidence.model == "large-v3@model-snapshot-20260812"
    assert adapter.identity.model_version == "model-snapshot-20260812"
    assert adapter.identity.aligner == "fixture-whisperx-aligner"
    assert adapter.identity.aligner_version == "aligner-snapshot-20260812"
    assert evidence.config_hash == adapter.identity.config_hash
    assert [token.text for token in evidence.tokens] == ["人生", "樣貌"]
    assert evidence.language == "zh"
    assert evidence.raw_output.sha256 == evidence.raw_output_hash
    assert evidence.raw_output.size_bytes > 0
    assert evidence.raw_output.uri.endswith(f"whisperx-{evidence.raw_output_hash}.json")
    raw_path = next((tmp_path / "raw-evidence").glob("whisperx-*.json"))
    envelope = json.loads(raw_path.read_text(encoding="utf-8"))
    assert envelope["identity_hash"] == adapter.identity.content_hash
    assert envelope["provider_output"]["language"] == "zh"
    assert adapter.verify(
        evidence,
        request=_request(audio),
        raw_output=raw_path.read_bytes(),
    ) == evidence
    assert calls == [audio], "raw replay must not call the provider runner"


def test_whisperx_requires_generation_owned_raw_output_directory(tmp_path: Path) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    request = RecognitionRequest(
        episode_id="episode-1",
        invocation_id="recognition-run-1",
        normalized_audio=audio,
        language_hint="zh",
    )
    adapter = _whisper_adapter(
        lambda _path, _request: {
            "language": "zh",
            "segments": [
                {
                    "words": [
                        {"word": "人生", "start": 0.1, "end": 0.6, "score": 0.8},
                    ]
                }
            ],
        },
    )
    with pytest.raises(AdapterInputError, match="raw_output_dir"):
        adapter.recognize(request)


def test_whisperx_custom_runner_requires_complete_executable_identity() -> None:
    with pytest.raises(ValueError, match="explicit model, aligner, runtime, code"):
        WhisperXRecognizerAdapter(
            runner=lambda _path, _request: {},
        )


def test_whisperx_builtin_production_identity_is_explicitly_unavailable() -> None:
    sys.modules.pop("whisperx", None)
    adapter = WhisperXRecognizerAdapter()

    assert isinstance(adapter, Recognizer)
    with pytest.raises(AdapterUnavailableError, match="immutable model and aligner snapshots"):
        _ = adapter.identity
    assert "whisperx" not in sys.modules


def test_whisperx_fresh_replay_and_fail_closed_tamper_guards(tmp_path: Path) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")
    foreign_audio = _audio(tmp_path, "foreign.wav", b"foreign")
    calls = 0

    def runner(_path: Path, _request: RecognitionRequest) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "language": "zh",
            "segments": [
                {
                    "words": [
                        {"word": "replay", "start": 0.1, "end": 0.6},
                    ]
                }
            ],
            "provider_diagnostics": {"beam": 1},
        }

    request = _request(audio)
    adapter = _whisper_adapter(runner)
    evidence = adapter.recognize(request)
    raw_output = next((tmp_path / "raw-evidence").glob("whisperx-*.json")).read_bytes()
    fresh = _whisper_adapter(runner)

    assert fresh.identity == adapter.identity
    assert fresh.verify(evidence, request=request, raw_output=raw_output) == evidence
    assert calls == 1
    with pytest.raises(AdapterIntegrityError, match="raw envelope digest"):
        fresh.verify(evidence, request=request, raw_output=raw_output + b" ")
    with pytest.raises(AdapterIntegrityError, match="normalized audio mismatch"):
        fresh.verify(
            evidence,
            request=_request(foreign_audio),
            raw_output=raw_output,
        )

    drifted = _whisper_adapter(runner, runtime="whisperx-fixture-runtime-v2")
    with pytest.raises(AdapterIntegrityError, match="executable identity mismatch"):
        drifted.verify(evidence, request=request, raw_output=raw_output)
    assert calls == 1


def test_whisperx_replay_rejects_malformed_stored_raw_bytes(tmp_path: Path) -> None:
    audio = _audio(tmp_path, "normalized.wav", b"normalized")

    def runner(_path: Path, _request: RecognitionRequest) -> dict[str, object]:
        return {
            "language": "zh",
            "segments": [
                {"words": [{"word": "raw", "start": 0.1, "end": 0.6}]},
            ],
        }

    adapter = _whisper_adapter(runner)
    request = _request(audio)
    evidence = adapter.recognize(request)
    malformed = b'{"schema_version":1'

    with pytest.raises(AdapterInputError, match="malformed stored WhisperX"):
        adapter.verify(
            _with_raw_digest(evidence, malformed),
            request=request,
            raw_output=malformed,
        )

    raw_path = next((tmp_path / "raw-evidence").glob("whisperx-*.json"))
    schema_drift = json.loads(raw_path.read_text(encoding="utf-8"))
    schema_drift["schema_version"] = "1"
    schema_drift_bytes = json.dumps(
        schema_drift,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    with pytest.raises(AdapterInputError, match="invalid WhisperX Evidence envelope"):
        adapter.verify(
            _with_raw_digest(evidence, schema_drift_bytes),
            request=request,
            raw_output=schema_drift_bytes,
        )


def test_fixture_adapters_satisfy_ports_and_fail_on_cross_generation(tmp_path: Path) -> None:
    source = _audio(tmp_path, "source.wav", b"source")
    normalized = _audio(tmp_path, "normalized.wav", b"normalized")
    receipt = _receipt(source, normalized, accepted=True)
    normalizer = FixtureNormalizerAdapter(normalized, receipt)
    assert isinstance(normalizer, Normalizer)
    assert normalizer.normalize(NormalizeRequest(source_audio=source)).receipt == receipt

    words = _write_words(
        tmp_path / "words.json",
        [{"word": "學業", "start": 0.1, "end": 0.5}],
    )
    evidence = _words_adapter(words, normalized).recognize(_request(normalized))
    recognizer = FixtureRecognizerAdapter(evidence)
    assert isinstance(recognizer, Recognizer)
    assert recognizer.recognize(_request(normalized)) == evidence
    with pytest.raises(AdapterIntegrityError, match="another invocation"):
        recognizer.recognize(_request(normalized, invocation_id="recognition-run-2"))

    audio_transcript = _transcript().model_copy(
        update={"normalized_audio_hash": _sha(normalized.read_bytes())}
    )
    audio_request = AudioAuditRequest(
        transcript=audio_transcript,
        normalized_audio=normalized,
        expected_normalized_audio_hash=_sha(normalized.read_bytes()),
        normalized_audio_duration_ms=1_000,
        target_span_ids=("span-1",),
        evidence=(evidence,),
    )
    audio_auditor = FixtureAudioAuditorAdapter()
    assert isinstance(audio_auditor, AudioAuditor)
    audio_run = audio_auditor.audit(audio_request)
    assert (
        FixtureAudioAuditorAdapter().replay(
            audio_request,
            proposals=audio_run.proposals,
            receipt=audio_run.receipt,
            request_bytes=audio_run.request_bytes,
            response_bytes=audio_run.response_bytes,
            clip_bytes=audio_run.clip_bytes,
        )
        == audio_run
    )
    assert audio_auditor.requests == [audio_request]

    passage = "安吉在研究報告中使用心理健康一詞"
    reference = ReferenceSnippet(
        id="snippet-1",
        artifact=ReferenceArtifact(
            source_id="report-1",
            kind="research_report",
            source_format="text",
            digest=ArtifactDigest(uri="kb://source/report-1", sha256=H1, size_bytes=100),
            extracted_text=ArtifactDigest(
                uri="reference-extraction://report-1",
                sha256=_sha(passage.encode("utf-8")),
                size_bytes=len(passage.encode("utf-8")),
            ),
            extractor_name="fixture",
            extractor_version="1",
            extractor_config_hash=H2,
            extractor_code_hash=H3,
            extractor_runtime_hash=H4,
            offset_unit="unicode_scalar_v1",
            extraction_block_count=1,
            title="研究報告",
            author="安吉",
            version="snapshot:1",
            trust_tier="curated",
            authority=reference_authority_fixture(
                source_id="report-1",
                kind="research_report",
                title="研究報告",
                author="安吉",
                version="snapshot:1",
                trust_tier="curated",
            ),
        ),
        locator=ReferenceLocator(parts=(ReferenceLocatorPart(kind="page", value="12"),)),
        extraction_block_index=0,
        extraction_block_hash=_sha(passage.encode("utf-8")),
        excerpt_start=0,
        excerpt_end=len(passage),
        excerpt=passage,
        excerpt_hash=_sha(passage.encode("utf-8")),
    )
    reference_policy = ReferenceRetrievalPolicySnapshot(
        left_unicode_scalar_budget=5,
        right_unicode_scalar_budget=5,
        max_adjacent_spans_per_side=5,
        max_anchor_unicode_scalars=256,
        max_query_unicode_scalars=266,
        stop_at_known_speaker_change=True,
        max_adjacent_gap_ms=2_000,
        max_candidate_terms=16,
        max_results=3,
        retrievable_codes=("suspicious_token",),
        vocabulary=(),
    )
    query_text = "心理開始健康"
    reference_context = ReferenceQueryContext(
        basis_content_hash=H0,
        anchor_span_id="span-1",
        anchor_query_start=0,
        anchor_query_end=len(query_text),
        slices=(
            ReferenceQueryContextSlice(
                span_id="span-1",
                token_ids=("ct-1",),
                span_text_hash=_sha(query_text.encode("utf-8")),
                slice_start=0,
                slice_end=len(query_text),
            ),
        ),
        exact_query=query_text,
        algorithm="canonical_adjacent_context",
        algorithm_version="unicode-scalar-v1",
        policy_hash=reference_retrieval_policy_hash(reference_policy),
    )
    retrieval_result = ReferenceRetrievalResult(
        query_id="query-1",
        episode_id="episode-1",
        invocation_id="reference-run-1",
        audio_span_id="span-1",
        query=query_text,
        context=reference_context,
        policy=reference_policy,
        candidate_terms=("心理健康",),
        allowed_source_ids=("report-1",),
        retriever="fixture",
        retriever_version="1",
        retriever_config_hash=H3,
        retriever_code_hash=H4,
        retriever_runtime_hash=H1,
        index_hash=H2,
        query_plan_hash=H4,
        candidate_passages_examined=1,
        max_results=3,
        hits=(
            ReferenceRetrievalHit(
                evidence_id=reference.id,
                rank=1,
                relevance=0.98,
                query_support_start=0,
                query_support_end=len(query_text),
                support_kind="candidate_term_exact",
                candidate_term_index=0,
            ),
        ),
        evidence=(reference,),
    )
    retriever = FixtureReferenceRetrieverAdapter({"span-1": retrieval_result})
    assert isinstance(retriever, ReferenceRetriever)
    assert (
        retriever.retrieve(
            ReferenceRetrievalRequest(
                episode_id="episode-1",
                invocation_id="reference-run-1",
                context=reference_context,
                policy=reference_policy,
                candidate_terms=("心理健康",),
                allowed_artifact_ids=("report-1",),
            )
        )
        == retrieval_result
    )

    proposal = CorrectionProposal(
        id="proposal-1",
        audio_span_ids=("span-1",),
        start_ms=100,
        end_ms=500,
        evidence_token_ids=(evidence.tokens[0].id,),
        observed_text="學業",
        candidate_text="學業",
        confidence=1.0,
        rationale="gold",
        source="fixture",
    )
    corrector = FixtureCorrectorAdapter((proposal,))
    assert isinstance(corrector, Corrector)
    transcript = _transcript()
    correction_request = CorrectionRequest(
        episode_id="episode-1",
        generation_id="gen-1",
        mode="targeted_review",
        transcript=transcript,
        target_span_ids=("span-1",),
        review_issues=transcript.review_issues,
        evidence=(evidence,),
        reference_evidence=(reference,),
    )
    assert corrector.propose(correction_request) == (proposal,)
    correction_run = corrector.propose_with_receipt(correction_request)
    assert (
        FixtureCorrectorAdapter((proposal,)).replay(
            correction_request,
            proposals=correction_run.proposals,
            execution_receipts=correction_run.execution_receipts,
            request_bytes=correction_run.request_bytes,
            response_bytes=correction_run.response_bytes,
        )
        == correction_run
    )
    with pytest.raises(AdapterIntegrityError, match="does not replay"):
        corrector.replay(
            correction_request,
            proposals=(replace(proposal, rationale="rewritten after storage"),),
            execution_receipts=correction_run.execution_receipts,
            request_bytes=correction_run.request_bytes,
            response_bytes=correction_run.response_bytes,
        )

    verdict = ArbitrationVerdict(
        proposal_id="proposal-1",
        status="accepted",
        selected_text="學業",
        confidence=1.0,
        rationale="gold",
    )
    arbiter = FixtureArbiterAdapter({"proposal-1": verdict})
    assert isinstance(arbiter, Arbiter)
    arbitration_transcript = transcript.model_copy(
        update={"normalized_audio_hash": _sha(normalized.read_bytes())}
    )
    arbitration_request = ArbitrationRequest(
        episode_id="episode-1",
        generation_id="gen-1",
        transcript=arbitration_transcript,
        normalized_audio=normalized,
        expected_normalized_audio_hash=_sha(normalized.read_bytes()),
        proposal=proposal,
        reference_evidence=(reference,),
    )
    arbitration_run = arbiter.decide_with_receipt(arbitration_request)
    assert arbitration_run.verdict == verdict
    assert (
        FixtureArbiterAdapter({"proposal-1": verdict}).replay(
            arbitration_request,
            verdict=arbitration_run.verdict,
            receipt=arbitration_run.receipt,
            request_bytes=arbitration_run.request_bytes,
            response_bytes=arbitration_run.response_bytes,
            clip_bytes=arbitration_run.clip_bytes,
        )
        == arbitration_run
    )

    semantic = FixtureSemanticAnalyzerAdapter(
        (
            SemanticUnit(
                id="unit-1",
                token_ids=("ct-1", "ct-2"),
                kind="term",
                strength=1.0,
                forbid_cue_breaks=True,
                forbid_line_breaks=True,
            ),
        ),
        expected_content_hash=transcript.content_hash,
    )
    assert isinstance(semantic, SemanticAnalyzer)
    assert semantic.partition(SemanticAnalysisRequest(transcript=transcript))[0].id == "unit-1"


def test_reference_snippet_rejects_unsourced_or_tampered_text() -> None:
    artifact = ReferenceArtifact(
        source_id="book-1",
        kind="book",
        source_format="text",
        digest=ArtifactDigest(uri="kb://source/book-1", sha256=H1, size_bytes=1),
        extracted_text=ArtifactDigest(
            uri="reference-extraction://book-1",
            sha256=H2,
            size_bytes=12,
        ),
        extractor_name="fixture",
        extractor_version="1",
        extractor_config_hash=H2,
        extractor_code_hash=H3,
        extractor_runtime_hash=H4,
        offset_unit="unicode_scalar_v1",
        extraction_block_count=1,
        title="Book",
        author="Author",
        publisher="Publisher",
        version="edition:1",
        trust_tier="authoritative",
        authority=reference_authority_fixture(
            source_id="book-1",
            kind="book",
            title="Book",
            author="Author",
            publisher="Publisher",
            version="edition:1",
            trust_tier="authoritative",
        ),
    )
    with pytest.raises(ValueError, match="excerpt hash mismatch"):
        ReferenceSnippet(
            id="snippet-1",
            artifact=artifact,
            locator=ReferenceLocator(parts=(ReferenceLocatorPart(kind="chapter", value="3"),)),
            extraction_block_index=0,
            extraction_block_hash=H2,
            excerpt_start=0,
            excerpt_end=4,
            excerpt="作者原文",
            excerpt_hash=H0,
        )


def test_reference_backed_proposal_requires_reference_evidence_ids() -> None:
    with pytest.raises(ValueError, match="reference-backed proposal"):
        CorrectionProposal(
            id="proposal-reference",
            audio_span_ids=("span-1",),
            start_ms=100,
            end_ms=500,
            evidence_token_ids=("ev-1",),
            observed_text="五物之物",
            candidate_text="《無路之路》",
            confidence=0.9,
            rationale="title appears in author's book",
            source="corrector",
            evidence_basis="audio_and_reference",
        )


def test_correction_request_supports_full_audit_without_fake_issue(
    tmp_path: Path,
) -> None:
    normalized = _audio(tmp_path, "normalized.wav", b"normalized")
    words = _write_words(
        tmp_path / "words.json",
        [{"word": "高信心錯詞", "start": 0.1, "end": 0.5, "score": 0.99}],
    )
    evidence = _words_adapter(words, normalized).recognize(_request(normalized))
    transcript = _transcript()
    request = CorrectionRequest(
        episode_id=transcript.episode_id,
        generation_id=transcript.generation_id,
        mode="full_audit",
        transcript=transcript,
        target_span_ids=("span-1",),
        evidence=(evidence,),
        review_issues=(),
    )
    assert request.mode == "full_audit"
    assert request.review_issues == ()

    with pytest.raises(ValueError, match="material unresolved"):
        CorrectionRequest(
            episode_id=transcript.episode_id,
            generation_id=transcript.generation_id,
            mode="targeted_review",
            transcript=transcript,
            target_span_ids=("span-1",),
            evidence=(evidence,),
            review_issues=(),
        )
