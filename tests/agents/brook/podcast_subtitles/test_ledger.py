from __future__ import annotations

import json
import multiprocessing
from datetime import datetime, timezone

import pytest

from agents.brook.podcast_subtitles.errors import (
    LedgerConflictError,
    LedgerIntegrityError,
    StaleFingerprintError,
)
from agents.brook.podcast_subtitles.ledger import GENESIS_HASH, CorrectionLedger
from shared.schemas.podcast_subtitles_v2 import CorrectionDecision


def _append_in_process(path: str, decision_id: str, gate) -> None:
    gate.wait()
    CorrectionLedger(path).append(
        _decision(decision_id=decision_id),
        current_fingerprint="fingerprint-a",
    )


def _decision(
    *,
    decision_id: str = "decision-001",
    fingerprint: str = "fingerprint-a",
    replacement: str = "數位遊牧",
) -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "audio_span_id": "audiohash:1200-1800",
        "target_fingerprint": fingerprint,
        "action": "replace",
        "replacement_lexemes": replacement,
    }


def test_identical_decision_retry_is_idempotent(tmp_path) -> None:
    ledger = CorrectionLedger(tmp_path / "events.ndjson")
    first = ledger.append(_decision(), current_fingerprint="fingerprint-a")
    second = ledger.append(_decision(), current_fingerprint="fingerprint-now-changed")

    assert first == second
    assert len(ledger.entries()) == 1
    assert ledger.head_hash == first.entry_hash
    assert first.previous_hash == GENESIS_HASH


def test_decision_id_cannot_be_reused_with_different_content(tmp_path) -> None:
    ledger = CorrectionLedger(tmp_path / "events.ndjson")
    ledger.append(_decision(), current_fingerprint="fingerprint-a")

    with pytest.raises(LedgerConflictError):
        ledger.append(
            _decision(replacement="蘇味遊牧"),
            current_fingerprint="fingerprint-a",
        )


def test_new_decision_against_stale_fingerprint_is_rejected(tmp_path) -> None:
    ledger = CorrectionLedger(tmp_path / "events.ndjson")

    with pytest.raises(StaleFingerprintError):
        ledger.append(_decision(), current_fingerprint="fingerprint-b")
    assert ledger.entries() == ()


def test_hash_chain_tamper_is_detected(tmp_path) -> None:
    path = tmp_path / "events.ndjson"
    ledger = CorrectionLedger(path)
    ledger.append(_decision(), current_fingerprint="fingerprint-a")
    ledger.append(
        _decision(
            decision_id="decision-002",
            fingerprint="fingerprint-b",
            replacement="畢業典禮",
        ),
        current_fingerprint="fingerprint-b",
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["decision"]["replacement_lexemes"] = "哥大 BA 典禮"
    lines[0] = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError):
        ledger.entries()


def test_typed_schema_decision_appends_by_event_and_evidence_fingerprint(tmp_path) -> None:
    ledger = CorrectionLedger(tmp_path / "events.ndjson")
    decision = CorrectionDecision(
        event_id="event-typed-001",
        episode_id="anji",
        generation_id="generation-a",
        target_span_ids=("span-a",),
        target_start_ms=1200,
        target_end_ms=1800,
        evidence_fingerprint="a" * 64,
        issue_ids=("issue-a",),
        audio_evidence_ids=("ev-a",),
        action="replace",
        replacement_text="數位遊牧",
        replacement_lexemes=("數位", "遊牧"),
        actor_kind="human",
        actor="修修",
        rationale="audio relisten",
        timestamp=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )

    entry = ledger.append(decision, current_fingerprint="a" * 64)

    assert entry.decision["event_id"] == "event-typed-001"
    assert ledger.append(decision, current_fingerprint="b" * 64) == entry


def test_concurrent_processes_append_one_valid_hash_chain(tmp_path) -> None:
    path = tmp_path / "events.ndjson"
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    processes = [
        context.Process(
            target=_append_in_process,
            args=(str(path), f"decision-{index}", gate),
        )
        for index in range(4)
    ]
    for process in processes:
        process.start()
    gate.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    entries = CorrectionLedger(path).entries()
    assert len(entries) == 4
    assert [entry.sequence for entry in entries] == [1, 2, 3, 4]
    assert len({entry.decision["decision_id"] for entry in entries}) == 4


def test_prepared_entry_cannot_append_after_head_changes(tmp_path) -> None:
    ledger = CorrectionLedger(tmp_path / "events.ndjson")
    prepared = ledger.prepare(
        _decision(decision_id="prepared-a"),
        current_fingerprint="fingerprint-a",
        expected_head=GENESIS_HASH,
    )
    winner = ledger.append(
        _decision(decision_id="winner-b"),
        current_fingerprint="fingerprint-a",
    )

    with pytest.raises(LedgerConflictError, match="no longer follows"):
        ledger.append_prepared(prepared)

    assert ledger.entries() == (winner,)
