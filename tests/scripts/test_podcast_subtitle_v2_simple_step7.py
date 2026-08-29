from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.podcast_subtitle_v2_simple_step7 import (
    _ARBITRATION_CONTRACT,
    _ARBITRATION_MIGRATED_FROM_SHA256,
    _ARBITRATION_MIGRATION_REASON,
    _ARBITRATION_QUEUE_V2_SHA256,
    SimpleStep7Error,
    _parse_srt,
    _parser,
    apply_arbitration,
    main,
    merge_simple_step7,
)


def test_cli_help_distinguishes_official_production_from_legacy_degraded() -> None:
    help_text = _parser().format_help()
    assert "Deterministic Memo Dual-Audit text consensus" in help_text
    assert "merge-official" in help_text
    assert "production: build the generic Memo Dual-Audit" in help_text
    assert "historical degraded-only audit merge" in help_text
    assert "historical degraded-only Arbitration C replay" in help_text


def _srt(cue_count: int = 2630) -> bytes:
    def stamp(value: int) -> str:
        return (
            f"{value // 3_600_000:02d}:{value // 60_000 % 60:02d}:"
            f"{value // 1000 % 60:02d},{value % 1000:03d}"
        )

    blocks = []
    for number in range(1, cue_count + 1):
        start = number * 2000
        end = start + 1000
        blocks.append(f"{number}\n{stamp(start)} --> {stamp(end)}\n原文{number}")
    return ("\n\n".join(blocks) + "\n").encode()


def _record(
    cue_numbers: list[int],
    *,
    proposed: str | None,
    category: str = "term",
    confidence: object = "high",
) -> dict[str, object]:
    first, last = cue_numbers[0], cue_numbers[-1]
    start = first * 2000
    end = last * 2000 + 1000

    def stamp(value: int) -> str:
        return (
            f"{value // 3_600_000:02d}:{value // 60_000 % 60:02d}:"
            f"{value // 1000 % 60:02d},{value % 1000:03d}"
        )

    return {
        "cue_numbers": cue_numbers,
        "start": stamp(start),
        "end": stamp(end),
        "original": "\n".join(f"原文{number}" for number in cue_numbers),
        "proposed": proposed,
        "category": category,
        "confidence": confidence,
        "evidence": "test evidence",
        "needs_audio": False,
        "reason": "test reason",
    }


def _audit(
    agent: str,
    findings: list[dict],
    risks: list[dict] | None = None,
    *,
    cues_reviewed: int = 2630,
) -> bytes:
    return json.dumps(
        {
            "agent": agent,
            "cues_reviewed": cues_reviewed,
            "audio_reviewed": False,
            "findings": findings,
            "risk_cues": risks or [],
        },
        ensure_ascii=False,
    ).encode()


def _merge(a: list[dict], b: list[dict], *, b_risks: list[dict] | None = None):
    return merge_simple_step7(
        srt_bytes=_srt(),
        audit_a_bytes=_audit("A", a),
        audit_b_bytes=_audit("B", b, b_risks),
    )


def test_accepts_exact_high_confidence_consensus_and_preserves_timing() -> None:
    output, ledger_raw, queue_raw = _merge(
        [_record([3], proposed="正確 詞")],
        [_record([3], proposed="正確　詞", confidence=0.99)],
    )
    ledger, queue = json.loads(ledger_raw), json.loads(queue_raw)
    assert ledger["accepted_cue_ids"] == [3]
    assert ledger["needs_audio_count"] == 0
    assert queue["items"] == []
    before, after = _parse_srt(_srt()), _parse_srt(output)
    assert after[2].text == "正確 詞"
    assert [(item.number, item.start, item.end) for item in before] == [
        (item.number, item.start, item.end) for item in after
    ]


def test_conflicting_proposals_are_queued() -> None:
    _, ledger_raw, _ = _merge(
        [_record([4], proposed="甲")],
        [_record([4], proposed="乙", confidence=0.99)],
    )
    ledger = json.loads(ledger_raw)
    assert ledger["accepted_cue_ids"] == []
    assert ledger["rejected_cue_ids"] == [4]
    assert "normalized_proposal_conflict" in ledger["rejected"][0]["reasons"]


