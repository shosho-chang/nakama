"""Provider-free crash recovery contract for Auphonic normalization runs."""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_object
from agents.brook.podcast_subtitles.normalization_run import (
    ExternalAnchorReconciliationPageV1,
    ExternalAnchorReconciliationV1,
    NormalizationAdapterIdentityV1,
    NormalizationAlignmentPolicyV1,
    NormalizationArtifactBindingV1,
    NormalizationContentDigestV1,
    NormalizationOutputContractV1,
    NormalizationRunConflictError,
    NormalizationRunIntegrityError,
    NormalizationRunRepository,
    NormalizationSettingV1,
    ProviderObservationV1,
    build_external_anchor_reconciliation,
    build_normalization_run_plan,
    build_normalization_run_proof,
    build_normalization_run_request,
    build_provider_observation,
)

H0 = "0" * 64
H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64
H5 = "5" * 64
H6 = "6" * 64
H7 = "7" * 64
H8 = "8" * 64
H9 = "9" * 64


def _request(**changes: object):
    values: dict[str, object] = {
        "source_sha256": H0,
        "source_size_bytes": 123_456,
        "source_probe_hash": H1,
        "effective_settings": (
            NormalizationSettingV1(scope="algorithm", name="denoise", value=True),
            NormalizationSettingV1(scope="output", name="bitdepth", value=24),
        ),
        "preset": None,
        "output_contract": NormalizationOutputContractV1(
            container="wav",
            codec="pcm_s24le",
            bit_depth=24,
            audio_stream_count=1,
            channel_policy="preserve",
            sample_rate_policy="preserve",
        ),
        "alignment_policy": NormalizationAlignmentPolicyV1(
            algorithm="cross_correlation_v1",
            trim_jingle=True,
            requested_jingle_ms=6000,
            minimum_head_correlation=0.5,
            minimum_mid_correlation=0.5,
            maximum_drift_ms=50,
        ),
        "normalizer_identity": NormalizationAdapterIdentityV1(
            name="auphonic-normalizer-v2",
            version="1",
            config_hash=H2,
            code_hash=H3,
            runtime_hash=H4,
        ),
        "provider_protocol_version": "auphonic-rest-v1",
        "credential_ref": "cred_" + "a" * 32,
    }
    values.update(changes)
    return build_normalization_run_request(**values)


def _repository(tmp_path: Path) -> NormalizationRunRepository:
    return NormalizationRunRepository(tmp_path / ".subtitle-v2")


def _observation(
    operation: str,
    *,
    production_uuid: str = "prod-12345678",
    provider_status_code: int | None = None,
    submitted_settings_hash: str | None = None,
    source_checksum: str | None = None,
) -> ProviderObservationV1:
    return build_provider_observation(
        operation=operation,
        outcome="acknowledged",
        http_status_code=200,
        provider_status_code=provider_status_code,
        production_uuid=production_uuid,
        submitted_settings_hash=submitted_settings_hash,
        source_checksum=source_checksum,
        output_contract_hash=None,
    )


def _binding(name: str, sha256: str) -> NormalizationArtifactBindingV1:
    return NormalizationArtifactBindingV1(
        name=name,
        digest=NormalizationContentDigestV1(sha256=sha256, size_bytes=64),
    )


def _fresh_process_load(subtitle_root: Path, request_key: str) -> subprocess.CompletedProcess[str]:
    code = (
        "import sys; "
        "from agents.brook.podcast_subtitles.normalization_run import "
        "NormalizationRunRepository; "
        "stored = NormalizationRunRepository(sys.argv[1]).load(sys.argv[2]); "
        "print(stored.event.state)"
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(subtitle_root), request_key],
        cwd=Path(__file__).resolve().parents[4],
        check=False,
        capture_output=True,
        text=True,
    )


def _reconciliation(
    external_anchor: str,
    *,
    matches: tuple[str, ...] = ("prod-12345678",),
) -> ExternalAnchorReconciliationV1:
    return build_external_anchor_reconciliation(
        external_anchor=external_anchor,
        pages=(
            ExternalAnchorReconciliationPageV1(
                offset=0,
                limit=2,
                result_count=2,
                projection_hash=H1,
            ),
            ExternalAnchorReconciliationPageV1(
                offset=2,
                limit=2,
                result_count=1,
                projection_hash=H2,
            ),
        ),
        matched_production_uuids=matches,
    )


