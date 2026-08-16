from __future__ import annotations

import json
import multiprocessing
import os
import threading
from unittest.mock import patch

import pytest

from agents.brook.podcast_subtitles.errors import (
    ArtifactHashMismatchError,
    GenerationConflictError,
    GenerationIsolationError,
)
from agents.brook.podcast_subtitles.hashing import (
    canonical_token_hash,
    hash_object,
    stage_cache_key,
)
from agents.brook.podcast_subtitles.store import GenerationStore
from shared.schemas.podcast_subtitles_v2 import CanonicalToken, canonical_content_hash


def _exit_while_holding_create_lease(episode_root: str) -> None:
    store = GenerationStore(episode_root)
    with store.create_execution_lease(timeout_seconds=5):
        os._exit(0)


def test_canonical_hash_ignores_mapping_insertion_order() -> None:
    assert hash_object({"b": 2, "a": [1, "安吉"]}) == hash_object(
        {"a": [1, "安吉"], "b": 2}
    )


def test_canonical_hash_rejects_mapping_key_coercion_collisions() -> None:
    with pytest.raises(TypeError, match="string keys"):
        hash_object({1: "integer-key"})
    with pytest.raises(TypeError, match="string keys"):
        hash_object({1: "lost", "1": "kept"})


def test_read_artifact_rehashes_the_exact_returned_bytes(tmp_path) -> None:
    store = GenerationStore(tmp_path)
    generation = store.commit(manifest={"stage": "test"}, artifacts={"value.txt": b"safe"})
    artifact_path = generation.directory / "value.txt"
    original_read_bytes = type(artifact_path).read_bytes
    def swap_after_load(path):
        if path == artifact_path:
            return b"tampered-after-load"
        return original_read_bytes(path)

    with patch.object(type(artifact_path), "read_bytes", swap_after_load):
        with pytest.raises(ArtifactHashMismatchError, match="changed after"):
            store.read_artifact(generation.generation_id, "value.txt")


def test_stage_cache_key_covers_policy_code_config_and_upstream() -> None:
    baseline = stage_cache_key(
        stage="projection",
        upstream_hashes=["canonical-a", "semantic-a"],
        policy_hash="policy-a",
        code_hash="code-a",
        config_hash="config-a",
    )
    variants = {
        stage_cache_key(
            stage="projection",
            upstream_hashes=["canonical-b", "semantic-a"],
            policy_hash="policy-a",
            code_hash="code-a",
            config_hash="config-a",
        ),
        stage_cache_key(
            stage="projection",
            upstream_hashes=["canonical-a", "semantic-a"],
            policy_hash="policy-b",
            code_hash="code-a",
            config_hash="config-a",
        ),
        stage_cache_key(
            stage="projection",
            upstream_hashes=["canonical-a", "semantic-a"],
            policy_hash="policy-a",
            code_hash="code-b",
            config_hash="config-a",
        ),
        stage_cache_key(
            stage="projection",
            upstream_hashes=["canonical-a", "semantic-a"],
            policy_hash="policy-a",
            code_hash="code-a",
            config_hash="config-b",
        ),
    }
    assert baseline not in variants
    assert len(variants) == 4


def test_canonical_token_hash_matches_shared_schema_truth_contract() -> None:
    tokens = (
        CanonicalToken(id="t1", text="學業", start_ms=0, end_ms=400, speaker="a"),
        CanonicalToken(id="t2", text="經歷", start_ms=400, end_ms=800, speaker="a"),
    )
    assert canonical_token_hash(tokens) == canonical_content_hash(tokens)


def test_generation_commit_is_content_addressed_and_idempotent(tmp_path) -> None:
    store = GenerationStore(tmp_path / "episode")
    first = store.commit(
        manifest={"episode_id": "anji", "ledger_head": "ledger-a"},
        artifacts={"canonical.json": {"tokens": ["學業經歷"]}},
    )
    second = store.commit(
        manifest={"ledger_head": "ledger-a", "episode_id": "anji"},
        artifacts={"canonical.json": {"tokens": ["學業經歷"]}},
    )

    assert first.generation_id == second.generation_id
    assert first.generation_id == first.artifact_set_hash
    assert first.directory == second.directory
    artifact = json.loads(
        store.read_artifact(first.generation_id, "canonical.json").decode("utf-8")
    )
    assert artifact == {"tokens": ["學業經歷"]}


def test_logical_generation_id_is_single_public_identity(tmp_path) -> None:
    store = GenerationStore(tmp_path / "episode")
    logical_id = f"generation-{'a' * 64}"

    stored = store.commit(
        logical_generation_id=logical_id,
        manifest={"episode_id": "anji", "generation_id": logical_id},
        artifacts={"canonical.json": {"generation_id": logical_id, "tokens": ["stable"]}},
    )

    assert stored.generation_id == logical_id
    assert stored.artifact_set_hash != logical_id
    assert stored.directory.name == logical_id
    store.set_active(logical_id)
    assert store.active_generation_id() == logical_id


