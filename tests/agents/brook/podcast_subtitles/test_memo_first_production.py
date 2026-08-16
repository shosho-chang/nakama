from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import wave

import pytest

from agents.brook.podcast_subtitles.adapters import MemoRecognizerAdapter
from agents.brook.podcast_subtitles.adapters.fixtures import (
    FixtureAudioAuditorAdapter,
    FixtureNormalizerAdapter,
)
from agents.brook.podcast_subtitles.adapters.speech_coverage import (
    FixtureSpeechCoverageAnalyzer,
)
from agents.brook.podcast_subtitles.adapters.normalized_handoff import (
    NormalizedAudioHandoffManifestV1,
    VerifiedNormalizedAudioHandoffAdapter,
)
from agents.brook.podcast_subtitles.canonical import (
    reconcile_canonical,
    review_target_fingerprint,
)
from agents.brook.podcast_subtitles.composition import FactoryContextV1
from agents.brook.podcast_subtitles.errors import GenerationIsolationError
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_file, sha256_bytes
from agents.brook.podcast_subtitles.memo_boundary import (
    MemoAcceptedCueV1,
    MemoBoundaryAuthorityV1,
    MemoBoundaryRepairProofV1,
    MemoBoundaryRepairV1,
    MemoCueBoundaryManifestV1,
    MemoSourceCueV1,
)
from agents.brook.podcast_subtitles.ports import (
    AdapterIntegrityError,
    NormalizeRequest,
    RecognitionRequest,
)
from agents.brook.podcast_subtitles.profiles import HORIZONTAL_16X9
from agents.brook.podcast_subtitles.production import build_production
from agents.brook.podcast_subtitles.semantic_projection import project_semantic_units
from shared.schemas.podcast_subtitles_v2 import CorrectionDecision, SemanticUnit
from agents.brook.podcast_subtitles.module import (
    AcceptedGeneration,
    AdapterIdentity,
    CreateRequest,
    PodcastSubtitleV2,
    ProjectRequest,
    ResolveRequest,
)
from tests.agents.brook.podcast_subtitles.test_module import (
    _AcceptEveryProposalArbiter,
    _DynamicSemanticAnalyzer,
    _SequentialDisjointCorrector,
    _accepted_receipt,
    _adapter_identity,
)


def _write(path: Path, value: object) -> Path:
    path.write_bytes(canonical_json_bytes(value))
    return path


