from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from agents.brook.script_video.highlight_visual_pipeline import (
    WORK_PACKET_NAME,
    HighlightVisualContractError,
    accept_director_plan,
    accept_dp_fulfillment,
    accept_semantic_audit,
    init_visual_work_packet,
    load_director_plan,
    load_dp_fulfillment,
    load_semantic_audit,
    load_visual_materializations,
    load_visual_work_packet,
    preflight_visual_work_packet,
    verify_visual_lineage,
    verify_visual_pipeline,
    visual_pipeline_status,
)
from shared.highlight_materialization import (
    HighlightSource,
    build_materialization_receipt,
    write_materialization_receipt,
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


def _assert_contract_error(call, contains: str) -> None:
    try:
        call()
    except HighlightVisualContractError as error:
        assert contains in str(error), str(error)
    else:
        raise AssertionError(f"expected HighlightVisualContractError containing {contains!r}")


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


def _identity(root: Path, path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


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


def _dp_proposal(root: Path, director: object) -> dict[str, object]:
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
    stock_licenses = []
    for suffix in ("a", "b", "c"):
        receipt = assets / f"postwar-school-{suffix}-license.json"
        receipt.write_text('{"license":"Pexels"}', encoding="utf-8")
        stock_licenses.append(receipt)
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
    render_params = {"title": "教育開始\n改變", "accent": "orange"}
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
        "style": "hero",
        "show_sec": 4.0,
        "pos_y": 40,
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
    return {
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
                        "visual_summary": summary,
                        "media": _identity(root, media),
                        "provenance": {
                            "kind": "stock_source",
                            "provider": "pexels",
                            "source_url": f"https://www.pexels.com/video/12{index}/",
                            "license": "Pexels license",
                            "receipt": _identity(root, receipt),
                        },
                    }
                    for index, (suffix, summary, media, receipt) in enumerate(
                        zip(
                            ("a", "b", "c"),
                            (
                                "戰後東亞校園、整齊制服與軍事化隊列的歷史實拍",
                                "歷史教室裡學生一致髮型與制服的實拍",
                                "校園教官巡視與高壓紀律的歷史實拍",
                            ),
                            stock_paths,
                            stock_licenses,
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
                            "provider": "hyperframes",
                            "source_url": None,
                            "license": "Nakama internal composition",
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
                            "provider": "hyperframes",
                            "source_url": None,
                            "license": "Nakama internal composition",
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


def test_dp_rejects_lineage_candidate_timing_media_and_target_drift(tmp_path: Path) -> None:
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
    reject(proposal, "candidates are not distinct")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][0]["candidates"][0]["provenance"].pop("license")
    reject(proposal, "fields mismatch")

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
    reject(proposal, "path escapes episode root")

    proposal = _dp_proposal(root, director)
    fake = root / "highlights" / "visual-pipeline" / "value-L01" / "proposal-assets" / "fake.mp4"
    fake.write_bytes(b"not a playable video")
    proposal["implementations"][0]["candidates"][0]["media"] = _identity(root, fake)
    reject(proposal, "not inspectable playable video")

    proposal = _dp_proposal(root, director)
    stock_candidates = proposal["implementations"][0]["candidates"]
    copied_to = root / stock_candidates[1]["media"]["path"]
    copied_from = root / stock_candidates[0]["media"]["path"]
    copied_to.write_bytes(copied_from.read_bytes())
    stock_candidates[1]["media"] = _identity(root, copied_to)
    reject(proposal, "candidates are not distinct")

    proposal = _dp_proposal(root, director)
    proposal["implementations"][1]["candidates"][0]["render_spec_sha256"] = "0" * 64
    reject(proposal, "render spec hash mismatch")

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

    for verdict in ("mismatch", "uncertain"):
        proposal = _audit_proposal(director, dp)
        proposal["findings"][0]["verdict"] = verdict
        reject(proposal, f"verdict={verdict}")

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
    dp = accept_dp_fulfillment(
        root,
        cut_id="value-L01",
        revision_id=revision_id,
        proposal=_dp_for_hero_feedback(root, accepted),
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


def test_legacy_feedback_requires_bridge_save_draft_rebuild_from_current_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    root, master = _episode(tmp_path)
    first = init_visual_work_packet(root, cut_id="value-L01", editorial_master=master)
    _complete_generation(root, master, first)
    request = _legacy_migrated_hero_request(root, master)
    _assert_contract_error(
        lambda: preflight_visual_work_packet(
            root,
            cut_id="value-L01",
            revision_request=request,
            editorial_master=master,
        ),
        "regenerate the review request",
    )

    original = json.loads(request.read_text(encoding="utf-8"))
    current_path = root / "highlights" / "review" / "finished_review_manifest_current.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current.update(
        {
            "_path": str(current_path.resolve()),
            "_sha256": hashlib.sha256(current_path.read_bytes()).hexdigest(),
        }
    )
    import thousand_sunny.routers.highlight_review as review_router

    monkeypatch.setattr(review_router, "load_episode_trusted_asset_handoff", lambda _root: None)
    monkeypatch.setattr(review_router, "revision_requires_stock_assets", lambda *_args: False)
    rebuilt = review_router._finished_revision_job(
        manifest=current,
        audit={"revisions": []},
        cut_statuses={"value-L01": "needs_changes"},
        component_feedback=original["component_feedback"],
        overall_feedback=original["overall_feedback"],
        preview_sha256=original["source_preview_sha256"],
    )
    assert rebuilt is not None
    assert rebuilt["component_feedback"] == original["component_feedback"]
    assert rebuilt["overall_feedback"] == original["overall_feedback"]
    assert rebuilt["manifest_filename"] == current_path.name
    assert (
        rebuilt["source_manifest_sha256"] == hashlib.sha256(current_path.read_bytes()).hexdigest()
    )

    rebuilt_path = root / "highlights" / "review" / "revisions" / "rebuilt" / "request.json"
    rebuilt_path.parent.mkdir(parents=True)
    rebuilt_path.write_text(json.dumps(rebuilt, ensure_ascii=False), encoding="utf-8")
    prospective = preflight_visual_work_packet(
        root,
        cut_id="value-L01",
        revision_request=rebuilt_path,
        editorial_master=master,
    )
    assert prospective["status"] == "would_initialize"
    feedback = prospective["requested_visual_feedback"]
    assert feedback["source_manifest"] == {
        "path": "highlights/review/finished_review_manifest_current.json",
        "bytes": current_path.stat().st_size,
        "sha256": hashlib.sha256(current_path.read_bytes()).hexdigest(),
    }
    assert [row["replacement"] for row in feedback["directives"]] == [
        "與其教故事\n不如動手做",
        "傳統道路\n沒有保證了",
    ]


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
