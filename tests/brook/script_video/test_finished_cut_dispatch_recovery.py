from __future__ import annotations

from dataclasses import replace

import pytest

from agents.brook.script_video.finished_cut_production import (
    CommandRejectedError,
    FinishedCutProduction,
)
from agents.brook.script_video.finished_cut_production._assets import InMemoryAssetResolver
from agents.brook.script_video.finished_cut_production._commands import ApprovedCutCommand
from agents.brook.script_video.finished_cut_production._context import (
    CanonicalSection,
    CueAnchor,
    CutSourceRange,
    EditorialCutContext,
    InMemoryEditorialCutContextResolver,
)
from agents.brook.script_video.finished_cut_production._records import (
    DirectorEventProposal,
    StageProposal,
    StageRequest,
)
from agents.brook.script_video.finished_cut_production._semantic import (
    DurableSemanticAdapter,
    SemanticDispatchOutcome,
)
from agents.brook.script_video.finished_cut_production._store import (
    InMemoryApprovedCutStore,
    _FilesystemProductionStore,
    _FilesystemSemanticDispatchLedger,
    _StoredRun,
)

COMMAND_ID = "approved-cut:0123456789abcdef0123456789abcdef"


def _approved_cut() -> ApprovedCutCommand:
    return ApprovedCutCommand(
        command_id=COMMAND_ID,
        episode_id="episode-recovery",
        cut_id="short-1",
        format="short",
        editorial_master_id="master-current",
        winner_id="winner-current",
        tight_cut_id="tight-current",
    )


def _context() -> EditorialCutContext:
    text = "工作除了 Purpose，也需要清楚知道自己的 Calling。"
    return EditorialCutContext(
        episode_id="episode-recovery",
        cut_id="short-1",
        format="short",
        editorial_master_id="master-current",
        tight_cut_id="tight-current",
        duration_sec=45.0,
        source_ranges=(CutSourceRange(0.0, 45.0),),
        cues=(CueAnchor("cue-1", text, 0.0, 4.0, "section-1"),),
        sections=(CanonicalSection("section-1", "工作的意義", 0.0),),
    )


class _FailingWorker:
    def __init__(self, reason_code: str = "semantic_dispatch_error") -> None:
        self.calls = 0
        self.request_ids: list[str] = []
        self.reason_code = reason_code

    def dispatch(self, request: StageRequest) -> SemanticDispatchOutcome:
        self.calls += 1
        self.request_ids.append(request.request_id)
        return SemanticDispatchOutcome(
            request.request_id,
            "failed",
            reason_code=self.reason_code,
            diagnostic="[WinError 5] Access is denied",
        )

    def outcome_for(self, _request_id: str) -> None:
        return None

    def dispatch_state(self, _request_id: str) -> str:
        return "unclaimed"


class _ReadyDirectorWorker:
    def __init__(self) -> None:
        self.calls = 0

    def dispatch(self, request: StageRequest) -> SemanticDispatchOutcome:
        self.calls += 1
        proposal = StageProposal(
            run_id=request.run_id,
            request_id=request.request_id,
            episode_id=request.episode_id,
            cut_id=request.cut_id,
            format=request.format,
            stage=request.stage,
            attempt=request.attempt,
            scope=request.scope,
            event_id=request.event_id,
            parent_acceptance_id=request.parent_acceptance_id,
            events=(
                DirectorEventProposal(
                    "event-purpose",
                    ("cue-1",),
                    "State the complete relationship",
                    "Purpose 與 Calling 共同定義工作的意義",
                    "hero_title",
                ),
            ),
        )
        return SemanticDispatchOutcome(request.request_id, "ready", proposal=proposal)

    def outcome_for(self, _request_id: str) -> None:
        return None

    def dispatch_state(self, _request_id: str) -> str:
        return "unclaimed"


def _production(tmp_path, worker) -> FinishedCutProduction:
    run_root = tmp_path / "runtime"
    return FinishedCutProduction(
        store_root=run_root,
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=DurableSemanticAdapter(
            worker,
            _FilesystemSemanticDispatchLedger(run_root),
        ),
        context_resolver=InMemoryEditorialCutContextResolver((_context(),)),
    )


