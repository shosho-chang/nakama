from __future__ import annotations

import json
import pathlib
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production import CommandRejectedError
from agents.brook.script_video.finished_cut_production._approved_cut import (
    ApprovedCutAuthority,
    ApprovedCutRegistration,
    ApprovedCutRegistrationError,
    FilesystemEditorialMasterVerifier,
    VerifiedEditorialMaster,
)
from agents.brook.script_video.finished_cut_production._assets import (
    InMemoryAssetResolver,
)
from agents.brook.script_video.finished_cut_production._codex_semantic import (
    CodexProcessResult,
    SubprocessCodexProcessRunner,
)
from agents.brook.script_video.finished_cut_production._composition import (
    FinishedCutProductionApplication,
    ProductionDependencies,
    ProductionPaths,
    build_production_application,
)
from agents.brook.script_video.finished_cut_production._context import (
    CanonicalSection,
    CueAnchor,
    CutSourceRange,
)
from agents.brook.script_video.finished_cut_production._policy import PolicyDecision
from agents.brook.script_video.finished_cut_production._records import (
    DirectorEventProposal,
    DPEventProposal,
    EventRecord,
    ReleaseArtifact,
    VisualEventProposal,
)
from agents.brook.script_video.finished_cut_production._semantic import (
    DurableSemanticAdapter,
    InMemorySemanticAdapter,
    SemanticDispatchOutcome,
)
from agents.brook.script_video.finished_cut_production._store import (
    InMemoryPlanRecordIndex,
    _FilesystemProductionStore,
    _FilesystemSemanticDispatchLedger,
)
from agents.brook.script_video.finished_cut_production._visual_assets import (
    LongDerivedAssetBuilder,
)
from tests.brook.script_video.finished_cut_plan_records import plan_record

# 這幾支測的是 Long media composition 的實際接線，需要本機釘住的 HyperFrames
# runtime 與 Node（ci.yml 有意不裝 Node）。缺了就 skip，照 repo 既有慣例——
# 但要記得：CI 因此沒有覆蓋這段接線，只有本機 render host 會跑到。
_HYPERFRAMES_RUNTIME = (
    pathlib.Path(__file__).resolve().parents[3]
    / "video"
    / "node_modules"
    / ".nakama-hyperframes"
    / "0.7.72"
)
requires_local_hyperframes = pytest.mark.skipif(
    not _HYPERFRAMES_RUNTIME.is_dir() or (shutil.which("node.exe") or shutil.which("node")) is None,
    reason="pinned HyperFrames runtime + Node required (local render host only)",
)


@dataclass
class _MasterVerifier:
    master: VerifiedEditorialMaster

    def verify(
        self,
        *,
        episode_id: str,
        editorial_master_id: str,
    ) -> VerifiedEditorialMaster:
        assert episode_id == self.master.episode_id
        assert editorial_master_id == self.master.content_hash
        return self.master


class _AcceptingPolicy:
    def validate(self, _candidate) -> PolicyDecision:
        return PolicyDecision("accepted")


def _registration() -> ApprovedCutRegistration:
    return ApprovedCutRegistration(
        episode_id="episode-1",
        cut_id="long-3",
        format="long",
        editorial_master_id="a" * 64,
        winner_id="winner-long-3",
        tight_cut_id="tight-long-3",
        source_ranges=(CutSourceRange(120.0, 660.0),),
        cues=(
            CueAnchor("cue-1", "第一句", 0.0, 270.0, "section-1"),
            CueAnchor("cue-2", "第二句", 270.0, 540.0, "section-1"),
        ),
        sections=(CanonicalSection("section-1", "第一章", 0.0),),
        human_approved=True,
        approved_by="human:shosho",
        approved_at="2026-08-28T10:00:00+08:00",
    )


def _request_for_stage(
    application: FinishedCutProductionApplication,
    semantic: InMemorySemanticAdapter,
    command_id: str,
    stage: str,
):
    observed = None
    for _ in range(4):
        application.advance(command_id)
        try:
            observed = semantic.current_request(command_id)
        except KeyError:
            continue
        if observed.stage == stage:
            return observed
    raise AssertionError(f"stage was not dispatched: {stage}; observed={observed}")


