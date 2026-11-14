from __future__ import annotations

import pytest

from agents.brook.podcast_subtitles.hashing import (
    canonical_token_hash,
    hash_object,
    stage_cache_key,
)
from shared.schemas.podcast_subtitles_v2 import CanonicalToken, canonical_content_hash


def test_canonical_hash_ignores_mapping_insertion_order() -> None:
    assert hash_object({"b": 2, "a": [1, "安吉"]}) == hash_object({"a": [1, "安吉"], "b": 2})


def test_canonical_hash_rejects_mapping_key_coercion_collisions() -> None:
    with pytest.raises(TypeError, match="string keys"):
        hash_object({1: "integer-key"})
    with pytest.raises(TypeError, match="string keys"):
        hash_object({1: "lost", "1": "kept"})


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
