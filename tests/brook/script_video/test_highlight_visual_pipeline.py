from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import pytest

import agents.brook.script_video.highlight_visual_pipeline as visual_contract_module
from agents.brook.script_video.highlight_visual_pipeline import (
    ABANDONED_NAME,
    WORK_PACKET_NAME,
    HighlightVisualContractError,
    abandon_visual_revision,
    accept_director_plan,
    accept_director_replan,
    accept_dp_fulfillment,
    accept_dp_refinement,
    accept_refinement_decision,
    accept_semantic_audit,
    init_visual_work_packet,
    load_asset_authority,
    load_director_plan,
    load_dp_fulfillment,
    load_semantic_audit,
    load_visual_materializations,
    load_visual_work_packet,
    preflight_visual_work_packet,
    publish_asset_authority,
    verify_visual_lineage,
    verify_visual_pipeline,
    visual_pipeline_status,
)
from shared.highlight_materialization import (
    HighlightSource,
    build_materialization_receipt,
    write_materialization_receipt,
)

_REAL_REQUIRE_TRUSTED_EXECUTION_RECEIPT = (
    visual_contract_module._require_trusted_execution_receipt
)
_REAL_REQUIRE_PERSISTED_EXECUTION_RECEIPT = (
    visual_contract_module._require_persisted_execution_receipt
)
_REAL_ACCEPTED_DP_HYDRATION_LINEAGE = visual_contract_module._accepted_dp_hydration_lineage
_REAL_VERIFY_CANONICAL_DP_HYDRATION = visual_contract_module._verify_canonical_dp_hydration
_REAL_VERIFY_CANONICAL_ASSET_EXECUTION = (
    visual_contract_module._verify_canonical_asset_execution
)

DIRECTOR_WORKER = {
    "worker_id": "director-owner-v1",
    "execution_id": "director-execution-001",
    "role": "director",
    "session_id": "session-director-001",
}
DP_WORKER = {
    "worker_id": "dp-worker-v1",
    "execution_id": "dp-execution-001",
    "role": "dp",
    "session_id": "session-dp-001",
}
AUDIT_WORKER = {
    "worker_id": "director-owner-v1",
    "execution_id": "director-audit-execution-002",
    "role": "director",
    "session_id": "session-director-001",
}
DP2_WORKER = {
    "worker_id": "dp-worker-v1",
    "execution_id": "dp-execution-002",
    "role": "dp",
    "session_id": "session-dp-002",
}
AUDIT2_WORKER = {
    "worker_id": "director-owner-v1",
    "execution_id": "director-audit-execution-004",
    "role": "director",
    "session_id": "session-director-001",
}


@pytest.mark.parametrize("category", ["self_archive", "screen_demo", "evidence_doc"])
def test_unavailable_provided_categories_allow_hyperframes_fallback(category: str) -> None:
    assert visual_contract_module._MODES_BY_DIRECTOR_CATEGORY[category] == {
        "provided_asset",
        "hyperframes",
    }
    assert visual_contract_module._MODES_BY_DIRECTOR_CATEGORY["stock_scene"] == {
        "stock",
        "hyperframes",
    }


REFINEMENT_WORKER = {
    "worker_id": "director-owner-v1",
    "execution_id": "director-refinement-execution-003",
    "role": "director",
    "session_id": "session-director-001",
}
ASSET_WORKER = {
    "worker_id": "trusted-acquisition-v1",
    "execution_id": "asset-acquisition-execution-001",
    "role": "asset_acquisition",
    "session_id": "session-asset-acquisition-001",
}
ASSET2_WORKER = {
    **ASSET_WORKER,
    "execution_id": "asset-acquisition-execution-002",
    "session_id": "session-asset-acquisition-002",
}
DIRECTOR_REPLAN_WORKER = {
    "worker_id": "director-owner-v1",
    "execution_id": "director-replan-execution-005",
    "role": "director",
    "session_id": "session-director-001",
}
AUDIT_AFTER_REPLAN_WORKER = {
    "worker_id": "director-owner-v1",
    "execution_id": "director-audit-execution-006",
    "role": "director",
    "session_id": "session-director-001",
}


@pytest.fixture(autouse=True)
def _trusted_hyperframes_verifier_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    def verify(episode_root: object, **kwargs: object) -> dict[str, object]:
        assert Path(episode_root).is_dir()
        assert set(kwargs) == {
            "receipt_identity",
            "expected_cut_id",
            "expected_revision_id",
            "expected_candidate_id",
            "expected_component",
            "expected_render_params",
            "expected_on_screen_text",
            "expected_media",
        }
        return {"contract": "trusted-render-test-boundary"}

    monkeypatch.setattr(visual_contract_module, "verify_hyperframes_render_receipt", verify)
    monkeypatch.setattr(
        visual_contract_module,
        "_require_trusted_execution_receipt",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        visual_contract_module,
        "_require_persisted_execution_receipt",
        lambda root, **kwargs: Path(root) / ".test-execution-proposal",
    )
    monkeypatch.setattr(
        visual_contract_module,
        "_accepted_dp_hydration_lineage",
        lambda *args, **kwargs: (
            {
                "worker_proposal": None,
                "hydrated_proposal": None,
                "hydration_receipt": None,
            },
            None,
            None,
        ),
    )
    monkeypatch.setattr(
        visual_contract_module,
        "_verify_canonical_dp_hydration",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        visual_contract_module,
        "_verify_canonical_asset_execution",
        lambda *args, **kwargs: None,
    )


def _assert_contract_error(call, contains: str) -> None:
    try:
        call()
    except HighlightVisualContractError as error:
        assert contains in str(error), str(error)
    else:
        raise AssertionError(f"expected HighlightVisualContractError containing {contains!r}")


def test_direct_accept_and_asset_publish_reject_forged_worker_without_execution_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = str(work.document["revision_id"])
    monkeypatch.setattr(
        visual_contract_module,
        "_require_trusted_execution_receipt",
        _REAL_REQUIRE_TRUSTED_EXECUTION_RECEIPT,
    )

    _assert_contract_error(
        lambda: accept_director_plan(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=_director_proposal(work),
            worker_identity=DIRECTOR_WORKER,
            editorial_master=master,
        ),
        "canonical trusted execution receipt",
    )

    fixture = Path(__file__).parent / "fixtures" / "davinci_import" / "black10s.mp4"
    local_media = root / "assets" / "forged-local-stock.mp4"
    local_media.parent.mkdir(parents=True, exist_ok=True)
    local_media.write_bytes(fixture.read_bytes())
    _assert_contract_error(
        lambda: publish_asset_authority(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            attempt=1,
            assets=[
                _trusted_asset_source(
                    root,
                    local_media,
                    asset_id="forged-local-stock",
                    provider_item_id="111111",
                    semantic_summary="偽造的本機 Stock 素材不可自行宣告成可信取得來源",
                )
            ],
            worker_identity=ASSET_WORKER,
            editorial_master=master,
        ),
        "canonical trusted execution receipt",
    )
    assert not (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "revisions"
        / revision_id
        / "attempts"
        / "attempt-001"
        / "trusted-acquisitions"
    ).exists()


def test_director_accept_consumes_execution_verified_snapshot_if_file_swaps_after_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = str(work.document["revision_id"])
    proposal_path = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "jobs"
        / revision_id
        / "workers"
        / "director-session"
        / "director-proposal.json"
    )
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    original = _director_proposal(work)
    proposal_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    execution_receipt = _write_trusted_execution_receipt(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        phase="director",
        role="director",
        worker_identity=DIRECTOR_WORKER,
        proposal_path=proposal_path,
    )

    def verify_then_swap(*args: object, **kwargs: object) -> dict[str, object]:
        verified = _REAL_REQUIRE_TRUSTED_EXECUTION_RECEIPT(*args, **kwargs)
        swapped = deepcopy(original)
        swapped["events"][0]["description"] = (
            "這是收據驗證完成後才換入的另一份描述，不可被 canonical acceptance 重新開檔吃入。"
        )
        proposal_path.write_text(json.dumps(swapped, ensure_ascii=False), encoding="utf-8")
        return verified

    monkeypatch.setattr(
        visual_contract_module, "_require_trusted_execution_receipt", verify_then_swap
    )
    accepted = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=proposal_path,
        worker_identity=DIRECTOR_WORKER,
        execution_receipt=execution_receipt,
        editorial_master=master,
    )

    assert accepted.document["events"][0]["description"] == original["events"][0]["description"]


def test_director_accept_rejects_tampered_dispatch_prepare_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = str(work.document["revision_id"])
    proposal_path = root / "workers" / "director-proposal.json"
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text(
        json.dumps(_director_proposal(work), ensure_ascii=False), encoding="utf-8"
    )
    receipt_identity = _write_trusted_execution_receipt(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        phase="director",
        role="director",
        worker_identity=DIRECTOR_WORKER,
        proposal_path=proposal_path,
    )
    receipt = json.loads((root / str(receipt_identity["path"])).read_text(encoding="utf-8"))
    prepare_path = root / str(receipt["prepare"]["path"])
    prepare_path.write_text(prepare_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    monkeypatch.setattr(
        visual_contract_module,
        "_require_trusted_execution_receipt",
        _REAL_REQUIRE_TRUSTED_EXECUTION_RECEIPT,
    )

    _assert_contract_error(
        lambda: accept_director_plan(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=proposal_path,
            worker_identity=DIRECTOR_WORKER,
            execution_receipt=receipt_identity,
            editorial_master=master,
        ),
        "prepare receipt identity drift",
    )


@dataclass(frozen=True)
class _FakeMaster:
    value: dict[str, object]
    media_path: Path
    srt_path: Path

    def identity(self) -> dict[str, object]:
        return dict(self.value)


def _episode(tmp_path: Path) -> tuple[Path, _FakeMaster]:
    root = tmp_path / "20260805-linzhichen"
    highlights = root / "highlights"
    srt_dir = highlights / "srt"
    srt_dir.mkdir(parents=True)
    master = _FakeMaster(
        value={
            "contract": "podcast-editorial-master-v1",
            "episode_id": root.name,
            "content_hash": "a" * 64,
            "master_media_sha256": "b" * 64,
            "master_srt_sha256": "c" * 64,
            "editorial_master_receipt": "editorial-master/v1/EDITORIAL-MASTER.json",
        },
        media_path=root / "editorial-master" / "v1" / "master.mp4",
        srt_path=root / "editorial-master" / "v1" / "master.srt",
    )
    master.media_path.parent.mkdir(parents=True)
    master.media_path.write_bytes(b"master-media")
    master.srt_path.write_text("master subtitles", encoding="utf-8")
    candidate = {
        "id": "value-L01",
        "format": "long",
        "t_start": 10.0,
        "t_end": 34.0,
    }
    (highlights / "candidates.json").write_text(
        json.dumps(
            {
                "editorial_master_lineage": master.identity(),
                "candidates": [candidate],
            }
        ),
        encoding="utf-8",
    )
    (highlights / "winners.json").write_text(
        json.dumps(
            {
                "editorial_master_lineage": master.identity(),
                "winners": [{"id": "value-L01", "rank": 1}],
            }
        ),
        encoding="utf-8",
    )
    (srt_dir / "value-L01_tight_r001.srt").write_text(
        "1\n00:00:00,000 --> 00:00:04,933\n戰後教育強調服從\n\n"
        "2\n00:00:04,933 --> 00:00:06,000\n學生要剃平頭，學校裡還有教官\n\n"
        "3\n00:00:06,000 --> 00:00:10,000\n高壓教育留下深遠影響\n\n"
        "4\n00:00:10,000 --> 00:00:14,000\n這種制度延續了很長一段時間\n\n"
        "5\n00:00:14,000 --> 00:00:18,000\n直到後來教育才逐漸改變\n\n"
        "6\n00:00:18,000 --> 00:00:24,000\n這裡刻意讓觀眾看來賓完整說明原因\n",
        encoding="utf-8",
        newline="\n",
    )

    class _Timeline:
        def GetName(self) -> str:
            return "長1 - value-L01"

        def GetUniqueId(self) -> str:
            return "timeline-value-L01"

    receipt = build_materialization_receipt(
        root,
        cut_id="value-L01",
        cut_format="long",
        timeline=_Timeline(),
        source_range={
            "start_sec": 10.0,
            "end_sec": 34.0,
            "start_frame": 300,
            "end_frame": 1020,
        },
        source=HighlightSource(
            srt_path=master.srt_path,
            media_path=master.media_path,
            lineage=master.identity(),
        ),
    )
    write_materialization_receipt(root, receipt)
    return root, master


def test_init_work_packet_is_deterministic_and_waits_for_director(tmp_path: Path) -> None:
    root, master = _episode(tmp_path)

    first = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    original = first.path.read_bytes()
    second = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)

    assert first.path.name == WORK_PACKET_NAME
    assert second.path.read_bytes() == original
    loaded = load_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    assert loaded.document["format"] == "long"
    assert loaded.document["cut_srt"]["path"].endswith("_tight_r001.srt")
    assert (
        visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)["status"]
        == "awaiting_director"
    )


def test_preflight_is_read_only_and_status_explicitly_awaits_init(tmp_path: Path) -> None:
    root, master = _episode(tmp_path)
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

    assert (
        visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)["status"]
        == "awaiting_init"
    )
    prospective = preflight_visual_work_packet(root, cut_id="value-L01", editorial_master=master)

    assert prospective["contract"] == "podcast-highlight-visual-preflight-v1"
    assert prospective["status"] == "would_initialize"
    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*")) == before
    initialized = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    assert initialized.document["revision_id"] == prospective["revision_id"]

    _assert_contract_error(
        lambda: preflight_visual_work_packet(root, cut_id="../escape", editorial_master=master),
        "unsafe cut_id",
    )
    foreign_request = tmp_path / "foreign-feedback.json"
    foreign_request.write_text('{"feedback":"outside episode"}', encoding="utf-8")
    _assert_contract_error(
        lambda: preflight_visual_work_packet(
            root,
            cut_id="value-L01",
            revision_request=foreign_request,
            editorial_master=master,
        ),
        "episode-local existing file",
    )


def test_pending_revision_can_be_abandoned_without_changing_old_artifacts_then_retried(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    pending = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = str(pending.document["revision_id"])
    cut_root = root / "highlights" / "visual-pipeline" / "value-L01"
    old_receipt = cut_root / "jobs" / revision_id / "receipts" / "dp.json"
    old_dp_input = cut_root / "jobs" / revision_id / "workers" / "dp-session" / "dp-input.json"
    old_receipt.parent.mkdir(parents=True, exist_ok=True)
    old_dp_input.parent.mkdir(parents=True, exist_ok=True)
    old_receipt.write_bytes(b"immutable-old-receipt")
    old_dp_input.write_bytes(b"immutable-old-dp-input")
    before = {
        path.relative_to(cut_root).as_posix(): path.read_bytes()
        for path in cut_root.rglob("*")
        if path.is_file() and path.name != "PENDING.json"
    }

    abandoned = abandon_visual_revision(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        reason="The worker stopped before Resolve materialization.",
        editorial_master=master,
    )

    assert abandoned.path.name == ABANDONED_NAME
    assert abandoned.document["state"] == "abandoned"
    assert abandoned.document["revision_id"] == revision_id
    assert visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)[
        "status"
    ] == "abandoned"
    abandoned_bytes = abandoned.path.read_bytes()
    _assert_contract_error(
        lambda: abandon_visual_revision(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            reason="A second abandon is not a pending transition.",
            editorial_master=master,
        ),
        "not the active PENDING generation",
    )
    assert abandoned.path.read_bytes() == abandoned_bytes
    assert {
        path.relative_to(cut_root).as_posix(): path.read_bytes()
        for path in cut_root.rglob("*")
        if path.is_file() and path.name not in {"PENDING.json", ABANDONED_NAME}
    } == before

    request = root / "highlights" / "review" / "retry-request.json"
    request.parent.mkdir(parents=True, exist_ok=True)
    request.write_text('{"reason":"fresh retry"}', encoding="utf-8")
    retried = init_visual_work_packet(
        root,
        cut_id="value-L01",
        revision_request=request,
        editorial_master=master,
    )

    assert retried.document["revision_id"] != revision_id
    assert visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)[
        "status"
    ] == "awaiting_director"
    assert abandoned.path.read_bytes() == (
        cut_root / "revisions" / revision_id / ABANDONED_NAME
    ).read_bytes()


