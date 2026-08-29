from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.adapters.semantic import SemanticAnalyzerAdapter
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes
from agents.brook.podcast_subtitles.ports import AdapterWorkPending
from scripts.podcast_subtitle_v2_work_packets import (
    WorkPacketOperatorError,
    accept_candidate,
    discover_work_items,
    render_worker_bundle,
    validate_candidate,
)
from tests.agents.brook.podcast_subtitles.test_semantic_boundary_adapter import (
    _request as semantic_request,
)
from tests.agents.brook.podcast_subtitles.test_semantic_boundary_adapter import (
    _response_bytes as semantic_response_bytes,
)


def _semantic_pending(tmp_path: Path, *, token_count: int = 3) -> tuple[Path, Path, Path]:
    episode = tmp_path / "episode"
    workspace = episode / ".subtitle-v2" / "subscription-work"
    adapter = SemanticAnalyzerAdapter(
        model="codex",
        model_version="5.6-sol",
        workspace_root=workspace,
        execution_mode="subscription",
    )
    request = semantic_request(token_count)
    with pytest.raises(AdapterWorkPending) as pending:
        adapter.partition_with_receipts(request)
    request_path = pending.value.packet_paths[0]
    packet = json.loads(request_path.read_text(encoding="utf-8"))
    candidate = tmp_path / "candidate.json"
    candidate.write_bytes(semantic_response_bytes(packet))
    return episode, request_path, candidate


def test_list_render_validate_and_atomic_accept_need_no_schema_guessing(tmp_path: Path) -> None:
    episode, request_path, candidate = _semantic_pending(tmp_path)

    pending = discover_work_items(episode)
    assert len(pending) == 1
    assert pending[0].adapter == "semantic"
    assert pending[0].status == "pending"
    assert pending[0].response_path.name.endswith(".response.json")

    bundle = render_worker_bundle(episode, request_path)
    assert bundle["contract"] == "podcast-subtitle-v2-subscription-worker-v1"
    assert bundle["request"]["sha256"]
    assert bundle["request_json"]["work_packet_id"] == pending[0].request_id
    assert bundle["response_json_schema"]["additionalProperties"] is False
    assert "every owned boundary exactly once" in bundle["worker_instruction"]
    assert bundle["provider_gate"] == "text_capable_subscription_worker"

    validated = validate_candidate(episode, request_path, candidate)
    assert validated.response_path == pending[0].response_path
    accepted = accept_candidate(episode, request_path, candidate)
    assert accepted == pending[0].response_path
    assert accepted.read_bytes() == candidate.read_bytes()

    with pytest.raises(WorkPacketOperatorError, match="already exists"):
        accept_candidate(episode, request_path, candidate)


def test_malformed_and_cross_bound_semantic_responses_fail_closed(tmp_path: Path) -> None:
    episode, request_path, candidate = _semantic_pending(tmp_path)
    candidate.write_bytes(b'{"schema_version":5')
    with pytest.raises(WorkPacketOperatorError, match="response"):
        validate_candidate(episode, request_path, candidate)

    other_episode, other_request, other_candidate = _semantic_pending(
        tmp_path / "other", token_count=4
    )
    del other_episode, other_request
    with pytest.raises(WorkPacketOperatorError, match="another work packet"):
        validate_candidate(episode, request_path, other_candidate)


def test_wrong_request_filename_path_unknown_adapter_and_request_drift_fail_closed(
    tmp_path: Path,
) -> None:
    episode, request_path, candidate = _semantic_pending(tmp_path)

    wrong_name = request_path.with_name("wrong.json")
    wrong_name.write_bytes(request_path.read_bytes())
    with pytest.raises(WorkPacketOperatorError, match="filename"):
        validate_candidate(episode, wrong_name, candidate)

    unknown = (
        episode
        / ".subtitle-v2"
        / "subscription-work"
        / "mystery-adapter"
        / "packets"
        / request_path.name
    )
    unknown.parent.mkdir(parents=True)
    unknown.write_bytes(request_path.read_bytes())
    with pytest.raises(WorkPacketOperatorError, match="known subscription adapter"):
        render_worker_bundle(episode, unknown)

    packet = json.loads(request_path.read_text(encoding="utf-8"))
    packet["model_version"] = "drifted"
    request_path.write_bytes(canonical_json_bytes(packet))
    with pytest.raises(WorkPacketOperatorError, match="request"):
        accept_candidate(episode, request_path, candidate)


