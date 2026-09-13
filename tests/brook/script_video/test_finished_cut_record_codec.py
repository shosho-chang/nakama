"""落盤格式由欄位宣告推導出來（ADR-069 階段 3）。

以前 `_store` 每個 record 各有一支手寫的 `_to_dict` 與 `_from_dict`，欄位名抄三遍。
現在只有一份 `_codec.RecordCodec`，規則是「欄位的型別註記就是 schema」。這一組測試
鎖住那條規則的三個面向：

* 整支 run 進磁碟再回來要**一模一樣**——少一格、型別換一種都會紅。
* 詞彙表以外的值在讀回來的當下就擋（sentinel 擋不到的，這個擋得到）。
* 推導出來的欄位不落盤，reload 時重算。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from agents.brook.script_video.finished_cut_production._assets import (
    AssetKind,
    WorkerCatalogItem,
    WorkerSelectionCatalog,
)
from agents.brook.script_video.finished_cut_production._codec import (
    RecordCodec,
    RecordCodecError,
)
from agents.brook.script_video.finished_cut_production._context import (
    CanonicalSection,
    CueAnchor,
    CutSourceRange,
    EditorialCutContext,
    VisualPlacement,
)
from agents.brook.script_video.finished_cut_production._correction import _PreReleaseCorrection
from agents.brook.script_video.finished_cut_production._derived_assets import (
    BuiltComponentAsset,
    DerivedAssetBuildRequest,
    DerivedAssetGeometry,
    DerivedAssetInstruction,
)
from agents.brook.script_video.finished_cut_production._policy import PolicyDiagnostic
from agents.brook.script_video.finished_cut_production._records import (
    ComponentProposal,
    EventPlacementCandidates,
    EventRecord,
    StageRequest,
    _mint_accepted_stage,
    _mint_materialization_plan,
    _mint_projected_component,
    _ProductionRun,
)
from agents.brook.script_video.finished_cut_production._store import (
    ProductionStoreError,
    _catalog_from_list,
    _catalog_to_list,
    _view_from_dict,
    _view_to_dict,
)

_ASSET = "asset-sha256:" + "a" * 64
_TITLE_ASSET = "asset-sha256:" + "b" * 64


def _context() -> EditorialCutContext:
    return EditorialCutContext(
        episode_id="episode-codec",
        cut_id="value-L04",
        format="long",
        editorial_master_id="c" * 64,
        tight_cut_id="tight-codec",
        duration_sec=600.0,
        source_ranges=(CutSourceRange(0.0, 250.0), CutSourceRange(300.0, 650.0)),
        cues=(
            CueAnchor("cue-1", "第一句", 0.0, 4.0, "section-1"),
            CueAnchor("cue-2", "第二句", 4.0, 9.0, "section-1"),
            CueAnchor("cue-3", "第三句", 20.0, 26.0, "section-2"),
        ),
        sections=(
            CanonicalSection("section-1", "開場", 0.0, summary="怎麼開始的"),
            CanonicalSection(
                "section-2",
                "轉折",
                20.0,
                transition_before=True,
                transition_title="轉折",
            ),
        ),
        editorial_feedback=("第二段太長",),
    )


def _events() -> tuple[EventRecord, ...]:
    return (
        EventRecord(
            event_id="event-broll",
            master_cue_ids=("cue-1", "cue-2"),
            text_hash="d" * 64,
            intent="放一段工作場景",
            asset_ref=_ASSET,
            visual_status="approved",
            text="第一句\n第二句",
            t0=0.0,
            t1=9.0,
            section_id="section-1",
            display="",
            semantic_kind="b_roll",
            implementation_kind="stock_video",
            lane="b_roll",
            visual_placement=VisualPlacement(
                placement_cue_ids=("cue-1",),
                t0=0.0,
                t1=4.0,
                section_id="section-1",
            ),
        ),
        EventRecord(
            event_id="event-chapter",
            master_cue_ids=("cue-3",),
            text_hash="e" * 64,
            intent="章節卡",
            asset_ref=_TITLE_ASSET,
            visual_status="approved",
            text="第三句",
            t0=20.0,
            t1=26.0,
            section_id="section-2",
            display="轉折",
            semantic_kind="chapter",
            implementation_kind="fullscreen_transition",
            lane="fullscreen_transition",
        ),
    )


def _components():
    return (
        _mint_projected_component(
            component_id="component-broll",
            event_id="event-broll",
            semantic_kind="b_roll",
            implementation_kind="stock_video",
            lane="b_roll",
            display="",
            t0=0.0,
            t1=4.0,
            asset_ref=_ASSET,
        ),
        _mint_projected_component(
            component_id="component-chapter",
            event_id="event-chapter",
            semantic_kind="chapter",
            implementation_kind="fullscreen_transition",
            lane="fullscreen_transition",
            display="轉折",
            t0=20.0,
            t1=23.0,
            asset_ref=_TITLE_ASSET,
        ),
    )


def _catalog() -> WorkerSelectionCatalog:
    return WorkerSelectionCatalog(
        (
            WorkerCatalogItem(
                reference=_ASSET,
                kind=AssetKind.STOCK,
                visual_summary="有人在工作",
                width=3840,
                height=2160,
                duration_sec=12.5,
            ),
        )
    )


def _run() -> _ProductionRun:
    context = _context()
    events = _events()
    accepted = _mint_accepted_stage(
        acceptance_id="acceptance-1",
        run_id="run-codec",
        request_id="request-" + "1" * 32,
        stage="dp",
        attempt=1,
        scope="full_stage",
        event_id=None,
        parent_acceptance_id="acceptance-0",
        events=events,
        components=(
            ComponentProposal(
                component_id="component-chapter",
                event_id="event-chapter",
                semantic_kind="chapter",
                implementation_kind="fullscreen_transition",
                lane="fullscreen_transition",
                display="轉折",
                t0=20.0,
                t1=23.0,
            ),
        ),
        built_components=(
            BuiltComponentAsset(
                component_id="component-chapter",
                event_id="event-chapter",
                source_asset_ref=None,
                final_asset_ref=_TITLE_ASSET,
                inspection_ref=None,
                recipe_identity="transition-title@v4",
            ),
        ),
    )
    return _ProductionRun(
        run_id="run-codec",
        command_id="approved-cut:" + "f" * 32,
        editorial_context=context,
        status="review_ready",
        outstanding_request=StageRequest(
            run_id="run-codec",
            request_id="request-" + "2" * 32,
            command_id="approved-cut:" + "f" * 32,
            episode_id="episode-codec",
            cut_id="value-L04",
            format="long",
            stage="visual_review",
            attempt=2,
            scope="event_retry",
            event_id="event-broll",
            parent_acceptance_id="acceptance-1",
            base_acceptance_id="acceptance-0",
            events=events,
            placement_candidates=(
                EventPlacementCandidates(
                    event_id="event-broll",
                    cues=(CueAnchor("cue-1", "第一句", 0.0, 4.0, "section-1"),),
                ),
            ),
            feedback="這一支語意不準",
            worker_asset_refs=(_ASSET,),
            worker_catalog_items=_catalog().items(),
            editorial_context=context,
        ),
        accepted_stages=(accepted,),
        accepted_stage_history=(accepted,),
        derived_asset_request=DerivedAssetBuildRequest(
            build_request_id="build-1",
            run_id="run-codec",
            command_id="approved-cut:" + "f" * 32,
            episode_id="episode-codec",
            cut_id="value-L04",
            format="long",
            dp_acceptance_id="acceptance-1",
            scope="full_stage",
            event_id=None,
            instructions=(
                DerivedAssetInstruction(
                    component_id="component-chapter",
                    event_id="event-chapter",
                    semantic_kind="chapter",
                    implementation_kind="fullscreen_transition",
                    lane="fullscreen_transition",
                    display="轉折",
                    t0=20.0,
                    t1=23.0,
                    source_asset_ref=None,
                    geometry=DerivedAssetGeometry(1920, 1080, "transition-title@v4"),
                    recipe_identity="transition-title@v4",
                ),
            ),
            worker_catalog_items=_catalog().items(),
        ),
        materialization_plan=_mint_materialization_plan(
            plan_id="plan-1",
            run_id="run-codec",
            command_id="approved-cut:" + "f" * 32,
            episode_id="episode-codec",
            cut_id="value-L04",
            format="long",
            director_acceptance_id="acceptance-d",
            dp_acceptance_id="acceptance-1",
            visual_acceptance_id="acceptance-v",
            events=events,
            components=_components(),
            duration_sec=600.0,
        ),
        correction=_PreReleaseCorrection(
            event_id="event-broll",
            feedback="換一支",
            remaining_base_acceptance_ids=("acceptance-0",),
        ),
        policy_diagnostics=(
            PolicyDiagnostic(
                code="visual_gap_exceeded",
                message="中段沒有畫面",
                component_ids=("component-broll",),
                section_ids=("section-2",),
                asset_refs=(_ASSET,),
            ),
        ),
    )


def _round_trip(run: _ProductionRun) -> _ProductionRun:
    """Go through real JSON, so a tuple that silently became a list shows up."""

    return _view_from_dict(json.loads(json.dumps(_view_to_dict(run))))


def test_production_run_survives_a_real_json_round_trip_unchanged() -> None:
    run = _run()

    assert _round_trip(run) == run


def test_round_trip_is_stable_across_a_second_pass() -> None:
    # 第一趟相等還不夠：如果讀回來的形狀跟寫出去的形狀差一點（例如某個欄位被
    # 讀成預設值），第二趟的 payload 就會跟第一趟不同。
    run = _run()

    first = _view_to_dict(run)
    second = _view_to_dict(_view_from_dict(json.loads(json.dumps(first))))

    assert second == first


def test_worker_catalog_round_trips_through_its_enum() -> None:
    catalog = _catalog()

    reloaded = _catalog_from_list(json.loads(json.dumps(_catalog_to_list(catalog))))

    assert reloaded.items() == catalog.items()
    assert reloaded.items()[0].kind is AssetKind.STOCK


def test_derived_brand_badges_are_recomputed_not_persisted() -> None:
    run = _run()
    plan = run.materialization_plan
    assert plan is not None
    assert plan.brand_badge_overlays, "fixture 要能推導出 badge，否則這條測試什麼都沒鎖"

    payload = _view_to_dict(run)

    assert "brand_badge_overlays" not in payload["materialization_plan"]
    reloaded = _view_from_dict(json.loads(json.dumps(payload)))
    assert reloaded.materialization_plan is not None
    assert reloaded.materialization_plan.brand_badge_overlays == plan.brand_badge_overlays


@pytest.mark.parametrize(
    ("pointer", "forged"),
    [
        (("status",), "almost_ready"),
        (("outstanding_request", "stage"), "colourist"),
        (("outstanding_request", "scope"), "whole_thing"),
        (("editorial_context", "format"), "medium"),
        (("derived_asset_request", "scope"), "forged"),
    ],
)
def test_a_value_outside_the_declared_vocabulary_is_refused_on_read(
    pointer: tuple[str, ...],
    forged: str,
) -> None:
    payload = json.loads(json.dumps(_view_to_dict(_run())))
    target = payload
    for key in pointer[:-1]:
        target = target[key]
    assert pointer[-1] in target, pointer
    target[pointer[-1]] = forged

    with pytest.raises(ProductionStoreError, match=pointer[-1]):
        _view_from_dict(payload)


def test_a_missing_required_field_is_refused_rather_than_defaulted() -> None:
    payload = json.loads(json.dumps(_view_to_dict(_run())))
    del payload["editorial_context"]["cues"]

    with pytest.raises(ProductionStoreError, match="cues"):
        _view_from_dict(payload)


def test_an_optional_field_stored_as_null_falls_back_to_its_default() -> None:
    # 舊檔把「還沒有這個欄位」寫成 null。那時候的 loader 用 `value.get(...) or ""`
    # 吸收掉；現在是 codec 的通則，別讓它退化成字串 "None"。
    payload = json.loads(json.dumps(_view_to_dict(_run())))
    payload["editorial_context"]["sections"][0]["summary"] = None

    reloaded = _view_from_dict(payload)

    assert reloaded.editorial_context.sections[0].summary == ""


def test_an_older_store_without_stage_history_reads_the_current_stages_as_history() -> None:
    payload = json.loads(json.dumps(_view_to_dict(_run())))
    del payload["accepted_stage_history"]

    reloaded = _view_from_dict(payload)

    assert reloaded.accepted_stage_history == reloaded.accepted_stages


def test_the_codec_refuses_a_shape_it_cannot_persist_instead_of_guessing() -> None:
    from dataclasses import dataclass

    from agents.brook.script_video.finished_cut_production._codec import RecordCodec

    @dataclass(frozen=True, slots=True)
    class _Unsupported:
        mapping: dict[str, int]

    with pytest.raises(RecordCodecError, match="does not persist"):
        RecordCodec().load_record(_Unsupported, {"mapping": {"a": 1}})


def test_editorial_cut_context_cannot_exist_with_overlapping_source_ranges() -> None:
    # 這條以前只在物化那一關驗（`_validate_context_contract`），store 讀回來、
    # worker packet 拿去用的路徑都驗不到。現在在建構子，全部路徑共用一份。
    with pytest.raises(ValueError, match="source ranges are invalid"):
        EditorialCutContext(
            episode_id="episode-codec",
            cut_id="value-L04",
            format="long",
            editorial_master_id="c" * 64,
            tight_cut_id="tight-codec",
            duration_sec=600.0,
            source_ranges=(CutSourceRange(0.0, 250.0), CutSourceRange(200.0, 550.0)),
            cues=(CueAnchor("cue-1", "第一句", 0.0, 4.0, "section-1"),),
        )


def test_editorial_cut_context_cannot_exist_with_cues_that_run_backwards() -> None:
    with pytest.raises(ValueError, match="cue contract is invalid"):
        EditorialCutContext(
            episode_id="episode-codec",
            cut_id="value-L04",
            format="long",
            editorial_master_id="c" * 64,
            tight_cut_id="tight-codec",
            duration_sec=600.0,
            source_ranges=(CutSourceRange(0.0, 600.0),),
            cues=(
                CueAnchor("cue-1", "第一句", 10.0, 14.0, "section-1"),
                CueAnchor("cue-2", "第二句", 4.0, 9.0, "section-1"),
            ),
        )


def test_range_sum_mismatch_stays_a_policy_diagnostic_not_a_construction_error() -> None:
    """段落總和對不上 duration 不在這裡擋——`_policy` 要把它報給修修看。

    把它搬進建構子會讓 `source_range_sum_mismatch` 這條診斷永遠發不出來：
    帶著它的 context 根本造不出來，policy 就沒有東西可以檢查。
    """

    context = EditorialCutContext(
        episode_id="episode-codec",
        cut_id="value-L04",
        format="long",
        editorial_master_id="c" * 64,
        tight_cut_id="tight-codec",
        duration_sec=600.0,
        source_ranges=(CutSourceRange(0.0, 250.0),),
        cues=(CueAnchor("cue-1", "第一句", 0.0, 4.0, "section-1"),),
    )

    assert context.duration_sec == 600.0


def test_a_visual_placement_cannot_exist_with_an_inverted_window() -> None:
    with pytest.raises(ValueError, match="Visual Placement fields are invalid"):
        VisualPlacement(placement_cue_ids=("cue-1",), t0=9.0, t1=4.0, section_id="section-1")


def test_a_bool_field_refuses_anything_that_is_not_a_bool() -> None:
    """`bool(raw)` 對任何東西都給得出答案，於是壞掉的 payload 會變成合理的布林值。

    `{}` 是 False、`"false"` 是 True、`{"a": 1}` 是 True——三個都是謊，而且會一路
    存回磁碟。這支的承諾是「不支援的形狀當場報錯」，布林不能是唯一的例外。
    """

    @dataclass(frozen=True, slots=True)
    class Flagged:
        flag: bool

    codec = RecordCodec()

    assert codec.load_record(Flagged, {"flag": True}).flag is True
    assert codec.load_record(Flagged, {"flag": False}).flag is False
    for rubbish in ({}, [], "false", "true", 1, 0, None):
        with pytest.raises(RecordCodecError, match="is not a bool"):
            codec.load_record(Flagged, {"flag": rubbish})