def test_valid_long_registration_mints_opaque_authority_and_exact_context(
    tmp_path: Path,
) -> None:
    authority = ApprovedCutAuthority(
        tmp_path / "authority",
        master_verifier=_MasterVerifier(
            VerifiedEditorialMaster(
                episode_id="episode-1",
                content_hash="a" * 64,
                duration_sec=1_200.0,
            )
        ),
    )

    command_id = authority.register(_registration())

    assert re.fullmatch(r"approved-cut:[0-9a-f]{32}", command_id)
    command = authority.resolve(command_id)
    assert command is not None
    assert command.command_id == command_id
    assert command.editorial_master_id == "a" * 64
    context = authority.resolve_context(
        episode_id="episode-1",
        cut_id="long-3",
        editorial_master_id="a" * 64,
        tight_cut_id="tight-long-3",
    )
    assert context is not None
    assert context.duration_sec == 540.0
    assert context.sections == (CanonicalSection("section-1", "第一章", 0.0),)


def test_same_registration_is_idempotent_after_process_restart(tmp_path: Path) -> None:
    root = tmp_path / "authority"
    verifier = _MasterVerifier(
        VerifiedEditorialMaster(
            episode_id="episode-1",
            content_hash="a" * 64,
            duration_sec=1_200.0,
        )
    )
    first = ApprovedCutAuthority(root, master_verifier=verifier)
    command_id = first.register(_registration())

    reopened = ApprovedCutAuthority(root, master_verifier=verifier)

    assert reopened.register(_registration()) == command_id
    assert reopened.resolve(command_id) == first.resolve(command_id)


def test_registration_requires_explicit_human_approval(tmp_path: Path) -> None:
    authority = ApprovedCutAuthority(
        tmp_path / "authority",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
    )

    with pytest.raises(ApprovedCutRegistrationError, match="human approval"):
        authority.register(replace(_registration(), human_approved=False))


def test_tampered_editorial_master_fails_before_registration(tmp_path: Path) -> None:
    version = tmp_path / "episodes/episode-1/editorial-master/v1"
    version.mkdir(parents=True)
    (version / "EDITORIAL-MASTER.json").write_text("{}", encoding="utf-8")
    authority = ApprovedCutAuthority(
        tmp_path / "authority",
        master_verifier=FilesystemEditorialMasterVerifier(tmp_path / "episodes"),
    )

    with pytest.raises(ApprovedCutRegistrationError, match="Editorial Master"):
        authority.register(_registration())


def test_long_registration_rejects_cut_shorter_than_eight_minutes(tmp_path: Path) -> None:
    authority = ApprovedCutAuthority(
        tmp_path / "authority",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
    )
    too_short = replace(
        _registration(),
        source_ranges=(CutSourceRange(120.0, 599.0),),
        cues=(CueAnchor("cue-1", "太短", 0.0, 479.0, "section-1"),),
    )

    with pytest.raises(ApprovedCutRegistrationError, match="eight minutes"):
        authority.register(too_short)


def test_registration_rejects_source_range_outside_verified_master(tmp_path: Path) -> None:
    authority = ApprovedCutAuthority(
        tmp_path / "authority",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
    )
    invalid = replace(
        _registration(),
        source_ranges=(CutSourceRange(0.0, 300.0), CutSourceRange(1_100.0, 1_340.0)),
    )

    with pytest.raises(ApprovedCutRegistrationError, match="source range"):
        authority.register(invalid)


def test_a_long_cut_without_sections_registers_and_is_left_to_policy(tmp_path: Path) -> None:
    """沒有章節不再擋在登錄門外——長片本來就都有章節，這條幾乎不會 fire。

    真的沒有的時候，`_policy` 的 `canonical_sections_missing` 會接住（它才是真的
    前置條件：下一行就 index `sections[0]`），而且它是**診斷**，會出現在審核頁上。
    修修 2026-09-13：「長片不是一定要有章節嗎？那檢查這個做什麼？」
    """
    authority = ApprovedCutAuthority(
        tmp_path / "authority",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
    )

    command_id = authority.register(replace(_registration(), sections=()))

    assert authority.resolve(command_id) is not None


def test_a_chapter_title_with_a_colon_prefix_is_no_longer_refused(tmp_path: Path) -> None:
    """「三個選擇：先做哪一個」是正常的中文標題，不該讓整支 cut 登錄不進來。

    登錄門口本來有兩條正則（4 字內＋冒號、第三人稱代名詞開頭）。同一條規則寫作端
    已經有了（`.claude/skills/longform-cut/SKILL.md`），而正則這一份會誤殺。
    """
    authority = ApprovedCutAuthority(
        tmp_path / "authority",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
    )
    registration = _registration()
    sections = tuple(
        replace(section, transition_title="三個選擇：先做哪一個")
        for section in registration.sections
    )

    command_id = authority.register(replace(registration, sections=sections))

    assert authority.resolve(command_id) is not None


