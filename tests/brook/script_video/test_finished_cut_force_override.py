"""`--force`：修修下的命令直接做，門讓路、紀錄照留。

這支測試的形狀是刻意的——每一道門都成對出現：

* **不帶 `--force`**：門照擋，`reason_code` 跟以前一模一樣。
* **帶 `--force`**：同一組輸入走得過去，而且那道門在收據上留下名字。

成對寫是因為這個功能唯一真正的風險不是「讓不讓得過」，是「沒帶旗標的時候行為
悄悄變了」。一邊綠一邊紅都算失敗。
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.brook.script_video.finished_cut_production import ProductionStatusView, _force
from agents.brook.script_video.finished_cut_production._commands import (
    CommandRejectedError,
    TargetedRevisionCommand,
)
from agents.brook.script_video.finished_cut_production._materialization import (
    MaterializationCoordinator,
    MaterializationError,
    MaterializationPreparation,
)
from agents.brook.script_video.finished_cut_production._persistence import (
    AtomicResolveTransactionStore,
)
from agents.brook.script_video.finished_cut_production._plan_record import PlanRecordStore
from agents.brook.script_video.finished_cut_production._resolve import (
    ResolveTransactionManager,
    TimelineIdentity,
)

from .test_finished_cut_materialization import (
    _AssetResolver,
    _canonical,
    _CanonicalAuthority,
    _component_plan,
    _context,
    _resolved_asset,
    _RunStore,
    _stored,
    _TimelineAdapter,
    _UnexpectedDependency,
)

_COMMAND_ID = "approved-cut:" + "a" * 32


@pytest.fixture
def forced() -> object:
    """打開覆寫，並保證離開時還原——沒有任何測試可以把它漏給下一支。"""

    stream = io.StringIO()
    override, token = _force.activate(stream=stream)
    try:
        yield SimpleNamespace(override=override, stream=stream)
    finally:
        _force.deactivate(token)


def _happy_coordinator(
    tmp_path: Path,
    *,
    stored: object,
    authority: object | None = None,
    adapter: object | None = None,
) -> MaterializationCoordinator:
    """一條會走到底的物化線——只有被測的那一道門是壞的。"""

    asset = tmp_path / "title.mov"
    if not asset.exists():
        asset.write_bytes(b"title")
    resolved = _resolved_asset(asset)
    adapter = adapter or _TimelineAdapter(tmp_path, baseline=_canonical().baseline)
    manager = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(tmp_path / "transactions"),
    )
    records = PlanRecordStore(
        tmp_path,
        transactions=manager,
        preview_probe=lambda path: {
            "duration_sec": 480.0,
            "video_codec": "h264",
            "audio_codec": "aac",
            "decode_ok": True,
            "offline_frame_count": 0,
        },
    )
    return MaterializationCoordinator(
        run_store=_RunStore(stored),
        canonical_authority=authority or _CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=manager,
        records=records,
        episode_root=tmp_path,
    )


def _asset_backed_stored(tmp_path: Path, **changes: object) -> object:
    asset = tmp_path / "title.mov"
    asset.write_bytes(b"title")
    plan = _component_plan(_resolved_asset(asset).record.reference)
    return _stored(plan=plan, **changes)  # type: ignore[arg-type]


# --------------------------------------------------------------- 基本契約


def test_the_override_is_off_until_something_turns_it_on() -> None:
    assert _force.is_active() is False
    assert _force.let_pass("editorial_master_mismatch", "anything") is False
    assert _force.entries() == ()


def test_the_override_is_restored_even_though_a_gate_fired(forced: object) -> None:
    assert _force.is_active() is True
    assert _force.let_pass("protected_track_drift", "one") is True


def test_the_override_does_not_survive_its_own_fixture() -> None:
    # 上一支測試開過覆寫。它沒有漏過來，這一行就是那個保證。
    assert _force.is_active() is False


def test_one_gate_prints_one_line_and_counts_the_rest(forced: object) -> None:
    for _ in range(3):
        _force.let_pass("protected_track_drift", "protected V1 differs")
    _force.let_pass("editorial_master_mismatch", "master differs")

    assert forced.stream.getvalue() == (
        "⚠ OVERRIDDEN: protected_track_drift protected V1 differs\n"
        "⚠ OVERRIDDEN: editorial_master_mismatch master differs\n"
    )
    assert [(gate.reason_code, gate.count) for gate in _force.entries()] == [
        ("protected_track_drift", 3),
        ("editorial_master_mismatch", 1),
    ]


# ------------------------------------------------- 身分鏈：擋 / 讓路成對


def test_authority_chain_still_blocks_without_force(tmp_path: Path) -> None:
    stored = _stored(context=_context(episode_id="another-episode"))
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(stored),
        canonical_authority=_UnexpectedDependency(),
        assets=_UnexpectedDependency(),
        transactions=_UnexpectedDependency(),
        records=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare(_COMMAND_ID)

    assert raised.value.reason_code == "authority_chain_mismatch"
    assert list(tmp_path.rglob("*")) == []


def test_a_targeted_revision_reaches_preview_ready_with_force(
    tmp_path: Path,
    forced: object,
) -> None:
    """(d) `TargetedRevisionCommand` 沒有 `editorial_master_id` / `tight_cut_id`。

    身分鏈那道門的第一個 clause 就是 `not isinstance(command, ApprovedCutCommand)`，
    所以平常修訂**永遠**物化不了。`--force` 下它走得完，而 winner 從它修訂的那份
    紀錄查出來，查不到就留空——不編。
    """

    revision = TargetedRevisionCommand(
        command_id="targeted-revision:" + "c" * 32,
        current_plan_id="plan-0",
        episode_id="episode-1",
        cut_id="punch-L04",
        format="long",
        event_id="event-1",
        feedback="把這張卡換掉",
    )
    stored = _asset_backed_stored(tmp_path, command=revision)

    prepared = _happy_coordinator(tmp_path, stored=stored).prepare(revision.command_id)

    assert isinstance(prepared, MaterializationPreparation)
    assert prepared.status == "preview_ready"
    assert prepared.record.command_id == "approved-cut:" + "a" * 32
    assert prepared.record.winner_id == ""
    assert "authority_chain_mismatch" in {gate.reason_code for gate in _force.entries()}


def test_a_targeted_revision_is_still_refused_without_force(tmp_path: Path) -> None:
    revision = TargetedRevisionCommand(
        command_id="targeted-revision:" + "c" * 32,
        current_plan_id="plan-0",
        episode_id="episode-1",
        cut_id="punch-L04",
        format="long",
        event_id="event-1",
        feedback="把這張卡換掉",
    )
    stored = _asset_backed_stored(tmp_path, command=revision)
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(stored),
        canonical_authority=_UnexpectedDependency(),
        assets=_UnexpectedDependency(),
        transactions=_UnexpectedDependency(),
        records=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare(revision.command_id)

    assert raised.value.reason_code == "authority_chain_mismatch"


# ------------------------------------------- 母帶一致性：擋 / 讓路成對


def _drifted_master_stored(tmp_path: Path) -> object:
    """Timeline 上的 V1 媒體不是登錄那一支 Master。"""

    canonical = _canonical()
    items = tuple(
        replace(item, media_digest="d" * 64) if item.track_type != "subtitle" else item
        for item in canonical.state.items
    )
    return replace(canonical, state=replace(canonical.state, items=items))


def test_editorial_master_drift_still_blocks_without_force(tmp_path: Path) -> None:
    stored = _asset_backed_stored(tmp_path)
    coordinator = _happy_coordinator(
        tmp_path,
        stored=stored,
        authority=_CanonicalAuthority((_drifted_master_stored(tmp_path),)),
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare(_COMMAND_ID)

    assert raised.value.reason_code == "editorial_master_mismatch"


def test_editorial_master_drift_lets_through_with_force(
    tmp_path: Path,
    forced: object,
) -> None:
    stored = _asset_backed_stored(tmp_path)
    coordinator = _happy_coordinator(
        tmp_path,
        stored=stored,
        authority=_CanonicalAuthority((_drifted_master_stored(tmp_path),)),
    )

    prepared = coordinator.prepare(_COMMAND_ID)

    assert prepared.status == "preview_ready"
    reported = {gate.reason_code for gate in _force.entries()}
    assert "editorial_master_mismatch" in reported
    assert "⚠ OVERRIDDEN: editorial_master_mismatch " in forced.stream.getvalue()


# ------------------------------------- 剪輯本體保護：擋 / 讓路成對


def _drifted_tracks(tmp_path: Path) -> object:
    """字幕軌的文字被動過——`protected_track_drift` 的典型形狀。"""

    canonical = _canonical()
    items = tuple(
        replace(item, properties=(("Text", "有人改過這一句"),))
        if item.track_type == "subtitle"
        else item
        for item in canonical.state.items
    )
    return replace(canonical, state=replace(canonical.state, items=items))


def test_protected_track_drift_still_blocks_without_force(tmp_path: Path) -> None:
    stored = _asset_backed_stored(tmp_path)
    coordinator = _happy_coordinator(
        tmp_path,
        stored=stored,
        authority=_CanonicalAuthority((_drifted_tracks(tmp_path),)),
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare(_COMMAND_ID)

    assert raised.value.reason_code == "protected_track_drift"


def test_protected_track_drift_lets_through_with_force(
    tmp_path: Path,
    forced: object,
) -> None:
    stored = _asset_backed_stored(tmp_path)
    coordinator = _happy_coordinator(
        tmp_path,
        stored=stored,
        authority=_CanonicalAuthority((_drifted_tracks(tmp_path),)),
    )

    prepared = coordinator.prepare(_COMMAND_ID)

    assert prepared.status == "preview_ready"
    assert "protected_track_drift" in {gate.reason_code for gate in _force.entries()}


# ------------------------------------ Resolve 綁定：擋 / 讓路成對


def test_ambiguous_canonical_binding_still_blocks_without_force(tmp_path: Path) -> None:
    stored = _asset_backed_stored(tmp_path)
    authority = _CanonicalAuthority(
        (_canonical(), replace(_canonical(), canonical=TimelineIdentity("Other", "uid-2")))
    )
    coordinator = _happy_coordinator(tmp_path, stored=stored, authority=authority)

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare(_COMMAND_ID)

    assert raised.value.reason_code == "resolve_binding_mismatch"


def test_ambiguous_canonical_binding_takes_the_first_with_force(
    tmp_path: Path,
    forced: object,
) -> None:
    stored = _asset_backed_stored(tmp_path)
    authority = _CanonicalAuthority(
        (_canonical(), replace(_canonical(), canonical=TimelineIdentity("Other", "uid-2")))
    )

    prepared = _happy_coordinator(tmp_path, stored=stored, authority=authority).prepare(_COMMAND_ID)

    assert prepared.status == "preview_ready"
    assert "resolve_binding_mismatch" in {gate.reason_code for gate in _force.entries()}


# ---------------------------------- 字幕 staging 衝突：擋 / 讓路成對


def _stage_a_conflicting_subtitle(tmp_path: Path, stored: object) -> Path:
    """先跑一次成功的物化，再把磁碟上那份字幕改掉。"""

    _happy_coordinator(tmp_path, stored=stored).prepare(_COMMAND_ID)
    subtitle = next((tmp_path / "highlights" / "staging").rglob("review.srt"))
    subtitle.write_bytes("1\n00:00:00,000 --> 00:00:03,000\n有人改過\n".encode("utf-8"))
    return subtitle


def test_conflicting_staged_subtitle_still_blocks_without_force(tmp_path: Path) -> None:
    stored = _asset_backed_stored(tmp_path)
    subtitle = _stage_a_conflicting_subtitle(tmp_path, stored)
    for record in (tmp_path / "highlights" / "staging").rglob("materialization.json"):
        record.unlink()

    with pytest.raises(MaterializationError) as raised:
        _happy_coordinator(tmp_path, stored=stored).prepare(_COMMAND_ID)

    assert raised.value.reason_code == "subtitle_staging_conflict"
    assert subtitle.read_bytes().endswith("有人改過\n".encode("utf-8"))


def test_conflicting_staged_subtitle_reports_the_disk_digest_with_force(
    tmp_path: Path,
    forced: object,
) -> None:
    stored = _asset_backed_stored(tmp_path)
    subtitle = _stage_a_conflicting_subtitle(tmp_path, stored)

    prepared = _happy_coordinator(tmp_path, stored=stored).prepare(_COMMAND_ID)

    # 門讓路，帳不說謊：回報的是**磁碟上那一份**的 digest。
    assert prepared.subtitle_sha256 == hashlib.sha256(subtitle.read_bytes()).hexdigest()
    assert "subtitle_staging_conflict" in {gate.reason_code for gate in _force.entries()}


# --------------------------------------------- 重鋪，而不是重進入


def test_force_relays_instead_of_reopening_the_prior_record(tmp_path: Path) -> None:
    """`--force` 下有既有紀錄也要重鋪——重進入那條路完全不碰 timeline。"""

    stored = _asset_backed_stored(tmp_path)
    adapter = _TimelineAdapter(tmp_path, baseline=_canonical().baseline)
    authority = _CanonicalAuthority((_canonical(),))

    def prepare() -> None:
        _happy_coordinator(
            tmp_path,
            stored=stored,
            adapter=adapter,
            authority=authority,
        ).prepare(_COMMAND_ID)

    prepare()
    assert (adapter.duplicates, adapter.applies) == (1, 1)
    assert len(authority.calls) == 1

    # 不帶旗標：第二次走重進入。那條路在問 canonical 之前就 return 了，所以
    # `inspect` 的次數不會動——這就是「完全不碰 timeline」在測試裡看得見的形狀。
    prepare()
    assert len(authority.calls) == 1

    _override, token = _force.activate(stream=io.StringIO())
    try:
        prepare()
    finally:
        _force.deactivate(token)

    # 帶旗標：重進入被跳過，canonical 又被問了一次。同一個 plan 的交易已經做完，
    # 所以接回來而不是再 duplicate 一次——重鋪不等於重複疊軌。
    assert len(authority.calls) == 2
    assert (adapter.duplicates, adapter.applies) == (1, 1)


# ---------------------------------------------------------------- CLI


def _cli_args(tmp_path: Path, *rest: str) -> list[str]:
    return [
        "--runtime-root",
        str(tmp_path / "runtime"),
        "--episodes-root",
        str(tmp_path / "episodes"),
        "--episode-id",
        "episode-1",
        *rest,
    ]


def _load_cli() -> object:
    import importlib.util

    path = Path(__file__).resolve().parents[3] / "scripts" / "run_finished_cut_production.py"
    spec = importlib.util.spec_from_file_location("_run_finished_cut_production", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _GateApplication:
    """一個只會撞門的 application——CLI 那一層要證明的就是旗標有沒有接上。"""

    def __init__(self) -> None:
        self.calls = 0

    def advance(self, command_id: str) -> ProductionStatusView:
        self.calls += 1
        if not _force.let_pass("editorial_master_mismatch", "master differs"):
            raise MaterializationError("master differs", reason_code="editorial_master_mismatch")
        return ProductionStatusView(command_id=command_id, state="preview_ready")


def test_cli_without_force_leaves_every_gate_in_place(tmp_path: Path) -> None:
    module = _load_cli()
    application = _GateApplication()

    with pytest.raises(MaterializationError):
        module.main(
            _cli_args(tmp_path, "advance", _COMMAND_ID),
            application_factory=lambda *a, **k: application,
        )

    assert application.calls == 1
    assert not (tmp_path / "runtime" / "force-overrides.jsonl").exists()


def test_cli_force_lets_the_gate_through_and_writes_its_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_cli()
    application = _GateApplication()

    exit_code = module.main(
        _cli_args(tmp_path, "--force", "advance", _COMMAND_ID),
        application_factory=lambda *a, **k: application,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "⚠ OVERRIDDEN: editorial_master_mismatch master differs" in captured.err
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert payload["overridden_gates"] == [
        {
            "reason_code": "editorial_master_mismatch",
            "message": "master differs",
            "count": 1,
        }
    ]
    receipt = (tmp_path / "runtime" / "force-overrides.jsonl").read_text(encoding="utf-8")
    recorded = json.loads(receipt.strip())
    assert recorded["episode_id"] == "episode-1"
    assert recorded["operation"] == "advance"
    assert recorded["overridden_gates"][0]["reason_code"] == "editorial_master_mismatch"
    # 覆寫不外洩到下一次執行。
    assert _force.is_active() is False


def test_cli_force_is_not_inherited_by_unattended_paths() -> None:
    """旗標只掛在 CLI 的 parser 上——watcher 沒有這個入口。"""

    module = _load_cli()
    parser = module._parser()
    force = [action for action in parser._actions if action.dest == "force"]
    assert len(force) == 1
    assert force[0].default is False


# -------------------------------------------- engine 的命令門成對


def test_request_correction_gate_blocks_then_lets_through() -> None:
    from agents.brook.script_video.finished_cut_production import _engine

    with pytest.raises(CommandRejectedError):
        _engine._reject_command("blocked", gate="plan_record_already_exists")

    override, token = _force.activate(stream=io.StringIO())
    try:
        _engine._reject_command("blocked", gate="plan_record_already_exists")
    finally:
        _force.deactivate(token)

    assert [gate.reason_code for gate in override.entries()] == ["plan_record_already_exists"]


# ---------------------------------------- engine：終態重跑 / visual 未通過


def _ready_run() -> tuple[object, object]:
    """把一支 run 推到 `review_ready`，回傳 (system, view)。"""

    from agents.brook.script_video.finished_cut_production._records import EventRecord

    from .test_finished_cut_production import (
        InMemoryAssetResolver,
        InMemoryProductionSystem,
        _advance_ready,
        _approved_cut,
        _asset,
    )

    system = InMemoryProductionSystem(
        approved_cuts=(_approved_cut(),),
        asset_resolver=InMemoryAssetResolver((_asset("stock-1"),)),
    )
    director_event = EventRecord(
        event_id="stock-1",
        master_cue_ids=("cue-30", "cue-31"),
        text_hash="text-30-31",
        intent="Show the speaker's concrete example",
    )
    ready, _visual = _advance_ready(system, (director_event,))
    return system, ready


def test_a_review_ready_run_is_a_no_op_without_force() -> None:
    from .test_finished_cut_production import _approved_cut, advance

    system, ready = _ready_run()
    assert ready.status == "review_ready"

    again = advance(_approved_cut().command_id, system=system)

    assert again.materialization_plan is not None
    assert again.materialization_plan.plan_id == ready.materialization_plan.plan_id


def test_force_re_mints_the_plan_on_a_review_ready_run(forced: object) -> None:
    """(gate #4) 終態重跑：`--force` 下要能再鋪一次，而不是 no-op。

    新的 plan 有自己的 id，所以 `_materialization_paths` 會算出自己的 staging
    工作區與 Resolve 交易——重跑是重鋪，不會覆蓋上一份。
    """

    from .test_finished_cut_production import _approved_cut, advance

    system, ready = _ready_run()

    again = advance(_approved_cut().command_id, system=system)

    assert again.status == "review_ready"
    assert again.materialization_plan is not None
    assert again.materialization_plan.plan_id != ready.materialization_plan.plan_id


# ------------------------------ visual_review 未通過 / policy 硬擋：成對


def _checkpoint_run(*, visual_status: str, policy: object | None = None) -> object:
    """一條驗收鏈剛好走完三站的 run，直接餵給視覺 checkpoint。"""

    from agents.brook.script_video.finished_cut_production._engine import _RunState
    from agents.brook.script_video.finished_cut_production._records import (
        EventRecord,
        _mint_accepted_stage,
        _ProductionRun,
    )

    from .test_finished_cut_production import _approved_cut

    command = _approved_cut()
    event = EventRecord(
        event_id="stock-1",
        master_cue_ids=("cue-30",),
        text_hash="text-30",
        intent="Show the speaker's concrete example",
        visual_status=visual_status,
    )
    stages = []
    parent = None
    for index, stage in enumerate(("director", "dp", "visual_review")):
        accepted = _mint_accepted_stage(
            acceptance_id=f"acceptance-{index}",
            run_id="run-1",
            request_id=f"request-{index}",
            stage=stage,
            attempt=1,
            scope="full_stage",
            event_id=None,
            parent_acceptance_id=parent,
            events=(event,),
            components=(),
            built_components=(),
        )
        parent = accepted.acceptance_id
        stages.append(accepted)
    view = _ProductionRun(
        run_id="run-1",
        command_id=command.command_id,
        editorial_context=None,
        status="needs_review",
        outstanding_request=None,
        accepted_stages=tuple(stages),
        accepted_stage_history=tuple(stages),
    )
    return _RunState(
        command=command,
        view=view,
        worker_catalog=None,
        base_record=None,
        editorial_context=None,
        format_policy=policy,
        derived_asset_builder=None,
        asset_resolver=None,
    )


class _MintingAggregate:
    def mint_id(self, prefix: str) -> str:
        return f"{prefix}-forced"

    def dispatch(self, request: object) -> object:  # pragma: no cover - unused here
        raise AssertionError("the checkpoint never dispatches")

    def load_accepted(self, acceptance_id: str) -> object:  # pragma: no cover - unused
        raise AssertionError("the checkpoint never reloads acceptances")

    def record_accepted(self, accepted: object) -> None:  # pragma: no cover - unused
        raise AssertionError("the checkpoint never records acceptances")


def test_an_unapproved_visual_review_parks_the_run_without_force() -> None:
    from agents.brook.script_video.finished_cut_production._engine import (
        _advance_visual_checkpoint,
    )

    view = _advance_visual_checkpoint(
        _checkpoint_run(visual_status="rejected"),
        _MintingAggregate(),
    )

    assert view.status == "needs_review"
    assert view.materialization_plan is None


def test_an_unapproved_visual_review_mints_its_plan_with_force(forced: object) -> None:
    from agents.brook.script_video.finished_cut_production._engine import (
        _advance_visual_checkpoint,
    )

    view = _advance_visual_checkpoint(
        _checkpoint_run(visual_status="rejected"),
        _MintingAggregate(),
    )

    assert view.status == "review_ready"
    assert view.materialization_plan is not None
    assert "visual_review_not_approved" in {gate.reason_code for gate in _force.entries()}
    assert "⚠ OVERRIDDEN: visual_review_not_approved " in forced.stream.getvalue()


class _BlockingPolicy:
    """一定回 blocking 的 policy——`_policy` 的 `BLOCKING_DIAGNOSTICS` 是唯一硬擋。"""

    def validate(self, candidate: object) -> object:
        from agents.brook.script_video.finished_cut_production._policy import (
            PolicyDecision,
            PolicyDiagnostic,
        )

        return PolicyDecision(
            "needs_review",
            (
                PolicyDiagnostic(
                    "chapter_transition_projection_mismatch",
                    "章節標題被改寫",
                ),
            ),
        )


def _policy_run(policy: object) -> object:
    from dataclasses import replace as _replace

    run = _checkpoint_run(visual_status="approved", policy=policy)
    context = _context()
    run.editorial_context = context
    run.view = _replace(run.view, editorial_context=context)
    return run


def test_a_blocking_policy_diagnostic_parks_the_run_without_force() -> None:
    from agents.brook.script_video.finished_cut_production._engine import (
        _advance_visual_checkpoint,
    )

    view = _advance_visual_checkpoint(_policy_run(_BlockingPolicy()), _MintingAggregate())

    assert view.status == "needs_review"
    assert view.materialization_plan is None
    assert [d.code for d in view.policy_diagnostics] == ["chapter_transition_projection_mismatch"]


def test_a_blocking_policy_diagnostic_lets_through_with_force(forced: object) -> None:
    """(gate #5) `_policy` 的 blocking diagnostic 在 `--force` 下不再中止。"""

    from agents.brook.script_video.finished_cut_production._engine import (
        _advance_visual_checkpoint,
    )

    view = _advance_visual_checkpoint(_policy_run(_BlockingPolicy()), _MintingAggregate())

    assert view.status == "review_ready"
    assert view.materialization_plan is not None
    assert "policy_blocking_diagnostic" in {gate.reason_code for gate in _force.entries()}
    assert (
        "⚠ OVERRIDDEN: policy_blocking_diagnostic format policy returned blocking "
        "diagnostics: chapter_transition_projection_mismatch" in forced.stream.getvalue()
    )