def _advance_to_provider_completed(repository: NormalizationRunRepository, request=None):
    request = request or _request()
    stored = repository.prepare(request)
    stored = repository.append(stored.plan, state="create_in_flight")
    stored = repository.append(
        stored.plan,
        state="production_bound",
        observation=_observation("create"),
    )
    stored = repository.append(stored.plan, state="upload_in_flight")
    stored = repository.append(
        stored.plan,
        state="upload_acknowledged",
        observation=_observation("upload"),
    )
    stored = repository.append(stored.plan, state="start_in_flight")
    stored = repository.append(
        stored.plan,
        state="processing",
        observation=_observation("start", provider_status_code=1),
    )
    stored = repository.append(
        stored.plan,
        state="provider_completed",
        observation=_observation(
            "poll",
            provider_status_code=3,
            submitted_settings_hash=request.settings_hash,
        ),
    )
    return stored


def _advance_to_receipt_ready(repository: NormalizationRunRepository, request=None):
    request = request or _request()
    stored = _advance_to_provider_completed(repository, request)
    stored = repository.append(stored.plan, state="download_in_flight")
    stored = repository.append(
        stored.plan,
        state="downloaded_verified",
        artifacts=(_binding("raw_audio", H5), _binding("raw_probe", H6)),
    )
    stored = repository.append(stored.plan, state="alignment_in_flight")
    stored = repository.append(
        stored.plan,
        state="alignment_verified",
        artifacts=(
            _binding("aligned_audio", H7),
            _binding("aligned_probe", H8),
            _binding("clock_map", H9),
        ),
    )
    return repository.append(
        stored.plan,
        state="receipt_ready",
        artifacts=(_binding("normalization_receipt", H4),),
    )


def test_request_key_is_deterministic_and_excludes_random_external_anchor() -> None:
    request = _request()
    first = build_normalization_run_plan(
        request=request,
        external_anchor="norm_" + "a" * 32,
    )
    second = build_normalization_run_plan(
        request=request,
        external_anchor="norm_" + "b" * 32,
    )

    assert first.request_key == second.request_key == request.request_key
    assert first.id != second.id


@pytest.mark.parametrize(
    "change",
    [
        {"source_sha256": H1},
        {"source_size_bytes": 123_457},
        {"source_probe_hash": H2},
        {
            "effective_settings": (
                NormalizationSettingV1(scope="algorithm", name="denoise", value=False),
            )
        },
        {"preset": "preset-v2"},
        {
            "output_contract": NormalizationOutputContractV1(
                container="wav",
                codec="pcm_s16le",
                bit_depth=16,
                audio_stream_count=1,
                channel_policy="preserve",
                sample_rate_policy="preserve",
            )
        },
        {
            "alignment_policy": NormalizationAlignmentPolicyV1(
                algorithm="cross_correlation_v1",
                trim_jingle=True,
                requested_jingle_ms=6100,
                minimum_head_correlation=0.5,
                minimum_mid_correlation=0.5,
                maximum_drift_ms=50,
            )
        },
        {
            "normalizer_identity": NormalizationAdapterIdentityV1(
                name="auphonic-normalizer-v2",
                version="1",
                config_hash=H2,
                code_hash=H3,
                runtime_hash=H5,
            )
        },
        {"provider_protocol_version": "auphonic-rest-v2"},
        {"credential_ref": "cred_" + "b" * 32},
    ],
)
def test_every_request_binding_drift_changes_the_request_key(change: dict[str, object]) -> None:
    assert _request(**change).request_key != _request().request_key