def test_logical_generation_id_cannot_address_different_artifacts(tmp_path) -> None:
    store = GenerationStore(tmp_path / "episode")
    logical_id = f"generation-{'b' * 64}"
    manifest = {"episode_id": "anji", "generation_id": logical_id}
    store.commit(
        logical_generation_id=logical_id,
        manifest=manifest,
        artifacts={"canonical.json": {"tokens": ["first"]}},
    )

    with pytest.raises(GenerationConflictError):
        store.commit(
            logical_generation_id=logical_id,
            manifest=manifest,
            artifacts={"canonical.json": {"tokens": ["second"]}},
        )


def test_logical_generation_id_must_match_manifest(tmp_path) -> None:
    store = GenerationStore(tmp_path / "episode")

    with pytest.raises(GenerationIsolationError, match="manifest generation_id"):
        store.commit(
            logical_generation_id=f"generation-{'c' * 64}",
            manifest={"generation_id": f"generation-{'d' * 64}"},
            artifacts={"canonical.json": {"tokens": ["stable"]}},
        )


def test_generation_load_fails_after_artifact_tamper(tmp_path) -> None:
    store = GenerationStore(tmp_path / "episode")
    generation = store.commit(
        manifest={"episode_id": "anji"},
        artifacts={"canonical.json": {"tokens": ["心理健康有問題"]}},
    )
    (generation.directory / "canonical.json").write_text(
        json.dumps({"tokens": ["心理開始健康有問題"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ArtifactHashMismatchError):
        store.load(generation.generation_id)


def test_active_generation_prevents_cross_generation_read(tmp_path) -> None:
    store = GenerationStore(tmp_path / "episode")
    first = store.commit(
        manifest={"episode_id": "anji", "ledger_head": "a"},
        artifacts={"canonical.json": "第一代"},
    )
    second = store.commit(
        manifest={"episode_id": "anji", "ledger_head": "b"},
        artifacts={"canonical.json": "第二代"},
    )
    store.set_active(first.generation_id)

    assert store.read_artifact(
        first.generation_id, "canonical.json", require_active=True
    ) == "第一代".encode()
    with pytest.raises(GenerationIsolationError):
        store.read_artifact(second.generation_id, "canonical.json", require_active=True)


def test_active_pointer_compare_and_swap_rejects_stale_parent(tmp_path) -> None:
    store = GenerationStore(tmp_path / "episode")
    first = store.commit(manifest={"stage": "first"}, artifacts={"value": "first"})
    second = store.commit(manifest={"stage": "second"}, artifacts={"value": "second"})
    store.set_active(first.generation_id)

    with store.episode_transaction():
        with pytest.raises(GenerationIsolationError, match="changed"):
            store.set_active_cas_locked(
                second.generation_id,
                expected_generation_id="0" * 64,
            )

    assert store.active_generation_id() == first.generation_id


def test_active_pointer_cas_is_idempotent_when_target_is_already_active(
    tmp_path,
) -> None:
    store = GenerationStore(tmp_path / "episode")
    target = store.commit(manifest={"stage": "target"}, artifacts={"value": "target"})
    store.set_active(target.generation_id)

    with store.episode_transaction():
        store.set_active_cas_locked(
            target.generation_id,
            expected_generation_id=None,
        )

    assert store.active_generation_id() == target.generation_id


def test_create_execution_lease_rejects_a_concurrent_live_owner(tmp_path) -> None:
    owner = GenerationStore(tmp_path / "episode")
    contender = GenerationStore(tmp_path / "episode")
    entered = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with owner.create_execution_lease(timeout_seconds=1):
            entered.set()
            assert release.wait(timeout=5)

    thread = threading.Thread(target=hold)
    thread.start()
    assert entered.wait(timeout=2)
    try:
        with pytest.raises(GenerationIsolationError, match="create execution lease"):
            with contender.create_execution_lease(timeout_seconds=0.01):
                raise AssertionError("contender entered paid execution")
    finally:
        release.set()
        thread.join(timeout=5)


def test_create_execution_lease_is_recoverable_after_owner_exit(tmp_path) -> None:
    first = GenerationStore(tmp_path / "episode")
    second = GenerationStore(tmp_path / "episode")

    with first.create_execution_lease(timeout_seconds=1):
        pass
    with second.create_execution_lease(timeout_seconds=1):
        acquired = True

    assert acquired


def test_create_execution_lease_recovers_after_owner_process_dies(tmp_path) -> None:
    episode_root = tmp_path / "episode"
    context = multiprocessing.get_context("spawn")
    owner = context.Process(
        target=_exit_while_holding_create_lease,
        args=(str(episode_root),),
    )
    owner.start()
    owner.join(timeout=15)
    assert owner.exitcode == 0

    with GenerationStore(episode_root).create_execution_lease(timeout_seconds=1):
        acquired = True

    assert acquired
