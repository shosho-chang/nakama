from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.adapters import SemanticAnalyzerAdapter
from agents.brook.podcast_subtitles.boundary_constraints import assess_boundary_edges
from agents.brook.podcast_subtitles.canonical import reconcile_canonical
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, sha256_bytes
from agents.brook.podcast_subtitles.ports import (
    AdapterIntegrityError,
    AdapterUnavailableError,
    AdapterWorkPending,
    SemanticAnalysisRequest,
    SemanticRunResult,
)
from agents.brook.podcast_subtitles.profiles import HORIZONTAL_16X9
from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    EvidenceToken,
    ProtectedTokenRange,
    RecognitionEvidence,
)

H_AUDIO = "a" * 64
H_SOURCE = "b" * 64
H_CONFIG = "c" * 64
H_RAW = "d" * 64
H_POLICY = "e" * 64
H_RECEIPT = "f" * 64


def _request(token_count: int = 3) -> SemanticAnalysisRequest:
    words = tuple("語意邊界需要完整證據才能安全投影")
    evidence = RecognitionEvidence(
        episode_id="semantic-boundary-episode",
        invocation_id="asr-run-1",
        adapter="fixture-asr",
        model="fixture-v1",
        language="zh-Hant-TW",
        config_hash=H_CONFIG,
        raw_output=ArtifactDigest(uri="fixture://raw", sha256=H_RAW, size_bytes=10),
        raw_output_hash=H_RAW,
        normalized_audio_hash=H_AUDIO,
        tokens=tuple(
            EvidenceToken(
                id=f"raw-{index}",
                text=words[index],
                start_ms=index * 100,
                end_ms=(index + 1) * 100,
                confidence=0.96,
                speaker="speaker-1",
            )
            for index in range(token_count)
        ),
    )
    result = reconcile_canonical(
        primary=evidence,
        source_audio_hash=H_SOURCE,
        normalization_receipt_hash=H_RECEIPT,
        policy_hash=H_POLICY,
    )
    return SemanticAnalysisRequest(transcript=result.transcript)


def _response(
    packet: dict[str, object],
    *,
    relations: dict[int, tuple[str, str, float]] | None = None,
) -> dict[str, object]:
    configured = relations or {}
    boundaries: list[dict[str, object]] = []
    for owned in packet["owned_boundaries"]:
        edge = dict(owned)
        edge_index = int(edge["edge_index"])
        cue, line, strength = configured.get(
            edge_index,
            ("discouraged", "discouraged", 0.8),
        )
        boundaries.append(
            {
                **edge,
                "cue_relation": cue,
                "line_relation": line,
                "strength": strength,
            }
        )
    return {
        "schema_version": 5,
        "work_packet_id": packet["work_packet_id"],
        "boundaries": boundaries,
    }


def _response_bytes(packet: dict[str, object]) -> bytes:
    return (json.dumps(_response(packet), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def test_response_is_one_exact_assessment_per_owned_boundary() -> None:
    request = _request()
    adapter = SemanticAnalyzerAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)
    first_edge = int(packet["owned_boundaries"][0]["edge_index"])

    run = adapter.import_work_results_with_receipts(
        request,
        (
            _response(
                packet,
                relations={
                    first_edge: ("forbidden", "neutral", 0.99),
                    first_edge + 1: ("preferred", "preferred", 0.8),
                },
            ),
        ),
    )

    assert len(run.units) == len(request.transcript.tokens) - 1
    assert {token_id for unit in run.units for token_id in unit.token_ids} == {
        token.id for token in request.transcript.tokens
    }
    assert run.units[0].kind == "boundary_pair"
    assert run.units[0].cue_boundary_relation == "forbidden"
    assert run.units[0].line_boundary_relation == "neutral"
    assert run.units[0].forbid_cue_breaks is True
    assert run.units[0].forbid_line_breaks is False
    assert run.execution_receipts[0].owned_boundary_indices == (1, 2)
    assert run.execution_receipts[0].boundary_ownership_contract == "canonical-adjacent-edge-v1"


def test_packet_binds_policy_and_least_context_protected_ranges() -> None:
    plain = _request()
    protected_token = plain.transcript.tokens[1]
    protected = ProtectedTokenRange(
        id="protected-policy-term-1",
        token_ids=(protected_token.id,),
        canonical_text=protected_token.text,
        kind="term",
        source="policy_vocabulary",
        source_value_hash=hashlib.sha256(protected_token.text.encode("utf-8")).hexdigest(),
    )
    request = SemanticAnalysisRequest(
        transcript=plain.transcript,
        policy_hash=plain.transcript.policy_hash,
        protected_ranges=(protected,),
    )
    adapter = SemanticAnalyzerAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)

    assert packet["policy_hash"] == plain.transcript.policy_hash
    assert packet["protected_range_set_hash"] == request.protected_range_set_hash
    assert packet["protected_ranges"] == [protected.model_dump(mode="json")]

    run = adapter.import_work_results_with_receipts(request, (_response(packet),))
    drifted = SemanticAnalysisRequest(transcript=request.transcript)
    with pytest.raises(AdapterIntegrityError, match="missing Semantic result for work packet"):
        adapter.replay(
            drifted,
            units=run.units,
            execution_receipts=run.execution_receipts,
            request_bytes=run.request_bytes,
            response_bytes=run.response_bytes,
        )


