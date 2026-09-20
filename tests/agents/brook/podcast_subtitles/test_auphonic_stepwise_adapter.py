"""Provider-free contract tests for the production Auphonic step adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.skip(
    "Auphonic is an upstream, unchanged boundary of Memo-first Subtitle V2",
    allow_module_level=True,
)

from agents.brook.podcast_subtitles.adapters.auphonic_stepwise import (
    SharedAuphonicStepwiseProvider,
    effective_normalization_policy,
    effective_normalization_settings,
    normalizer_identity,
)
from agents.brook.podcast_subtitles.hashing import hash_object
from agents.brook.podcast_subtitles.normalization_execution import AuphonicCredentialV1
from agents.brook.podcast_subtitles.normalization_run import NormalizationOutputContractV1
from shared import auphonic
from shared.schemas.podcast_subtitles_v2 import (
    NormalizationParameter,
    normalization_settings_hash,
)

T1 = datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc)
UUID = "prod-12345678"


def _clear_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(1, 6):
        monkeypatch.delenv(f"AUPHONIC_ACCOUNT_{index}", raising=False)


def test_effective_policy_freezes_exact_payload_and_alignment() -> None:
    policy = effective_normalization_policy(
        {
            "output_format": "wav",
            "output_bitdepth": 24,
            "trim_jingle": True,
            "jingle_seconds": 7.25,
        }
    )

    assert policy.alignment_policy.requested_jingle_ms == 7_250
    assert policy.output_contract.codec == "pcm_s24le"
    assert tuple((item.scope, item.name) for item in policy.settings) == tuple(
        sorted((item.scope, item.name) for item in policy.settings)
    )
    assert ("output", "format", "wav-24bit") in tuple(
        (item.scope, item.name, item.value) for item in policy.settings
    )
    assert not any(item.scope == "output" and item.name == "bitdepth" for item in policy.settings)


@pytest.mark.parametrize(
    "overrides",
    [
        {"output_format": "mp3"},
        {"output_bitdepth": 16},
        {"trim_jingle": False},
        {"jingle_seconds": 121.0},
    ],
)
def test_effective_policy_rejects_unverifiable_output_or_alignment(overrides) -> None:
    with pytest.raises(ValueError):
        effective_normalization_policy(overrides)


def test_credential_preflight_is_credit_aware_opaque_and_secret_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_accounts(monkeypatch)
    email = "private@example.test"
    api_key = "TOP-SECRET-API-KEY"
    monkeypatch.setenv("AUPHONIC_ACCOUNT_1", f"{email},{api_key}")

    observed: list[str] = []

    def credit(api_key: str):
        observed.append(api_key)
        return {"credits": 0.1, "recharge_date": "2026-08-20T00:00:00Z"}

    monkeypatch.setattr(auphonic, "query_user_credit_step", credit)

    credentials = SharedAuphonicStepwiseProvider().credentials(source_duration_ms=30_000)

    assert len(credentials) == 1
    assert observed == [api_key]
    assert credentials[0].available_for_new_run is True
    assert credentials[0].credential_ref.startswith("cred_")
    assert email not in credentials[0].credential_ref
    assert email not in repr(credentials[0])
    assert api_key not in repr(credentials[0])


def test_credential_preflight_rejects_insufficient_or_unobservable_credit_and_ranks_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_accounts(monkeypatch)
    monkeypatch.setenv("AUPHONIC_ACCOUNT_1", "late@example.test,LATE")
    monkeypatch.setenv("AUPHONIC_ACCOUNT_2", "empty@example.test,EMPTY")
    monkeypatch.setenv("AUPHONIC_ACCOUNT_3", "early@example.test,EARLY")
    monkeypatch.setenv("AUPHONIC_ACCOUNT_4", "down@example.test,DOWN")

    def credit(api_key: str):
        values = {
            "LATE": {"credits": 1.0, "recharge_date": "2026-09-20T00:00:00Z"},
            "EMPTY": {"credits": 0.01, "recharge_date": "2026-08-15T00:00:00Z"},
            "EARLY": {"credits": 1.0, "recharge_date": "2026-08-20T00:00:00Z"},
        }
        if api_key == "DOWN":
            raise TimeoutError("secret-bearing provider failure")
        return values[api_key]

    monkeypatch.setattr(auphonic, "query_user_credit_step", credit)

    credentials = SharedAuphonicStepwiseProvider().credentials(source_duration_ms=30 * 60 * 1000)
    by_key = {item.handle.api_key: item for item in credentials}

    assert by_key["EARLY"].available_for_new_run is True
    assert by_key["LATE"].available_for_new_run is True
    assert by_key["EARLY"].selection_rank < by_key["LATE"].selection_rank
    assert by_key["EMPTY"].available_for_new_run is False
    assert by_key["DOWN"].available_for_new_run is False


def test_create_submits_exact_frozen_payload_and_opaque_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = SharedAuphonicStepwiseProvider()
    credential = AuphonicCredentialV1(
        "cred_" + "a" * 32,
        auphonic.AuphonicAccount("private@example.test", "TOP-SECRET"),
    )
    settings = effective_normalization_settings({"output_format": "wav", "output_bitdepth": 24})
    anchor = "norm_" + "b" * 32
    captured: dict[str, object] = {}

    def create(api_key, *, submitted_payload, external_anchor):
        captured.update(
            api_key=api_key,
            submitted_payload=submitted_payload,
            external_anchor=external_anchor,
        )
        return UUID, 201

    monkeypatch.setattr(auphonic, "create_production_payload_step", create)
    monkeypatch.setattr(
        auphonic,
        "inspect_production_step",
        lambda api_key, uuid: (
            {"uuid": uuid, "status": 0, "creation_time": T1.isoformat()},
            200,
        ),
    )

    result = provider.create(
        credential,
        external_anchor=anchor,
        settings=settings,
        preset=None,
    )

    assert result.production_uuid == UUID
    assert result.provider_created_at == T1
    assert captured["external_anchor"] == anchor
    assert captured["api_key"] == "TOP-SECRET"
    expected = {
        "algorithms": {item.name: item.value for item in settings if item.scope == "algorithm"},
        "output_files": [{item.name: item.value for item in settings if item.scope == "output"}],
    }
    assert expected["output_files"] == [{"format": "wav-24bit"}]
    assert captured["submitted_payload"] == expected


def test_poll_projects_exact_bindings_and_keeps_signed_url_in_memory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = SharedAuphonicStepwiseProvider()
    credential = AuphonicCredentialV1(
        "cred_" + "a" * 32,
        auphonic.AuphonicAccount("private@example.test", "TOP-SECRET"),
    )
    settings = effective_normalization_settings({"output_format": "wav", "output_bitdepth": 24})
    algorithms = {item.name: item.value for item in settings if item.scope == "algorithm"}
    output = {item.name: item.value for item in settings if item.scope == "output"}
    signed_url = "https://signed.example.test/secret-token"
    monkeypatch.setattr(
        auphonic,
        "inspect_production_step",
        lambda api_key, uuid: (
            {
                "uuid": uuid,
                "status": 3,
                "creation_time": T1.isoformat(),
                "completion_time": T1.isoformat(),
                "algorithms": algorithms,
                "output_file": {**output, "download_url": signed_url},
                "download_url": signed_url,
            },
            200,
        ),
    )

    result = provider.poll(credential, production_uuid=UUID)

    expected_settings_hash = normalization_settings_hash(
        tuple(
            NormalizationParameter(scope=item.scope, name=item.name, value=item.value)
            for item in settings
        ),
        preset=None,
    )
    expected_output_hash = NormalizationOutputContractV1(
        container="wav",
        codec="pcm_s24le",
        bit_depth=24,
        audio_stream_count=1,
        channel_policy="preserve",
        sample_rate_policy="preserve",
    ).content_hash
    assert result.submitted_settings_hash == expected_settings_hash
    assert result.output_contract_hash == expected_output_hash
    assert result.source_checksum is None
    assert signed_url not in repr(result)

    captured: dict[str, object] = {}

    def download(api_key, url, destination):
        captured.update(api_key=api_key, url=url)
        destination.write_bytes(b"OUTPUT")
        return 200

    monkeypatch.setattr(auphonic, "download_production_step", download)
    destination = tmp_path / "output.wav"
    provider.download(credential, production_uuid=UUID, destination=destination)
    assert destination.read_bytes() == b"OUTPUT"
    assert captured == {"api_key": "TOP-SECRET", "url": signed_url}


def test_provider_md5_is_never_mislabeled_as_exact_source_sha256(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = SharedAuphonicStepwiseProvider()
    credential = AuphonicCredentialV1(
        "cred_" + "a" * 32,
        auphonic.AuphonicAccount("private@example.test", "TOP-SECRET"),
    )
    monkeypatch.setattr(
        auphonic,
        "inspect_production_step",
        lambda _api_key, uuid: (
            {
                "uuid": uuid,
                "status": 0,
                "source_checksum": "1bcb74117e73d00dc469d4364625468e",
            },
            200,
        ),
    )

    result = provider.inspect_upload(credential, production_uuid=UUID)

    assert result.source_checksum is None


def test_setting_identity_changes_when_exact_provider_value_changes() -> None:
    first = effective_normalization_settings({"denoise_amount": 0})
    second = effective_normalization_settings({"denoise_amount": 1})

    assert hash_object(first) != hash_object(second)


def test_normalizer_identity_changes_when_exact_tool_binary_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"ffmpeg-build-one")
    ffprobe.write_bytes(b"ffprobe-build")
    monkeypatch.setenv("PODCAST_SUBTITLE_V2_FFMPEG", str(ffmpeg))
    monkeypatch.setenv("PODCAST_SUBTITLE_V2_FFPROBE", str(ffprobe))
    monkeypatch.setattr(
        "agents.brook.podcast_subtitles.adapters.auphonic_stepwise.subprocess.run",
        lambda *_args, **_kwargs: type(
            "Completed", (), {"returncode": 0, "stdout": "tool exact-version\n", "stderr": ""}
        )(),
    )
    settings = effective_normalization_settings()

    first = normalizer_identity(settings)
    ffmpeg.write_bytes(b"ffmpeg-build-two")
    second = normalizer_identity(settings)

    assert first.runtime_hash != second.runtime_hash