def test_failed_first_dispatch_requires_one_explicit_recovery_after_restart(
    tmp_path,
) -> None:
    failing = _FailingWorker()
    first = _production(tmp_path, failing)
    failed = first.advance(COMMAND_ID)
    failed_checkpoint = first.inspect_run(COMMAND_ID)
    store = _FilesystemProductionStore(tmp_path / "runtime")
    stored_failure = store.load_run(COMMAND_ID)
    assert stored_failure is not None
    failed_request = stored_failure.view.outstanding_request
    assert failed_request is not None
    old_ledger_path = tmp_path / "runtime" / "semantic-dispatch" / f"{failing.request_ids[0]}.json"
    old_ledger_bytes = old_ledger_path.read_bytes()
    ready_worker = _ReadyDirectorWorker()
    restarted = _production(tmp_path, ready_worker)
    unchanged = restarted.advance(COMMAND_ID)
    new_request_id = restarted.retry_failed_dispatch(COMMAND_ID)
    recovery_checkpoint = restarted.inspect_run(COMMAND_ID)
    stored_recovery = store.load_run(COMMAND_ID)
    assert stored_recovery is not None
    retry_request = stored_recovery.view.outstanding_request
    assert retry_request == replace(
        failed_request,
        request_id=new_request_id,
        attempt=failed_request.attempt + 1,
    )
    assert ready_worker.calls == 0
    retry_restarted = _production(tmp_path, ready_worker)
    with pytest.raises(CommandRejectedError):
        retry_restarted.retry_failed_dispatch(COMMAND_ID)
    accepted = retry_restarted.advance(COMMAND_ID)
    checkpoint = retry_restarted.inspect_run(COMMAND_ID)

    assert failed.status == "needs_review"
    assert failed_checkpoint.current_stages == ()
    assert failing.calls == 1
    assert unchanged.status == "needs_review"
    assert ready_worker.calls == 1
    assert new_request_id.startswith("request-")
    assert new_request_id != failing.request_ids[0]
    assert recovery_checkpoint.current_stages == ()
    assert old_ledger_path.read_bytes() == old_ledger_bytes
    old_outcome = _FilesystemSemanticDispatchLedger(tmp_path / "runtime").outcome_for(
        failing.request_ids[0]
    )
    assert old_outcome is not None
    assert old_outcome.reason_code == "semantic_dispatch_error"
    assert accepted.current_stage == "dp"
    assert checkpoint.current_stages[0].stage == "director"
    assert checkpoint.current_stages[0].attempt == 2
    with pytest.raises(CommandRejectedError):
        retry_restarted.retry_failed_dispatch(COMMAND_ID)


def test_claimed_request_without_terminal_outcome_cannot_be_recovered(tmp_path) -> None:
    class _CrashingWorker:
        def dispatch(self, _request: StageRequest) -> SemanticDispatchOutcome:
            raise SystemExit("simulated process death after durable claim")

        def outcome_for(self, _request_id: str) -> None:
            return None

        def dispatch_state(self, _request_id: str) -> str:
            return "unclaimed"

    first = _production(tmp_path, _CrashingWorker())
    with pytest.raises(SystemExit):
        first.advance(COMMAND_ID)
    ready_worker = _ReadyDirectorWorker()
    restarted = _production(tmp_path, ready_worker)
    indeterminate = restarted.advance(COMMAND_ID)

    with pytest.raises(CommandRejectedError, match="terminal dispatch failure"):
        restarted.retry_failed_dispatch(COMMAND_ID)

    assert indeterminate.status == "needs_review"
    assert ready_worker.calls == 0


def test_completed_failure_can_be_recovered_after_run_save_crash_window(tmp_path) -> None:
    failing = _FailingWorker()
    first = _production(tmp_path, failing)
    first.advance(COMMAND_ID)
    store = _FilesystemProductionStore(tmp_path / "runtime")
    stored = store.load_run(COMMAND_ID)
    assert stored is not None
    store.save_run(
        _StoredRun(
            command=stored.command,
            view=replace(stored.view, status="pending"),
            worker_catalog=stored.worker_catalog,
            base_release_id=stored.base_release_id,
        )
    )
    restarted = _production(tmp_path, _ReadyDirectorWorker())

    retry_request_id = restarted.retry_failed_dispatch(COMMAND_ID)

    assert retry_request_id.startswith("request-")
    assert retry_request_id != failing.request_ids[0]


def test_recovery_rejects_wrong_stage_that_differs_from_durable_claim(tmp_path) -> None:
    first = _production(tmp_path, _FailingWorker())
    first.advance(COMMAND_ID)
    store = _FilesystemProductionStore(tmp_path / "runtime")
    stored = store.load_run(COMMAND_ID)
    assert stored is not None
    request = stored.view.outstanding_request
    assert request is not None
    store.save_run(
        _StoredRun(
            command=stored.command,
            view=replace(
                stored.view,
                outstanding_request=replace(request, stage="dp"),
            ),
            worker_catalog=stored.worker_catalog,
            base_release_id=stored.base_release_id,
        )
    )
    restarted = _production(tmp_path, _ReadyDirectorWorker())

    with pytest.raises(CommandRejectedError, match="does not match durable dispatch"):
        restarted.retry_failed_dispatch(COMMAND_ID)


def test_recovery_accepts_any_typed_terminal_worker_failure(tmp_path) -> None:
    first = _production(tmp_path, _FailingWorker("worker_process_failed"))
    first.advance(COMMAND_ID)
    restarted = _production(tmp_path, _ReadyDirectorWorker())

    request_id = restarted.retry_failed_dispatch(COMMAND_ID)

    assert request_id.startswith("request-")