def test_registration_requires_valid_tight_subtitle_cues(tmp_path: Path) -> None:
    authority = ApprovedCutAuthority(
        tmp_path / "authority",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
    )

    with pytest.raises(ApprovedCutRegistrationError, match="tight subtitle cues"):
        authority.register(replace(_registration(), cues=()))


def test_registered_editorial_feedback_is_durable_context_not_command_payload(
    tmp_path: Path,
) -> None:
    root = tmp_path / "authority"
    verifier = _MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0))
    registration = replace(
        _registration(),
        editorial_feedback=(
            "Hero Title 太密，先保留完整論述。",
            "Purpose / Calling 不可拆成沒有語意的兩行。",
        ),
    )
    command_id = ApprovedCutAuthority(root, master_verifier=verifier).register(registration)

    reopened = ApprovedCutAuthority(root, master_verifier=verifier)
    command = reopened.resolve(command_id)
    context = reopened.resolve_context(
        episode_id="episode-1",
        cut_id="long-3",
        editorial_master_id="a" * 64,
        tight_cut_id="tight-long-3",
    )

    assert command is not None
    assert not hasattr(command, "editorial_feedback")
    assert context is not None
    assert context.editorial_feedback == registration.editorial_feedback


def test_restarted_production_starts_director_with_exact_registered_feedback(
    tmp_path: Path,
) -> None:
    paths = ProductionPaths(
        runtime_root=tmp_path / "runtime",
        episodes_root=tmp_path / "episodes",
    )
    verifier = _MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0))
    feedback = (
        "Hero Title 太密，先保留完整論述。",
        "Purpose / Calling 不可拆成沒有語意的兩行。",
    )
    first = FinishedCutProductionApplication.open(
        paths,
        episode_id="episode-1",
        master_verifier=verifier,
        dependencies=ProductionDependencies(
            asset_resolver=InMemoryAssetResolver(()),
            semantic_adapter=InMemorySemanticAdapter(),
        ),
    )
    command_id = first.register_approved_cut(replace(_registration(), editorial_feedback=feedback))
    semantic = InMemorySemanticAdapter()
    reopened = FinishedCutProductionApplication.open(
        paths,
        episode_id="episode-1",
        master_verifier=verifier,
        dependencies=ProductionDependencies(
            asset_resolver=InMemoryAssetResolver(()),
            semantic_adapter=semantic,
        ),
    )

    status = reopened.advance(command_id)

    request = semantic.current_request(command_id)
    assert status.state == "pending"
    assert status.current_stage == "director"
    assert request.editorial_context is not None
    assert request.editorial_context.editorial_feedback == feedback


def test_registration_rejects_path_bearing_command_identity(tmp_path: Path) -> None:
    authority = ApprovedCutAuthority(
        tmp_path / "authority",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
    )

    with pytest.raises(ApprovedCutRegistrationError, match="identity"):
        authority.register(replace(_registration(), winner_id=r"G:\old-route\winner.json"))


def test_editorial_feedback_rejects_route_state_or_asset_payloads(tmp_path: Path) -> None:
    authority = ApprovedCutAuthority(
        tmp_path / "authority",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
    )

    with pytest.raises(ApprovedCutRegistrationError, match="feedback"):
        authority.register(
            replace(
                _registration(),
                editorial_feedback=(r"請沿用 G:\old-route\state.json 的 asset_ref",),
            )
        )


def test_tight_subtitle_cues_must_fit_cut_and_canonical_section(tmp_path: Path) -> None:
    authority = ApprovedCutAuthority(
        tmp_path / "authority",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
    )
    invalid = replace(
        _registration(),
        cues=(CueAnchor("cue-1", "越界", 539.0, 541.0, "missing-section"),),
    )

    with pytest.raises(ApprovedCutRegistrationError, match="tight subtitle cues"):
        authority.register(invalid)