def _memo_fixture(tmp_path: Path, *, final_text: str = "謝謝"):
    audio = tmp_path / "normalized.wav"
    audio.write_bytes(b"minimized-zheng-normalized-audio")
    source_export = tmp_path / "memo.stdout"
    source_export.write_bytes(b"accepted Memo attempt 02 source bytes")
    recognition_acceptance = tmp_path / "recognition-acceptance.json"
    recognition_acceptance.write_bytes(b'{"accepted":true,"attempt":2}')
    from agents.brook.podcast_subtitles.adapters.memo_recognition import (
        MemoRecognitionManifestV1,
        MemoRecognitionTokenV1,
    )

    raw_tokens = (
        MemoRecognitionTokenV1(id="z-001", text="今天", start_ms=100, end_ms=500, confidence=0.99, speaker="guest"),
        MemoRecognitionTokenV1(id="z-002", text="我們談", start_ms=500, end_ms=1_000, confidence=0.99, speaker="guest"),
        MemoRecognitionTokenV1(id="z-003", text="修修", start_ms=1_000, end_ms=1_500, confidence=0.99, speaker="guest"),
        MemoRecognitionTokenV1(id="z-004", text="再談內容", start_ms=1_500, end_ms=2_000, confidence=0.99, speaker="guest"),
        MemoRecognitionTokenV1(id="z-005", text=final_text, start_ms=2_000, end_ms=3_000, confidence=0.99, speaker="guest"),
    )
    recognition = MemoRecognitionManifestV1(
        memo_version="1.7.5",
        language="zh",
        prompt="鄭國威 泛科學 修修",
        normalized_audio_sha256=hash_file(audio),
        normalized_audio_size_bytes=audio.stat().st_size,
        normalized_audio_duration_ms=4_000,
        source_export_sha256=hash_file(source_export),
        source_export_size_bytes=source_export.stat().st_size,
        source_export_kind="memo_stdout",
        accepted_by_receipt_sha256=hash_file(recognition_acceptance),
        tokens=raw_tokens,
    )
    recognition_path = _write(tmp_path / "memo-recognition.json", recognition)
    adapter = MemoRecognizerAdapter(
        recognition_path,
        source_export=source_export,
        acceptance_receipt=recognition_acceptance,
    )
    request = RecognitionRequest(
        episode_id="zheng-minimized",
        invocation_id="recognition-zheng-minimized",
        normalized_audio=audio,
        expected_normalized_audio_hash=hash_file(audio),
    )
    evidence = adapter.recognize(request)

    cue_export = tmp_path / "memo-gui.srt"
    cue_export.write_bytes(b"accepted reviewed Memo cue export")
    cue_acceptance = tmp_path / "cue-acceptance.json"
    cue_acceptance.write_bytes(b'{"accepted":true,"reviewer":"human"}')
    cues = (
        MemoAcceptedCueV1(id="memo-cue-001", start_ms=100, end_ms=1_000, text="今天我們談", source_token_ids=("z-001", "z-002")),
        MemoAcceptedCueV1(id="memo-cue-002", start_ms=1_000, end_ms=2_000, text="修修再談內容", source_token_ids=("z-003", "z-004")),
        MemoAcceptedCueV1(id="memo-cue-003", start_ms=2_000, end_ms=3_000, text=final_text, source_token_ids=("z-005",)),
    )
    cue_manifest = MemoCueBoundaryManifestV1(
        recognition_manifest_sha256=hash_file(recognition_path),
        source_export_sha256=hash_file(cue_export),
        source_export_size_bytes=cue_export.stat().st_size,
        source_export_kind="memo_gui_srt",
        acceptance_receipt_sha256=hash_file(cue_acceptance),
        base_cues=tuple(
            MemoSourceCueV1(**cue.model_dump()) for cue in cues
        ),
        cues=cues,
    )
    cue_path = _write(tmp_path / "memo-cues.json", cue_manifest)
    authority = MemoBoundaryAuthorityV1.load_verified(
        cue_path,
        recognition_manifest_sha256=hash_file(recognition_path),
        recognition_evidence=evidence,
        source_export=cue_export,
        acceptance_receipt=cue_acceptance,
    )
    return adapter, request, evidence, authority, source_export


def _canonical(evidence):
    return reconcile_canonical(
        primary=evidence,
        source_audio_hash="a" * 64,
        normalization_receipt_hash="b" * 64,
        policy_hash="c" * 64,
    ).transcript


def test_memo_import_is_immutable_and_binds_actual_export_and_acceptance(tmp_path: Path) -> None:
    adapter, request, evidence, _authority, source_export = _memo_fixture(tmp_path)
    raw = Path(adapter._manifest_path).read_bytes()
    portable = evidence.model_copy(
        update={"raw_output": evidence.raw_output.model_copy(update={"uri": "generation-artifact://raw"})}
    )
    assert adapter.verify(portable, request=request, raw_output=raw) == portable
    source_export.write_bytes(b"tampered")
    with pytest.raises(AdapterIntegrityError, match="source export differs"):
        adapter.recognize(request)


def test_accepted_memo_cues_forbid_global_boundary_drift_and_keep_exact_times(tmp_path: Path) -> None:
    _adapter, _request, evidence, authority, _source = _memo_fixture(tmp_path)
    transcript = _canonical(evidence)
    boundaries, authoritative = authority.projection_contract(transcript)
    units = tuple(
        SemanticUnit(id=f"unit-{index}", token_ids=(token.id,), kind="token", strength=1.0)
        for index, token in enumerate(transcript.tokens)
    )
    result = project_semantic_units(
        transcript.tokens,
        units,
        HORIZONTAL_16X9,
        episode_id=transcript.episode_id,
        generation_id=transcript.generation_id,
        audio_start_ms=0,
        audio_end_ms=4_000,
        canonical_spans=transcript.spans,
        mandatory_cue_boundaries=boundaries,
        allowed_cue_boundaries=boundaries,
        authoritative_cues=authoritative,
    )
    assert len(result.projection.cues) == 3
    assert tuple((cue.start_ms, cue.end_ms) for cue in result.projection.cues) == (
        (100, 1_000), (1_000, 2_000), (2_000, 3_000)
    )
    assert all(len(cue.lines) == 1 for cue in result.projection.cues)
    assert "".join(line for cue in result.projection.cues for line in cue.lines) == "".join(
        token.text for token in transcript.tokens
    )