def test_abandon_rejects_revision_that_is_not_the_active_pending_generation(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)

    _assert_contract_error(
        lambda: abandon_visual_revision(
            root,
            cut_id="value-L01",
            revision_id="r-000000000000000000000000",
            reason="There is no active pending generation.",
            editorial_master=master,
        ),
        "not PENDING",
    )
    assert not (root / "highlights" / "visual-pipeline" / "value-L01").exists()


def test_abandon_rejects_current_revision(tmp_path: Path) -> None:
    root, master = _episode(tmp_path)
    pending = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    completed = _complete_generation(root, master, pending)
    revision_id = str(completed.work_packet.document["revision_id"])
    current = root / "highlights" / "visual-pipeline" / "value-L01" / "CURRENT.json"
    before = current.read_bytes()

    _assert_contract_error(
        lambda: abandon_visual_revision(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            reason="A CURRENT generation must remain immutable.",
            editorial_master=master,
        ),
        "CURRENT visual revision cannot be abandoned",
    )
    assert current.read_bytes() == before
    assert not (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "revisions"
        / revision_id
        / ABANDONED_NAME
    ).exists()


def test_abandon_rejects_revision_with_committed_resolve_materialization(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    pending = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = str(pending.document["revision_id"])
    receipt = root / "highlights" / "tighten" / "value-L01_broll_materialization.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "contract": "podcast-long-highlight-stock-video-v2",
                "visual_pipeline_lineage": {"revision_id": revision_id},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    before = receipt.read_bytes()

    _assert_contract_error(
        lambda: abandon_visual_revision(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            reason="Materialized bytes cannot be detached from their lineage.",
            editorial_master=master,
        ),
        "materialized visual revision cannot be abandoned",
    )
    assert receipt.read_bytes() == before
    assert not (
        pending.path.parent / ABANDONED_NAME
    ).exists()


def _director_proposal(work: object) -> dict[str, object]:
    identity = work.identity()
    return {
        "contract": "podcast-highlight-director-plan-v1",
        "episode_id": work.document["episode_id"],
        "cut_id": work.document["cut_id"],
        "work_packet": identity,
        "events": [
            {
                "event_id": "visual-001",
                "cue_ids": [1, 2],
                "t0": 0.0,
                "t1": 6.0,
                "quote": "戰後教育強調服從\n學生要剃平頭，學校裡還有教官",
                "category": "stock_scene",
                "form": "cutaway",
                "description": "戰後校園的軍事化紀律、平頭學生與教官，而非現代遊戲教學",
                "on_screen_text": None,
                "shots_hint": 3,
                "negative_constraints": ["不可出現現代幼兒教具", "不可用快樂遊戲課堂"],
                "search_angles": [
                    "postwar Taiwan school military discipline",
                    "East Asian students buzz cut historical classroom",
                    "school military instructor archival footage",
                ],
                "decision": "add_visual",
                "rationale": "逐字稿提出具體歷史教育場景，需要可核對的歷史畫面。",
            },
            {
                "event_id": "title-002",
                "cue_ids": [3],
                "t0": 6.0,
                "t1": 10.0,
                "quote": "高壓教育留下深遠影響",
                "category": "keyword",
                "form": "overlay",
                "description": "以完整的雙行 Hero Title 點出高壓教育的長期影響",
                "on_screen_text": "高壓教育\n留下深遠影響",
                "shots_hint": 1,
                "negative_constraints": ["不可截成不完整片語"],
                "search_angles": [],
                "decision": "add_visual",
                "rationale": "這是完整論點，換行後可在來賓畫面上快速讀懂。",
            },
            {
                "event_id": "visual-002",
                "cue_ids": [5],
                "t0": 14.0,
                "t1": 18.0,
                "quote": "直到後來教育才逐漸改變",
                "category": "chapter",
                "form": "overlay",
                "description": "教育制度逐漸改變的章節轉換卡",
                "on_screen_text": "教育開始\n改變",
                "shots_hint": 1,
                "negative_constraints": [],
                "search_angles": [],
                "decision": "add_visual",
                "rationale": "用簡短疊字維持視覺節奏，不取代來賓表情。",
            },
            {
                "event_id": "aroll-003",
                "cue_ids": [6],
                "t0": 18.0,
                "t1": 24.0,
                "quote": "這裡刻意讓觀眾看來賓完整說明原因",
                "category": "none",
                "form": "aroll",
                "description": "保留來賓完整表情與論證",
                "on_screen_text": None,
                "shots_hint": 1,
                "negative_constraints": [],
                "search_angles": [],
                "decision": "intentional_aroll",
                "rationale": "這段是來賓完整推理與表情反應，刻意不以持續素材遮住。",
            },
        ],
        "coverage": {
            "timeline_start_sec": 0.0,
            "timeline_end_sec": 24.0,
            "add_visual_count": 3,
            "planned_visual_count": 5,
            "planned_stock_video_count": 3,
            "intentional_aroll_count": 1,
            "max_uncovered_sec": 4.0,
            "max_uncovered_start_sec": 10.0,
            "max_uncovered_end_sec": 14.0,
            "visual_events_per_minute": 12.5,
            "cutaway_events_per_minute": 2.5,
        },
    }


def test_director_plan_accepts_exact_cues_and_advances_to_dp(tmp_path: Path) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)

    accepted = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=work.document["revision_id"],
        proposal=_director_proposal(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )

    assert accepted.document["coverage"]["max_uncovered_sec"] == 4.0
    assert (
        load_director_plan(root, cut_id="value-L01", editorial_master=master).identity()
        == accepted.identity()
    )
    assert (
        visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)["status"]
        == "awaiting_dp"
    )


def test_director_rejects_quote_drift_and_zero_or_two_stock_shots(tmp_path: Path) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = work.document["revision_id"]

    quote_drift = _director_proposal(work)
    quote_drift["events"][0]["quote"] = "泛稱教育很嚴格"
    _assert_contract_error(
        lambda: accept_director_plan(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=quote_drift,
            worker_identity=DIRECTOR_WORKER,
            editorial_master=master,
        ),
        "exact cue text",
    )

    two_stock = _director_proposal(work)
    two_stock["events"][0]["shots_hint"] = 2
    _assert_contract_error(
        lambda: accept_director_plan(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=two_stock,
            worker_identity=DIRECTOR_WORKER,
            editorial_master=master,
        ),
        "at least 3 planned stock",
    )

    zero_stock = _director_proposal(work)
    zero_stock["events"][0]["category"] = "worked_example"
    _assert_contract_error(
        lambda: accept_director_plan(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=zero_stock,
            worker_identity=DIRECTOR_WORKER,
            editorial_master=master,
        ),
        "at least 3 planned stock",
    )

    accepted = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_director_proposal(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )
    assert accepted.document["coverage"]["planned_stock_video_count"] == 3


def test_dp_allows_truthful_hyperframes_text_only_when_director_text_is_null(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    director_proposal = _director_proposal(work)
    fallback_event = next(
        event for event in director_proposal["events"] if event["event_id"] == "visual-002"
    )
    fallback_event["category"] = "screen_demo"
    fallback_event["form"] = "overlay"
    fallback_event["on_screen_text"] = None
    director = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=work.document["revision_id"],
        proposal=director_proposal,
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )

    proposal = _dp_proposal(root, director)
    fallback = next(
        item for item in proposal["implementations"] if item["event_id"] == "visual-002"
    )
    fallback_candidate = fallback["candidates"][0]
    fallback_candidate["render_params"]["show_sec"] = 2.0
    fallback_candidate["render_spec_sha256"] = _content_hash(
        {
            "component": fallback_candidate["component"],
            "render_params": fallback_candidate["render_params"],
        }
    )
    fallback["selections"][0]["t1"] = fallback["selections"][0]["t0"] + 2.0
    fallback["selections"][0]["source_range"]["end_sec"] = 2.0

    accepted = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=work.document["revision_id"],
        proposal=proposal,
        worker_identity=DP_WORKER,
        editorial_master=master,
    )

    implementation = next(
        item for item in accepted.document["implementations"] if item["event_id"] == "visual-002"
    )
    assert implementation["on_screen_text"] == "教育開始\n改變"
    materialization = next(
        item
        for item in load_visual_materializations(
            root,
            cut_id="value-L01",
            revision_id=work.document["revision_id"],
            editorial_master=master,
        )
        if item["event_id"] == "visual-002"
    )
    assert materialization["t1"] - materialization["t0"] == 2.0
    assert materialization["source_range"] == {"start_sec": 0.0, "end_sec": 2.0}