def test_long_run_all_neutral_boundary_evidence_fails_closed() -> None:
    request = _request(6)
    adapter = SemanticAnalyzerAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)
    neutral = {
        int(edge["edge_index"]): ("neutral", "neutral", 0.0) for edge in packet["owned_boundaries"]
    }

    with pytest.raises(AdapterIntegrityError, match="singleton-only/all-neutral"):
        adapter.import_work_result(request, _response(packet, relations=neutral))


def test_legacy_range_response_schema_fails_closed() -> None:
    request = _request()
    adapter = SemanticAnalyzerAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)

    with pytest.raises(AdapterIntegrityError, match="response violates schema"):
        adapter.import_work_result(
            request,
            {
                "schema_version": 4,
                "work_packet_id": packet["work_packet_id"],
                "units": [],
            },
        )


@pytest.mark.parametrize("mutation", ("missing", "duplicate", "unexpected", "endpoint"))
def test_boundary_coverage_and_identity_fail_closed(mutation: str) -> None:
    request = _request()
    adapter = SemanticAnalyzerAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)
    payload = _response(packet)
    boundaries = list(payload["boundaries"])

    if mutation == "missing":
        boundaries.pop()
        message = "every owned boundary exactly once"
    elif mutation == "duplicate":
        boundaries.append(dict(boundaries[0]))
        message = "duplicate Semantic boundary"
    elif mutation == "unexpected":
        boundaries[-1] = {**boundaries[-1], "edge_index": 999}
        message = "every owned boundary exactly once"
    else:
        boundaries[0] = {**boundaries[0], "right_token_id": "invented-token"}
        message = "token endpoints drifted"
    payload["boundaries"] = boundaries

    with pytest.raises(AdapterIntegrityError, match=message):
        adapter.import_work_result(request, payload)


@pytest.mark.parametrize(
    "cue_relation,line_relation,strength",
    (
        ("neutral", "neutral", 0.5),
        ("discouraged", "neutral", 0.0),
        ("neutral", "preferred", 0.0),
    ),
)
def test_boundary_strength_contract_fails_closed(
    cue_relation: str,
    line_relation: str,
    strength: float,
) -> None:
    request = _request()
    adapter = SemanticAnalyzerAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)
    first_edge = int(packet["owned_boundaries"][0]["edge_index"])

    with pytest.raises(AdapterIntegrityError, match="neutral Semantic boundary"):
        adapter.import_work_result(
            request,
            _response(
                packet,
                relations={first_edge: (cue_relation, line_relation, strength)},
            ),
        )


def test_packets_own_every_boundary_once_and_expose_bounded_context() -> None:
    request = _request()
    adapter = SemanticAnalyzerAdapter(
        model="codex",
        model_version="5.6-sol",
        max_target_tokens_per_packet=2,
    )
    packets = adapter.export_work_packets(request)

    assert len(packets) == 2
    first_ids = tuple(item["id"] for item in packets[0]["target_tokens"])
    second_ids = tuple(item["id"] for item in packets[1]["target_tokens"])
    assert first_ids[-1] == second_ids[0]
    assert [item["id"] for item in packets[0]["context_after"]] == [second_ids[1]]
    assert [item["id"] for item in packets[1]["context_before"]] == [first_ids[0]]
    assert [edge["edge_index"] for packet in packets for edge in packet["owned_boundaries"]] == [
        1,
        2,
    ]

    run = adapter.import_work_results_with_receipts(
        request,
        tuple(_response(packet) for packet in packets),
    )
    assert [
        edge for receipt in run.execution_receipts for edge in receipt.owned_boundary_indices
    ] == [1, 2]