def test_text_only_change_preserves_memo_cue_identity_and_cross_cue_change_fails(tmp_path: Path) -> None:
    _adapter, _request, evidence, authority, _source = _memo_fixture(tmp_path)
    before = _canonical(evidence)
    changed_tokens = list(before.tokens)
    changed_tokens[2] = changed_tokens[2].model_copy(update={"text": "修修老師"})
    after = before.model_copy(update={"tokens": tuple(changed_tokens)})
    authority.assert_text_correction_preserved_boundaries(before, after)

    crossed = changed_tokens[1].model_copy(
        update={"evidence_ids": tuple((*changed_tokens[1].evidence_ids, *changed_tokens[2].evidence_ids))}
    )
    invalid = before.model_copy(update={"tokens": (changed_tokens[0], crossed, *changed_tokens[2:])})
    with pytest.raises(AdapterIntegrityError, match="crossed an accepted Memo cue"):
        authority.mandatory_cue_boundaries(invalid)


def test_punctuation_repair_is_local_logged_and_exactly_verified() -> None:
    base = MemoSourceCueV1(id="base-1", start_ms=0, end_ms=2_000, text="今天，我們聊", source_token_ids=("a", "b"))
    outputs = (
        MemoAcceptedCueV1(id="out-1", start_ms=0, end_ms=1_000, text="今天，", source_token_ids=("a",)),
        MemoAcceptedCueV1(id="out-2", start_ms=1_000, end_ms=2_000, text="我們聊", source_token_ids=("b",)),
    )
    fixed = dict(
        recognition_manifest_sha256="1" * 64,
        source_export_sha256="2" * 64,
        source_export_size_bytes=10,
        source_export_kind="reviewed_memo_cue_projection",
        acceptance_receipt_sha256="3" * 64,
        base_cues=(base,),
        cues=outputs,
    )
    manifest = MemoCueBoundaryManifestV1(
        **fixed,
        repairs=(MemoBoundaryRepairV1(
            id="repair-1",
            reason_code="explicit_punctuation",
            source_cue_ids=("base-1",),
            output_cue_ids=("out-1", "out-2"),
            proof=MemoBoundaryRepairProofV1(punctuation="，", punctuation_scalar_offset=3),
        ),),
    )
    assert manifest.repairs[0].reason_code == "explicit_punctuation"
    with pytest.raises(ValueError, match="explicit_punctuation"):
        MemoCueBoundaryManifestV1(
            **fixed,
            repairs=(MemoBoundaryRepairV1(
                id="repair-1",
                reason_code="explicit_punctuation",
                source_cue_ids=("base-1",),
                output_cue_ids=("out-1", "out-2"),
                proof=MemoBoundaryRepairProofV1(punctuation="。", punctuation_scalar_offset=3),
            ),),
        )


def test_horizontal_profile_remains_single_line() -> None:
    assert HORIZONTAL_16X9.max_lines == 1