def _identity(root: Path, path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _content_hash(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _write_trusted_execution_receipt(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    phase: str,
    role: str,
    worker_identity: dict[str, str],
    proposal_path: Path,
) -> dict[str, object]:
    job_root = root / "highlights" / "visual-pipeline" / cut_id / "jobs" / revision_id
    receipt_root = job_root / "receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)
    phase_input = receipt_root / f"{phase}.input.json"
    stdout = receipt_root / f"{phase}.stdout.jsonl"
    stderr = receipt_root / f"{phase}.stderr.txt"
    phase_input.write_text('{"trusted_test_input":true}\n', encoding="utf-8")
    stdout.write_text(
        json.dumps(
            {"type": "thread.started", "thread_id": worker_identity["session_id"]}
        )
        + "\n",
        encoding="utf-8",
    )
    stderr.write_text("", encoding="utf-8")
    prompt_sha256 = hashlib.sha256(b"trusted fixture prompt").hexdigest()
    attempt_root = receipt_root / f"{phase}.attempts" / "attempt-001"
    prepare: dict[str, object] = {
        "contract": "podcast-highlight-visual-worker-prepare-v1",
        "episode_id": root.name,
        "cut_id": cut_id,
        "revision_id": revision_id,
        "phase": phase,
        "role": role,
        "attempt": 1,
        "orchestrator_pid": os.getpid(),
        "prompt_sha256": prompt_sha256,
        "phase_input": _identity(root, phase_input),
        "proposal_path": proposal_path.relative_to(root).as_posix(),
    }
    prepare["content_hash"] = _content_hash(prepare)
    prepare_path = attempt_root / "PREPARE.json"
    prepare_path.parent.mkdir(parents=True, exist_ok=True)
    prepare_path.write_text(json.dumps(prepare, ensure_ascii=False), encoding="utf-8")
    receipt: dict[str, object] = {
        "contract": "podcast-highlight-visual-worker-execution-v1",
        "episode_id": root.name,
        "cut_id": cut_id,
        "revision_id": revision_id,
        "phase": phase,
        "role": role,
        "worker_identity": worker_identity,
        "prepare": _identity(root, prepare_path),
        "prompt_sha256": prompt_sha256,
        "phase_input": _identity(root, phase_input),
        "proposal": _identity(root, proposal_path),
        "stdout": _identity(root, stdout),
        "stderr": _identity(root, stderr),
    }
    receipt["content_hash"] = _content_hash(receipt)
    receipt_path = receipt_root / f"{phase}.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    return _identity(root, receipt_path)


def _trusted_asset_source(
    root: Path,
    path: Path,
    *,
    asset_id: str,
    provider_item_id: str,
    semantic_summary: str,
    provider: str = "pexels",
) -> dict[str, object]:
    return {
        "asset_id": asset_id,
        "source_class": "licensed_stock",
        "provider": provider,
        "provider_item_id": provider_item_id,
        "source_url": f"https://www.pexels.com/video/{provider_item_id}/",
        "license": "Pexels license: https://www.pexels.com/license/",
        "acquired_at": "2026-08-26T01:00:00Z",
        "semantic_summary": semantic_summary,
        "original_media": _identity(root, path),
    }


def test_asset_authority_freshly_rechecks_execution_bound_acquisition_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = str(work.document["revision_id"])
    fixture = Path(__file__).parent / "fixtures" / "davinci_import" / "black10s.mp4"
    media = root / "proposal-assets" / "trusted-acquisition.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(fixture.read_bytes())
    source = _trusted_asset_source(
        root,
        media,
        asset_id="trusted-acquisition",
        provider_item_id="7106572",
        semantic_summary="可核對來源與授權的真實校園協作影片，不是生成圖片裁切素材",
    )
    manifest_path = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "jobs"
        / revision_id
        / "workers"
        / "asset-session"
        / "asset-acquisition.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "contract": "podcast-highlight-asset-acquisition-proposal-v1",
                "episode_id": root.name,
                "cut_id": "value-L01",
                "revision_id": revision_id,
                "attempt": 1,
                "assets": [source],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    receipt = _write_trusted_execution_receipt(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        phase="asset_acquisition-001",
        role="asset_acquisition",
        worker_identity=ASSET_WORKER,
        proposal_path=manifest_path,
    )
    monkeypatch.setattr(
        visual_contract_module,
        "_require_trusted_execution_receipt",
        _REAL_REQUIRE_TRUSTED_EXECUTION_RECEIPT,
    )
    monkeypatch.setattr(
        visual_contract_module,
        "_verify_canonical_asset_execution",
        _REAL_VERIFY_CANONICAL_ASSET_EXECUTION,
    )
    monkeypatch.setattr(
        visual_contract_module,
        "_require_persisted_execution_receipt",
        _REAL_REQUIRE_PERSISTED_EXECUTION_RECEIPT,
    )

    accepted = publish_asset_authority(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        attempt=1,
        assets=[source],
        worker_identity=ASSET_WORKER,
        acquisition_manifest=manifest_path,
        execution_receipt=receipt,
        editorial_master=master,
    )
    assert accepted.document["assets"][0]["original_media"] == _identity(root, media)

    manifest_path.write_text(manifest_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    _assert_contract_error(
        lambda: load_asset_authority(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            attempt=1,
            editorial_master=master,
        ),
        "trusted worker proposal changed",
    )


def test_asset_authority_preflights_the_whole_batch_before_publishing_receipts(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = str(work.document["revision_id"])
    fixture_dir = Path(__file__).parent / "fixtures" / "davinci_import"
    assets_dir = root / "highlights" / "visual-pipeline" / "value-L01" / "proposal-assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    first = assets_dir / "trusted-first.mp4"
    second = assets_dir / "trusted-second.mp4"
    first.write_bytes((fixture_dir / "black10s.mp4").read_bytes() + b"trusted-first")
    second.write_bytes((fixture_dir / "bigstat3s.mp4").read_bytes() + b"trusted-second")
    first_source = _trusted_asset_source(
        root,
        first,
        asset_id="trusted-first",
        provider_item_id="101",
        semantic_summary="戰後校園升旗隊列與教官巡視的可核對歷史實拍",
    )
    invalid_second = _trusted_asset_source(
        root,
        second,
        asset_id="trusted-second",
        provider_item_id="102",
        semantic_summary="學生一致髮型與制服的可核對歷史教室實拍",
        provider="ImageGen forged as Envato",
    )
    attempt_dir = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "revisions"
        / revision_id
        / "attempts"
        / "attempt-001"
    )

    forged_profile = {
        **invalid_second,
        "provider": "pexels",
        "source_url": "https://elements.envato.com/fake-item-102",
    }
    _assert_contract_error(
        lambda: publish_asset_authority(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            attempt=1,
            assets=[first_source, forged_profile],
            worker_identity=ASSET_WORKER,
            editorial_master=master,
        ),
        "Pexels provider/item/URL/license profile mismatch",
    )
    assert not (attempt_dir / "trusted-acquisitions").exists()
    _assert_contract_error(
        lambda: publish_asset_authority(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            attempt=1,
            assets=[first_source, invalid_second],
            worker_identity=ASSET_WORKER,
            editorial_master=master,
        ),
        "generated provider",
    )
    assert not (attempt_dir / "trusted-acquisitions").exists()
    assert not (attempt_dir / "ASSET-AUTHORITY.json").exists()

    corrected_second = {
        **invalid_second,
        "provider": "pexels",
    }
    accepted = publish_asset_authority(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        attempt=1,
        assets=[first_source, corrected_second],
        worker_identity=ASSET_WORKER,
        editorial_master=master,
    )
    assert [row["asset_id"] for row in accepted.document["assets"]] == [
        "trusted-first",
        "trusted-second",
    ]


def test_envato_source_identity_rejects_case_variants_before_any_publish(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = str(work.document["revision_id"])
    fixture = Path(__file__).parent / "fixtures" / "davinci_import" / "black10s.mp4"
    media = root / "assets" / "case-variant-envato.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(fixture.read_bytes() + b"case-variant-envato")
    source = {
        **_trusted_asset_source(
            root,
            media,
            asset_id="envato-case-variant",
            provider_item_id="abc123",
            semantic_summary="可核對來源的教室隊列實拍，僅用來測試 Envato identity casing",
        ),
        "provider": "envato-elements",
        "provider_item_id": "ABC123",
        "source_url": "https://elements.envato.com/classroom-cadets-ABC123",
        "license": "Envato Elements license: https://elements.envato.com/license-terms",
    }

    _assert_contract_error(
        lambda: publish_asset_authority(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            attempt=1,
            assets=[source],
            worker_identity=ASSET_WORKER,
            editorial_master=master,
        ),
        "Envato provider_item_id is invalid",
    )
    trusted_root = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "revisions"
        / revision_id
        / "attempts"
        / "attempt-001"
        / "trusted-acquisitions"
    )
    assert not trusted_root.exists()


def test_authority_extension_deduplicates_envato_item_and_url_casefolded() -> None:
    prior = {
        "asset_id": "prior-envato",
        "provider": "envato-elements",
        "provider_item_id": "abc123",
        "source_url": "https://elements.envato.com/classroom-cadets-abc123",
        "original_media": {"sha256": "1" * 64},
    }
    renamed_case_variant = {
        "asset_id": "renamed-envato",
        "provider": "ENVATO-ELEMENTS",
        "provider_item_id": "ABC123",
        "source_url": "https://elements.envato.com/CLASSROOM-CADETS-ABC123",
        "original_media": {"sha256": "2" * 64},
    }

    _assert_contract_error(
        lambda: visual_contract_module._validate_authority_extension(
            [prior], [renamed_case_variant]
        ),
        "reuses original source across attempts",
    )


def _intent_hash(event: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            event,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _publish_fixture_asset_authority(
    root: Path,
    work_packet: dict[str, object],
    *,
    extra_sources: list[dict[str, object]] | None = None,
) -> tuple[list[Path], tuple[str, str, str], dict[str, dict[str, object]]]:
    assets = root / "highlights" / "visual-pipeline" / "value-L01" / "proposal-assets"
    assets.mkdir(parents=True, exist_ok=True)
    fixture_dir = Path(__file__).parent / "fixtures" / "davinci_import"
    stock_paths = [
        assets / "postwar-school-a.mp4",
        assets / "postwar-school-b.mp4",
        assets / "postwar-school-c.mp4",
    ]
    black = (fixture_dir / "black10s.mp4").read_bytes()
    stock_paths[0].write_bytes(black + b"candidate-a")
    stock_paths[1].write_bytes((fixture_dir / "bigstat3s.mp4").read_bytes())
    stock_paths[2].write_bytes(black + b"candidate-c")
    stock_summaries = (
        "戰後東亞校園、整齊制服與軍事化隊列的歷史實拍",
        "歷史教室裡學生一致髮型與制服的實拍",
        "校園教官巡視與高壓紀律的歷史實拍",
    )
    revision_id = Path(str(work_packet["path"])).parent.name
    work_document = json.loads((root / str(work_packet["path"])).read_text(encoding="utf-8"))
    fixture_master = _FakeMaster(
        value=work_document["editorial_master"],
        media_path=root / "editorial-master" / "v1" / "master.mp4",
        srt_path=root / "editorial-master" / "v1" / "master.srt",
    )
    sources = [
        {
            "asset_id": f"stock-{suffix}",
            "source_class": "licensed_stock",
            "provider": "pexels",
            "provider_item_id": f"12{index}",
            "source_url": f"https://www.pexels.com/video/12{index}/",
            "license": "Pexels license: https://www.pexels.com/license/",
            "acquired_at": "2026-08-26T01:00:00Z",
            "semantic_summary": summary,
            "original_media": _identity(root, media),
        }
        for index, (suffix, summary, media) in enumerate(
            zip(("a", "b", "c"), stock_summaries, stock_paths, strict=True), 1
        )
    ]
    sources.extend(extra_sources or [])
    phase = "asset_acquisition-001"
    manifest_path = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "jobs"
        / revision_id
        / "workers"
        / "asset-acquisition"
        / f"{phase}-proposal.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "contract": "podcast-highlight-asset-acquisition-proposal-v1",
                "episode_id": root.name,
                "cut_id": "value-L01",
                "revision_id": revision_id,
                "attempt": 1,
                "assets": sources,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    execution_receipt = _write_trusted_execution_receipt(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        phase=phase,
        role="asset_acquisition",
        worker_identity=ASSET_WORKER,
        proposal_path=manifest_path,
    )
    publish_asset_authority(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        attempt=1,
        assets=sources,
        worker_identity=ASSET_WORKER,
        acquisition_manifest=manifest_path,
        execution_receipt=execution_receipt,
        editorial_master=fixture_master,
    )
    authority = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "revisions"
        / revision_id
        / "attempts"
        / "attempt-001"
        / "ASSET-AUTHORITY.json"
    )
    authority_rows = {
        row["asset_id"]: row for row in json.loads(authority.read_text(encoding="utf-8"))["assets"]
    }
    return stock_paths, stock_summaries, authority_rows


def _dp_proposal(
    root: Path,
    director: object,
    *,
    extra_authority_sources: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    assets = root / "highlights" / "visual-pipeline" / "value-L01" / "proposal-assets"
    assets.mkdir(parents=True, exist_ok=True)
    fixture_dir = Path(__file__).parent / "fixtures" / "davinci_import"
    card = assets / "education-change-card.mp4"
    card.write_bytes((fixture_dir / "black10s.mp4").read_bytes() + b"hf-card")
    title_card = assets / "high-pressure-hero-title.mp4"
    title_card.write_bytes((fixture_dir / "black10s.mp4").read_bytes() + b"hf-title")
    render_receipt = assets / "education-change-render.json"
    render_receipt.write_text('{"renderer":"hyperframes","version":"1"}', encoding="utf-8")
    events = {
        event["event_id"]: event
        for event in director.document["events"]
        if event["decision"] == "add_visual"
    }
    render_params = {
        "kicker": "教育",
        "title": "教育開始\n改變",
        "style": "paper",
        "show_sec": 4.0,
    }
    render_spec_sha256 = hashlib.sha256(
        json.dumps(
            {"component": "transition_title", "render_params": render_params},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    title_params = {
        "text": "高壓教育\n留下深遠影響",
        "tier": 1,
        "style": "orange",
        "show_sec": 4.0,
        "pos_y": 0.4,
    }
    title_spec_sha256 = hashlib.sha256(
        json.dumps(
            {"component": "punch_card_wide", "render_params": title_params},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    stock_paths, stock_summaries, authority_rows = _publish_fixture_asset_authority(
        root,
        director.document["work_packet"],
        extra_sources=extra_authority_sources,
    )
    proposal = {
        "contract": "podcast-highlight-dp-fulfillment-v1",
        "episode_id": root.name,
        "cut_id": "value-L01",
        "director_plan": director.identity(),
        "implementations": [
            {
                "event_id": "visual-001",
                "director_intent_sha256": _intent_hash(events["visual-001"]),
                "mode": "stock",
                "target_lane": "broll_track2",
                "implementation_kind": "stock_video",
                "on_screen_text": None,
                "candidates": [
                    {
                        "candidate_id": f"stock-{suffix}",
                        "authority_asset_id": f"stock-{suffix}",
                        "visual_summary": summary,
                        "media": authority_rows[f"stock-{suffix}"]["original_media"],
                        "provenance": {
                            "kind": "stock_source",
                            "provider": "pexels",
                            "source_url": f"https://www.pexels.com/video/12{index}/",
                            "license": "Pexels license: https://www.pexels.com/license/",
                            "receipt": authority_rows[f"stock-{suffix}"][
                                "acquisition_receipt"
                            ],
                        },
                    }
                    for index, (suffix, summary, media) in enumerate(
                        zip(
                            ("a", "b", "c"),
                            stock_summaries,
                            stock_paths,
                            strict=True,
                        ),
                        1,
                    )
                ],
                "selections": [
                    {
                        "candidate_id": "stock-a",
                        "cue_ids": [1],
                        "t0": 0.0,
                        "t1": 2.5,
                        "quote": "戰後教育強調服從",
                        "source_range": {"start_sec": 0.0, "end_sec": 2.5},
                    },
                    {
                        "candidate_id": "stock-b",
                        "cue_ids": [1],
                        "t0": 2.5,
                        "t1": 4.933,
                        "quote": "戰後教育強調服從",
                        "source_range": {"start_sec": 0.0, "end_sec": 2.433},
                    },
                    {
                        "candidate_id": "stock-c",
                        "cue_ids": [2],
                        "t0": 4.933,
                        "t1": 6.0,
                        "quote": "學生要剃平頭，學校裡還有教官",
                        "source_range": {"start_sec": 0.0, "end_sec": 1.067},
                    },
                ],
                "semantic_justification": (
                    "畫面直接呈現歷史校園紀律，而不是泛用的現代兒童學習情境。"
                ),
            },
            {
                "event_id": "title-002",
                "director_intent_sha256": _intent_hash(events["title-002"]),
                "mode": "hyperframes",
                "target_lane": "title_track3",
                "implementation_kind": "hero_title",
                "on_screen_text": "高壓教育\n留下深遠影響",
                "candidates": [
                    {
                        "candidate_id": "title-a",
                        "visual_summary": "完整雙行 Hero Title，第一行高壓教育、第二行留下深遠影響",
                        "component": "punch_card_wide",
                        "render_params": title_params,
                        "render_spec_sha256": title_spec_sha256,
                        "preview_media": _identity(root, title_card),
                        "provenance": {
                            "kind": "hyperframes_render",
                            "provider": "Nakama trusted HyperFrames renderer",
                            "source_url": None,
                            "license": "Nakama original composition render",
                            "receipt": _identity(root, render_receipt),
                        },
                    }
                ],
                "selections": [
                    {
                        "candidate_id": "title-a",
                        "cue_ids": [3],
                        "t0": 6.0,
                        "t1": 10.0,
                        "quote": "高壓教育留下深遠影響",
                        "source_range": {"start_sec": 0.0, "end_sec": 4.0},
                    }
                ],
                "semantic_justification": (
                    "已預先 render 並確認雙行 Hero Title 是完整句子且換行正確。"
                ),
            },
            {
                "event_id": "visual-002",
                "director_intent_sha256": _intent_hash(events["visual-002"]),
                "mode": "hyperframes",
                "target_lane": "content_card_track4",
                "implementation_kind": "transition_title",
                "on_screen_text": "教育開始\n改變",
                "candidates": [
                    {
                        "candidate_id": "card-a",
                        "visual_summary": "教育開始改變的滿版章節卡",
                        "component": "transition_title",
                        "render_params": render_params,
                        "render_spec_sha256": render_spec_sha256,
                        "preview_media": _identity(root, card),
                        "provenance": {
                            "kind": "hyperframes_render",
                            "provider": "Nakama trusted HyperFrames renderer",
                            "source_url": None,
                            "license": "Nakama original composition render",
                            "receipt": _identity(root, render_receipt),
                        },
                    }
                ],
                "selections": [
                    {
                        "candidate_id": "card-a",
                        "cue_ids": [5],
                        "t0": 14.0,
                        "t1": 18.0,
                        "quote": "直到後來教育才逐漸改變",
                        "source_range": {"start_sec": 0.0, "end_sec": 4.0},
                    }
                ],
                "semantic_justification": "已完成可觀看的章節卡 render，文字直接對應教育制度轉變。",
            },
        ],
    }
    return proposal


def test_dp_fulfillment_binds_stock_and_rendered_hyperframes_media(tmp_path: Path) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    director = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=work.document["revision_id"],
        proposal=_director_proposal(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )

    accepted = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=work.document["revision_id"],
        proposal=_dp_proposal(root, director),
        worker_identity=DP_WORKER,
        editorial_master=master,
    )

    assert len(accepted.document["implementations"]) == 3
    assert (
        load_dp_fulfillment(root, cut_id="value-L01", editorial_master=master).identity()
        == accepted.identity()
    )
    assert (
        visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)["status"]
        == "awaiting_semantic_audit"
    )


def test_provided_photo_can_hold_longer_than_its_source_probe_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    director_proposal = _director_proposal(work)
    photo_event = director_proposal["events"][2]
    photo_event.update(
        {
            "category": "self_archive",
            "form": "overlay",
            "description": "官方提供的教育現場照片，以靜止畫面保留來賓說明時間",
            "on_screen_text": None,
            "search_angles": [],
            "rationale": "官方照片直接對應逐字稿中的教育現場，應以 freeze-frame 顯示。",
        }
    )
    director = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=work.document["revision_id"],
        proposal=director_proposal,
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )
    photo = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "proposal-assets"
        / "official.jpg"
    )
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"official-jpeg-source-bytes")
    summary = "官方提供的教育現場靜照，畫面內容與逐字稿描述一致"
    source = {
        "asset_id": "official-photo",
        "source_class": "provided_self_archive",
        "provider": "official-guest-source",
        "provider_item_id": "official-photo-01",
        "source_url": "https://example.test/official-photo-01",
        "license": "Official guest-provided editorial asset",
        "acquired_at": "2026-08-27T01:00:00Z",
        "semantic_summary": summary,
        "original_media": _identity(root, photo),
    }
    monkeypatch.setattr(
        visual_contract_module,
        "probe_stock_video",
        lambda path: {
            "duration_seconds": 0.04 if Path(path).suffix.lower() == ".jpg" else 10.0,
            "video_streams": [
                {"index": 0, "codec_name": "mjpeg", "width": 1920, "height": 1080}
            ],
        },
    )
    proposal = _dp_proposal(root, director, extra_authority_sources=[source])
    authority_path = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "revisions"
        / str(work.document["revision_id"])
        / "attempts"
        / "attempt-001"
        / "ASSET-AUTHORITY.json"
    )
    authority = {
        row["asset_id"]: row
        for row in json.loads(authority_path.read_text(encoding="utf-8"))["assets"]
    }
    implementation = proposal["implementations"][2]
    implementation.update(
        {
            "mode": "provided_asset",
            "target_lane": "broll_track2",
            "implementation_kind": "photo",
            "on_screen_text": None,
            "candidates": [
                {
                    "candidate_id": "official-photo",
                    "authority_asset_id": "official-photo",
                    "visual_summary": summary,
                    "media": authority["official-photo"]["original_media"],
                    "provenance": {
                        "kind": "provided_source",
                        "provider": "official-guest-source",
                        "source_url": "https://example.test/official-photo-01",
                        "license": "Official guest-provided editorial asset",
                        "receipt": authority["official-photo"]["acquisition_receipt"],
                    },
                }
            ],
            "selections": [
                {
                    "candidate_id": "official-photo",
                    "cue_ids": [5],
                    "t0": 14.0,
                    "t1": 18.0,
                    "quote": "直到後來教育才逐漸改變",
                    "source_range": {"start_sec": 0.0, "end_sec": 0.04},
                }
            ],
            "semantic_justification": "官方提供照片直接對應教育現場，保留四秒讓觀眾辨識細節。",
        }
    )

    out_of_bounds = deepcopy(proposal)
    out_of_bounds["implementations"][2]["selections"][0]["source_range"]["end_sec"] = 0.05
    _assert_contract_error(
        lambda: accept_dp_fulfillment(
            root,
            cut_id="value-L01",
            revision_id=work.document["revision_id"],
            proposal=out_of_bounds,
            worker_identity=DP_WORKER,
            editorial_master=master,
        ),
        "selected source range exceeds media duration",
    )

    accepted = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=work.document["revision_id"],
        proposal=proposal,
        worker_identity=DP_WORKER,
        editorial_master=master,
    )

    photo_materialization = load_visual_materializations(
        root,
        cut_id="value-L01",
        revision_id=work.document["revision_id"],
        editorial_master=master,
    )[-1]
    assert accepted.document["implementations"][2]["implementation_kind"] == "photo"
    assert photo_materialization["source_range"] == {"start_sec": 0.0, "end_sec": 0.04}
    assert photo_materialization["t1"] - photo_materialization["t0"] == pytest.approx(4.0)

def test_dp_rejects_lineage_candidate_timing_media_and_target_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = work.document["revision_id"]
    director = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_director_proposal(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )

    def reject(proposal: dict[str, object], contains: str) -> None:
        _assert_contract_error(
            lambda: accept_dp_fulfillment(
                root,
                cut_id="value-L01",
                revision_id=revision_id,
                proposal=proposal,
                worker_identity=DP_WORKER,
                editorial_master=master,
            ),
            contains,
        )

    _assert_contract_error(
        lambda: accept_dp_fulfillment(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=_dp_proposal(root, director),
            worker_identity={
                **DP_WORKER,
                "session_id": DIRECTOR_WORKER["session_id"],
            },
            editorial_master=master,
        ),
        "must be distinct from Director",
    )

    proposal = _dp_proposal(root, director)
    proposal["episode_id"] = "another-episode"
    reject(proposal, "another contract/episode/cut")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][0]["director_intent_sha256"] = "f" * 64
    reject(proposal, "changed Director intent")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][0]["candidates"] = proposal["implementations"][0]["candidates"][:2]
    reject(proposal, "A/B/C")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][0]["candidates"][1]["provenance"]["source_url"] = proposal[
        "implementations"
    ][0]["candidates"][0]["provenance"]["source_url"]
    reject(proposal, "provenance differs from trusted acquisition authority")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][0]["candidates"][0]["provenance"].pop("license")
    reject(proposal, "provenance differs from trusted acquisition authority")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][1]["target_lane"] = "content_card_track4"
    reject(proposal, "not valid for target_lane")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][1]["on_screen_text"] = "不完整標題"
    reject(proposal, "changed exact on_screen_text")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][1]["event_id"] = "visual-001"
    reject(proposal, "coverage is missing, duplicated, or unknown")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][0]["selections"][0]["candidate_id"] = "absent-stock"
    reject(proposal, "selected candidate is absent or reused")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][0]["selections"][1]["candidate_id"] = "stock-a"
    reject(proposal, "selected candidate is absent or reused")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][0]["selections"][0]["cue_ids"] = [2]
    proposal["implementations"][0]["selections"][0]["quote"] = "學生要剃平頭，學校裡還有教官"
    reject(proposal, "timeline subrange")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][0]["selections"][1]["t0"] = 2.6
    proposal["implementations"][0]["selections"][1]["source_range"]["end_sec"] = 2.333
    reject(proposal, "tile the exact Director event range")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][0]["selections"][1]["t0"] = 2.4
    proposal["implementations"][0]["selections"][1]["source_range"]["end_sec"] = 2.533
    reject(proposal, "tile the exact Director event range")

    proposal = _dp_proposal(root, director)
    first = proposal["implementations"][0]["selections"][0]
    second = proposal["implementations"][0]["selections"][1]
    first["t1"] = 3.1
    first["source_range"]["end_sec"] = 3.1
    second["t0"] = 3.1
    second["source_range"]["end_sec"] = 1.833
    reject(proposal, "exceeds the 3s")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][0]["selections"][0]["source_range"]["end_sec"] = 2.4
    reject(proposal, "match exact timeline display duration")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][0]["candidates"][0]["media"]["path"] = "../../escape.mp4"
    reject(proposal, "media differs from trusted original acquisition bytes")

    proposal = _dp_proposal(root, director)
    fake = root / "highlights" / "visual-pipeline" / "value-L01" / "proposal-assets" / "fake.mp4"
    fake.write_bytes(b"not a playable video")
    proposal["implementations"][0]["candidates"][0]["media"] = _identity(root, fake)
    reject(proposal, "media differs from trusted original acquisition bytes")

    proposal = _dp_proposal(root, director)
    stock_candidates = proposal["implementations"][0]["candidates"]
    copied_to = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "proposal-assets"
        / "copied-stock-under-another-path.mp4"
    )
    copied_from = root / stock_candidates[0]["media"]["path"]
    copied_to.write_bytes(copied_from.read_bytes())
    stock_candidates[1]["media"] = _identity(root, copied_to)
    reject(proposal, "media differs from trusted original acquisition bytes")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][1]["candidates"][0]["render_spec_sha256"] = "0" * 64
    reject(proposal, "render spec hash mismatch")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][1]["candidates"][0]["provenance"][
        "provider"
    ] = "worker self-authored HyperFrames"
    reject(proposal, "not a trusted HyperFrames render")

    with monkeypatch.context() as context:
        context.setattr(
            visual_contract_module,
            "verify_hyperframes_render_receipt",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                visual_contract_module.TrustedRenderError("forged trusted receipt")
            ),
        )
        reject(_dp_proposal(root, director), "trusted HyperFrames receipt failed")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][1]["candidates"][0]["preview_media"]["sha256"] = "0" * 64
    reject(proposal, "hash drift")

    proposal = _dp_proposal(root, director)
    receipt_identity = proposal["implementations"][1]["candidates"][0]["provenance"]["receipt"]
    receipt = root / receipt_identity["path"]
    receipt.write_text('{"renderer":"tampered"}', encoding="utf-8")
    reject(proposal, "byte size drift")

    accepted = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_dp_proposal(root, director),
        worker_identity=DP_WORKER,
        editorial_master=master,
    )
    materializations = load_visual_materializations(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        editorial_master=master,
    )
    assert accepted.document["worker_execution"] == DP_WORKER
    assert len(materializations) == 5
    assert materializations[0]["t0"] == 0.0
    assert materializations[1]["t0"] == 2.5