def test_four_token_phrase_crossing_packet_seam_is_fully_expressible() -> None:
    request = _request(6)
    adapter = SemanticAnalyzerAdapter(
        model="codex",
        model_version="5.6-sol",
        max_target_tokens_per_packet=3,
    )
    packets = adapter.export_work_packets(request)
    phrase_edges = {2, 3, 4}
    payloads = []
    for packet in packets:
        relations = {
            int(edge["edge_index"]): (
                ("forbidden", "forbidden", 1.0)
                if int(edge["edge_index"]) in phrase_edges
                else ("preferred", "preferred", 0.7)
            )
            for edge in packet["owned_boundaries"]
        }
        payloads.append(_response(packet, relations=relations))

    run = adapter.import_work_results_with_receipts(request, payloads)
    edges = assess_boundary_edges(request.transcript.tokens, run.units, HORIZONTAL_16X9)

    assert {edge.edge_index for edge in edges if edge.cue_relation == "forbidden"} == (phrase_edges)
    assert any(3 in receipt.owned_boundary_indices for receipt in run.execution_receipts)


def test_single_token_has_empty_but_complete_boundary_partition() -> None:
    request = _request(1)
    adapter = SemanticAnalyzerAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)

    assert packet["owned_boundaries"] == []
    run = adapter.import_work_results_with_receipts(request, (_response(packet),))

    assert len(run.units) == 1
    assert run.units[0].kind == "token"
    assert run.execution_receipts[0].owned_boundary_indices == ()


def test_workspace_emits_pending_then_strictly_resumes(tmp_path: Path) -> None:
    request = _request()
    adapter = SemanticAnalyzerAdapter(
        model="codex",
        model_version="5.6-sol",
        workspace_root=tmp_path / "work",
    )

    with pytest.raises(AdapterWorkPending) as pending:
        adapter.partition(request)
    packet = json.loads(pending.value.packet_paths[0].read_text(encoding="utf-8"))
    pending.value.response_paths[0].write_text(
        json.dumps(_response(packet), ensure_ascii=False),
        encoding="utf-8",
    )

    units = adapter.partition(request)
    assert {token_id for unit in units for token_id in unit.token_ids} == {
        token.id for token in request.transcript.tokens
    }


def test_execution_receipt_preserves_exact_bytes_and_replays() -> None:
    request = _request()
    adapter = SemanticAnalyzerAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)
    response_bytes = _response_bytes(packet)

    run = adapter.import_work_results_with_receipts(request, (response_bytes,))

    assert run.request_bytes == (canonical_json_bytes(packet),)
    assert run.response_bytes == (response_bytes,)
    assert run.execution_receipts[0].request.sha256 == sha256_bytes(run.request_bytes[0])
    assert run.execution_receipts[0].response.sha256 == sha256_bytes(response_bytes)
    assert run.execution_receipts[0].adapter_identity == adapter.identity
    assert (
        adapter.replay(
            request,
            units=run.units,
            execution_receipts=run.execution_receipts,
            request_bytes=run.request_bytes,
            response_bytes=run.response_bytes,
        )
        == run
    )


@pytest.mark.parametrize("payload", [b"", b"not-json", b'{"schema_version":5}'])
def test_empty_or_malformed_raw_response_is_never_semantic_evidence(
    payload: bytes,
) -> None:
    adapter = SemanticAnalyzerAdapter(model="codex", model_version="5.6-sol")
    with pytest.raises(AdapterIntegrityError):
        adapter.import_work_results_with_receipts(_request(), (payload,))