@pytest.mark.parametrize(
    "command_id",
    [
        "legacy:0123456789abcdef0123456789abcdef",
        "approved-cut:ffffffffffffffffffffffffffffffff",
    ],
)
def test_production_rejects_legacy_or_unknown_command_before_run_creation(
    tmp_path: Path,
    command_id: str,
) -> None:
    application = FinishedCutProductionApplication.open(
        ProductionPaths(tmp_path / "runtime", tmp_path / "episodes"),
        episode_id="episode-1",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
        dependencies=ProductionDependencies(
            asset_resolver=InMemoryAssetResolver(()),
            semantic_adapter=InMemorySemanticAdapter(),
        ),
    )

    with pytest.raises(CommandRejectedError):
        application.advance(command_id)
    with pytest.raises(CommandRejectedError):
        application.status(command_id)


def test_status_is_typed_read_only_and_survives_restart(tmp_path: Path) -> None:
    paths = ProductionPaths(tmp_path / "runtime", tmp_path / "episodes")
    verifier = _MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0))
    semantic = InMemorySemanticAdapter()
    application = FinishedCutProductionApplication.open(
        paths,
        episode_id="episode-1",
        master_verifier=verifier,
        dependencies=ProductionDependencies(
            asset_resolver=InMemoryAssetResolver(()),
            semantic_adapter=semantic,
        ),
    )
    command_id = application.register_approved_cut(_registration())
    registered = application.status(command_id)
    application.advance(command_id)
    restarted_semantic = InMemorySemanticAdapter()
    restarted = FinishedCutProductionApplication.open(
        paths,
        episode_id="episode-1",
        master_verifier=verifier,
        dependencies=ProductionDependencies(
            asset_resolver=InMemoryAssetResolver(()),
            semantic_adapter=restarted_semantic,
        ),
    )

    status = restarted.status(command_id)

    assert registered.state == "registered"
    assert registered.run_id is None
    assert status.state == "pending"
    assert status.current_stage == "director"
    with pytest.raises(KeyError):
        restarted_semantic.current_request(command_id)


def test_application_inspects_and_requests_one_pre_release_event_correction(
    tmp_path: Path,
) -> None:
    semantic = InMemorySemanticAdapter()
    application = FinishedCutProductionApplication.open(
        ProductionPaths(tmp_path / "runtime", tmp_path / "episodes"),
        episode_id="episode-1",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
        dependencies=ProductionDependencies(
            asset_resolver=InMemoryAssetResolver(()),
            semantic_adapter=semantic,
        ),
    )
    command_id = application.register_approved_cut(_registration())
    application.advance(command_id)
    director = semantic.current_request(command_id)
    semantic.respond(
        director,
        events=(
            DirectorEventProposal(
                event_id="event-1",
                master_cue_ids=("cue-1",),
                intent="保留完整論述",
                display="完整主詞與命題",
                semantic_kind="intentional_aroll",
                intentional_aroll=True,
            ),
        ),
    )
    application.advance(command_id)

    checkpoint = application.inspect_run(command_id)
    request_id = application.request_correction(
        command_id,
        "director",
        "event-1",
        "補上完整主詞。",
    )

    assert checkpoint.current_stages[0].events[0].display == "完整主詞與命題"
    assert checkpoint.outstanding_stage == "dp"
    assert re.fullmatch(r"request-[0-9a-f]{32}", request_id)
    corrected = application.inspect_run(command_id)
    assert corrected.outstanding_stage == "director"
    assert corrected.outstanding_scope == "event_retry"
    assert corrected.outstanding_event_id == "event-1"


def test_editorial_feedback_is_director_only_and_not_forwarded_to_dp(tmp_path: Path) -> None:
    semantic = InMemorySemanticAdapter()
    application = FinishedCutProductionApplication.open(
        ProductionPaths(tmp_path / "runtime", tmp_path / "episodes"),
        episode_id="episode-1",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
        dependencies=ProductionDependencies(
            asset_resolver=InMemoryAssetResolver(()),
            semantic_adapter=semantic,
        ),
    )
    command_id = application.register_approved_cut(
        replace(_registration(), editorial_feedback=("Hero Title 太密。",))
    )
    application.advance(command_id)
    director = semantic.current_request(command_id)
    semantic.respond(
        director,
        events=(
            DirectorEventProposal(
                event_id="event-1",
                master_cue_ids=("cue-1",),
                intent="保留 A-roll 完整說明",
                display="保留講者原畫面",
                semantic_kind="intentional_aroll",
                intentional_aroll=True,
            ),
        ),
    )

    application.advance(command_id)
    application.advance(command_id)

    dp = semantic.current_request(command_id)
    assert dp.stage == "dp"
    assert dp.editorial_context is None


