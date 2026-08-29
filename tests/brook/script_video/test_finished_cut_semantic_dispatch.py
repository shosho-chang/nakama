from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

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
    _FilesystemSemanticDispatchLedger,
)


def _request() -> StageRequest:
    return StageRequest(
        run_id="run-" + "1" * 32,
        request_id="request-" + "2" * 32,
        command_id="approved-cut:" + "3" * 32,
        episode_id="episode-1",
        cut_id="long-3",
        format="long",
        stage="director",
        attempt=1,
        scope="full_stage",
        event_id=None,
        parent_acceptance_id=None,
    )


def test_ready_proposal_round_trips_after_restart_without_second_worker_call(tmp_path) -> None:
    request = _request()
    calls = 0
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
                "event-1",
                ("cue-1",),
                "current intent",
                "current display",
                "hero_title",
            ),
        ),
    )

    class _ReadyWorker:
        def dispatch(self, current: StageRequest) -> SemanticDispatchOutcome:
            nonlocal calls
            calls += 1
            return SemanticDispatchOutcome(current.request_id, "ready", proposal=proposal)

        def outcome_for(self, _request_id: str) -> None:
            return None

    first = DurableSemanticAdapter(
        _ReadyWorker(),
        _FilesystemSemanticDispatchLedger(tmp_path),
    ).dispatch(request)
    reopened = DurableSemanticAdapter(
        _ReadyWorker(),
        _FilesystemSemanticDispatchLedger(tmp_path),
    ).dispatch(request)

    assert calls == 1
    assert reopened == first
    assert reopened.proposal == proposal


def test_concurrent_durable_claims_launch_exactly_one_worker(tmp_path) -> None:
    request = _request()
    entered = Event()
    release = Event()
    calls = 0
    calls_lock = Lock()

    class _BlockingWorker:
        def dispatch(self, current: StageRequest) -> SemanticDispatchOutcome:
            nonlocal calls
            with calls_lock:
                calls += 1
            entered.set()
            assert release.wait(timeout=5)
            return SemanticDispatchOutcome(
                current.request_id,
                "failed",
                reason_code="semantic_process_failed",
                diagnostic="fixture failure",
            )

        def outcome_for(self, _request_id: str) -> None:
            return None

    worker = _BlockingWorker()
    first = DurableSemanticAdapter(
        worker,
        _FilesystemSemanticDispatchLedger(tmp_path),
    )
    second = DurableSemanticAdapter(
        worker,
        _FilesystemSemanticDispatchLedger(tmp_path),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(first.dispatch, request)
        assert entered.wait(timeout=5)
        loser = pool.submit(second.dispatch, request)
        loser_outcome = loser.result(timeout=5)
        release.set()
        winner_outcome = winner.result(timeout=5)

    assert calls == 1
    assert winner_outcome.state == "failed"
    assert loser_outcome.state == "indeterminate"