def _audit_proposal(director: object, dp: object) -> dict[str, object]:
    events = {event["event_id"]: event for event in director.document["events"]}
    implementations = {item["event_id"]: item for item in dp.document["implementations"]}
    findings = []
    for event_id in ("visual-001", "title-002", "visual-002"):
        event = events[event_id]
        implementation = implementations[event_id]
        candidates = {
            candidate["candidate_id"]: candidate for candidate in implementation["candidates"]
        }
        for index, selection in enumerate(implementation["selections"], 1):
            candidate = candidates[selection["candidate_id"]]
            media = (
                candidate["preview_media"]
                if implementation["mode"] == "hyperframes"
                else candidate["media"]
            )
            findings.append(
                {
                    "materialization_id": f"{event_id}-s{index:02d}",
                    "event_id": event_id,
                    "director_intent_sha256": _intent_hash(event),
                    "cue_ids": selection["cue_ids"],
                    "t0": selection["t0"],
                    "t1": selection["t1"],
                    "quote": selection["quote"],
                    "source_range": selection["source_range"],
                    "evidence_sha256": media["sha256"],
                    "visual_observation": (
                        "實際預覽顯示歷史校園紀律、制服或教官，沒有現代幼兒遊戲教具。"
                        if event_id == "visual-001"
                        else "實際 render 顯示完整且換行正確的標題文字，沒有不完整片語。"
                    ),
                    "verdict": "match",
                    "rationale": "逐一檢視實際影片或預先 render 的畫面後，語意與負面限制均符合。",
                }
            )
    return {
        "contract": "podcast-highlight-visual-semantic-audit-v1",
        "episode_id": director.document["episode_id"],
        "cut_id": director.document["cut_id"],
        "director_plan": director.identity(),
        "dp_fulfillment": dp.identity(),
        "findings": findings,
    }


def test_canonical_dp_binds_and_freshly_rechecks_raw_hydration_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = str(work.document["revision_id"])
    job_root = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "jobs"
        / revision_id
    )
    director_path = job_root / "workers" / "director-session" / "director-proposal.json"
    director_path.parent.mkdir(parents=True, exist_ok=True)
    director_path.write_text(
        json.dumps(_director_proposal(work), ensure_ascii=False), encoding="utf-8"
    )
    director_receipt = _write_trusted_execution_receipt(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        phase="director",
        role="director",
        worker_identity=DIRECTOR_WORKER,
        proposal_path=director_path,
    )
    monkeypatch.setattr(
        visual_contract_module,
        "_require_trusted_execution_receipt",
        _REAL_REQUIRE_TRUSTED_EXECUTION_RECEIPT,
    )
    monkeypatch.setattr(
        visual_contract_module,
        "_accepted_dp_hydration_lineage",
        _REAL_ACCEPTED_DP_HYDRATION_LINEAGE,
    )
    monkeypatch.setattr(
        visual_contract_module,
        "_verify_canonical_dp_hydration",
        _REAL_VERIFY_CANONICAL_DP_HYDRATION,
    )
    director = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=director_path,
        worker_identity=DIRECTOR_WORKER,
        execution_receipt=director_receipt,
        editorial_master=master,
    )
    raw_path = job_root / "workers" / "dp-session" / "dp-proposal.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(_dp_proposal(root, director), ensure_ascii=False), encoding="utf-8"
    )
    hydrated_path = job_root / "trusted" / "dp-proposal.json"
    hydrated_path.parent.mkdir(parents=True, exist_ok=True)
    hydrated_path.write_text(raw_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    sidecar = hydrated_path.with_name(f"{hydrated_path.name}.hydration.json")
    sidecar.write_text('{"trusted":"hydration"}\n', encoding="utf-8")
    dp_receipt = _write_trusted_execution_receipt(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        phase="dp",
        role="dp",
        worker_identity=DP_WORKER,
        proposal_path=raw_path,
    )
    calls: list[dict[str, object]] = []

    def hydration_identity(episode_root: object, _hydrated: object) -> dict[str, object]:
        assert Path(episode_root) == root
        return _identity(root, sidecar)

    def verify_hydration(episode_root: object, **kwargs: object) -> dict[str, object]:
        assert Path(episode_root) == root
        try:
            visual_contract_module._validate_file_identity(
                root, kwargs["receipt_identity"], "test hydration receipt"
            )
            visual_contract_module._validate_file_identity(
                root, kwargs["expected_raw_proposal"], "test raw proposal"
            )
            visual_contract_module._validate_file_identity(
                root, kwargs["expected_hydrated_proposal"], "test hydrated proposal"
            )
        except HighlightVisualContractError as error:
            raise visual_contract_module.TrustedRenderError(str(error)) from error
        assert kwargs["expected_attempt"] == 1
        calls.append(dict(kwargs))
        return {
            "contract": "trusted-test-hydration",
            "raw_proposal_document": json.loads(raw_path.read_text(encoding="utf-8")),
            "hydrated_proposal_document": json.loads(
                hydrated_path.read_text(encoding="utf-8")
            ),
        }

    monkeypatch.setattr(
        visual_contract_module, "dp_hydration_receipt_identity", hydration_identity
    )
    monkeypatch.setattr(visual_contract_module, "verify_dp_hydration_receipt", verify_hydration)
    raw_bytes = raw_path.read_bytes()
    swapped_raw = json.loads(raw_bytes)
    swapped_raw["implementations"][0]["semantic_justification"] = (
        "worker proposal B was swapped in after execution receipt bound proposal A"
    )
    raw_path.write_text(json.dumps(swapped_raw, ensure_ascii=False), encoding="utf-8")
    _assert_contract_error(
        lambda: accept_dp_fulfillment(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=hydrated_path,
            worker_proposal=raw_path,
            worker_identity=DP_WORKER,
            execution_receipt=dp_receipt,
            editorial_master=master,
        ),
        "trusted execution proposal identity drift",
    )
    raw_path.write_bytes(raw_bytes)
    accepted = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=hydrated_path,
        worker_proposal=raw_path,
        worker_identity=DP_WORKER,
        execution_receipt=dp_receipt,
        editorial_master=master,
    )

    assert accepted.document["worker_proposal"] == _identity(root, raw_path)
    assert accepted.document["hydrated_proposal"] == _identity(root, hydrated_path)
    assert accepted.document["hydration_receipt"] == _identity(root, sidecar)
    assert len(calls) >= 2  # acceptance and canonical reload both verify fresh bytes

    canonical_path = root / str(accepted.identity()["path"])
    canonical_bytes = canonical_path.read_bytes()
    canonical = json.loads(canonical_bytes)
    canonical["implementations"][0]["semantic_justification"] = (
        "self-consistently rehashed canonical content that was never hydrated"
    )
    canonical["content_hash"] = _content_hash(
        {key: value for key, value in canonical.items() if key != "content_hash"}
    )
    canonical_path.write_text(json.dumps(canonical, ensure_ascii=False), encoding="utf-8")
    _assert_contract_error(
        lambda: load_dp_fulfillment(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            editorial_master=master,
        ),
        "differs from its verified hydrated proposal",
    )
    canonical_path.write_bytes(canonical_bytes)

    sidecar.write_text('{"trusted":"tampered"}\n', encoding="utf-8")
    _assert_contract_error(
        lambda: load_dp_fulfillment(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            editorial_master=master,
        ),
        "hydration lineage is stale",
    )


def test_director_semantic_audit_is_required_before_materialization(tmp_path: Path) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    director = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=work.document["revision_id"],
        proposal=_director_proposal(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )
    dp = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=work.document["revision_id"],
        proposal=_dp_proposal(root, director),
        worker_identity=DP_WORKER,
        editorial_master=master,
    )

    audit = accept_semantic_audit(
        root,
        cut_id="value-L01",
        revision_id=work.document["revision_id"],
        proposal=_audit_proposal(director, dp),
        worker_identity=AUDIT_WORKER,
        editorial_master=master,
    )

    assert (
        load_semantic_audit(root, cut_id="value-L01", editorial_master=master).identity()
        == audit.identity()
    )
    pipeline = verify_visual_pipeline(root, cut_id="value-L01", editorial_master=master)
    lineage = verify_visual_lineage(
        root,
        "value-L01",
        cut_format="long",
        items=list(pipeline.materializations),
        editorial_master_lineage=master.identity(),
        editorial_master=master,
    )
    assert len(lineage["materializations"]) == 5
    assert {item["target_lane"] for item in lineage["materializations"]} == {
        "broll_track2",
        "content_card_track4",
        "title_track3",
    }
    assert (
        visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)["status"]
        == "ready_to_materialize"
    )