def test_completed_ready_proposal_cannot_be_recovered_as_failure(tmp_path) -> None:
    class _CrashAfterClaim:
        def dispatch(self, _request: StageRequest) -> SemanticDispatchOutcome:
            raise SystemExit("leave a durable claim")

    first = _production(tmp_path, _CrashAfterClaim())
    with pytest.raises(SystemExit):
        first.advance(COMMAND_ID)
    store = _FilesystemProductionStore(tmp_path / "runtime")
    stored = store.load_run(COMMAND_ID)
    assert stored is not None
    request = stored.view.outstanding_request
    assert request is not None
    ledger = _FilesystemSemanticDispatchLedger(tmp_path / "runtime")
    ready_outcome = _ReadyDirectorWorker().dispatch(request)
    ledger.complete(request, ready_outcome)
    recovery_worker = _ReadyDirectorWorker()
    restarted = _production(tmp_path, recovery_worker)

    with pytest.raises(CommandRejectedError, match="terminal dispatch failure"):
        restarted.retry_failed_dispatch(COMMAND_ID)

    assert recovery_worker.calls == 0


def test_explicit_recovery_can_redispatch_one_rejected_pre_release_correction(
    tmp_path,
) -> None:
    class _CorrectionWorker:
        def __init__(self, *, keep_anchor: bool) -> None:
            self.keep_anchor = keep_anchor
            self.calls = 0

        def dispatch(self, request: StageRequest) -> SemanticDispatchOutcome:
            self.calls += 1
            cue_ids = (
                request.events[0].master_cue_ids
                if request.scope == "event_retry" and self.keep_anchor
                else (("cue-missing",) if request.scope == "event_retry" else ("cue-1",))
            )
            proposal = StageProposal(
                run_id=request.run_id,
                request_id=request.request_id,
                episode_id=request.episode_id,
                cut_id=request.cut_id,
                format=request.format,
                stage=request.stage,
                attempt=request.attempt,
                scope=request.scope,
                event_id=request.event_id,
                parent_acceptance_id=request.parent_acceptance_id,
                events=(
                    DirectorEventProposal(
                        "event-purpose",
                        cue_ids,
                        "Use one honest B-roll cutaway",
                        "AI workflow cutaway",
                        "b_roll",
                    ),
                ),
            )
            return SemanticDispatchOutcome(request.request_id, "ready", proposal=proposal)

        def outcome_for(self, _request_id: str) -> None:
            return None

        def dispatch_state(self, _request_id: str) -> str:
            return "unclaimed"

    initial = _production(tmp_path, _ReadyDirectorWorker())
    initial.advance(COMMAND_ID)
    initial.request_correction(
        COMMAND_ID,
        "director",
        "event-purpose",
        "Keep the exact Semantic Evidence Range and change this event to B-roll.",
    )
    rejected = _production(tmp_path, _CorrectionWorker(keep_anchor=False))
    rejected_view = rejected.advance(COMMAND_ID)
    assert rejected_view.status == "needs_review"

    recovered = _production(tmp_path, _CorrectionWorker(keep_anchor=True))
    retry_request_id = recovered.retry_failed_dispatch(COMMAND_ID)
    accepted = recovered.advance(COMMAND_ID)

    assert retry_request_id.startswith("request-")
    assert accepted.current_stage == "dp"


def test_a_second_consecutive_dispatch_failure_is_still_recoverable(tmp_path) -> None:
    """人可以要求重試不只一次。

    這支指令只有人會發動（CLI；watcher 從不呼叫它），所以把它卡在 attempt 1
    擋的不是暴衝重試，而是「一個人只准問一次」。targeted revision 的 stage 連錯
    兩次之後就完全沒有復原路徑——request_correction 明文拒絕 revision run，
    watcher 又只撿 queued 的工作。20260805 那六個卡住的 needs_review 就是這樣來的。
    """
    failing = _FailingWorker()
    production = _production(tmp_path, failing)

    assert production.advance(COMMAND_ID).status == "needs_review"
    first_retry = production.retry_failed_dispatch(COMMAND_ID)

    # 第二次 dispatch 同樣失敗——舊的閘門會在這裡把人擋死。
    assert production.advance(COMMAND_ID).status == "needs_review"
    second_retry = production.retry_failed_dispatch(COMMAND_ID)

    assert second_retry.startswith("request-")
    assert second_retry != first_retry
    store = _FilesystemProductionStore(tmp_path / "runtime")
    stored = store.load_run(COMMAND_ID)
    assert stored is not None
    outstanding = stored.view.outstanding_request
    assert outstanding is not None
    assert outstanding.request_id == second_retry
    assert outstanding.attempt == 3