def test_effective_settings_must_be_sorted_and_unique() -> None:
    with pytest.raises(ValidationError, match="sorted"):
        _request(
            effective_settings=(
                NormalizationSettingV1(scope="output", name="format", value="wav"),
                NormalizationSettingV1(scope="algorithm", name="denoise", value=True),
            )
        )
    with pytest.raises(ValidationError, match="duplicate"):
        _request(
            effective_settings=(
                NormalizationSettingV1(scope="algorithm", name="denoise", value=True),
                NormalizationSettingV1(scope="algorithm", name="denoise", value=False),
            )
        )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_effective_setting_float_must_be_finite(value: float) -> None:
    with pytest.raises(ValidationError, match="finite"):
        NormalizationSettingV1(scope="algorithm", name="level", value=value)


def test_effective_setting_string_is_bounded() -> None:
    with pytest.raises(ValidationError, match="256"):
        NormalizationSettingV1(scope="algorithm", name="profile", value="x" * 257)


def test_first_prepare_generates_an_opaque_anchor_and_reuses_it(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request()

    first = repository.prepare(request)
    second = NormalizationRunRepository(repository.subtitle_root).prepare(request)

    assert first.plan == second.plan
    assert re.fullmatch(r"norm_[0-9a-f]{32}", first.plan.external_anchor)
    assert request.source.sha256 not in first.plan.external_anchor
    assert request.request_key not in first.plan.external_anchor


def test_prepare_rejects_expected_request_drift(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())

    with pytest.raises(NormalizationRunIntegrityError, match="expected request"):
        repository.load(stored.plan.request_key, expected_request=_request(source_sha256=H1))


def test_legal_full_fsm_and_complete_proof_replay_in_fresh_process(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stored = _advance_to_receipt_ready(repository)
    proof = build_normalization_run_proof(
        stored,
        source_binding_method="upload_acknowledged",
    )
    complete = repository.append(stored.plan, state="complete", proof=proof)

    fresh = NormalizationRunRepository(repository.subtitle_root).load(
        stored.plan.request_key,
        expected_request=_request(),
    )

    assert complete.event.state == "complete"
    assert fresh.event == complete.event
    assert fresh.head == complete.head
    assert fresh.proof == proof
    assert fresh.permitted_actions == ()
    assert fresh.proof.precomplete_event_id == stored.event.id
    assert fresh.proof.precomplete_head_id == stored.head.id
    assert fresh.proof.production_uuid == "prod-12345678"
    assert fresh.proof.submitted_settings_hash == stored.plan.request.settings_hash

    process = _fresh_process_load(repository.subtitle_root, stored.plan.request_key)
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "complete"


@pytest.mark.parametrize(
    ("current", "illegal"),
    [
        ("prepared", "upload_in_flight"),
        ("create_in_flight", "upload_acknowledged"),
        ("production_bound", "start_in_flight"),
        ("upload_acknowledged", "provider_completed"),
    ],
)
def test_illegal_transition_is_rejected(tmp_path: Path, current: str, illegal: str) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())
    if current != "prepared":
        paths = {
            "create_in_flight": ("create_in_flight",),
            "production_bound": ("create_in_flight", "production_bound"),
            "upload_acknowledged": (
                "create_in_flight",
                "production_bound",
                "upload_in_flight",
                "upload_acknowledged",
            ),
        }
        for state in paths[current]:
            observation = None
            if state == "production_bound":
                observation = _observation("create")
            elif state == "upload_acknowledged":
                observation = _observation("upload")
            stored = repository.append(stored.plan, state=state, observation=observation)

    with pytest.raises(NormalizationRunIntegrityError, match="illegal transition"):
        repository.append(stored.plan, state=illegal)


def test_create_intent_is_unknown_after_reload_and_never_permits_create(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())
    repository.append(stored.plan, state="create_in_flight")

    fresh = NormalizationRunRepository(repository.subtitle_root).load(stored.plan.request_key)

    assert fresh.effect_outcome == "unknown"
    assert fresh.permitted_actions == ("reconcile_external_anchor",)
    assert "create_production" not in fresh.permitted_actions


def test_ambiguous_create_requires_exhaustive_unique_anchor_reconciliation(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())
    stored = repository.append(stored.plan, state="create_in_flight")
    stored = repository.append(stored.plan, state="ambiguous_create")

    with pytest.raises(NormalizationRunIntegrityError, match="reconciliation"):
        repository.append(
            stored.plan,
            state="production_bound",
            observation=_observation("create"),
        )

    invalid_reconciliations = (
        _reconciliation("norm_" + "f" * 32),
        _reconciliation(
            stored.plan.external_anchor,
            matches=("prod-87654321",),
        ),
        _reconciliation(
            stored.plan.external_anchor,
            matches=("prod-12345678", "prod-87654321"),
        ),
    )
    for invalid in invalid_reconciliations:
        with pytest.raises(NormalizationRunIntegrityError, match="anchor|unique"):
            repository.append(
                stored.plan,
                state="production_bound",
                observation=_observation("reconcile"),
                reconciliation=invalid,
            )

    reconciliation = _reconciliation(stored.plan.external_anchor)
    rebound = repository.append(
        stored.plan,
        state="production_bound",
        observation=_observation("reconcile"),
        reconciliation=reconciliation,
    )
    fresh = NormalizationRunRepository(repository.subtitle_root).load(stored.plan.request_key)

    assert rebound.event.reconciliation == reconciliation
    assert fresh.event.reconciliation == reconciliation
    assert reconciliation.matched_production_uuids == ("prod-12345678",)


def test_reconciliation_pages_must_prove_contiguous_scan_and_short_final_page() -> None:
    with pytest.raises(ValidationError, match="contiguous"):
        build_external_anchor_reconciliation(
            external_anchor="norm_" + "a" * 32,
            pages=(
                ExternalAnchorReconciliationPageV1(
                    offset=1,
                    limit=2,
                    result_count=1,
                    projection_hash=H1,
                ),
            ),
            matched_production_uuids=("prod-12345678",),
        )
    with pytest.raises(ValidationError, match="final.*short"):
        build_external_anchor_reconciliation(
            external_anchor="norm_" + "a" * 32,
            pages=(
                ExternalAnchorReconciliationPageV1(
                    offset=0,
                    limit=2,
                    result_count=2,
                    projection_hash=H1,
                ),
            ),
            matched_production_uuids=("prod-12345678",),
        )


def test_upload_intent_is_unknown_and_never_permits_upload(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())
    stored = repository.append(stored.plan, state="create_in_flight")
    stored = repository.append(
        stored.plan, state="production_bound", observation=_observation("create")
    )
    repository.append(stored.plan, state="upload_in_flight")

    fresh = NormalizationRunRepository(repository.subtitle_root).load(stored.plan.request_key)

    assert fresh.effect_outcome == "unknown"
    assert fresh.permitted_actions == ("inspect_bound_production",)
    assert "upload_audio" not in fresh.permitted_actions


def test_ambiguous_upload_requires_read_only_inspection_with_exact_source_checksum(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())
    stored = repository.append(stored.plan, state="create_in_flight")
    stored = repository.append(
        stored.plan, state="production_bound", observation=_observation("create")
    )
    stored = repository.append(stored.plan, state="upload_in_flight")
    stored = repository.append(stored.plan, state="ambiguous_upload")

    for observation in (
        _observation("upload"),
        _observation("inspect_upload"),
        _observation("inspect_upload", source_checksum=H1),
    ):
        with pytest.raises(NormalizationRunIntegrityError, match="inspect|checksum"):
            repository.append(
                stored.plan,
                state="upload_acknowledged",
                observation=observation,
            )

    acknowledged = repository.append(
        stored.plan,
        state="upload_acknowledged",
        observation=_observation("inspect_upload", source_checksum=H0),
    )
    assert acknowledged.event.observation is not None
    assert acknowledged.event.observation.operation == "inspect_upload"
    assert acknowledged.event.observation.source_checksum == stored.plan.request.source.sha256


def test_start_intent_is_unknown_and_only_permits_poll(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())
    stored = repository.append(stored.plan, state="create_in_flight")
    stored = repository.append(
        stored.plan, state="production_bound", observation=_observation("create")
    )
    stored = repository.append(stored.plan, state="upload_in_flight")
    stored = repository.append(
        stored.plan, state="upload_acknowledged", observation=_observation("upload")
    )
    repository.append(stored.plan, state="start_in_flight")

    fresh = NormalizationRunRepository(repository.subtitle_root).load(stored.plan.request_key)

    assert fresh.effect_outcome == "unknown"
    assert fresh.permitted_actions == ("poll_bound_production",)
    assert "start_production" not in fresh.permitted_actions


def test_download_get_is_repeat_safe_after_intent_reload(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stored = _advance_to_provider_completed(repository)
    stored = repository.append(stored.plan, state="download_in_flight")

    fresh = NormalizationRunRepository(repository.subtitle_root).load(stored.plan.request_key)

    assert fresh.permitted_actions == ("download_bound_output_to_temp_and_verify",)


def test_every_provider_observation_must_keep_one_production_uuid(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())
    stored = repository.append(stored.plan, state="create_in_flight")
    stored = repository.append(
        stored.plan, state="production_bound", observation=_observation("create")
    )
    stored = repository.append(stored.plan, state="upload_in_flight")

    with pytest.raises(NormalizationRunIntegrityError, match="production UUID"):
        repository.append(
            stored.plan,
            state="upload_acknowledged",
            observation=_observation("upload", production_uuid="prod-87654321"),
        )


@pytest.mark.parametrize(
    "terminal",
    [
        "complete",
        "multi_record_conflict",
        "provider_failed",
        "source_binding_unproven",
        "output_rejected",
        "alignment_rejected",
    ],
)
def test_terminal_state_is_immutable(tmp_path: Path, terminal: str) -> None:
    repository = _repository(tmp_path)
    if terminal == "complete":
        stored = _advance_to_receipt_ready(repository)
        proof = build_normalization_run_proof(
            stored,
            source_binding_method="upload_acknowledged",
        )
        stored = repository.append(stored.plan, state="complete", proof=proof)
    elif terminal == "provider_failed":
        stored = repository.prepare(_request())
        stored = repository.append(stored.plan, state="create_in_flight")
        observation = build_provider_observation(
            operation="create",
            outcome="failed",
            http_status_code=400,
            provider_status_code=None,
            production_uuid=None,
            submitted_settings_hash=None,
            source_checksum=None,
            output_contract_hash=None,
        )
        stored = repository.append(stored.plan, state=terminal, observation=observation)
    elif terminal == "multi_record_conflict":
        stored = repository.prepare(_request())
        stored = repository.append(stored.plan, state="create_in_flight")
        stored = repository.append(stored.plan, state="ambiguous_create")
        stored = repository.append(
            stored.plan,
            state=terminal,
            reconciliation=_reconciliation(
                stored.plan.external_anchor,
                matches=("prod-12345678", "prod-87654321"),
            ),
        )
    elif terminal == "source_binding_unproven":
        stored = repository.prepare(_request())
        stored = repository.append(stored.plan, state="create_in_flight")
        stored = repository.append(
            stored.plan,
            state="production_bound",
            observation=_observation("create"),
        )
        stored = repository.append(stored.plan, state="upload_in_flight")
        stored = repository.append(stored.plan, state=terminal)
    elif terminal == "output_rejected":
        stored = _advance_to_provider_completed(repository)
        stored = repository.append(stored.plan, state=terminal)
    else:
        stored = _advance_to_provider_completed(repository)
        stored = repository.append(stored.plan, state="download_in_flight")
        stored = repository.append(
            stored.plan,
            state="downloaded_verified",
            artifacts=(_binding("raw_audio", H5), _binding("raw_probe", H6)),
        )
        stored = repository.append(stored.plan, state="alignment_in_flight")
        stored = repository.append(stored.plan, state=terminal)

    with pytest.raises(NormalizationRunConflictError, match="terminal"):
        repository.append(stored.plan, state="create_in_flight")


def test_provider_status_is_an_exact_integer_and_status_string_is_forbidden() -> None:
    values = {
        "schema_version": 1,
        "id": "provider-observation-" + H0,
        "operation": "poll",
        "outcome": "acknowledged",
        "http_status_code": 200,
        "provider_status_code": 3,
        "production_uuid": "prod-12345678",
        "submitted_settings_hash": H1,
        "source_checksum": None,
        "output_contract_hash": None,
        "content_hash": H0,
    }
    for value in ("3", True, False, 3.0):
        with pytest.raises(ValidationError, match="exact integer"):
            ProviderObservationV1.model_validate({**values, "provider_status_code": value})
    with pytest.raises(ValidationError, match="status_string"):
        ProviderObservationV1.model_validate({**values, "status_string": "Done"})


@pytest.mark.parametrize(
    ("operation", "field", "value"),
    [
        ("create", "source_checksum", H0),
        ("upload", "source_checksum", H0),
        ("start", "output_contract_hash", H1),
        ("inspect_upload", "submitted_settings_hash", H2),
    ],
)
def test_provider_observation_hash_fields_are_scoped_to_read_only_evidence(
    operation: str,
    field: str,
    value: str,
) -> None:
    arguments = {
        "operation": operation,
        "outcome": "acknowledged",
        "http_status_code": 200,
        "provider_status_code": None,
        "production_uuid": "prod-12345678",
        "submitted_settings_hash": None,
        "source_checksum": None,
        "output_contract_hash": None,
        field: value,
    }
    with pytest.raises(ValidationError, match="only"):
        build_provider_observation(**arguments)


@pytest.mark.parametrize(
    "forbidden",
    ["api_key", "email", "signed_url", "raw_body", "path", "filename", "message"],
)
def test_provider_observation_forbids_sensitive_or_unbounded_fields(forbidden: str) -> None:
    with pytest.raises(ValidationError, match=forbidden):
        ProviderObservationV1.model_validate(
            {
                "schema_version": 1,
                "id": "provider-observation-" + H0,
                "operation": "poll",
                "outcome": "acknowledged",
                "http_status_code": 200,
                "provider_status_code": 3,
                "production_uuid": "prod-12345678",
                "submitted_settings_hash": H1,
                "source_checksum": None,
                "output_contract_hash": None,
                "content_hash": H0,
                forbidden: "secret-sentinel@example.invalid/path?token=secret",
            }
        )


def test_repository_bytes_do_not_contain_secret_sentinels(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.prepare(_request())
    payload = b"".join(
        path.read_bytes()
        for path in repository.root.rglob("*")
        if path.is_file() and not path.name.endswith(".lock")
    )

    assert b"@" not in payload
    assert b"api_key" not in payload
    assert b"signed_url" not in payload
    assert b"filename" not in payload
    assert b"E:\\" not in payload
    assert b"https://" not in payload


def test_two_repository_instances_serialize_one_prepare(tmp_path: Path) -> None:
    subtitle_root = tmp_path / ".subtitle-v2"
    request = _request()

    def prepare() -> str:
        return NormalizationRunRepository(subtitle_root).prepare(request).plan.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = tuple(executor.map(lambda _index: prepare(), range(2)))

    assert len(set(ids)) == 1
    assert len(tuple((subtitle_root / "normalization-runs" / "runs").iterdir())) == 1


def test_event_tamper_is_rejected_on_fresh_load(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())
    stored = repository.append(stored.plan, state="create_in_flight")
    event_path = stored.directory / "transitions" / "000001" / "event.json"
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    payload["effect_certainty"] = "acknowledged"
    event_path.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(NormalizationRunIntegrityError, match="event"):
        NormalizationRunRepository(repository.subtitle_root).load(stored.plan.request_key)
    process = _fresh_process_load(repository.subtitle_root, stored.plan.request_key)
    assert process.returncode != 0
    assert "NormalizationRunIntegrityError" in process.stderr


def test_head_tamper_is_rejected_on_fresh_load(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())
    stored = repository.append(stored.plan, state="create_in_flight")
    head_path = stored.directory / "transitions" / "000001" / "head.json"
    head_path.write_text("{}", encoding="utf-8")

    with pytest.raises(NormalizationRunIntegrityError, match="head"):
        NormalizationRunRepository(repository.subtitle_root).load(stored.plan.request_key)


def test_pointer_rollback_by_more_than_one_transition_is_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())
    pointer = (stored.directory / "active-head.json").read_bytes()
    stored = repository.append(stored.plan, state="create_in_flight")
    repository.append(
        stored.plan,
        state="production_bound",
        observation=_observation("create"),
    )
    (stored.directory / "active-head.json").write_bytes(pointer)

    with pytest.raises(NormalizationRunIntegrityError, match="rollback|tail"):
        NormalizationRunRepository(repository.subtitle_root).load(stored.plan.request_key)


def test_one_fully_published_tail_can_be_repaired_without_provider_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())

    def crash_before_pointer(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated local pointer crash")

    monkeypatch.setattr(repository, "_publish_active_pointer", crash_before_pointer)
    with pytest.raises(OSError, match="pointer crash"):
        repository.append(stored.plan, state="create_in_flight")

    fresh = NormalizationRunRepository(repository.subtitle_root)
    with pytest.raises(NormalizationRunIntegrityError, match="awaiting.*repair"):
        fresh.load(stored.plan.request_key)
    repaired = fresh.repair_published_tail(
        stored.plan.request_key,
        expected_request=_request(),
    )
    assert repaired.event.state == "create_in_flight"
    assert repaired.effect_outcome == "unknown"


def test_unknown_repository_topology_entry_is_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())
    (stored.directory / "unexpected.txt").write_text("x", encoding="utf-8")

    with pytest.raises(NormalizationRunIntegrityError, match="inventory"):
        repository.load(stored.plan.request_key)


def test_symbolic_link_repository_topology_is_rejected(tmp_path: Path) -> None:
    subtitle_root = tmp_path / ".subtitle-v2"
    subtitle_root.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    link = subtitle_root / "normalization-runs"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available on this platform")

    with pytest.raises(NormalizationRunIntegrityError, match="symbolic link|junction"):
        NormalizationRunRepository(subtitle_root).prepare(_request())


def test_symbolic_link_subtitle_root_is_rejected_before_resolution(tmp_path: Path) -> None:
    target = tmp_path / "outside-root"
    target.mkdir()
    subtitle_root = tmp_path / ".subtitle-v2"
    try:
        subtitle_root.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available on this platform")

    with pytest.raises(NormalizationRunIntegrityError, match="symbolic link|junction"):
        NormalizationRunRepository(subtitle_root).prepare(_request())


@pytest.mark.parametrize("owned_child", ["keys", "runs", "locks"])
def test_symbolic_link_owned_child_is_rejected(
    tmp_path: Path,
    owned_child: str,
) -> None:
    subtitle_root = tmp_path / ".subtitle-v2"
    repository_root = subtitle_root / "normalization-runs"
    repository_root.mkdir(parents=True)
    target = tmp_path / f"outside-{owned_child}"
    target.mkdir()
    try:
        (repository_root / owned_child).symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available on this platform")

    with pytest.raises(NormalizationRunIntegrityError, match="symbolic link|junction"):
        NormalizationRunRepository(subtitle_root).prepare(_request())


def test_proof_cannot_bind_a_different_receipt_or_incomplete_chain(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())
    with pytest.raises(NormalizationRunIntegrityError, match="receipt_ready"):
        build_normalization_run_proof(
            stored,
            source_binding_method="upload_acknowledged",
        )

    ready = _advance_to_receipt_ready(repository, _request())
    proof = build_normalization_run_proof(
        ready,
        source_binding_method="upload_acknowledged",
    )
    forged = proof.model_copy(update={"receipt_hash": H5})
    with pytest.raises((NormalizationRunIntegrityError, ValidationError), match="proof|receipt"):
        repository.append(ready.plan, state="complete", proof=forged)


def test_canonical_record_whitespace_tamper_is_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stored = repository.prepare(_request())
    path = stored.directory / "plan.json"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(NormalizationRunIntegrityError, match="canonical"):
        repository.load(stored.plan.request_key)


def test_request_key_is_derived_not_caller_selected() -> None:
    request = _request()
    payload = request.model_dump(mode="python")
    payload["request_key"] = hash_object({"attacker": True})
    with pytest.raises(ValidationError, match="request_key"):
        type(request).model_validate(payload)