def test_audio_bundle_requires_a_real_audio_capable_review_gate(tmp_path: Path) -> None:
    from agents.brook.podcast_subtitles.audio_audit_execution import (
        AudioAuditWorkPending,
        AudioFullAuditExecutor,
        build_audio_audit_adapter_identity,
    )
    from tests.agents.brook.podcast_subtitles.test_audio_audit_execution import (
        _audio_inputs,
    )
    from tests.agents.brook.podcast_subtitles.test_audio_audit_execution import (
        _response_bytes as audio_response_bytes,
    )

    episode = tmp_path / "episode"
    workspace = episode / ".subtitle-v2" / "subscription-work"
    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir()
    fixture, sources = _audio_inputs(fixture_root)
    audit_plan, _, _, execution = fixture[:4]
    executor = AudioFullAuditExecutor(
        identity=build_audio_audit_adapter_identity(
            adapter="fixture-audio-auditor",
            adapter_version="1",
            model="fixture-audio-model",
            model_version="1",
            execution_mode="subscription",
        ),
        workspace_root=workspace,
    )
    with pytest.raises(AudioAuditWorkPending) as pending:
        executor.execute(execution, audit_plan, sources)

    bundle = render_worker_bundle(episode, pending.value.request_paths[0])
    assert bundle["provider_gate"] == "human_or_audio_capable_subscription_worker_required"
    assert bundle["audio_clip"]["path"].endswith(".wav")
    assert "actually listen" in bundle["worker_instruction"]
    assert "cannot access audio" in bundle["worker_instruction"]

    request_path = pending.value.request_paths[0]
    clip_path = pending.value.clip_paths[0]
    candidate = tmp_path / "audio-candidate.json"
    candidate.write_bytes(audio_response_bytes(request_path.read_bytes(), clip_path.read_bytes()))
    validate_candidate(episode, request_path, candidate)

    clip_path.write_bytes(clip_path.read_bytes() + b"drift")
    with pytest.raises(WorkPacketOperatorError, match="clip"):
        validate_candidate(episode, request_path, candidate)


def test_text_bundle_exposes_exact_schema_and_validates_with_production_parser(
    tmp_path: Path,
) -> None:
    from agents.brook.podcast_subtitles.text_audit_execution import TextAuditWorkPending
    from tests.agents.brook.podcast_subtitles.test_text_audit_execution import (
        _executor as text_executor,
    )
    from tests.agents.brook.podcast_subtitles.test_text_audit_execution import (
        _inputs as text_inputs,
    )
    from tests.agents.brook.podcast_subtitles.test_text_audit_execution import (
        _response_bytes as text_response_bytes,
    )

    episode = tmp_path / "episode"
    workspace = episode / ".subtitle-v2" / "subscription-work"
    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir()
    fixture, sources = text_inputs(fixture_root)
    audit_plan, _, _, execution = fixture[:4]
    executor = text_executor(workspace_root=workspace)
    with pytest.raises(TextAuditWorkPending) as pending:
        executor.execute(execution, audit_plan, sources)

    request_path = pending.value.request_paths[0]
    bundle = render_worker_bundle(episode, request_path)
    assert bundle["provider_gate"] == "text_capable_subscription_worker"
    assert bundle["response_json_schema"]["title"] == "TextAuditProviderResponseV2"
    assert "never claim to have heard" in bundle["worker_instruction"]

    candidate = tmp_path / "text-candidate.json"
    candidate.write_bytes(text_response_bytes(request_path.read_bytes()))
    validate_candidate(episode, request_path, candidate)
