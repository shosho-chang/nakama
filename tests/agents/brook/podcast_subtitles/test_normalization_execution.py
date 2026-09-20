"""Provider-free execution tests for durable Auphonic normalization."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.errors import ArtifactHashMismatchError
from agents.brook.podcast_subtitles.hashing import hash_file
from agents.brook.podcast_subtitles.normalization_execution import (
    AlignmentResultV1,
    AuphonicCreateResultV1,
    AuphonicCredentialV1,
    AuphonicMutationResultV1,
    AuphonicPollResultV1,
    AuphonicProductionPageV1,
    AuphonicProductionProjectionV1,
    AuphonicProviderFailure,
    AuphonicUploadInspectionV1,
    MediaProbeV1,
    NormalizationArtifactStore,
    NormalizationExecutor,
)
from agents.brook.podcast_subtitles.normalization_run import (
    NormalizationAdapterIdentityV1,
    NormalizationAlignmentPolicyV1,
    NormalizationOutputContractV1,
    NormalizationRunIntegrityError,
    NormalizationRunRepository,
    NormalizationSettingV1,
)
from agents.brook.podcast_subtitles.ports import (
    AdapterIntegrityError,
    AdapterUnavailableError,
    NormalizeRequest,
)
from agents.brook.podcast_subtitles.store import GenerationStore

T0 = datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(seconds=2)
T2 = T0 + timedelta(minutes=1)
UUID = "prod-12345678"
CRED = "cred_" + "a" * 32


class _Inspector:
    def probe(self, path: Path) -> MediaProbeV1:
        payload = path.read_bytes()
        if payload == b"SOURCE":
            duration = 10_000
        elif payload == b"OUTPUT":
            duration = 10_000
        else:
            raise AdapterIntegrityError("unrecognized test media")
        return MediaProbeV1(
            duration_ms=duration,
            container="wav",
            codec="pcm_s24le",
            bit_depth=24,
            audio_stream_count=1,
            channels=2,
            sample_rate_hz=48_000,
        )


class _IdentityAligner:
    def align(self, raw_audio: Path, source_audio: Path, *, policy):
        del source_audio, policy
        return AlignmentResultV1(
            output_path=raw_audio,
            method="identity",
            verified=True,
            source_origin_ms=0,
            normalized_origin_ms=0,
            drift_ms=0.0,
        )


class _FakeProvider:
    def __init__(self, repository: NormalizationRunRepository) -> None:
        self.repository = repository
        self.calls: list[str] = []
        self.anchor: str | None = None
        self.source_checksum: str | None = None
        self.settings_hash: str | None = None
        self.output_hash: str | None = None
        self.create_timeout_after_success = False
        self.upload_timeout_after_success = False
        self.start_timeout_after_success = False
        self.uploaded = False
        self.inspection_checksum_supported = True
        self.started = False
        self.reconcile_records: tuple[AuphonicProductionProjectionV1, ...] | None = None
        self.poll_result_override: AuphonicPollResultV1 | None = None
        self.download_bytes = b"OUTPUT"

    def _stored(self):
        keys = tuple(self.repository.keys_dir.glob("*.json"))
        assert len(keys) == 1
        return self.repository.load(keys[0].stem)

    def credentials(self, *, source_duration_ms: int):
        assert source_duration_ms == 10_000
        self.calls.append("credentials")
        return (AuphonicCredentialV1(CRED, object()),)

    def create(self, credential, *, external_anchor, settings, preset):
        del credential, settings, preset
        assert self._stored().event.state == "create_in_flight"
        self.calls.append("create")
        self.anchor = external_anchor
        request = self._stored().plan.request
        self.source_checksum = request.source.sha256
        self.settings_hash = request.settings_hash
        self.output_hash = request.output_contract.content_hash
        if self.create_timeout_after_success:
            self.create_timeout_after_success = False
            raise AuphonicProviderFailure("create", category="timeout")
        return AuphonicCreateResultV1(UUID, 201, T1)

    def reconcile_page(self, credential, *, offset, limit):
        del credential
        self.calls.append(f"reconcile:{offset}")
        records = self.reconcile_records
        if records is None:
            records = (AuphonicProductionProjectionV1(UUID, self.anchor or "", T1),)
        if offset:
            records = ()
        return AuphonicProductionPageV1(offset, limit, records)

    def upload(self, credential, *, production_uuid, source_audio):
        del credential
        assert self._stored().event.state == "upload_in_flight"
        assert production_uuid == UUID
        assert hash_file(source_audio) == self.source_checksum
        self.calls.append("upload")
        self.uploaded = True
        if self.upload_timeout_after_success:
            self.upload_timeout_after_success = False
            raise AuphonicProviderFailure("upload", category="timeout")
        return AuphonicMutationResultV1(UUID, 200)

    def inspect_upload(self, credential, *, production_uuid):
        del credential
        self.calls.append("inspect_upload")
        return AuphonicUploadInspectionV1(
            production_uuid,
            200,
            self.source_checksum if self.uploaded and self.inspection_checksum_supported else None,
        )

    def start(self, credential, *, production_uuid):
        del credential
        assert self._stored().event.state == "start_in_flight"
        self.calls.append("start")
        self.started = True
        if self.start_timeout_after_success:
            self.start_timeout_after_success = False
            raise AuphonicProviderFailure("start", category="timeout")
        return AuphonicMutationResultV1(production_uuid, 200, 1)

    def poll(self, credential, *, production_uuid):
        del credential
        self.calls.append("poll")
        if self.poll_result_override is not None:
            return self.poll_result_override
        return AuphonicPollResultV1(
            production_uuid=production_uuid,
            http_status_code=200,
            provider_status_code=3,
            submitted_settings_hash=self.settings_hash,
            source_checksum=self.source_checksum,
            output_contract_hash=self.output_hash,
            provider_created_at=T1,
            provider_completed_at=T2,
        )

    def download(self, credential, *, production_uuid, destination):
        del credential
        assert self._stored().event.state == "download_in_flight"
        assert production_uuid == UUID
        self.calls.append("download")
        destination.write_bytes(self.download_bytes)


def _executor(tmp_path: Path, *, provider: _FakeProvider | None = None):
    store = GenerationStore(tmp_path)
    repository = NormalizationRunRepository(store.root, clock=lambda: T0)
    provider = provider or _FakeProvider(repository)
    executor = NormalizationExecutor(
        generation_store=store,
        repository=repository,
        provider=provider,
        media_inspector=_Inspector(),
        aligner=_IdentityAligner(),
        effective_settings=(
            NormalizationSettingV1(scope="algorithm", name="denoise", value=True),
            NormalizationSettingV1(scope="output", name="bitdepth", value=24),
        ),
        preset=None,
        output_contract=NormalizationOutputContractV1(
            container="wav",
            codec="pcm_s24le",
            bit_depth=24,
            audio_stream_count=1,
            channel_policy="preserve",
            sample_rate_policy="preserve",
        ),
        alignment_policy=NormalizationAlignmentPolicyV1(
            algorithm="cross_correlation_v1",
            trim_jingle=True,
            requested_jingle_ms=6_000,
            minimum_head_correlation=0.5,
            minimum_mid_correlation=0.5,
            maximum_drift_ms=50,
        ),
        normalizer_identity=NormalizationAdapterIdentityV1(
            name="auphonic-normalizer-v2",
            version="1",
            config_hash="1" * 64,
            code_hash="2" * 64,
            runtime_hash="3" * 64,
        ),
        maximum_polls_per_call=3,
    )
    source = tmp_path / "source.wav"
    source.write_bytes(b"SOURCE")
    return executor, provider, repository, source


def test_complete_run_is_accepted_portable_and_replays_without_side_effects(
    tmp_path: Path,
) -> None:
    executor, provider, repository, source = _executor(tmp_path)

    first = executor.normalize(NormalizeRequest(source, expected_source_hash=hash_file(source)))
    first_receipt_bytes = first.receipt.model_dump_json().encode()
    before = tuple(provider.calls)
    provider.credentials = lambda **_kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("credential removed")
    )
    fresh_executor = _executor(tmp_path, provider=provider)[0]
    second = fresh_executor.normalize(
        NormalizeRequest(source, expected_source_hash=hash_file(source))
    )

    assert first.receipt.status == "accepted"
    assert first.receipt.request_started_at == T0
    assert first.receipt.provider_created_at == T1
    assert first.receipt.completed_at == T2
    assert first.receipt.alignment_method == "identity"
    assert first.receipt.source.uri.startswith("normalization-audio://sha256/")
    assert first.receipt.normalized.uri.startswith("normalization-audio://sha256/")
    assert tuple(provider.calls) == before
    assert second.receipt == first.receipt
    assert second.receipt.model_dump_json().encode() == first_receipt_bytes
    assert (
        repository.load(repository.keys_dir.glob("*.json").__next__().stem).event.state
        == "complete"
    )


@pytest.mark.parametrize(
    ("flag", "recovery_call", "nonrepeatable"),
    [
        ("create_timeout_after_success", "reconcile:0", "create"),
        ("upload_timeout_after_success", "inspect_upload", "upload"),
        ("start_timeout_after_success", "poll", "start"),
    ],
)
def test_lost_provider_ack_recovers_read_only_without_duplicate_side_effect(
    tmp_path: Path,
    flag: str,
    recovery_call: str,
    nonrepeatable: str,
) -> None:
    executor, provider, _repository, source = _executor(tmp_path)
    setattr(provider, flag, True)

    result = executor.normalize(NormalizeRequest(source))

    assert result.receipt.status == "accepted"
    assert provider.calls.count(nonrepeatable) == 1
    assert recovery_call in provider.calls


def test_empty_anchor_reconciliation_stays_ambiguous_and_never_recreates(tmp_path: Path) -> None:
    executor, provider, repository, source = _executor(tmp_path)
    provider.create_timeout_after_success = True
    provider.reconcile_records = ()

    with pytest.raises(AdapterUnavailableError, match="ambiguous"):
        executor.normalize(NormalizeRequest(source))
    with pytest.raises(AdapterUnavailableError, match="ambiguous"):
        executor.normalize(NormalizeRequest(source))

    assert provider.calls.count("create") == 1
    key = next(repository.keys_dir.glob("*.json")).stem
    stored = repository.load(key)
    assert stored.event.state == "ambiguous_create"
    assert stored.event.reconciliation is not None
    assert stored.event.reconciliation.matched_production_uuids == ()


def test_multiple_anchor_matches_are_terminal_and_never_recreate(tmp_path: Path) -> None:
    executor, provider, repository, source = _executor(tmp_path)
    provider.create_timeout_after_success = True

    def reconcile(credential, *, offset, limit):
        del credential
        provider.calls.append(f"reconcile:{offset}")
        records = (
            AuphonicProductionProjectionV1(UUID, provider.anchor or "", T1),
            AuphonicProductionProjectionV1("prod-87654321", provider.anchor or "", T1),
        )
        return AuphonicProductionPageV1(offset, limit, records if offset == 0 else ())

    provider.reconcile_page = reconcile  # type: ignore[method-assign]

    with pytest.raises(AdapterIntegrityError, match="terminal"):
        executor.normalize(NormalizeRequest(source))
    with pytest.raises(AdapterIntegrityError, match="terminal"):
        executor.normalize(NormalizeRequest(source))

    assert provider.calls.count("create") == 1
    key = next(repository.keys_dir.glob("*.json")).stem
    assert repository.load(key).event.state == "multi_record_conflict"


def test_full_reconciliation_page_fetches_a_short_terminal_page(tmp_path: Path) -> None:
    executor, provider, _repository, source = _executor(tmp_path)
    provider.create_timeout_after_success = True
    provider.reconcile_records = tuple(
        AuphonicProductionProjectionV1(
            UUID if index == 0 else f"other-{index:08d}",
            "anchor-pending",
            T1,
        )
        for index in range(100)
    )

    with pytest.raises(AdapterUnavailableError, match="ambiguous"):
        executor.normalize(NormalizeRequest(source))

    assert "reconcile:0" in provider.calls
    assert "reconcile:100" in provider.calls


def test_ambiguous_upload_without_checksum_is_terminal_unproven(tmp_path: Path) -> None:
    executor, provider, repository, source = _executor(tmp_path)
    provider.upload_timeout_after_success = True
    provider.inspection_checksum_supported = False

    with pytest.raises(AdapterIntegrityError, match="terminal"):
        executor.normalize(NormalizeRequest(source))

    key = next(repository.keys_dir.glob("*.json")).stem
    stored = repository.load(key)
    assert stored.event.state == "source_binding_unproven"
    assert stored.event.observation is not None
    assert stored.event.observation.operation == "inspect_upload"


@pytest.mark.parametrize("operation", ["create", "upload", "start", "poll"])
def test_non_2xx_provider_result_cannot_be_acknowledged(
    tmp_path: Path,
    operation: str,
) -> None:
    executor, provider, _repository, source = _executor(tmp_path)
    original = getattr(provider, operation)

    def invalid(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, http_status_code=500)

    setattr(provider, operation, invalid)

    with pytest.raises(AdapterIntegrityError, match="HTTP 2xx"):
        executor.normalize(NormalizeRequest(source))


@pytest.mark.parametrize("status", [-1, 4, 99])
def test_unknown_provider_status_is_rejected(tmp_path: Path, status: int) -> None:
    executor, provider, _repository, source = _executor(tmp_path)
    original = provider.poll

    def invalid(*args, **kwargs):
        return replace(original(*args, **kwargs), provider_status_code=status)

    provider.poll = invalid  # type: ignore[method-assign]

    with pytest.raises(AdapterIntegrityError, match="unknown status"):
        executor.normalize(NormalizeRequest(source))


@pytest.mark.parametrize("field", ["submitted_settings_hash", "output_contract_hash"])
def test_completed_provider_binding_mismatch_is_rejected(tmp_path: Path, field: str) -> None:
    executor, provider, repository, source = _executor(tmp_path)
    # Values become available after create; use a poll wrapper that tampers lazily.
    original = provider.poll

    def poll(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, **{field: "f" * 64})

    provider.poll = poll  # type: ignore[method-assign]

    with pytest.raises(AdapterIntegrityError, match="terminal"):
        executor.normalize(NormalizeRequest(source))

    key = next(repository.keys_dir.glob("*.json")).stem
    assert repository.load(key).event.state == "contract_rejected"
    assert repository.load(key).event.observation is not None
    assert repository.load(key).event.observation.outcome == "acknowledged"


def test_truncated_or_non_media_download_is_terminally_rejected(tmp_path: Path) -> None:
    executor, provider, repository, source = _executor(tmp_path)
    provider.download_bytes = b"TRUNCATED"

    with pytest.raises(AdapterIntegrityError, match="unrecognized test media"):
        executor.normalize(NormalizeRequest(source))

    key = next(repository.keys_dir.glob("*.json")).stem
    assert repository.load(key).event.state == "output_rejected"


def test_download_mutation_between_probe_and_cas_is_terminally_rejected(tmp_path: Path) -> None:
    executor, _provider, repository, source = _executor(tmp_path)

    class MutatingInspector(_Inspector):
        def probe(self, path: Path) -> MediaProbeV1:
            result = super().probe(path)
            if path.read_bytes() == b"OUTPUT" and "downloads" in path.parts:
                path.write_bytes(b"CHANGED")
            return result

    executor._inspector = MutatingInspector()

    with pytest.raises(AdapterIntegrityError):
        executor.normalize(NormalizeRequest(source))

    key = next(repository.keys_dir.glob("*.json")).stem
    assert repository.load(key).event.state == "output_rejected"


def test_alignment_failure_is_terminally_rejected(tmp_path: Path) -> None:
    executor, _provider, repository, source = _executor(tmp_path)

    class UnverifiedAligner:
        def align(self, raw_audio: Path, source_audio: Path, *, policy):
            del source_audio, policy
            return AlignmentResultV1(
                output_path=raw_audio,
                method="cross_correlation",
                verified=False,
                source_origin_ms=0,
                normalized_origin_ms=0,
                drift_ms=0.0,
                head_correlation=0.99,
                mid_correlation=0.99,
            )

    executor._aligner = UnverifiedAligner()

    with pytest.raises(AdapterIntegrityError, match="not verified"):
        executor.normalize(NormalizeRequest(source))

    key = next(repository.keys_dir.glob("*.json")).stem
    assert repository.load(key).event.state == "alignment_rejected"


def test_source_cas_mutation_during_upload_is_not_acknowledged(tmp_path: Path) -> None:
    executor, provider, repository, source = _executor(tmp_path)
    original = provider.upload

    def corrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        Path(kwargs["source_audio"]).write_bytes(b"CORRUPT")
        return result

    provider.upload = corrupt  # type: ignore[method-assign]

    with pytest.raises(ArtifactHashMismatchError):
        executor.normalize(NormalizeRequest(source))

    key = next(repository.keys_dir.glob("*.json")).stem
    assert repository.load(key).event.state == "upload_in_flight"
    assert provider.calls.count("upload") == 1


def test_active_run_keeps_exact_credential_when_order_and_availability_change(
    tmp_path: Path,
) -> None:
    executor, provider, _repository, source = _executor(tmp_path)
    alternate = "cred_" + "b" * 32
    first_pool = (
        AuphonicCredentialV1(CRED, "first", available_for_new_run=True, selection_rank=0),
        AuphonicCredentialV1(alternate, "second", available_for_new_run=True, selection_rank=1),
    )
    second_pool = (
        AuphonicCredentialV1(alternate, "second", available_for_new_run=True, selection_rank=0),
        AuphonicCredentialV1(CRED, "first", available_for_new_run=False, selection_rank=999),
    )
    pools = [first_pool, second_pool]

    def credentials(*, source_duration_ms):
        assert source_duration_ms == 10_000
        return pools.pop(0)

    provider.credentials = credentials  # type: ignore[method-assign]
    provider.create_timeout_after_success = True
    provider.reconcile_records = ()
    with pytest.raises(AdapterUnavailableError, match="ambiguous"):
        executor.normalize(NormalizeRequest(source))

    provider.reconcile_records = (AuphonicProductionProjectionV1(UUID, provider.anchor or "", T1),)
    result = executor.normalize(NormalizeRequest(source))

    assert result.receipt.status == "accepted"
    assert provider.calls.count("create") == 1


def test_zero_credentials_and_multiple_existing_runs_fail_closed(tmp_path: Path) -> None:
    executor, provider, repository, source = _executor(tmp_path)
    provider.credentials = lambda **_kwargs: ()  # type: ignore[method-assign]
    with pytest.raises(AdapterUnavailableError, match="no Auphonic credential"):
        executor.normalize(NormalizeRequest(source))

    # Prepare two credential-bound plans with the same recovery identity.
    stored_source = next(GenerationStore(tmp_path).audio_dir.glob("*"))
    probe_hash = next(
        path.name
        for path in NormalizationArtifactStore(tmp_path / ".subtitle-v2").blobs.glob("*")
        if path.is_file()
    )
    for credential_ref in (CRED, "cred_" + "b" * 32):
        repository.prepare(
            executor._request_for(
                source_sha256=hash_file(stored_source),
                source_size_bytes=stored_source.stat().st_size,
                source_probe_hash=probe_hash,
                credential_ref=credential_ref,
            )
        )
    with pytest.raises(AdapterIntegrityError, match="multiple credential-bound"):
        executor.normalize(NormalizeRequest(source))


def test_concurrent_identical_calls_execute_each_provider_side_effect_once(tmp_path: Path) -> None:
    executor, provider, _repository, source = _executor(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(executor.normalize, NormalizeRequest(source)) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]

    assert results[0].receipt == results[1].receipt
    assert provider.calls.count("create") == 1
    assert provider.calls.count("upload") == 1
    assert provider.calls.count("start") == 1


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_alignment_result_rejects_non_finite_measurements(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        AlignmentResultV1(
            output_path=Path("audio.wav"),
            method="cross_correlation",
            verified=True,
            source_origin_ms=0,
            normalized_origin_ms=0,
            drift_ms=value,
            head_correlation=0.9,
            mid_correlation=0.9,
        )


def test_artifact_publication_tail_is_recovered_exactly(tmp_path: Path) -> None:
    store = NormalizationArtifactStore(tmp_path / ".subtitle-v2")
    payload = b'{"probe":1}'
    digest = store.put_bytes(payload)
    final = store.blobs / digest.sha256
    tail = store.blobs / f".{digest.sha256}.0123456789abcdef.tmp"
    final.replace(tail)

    assert store.read(digest) == payload
    assert final.is_file()
    assert not tail.exists()


def test_artifact_store_rejects_unknown_or_linked_download_topology(tmp_path: Path) -> None:
    store = NormalizationArtifactStore(tmp_path / ".subtitle-v2")
    store.put_bytes(b"seed")
    (store.downloads / "unexpected.txt").write_text("x", encoding="utf-8")

    with pytest.raises(NormalizationRunIntegrityError, match="download tail"):
        store.put_bytes(b"other")


def test_provider_exception_text_is_never_persisted_or_rethrown(tmp_path: Path) -> None:
    executor, provider, repository, source = _executor(tmp_path)
    secret = "secret@example.com APIKEY signed://url"

    def create(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(secret)

    provider.create = create  # type: ignore[method-assign]

    with pytest.raises(AdapterUnavailableError) as caught:
        executor.normalize(NormalizeRequest(source))
    assert secret not in str(caught.value)
    persisted = b"".join(
        path.read_bytes() for path in repository.subtitle_root.rglob("*") if path.is_file()
    )
    assert secret.encode() not in persisted
