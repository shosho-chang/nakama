"""The amendment journal must keep the current Release re-derivable from git alone.

These tests deliberately read the real, committed journal.  Its whole purpose is
to be the version-controlled record of how the current L04 Release was reached,
so a journal that drifts from the pinned operations — or that stops describing a
closed, contiguous chain — is a production-provenance failure, not a fixture nit.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production.amendments import (
    JOURNAL_SCHEMA,
    AmendmentJournalError,
    load_journal,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
AMENDMENTS = (
    REPO_ROOT / "agents" / "brook" / "script_video" / "finished_cut_production" / "amendments"
)
JOURNAL = AMENDMENTS / "journal" / "20260805-lin-zhi-chen.json"

L04_CUT = "long3-fresh-20260828-r4"
CURRENT_RELEASE = "release-af65a1d7a2ac611eb78be493"


def _document() -> dict:
    return json.loads(JOURNAL.read_text(encoding="utf-8"))


def _written(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "journal.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def test_committed_journal_describes_a_closed_chain_ending_at_current() -> None:
    journal = load_journal(JOURNAL)

    assert journal.episode_id == "20260805 林之晨"
    assert journal.current_release_id == CURRENT_RELEASE
    assert journal.current_manifest_id == "manifest-135e2060e0d612e2499658a1"
    chain = journal.chain_for(L04_CUT)
    assert len(chain) == 2
    assert chain[0].result == chain[1].base
    assert chain[-1].result.release_id == CURRENT_RELEASE


def test_every_amendment_carries_the_base_acceptance_chain_untouched() -> None:
    # A mechanical amendment that minted a new acceptance would be a semantic
    # revision wearing an amendment's clothes.
    chain = load_journal(JOURNAL).chain_for(L04_CUT)

    authorities = {item.semantic_authority_unchanged for item in chain}
    assert len(authorities) == 1
    authority = authorities.pop()
    assert authority.run_id == "run-53b53c6bc22843f58607d85252d0fc96"
    assert authority.command_id == "approved-cut:eece07836239a532e995e657cd25b8ca"
    assert authority.director_acceptance_id == "acceptance-8e7b7e29890648fab8e90a8abd2b42c4"
    assert authority.dp_acceptance_id == "acceptance-630125cf56fb4ca9a53196bfff6f57b4"
    assert authority.visual_acceptance_id == "acceptance-321ecb4f61fd4aba81a029e74a189b67"


def test_recorded_transforms_match_the_l04_amendments() -> None:
    first, second = load_journal(JOURNAL).chain_for(L04_CUT)

    assert first.kind == "suppress_components"
    assert first.base.component_count == 20
    assert first.result.component_count == 15
    assert len(first.operation["target_event_ids"]) == 5
    assert first.operation["retired_from_semantic_kind"] == ["supporting_title"]
    assert first.operation["result_event_projection"] == "intentional_aroll"

    assert second.kind == "replace_component_assets"
    assert second.base.component_count == second.result.component_count == 15
    assert second.operation["lanes"] == ["fullscreen_transition"]
    assert len(second.operation["asset_ref_after"]) == 5
    assert set(second.operation["asset_ref_before"]).isdisjoint(
        second.operation["asset_ref_after"].values()
    )


def test_event_cardinality_is_preserved_across_every_amendment() -> None:
    # Suppression retires a component but keeps its event as intentional A-roll;
    # losing an event would mean the cut's semantics changed.
    for item in load_journal(JOURNAL).chain_for(L04_CUT):
        assert item.base.event_count == item.result.event_count == 20


def test_pinned_reference_operations_have_not_drifted() -> None:
    for item in load_journal(JOURNAL).amendments:
        item.reference_operation.verify(REPO_ROOT)
        assert (REPO_ROOT / item.reference_operation.path).is_file()


def test_reference_operation_digest_change_fails_closed(tmp_path: Path) -> None:
    journal = load_journal(JOURNAL)
    drifted = journal.amendments[0].reference_operation
    object.__setattr__(drifted, "sha256", "0" * 64)

    with pytest.raises(AmendmentJournalError, match="digest differs"):
        drifted.verify(REPO_ROOT)


def test_unknown_schema_is_rejected(tmp_path: Path) -> None:
    document = _document()
    document["schema"] = "nakama.finished_cut_amendment_journal.v2"

    with pytest.raises(AmendmentJournalError, match="schema is unsupported"):
        load_journal(_written(tmp_path, document))

    assert JOURNAL_SCHEMA == "nakama.finished_cut_amendment_journal.v1"


def test_broken_chain_is_rejected(tmp_path: Path) -> None:
    document = _document()
    document["amendments"][1]["base"]["release_id"] = "release-not-the-previous-result"

    with pytest.raises(AmendmentJournalError, match="does not equal the previous result"):
        load_journal(_written(tmp_path, document))


def test_chain_that_stops_short_of_current_is_rejected(tmp_path: Path) -> None:
    document = _document()
    document["amendments"] = document["amendments"][:1]

    with pytest.raises(AmendmentJournalError, match="not the current Release"):
        load_journal(_written(tmp_path, document))


def test_amendment_that_changes_the_acceptance_chain_is_rejected(tmp_path: Path) -> None:
    # A forged acceptance would let a semantic revision be recorded as a
    # mechanical amendment, so the journal has to fail closed on it.
    document = _document()
    document["amendments"][1]["semantic_authority_unchanged"]["dp_acceptance_id"] = (
        "acceptance-forged"
    )

    with pytest.raises(AmendmentJournalError, match="changes the acceptance chain"):
        load_journal(_written(tmp_path, document))


def test_amendment_without_a_new_plan_is_rejected(tmp_path: Path) -> None:
    document = _document()
    first = document["amendments"][0]
    first["result"]["materialization_plan_id"] = first["base"]["materialization_plan_id"]

    with pytest.raises(AmendmentJournalError, match="did not replace the materialization plan"):
        load_journal(_written(tmp_path, document))


def test_unsupported_transform_kind_is_rejected(tmp_path: Path) -> None:
    document = _document()
    document["amendments"][0]["operation"]["kind"] = "rewrite_director_intent"

    with pytest.raises(AmendmentJournalError, match="kind is unsupported"):
        load_journal(_written(tmp_path, document))


def test_event_count_change_is_rejected(tmp_path: Path) -> None:
    document = deepcopy(_document())
    document["amendments"][0]["result"]["event_count"] = 19

    with pytest.raises(AmendmentJournalError, match="cannot change event count"):
        load_journal(_written(tmp_path, document))