def test_receipt_reorder_duplicate_packet_and_duplicate_edge_fail_closed() -> None:
    request = _request()
    adapter = SemanticAnalyzerAdapter(
        model="codex",
        model_version="5.6-sol",
        max_target_tokens_per_packet=2,
    )
    packets = adapter.export_work_packets(request)
    responses = tuple(_response_bytes(packet) for packet in packets)
    run = adapter.import_work_results_with_receipts(request, responses)

    with pytest.raises(AdapterIntegrityError, match="reordered or incomplete"):
        adapter.replay(
            request,
            units=run.units,
            execution_receipts=tuple(reversed(run.execution_receipts)),
            request_bytes=tuple(reversed(run.request_bytes)),
            response_bytes=tuple(reversed(run.response_bytes)),
        )
    with pytest.raises(AdapterIntegrityError, match="duplicate Semantic work_packet_id"):
        adapter.import_work_results_with_receipts(request, (responses[0], responses[0]))

    duplicate_edge = json.loads(responses[0])
    duplicate_edge["boundaries"].append(dict(duplicate_edge["boundaries"][0]))
    with pytest.raises(AdapterIntegrityError, match="duplicate Semantic boundary"):
        adapter.import_work_results_with_receipts(
            request,
            (canonical_json_bytes(duplicate_edge), responses[1]),
        )


def test_request_response_tamper_and_identity_drift_fail_replay() -> None:
    request = _request()
    adapter = SemanticAnalyzerAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)
    run = adapter.import_work_results_with_receipts(request, (_response_bytes(packet),))

    with pytest.raises(AdapterIntegrityError, match="stored Semantic execution proof"):
        adapter.replay(
            request,
            units=run.units,
            execution_receipts=run.execution_receipts,
            request_bytes=(run.request_bytes[0] + b" ",),
            response_bytes=run.response_bytes,
        )
    with pytest.raises(AdapterIntegrityError, match="stored Semantic execution proof"):
        adapter.replay(
            request,
            units=run.units,
            execution_receipts=run.execution_receipts,
            request_bytes=run.request_bytes,
            response_bytes=(run.response_bytes[0] + b" ",),
        )

    drifted = SemanticAnalyzerAdapter(
        model="codex",
        model_version="5.6-sol",
        context_tokens_per_side=9,
    )
    with pytest.raises(AdapterIntegrityError, match="identity drifted"):
        drifted.replay(
            request,
            units=run.units,
            execution_receipts=run.execution_receipts,
            request_bytes=run.request_bytes,
            response_bytes=run.response_bytes,
        )

    with pytest.raises(ValueError, match="exact digest"):
        replace(
            run.execution_receipts[0],
            request=run.execution_receipts[0].request.model_copy(
                update={"uri": "projection-artifact://semantic/requests/../../projection.json"}
            ),
        )


def test_token_and_boundary_ownership_forgery_fail_closed() -> None:
    request = _request()
    adapter = SemanticAnalyzerAdapter(
        model="codex",
        model_version="5.6-sol",
        max_target_tokens_per_packet=2,
    )
    packets = adapter.export_work_packets(request)
    run = adapter.import_work_results_with_receipts(
        request,
        tuple(_response_bytes(packet) for packet in packets),
    )

    forged_tokens = replace(
        run.execution_receipts[1],
        owned_token_ids=run.execution_receipts[1].target_token_ids,
    )
    with pytest.raises(ValueError, match="ownership is not an exact Canonical partition"):
        SemanticRunResult(
            canonical_token_ids=run.canonical_token_ids,
            units=run.units,
            execution_receipts=(run.execution_receipts[0], forged_tokens),
            request_bytes=run.request_bytes,
            response_bytes=run.response_bytes,
        )

    forged_boundaries = replace(
        run.execution_receipts[1],
        owned_boundary_indices=run.execution_receipts[0].owned_boundary_indices,
    )
    with pytest.raises(ValueError, match="owns a boundary|boundary ownership is not an exact"):
        SemanticRunResult(
            canonical_token_ids=run.canonical_token_ids,
            units=run.units,
            execution_receipts=(run.execution_receipts[0], forged_boundaries),
            request_bytes=run.request_bytes,
            response_bytes=run.response_bytes,
        )


def test_runner_failure_and_default_paid_api_policy_are_blocking() -> None:
    def broken_runner(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("provider down")

    broken = SemanticAnalyzerAdapter(
        model="codex",
        model_version="5.6-sol",
        runner=broken_runner,
    )
    with pytest.raises(AdapterUnavailableError, match="runner failed"):
        broken.partition(_request())

    subscription = SemanticAnalyzerAdapter(model="claude-opus", model_version="4.7")
    with pytest.raises(AdapterUnavailableError, match="export_work_packets"):
        subscription.partition(_request())