def test_semantic_mismatch_is_an_immutable_refinement_not_a_current_acceptance(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = work.document["revision_id"]
    director = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_director_proposal(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )
    dp = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_dp_proposal(root, director),
        worker_identity=DP_WORKER,
        editorial_master=master,
    )
    proposal = _audit_proposal(director, dp)
    proposal["findings"][0]["verdict"] = "mismatch"
    proposal["findings"][0]["rationale"] = (
        "畫面是現代幼兒教具，無法表達戰後高壓教育、剃平頭與教官制度。"
    )

    refinement = accept_semantic_audit(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=proposal,
        worker_identity=AUDIT_WORKER,
        editorial_master=master,
    )

    assert refinement.document["contract"] == (
        "podcast-highlight-visual-semantic-refinement-v1"
    )
    assert refinement.document["attempt"] == 1
    assert refinement.document["mismatch_count"] == 1
    assert refinement.document["uncertain_count"] == 0
    assert not (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "revisions"
        / revision_id
        / "SEMANTIC-AUDIT.json"
    ).exists()
    assert not (
        root / "highlights" / "visual-pipeline" / "value-L01" / "CURRENT.json"
    ).exists()
    status = visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)
    assert status["status"] == "awaiting_refinement_decision"
    assert status["active_dp_attempt"] == 1
    assert status["refinement_input"] == refinement.identity()


def test_all_match_audit_crash_before_current_pointer_recovers_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = str(work.document["revision_id"])
    director = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_director_proposal(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )
    dp = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_dp_proposal(root, director),
        worker_identity=DP_WORKER,
        editorial_master=master,
    )
    proposal = _audit_proposal(director, dp)
    original_write_pointer = visual_contract_module._write_pointer

    def crash_before_current(*args: object, **kwargs: object) -> None:
        if kwargs.get("name") == "CURRENT.json":
            raise RuntimeError("simulated crash before CURRENT pointer")
        original_write_pointer(*args, **kwargs)

    monkeypatch.setattr(visual_contract_module, "_write_pointer", crash_before_current)
    with pytest.raises(RuntimeError, match="before CURRENT"):
        accept_semantic_audit(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=proposal,
            worker_identity=AUDIT_WORKER,
            editorial_master=master,
        )
    audit_path = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "revisions"
        / revision_id
        / "SEMANTIC-AUDIT.json"
    )
    assert audit_path.is_file()
    assert not (
        root / "highlights" / "visual-pipeline" / "value-L01" / "CURRENT.json"
    ).exists()

    monkeypatch.setattr(visual_contract_module, "_write_pointer", original_write_pointer)
    recovery = visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)
    assert recovery["status"] == "awaiting_semantic_audit"
    assert recovery["unpublished_semantic_audit"]["path"].endswith("SEMANTIC-AUDIT.json")
    accept_semantic_audit(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=proposal,
        worker_identity=AUDIT_WORKER,
        editorial_master=master,
    )
    assert visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)[
        "status"
    ] == "ready_to_materialize"


def _refinement_decision_proposal(
    refinement: object, *, action: str = "retry_dp"
) -> dict[str, object]:
    failed = [row for row in refinement.document["findings"] if row["verdict"] != "match"]
    return {
        "contract": "podcast-highlight-visual-refinement-decision-v1",
        "episode_id": refinement.document["episode_id"],
        "cut_id": refinement.document["cut_id"],
        "attempt": refinement.document["attempt"],
        "semantic_refinement": refinement.identity(),
        "decisions": [
            {
                "materialization_id": row["materialization_id"],
                "event_id": row["event_id"],
                "action": action,
                "rationale": (
                    "素材本身可替換，DP 應重新找一支直接呈現逐字稿場景且避開負面限制的畫面。"
                    if action == "retry_dp"
                    else (
                        "目前沒有可核對的原始素材，不能讓 DP 用合成卡冒充，"
                        "必須回到 Director 改稿。"
                    )
                ),
            }
            for row in failed
        ],
    }


def _dp2_proposal(root: Path, director: object) -> dict[str, object]:
    proposal = _dp_proposal(root, director)
    fixture = Path(__file__).parent / "fixtures" / "davinci_import" / "black10s.mp4"
    replacement = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "proposal-assets"
        / "postwar-school-a-refined.mp4"
    )
    replacement.write_bytes(fixture.read_bytes() + b"semantic-refinement-a")
    candidate = proposal["implementations"][0]["candidates"][0]
    candidate["candidate_id"] = "stock-a-v2"
    candidate["authority_asset_id"] = "stock-a-v2"
    candidate["media"] = _identity(root, replacement)
    candidate["visual_summary"] = "戰後校園升旗隊列、平頭學生與教官巡視的可核對歷史實拍"
    candidate["provenance"]["source_url"] = "https://www.pexels.com/video/99123/"
    proposal["implementations"][0]["selections"][0]["candidate_id"] = "stock-a-v2"
    revision_id = Path(str(director.document["work_packet"]["path"])).parent.name
    work_document = json.loads(
        (root / str(director.document["work_packet"]["path"])).read_text(encoding="utf-8")
    )
    fixture_master = _FakeMaster(
        value=work_document["editorial_master"],
        media_path=root / "editorial-master" / "v1" / "master.mp4",
        srt_path=root / "editorial-master" / "v1" / "master.srt",
    )
    stock_candidates = proposal["implementations"][0]["candidates"]
    publish_asset_authority(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        attempt=2,
        assets=[
            {
                "asset_id": row["candidate_id"],
                "source_class": "licensed_stock",
                "provider": row["provenance"]["provider"],
                "provider_item_id": row["provenance"]["source_url"].rstrip("/").split("/")[-1],
                "source_url": row["provenance"]["source_url"],
                "license": row["provenance"]["license"],
                "acquired_at": "2026-08-26T02:00:00Z",
                "semantic_summary": row["visual_summary"],
                "original_media": row["media"],
            }
            for row in stock_candidates
            if row["candidate_id"] == "stock-a-v2"
        ],
        worker_identity=ASSET2_WORKER,
        editorial_master=fixture_master,
    )
    authority_dir = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "revisions"
        / revision_id
        / "attempts"
    )
    authority_rows: dict[str, dict[str, object]] = {}
    for authority_attempt in (1, 2):
        authority_path = (
            authority_dir / f"attempt-{authority_attempt:03d}" / "ASSET-AUTHORITY.json"
        )
        authority_rows.update(
            {
                row["asset_id"]: row
                for row in json.loads(authority_path.read_text(encoding="utf-8"))["assets"]
            }
        )
    for row in stock_candidates:
        authority = authority_rows[row["candidate_id"]]
        row["authority_asset_id"] = authority["asset_id"]
        row["media"] = authority["original_media"]
        row["provenance"] = {
            "kind": "stock_source",
            "provider": authority["provider"],
            "source_url": authority["source_url"],
            "license": authority["license"],
            "receipt": authority["acquisition_receipt"],
        }
    return proposal


def test_mismatch_dp2_and_same_director_reaudit_activate_one_current(tmp_path: Path) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = work.document["revision_id"]
    director = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_director_proposal(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )
    dp1 = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_dp_proposal(root, director),
        worker_identity=DP_WORKER,
        editorial_master=master,
    )
    audit1 = _audit_proposal(director, dp1)
    audit1["findings"][0]["verdict"] = "mismatch"
    audit1["findings"][0]["rationale"] = "幼兒教具與戰後高壓教育的具體敘述相反，必須替換素材。"
    refinement = accept_semantic_audit(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=audit1,
        worker_identity=AUDIT_WORKER,
        editorial_master=master,
    )
    decision = accept_refinement_decision(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        attempt=1,
        proposal=_refinement_decision_proposal(refinement),
        worker_identity=REFINEMENT_WORKER,
        editorial_master=master,
    )
    status = visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)
    assert status["status"] == "awaiting_dp_refinement"
    assert status["refinement_decision"] == decision.identity()
    assert status["next_dp_attempt"] == 2

    copied_original = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "proposal-assets"
        / "postwar-school-a-renamed-copy.mp4"
    )
    copied_original.write_bytes(
        (
            root
            / "highlights"
            / "visual-pipeline"
            / "value-L01"
            / "proposal-assets"
            / "postwar-school-a.mp4"
        ).read_bytes()
    )
    _assert_contract_error(
        lambda: publish_asset_authority(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            attempt=2,
            assets=[
                _trusted_asset_source(
                    root,
                    copied_original,
                    asset_id="stock-a-renamed-copy",
                    provider_item_id="99999",
                    semantic_summary="改名後仍是同一份原始素材 bytes，不能繞過跨次嘗試去重",
                )
            ],
            worker_identity=ASSET2_WORKER,
            editorial_master=master,
        ),
        "reuses original source across attempts",
    )

    dp2 = accept_dp_refinement(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        attempt=2,
        proposal=_dp2_proposal(root, director),
        worker_identity=DP2_WORKER,
        editorial_master=master,
    )
    status = visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)
    assert status["status"] == "awaiting_semantic_audit"
    assert status["active_dp_attempt"] == 2

    audit2 = accept_semantic_audit(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_audit_proposal(director, dp2),
        worker_identity=AUDIT2_WORKER,
        editorial_master=master,
    )
    pipeline = verify_visual_pipeline(root, cut_id="value-L01", editorial_master=master)
    assert pipeline.dp_fulfillment.identity() == dp2.identity()
    assert pipeline.semantic_audit.identity() == audit2.identity()
    assert visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)[
        "status"
    ] == "ready_to_materialize"
    attempts = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "revisions"
        / revision_id
        / "attempts"
    )
    assert sorted(path.name for path in attempts.iterdir()) == ["attempt-001", "attempt-002"]


def test_infeasible_visual_can_be_replanned_to_intentional_aroll_in_same_revision(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = work.document["revision_id"]
    director1 = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_director_proposal(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )
    dp1 = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_dp_proposal(root, director1),
        worker_identity=DP_WORKER,
        editorial_master=master,
    )
    audit1 = _audit_proposal(director1, dp1)
    audit1["findings"][3]["verdict"] = "mismatch"
    audit1["findings"][3]["rationale"] = (
        "找不到能核對的自有檔案或示範畫面，不能用一般概念卡冒充實證素材。"
    )
    refinement = accept_semantic_audit(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=audit1,
        worker_identity=AUDIT_WORKER,
        editorial_master=master,
    )
    accept_refinement_decision(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        attempt=1,
        proposal=_refinement_decision_proposal(refinement, action="director_replan"),
        worker_identity=REFINEMENT_WORKER,
        editorial_master=master,
    )
    status = visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)
    assert status["status"] == "requires_director_replan"

    proposal = _director_proposal(work)
    title = proposal["events"][1]
    title.update(
        {
            "category": "none",
            "form": "aroll",
            "description": "沒有可信來源時保留來賓原畫面，不以合成卡冒充證據",
            "on_screen_text": None,
            "negative_constraints": ["不可用概念卡冒充自有檔案或畫面證據"],
            "search_angles": [],
            "decision": "intentional_aroll",
            "rationale": "稽核確認素材不可得，因此明確保留 A-roll，避免製造錯誤視覺證據。",
        }
    )
    proposal["coverage"].update(
        {
            "add_visual_count": 2,
            "planned_visual_count": 4,
            "intentional_aroll_count": 2,
            "visual_events_per_minute": 10.0,
        }
    )
    director2 = accept_director_replan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        attempt=2,
        proposal=proposal,
        worker_identity=DIRECTOR_REPLAN_WORKER,
        editorial_master=master,
    )
    status = visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)
    assert status["status"] == "awaiting_dp_refinement"
    assert status["director_replan"] == director2.identity()

    dp2_proposal = _dp_proposal(root, director1)
    dp2_proposal["director_plan"] = director2.identity()
    dp2_proposal["implementations"] = [
        row for row in dp2_proposal["implementations"] if row["event_id"] != "title-002"
    ]
    dp2 = accept_dp_refinement(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        attempt=2,
        proposal=dp2_proposal,
        worker_identity=DP2_WORKER,
        editorial_master=master,
    )
    audit2_proposal = _audit_proposal(director1, dp1)
    audit2_proposal["director_plan"] = director2.identity()
    audit2_proposal["dp_fulfillment"] = dp2.identity()
    audit2_proposal["findings"] = [
        row for row in audit2_proposal["findings"] if row["event_id"] != "title-002"
    ]
    accept_semantic_audit(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=audit2_proposal,
        worker_identity=AUDIT_AFTER_REPLAN_WORKER,
        editorial_master=master,
    )
    pipeline = verify_visual_pipeline(root, cut_id="value-L01", editorial_master=master)
    assert pipeline.director_plan.identity() == director2.identity()
    assert pipeline.dp_fulfillment.identity() == dp2.identity()


def test_director_replan_crash_before_marker_recovers_without_mutating_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = str(work.document["revision_id"])
    director1 = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_director_proposal(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )
    dp1 = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_dp_proposal(root, director1),
        worker_identity=DP_WORKER,
        editorial_master=master,
    )
    audit1 = _audit_proposal(director1, dp1)
    audit1["findings"][3]["verdict"] = "mismatch"
    audit1["findings"][3]["rationale"] = (
        "沒有可核對的自有檔案，合成概念卡不能冒充實際畫面。"
    )
    refinement = accept_semantic_audit(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=audit1,
        worker_identity=AUDIT_WORKER,
        editorial_master=master,
    )
    accept_refinement_decision(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        attempt=1,
        proposal=_refinement_decision_proposal(refinement, action="director_replan"),
        worker_identity=REFINEMENT_WORKER,
        editorial_master=master,
    )
    proposal = _director_proposal(work)
    proposal["events"][1].update(
        {
            "category": "none",
            "form": "aroll",
            "description": "沒有可信來源時保留來賓，不以合成卡冒充證據",
            "on_screen_text": None,
            "negative_constraints": ["不可用概念卡冒充自有檔案"],
            "search_angles": [],
            "decision": "intentional_aroll",
            "rationale": "稽核確認素材不可得，因此保留 A-roll。",
        }
    )
    proposal["coverage"].update(
        {
            "add_visual_count": 2,
            "planned_visual_count": 4,
            "intentional_aroll_count": 2,
            "visual_events_per_minute": 10.0,
        }
    )
    original_publish = visual_contract_module._atomic_publish

    def crash_before_marker(path: Path, document: dict[str, object]) -> None:
        if path.name == "DIRECTOR-REPLAN.json":
            raise RuntimeError("simulated crash before replan marker")
        original_publish(path, document)

    monkeypatch.setattr(visual_contract_module, "_atomic_publish", crash_before_marker)
    with pytest.raises(RuntimeError, match="before replan marker"):
        accept_director_replan(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            attempt=2,
            proposal=proposal,
            worker_identity=DIRECTOR_REPLAN_WORKER,
            editorial_master=master,
        )
    plan_path = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "revisions"
        / revision_id
        / "attempts"
        / "attempt-002"
        / "DIRECTOR-PLAN.json"
    )
    before = plan_path.read_bytes()
    monkeypatch.setattr(visual_contract_module, "_atomic_publish", original_publish)
    recovery = visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)
    assert recovery["status"] == "requires_director_replan"
    assert recovery["unpublished_director_replan"]["path"].endswith("DIRECTOR-PLAN.json")
    accepted = accept_director_replan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        attempt=2,
        proposal=proposal,
        worker_identity=DIRECTOR_REPLAN_WORKER,
        editorial_master=master,
    )
    assert plan_path.read_bytes() == before
    assert accepted.path == plan_path
    assert visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)[
        "status"
    ] == "awaiting_dp_refinement"