def test_targeted_revision_can_only_name_an_event_of_exact_current(tmp_path: Path) -> None:
    current = InMemoryPlanRecordIndex()
    artifact = ReleaseArtifact("artifact.bin", 1, "b" * 64)
    release = plan_record(
        plan_id="release-current",
        episode_id="episode-1",
        cut_id="long-3",
        format="long",
        command_id="approved-cut:0123456789abcdef0123456789abcdef",
        run_id="run-1",
        editorial_master_id="a" * 64,
        winner_id="winner-long-3",
        tight_cut_id="tight-long-3",
        director_acceptance_id="acceptance-director",
        dp_acceptance_id="acceptance-dp",
        visual_acceptance_id="acceptance-visual",
        events=(
            EventRecord(
                event_id="event-1",
                master_cue_ids=("cue-1",),
                text_hash="c" * 64,
                intent="保留完整論述",
                text="第一句",
                t0=0.0,
                t1=270.0,
                section_id="section-1",
                display="保留講者原畫面",
                semantic_kind="intentional_aroll",
                intentional_aroll=True,
                implementation_kind="intentional_aroll",
            ),
        ),
        preview=artifact,
        subtitle=artifact,
    )
    current.publish((release,))
    application = FinishedCutProductionApplication.open(
        ProductionPaths(tmp_path / "runtime", tmp_path / "episodes"),
        episode_id="episode-1",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
        dependencies=ProductionDependencies(
            asset_resolver=InMemoryAssetResolver(()),
            semantic_adapter=InMemorySemanticAdapter(),
            plan_records=current,
        ),
    )

    revision_id = application.request_revision("release-current", "event-1", "Hero Title 請縮小。")

    assert re.fullmatch(r"targeted-revision:[0-9a-f]{32}", revision_id)
    assert application.status(revision_id).state == "registered"
    with pytest.raises(CommandRejectedError, match="plan record is not current"):
        application.request_revision("release-old", "event-1", "不能套舊 release")


def test_episode_scoped_composition_rejects_another_episode_approved_cut(
    tmp_path: Path,
) -> None:
    paths = ProductionPaths(tmp_path / "runtime", tmp_path / "episodes")
    verifier = _MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0))
    first = FinishedCutProductionApplication.open(
        paths,
        episode_id="episode-1",
        master_verifier=verifier,
        dependencies=ProductionDependencies(
            asset_resolver=InMemoryAssetResolver(()),
            semantic_adapter=InMemorySemanticAdapter(),
        ),
    )
    command_id = first.register_approved_cut(_registration())
    other = FinishedCutProductionApplication.open(
        paths,
        episode_id="episode-2",
        master_verifier=verifier,
        dependencies=ProductionDependencies(
            asset_resolver=InMemoryAssetResolver(()),
            semantic_adapter=InMemorySemanticAdapter(),
        ),
    )

    with pytest.raises(CommandRejectedError, match="another episode"):
        other.advance(command_id)


def test_semantic_ready_reports_only_resolve_materialization_as_not_connected(
    tmp_path: Path,
) -> None:
    semantic = InMemorySemanticAdapter()
    application = FinishedCutProductionApplication.open(
        ProductionPaths(tmp_path / "runtime", tmp_path / "episodes"),
        episode_id="episode-1",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
        dependencies=ProductionDependencies(
            asset_resolver=InMemoryAssetResolver(()),
            semantic_adapter=semantic,
            long_policy=_AcceptingPolicy(),
        ),
    )
    command_id = application.register_approved_cut(_registration())
    director = _request_for_stage(application, semantic, command_id, "director")
    semantic.respond(
        director,
        events=(
            DirectorEventProposal(
                "event-1",
                ("cue-1",),
                "保留 A-roll 完整論述",
                "保留講者原畫面",
                "intentional_aroll",
                intentional_aroll=True,
            ),
        ),
    )
    dp = _request_for_stage(application, semantic, command_id, "dp")
    semantic.respond(
        dp,
        events=(DPEventProposal("event-1", "intentional_aroll", None, None),),
    )
    visual = _request_for_stage(application, semantic, command_id, "visual_review")
    semantic.respond(
        visual,
        events=(VisualEventProposal("event-1", "approved"),),
    )

    checkpoint = application.advance(command_id)
    status = application.advance(command_id)

    assert checkpoint.state == "pending"
    assert checkpoint.current_stage is None
    assert status.state == "pending"
    assert status.current_stage == "materialization"
    assert status.reason_code == "resolve_materialization_not_connected"