def test_normalized_handoff_constructs_content_bound_reused_receipt(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "normalized.wav"
    with wave.open(str(audio), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(2)
        target.setframerate(48_000)
        target.writeframes(b"\0" * 48_000 * 2 * 2)
    manifest = NormalizedAudioHandoffManifestV1(
        normalized_audio_sha256=hash_file(audio),
        normalized_audio_size_bytes=audio.stat().st_size,
        normalized_audio_duration_ms=1_000,
        accepted_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )
    manifest_path = _write(tmp_path / "normalized-handoff.json", manifest)

    result = VerifiedNormalizedAudioHandoffAdapter(manifest_path).normalize(
        NormalizeRequest(
            source_audio=audio,
            expected_source_hash=hash_file(audio),
        )
    )

    assert result.normalized_audio == audio.resolve()
    assert result.receipt.status == "accepted"
    assert result.receipt.production_source == "reused"
    assert result.receipt.original_production_id == (
        f"upstream-normalized-{hash_file(audio)}"
    )
    assert result.receipt.normalized_duration_ms == 1_000


def test_production_composition_is_memo_primary_and_corroborators_are_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _adapter, _request, _evidence, _authority, _source = _memo_fixture(tmp_path)
    environment = {
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
    monkeypatch.setattr("agents.brook.podcast_subtitles.production.os.environ", environment)
    module = build_production(FactoryContextV1(1, tmp_path / "episode", None))
    assert len(module._recognizers) == 1
    assert isinstance(module._recognizers[0], MemoRecognizerAdapter)
    assert module._recognition_independence_policy is None
    assert module._memo_boundary_authority_factory is not None
    assert type(module._normalizer).__name__ == "VerifiedNormalizedAudioHandoffAdapter"


def test_module_projects_corrected_text_on_exact_memo_cues_and_replays_authority_hash(
    tmp_path: Path,
) -> None:
    (tmp_path / "memo").mkdir()
    adapter, _request, _evidence, accepted_authority, _source_export = _memo_fixture(
        tmp_path / "memo", final_text="錯甲"
    )
    normalized = Path(adapter._manifest_path).parent / "normalized.wav"
    source = tmp_path / "source.wav"
    source.write_bytes(b"source-audio")
    semantic = _DynamicSemanticAnalyzer()

    def authority_for(stored_evidence):
        return MemoBoundaryAuthorityV1(
            accepted_authority.manifest,
            manifest_bytes=accepted_authority.manifest_bytes,
            recognition_evidence=stored_evidence,
        )

    def build_module(*, boundary_factory=authority_for) -> PodcastSubtitleV2:
        return PodcastSubtitleV2(
            tmp_path / "episode",
            normalizer=FixtureNormalizerAdapter(
                normalized,
                _accepted_receipt(source, normalized),
            ),
            recognizers=(adapter,),
            corrector=_SequentialDisjointCorrector(),
            audio_auditor=FixtureAudioAuditorAdapter(),
            arbiter=_AcceptEveryProposalArbiter(),
            speech_coverage_analyzer=FixtureSpeechCoverageAnalyzer(),
            semantic_analyzer=semantic,
            corrector_identity=_adapter_identity("fixture-corrector"),
            semantic_analyzer_identity=AdapterIdentity(
                name="fixture-semantic",
                version="1",
                config_hash=semantic.identity.config_hash,
                execution_mode="fixture",
            ),
            memo_boundary_authority_factory=boundary_factory,
        )

    module = build_module()
    created = module.create(
        CreateRequest(episode_id="zheng-minimized", source_audio=source)
    )
    target = created.transcript.spans[-1]
    selected = tuple(
        token for token in created.transcript.tokens if token.id in target.token_ids
    )
    corrected = module.resolve(
        ResolveRequest(
            created.generation_id,
            (
                CorrectionDecision(
                    event_id="accept-memo-text-correction",
                    episode_id=created.transcript.episode_id,
                    generation_id=created.generation_id,
                    target_span_ids=(target.id,),
                    target_start_ms=target.start_ms,
                    target_end_ms=target.end_ms,
                    evidence_fingerprint=review_target_fingerprint(
                        created.transcript, (target.id,)
                    ),
                    issue_ids=tuple(
                        issue.id
                        for issue in created.transcript.review_issues
                        if target.id in issue.span_ids
                    ),
                    audio_evidence_ids=tuple(
                        sorted(
                            {
                                evidence_id
                                for token in selected
                                for evidence_id in token.evidence_ids
                            }
                        )
                    ),
                    evidence_basis="audio",
                    action="replace",
                    replacement_text="正甲",
                    replacement_lexemes=("正甲",),
                    actor_kind="human",
                    actor="reviewer",
                    rationale="verified Memo text correction",
                    timestamp=datetime(2026, 8, 16, tzinfo=timezone.utc),
                ),
            ),
        )
    )
    assert isinstance(corrected, AcceptedGeneration)
    assert "正甲" in "".join(token.text for token in corrected.transcript.tokens)

    verified = module.project(
        ProjectRequest(corrected.generation_id, HORIZONTAL_16X9)
    )
    assert tuple(
        (cue.start_ms, cue.end_ms) for cue in verified.projection.cues
    ) == ((100, 1_000), (1_000, 2_000), (2_000, 3_000))
    assert len(verified.projection.cues) == 3
    assert all(len(cue.lines) == 1 for cue in verified.projection.cues)
    assert "正甲" in verified.srt_bytes.decode("utf-8")

    reopened = build_module()
    reopened._verify_projection_directory(
        verified.projection_id,
        expected_generation_id=corrected.generation_id,
    )

    class DriftedAuthority:
        content_hash = "f" * 64

        def __init__(self, delegate: MemoBoundaryAuthorityV1) -> None:
            self._delegate = delegate

        def projection_contract(self, transcript):
            return self._delegate.projection_contract(transcript)

    drifted = build_module(
        boundary_factory=lambda stored_evidence: DriftedAuthority(
            authority_for(stored_evidence)
        )
    )
    with pytest.raises(GenerationIsolationError, match="lineage is not reproducible"):
        drifted._verify_projection_directory(
            verified.projection_id,
            expected_generation_id=corrected.generation_id,
        )