def test_number_category_requires_audio() -> None:
    _, ledger_raw, _ = _merge(
        [_record([5], proposed="五千萬", category="number")],
        [_record([5], proposed="五千萬", category="數字", confidence=0.99)],
    )
    ledger = json.loads(ledger_raw)
    assert ledger["accepted_count"] == 0
    assert "category_requires_audio" in ledger["rejected"][0]["reasons"]


@pytest.mark.parametrize("category", ["numeric", "quantity", "unknown_category"])
def test_unknown_or_numeric_category_requires_audio(category: str) -> None:
    _, ledger_raw, _ = _merge(
        [_record([5], proposed="候選", category=category)],
        [_record([5], proposed="候選", category=category, confidence=0.99)],
    )
    ledger = json.loads(ledger_raw)
    assert ledger["accepted_count"] == 0
    assert "category_requires_audio" in ledger["rejected"][0]["reasons"]


def test_single_sided_finding_and_overlapping_risk_are_queued() -> None:
    _, ledger_raw, _ = _merge(
        [_record([6], proposed="修正")],
        [],
        b_risks=[_record([6, 7], proposed=None, confidence=0.2)],
    )
    ledger = json.loads(ledger_raw)
    assert ledger["accepted_count"] == 0
    assert ledger["rejected_cue_ids"] == [6, 7]
    assert "single_sided_or_overlapping_finding" in ledger["rejected"][0]["reasons"]


def test_original_mismatch_fails_closed() -> None:
    bad = _record([8], proposed="修正")
    bad["original"] = "不是來源"
    with pytest.raises(SimpleStep7Error, match="original differs"):
        _merge([bad], [_record([8], proposed="修正", confidence=0.99)])


def test_audit_start_timestamp_mismatch_fails_closed() -> None:
    a = _record([8], proposed="修正")
    a["start"] = "00:00:00,000"
    with pytest.raises(SimpleStep7Error, match="timestamps differ"):
        _merge([a], [_record([8], proposed="修正", confidence=0.99)])


def test_audit_end_timestamp_mismatch_fails_closed() -> None:
    b = _record([8], proposed="修正", confidence=0.99)
    b["end"] = "00:00:30,000"
    with pytest.raises(SimpleStep7Error, match="timestamps differ"):
        _merge([_record([8], proposed="修正")], [b])


def test_cross_cue_consensus_without_per_cue_replacement_is_queued() -> None:
    _, ledger_raw, _ = _merge(
        [_record([9, 10], proposed="合併修正")],
        [_record([9, 10], proposed="合併修正", confidence=0.99)],
    )
    assert (
        "cross_cue_without_per_cue_replacement" in json.loads(ledger_raw)["rejected"][0]["reasons"]
    )


def test_non_contiguous_cross_cue_record_fails_closed() -> None:
    with pytest.raises(SimpleStep7Error, match="must be contiguous"):
        _merge(
            [_record([9, 11], proposed="不合法")],
            [_record([9, 11], proposed="不合法", confidence=0.99)],
        )


def test_rerun_is_byte_identical_and_refuses_different_existing_output(tmp_path: Path) -> None:
    srt = tmp_path / "source.srt"
    audit_a = tmp_path / "a.json"
    audit_b = tmp_path / "b.json"
    output = tmp_path / "corrected.srt"
    ledger = tmp_path / "ledger.json"
    queue = tmp_path / "queue.json"
    srt.write_bytes(_srt())
    audit_a.write_bytes(_audit("A", [_record([11], proposed="修正")]))
    audit_b.write_bytes(_audit("B", [_record([11], proposed="修正", confidence=0.99)]))
    argv = [
        "merge",
        "--srt",
        str(srt),
        "--audit-a",
        str(audit_a),
        "--audit-b",
        str(audit_b),
        "--output-srt",
        str(output),
        "--ledger-output",
        str(ledger),
        "--needs-audio-output",
        str(queue),
    ]
    assert main(argv) == 0
    first = tuple(path.read_bytes() for path in (output, ledger, queue))
    assert main(argv) == 0
    assert tuple(path.read_bytes() for path in (output, ledger, queue)) == first
    output.write_text("drift", encoding="utf-8")
    with pytest.raises(SimpleStep7Error, match="overwrite refused"):
        main(argv)