def test_audit_rejects_worker_coverage_evidence_quote_and_nonmatch(tmp_path: Path) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = work.document["revision_id"]
    director = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_director_proposal(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )
    dp = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_dp_proposal(root, director),
        worker_identity=DP_WORKER,
        editorial_master=master,
    )

    def reject(
        proposal: dict[str, object], contains: str, worker: dict[str, str] = AUDIT_WORKER
    ) -> None:
        _assert_contract_error(
            lambda: accept_semantic_audit(
                root,
                cut_id="value-L01",
                revision_id=revision_id,
                proposal=proposal,
                worker_identity=worker,
                editorial_master=master,
            ),
            contains,
        )

    wrong_session = {**AUDIT_WORKER, "session_id": "forged-director-session"}
    reject(_audit_proposal(director, dp), "Director intent owner", wrong_session)

    proposal = _audit_proposal(director, dp)
    proposal["findings"] = proposal["findings"][:-1]
    reject(proposal, "every selected materialization")

    proposal = _audit_proposal(director, dp)
    proposal["findings"][0]["evidence_sha256"] = "e" * 64
    reject(proposal, "evidence_sha256 differs")

    proposal = _audit_proposal(director, dp)
    proposal["findings"][0]["quote"] = "不對應逐字稿的泛稱"
    reject(proposal, "quote differs")

    accepted = accept_semantic_audit(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_audit_proposal(director, dp),
        worker_identity=AUDIT_WORKER,
        editorial_master=master,
    )
    assert len(accepted.document["findings"]) == 5


def test_visual_pipeline_cli_exposes_every_acceptance_step() -> None:
    script = Path(__file__).parents[3] / "scripts" / "podcast_highlight_visual_pipeline.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0
    for command in (
        "init",
        "status",
        "accept-director",
        "accept-dp",
        "accept-audit",
        "verify",
    ):
        assert command in result.stdout


def _complete_generation(root: Path, master: _FakeMaster, work: object) -> object:
    revision_id = work.document["revision_id"]
    director = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_director_proposal(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )
    dp = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_dp_proposal(root, director),
        worker_identity=DP_WORKER,
        editorial_master=master,
    )
    accept_semantic_audit(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_audit_proposal(director, dp),
        worker_identity=AUDIT_WORKER,
        editorial_master=master,
    )
    return verify_visual_pipeline(root, cut_id="value-L01", editorial_master=master)


