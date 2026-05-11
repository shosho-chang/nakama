"""Tests for `shared.brook_synthesize_store` (ADR-021 §4 / issue #454).

Round-trip read/write, lifecycle (create/exists/read), and the two mutate
helpers exercised by the Sunny `/api/projects/{slug}/synthesize` route.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared import brook_synthesize_store as store
from shared.brook_synthesize_store import (
    StoreAlreadyExistsError,
    StoreNotFoundError,
)
from shared.schemas.brook_synthesize import (
    BrookSynthesizeStore,
    EvidencePoolItem,
    OutlineSection,
    UserAction,
)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect the store to a per-test directory."""
    d = tmp_path / "brook_synthesize"
    monkeypatch.setenv("NAKAMA_BROOK_SYNTHESIZE_DIR", str(d))
    monkeypatch.delenv("NAKAMA_DATA_DIR", raising=False)
    return d


def _sample(slug: str = "creatine-cognitive") -> BrookSynthesizeStore:
    return BrookSynthesizeStore(
        project_slug=slug,
        topic="creatine and cognition",
        keywords=["creatine", "cognition", "肌酸"],
        evidence_pool=[
            EvidencePoolItem(slug="rae-2003", chunks=[{"id": "c1"}], hit_reason="dose-response"),
            EvidencePoolItem(slug="benton-2011", chunks=[], hit_reason="vegetarians"),
        ],
        outline_draft=[
            OutlineSection(
                section=1,
                heading="Why creatine for cognition",
                evidence_refs=["rae-2003"],
            ),
            OutlineSection(
                section=2,
                heading="Vegetarian responders",
                evidence_refs=["benton-2011"],
            ),
        ],
    )


def test_exists_false_for_unknown_slug():
    assert store.exists("nope") is False


def test_create_then_read_round_trip():
    s = _sample()
    written = store.create(s)
    # write() stamps updated_at
    assert written.updated_at != ""
    assert written.schema_version == 1

    loaded = store.read(s.project_slug)
    assert loaded.project_slug == s.project_slug
    assert loaded.topic == s.topic
    assert loaded.keywords == s.keywords
    assert len(loaded.evidence_pool) == 2
    assert loaded.evidence_pool[0].slug == "rae-2003"
    assert loaded.outline_draft[0].evidence_refs == ["rae-2003"]
    assert loaded.user_actions == []
    assert loaded.outline_final == []


def test_create_refuses_duplicate():
    s = _sample()
    store.create(s)
    with pytest.raises(StoreAlreadyExistsError):
        store.create(s)


def test_read_missing_raises():
    with pytest.raises(StoreNotFoundError):
        store.read("does-not-exist")


def test_append_user_action_appends_and_persists():
    s = _sample()
    store.create(s)
    action = UserAction(
        timestamp="2026-05-07T12:00:00+00:00",
        action="reject_from_section",
        section=2,
        evidence_slug="benton-2011",
    )
    updated = store.append_user_action(s.project_slug, action)
    assert len(updated.user_actions) == 1
    assert updated.user_actions[0].action == "reject_from_section"

    # second append builds on the first
    action2 = UserAction(
        timestamp="2026-05-07T12:01:00+00:00",
        action="reject_evidence_entirely",
        evidence_slug="rae-2003",
    )
    updated2 = store.append_user_action(s.project_slug, action2)
    assert len(updated2.user_actions) == 2

    # reload from disk to confirm persistence
    fresh = store.read(s.project_slug)
    assert [a.action for a in fresh.user_actions] == [
        "reject_from_section",
        "reject_evidence_entirely",
    ]


def test_append_user_action_missing_slug_raises():
    with pytest.raises(StoreNotFoundError):
        store.append_user_action(
            "ghost",
            UserAction(
                timestamp="2026-05-07T00:00:00+00:00",
                action="reject_from_section",
                section=1,
                evidence_slug="x",
            ),
        )


def test_update_outline_final_replaces():
    s = _sample()
    store.create(s)
    sections = [
        OutlineSection(section=1, heading="Final intro", evidence_refs=["rae-2003"]),
        OutlineSection(section=2, heading="Final body", evidence_refs=[]),
    ]
    updated = store.update_outline_final(s.project_slug, sections)
    assert len(updated.outline_final) == 2
    assert updated.outline_final[0].heading == "Final intro"

    # outline_draft untouched
    assert updated.outline_draft == s.outline_draft

    fresh = store.read(s.project_slug)
    assert [sec.heading for sec in fresh.outline_final] == ["Final intro", "Final body"]


def test_update_outline_final_missing_slug_raises():
    with pytest.raises(StoreNotFoundError):
        store.update_outline_final("ghost", [])


def test_invalid_slug_rejected():
    for bad in ["", ".", "..", "a/b", "a\\b"]:
        with pytest.raises(ValueError):
            store.store_path(bad)


def test_store_path_uses_env_override(tmp_path: Path, monkeypatch):
    target = tmp_path / "alt"
    monkeypatch.setenv("NAKAMA_BROOK_SYNTHESIZE_DIR", str(target))
    p = store.store_path("foo")
    assert p == target / "foo.json"


def test_store_path_uses_data_dir_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("NAKAMA_BROOK_SYNTHESIZE_DIR", raising=False)
    monkeypatch.setenv("NAKAMA_DATA_DIR", str(tmp_path))
    p = store.store_path("foo")
    assert p == tmp_path / "brook_synthesize" / "foo.json"