@requires_local_hyperframes
def test_production_composition_wires_offline_long_media_builder_across_restart(
    tmp_path: Path,
) -> None:
    paths = ProductionPaths(tmp_path / "runtime", tmp_path / "episodes")

    first = build_production_application(paths, "episode-1")
    reopened = build_production_application(paths, "episode-1")

    first_builder = first._production._derived_asset_builder
    reopened_builder = reopened._production._derived_asset_builder
    assert isinstance(first_builder, LongDerivedAssetBuilder)
    assert isinstance(reopened_builder, LongDerivedAssetBuilder)
    first_runtime = first_builder._title_renderer._browser._runtime
    reopened_runtime = reopened_builder._title_renderer._browser._runtime
    assert first_runtime == reopened_runtime
    assert first_runtime.receipt_content_hash == (
        "59037c5dfd0c6769e2f6c43e5f31894913d7b6a3a7d5847d265da1a5a5a3938d"
    )


@requires_local_hyperframes
def test_failed_director_dispatch_is_needs_review_and_never_redispatches_after_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = ProductionPaths(tmp_path / "runtime", tmp_path / "episodes")
    authority = ApprovedCutAuthority(
        paths.runtime_root / "approved-cuts",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
    )
    command_id = authority.register(_registration())
    dispatches: list[tuple[str, ...]] = []

    def _record_dispatch(self, argv, **_kwargs):
        dispatches.append(argv)
        return CodexProcessResult(returncode=1)

    monkeypatch.setattr(SubprocessCodexProcessRunner, "run", _record_dispatch)

    first = build_production_application(paths, "episode-1")
    first_status = first.advance(command_id)
    reopened = build_production_application(paths, "episode-1")
    reopened_status = reopened.advance(command_id)

    assert len(dispatches) == 1
    assert reopened_status.run_id == first_status.run_id
    assert first_status.state == "needs_review"
    assert first_status.reason_code == "semantic_dispatch_failed"
    assert reopened_status.state == "needs_review"
    assert reopened_status.reason_code == "semantic_dispatch_failed"
    retry_request_id = reopened.retry_failed_dispatch(command_id)
    assert retry_request_id.startswith("request-")


@requires_local_hyperframes
def test_successful_director_outcome_survives_restart_and_dp_dispatches_once(
    tmp_path: Path,
) -> None:
    paths = ProductionPaths(tmp_path / "runtime", tmp_path / "episodes")
    authority = ApprovedCutAuthority(
        paths.runtime_root / "approved-cuts",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
    )
    command_id = authority.register(_registration())
    stages: list[str] = []

    class _SuccessfulRunner:
        def run(self, argv, *, cwd, **_kwargs):
            request = json.loads((cwd / "packet.json").read_text(encoding="utf-8"))["request"]
            stage = request["stage"]
            stages.append(stage)
            events = (
                [
                    {
                        "event_id": "event-1",
                        "master_cue_ids": ["cue-1"],
                        "intent": "保留完整論述",
                        "display": "保留講者原畫面",
                        "semantic_kind": "intentional_aroll",
                        "intentional_aroll": True,
                    }
                ]
                if stage == "director"
                else [
                    {
                        "event_id": "event-1",
                        "implementation_kind": "intentional_aroll",
                        "lane": None,
                        "asset_ref": None,
                        "placement_cue_ids": [],
                    }
                ]
            )
            response = {
                key: request[key]
                for key in (
                    "schema",
                    "run_id",
                    "request_id",
                    "episode_id",
                    "cut_id",
                    "format",
                    "stage",
                    "attempt",
                    "scope",
                    "event_id",
                    "parent_acceptance_id",
                )
            }
            response["events"] = events
            output_path = Path(argv[argv.index("--output-last-message") + 1])
            output_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
            return CodexProcessResult(returncode=0)

    runner = _SuccessfulRunner()
    first = build_production_application(paths, "episode-1", process_runner=runner)
    first_status = first.advance(command_id)
    reopened = build_production_application(paths, "episode-1", process_runner=runner)
    reopened_status = reopened.advance(command_id)

    assert first_status.current_stage == "dp"
    assert reopened_status.current_stage == "visual_review"
    assert stages == ["director", "dp"]