def test_ledger_binds_exact_finding_lineage() -> None:
    _, ledger_raw, _ = _merge(
        [_record([12], proposed="修正")],
        [_record([12], proposed="修正", confidence=0.99)],
    )
    ledger = json.loads(ledger_raw)
    lineages = ledger["accepted"][0]["lineage"]
    assert [item["agent"] for item in lineages] == ["A", "B"]
    for item in lineages:
        canonical = (
            json.dumps(
                item["finding"],
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        assert item["finding_sha256"] == hashlib.sha256(canonical).hexdigest()


def test_non_2630_episode_uses_actual_source_cue_count() -> None:
    output, ledger_raw, _ = merge_simple_step7(
        srt_bytes=_srt(3),
        audit_a_bytes=_audit("A", [_record([2], proposed="修正")], cues_reviewed=3),
        audit_b_bytes=_audit(
            "B", [_record([2], proposed="修正", confidence=0.99)], cues_reviewed=3
        ),
    )
    assert len(_parse_srt(output)) == 3
    assert json.loads(ledger_raw)["source_cue_count"] == 3


@pytest.mark.parametrize("conflict_destination", ["needs", "ledger"])
def test_output_bundle_conflict_creates_no_other_missing_destinations(
    tmp_path: Path, conflict_destination: str
) -> None:
    source = tmp_path / "source.srt"
    audit_a = tmp_path / "a.json"
    audit_b = tmp_path / "b.json"
    corrected = tmp_path / "corrected.srt"
    needs = tmp_path / "needs.json"
    ledger = tmp_path / "ledger.json"
    source.write_bytes(_srt(3))
    audit_a.write_bytes(_audit("A", [_record([2], proposed="修正")], cues_reviewed=3))
    audit_b.write_bytes(
        _audit("B", [_record([2], proposed="修正", confidence=0.99)], cues_reviewed=3)
    )
    conflicting = needs if conflict_destination == "needs" else ledger
    conflicting.write_bytes(b"conflict")
    with pytest.raises(SimpleStep7Error, match="overwrite refused"):
        main(
            [
                "merge",
                "--srt",
                str(source),
                "--audit-a",
                str(audit_a),
                "--audit-b",
                str(audit_b),
                "--output-srt",
                str(corrected),
                "--ledger-output",
                str(ledger),
                "--needs-audio-output",
                str(needs),
            ]
        )
    assert not corrected.exists()
    other = ledger if conflict_destination == "needs" else needs
    assert not other.exists()


def _arbitration_fixture(
    *,
    cue_numbers: list[int] | None = None,
    replacement: str = "正確詞",
    decision: str = "accept_single",
    major_risk: bool = False,
    source_agent: str = "A",
) -> dict[str, bytes]:
    cue_numbers = cue_numbers or [2]
    cue_count = max(4, cue_numbers[-1])
    source = _srt(cue_count)
    accepted_a = _record([1], proposed="基礎修正")
    accepted_b = _record([1], proposed="基礎修正", confidence=0.99)
    queued_a = _record(cue_numbers, proposed=replacement)
    audit_a = _audit(
        "A",
        [accepted_a, queued_a] if source_agent == "A" else [accepted_a],
        cues_reviewed=cue_count,
    )
    audit_b = _audit(
        "B",
        [accepted_b, queued_a] if source_agent == "B" else [accepted_b],
        cues_reviewed=cue_count,
    )
    corrected, ledger, needs = merge_simple_step7(
        srt_bytes=source,
        audit_a_bytes=audit_a,
        audit_b_bytes=audit_b,
    )
    original = "\n".join(f"原文{number}" for number in cue_numbers)
    item = {
        "a_proposals": [replacement] if source_agent == "A" else [],
        "b_proposals": [replacement] if source_agent == "B" else [],
        "b_risks": [],
        "confidence": "high",
        "cue_numbers": cue_numbers,
        "decision": decision,
        "evidence": "fixture",
        "major_risk": major_risk,
        "original": original,
        "reason": "fixture",
        "replacement": replacement,
    }
    arbitration = {
        "schema_version": 1,
        "contract": _ARBITRATION_CONTRACT,
        "migrated_from_arbitration_sha256": _ARBITRATION_MIGRATED_FROM_SHA256,
        "migration_reason": _ARBITRATION_MIGRATION_REASON,
        "input_hashes": {
            "srt_sha256": hashlib.sha256(source).hexdigest(),
            "audit_a_sha256": hashlib.sha256(audit_a).hexdigest(),
            "audit_b_sha256": hashlib.sha256(audit_b).hexdigest(),
            "strict_needs_audio_sha256": hashlib.sha256(needs).hexdigest(),
        },
        "queue_v3_sha256": hashlib.sha256(needs).hexdigest(),
        "queue_v2_sha256": _ARBITRATION_QUEUE_V2_SHA256,
        "queue_semantic_identity_verified_for_accepted": True,
        "accepted_count": 1,
        "unresolved_count": 0,
        "items": [item],
    }
    return {
        "srt": source,
        "audit_a": audit_a,
        "audit_b": audit_b,
        "base_corrected": corrected,
        "base_ledger": ledger,
        "base_needs": needs,
        "arbitration": json.dumps(arbitration, ensure_ascii=False).encode(),
    }


def _apply_fixture(bundle: dict[str, bytes]) -> tuple[bytes, bytes, bytes]:
    return apply_arbitration(
        srt_bytes=bundle["srt"],
        audit_a_bytes=bundle["audit_a"],
        audit_b_bytes=bundle["audit_b"],
        base_corrected_bytes=bundle["base_corrected"],
        base_ledger_bytes=bundle["base_ledger"],
        base_needs_audio_bytes=bundle["base_needs"],
        arbitration_bytes=bundle["arbitration"],
    )


def test_arbitration_accepts_single_and_preserves_base_correction() -> None:
    final_srt, ledger_raw, unresolved_raw = _apply_fixture(_arbitration_fixture())
    cues = _parse_srt(final_srt)
    ledger = json.loads(ledger_raw)
    assert cues[0].text == "基礎修正"
    assert cues[1].text == "正確詞"
    assert ledger["final_corrected_component_count"] == 2
    assert ledger["final_changed_cue_count"] == 2
    assert json.loads(unresolved_raw)["item_count"] == 0


def test_arbitration_accepts_multi_cue_only_with_aligned_lines() -> None:
    bundle = _arbitration_fixture(cue_numbers=[2, 3], replacement="修正二\n修正三")
    final_srt, ledger_raw, _ = _apply_fixture(bundle)
    cues = _parse_srt(final_srt)
    assert [cues[1].text, cues[2].text] == ["修正二", "修正三"]
    assert json.loads(ledger_raw)["final_changed_cue_count"] == 3


def test_arbitration_rejects_major_risk_acceptance() -> None:
    with pytest.raises(SimpleStep7Error, match="major-risk"):
        _apply_fixture(_arbitration_fixture(major_risk=True))


@pytest.mark.parametrize("mutation", ["hash", "coverage", "original"])
def test_arbitration_rejects_hash_coverage_or_original_mismatch(mutation: str) -> None:
    bundle = _arbitration_fixture()
    arbitration = json.loads(bundle["arbitration"])
    if mutation == "hash":
        arbitration["input_hashes"]["audit_a_sha256"] = "0" * 64
    elif mutation == "coverage":
        arbitration["items"] = []
    else:
        arbitration["items"][0]["original"] = "不是來源"
    bundle["arbitration"] = json.dumps(arbitration, ensure_ascii=False).encode()
    with pytest.raises(SimpleStep7Error):
        _apply_fixture(bundle)


def test_arbitration_rejects_overlap_with_base_accepted_cue() -> None:
    bundle = _arbitration_fixture()
    ledger = json.loads(bundle["base_ledger"])
    ledger["accepted"].append(
        {
            "cue_numbers": [2],
            "original": "原文2",
            "replacement": "正確詞",
            "lineage": [],
        }
    )
    ledger["accepted_count"] = 2
    ledger["accepted_cue_ids"] = [1, 2]
    bundle["base_corrected"] = bundle["base_corrected"].replace("原文2".encode(), "正確詞".encode())
    ledger["output_hashes"]["corrected_srt_sha256"] = hashlib.sha256(
        bundle["base_corrected"]
    ).hexdigest()
    bundle["base_ledger"] = json.dumps(ledger, ensure_ascii=False).encode()
    with pytest.raises(SimpleStep7Error, match="base corrected|overlaps"):
        _apply_fixture(bundle)


@pytest.mark.parametrize(
    ("source_agent", "cue_numbers", "injected"),
    [
        ("A", [2], "INJECTED-A"),
        ("B", [2], "INJECTED-B"),
        ("A", [2, 3], "INJECTED-2\nINJECTED-3"),
    ],
)
def test_arbitration_rejects_forged_proposal_injection(
    source_agent: str, cue_numbers: list[int], injected: str
) -> None:
    bundle = _arbitration_fixture(
        source_agent=source_agent,
        cue_numbers=cue_numbers,
        replacement="原始候選" if len(cue_numbers) == 1 else "原始二\n原始三",
    )
    arbitration = json.loads(bundle["arbitration"])
    key = "a_proposals" if source_agent == "A" else "b_proposals"
    arbitration["items"][0][key] = [injected]
    arbitration["items"][0]["replacement"] = injected
    bundle["arbitration"] = json.dumps(arbitration, ensure_ascii=False).encode()
    with pytest.raises(SimpleStep7Error, match="proposals differ from exact audits"):
        _apply_fixture(bundle)


def test_proposal_injection_cli_writes_no_outputs(tmp_path: Path) -> None:
    bundle = _arbitration_fixture()
    arbitration = json.loads(bundle["arbitration"])
    arbitration["items"][0]["a_proposals"] = ["INJECTED"]
    arbitration["items"][0]["replacement"] = "INJECTED"
    bundle["arbitration"] = json.dumps(arbitration, ensure_ascii=False).encode()
    paths = {key: tmp_path / f"{key}.bin" for key in bundle}
    for key, payload in bundle.items():
        paths[key].write_bytes(payload)
    final_paths = [
        tmp_path / "final.srt",
        tmp_path / "final-ledger.json",
        tmp_path / "final-unresolved.json",
    ]
    with pytest.raises(SimpleStep7Error, match="proposals differ from exact audits"):
        main(
            [
                "apply-arbitration",
                "--srt",
                str(paths["srt"]),
                "--audit-a",
                str(paths["audit_a"]),
                "--audit-b",
                str(paths["audit_b"]),
                "--base-corrected",
                str(paths["base_corrected"]),
                "--base-ledger",
                str(paths["base_ledger"]),
                "--base-needs-audio",
                str(paths["base_needs"]),
                "--arbitration",
                str(paths["arbitration"]),
                "--final-srt",
                str(final_paths[0]),
                "--final-ledger",
                str(final_paths[1]),
                "--final-unresolved",
                str(final_paths[2]),
            ]
        )
    assert not any(path.exists() for path in final_paths)


def test_arbitration_rejects_forged_risk_metadata() -> None:
    bundle = _arbitration_fixture()
    arbitration = json.loads(bundle["arbitration"])
    arbitration["items"][0]["b_risks"] = ["FORGED-RISK"]
    bundle["arbitration"] = json.dumps(arbitration, ensure_ascii=False).encode()
    with pytest.raises(SimpleStep7Error, match="risk metadata differs"):
        _apply_fixture(bundle)


def test_forged_base_bundle_fails_even_when_ledger_hash_is_synchronized() -> None:
    bundle = _arbitration_fixture()
    bundle["base_corrected"] = bundle["base_corrected"].replace("原文2".encode(), "FORGED".encode())
    ledger = json.loads(bundle["base_ledger"])
    ledger["output_hashes"]["corrected_srt_sha256"] = hashlib.sha256(
        bundle["base_corrected"]
    ).hexdigest()
    bundle["base_ledger"] = json.dumps(ledger, ensure_ascii=False).encode()
    with pytest.raises(SimpleStep7Error, match="fresh audit merge"):
        _apply_fixture(bundle)


def test_arbitration_rejects_multi_cue_line_count_mismatch() -> None:
    bundle = _arbitration_fixture(cue_numbers=[2, 3], replacement="只有一行")
    with pytest.raises(SimpleStep7Error, match="line count"):
        _apply_fixture(bundle)


@pytest.mark.parametrize("conflict", ["unresolved", "ledger"])
def test_arbitration_preflight_conflict_creates_no_partial_outputs(
    tmp_path: Path, conflict: str
) -> None:
    bundle = _arbitration_fixture()
    paths = {key: tmp_path / f"{key}.bin" for key in bundle}
    for key, payload in bundle.items():
        paths[key].write_bytes(payload)
    final_srt = tmp_path / "final.srt"
    final_unresolved = tmp_path / "unresolved.json"
    final_ledger = tmp_path / "ledger.json"
    conflicting = final_unresolved if conflict == "unresolved" else final_ledger
    conflicting.write_bytes(b"conflict")
    argv = [
        "apply-arbitration",
        "--srt",
        str(paths["srt"]),
        "--audit-a",
        str(paths["audit_a"]),
        "--audit-b",
        str(paths["audit_b"]),
        "--base-corrected",
        str(paths["base_corrected"]),
        "--base-ledger",
        str(paths["base_ledger"]),
        "--base-needs-audio",
        str(paths["base_needs"]),
        "--arbitration",
        str(paths["arbitration"]),
        "--final-srt",
        str(final_srt),
        "--final-ledger",
        str(final_ledger),
        "--final-unresolved",
        str(final_unresolved),
    ]
    with pytest.raises(SimpleStep7Error, match="overwrite refused"):
        main(argv)
    assert not final_srt.exists()
    other = final_ledger if conflict == "unresolved" else final_unresolved
    assert not other.exists()


def test_real_arbitration_import_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    source = Path(r"G:\Footages\20260814 抹布\subtitle-v2\memo-recognition.composite.execution.srt")
    required = [
        source,
        root / ".cache/simple-step7/audit-a.json",
        root / ".cache/simple-step7/audit-b.json",
        root / ".cache/simple-step7/strict-v3-corrected.srt",
        root / ".cache/simple-step7/strict-v3-consensus-ledger.json",
        root / ".cache/simple-step7/strict-v3-needs-audio.json",
        root / ".cache/simple-step7/arbitration-c-v3.json",
    ]
    if not all(path.exists() for path in required):
        pytest.skip("real episode artifacts are unavailable")
    final_srt, ledger_raw, unresolved_raw = apply_arbitration(
        srt_bytes=required[0].read_bytes(),
        audit_a_bytes=required[1].read_bytes(),
        audit_b_bytes=required[2].read_bytes(),
        base_corrected_bytes=required[3].read_bytes(),
        base_ledger_bytes=required[4].read_bytes(),
        base_needs_audio_bytes=required[5].read_bytes(),
        arbitration_bytes=required[6].read_bytes(),
    )
    ledger, unresolved = json.loads(ledger_raw), json.loads(unresolved_raw)
    assert len(_parse_srt(final_srt)) == 2630
    assert ledger["final_corrected_component_count"] == 49
    assert ledger["final_changed_cue_count"] == 51
    assert ledger["arbitration_accepted_component_count"] == 15
    assert unresolved["item_count"] == 59
    assert (final_srt, ledger_raw, unresolved_raw) == apply_arbitration(
        srt_bytes=required[0].read_bytes(),
        audit_a_bytes=required[1].read_bytes(),
        audit_b_bytes=required[2].read_bytes(),
        base_corrected_bytes=required[3].read_bytes(),
        base_ledger_bytes=required[4].read_bytes(),
        base_needs_audio_bytes=required[5].read_bytes(),
        arbitration_bytes=required[6].read_bytes(),
    )