def _finished_visual_request(
    root: Path,
    *,
    rows: list[dict[str, object]],
    request_id: str = "feedback-visual-002",
) -> Path:
    review = root / "highlights" / "review"
    review.mkdir(parents=True, exist_ok=True)
    components = [
        {
            "component_id": "value-L01-hero-001",
            "lane": "hero_title",
            "t0": 6.0,
            "t1": 10.0,
            "text": "舊的第一張 Hero",
        },
        {
            "component_id": "value-L01-hero-002",
            "lane": "hero_title",
            "t0": 14.0,
            "t1": 18.0,
            "text": "舊的第二張 Hero",
        },
        {
            "component_id": "value-L01-visual-001",
            "lane": "visual_effect",
            "t0": 14.0,
            "t1": 18.0,
            "type": "transition",
            "slug": "old-card",
        },
    ]
    manifest = review / "finished_review_manifest_source.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "nakama.finished_cut_review_manifest.v1",
                "episode_id": root.name,
                "cuts": [{"cut_id": "value-L01", "components": components}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    request = review / "revisions" / request_id / "request.json"
    request.parent.mkdir(parents=True, exist_ok=True)
    request.write_text(
        json.dumps(
            {
                "worker_contract": "finished-cut-revision-worker-v2-stock-required",
                "episode_id": root.name,
                "review_format": "long",
                "manifest_filename": manifest.name,
                "source_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "source_preview_sha256": {"value-L01": "9" * 64},
                "requested_cut_ids": ["value-L01"],
                "component_feedback": rows,
                "overall_feedback": {
                    "value-L01": "這是 creative context，不得變成 deterministic 文字規則。"
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return request


def _hero_feedback_rows() -> list[dict[str, object]]:
    return [
        {
            "cut_id": "value-L01",
            "component_id": "value-L01-hero-001",
            "lane": "hero_title",
            "timeline_seconds": {"t0": 6.0, "t1": 10.0},
            "action": "edit_text",
            "comment": "句子要完整並保留人工換行",
            "replacement": "與其教故事\n不如動手做",
            "remember_preference": False,
        },
        {
            "cut_id": "value-L01",
            "component_id": "value-L01-hero-002",
            "lane": "hero_title",
            "timeline_seconds": {"t0": 14.0, "t1": 18.0},
            "action": "edit_text",
            "comment": "保留完整主張",
            "replacement": "傳統道路\n沒有保證了",
            "remember_preference": False,
        },
    ]


def _absolute_stock_feedback_request(root: Path) -> tuple[Path, Path, Path]:
    fixture = Path(__file__).parent / "fixtures" / "davinci_import" / "black10s.mp4"
    stock = root / "assets" / "broll" / "legacy-stock.mp4"
    stock.parent.mkdir(parents=True, exist_ok=True)
    stock.write_bytes(fixture.read_bytes())
    request = _finished_visual_request(
        root,
        request_id="legacy-absolute-stock",
        rows=[
            {
                "cut_id": "value-L01",
                "component_id": "value-L01-b-roll-001",
                "lane": "b_roll",
                "timeline_seconds": {"t0": 6.0, "t1": 9.0},
                "action": "remove",
                "comment": "淘汰舊 Stock，保留 request-bound bytes 作 deny evidence。",
                "replacement": "",
                "remember_preference": False,
            }
        ],
    )
    manifest_path = root / "highlights" / "review" / "finished_review_manifest_source.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cuts"][0]["components"].append(
        {
            "component_id": "value-L01-b-roll-001",
            "lane": "b_roll",
            "t0": 6.0,
            "t1": 9.0,
            "type": "video",
            "slug": "legacy-stock",
            "asset": {
                "path": str(stock.resolve()),
                "bytes": stock.stat().st_size,
                "sha256": hashlib.sha256(stock.read_bytes()).hexdigest(),
            },
        }
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    request_document = json.loads(request.read_text(encoding="utf-8"))
    request_document["source_manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    request.write_text(json.dumps(request_document, ensure_ascii=False), encoding="utf-8")
    return request, stock, fixture


def test_request_bound_legacy_absolute_stock_path_is_normalized_under_episode(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    request, stock, fixture = _absolute_stock_feedback_request(root)

    prospective = preflight_visual_work_packet(
        root,
        cut_id="value-L01",
        revision_request=request,
        editorial_master=master,
    )

    assert prospective["status"] == "would_initialize"
    source_component = prospective["requested_visual_feedback"]["directives"][0][
        "source_component"
    ]
    assert source_component["asset"] == _identity(root, stock)

    outside = tmp_path / "outside-episode.mp4"
    outside.write_bytes(fixture.read_bytes())
    _assert_contract_error(
        lambda: visual_contract_module._validate_feedback_source_asset_identity(
            root,
            {
                "path": str(outside.resolve()),
                "bytes": outside.stat().st_size,
                "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
            },
            "legacy outside asset",
            media=True,
        ),
        "escapes episode root",
    )


def test_existing_legacy_pending_revision_resumes_with_fresh_removed_stock_evidence(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    request, stock, _ = _absolute_stock_feedback_request(root)
    _, cut_id, modern = visual_contract_module._prospective_work_document(
        root,
        cut_id="value-L01",
        revision_request=request,
        editorial_master=master,
    )
    legacy_feedback = dict(modern["requested_visual_feedback"])
    legacy_feedback["directives"] = [
        {key: value for key, value in row.items() if key != "source_component"}
        for row in legacy_feedback["directives"]
    ]
    legacy_feedback["content_hash"] = _intent_hash(
        {key: value for key, value in legacy_feedback.items() if key != "content_hash"}
    )
    legacy = {**modern, "requested_visual_feedback": legacy_feedback}
    revision_seed = {
        key: value for key, value in legacy.items() if key not in {"revision_id", "content_hash"}
    }
    revision_id = f"r-{_intent_hash(revision_seed)[:24]}"
    legacy["revision_id"] = revision_id
    legacy["content_hash"] = _intent_hash(
        {key: value for key, value in legacy.items() if key != "content_hash"}
    )
    work_path = (
        root
        / "highlights"
        / "visual-pipeline"
        / cut_id
        / "revisions"
        / revision_id
        / "DIRECTOR-WORK.json"
    )
    visual_contract_module._atomic_publish(work_path, legacy)
    legacy_selection = visual_contract_module.ArtifactSelection(work_path, legacy, root)
    visual_contract_module._write_pointer(
        root,
        cut_id=cut_id,
        name="PENDING.json",
        revision_id=revision_id,
        state="pending",
        work_packet=legacy_selection.identity(),
        semantic_audit=None,
        expected_existing_content_hash=None,
    )
    frozen_bytes = work_path.read_bytes()

    resumed = init_visual_work_packet(
        root,
        cut_id=cut_id,
        revision_request=request,
        editorial_master=master,
    )

    assert resumed.document["revision_id"] == revision_id
    assert work_path.read_bytes() == frozen_bytes
    _assert_contract_error(
        lambda: visual_contract_module._reject_human_removed_assets(
            root,
            resumed.document,
            [
                {
                    "asset_id": "renamed-legacy-stock",
                    "source_url": "https://www.pexels.com/video/9999999/",
                    "original_media": _identity(root, stock),
                }
            ],
        ),
        "human-removed visual source",
    )


def _legacy_migrated_hero_request(root: Path, master: _FakeMaster) -> Path:
    review = root / "highlights" / "review"
    cut_dir = review / "value-L01"
    cut_dir.mkdir(parents=True, exist_ok=True)
    source = review / "finished_review_manifest_20260822.json"
    source_components = [
        {
            "component_id": "value-L01-hero-001",
            "lane": "hero_title",
            "t0": 101.82,
            "t1": 103.9,
            "text": "與其去教\n三國的故事",
        },
        {
            "component_id": "value-L01-hero-002",
            "lane": "hero_title",
            "t0": 380.28,
            "t1": 383.48,
            "text": "沒有任何\n保證了",
        },
    ]
    source.write_text(
        json.dumps(
            {
                "schema": "nakama.finished_cut_review_manifest.v1",
                "episode_id": root.name,
                "stage": 5,
                "cuts": [{"cut_id": "value-L01", "components": source_components}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    registry_core = {
        "contract": "finished-review-component-identity-v1",
        "episode_id": root.name,
        "source_manifest": {
            "filename": source.name,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        "cuts": {
            "value-L01": [
                {
                    "component_id": component["component_id"],
                    "lane": component["lane"],
                    "snapshot": {
                        "lane": component["lane"],
                        "t0": component["t0"],
                        "t1": component["t1"],
                        "text": component["text"],
                    },
                }
                for component in source_components
            ]
        },
    }
    registry = review / "finished_review_component_identity.v1.json"
    registry.write_text(
        json.dumps(
            {**registry_core, "content_hash": _intent_hash(registry_core)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    current_components = [
        {
            "component_id": "value-L01-hero-001",
            "lane": "hero_title",
            "type": "card-tier1",
            "slug": "與其教故事/不如動手做",
            "t0": 104.0,
            "t1": 107.6,
            "note": "",
        },
        {
            "component_id": "value-L01-hero-002",
            "lane": "hero_title",
            "type": "card-tier1",
            "slug": "傳統道路/沒有保證了",
            "t0": 508.667,
            "t1": 512.267,
            "note": "",
        },
    ]
    events = cut_dir / "events.json"
    events.write_text(
        json.dumps(
            {
                "editorial_master_lineage": master.identity(),
                "duration_sec": 592.9,
                "events": [
                    {
                        key: value
                        for key, value in component.items()
                        if key not in {"component_id", "lane"}
                    }
                    for component in current_components
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    current = review / "finished_review_manifest_current.json"
    current.write_text(
        json.dumps(
            {
                "schema": "nakama.finished_cut_review_manifest.v1",
                "episode_id": root.name,
                "stage": 5,
                "editorial_master_lineage": master.identity(),
                "cuts": [
                    {
                        "cut_id": "value-L01",
                        "artifacts": {
                            "events": {
                                "path": str(events.resolve()),
                                "bytes": events.stat().st_size,
                                "sha256": hashlib.sha256(events.read_bytes()).hexdigest(),
                            }
                        },
                        "components": current_components,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rows = _hero_feedback_rows()
    rows[0]["timeline_seconds"] = {"t0": 104.0, "t1": 107.6}
    rows[1]["timeline_seconds"] = {"t0": 508.667, "t1": 512.267}
    request = review / "revisions" / "finished-revision-real-shaped" / "request.json"
    request.parent.mkdir(parents=True)
    request.write_text(
        json.dumps(
            {
                "worker_contract": "finished-cut-revision-worker-v2-stock-required",
                "episode_id": root.name,
                "review_format": "long",
                "manifest_filename": source.name,
                "source_manifest_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "source_preview_sha256": {"value-L01": "9" * 64},
                "requested_cut_ids": ["value-L01"],
                "component_feedback": rows,
                "overall_feedback": {"value-L01": "保留 Hero Title 的完整句子。"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return request


def _director_for_hero_feedback(work: object) -> dict[str, object]:
    proposal = _director_proposal(work)
    first = proposal["events"][1]
    first["event_id"] = "value-L01-hero-001"
    first["on_screen_text"] = "與其教故事\n不如動手做"
    first["description"] = "依照人工 review 保留完整雙行 Hero Title"
    second = proposal["events"][2]
    second["event_id"] = "value-L01-hero-002"
    second["category"] = "keyword"
    second["form"] = "overlay"
    second["description"] = "依照人工 review 修正第二張完整雙行 Hero Title"
    second["on_screen_text"] = "傳統道路\n沒有保證了"
    second["rationale"] = "人工明確要求第二張 Hero Title 的逐字 replacement 與換行。"
    return proposal


def _director_for_remove_move_asset_feedback(work: object) -> dict[str, object]:
    proposal = _director_proposal(work)
    stock = proposal["events"][0]
    moved = deepcopy(proposal["events"][1])
    moved.update(
        {
            "event_id": "value-L01-hero-002",
            "cue_ids": [3, 4],
            "t0": 9.75,
            "t1": 13.75,
            "quote": "高壓教育留下深遠影響\n這種制度延續了很長一段時間",
            "on_screen_text": "移動後的 Hero",
            "description": "依照人工 move 指令移到指定的四秒區間",
        }
    )
    visual = deepcopy(proposal["events"][2])
    visual["event_id"] = "value-L01-visual-001"
    proposal["events"] = [stock, moved, visual, proposal["events"][3]]
    proposal["coverage"]["max_uncovered_start_sec"] = 6.0
    proposal["coverage"]["max_uncovered_sec"] = 3.75
    proposal["coverage"]["max_uncovered_end_sec"] = 9.75
    return proposal


def _dp_for_remove_move_asset_feedback(root: Path, director: object) -> dict[str, object]:
    actual = {
        event["event_id"]: event
        for event in director.document["events"]
        if event["decision"] == "add_visual"
    }

    class _LegacyShapeAdapter:
        document = {
            "work_packet": director.document["work_packet"],
            "events": [
                deepcopy(actual["visual-001"]),
                {**deepcopy(actual["value-L01-hero-002"]), "event_id": "title-002"},
                {
                    **deepcopy(actual["value-L01-visual-001"]),
                    "event_id": "visual-002",
                },
            ]
        }

        @staticmethod
        def identity() -> dict[str, object]:
            return director.identity()

    proposal = _dp_proposal(root, _LegacyShapeAdapter())
    title = proposal["implementations"][1]
    title_event = actual["value-L01-hero-002"]
    title["event_id"] = "value-L01-hero-002"
    title["director_intent_sha256"] = _intent_hash(title_event)
    title["on_screen_text"] = title_event["on_screen_text"]
    title_candidate = title["candidates"][0]
    title_candidate["render_params"]["text"] = title_event["on_screen_text"]
    title_candidate["render_spec_sha256"] = hashlib.sha256(
        json.dumps(
            {
                "component": title_candidate["component"],
                "render_params": title_candidate["render_params"],
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    title["selections"][0].update(
        {
            "cue_ids": title_event["cue_ids"],
            "t0": title_event["t0"],
            "t1": title_event["t1"],
            "quote": title_event["quote"],
        }
    )
    visual = proposal["implementations"][2]
    visual_event = actual["value-L01-visual-001"]
    visual["event_id"] = "value-L01-visual-001"
    visual["director_intent_sha256"] = _intent_hash(visual_event)
    visual["candidates"][0]["candidate_id"] = "replacement-card"
    visual["selections"][0]["candidate_id"] = "replacement-card"
    return proposal


def _director_for_change_type_feedback(work: object) -> dict[str, object]:
    proposal = _director_proposal(work)
    proposal["events"][2]["event_id"] = "value-L01-visual-001"
    return proposal


def _dp_for_change_type_feedback(
    root: Path,
    director: object,
    *,
    target_lane: str = "content_card_track4",
    implementation_kind: str = "concept_card",
) -> dict[str, object]:
    actual = {
        event["event_id"]: event
        for event in director.document["events"]
        if event["decision"] == "add_visual"
    }

    class _LegacyShapeAdapter:
        document = {
            "work_packet": director.document["work_packet"],
            "events": [
                deepcopy(actual["visual-001"]),
                deepcopy(actual["title-002"]),
                {
                    **deepcopy(actual["value-L01-visual-001"]),
                    "event_id": "visual-002",
                },
            ]
        }

        @staticmethod
        def identity() -> dict[str, object]:
            return director.identity()

    proposal = _dp_proposal(root, _LegacyShapeAdapter())
    implementation = proposal["implementations"][2]
    event = actual["value-L01-visual-001"]
    implementation["event_id"] = "value-L01-visual-001"
    implementation["director_intent_sha256"] = _intent_hash(event)
    implementation["target_lane"] = target_lane
    implementation["implementation_kind"] = implementation_kind
    candidate = implementation["candidates"][0]
    if implementation_kind == "concept_card":
        candidate["component"] = "concept_card"
        candidate["render_spec_sha256"] = hashlib.sha256(
            json.dumps(
                {
                    "component": candidate["component"],
                    "render_params": candidate["render_params"],
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    return proposal


def _dp_for_hero_feedback(root: Path, director: object) -> dict[str, object]:
    actual_events = {
        event["event_id"]: event
        for event in director.document["events"]
        if event["decision"] == "add_visual"
    }

    class _LegacyShapeAdapter:
        document = {
            "work_packet": director.document["work_packet"],
            "events": [
                deepcopy(actual_events["visual-001"]),
                {
                    **deepcopy(actual_events["value-L01-hero-001"]),
                    "event_id": "title-002",
                },
                {
                    **deepcopy(actual_events["value-L01-hero-002"]),
                    "event_id": "visual-002",
                    "category": "chapter",
                },
            ]
        }

        @staticmethod
        def identity() -> dict[str, object]:
            return director.identity()

    proposal = _dp_proposal(root, _LegacyShapeAdapter())
    title_template = proposal["implementations"][1]
    hero_items: list[dict[str, object]] = []
    for index, component_id in enumerate(("value-L01-hero-001", "value-L01-hero-002"), 1):
        event = actual_events[component_id]
        item = deepcopy(title_template)
        item["event_id"] = component_id
        item["director_intent_sha256"] = _intent_hash(event)
        item["on_screen_text"] = event["on_screen_text"]
        candidate_id = f"hero-feedback-{index}"
        candidate = item["candidates"][0]
        candidate["candidate_id"] = candidate_id
        candidate["visual_summary"] = f"人工指定 Hero {index} 的 exact multiline render"
        candidate["render_params"]["text"] = event["on_screen_text"]
        candidate["render_params"]["show_sec"] = event["t1"] - event["t0"]
        candidate["render_spec_sha256"] = hashlib.sha256(
            json.dumps(
                {
                    "component": candidate["component"],
                    "render_params": candidate["render_params"],
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        item["selections"] = [
            {
                "candidate_id": candidate_id,
                "cue_ids": event["cue_ids"],
                "t0": event["t0"],
                "t1": event["t1"],
                "quote": event["quote"],
                "source_range": {
                    "start_sec": 0.0,
                    "end_sec": event["t1"] - event["t0"],
                },
            }
        ]
        hero_items.append(item)
    proposal["implementations"] = [proposal["implementations"][0], *hero_items]
    return proposal


def _audit_from_materializations(
    director: object,
    dp: object,
    materializations: tuple[dict[str, object], ...],
) -> dict[str, object]:
    return {
        "contract": "podcast-highlight-visual-semantic-audit-v1",
        "episode_id": director.document["episode_id"],
        "cut_id": director.document["cut_id"],
        "director_plan": director.identity(),
        "dp_fulfillment": dp.identity(),
        "findings": [
            {
                "materialization_id": item["materialization_id"],
                "event_id": item["event_id"],
                "director_intent_sha256": item["director_intent_sha256"],
                "cue_ids": item["cue_ids"],
                "t0": item["t0"],
                "t1": item["t1"],
                "quote": item["quote"],
                "source_range": item["source_range"],
                "evidence_sha256": item["media"]["sha256"],
                "visual_observation": "逐一檢視 exact render，人工 replacement 與換行均完整可讀。",
                "verdict": "match",
                "rationale": "實際預覽畫面符合人工明確指定的 component 文字與視覺語意。",
            }
            for item in materializations
        ],
    }


def _bind_broll_remove_request(
    root: Path,
    *,
    asset: dict[str, object] | None,
    request_id: str,
    source_url: str | None = None,
) -> Path:
    component_id = "value-L01-b-roll-001"
    request = _finished_visual_request(
        root,
        rows=[
            {
                "cut_id": "value-L01",
                "component_id": component_id,
                "lane": "b_roll",
                "timeline_seconds": {"t0": 20.0, "t1": 23.0},
                "action": "remove",
                "comment": "這支 Stock 畫面語意錯誤，不得換 ID 後再放回來",
                "replacement": "",
                "remember_preference": False,
            }
        ],
        request_id=request_id,
    )
    request_payload = json.loads(request.read_text(encoding="utf-8"))
    manifest_path = root / "highlights" / "review" / request_payload["manifest_filename"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    component = {
        "component_id": component_id,
        "lane": "b_roll",
        "type": "video",
        "slug": "old-wrong-stock",
        "t0": 20.0,
        "t1": 23.0,
        "asset_category": "stock_video",
    }
    if asset is not None:
        component["asset"] = asset
    if source_url is not None:
        component["provenance"] = {"source_url": source_url}
    manifest["cuts"][0]["components"].append(component)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    request_payload["source_manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    request.write_text(json.dumps(request_payload, ensure_ascii=False), encoding="utf-8")
    return request


def test_removed_human_stock_binds_old_bytes_and_cannot_be_reintroduced_under_new_id(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "davinci_import" / "black10s.mp4"
    old_asset = root / "assets" / "broll" / "old-wrong-stock.mp4"
    old_asset.parent.mkdir(parents=True, exist_ok=True)
    old_asset.write_bytes(fixture.read_bytes() + b"human-removed-stock")
    request = _bind_broll_remove_request(
        root,
        asset=_identity(root, old_asset),
        request_id="remove-old-stock",
    )
    work = init_visual_work_packet(
        root,
        cut_id="value-L01",
        revision_request=request,
        editorial_master=master,
    )
    directive = work.document["requested_visual_feedback"]["directives"][0]
    assert directive["source_component"]["asset"]["sha256"] == _identity(root, old_asset)[
        "sha256"
    ]

    renamed = root / "assets" / "broll" / "renamed-new-component-id.mp4"
    renamed.write_bytes(old_asset.read_bytes())
    _assert_contract_error(
        lambda: publish_asset_authority(
            root,
            cut_id="value-L01",
            revision_id=str(work.document["revision_id"]),
            attempt=1,
            assets=[
                _trusted_asset_source(
                    root,
                    renamed,
                    asset_id="brand-new-component-id",
                    provider_item_id="987654321",
                    semantic_summary="改名後仍是人工明確移除的同一支錯誤 Stock 素材",
                )
            ],
            worker_identity=ASSET_WORKER,
            editorial_master=master,
        ),
        "human-removed visual source",
    )


def test_removed_stock_without_request_bound_media_identity_requires_operator_rebind(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    request = _bind_broll_remove_request(
        root,
        asset=None,
        request_id="remove-stock-without-media-proof",
    )
    _assert_contract_error(
        lambda: preflight_visual_work_packet(
            root,
            cut_id="value-L01",
            revision_request=request,
            editorial_master=master,
        ),
        "requires_operator_rebind",
    )


def test_removed_human_stock_url_cannot_be_reintroduced_with_new_bytes_or_url_case(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "davinci_import" / "black10s.mp4"
    old_asset = root / "assets" / "broll" / "old-envato-stock.mp4"
    old_asset.parent.mkdir(parents=True, exist_ok=True)
    old_asset.write_bytes(fixture.read_bytes() + b"old-envato-stock")
    canonical_url = "https://elements.envato.com/classroom-cadets-abc123"
    request = _bind_broll_remove_request(
        root,
        asset=_identity(root, old_asset),
        request_id="remove-old-envato-url",
        source_url=canonical_url,
    )
    work = init_visual_work_packet(
        root,
        cut_id="value-L01",
        revision_request=request,
        editorial_master=master,
    )
    replacement = root / "assets" / "broll" / "new-bytes-renamed-id.mp4"
    replacement.write_bytes(fixture.read_bytes() + b"different-envato-stock")
    source = {
        **_trusted_asset_source(
            root,
            replacement,
            asset_id="renamed-envato-component",
            provider_item_id="abc123",
            semantic_summary="即使換新 bytes 與 component id 也不可重放人工移除的來源",
        ),
        "provider": "envato-elements",
        "source_url": canonical_url,
        "license": "Envato Elements license: https://elements.envato.com/license-terms",
    }
    _assert_contract_error(
        lambda: publish_asset_authority(
            root,
            cut_id="value-L01",
            revision_id=str(work.document["revision_id"]),
            attempt=1,
            assets=[source],
            worker_identity=ASSET_WORKER,
            editorial_master=master,
        ),
        "human-removed visual source",
    )
    case_variant = {
        **source,
        "provider_item_id": "ABC123",
        "source_url": "https://elements.envato.com/CLASSROOM-CADETS-ABC123",
    }
    _assert_contract_error(
        lambda: publish_asset_authority(
            root,
            cut_id="value-L01",
            revision_id=str(work.document["revision_id"]),
            attempt=1,
            assets=[case_variant],
            worker_identity=ASSET_WORKER,
            editorial_master=master,
        ),
        "Envato provider_item_id is invalid",
    )


def test_revision_work_projects_and_enforces_two_exact_multiline_hero_edits(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    first = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    _complete_generation(root, master, first)
    wrong_time_rows = _hero_feedback_rows()
    wrong_time_rows[0]["timeline_seconds"] = {"t0": 6.25, "t1": 10.25}
    wrong_time_request = _finished_visual_request(
        root,
        rows=wrong_time_rows,
        request_id="wrong-component-time",
    )
    _assert_contract_error(
        lambda: preflight_visual_work_packet(
            root,
            cut_id="value-L01",
            revision_request=wrong_time_request,
            editorial_master=master,
        ),
        "legacy feedback identity cannot be proven",
    )
    request = _finished_visual_request(root, rows=_hero_feedback_rows())
    work = init_visual_work_packet(
        root,
        cut_id="value-L01",
        revision_request=request,
        editorial_master=master,
    )
    revision_id = work.document["revision_id"]
    projection = work.document["requested_visual_feedback"]
    assert projection["contract"] == "podcast-highlight-requested-visual-feedback-v1"
    assert [row["replacement"] for row in projection["directives"]] == [
        "與其教故事\n不如動手做",
        "傳統道路\n沒有保證了",
    ]
    assert projection["creative_context"] == {
        "policy": "informational_not_acceptance",
        "overall_feedback": "這是 creative context，不得變成 deterministic 文字規則。",
    }

    ignored = _director_proposal(work)
    wrong_component = _director_for_hero_feedback(work)
    wrong_component["events"][1]["event_id"] = "value-L01-wrong-component"
    missing_newline = _director_for_hero_feedback(work)
    missing_newline["events"][1]["on_screen_text"] = "與其教故事 不如動手做"
    changed_words = _director_for_hero_feedback(work)
    changed_words["events"][2]["on_screen_text"] = "傳統道路\n不一定有保證"
    for proposal, expected in (
        (ignored, "missing requested visual component"),
        (wrong_component, "missing requested visual component"),
        (missing_newline, "exact requested replacement"),
        (changed_words, "exact requested replacement"),
    ):
        _assert_contract_error(
            lambda proposal=proposal: accept_director_plan(
                root,
                cut_id="value-L01",
                revision_id=revision_id,
                proposal=proposal,
                worker_identity=DIRECTOR_WORKER,
                editorial_master=master,
            ),
            expected,
        )

    accepted = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_director_for_hero_feedback(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )
    assert [
        event["on_screen_text"]
        for event in accepted.document["events"]
        if event["event_id"].startswith("value-L01-hero")
    ] == ["與其教故事\n不如動手做", "傳統道路\n沒有保證了"]

    wrong_lane = _dp_for_hero_feedback(root, accepted)
    wrong_lane["implementations"][1]["target_lane"] = "content_card_track4"
    wrong_lane["implementations"][1]["implementation_kind"] = "concept_card"
    _assert_contract_error(
        lambda: accept_dp_fulfillment(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=wrong_lane,
            worker_identity=DP_WORKER,
            editorial_master=master,
        ),
        "violates requested component lane",
    )
    rounded_duration_proposal = _dp_for_hero_feedback(root, accepted)
    rounded_hero = rounded_duration_proposal["implementations"][1]["candidates"][0]
    rounded_hero["render_params"]["text"] = rounded_hero["render_params"]["text"].replace(
        "\n", "\r\n"
    )
    rounded_hero["render_params"]["show_sec"] += 1e-12
    rounded_hero["render_spec_sha256"] = _content_hash(
        {
            "component": rounded_hero["component"],
            "render_params": rounded_hero["render_params"],
        }
    )
    dp = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=rounded_duration_proposal,
        worker_identity=DP_WORKER,
        editorial_master=master,
    )
    materializations = load_visual_materializations(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        editorial_master=master,
    )
    assert [
        item["event_id"]
        for item in materializations
        if item["event_id"].startswith("value-L01-hero")
    ] == ["value-L01-hero-001", "value-L01-hero-002"]
    accept_semantic_audit(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_audit_from_materializations(accepted, dp, materializations),
        worker_identity=AUDIT_WORKER,
        editorial_master=master,
    )
    assert (
        verify_visual_pipeline(
            root, cut_id="value-L01", editorial_master=master
        ).work_packet.document["requested_visual_feedback"]
        == projection
    )


def test_bridge_does_not_rebuild_legacy_visual_requests() -> None:
    import thousand_sunny.routers.highlight_review as review_router

    assert not hasattr(review_router, "_finished_revision_job")
    assert hasattr(review_router, "_finished_revision_jobs")


def test_feedback_identity_migration_rejects_arbitrary_span_mismatch(tmp_path: Path) -> None:
    root, master = _episode(tmp_path)
    request = _legacy_migrated_hero_request(root, master)
    payload = json.loads(request.read_text(encoding="utf-8"))
    payload["component_feedback"][0]["timeline_seconds"] = {
        "t0": 104.125,
        "t1": 107.725,
    }
    request.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    _assert_contract_error(
        lambda: preflight_visual_work_packet(
            root,
            cut_id="value-L01",
            revision_request=request,
            editorial_master=master,
        ),
        "regenerate the review request",
    )


def test_feedback_identity_migration_rejects_self_consistent_target_rewrite(
    tmp_path: Path,
) -> None:
    for drift in ("slug_text", "alternate_order", "extra_row"):
        root, master = _episode(tmp_path / drift)
        request = _legacy_migrated_hero_request(root, master)
        review = root / "highlights" / "review"
        events_path = review / "value-L01" / "events.json"
        events = json.loads(events_path.read_text(encoding="utf-8"))
        current_path = review / "finished_review_manifest_current.json"
        current = json.loads(current_path.read_text(encoding="utf-8"))
        components = current["cuts"][0]["components"]
        if drift == "slug_text":
            events["events"][0]["slug"] = "竄改的前句/竄改的後句"
            events["events"][1]["slug"] = "另一個竄改/仍是竄改"
            components[0]["slug"] = "竄改的前句/竄改的後句"
            components[1]["slug"] = "另一個竄改/仍是竄改"
        elif drift == "alternate_order":
            events["events"].reverse()
            components.reverse()
        else:
            extra = {
                "type": "card-tier1",
                "slug": "額外插入/未受信任",
                "t0": 520.0,
                "t1": 523.6,
                "note": "",
            }
            events["events"].append(extra)
            components.append(
                {
                    **extra,
                    "component_id": "value-L01-hero-003",
                    "lane": "hero_title",
                }
            )
        events_path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
        current["cuts"][0]["artifacts"]["events"].update(
            {
                "bytes": events_path.stat().st_size,
                "sha256": hashlib.sha256(events_path.read_bytes()).hexdigest(),
            }
        )
        current_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")

        _assert_contract_error(
            lambda: preflight_visual_work_packet(
                root,
                cut_id="value-L01",
                revision_request=request,
                editorial_master=master,
            ),
            "legacy feedback identity cannot be proven",
        )


def test_remove_move_and_replace_asset_feedback_are_early_machine_gates(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    first = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    _complete_generation(root, master, first)
    rows = [
        {
            **_hero_feedback_rows()[0],
            "action": "remove",
            "replacement": "",
        },
        {
            **_hero_feedback_rows()[1],
            "action": "move",
            "replacement": "",
            "move_to_seconds": 9.75,
        },
        {
            "cut_id": "value-L01",
            "component_id": "value-L01-visual-001",
            "lane": "visual_effect",
            "timeline_seconds": {"t0": 14.0, "t1": 18.0},
            "action": "replace_asset",
            "comment": "換成指定的 visual candidate",
            "replacement": "replacement-card",
            "remember_preference": False,
        },
    ]
    request = _finished_visual_request(root, rows=rows, request_id="mixed-actions")
    work = init_visual_work_packet(
        root,
        cut_id="value-L01",
        revision_request=request,
        editorial_master=master,
    )
    revision_id = work.document["revision_id"]

    remove_ignored = _director_for_remove_move_asset_feedback(work)
    removed_event = remove_ignored["events"][-1]
    removed_event.update(
        {
            "event_id": "value-L01-hero-001",
            "category": "chapter",
            "form": "overlay",
            "description": "故意違反 remove 指令的可視章節卡",
            "on_screen_text": "這張應該被移除",
            "decision": "add_visual",
            "rationale": "測試機械 gate 會在 Resolve 之前拒絕被要求移除的 component。",
        }
    )
    remove_ignored["coverage"].update(
        {
            "add_visual_count": 4,
            "planned_visual_count": 6,
            "intentional_aroll_count": 0,
            "visual_events_per_minute": 15.0,
        }
    )
    _assert_contract_error(
        lambda: accept_director_plan(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=remove_ignored,
            worker_identity=DIRECTOR_WORKER,
            editorial_master=master,
        ),
        "removed visual component must not be planned",
    )

    wrong_move = _director_for_remove_move_asset_feedback(work)
    wrong_move["events"][1]["t0"] = 10.0
    wrong_move["events"][1]["t1"] = 14.0
    _assert_contract_error(
        lambda: accept_director_plan(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=wrong_move,
            worker_identity=DIRECTOR_WORKER,
            editorial_master=master,
        ),
        "exact requested range",
    )

    director = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_director_for_remove_move_asset_feedback(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )
    wrong_asset = _dp_for_remove_move_asset_feedback(root, director)
    wrong_asset["implementations"][2]["candidates"][0]["candidate_id"] = "wrong-card"
    wrong_asset["implementations"][2]["selections"][0]["candidate_id"] = "wrong-card"
    _assert_contract_error(
        lambda: accept_dp_fulfillment(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=wrong_asset,
            worker_identity=DP_WORKER,
            editorial_master=master,
        ),
        "selected asset differs from requested replacement",
    )
    dp = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_dp_for_remove_move_asset_feedback(root, director),
        worker_identity=DP_WORKER,
        editorial_master=master,
    )
    assert {row["event_id"] for row in dp.document["implementations"]} == {
        "visual-001",
        "value-L01-hero-002",
        "value-L01-visual-001",
    }


def test_change_type_feedback_rejects_wrong_lane_and_kind_then_accepts_exact_type(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    first = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    _complete_generation(root, master, first)
    request = _finished_visual_request(
        root,
        request_id="change-type",
        rows=[
            {
                "cut_id": "value-L01",
                "component_id": "value-L01-visual-001",
                "lane": "visual_effect",
                "timeline_seconds": {"t0": 14.0, "t1": 18.0},
                "action": "change_type",
                "comment": "章節轉換改成概念卡",
                "replacement": "concept",
                "remember_preference": False,
            }
        ],
    )
    work = init_visual_work_packet(
        root,
        cut_id="value-L01",
        revision_request=request,
        editorial_master=master,
    )
    revision_id = work.document["revision_id"]
    director = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_director_for_change_type_feedback(work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )

    wrong_lane = _dp_for_change_type_feedback(
        root,
        director,
        target_lane="broll_track2",
        implementation_kind="photo",
    )
    _assert_contract_error(
        lambda: accept_dp_fulfillment(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=wrong_lane,
            worker_identity=DP_WORKER,
            editorial_master=master,
        ),
        "violates requested component lane",
    )

    wrong_kind = _dp_for_change_type_feedback(
        root,
        director,
        implementation_kind="transition_title",
    )
    _assert_contract_error(
        lambda: accept_dp_fulfillment(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=wrong_kind,
            worker_identity=DP_WORKER,
            editorial_master=master,
        ),
        "type differs from requested change_type",
    )

    dp = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_dp_for_change_type_feedback(root, director),
        worker_identity=DP_WORKER,
        editorial_master=master,
    )
    changed = next(
        row
        for row in load_visual_materializations(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            editorial_master=master,
        )
        if row["event_id"] == "value-L01-visual-001"
    )
    assert changed["target_lane"] == "content_card_track4"
    assert changed["implementation_kind"] == "concept_card"
    assert dp.document["implementations"][2]["implementation_kind"] == "concept_card"


def test_change_type_request_rejects_unknown_or_lane_incompatible_kind_before_init(
    tmp_path: Path,
) -> None:
    for replacement, expected in (
        ("execute-arbitrary-instruction", "not a closed visual kind"),
        ("video", "not compatible with lane=visual_effect"),
    ):
        root, master = _episode(tmp_path / replacement)
        request = _finished_visual_request(
            root,
            request_id=f"invalid-{replacement}",
            rows=[
                {
                    "cut_id": "value-L01",
                    "component_id": "value-L01-visual-001",
                    "lane": "visual_effect",
                    "timeline_seconds": {"t0": 14.0, "t1": 18.0},
                    "action": "change_type",
                    "comment": "只允許 closed lane semantics",
                    "replacement": replacement,
                    "remember_preference": False,
                }
            ],
        )
        _assert_contract_error(
            lambda: init_visual_work_packet(
                root,
                cut_id="value-L01",
                revision_request=request,
                editorial_master=master,
            ),
            expected,
        )


def test_feedback_source_manifest_tamper_invalidates_pending_work(tmp_path: Path) -> None:
    root, master = _episode(tmp_path)
    request = _finished_visual_request(root, rows=_hero_feedback_rows())
    work = init_visual_work_packet(
        root,
        cut_id="value-L01",
        revision_request=request,
        editorial_master=master,
    )
    source = root / work.document["requested_visual_feedback"]["source_manifest"]["path"]
    source.write_bytes(source.read_bytes() + b"\n")

    _assert_contract_error(
        lambda: load_visual_work_packet(
            root,
            cut_id="value-L01",
            revision_id=work.document["revision_id"],
            editorial_master=master,
        ),
        "visual feedback source manifest hash drift",
    )


def test_failed_second_revision_preserves_current_then_switches_pointer_last(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    first_work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    first = _complete_generation(root, master, first_work)
    first_revision = first.work_packet.document["revision_id"]

    request = root / "highlights" / "review" / "revisions" / "feedback-002" / "request.json"
    request.parent.mkdir(parents=True)
    request.write_text(
        json.dumps(
            {
                "contract": "finished-cut-revision-request-v1",
                "request_id": "feedback-002",
                "overall_feedback": "Hero Title 要保留完整句子並重新挑選 Stock。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    second_work = init_visual_work_packet(
        root,
        cut_id="value-L01",
        revision_request=request,
        editorial_master=master,
    )
    second_revision = second_work.document["revision_id"]
    assert second_revision != first_revision
    assert (
        verify_visual_lineage(root, "value-L01", editorial_master=master)["revision_id"]
        == first_revision
    )

    director = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=second_revision,
        proposal=_director_proposal(second_work),
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )
    bad_dp = deepcopy(_dp_proposal(root, director))
    bad_dp["implementations"][0]["director_intent_sha256"] = "f" * 64
    try:
        accept_dp_fulfillment(
            root,
            cut_id="value-L01",
            revision_id=second_revision,
            proposal=bad_dp,
            worker_identity=DP_WORKER,
            editorial_master=master,
        )
    except HighlightVisualContractError:
        pass
    else:
        raise AssertionError("intent-drifted DP proposal must fail")
    assert (
        verify_visual_lineage(root, "value-L01", editorial_master=master)["revision_id"]
        == first_revision
    )

    dp = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=second_revision,
        proposal=_dp_proposal(root, director),
        worker_identity=DP_WORKER,
        editorial_master=master,
    )
    audit_proposal = _audit_proposal(director, dp)
    accepted = accept_semantic_audit(
        root,
        cut_id="value-L01",
        revision_id=second_revision,
        proposal=audit_proposal,
        worker_identity=AUDIT_WORKER,
        editorial_master=master,
    )
    original = accepted.path.read_bytes()
    retried = accept_semantic_audit(
        root,
        cut_id="value-L01",
        revision_id=second_revision,
        proposal=audit_proposal,
        worker_identity=AUDIT_WORKER,
        editorial_master=master,
    )

    assert retried.path.read_bytes() == original
    assert (
        verify_visual_lineage(root, "value-L01", editorial_master=master)["revision_id"]
        == second_revision
    )


def test_fresh_load_rejects_srt_candidate_materialization_and_request_drift(
    tmp_path: Path,
) -> None:
    cases = ("srt", "candidates", "materialization")
    for case in cases:
        root, master = _episode(tmp_path / case)
        work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
        if case == "srt":
            path = root / work.document["cut_srt"]["path"]
        elif case == "candidates":
            path = root / work.document["candidates_file"]["path"]
        else:
            path = root / work.document["materialization"]["path"]
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        _assert_contract_error(
            lambda: load_visual_work_packet(
                root,
                cut_id="value-L01",
                revision_id=work.document["revision_id"],
                editorial_master=master,
            ),
            "stale",
        )

    root, master = _episode(tmp_path / "request")
    base = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    _complete_generation(root, master, base)
    request = root / "highlights" / "review" / "feedback-003.json"
    request.parent.mkdir(parents=True, exist_ok=True)
    request.write_text('{"feedback":"first"}', encoding="utf-8")
    revised = init_visual_work_packet(
        root,
        cut_id="value-L01",
        revision_request=request,
        editorial_master=master,
    )
    request.write_text('{"feedback":"changed after init"}', encoding="utf-8")
    _assert_contract_error(
        lambda: load_visual_work_packet(
            root,
            cut_id="value-L01",
            revision_id=revised.document["revision_id"],
            editorial_master=master,
        ),
        "byte size drift",
    )


def test_fresh_load_rejects_master_director_and_selected_media_drift(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path / "master")
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    changed_master = _FakeMaster(
        value={**master.value, "content_hash": "d" * 64},
        media_path=master.media_path,
        srt_path=master.srt_path,
    )
    _assert_contract_error(
        lambda: load_visual_work_packet(
            root,
            cut_id="value-L01",
            revision_id=work.document["revision_id"],
            editorial_master=changed_master,
        ),
        "Editorial Master lineage is stale",
    )

    for drift in ("director", "media-size", "media-hash", "media-missing"):
        root, master = _episode(tmp_path / drift)
        work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
        revision_id = work.document["revision_id"]
        director = accept_director_plan(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=_director_proposal(work),
            worker_identity=DIRECTOR_WORKER,
            editorial_master=master,
        )
        dp = accept_dp_fulfillment(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=_dp_proposal(root, director),
            worker_identity=DP_WORKER,
            editorial_master=master,
        )
        if drift == "director":
            document = json.loads(director.path.read_text(encoding="utf-8"))
            document["events"][0]["description"] = "tampered Director intent"
            director.path.write_text(json.dumps(document), encoding="utf-8")
            expected = "content hash mismatch"
        else:
            selected_media = (
                root / dp.document["implementations"][0]["candidates"][0]["media"]["path"]
            )
            if drift == "media-size":
                selected_media.write_bytes(selected_media.read_bytes() + b"tamper")
                expected = "byte size drift"
            elif drift == "media-hash":
                payload = bytearray(selected_media.read_bytes())
                payload[-1] = (payload[-1] + 1) % 256
                selected_media.write_bytes(payload)
                expected = "hash drift"
            else:
                selected_media.rename(selected_media.with_suffix(".moved"))
                expected = "escapes episode root or is missing"
        _assert_contract_error(
            lambda: load_dp_fulfillment(
                root,
                cut_id="value-L01",
                revision_id=revision_id,
                editorial_master=master,
            ),
            expected,
        )


def test_canonical_conflict_is_fail_closed_and_current_pointer_tamper_is_invalid(
    tmp_path: Path,
) -> None:
    root, master = _episode(tmp_path)
    work = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    revision_id = work.document["revision_id"]
    proposal = _director_proposal(work)
    first = accept_director_plan(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=proposal,
        worker_identity=DIRECTOR_WORKER,
        editorial_master=master,
    )
    assert (
        accept_director_plan(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=proposal,
            worker_identity=DIRECTOR_WORKER,
            editorial_master=master,
        ).path.read_bytes()
        == first.path.read_bytes()
    )

    changed = _director_proposal(work)
    changed["events"][0]["rationale"] += " 這是不同的 canonical creative decision。"
    _assert_contract_error(
        lambda: accept_director_plan(
            root,
            cut_id="value-L01",
            revision_id=revision_id,
            proposal=changed,
            worker_identity=DIRECTOR_WORKER,
            editorial_master=master,
        ),
        "immutable canonical artifact already differs",
    )

    dp = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_dp_proposal(root, first),
        worker_identity=DP_WORKER,
        editorial_master=master,
    )
    accept_semantic_audit(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_audit_proposal(first, dp),
        worker_identity=AUDIT_WORKER,
        editorial_master=master,
    )
    current = root / "highlights" / "visual-pipeline" / "value-L01" / "CURRENT.json"
    document = json.loads(current.read_text(encoding="utf-8"))
    document["content_hash"] = "0" * 64
    current.write_text(json.dumps(document), encoding="utf-8")
    _assert_contract_error(
        lambda: verify_visual_pipeline(root, cut_id="value-L01", editorial_master=master),
        "content hash mismatch",
    )
    assert (
        visual_pipeline_status(root, cut_id="value-L01", editorial_master=master)["status"]
        == "invalid"
    )
    assert first.path.is_file()
