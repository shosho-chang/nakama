"""TDD contract for the subscription-worker visual pipeline orchestrator."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.podcast_highlight_visual_orchestrator as orchestrator
from tests.brook.script_video import test_highlight_visual_pipeline as core_fixtures

DIRECTOR_SESSION = "0199a213-81c0-7800-8aa1-bbab2a035a53"
DP_SESSION = "0299a213-81c0-7800-8aa1-bbab2a035a54"
WRONG_SESSION = "0399a213-81c0-7800-8aa1-bbab2a035a55"
REVISION_ID = "r-a1b2c3d4e5f60718293a4b5c"
SECOND_REVISION_ID = "r-b1c2d3e4f5061728394a5b6c"


@dataclass
class _Artifact:
    document: dict[str, object]
    name: str

    def identity(self) -> dict[str, object]:
        payload = json.dumps(self.document, sort_keys=True).encode()
        return {
            "contract": str(self.document["contract"]),
            "path": self.name,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "content_hash": hashlib.sha256(payload + b"content").hexdigest(),
        }


class _FakePipeline:
    """Public-core test double; it never trusts proposal worker fields."""

    WORK_PACKET_CONTRACT = "podcast-highlight-visual-work-packet-v1"
    DIRECTOR_PLAN_CONTRACT = "podcast-highlight-director-plan-v1"
    DP_FULFILLMENT_CONTRACT = "podcast-highlight-dp-fulfillment-v1"
    SEMANTIC_AUDIT_CONTRACT = "podcast-highlight-visual-semantic-audit-v1"

    def __init__(self, revision_id: str = REVISION_ID) -> None:
        self.revision_id = revision_id
        self.director: _Artifact | None = None
        self.dp: _Artifact | None = None
        self.audit: _Artifact | None = None
        self.current = False
        self.accepted_identities: list[dict[str, str]] = []
        self.init_calls: list[Path | None] = []
        self.work = _Artifact(
            {
                "contract": self.WORK_PACKET_CONTRACT,
                "episode_id": "episode",
                "cut_id": "value-L01",
                "revision_id": revision_id,
                "cues": [{"number": 1, "text": "exact quote"}],
            },
            f"revisions/{revision_id}/DIRECTOR-WORK.json",
        )

    def visual_pipeline_status(self, _root, *, cut_id, editorial_master=None):
        del editorial_master
        if cut_id != "value-L01":
            raise AssertionError(cut_id)
        if self.current:
            state = "ready_to_materialize"
            pending = self.revision_id
            current = self.revision_id
        elif self.dp is not None:
            state = "awaiting_semantic_audit"
            pending = self.revision_id
            current = None
        elif self.director is not None:
            state = "awaiting_dp"
            pending = self.revision_id
            current = None
        else:
            state = "awaiting_director"
            pending = self.revision_id
            current = None
        return {
            "contract": "podcast-highlight-visual-status-v1",
            "episode_id": "episode",
            "cut_id": cut_id,
            "status": state,
            "pending_revision_id": pending,
            "current_revision_id": current,
            "paths": {},
        }

    def init_visual_work_packet(
        self,
        _root,
        *,
        cut_id,
        revision_request,
        editorial_master=None,
    ):
        del cut_id, editorial_master
        self.init_calls.append(Path(revision_request) if revision_request else None)
        return self.work

    def accept_director_plan(
        self,
        _root,
        *,
        cut_id,
        revision_id,
        proposal,
        worker_identity,
        editorial_master=None,
    ):
        del cut_id, editorial_master
        assert revision_id == self.revision_id
        data = json.loads(Path(proposal).read_text(encoding="utf-8"))
        self.accepted_identities.append(dict(worker_identity))
        self.director = _Artifact(
            {
                "contract": self.DIRECTOR_PLAN_CONTRACT,
                "revision_id": revision_id,
                "worker_execution": dict(worker_identity),
                "events": data.get("events", []),
            },
            f"revisions/{revision_id}/DIRECTOR-PLAN.json",
        )
        return self.director

    def load_director_plan(self, _root, *, cut_id, revision_id, editorial_master=None):
        del cut_id, editorial_master
        assert revision_id == self.revision_id
        assert self.director is not None
        return self.director

    def accept_dp_fulfillment(
        self,
        _root,
        *,
        cut_id,
        revision_id,
        proposal,
        worker_identity,
        editorial_master=None,
    ):
        del cut_id, editorial_master
        assert revision_id == self.revision_id
        assert Path(proposal).is_file()
        self.accepted_identities.append(dict(worker_identity))
        self.dp = _Artifact(
            {
                "contract": self.DP_FULFILLMENT_CONTRACT,
                "revision_id": revision_id,
                "worker_execution": dict(worker_identity),
                "implementations": [],
            },
            f"revisions/{revision_id}/DP-FULFILLMENT.json",
        )
        return self.dp

    def load_dp_fulfillment(self, _root, *, cut_id, revision_id, editorial_master=None):
        del cut_id, editorial_master
        assert revision_id == self.revision_id
        assert self.dp is not None
        return self.dp

    def load_visual_materializations(self, _root, *, cut_id, revision_id, editorial_master=None):
        del cut_id, editorial_master
        assert revision_id == self.revision_id
        return ()

    def accept_semantic_audit(
        self,
        _root,
        *,
        cut_id,
        revision_id,
        proposal,
        worker_identity,
        editorial_master=None,
    ):
        del cut_id, editorial_master
        assert revision_id == self.revision_id
        assert Path(proposal).is_file()
        self.accepted_identities.append(dict(worker_identity))
        self.audit = _Artifact(
            {
                "contract": self.SEMANTIC_AUDIT_CONTRACT,
                "revision_id": revision_id,
                "worker_execution": dict(worker_identity),
                "findings": [],
            },
            f"revisions/{revision_id}/SEMANTIC-AUDIT.json",
        )
        self.current = True
        return self.audit

    def verify_visual_pipeline(self, _root, *, cut_id, revision_id=None, editorial_master=None):
        del cut_id, editorial_master
        assert self.current
        assert revision_id in (None, self.revision_id)
        return SimpleNamespace(
            work_packet=self.work,
            director_plan=self.director,
            dp_fulfillment=self.dp,
            semantic_audit=self.audit,
            materializations=(),
        )


class _SecondGenerationPipeline(_FakePipeline):
    """A ready CURRENT plus a new immutable request-driven PENDING generation."""

    def __init__(self) -> None:
        super().__init__(SECOND_REVISION_ID)
        self.started = False
        self.current_revision_id = REVISION_ID
        self.old_selection = SimpleNamespace(
            work_packet=_Artifact(
                {"contract": self.WORK_PACKET_CONTRACT, "revision_id": REVISION_ID},
                f"revisions/{REVISION_ID}/DIRECTOR-WORK.json",
            ),
            director_plan=object(),
            dp_fulfillment=object(),
            semantic_audit=object(),
            materializations=(),
        )

    def visual_pipeline_status(self, _root, *, cut_id, editorial_master=None):
        del editorial_master
        if not self.started:
            state = "ready_to_materialize"
            pending = self.current_revision_id
        elif self.current:
            state = "ready_to_materialize"
            pending = SECOND_REVISION_ID
        elif self.dp is not None:
            state = "awaiting_semantic_audit"
            pending = SECOND_REVISION_ID
        elif self.director is not None:
            state = "awaiting_dp"
            pending = SECOND_REVISION_ID
        else:
            state = "awaiting_director"
            pending = SECOND_REVISION_ID
        return {
            "contract": "podcast-highlight-visual-status-v1",
            "episode_id": "episode",
            "cut_id": cut_id,
            "status": state,
            "pending_revision_id": pending,
            "current_revision_id": self.current_revision_id,
            "paths": {},
        }

    def init_visual_work_packet(
        self,
        _root,
        *,
        cut_id,
        revision_request,
        editorial_master=None,
    ):
        del cut_id, editorial_master
        assert revision_request is not None
        self.started = True
        self.init_calls.append(Path(revision_request))
        return self.work

    def accept_semantic_audit(self, *args, **kwargs):
        accepted = super().accept_semantic_audit(*args, **kwargs)
        self.current_revision_id = SECOND_REVISION_ID
        return accepted

    def verify_visual_pipeline(self, _root, *, cut_id, revision_id=None, editorial_master=None):
        del cut_id, editorial_master
        if revision_id is None and self.current_revision_id == REVISION_ID:
            return self.old_selection
        assert revision_id in (None, SECOND_REVISION_ID)
        assert self.current
        return SimpleNamespace(
            work_packet=self.work,
            director_plan=self.director,
            dp_fulfillment=self.dp,
            semantic_audit=self.audit,
            materializations=(),
        )


class _FakeDispatcher:
    name = "fake-codex"

    def __init__(
        self,
        *,
        dp_session: str = DP_SESSION,
        audit_session: str = DIRECTOR_SESSION,
        fail_phase: str | None = None,
        self_report_worker: bool = False,
    ) -> None:
        self.dp_session = dp_session
        self.audit_session = audit_session
        self.fail_phase = fail_phase
        self.self_report_worker = self_report_worker
        self.calls: list[orchestrator.DispatchRequest] = []

    def dispatch(self, request: orchestrator.DispatchRequest) -> orchestrator.DispatchResult:
        self.calls.append(request)
        if request.phase == self.fail_phase:
            raise orchestrator.VisualPipelineOrchestrationError(
                f"simulated crash in {request.phase}"
            )
        if request.phase == "director":
            session_id = DIRECTOR_SESSION
            proposal = {"events": []}
        elif request.phase == "dp":
            session_id = self.dp_session
            proposal = {"implementations": []}
        else:
            session_id = self.audit_session
            proposal = {"findings": []}
        proposal.update(
            {
                "contract": request.proposal_contract,
                "episode_id": "episode",
                "cut_id": "value-L01",
                "revision_id": REVISION_ID,
            }
        )
        if self.self_report_worker:
            proposal["worker_execution"] = {
                "worker_id": "agent-forged",
                "execution_id": "agent-forged",
                "role": request.role,
                "session_id": session_id,
            }
        request.proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
        return orchestrator.DispatchResult(
            session_id=session_id,
            returncode=0,
            stdout=(json.dumps({"type": "thread.started", "thread_id": session_id}) + "\n"),
            stderr="",
        )


@dataclass(frozen=True)
class _CapturedArtifact:
    payload: dict[str, object]

    @property
    def document(self) -> dict[str, object]:
        document = self.payload["document"]
        assert isinstance(document, dict)
        return document

    def identity(self) -> dict[str, object]:
        identity = self.payload["identity"]
        assert isinstance(identity, dict)
        return dict(identity)


class _RealCoreDispatcher:
    """Injected creative workers that produce the real core proposal schemas."""

    name = "integration-codex"

    def __init__(self, episode_root: Path) -> None:
        self.episode_root = episode_root
        self.calls: list[orchestrator.DispatchRequest] = []
        self.phase_inputs: list[dict[str, object]] = []

    def dispatch(self, request: orchestrator.DispatchRequest) -> orchestrator.DispatchResult:
        self.calls.append(request)
        phase_input = json.loads(
            (request.working_directory / f"{request.phase}-input.json").read_text(encoding="utf-8")
        )
        self.phase_inputs.append(phase_input)
        work = _CapturedArtifact(phase_input["work_packet"])
        if request.phase == "director":
            proposal = core_fixtures._director_proposal(work)
            session_id = DIRECTOR_SESSION
        elif request.phase == "dp":
            director = _CapturedArtifact(phase_input["director_plan"])
            proposal = core_fixtures._dp_proposal(self.episode_root, director)
            session_id = DP_SESSION
        else:
            director = _CapturedArtifact(phase_input["director_plan"])
            dp = _CapturedArtifact(phase_input["dp_fulfillment"])
            proposal = core_fixtures._audit_proposal(director, dp)
            session_id = DIRECTOR_SESSION
        request.proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
        return orchestrator.DispatchResult(
            session_id=session_id,
            returncode=0,
            stdout=json.dumps({"type": "thread.started", "thread_id": session_id}) + "\n",
            stderr="",
        )


@pytest.fixture
def episode(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "episode"
    root.mkdir()
    request = root / "highlights" / "revision-jobs" / "request.json"
    request.parent.mkdir(parents=True)
    request.write_text('{"feedback":"replace stock"}\n', encoding="utf-8")
    return root, request


def _run(
    monkeypatch: pytest.MonkeyPatch,
    episode: tuple[Path, Path],
    pipeline: _FakePipeline,
    dispatcher: _FakeDispatcher,
):
    root, request = episode
    monkeypatch.setattr(orchestrator, "visual_pipeline", pipeline)
    return orchestrator.run_visual_pipeline(
        root,
        cut_id="value-L01",
        revision_request=request,
        dispatcher=dispatcher,
        resume=True,
    )


def test_two_new_dispatches_then_same_director_session_resume_are_required(
    monkeypatch: pytest.MonkeyPatch, episode: tuple[Path, Path]
) -> None:
    pipeline = _FakePipeline()
    dispatcher = _FakeDispatcher()

    selection = _run(monkeypatch, episode, pipeline, dispatcher)

    assert selection.semantic_audit is pipeline.audit
    assert [(call.phase, call.resume_session_id) for call in dispatcher.calls] == [
        ("director", None),
        ("dp", None),
        ("semantic_audit", DIRECTOR_SESSION),
    ]
    director, dp, audit = pipeline.accepted_identities
    assert director["session_id"] == audit["session_id"] == DIRECTOR_SESSION
    assert director["worker_id"] == audit["worker_id"]
    assert director["execution_id"] != audit["execution_id"]
    assert dp["session_id"] == DP_SESSION
    assert dp["worker_id"] != director["worker_id"]
    assert all(call.working_directory.is_relative_to(episode[0]) for call in dispatcher.calls)
    assert all(REVISION_ID in call.working_directory.parts for call in dispatcher.calls)


def test_ready_current_is_pure_verify_with_zero_dispatch(
    monkeypatch: pytest.MonkeyPatch, episode: tuple[Path, Path]
) -> None:
    pipeline = _FakePipeline()
    pipeline.current = True
    pipeline.director = _Artifact(
        {"contract": pipeline.DIRECTOR_PLAN_CONTRACT}, "DIRECTOR-PLAN.json"
    )
    pipeline.dp = _Artifact({"contract": pipeline.DP_FULFILLMENT_CONTRACT}, "DP.json")
    pipeline.audit = _Artifact({"contract": pipeline.SEMANTIC_AUDIT_CONTRACT}, "AUDIT.json")
    dispatcher = _FakeDispatcher()
    monkeypatch.setattr(orchestrator, "visual_pipeline", pipeline)

    result = orchestrator.run_visual_pipeline(episode[0], cut_id="value-L01", dispatcher=dispatcher)

    assert result.semantic_audit is pipeline.audit
    assert dispatcher.calls == []
    assert pipeline.init_calls == []


def test_explicit_second_request_runs_new_generation_and_same_request_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, episode: tuple[Path, Path]
) -> None:
    pipeline = _SecondGenerationPipeline()
    dispatcher = _FakeDispatcher()

    result = _run(monkeypatch, episode, pipeline, dispatcher)

    assert result.work_packet.document["revision_id"] == SECOND_REVISION_ID
    assert pipeline.current_revision_id == SECOND_REVISION_ID
    assert [call.phase for call in dispatcher.calls] == [
        "director",
        "dp",
        "semantic_audit",
    ]

    retry = _FakeDispatcher()
    same = _run(monkeypatch, episode, pipeline, retry)
    assert same.work_packet.document["revision_id"] == SECOND_REVISION_ID
    assert retry.calls == []


def test_failed_second_generation_preserves_current_then_resumes_only_pending(
    monkeypatch: pytest.MonkeyPatch, episode: tuple[Path, Path]
) -> None:
    pipeline = _SecondGenerationPipeline()
    first = _FakeDispatcher(fail_phase="dp")
    with pytest.raises(orchestrator.VisualPipelineOrchestrationError, match="simulated crash"):
        _run(monkeypatch, episode, pipeline, first)

    assert pipeline.current_revision_id == REVISION_ID
    assert pipeline.verify_visual_pipeline(episode[0], cut_id="value-L01") is pipeline.old_selection

    resumed = _FakeDispatcher()
    result = _run(monkeypatch, episode, pipeline, resumed)
    assert result.work_packet.document["revision_id"] == SECOND_REVISION_ID
    assert [call.phase for call in resumed.calls] == ["dp", "semantic_audit"]


@pytest.mark.parametrize(
    ("dispatcher", "message"),
    [
        (_FakeDispatcher(dp_session=DIRECTOR_SESSION), "DP session"),
        (_FakeDispatcher(audit_session=WRONG_SESSION), "Director session"),
        (_FakeDispatcher(self_report_worker=True), "worker identity"),
    ],
)
def test_untrusted_or_same_session_worker_claims_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    episode: tuple[Path, Path],
    dispatcher: _FakeDispatcher,
    message: str,
) -> None:
    with pytest.raises(orchestrator.VisualPipelineOrchestrationError, match=message):
        _run(monkeypatch, episode, _FakePipeline(), dispatcher)


def test_partial_run_resumes_from_canonical_state_without_redispatching_director(
    monkeypatch: pytest.MonkeyPatch, episode: tuple[Path, Path]
) -> None:
    pipeline = _FakePipeline()
    first = _FakeDispatcher(fail_phase="dp")
    with pytest.raises(orchestrator.VisualPipelineOrchestrationError, match="simulated crash"):
        _run(monkeypatch, episode, pipeline, first)
    assert pipeline.director is not None
    assert pipeline.dp is None

    second = _FakeDispatcher()
    result = _run(monkeypatch, episode, pipeline, second)

    assert result.semantic_audit is pipeline.audit
    assert [call.phase for call in second.calls] == ["dp", "semantic_audit"]
    assert second.calls[-1].resume_session_id == DIRECTOR_SESSION


def test_tampered_execution_receipt_or_proposal_is_not_resumed(
    monkeypatch: pytest.MonkeyPatch, episode: tuple[Path, Path]
) -> None:
    pipeline = _FakePipeline()
    with pytest.raises(orchestrator.VisualPipelineOrchestrationError):
        _run(monkeypatch, episode, pipeline, _FakeDispatcher(fail_phase="dp"))
    job_root = episode[0] / "highlights" / "visual-pipeline" / "value-L01" / "jobs" / REVISION_ID
    receipt = job_root / "receipts" / "director.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["worker_identity"]["session_id"] = WRONG_SESSION
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(orchestrator.VisualPipelineOrchestrationError, match="receipt"):
        _run(monkeypatch, episode, pipeline, _FakeDispatcher())


def test_core_supplied_revision_id_and_revision_request_must_stay_inside_episode(
    monkeypatch: pytest.MonkeyPatch, episode: tuple[Path, Path], tmp_path: Path
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(orchestrator, "visual_pipeline", _FakePipeline("../../escape"))
    with pytest.raises(orchestrator.VisualPipelineOrchestrationError, match="revision_id"):
        orchestrator.run_visual_pipeline(
            episode[0],
            cut_id="value-L01",
            revision_request=episode[1],
            dispatcher=_FakeDispatcher(),
        )
    with pytest.raises(orchestrator.VisualPipelineOrchestrationError, match="episode-local"):
        orchestrator.run_visual_pipeline(
            episode[0],
            cut_id="value-L01",
            revision_request=outside,
            dispatcher=_FakeDispatcher(),
        )


def test_codex_dispatcher_uses_jsonl_workspace_sandbox_and_exact_resume_session(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], str, Path]] = []

    def runner(argv, *, input, cwd, **_kwargs):
        calls.append((list(argv), input, Path(cwd)))
        session = DIRECTOR_SESSION
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"type": "thread.started", "thread_id": session}) + "\n",
            stderr="",
        )

    dispatcher = orchestrator.CodexExecDispatcher(executable="codex", runner=runner)
    work = tmp_path / "job"
    work.mkdir()
    proposal = work / "proposal.json"
    request = orchestrator.DispatchRequest(
        phase="semantic_audit",
        role="director",
        prompt="audit",
        working_directory=work,
        proposal_path=proposal,
        proposal_contract="podcast-highlight-visual-semantic-audit-v1",
        resume_session_id=DIRECTOR_SESSION,
    )

    result = dispatcher.dispatch(request)

    argv, prompt, cwd = calls[0]
    assert argv[:2] == ["codex", "exec"]
    assert "--json" in argv
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert "--approve-for-me" not in argv
    assert argv[-3:] == ["resume", DIRECTOR_SESSION, "-"]
    assert not any("ephemeral" in arg for arg in argv)
    assert not any("add-dir" in arg for arg in argv)
    assert not any("danger" in arg or "yolo" in arg for arg in argv)
    assert prompt == "audit"
    assert cwd == work
    assert result.session_id == DIRECTOR_SESSION


def test_codex_dispatcher_rejects_ambiguous_jsonl_session_identity(tmp_path: Path) -> None:
    def runner(_argv, **_kwargs):
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": DIRECTOR_SESSION}),
                json.dumps({"type": "thread.started", "thread_id": WRONG_SESSION}),
            ]
        )
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    work = tmp_path / "job"
    work.mkdir()
    dispatcher = orchestrator.CodexExecDispatcher(executable="codex", runner=runner)
    with pytest.raises(orchestrator.VisualPipelineOrchestrationError, match="session"):
        dispatcher.dispatch(
            orchestrator.DispatchRequest(
                phase="director",
                role="director",
                prompt="plan",
                working_directory=work,
                proposal_path=work / "proposal.json",
                proposal_contract="podcast-highlight-director-plan-v1",
            )
        )


def test_real_core_base_and_second_generation_reach_current_with_exact_sessions(
    tmp_path: Path,
) -> None:
    episode_root, master = core_fixtures._episode(tmp_path)
    first_dispatcher = _RealCoreDispatcher(episode_root)

    first = orchestrator.run_visual_pipeline(
        episode_root,
        cut_id="value-L01",
        dispatcher=first_dispatcher,
        editorial_master=master,
    )

    first_revision = first.work_packet.document["revision_id"]
    assert len(first.materializations) == 5
    assert [(call.phase, call.resume_session_id) for call in first_dispatcher.calls] == [
        ("director", None),
        ("dp", None),
        ("semantic_audit", DIRECTOR_SESSION),
    ]
    assert first.director_plan.document["worker_execution"]["session_id"] == DIRECTOR_SESSION
    assert first.dp_fulfillment.document["worker_execution"]["session_id"] == DP_SESSION
    assert first.semantic_audit.document["worker_execution"]["session_id"] == DIRECTOR_SESSION

    request = episode_root / "highlights" / "revision-jobs" / "request.json"
    request.parent.mkdir(parents=True)
    request.write_text(
        json.dumps(
            {
                "component_feedback": [],
                "overall_feedback": {
                    "value-L01": "Stock 必須緊貼逐字稿語意，不得使用泛用教育畫面。"
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    second_dispatcher = _RealCoreDispatcher(episode_root)
    second = orchestrator.run_visual_pipeline(
        episode_root,
        cut_id="value-L01",
        revision_request=request,
        dispatcher=second_dispatcher,
        editorial_master=master,
    )

    assert second.work_packet.document["revision_id"] != first_revision
    assert second.work_packet.document["parent_current"]["revision_id"] == first_revision
    assert [(call.phase, call.resume_session_id) for call in second_dispatcher.calls] == [
        ("director", None),
        ("dp", None),
        ("semantic_audit", DIRECTOR_SESSION),
    ]
    director_input = second_dispatcher.phase_inputs[0]["work_packet"]["document"]
    assert director_input["requested_visual_feedback"]["creative_context"] == {
        "policy": "informational_not_acceptance",
        "overall_feedback": "Stock 必須緊貼逐字稿語意，不得使用泛用教育畫面。",
    }
    current = orchestrator.visual_pipeline.verify_visual_pipeline(
        episode_root, cut_id="value-L01", editorial_master=master
    )
    assert current.work_packet.identity() == second.work_packet.identity()


def test_cli_help_does_not_dispatch(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        orchestrator.main(["--help"])
    assert raised.value.code == 0
    help_text = capsys.readouterr().out
    assert "--cut-id" in help_text
    assert "--revision-request" in help_text
    assert "--no-resume" in help_text


def test_direct_script_help_bootstraps_repo_import_path() -> None:
    repo = Path(__file__).parents[2]
    result = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / "podcast_highlight_visual_orchestrator.py"),
            "--help",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--cut-id" in result.stdout
    assert "--revision-request" in result.stdout