@requires_local_hyperframes
def test_restart_after_claim_without_outcome_is_indeterminate_and_never_redispatches(
    tmp_path: Path,
) -> None:
    paths = ProductionPaths(tmp_path / "runtime", tmp_path / "episodes")
    authority = ApprovedCutAuthority(
        paths.runtime_root / "approved-cuts",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
    )
    command_id = authority.register(_registration())
    child_calls = 0

    class _CrashingRunner:
        def run(self, _argv, **_kwargs):
            nonlocal child_calls
            child_calls += 1
            raise SystemExit("simulated process death after durable claim")

    first = build_production_application(
        paths,
        "episode-1",
        process_runner=_CrashingRunner(),
    )
    with pytest.raises(SystemExit):
        first.advance(command_id)

    reopened = build_production_application(
        paths,
        "episode-1",
        process_runner=_CrashingRunner(),
    )
    status = reopened.advance(command_id)

    assert child_calls == 1
    assert status.state == "needs_review"
    assert status.reason_code == "semantic_dispatch_failed"


@requires_local_hyperframes
def test_wrong_request_response_is_durably_rejected_without_redispatch(
    tmp_path: Path,
) -> None:
    paths = ProductionPaths(tmp_path / "runtime", tmp_path / "episodes")
    authority = ApprovedCutAuthority(
        paths.runtime_root / "approved-cuts",
        master_verifier=_MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0)),
    )
    command_id = authority.register(_registration())
    child_calls = 0

    class _WrongRequestRunner:
        def run(self, argv, *, cwd, **_kwargs):
            nonlocal child_calls
            child_calls += 1
            request = json.loads((cwd / "packet.json").read_text(encoding="utf-8"))["request"]
            response = {
                key: request[key]
                for key in (
                    "schema",
                    "run_id",
                    "request_id",
                    "episode_id",
                    "cut_id",
                    "format",
                    "stage",
                    "attempt",
                    "scope",
                    "event_id",
                    "parent_acceptance_id",
                )
            }
            response["request_id"] = "request-00000000000000000000000000000000"
            response["events"] = [
                {
                    "event_id": "event-1",
                    "master_cue_ids": ["cue-1"],
                    "intent": "stale replay",
                    "display": "stale replay",
                    "semantic_kind": "hero_title",
                    "intentional_aroll": False,
                }
            ]
            output_path = Path(argv[argv.index("--output-last-message") + 1])
            output_path.write_text(json.dumps(response), encoding="utf-8")
            return CodexProcessResult(returncode=0)

    runner = _WrongRequestRunner()
    first = build_production_application(paths, "episode-1", process_runner=runner)
    first_status = first.advance(command_id)
    reopened = build_production_application(paths, "episode-1", process_runner=runner)
    reopened_status = reopened.advance(command_id)

    assert child_calls == 1
    assert first_status.state == "needs_review"
    assert first_status.reason_code == "semantic_dispatch_failed"
    assert reopened_status == first_status


def test_targeted_revision_dispatches_its_new_event_request_once_without_full_stage_retry(
    tmp_path: Path,
) -> None:
    paths = ProductionPaths(tmp_path / "runtime", tmp_path / "episodes")
    verifier = _MasterVerifier(VerifiedEditorialMaster("episode-1", "a" * 64, 1_200.0))
    current = InMemoryPlanRecordIndex()
    base_semantic = InMemorySemanticAdapter()
    base = FinishedCutProductionApplication.open(
        paths,
        episode_id="episode-1",
        master_verifier=verifier,
        dependencies=ProductionDependencies(
            asset_resolver=InMemoryAssetResolver(()),
            semantic_adapter=base_semantic,
            plan_records=current,
            long_policy=_AcceptingPolicy(),
        ),
    )
    base_command_id = base.register_approved_cut(_registration())
    director = _request_for_stage(base, base_semantic, base_command_id, "director")
    base_semantic.respond(
        director,
        events=(
            DirectorEventProposal(
                "event-1",
                ("cue-1",),
                "保留 A-roll 完整論述",
                "保留講者原畫面",
                "intentional_aroll",
                intentional_aroll=True,
            ),
        ),
    )
    dp = _request_for_stage(base, base_semantic, base_command_id, "dp")
    base_semantic.respond(
        dp,
        events=(DPEventProposal("event-1", "intentional_aroll", None, None),),
    )
    visual = _request_for_stage(base, base_semantic, base_command_id, "visual_review")
    base_semantic.respond(
        visual,
        events=(VisualEventProposal("event-1", "approved"),),
    )
    checkpoint = base.advance(base_command_id)
    base.advance(base_command_id)
    assert checkpoint.state == "pending"
    run_store_root = paths.runtime_root / "episodes" / "episode-1" / "runs"
    stored = _FilesystemProductionStore(run_store_root).load_run(base_command_id)
    assert stored is not None
    plan = stored.view.materialization_plan
    assert plan is not None
    artifact = ReleaseArtifact("artifact.bin", 1, "b" * 64)
    release = plan_record(
        plan_id="release-current",
        episode_id="episode-1",
        cut_id="long-3",
        format="long",
        command_id=base_command_id,
        run_id=stored.view.run_id,
        editorial_master_id="a" * 64,
        winner_id="winner-long-3",
        tight_cut_id="tight-long-3",
        director_acceptance_id=plan.director_acceptance_id,
        dp_acceptance_id=plan.dp_acceptance_id,
        visual_acceptance_id=plan.visual_acceptance_id,
        events=plan.events,
        components=plan.components,
        preview=artifact,
        subtitle=artifact,
    )
    current.publish((release,))
    worker_requests = []

    class _FailingTargetedWorker:
        def dispatch(self, request):
            worker_requests.append(request)
            return SemanticDispatchOutcome(
                request.request_id,
                "failed",
                reason_code="semantic_dispatch_failed",
                diagnostic="fixture failure",
            )

        def outcome_for(self, _request_id):
            return None

    def _open_targeted() -> FinishedCutProductionApplication:
        semantic = DurableSemanticAdapter(
            _FailingTargetedWorker(),
            _FilesystemSemanticDispatchLedger(run_store_root),
        )
        return FinishedCutProductionApplication.open(
            paths,
            episode_id="episode-1",
            master_verifier=verifier,
            dependencies=ProductionDependencies(
                asset_resolver=InMemoryAssetResolver(()),
                semantic_adapter=semantic,
                plan_records=current,
                long_policy=_AcceptingPolicy(),
            ),
        )

    targeted = _open_targeted()
    revision_id = targeted.request_revision(
        "release-current",
        "event-1",
        "只修改這一個事件。",
    )
    first_status = targeted.advance(revision_id)
    reopened_status = _open_targeted().advance(revision_id)

    assert len(worker_requests) == 1
    assert worker_requests[0].scope == "event_retry"
    assert worker_requests[0].event_id == "event-1"
    assert worker_requests[0].request_id not in {
        stage.request_id for stage in stored.view.accepted_stages
    }
    assert tuple(event.event_id for event in worker_requests[0].events) == ("event-1",)
    assert first_status.state == "needs_review"
    assert first_status.reason_code == "semantic_dispatch_failed"
    assert reopened_status == first_status
    assert base.status(base_command_id).current_stage == "materialization"


def test_accepted_stage_lookup_survives_a_retired_projection_in_history(tmp_path) -> None:
    """歷史可以留著已退役的 projection，只是不准再拿它來 build。

    20260805 的 authority store 有兩筆 `supporting_title` derived instruction，是那個
    lane 退役之前寫下的。`accepted_stages()` 原本會把整個 view（含 derived
    instruction）重建，而 DerivedAssetInstruction 在 __post_init__ 重跑 active
    projection 契約——於是那兩筆讓每一次 acceptance 查詢都 raise，連帶讓每一次修訂
    都不可能開始。
    """
    root = tmp_path / "runs"
    root.mkdir()
    (root / "authority.json").write_text(
        json.dumps(
            {
                "schema": "nakama.finished-cut-production-store.v1",
                "targeted_revisions": {},
                "runs": {
                    "approved-cut:legacy": {
                        "run_id": "run-legacy",
                        "command_id": "approved-cut:legacy",
                        "view": {
                            "accepted_stages": [],
                            "derived_asset_request": {
                                "instructions": [
                                    {
                                        "component_id": "c1",
                                        "event_id": "e1",
                                        "semantic_kind": "supporting_title",
                                        "implementation_kind": "supporting_title",
                                        "lane": "supporting_title",
                                        "display": "退役的 lane",
                                        "t0": 1.0,
                                        "t1": 2.0,
                                        "source_asset_ref": None,
                                        "geometry": None,
                                        "recipe_identity": None,
                                    }
                                ]
                            },
                        },
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    store = _FilesystemProductionStore(root)

    assert store.accepted_stages() == ()
    assert store.load_accepted("acceptance-does-not-exist") is None
