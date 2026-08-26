"""TDD contract for the subscription-worker visual pipeline orchestrator."""

from __future__ import annotations

import hashlib
import json
import os
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


@pytest.fixture(autouse=True)
def _trusted_hydrator_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def hydrate(episode_root: object, **kwargs: object) -> dict[str, object]:
        calls.append({"episode_root": episode_root, **kwargs})
        proposal_path = Path(str(kwargs["proposal_path"]))
        output_path = Path(str(kwargs["output_path"]))
        document = json.loads(proposal_path.read_text(encoding="utf-8"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return document

    monkeypatch.setattr(orchestrator, "hydrate_dp_proposal", hydrate, raising=False)
    monkeypatch.setattr(
        orchestrator.visual_pipeline,
        "verify_hyperframes_render_receipt",
        lambda *args, **kwargs: {"contract": "trusted-render-test-boundary"},
    )
    monkeypatch.setattr(
        orchestrator.visual_pipeline,
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
        orchestrator.visual_pipeline,
        "_verify_canonical_dp_hydration",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        orchestrator.visual_pipeline,
        "_verify_canonical_asset_execution",
        lambda *args, **kwargs: None,
    )
    return calls


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

    def load_asset_authority_projection(
        self,
        _root,
        *,
        cut_id,
        revision_id,
        attempt,
        editorial_master=None,
    ):
        del editorial_master
        assert cut_id == "value-L01"
        assert revision_id == self.revision_id
        return {
            "identity": {"content_hash": "a" * 64},
            "authority_chain": [{"content_hash": "a" * 64}],
            "attempt": attempt,
            "assets": [],
        }

    def accept_director_plan(
        self,
        _root,
        *,
        cut_id,
        revision_id,
        proposal,
        worker_identity,
        execution_receipt=None,
        editorial_master=None,
    ):
        del cut_id, editorial_master, execution_receipt
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
        execution_receipt=None,
        worker_proposal=None,
        editorial_master=None,
    ):
        del cut_id, editorial_master, execution_receipt, worker_proposal
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
        execution_receipt=None,
        editorial_master=None,
    ):
        del cut_id, editorial_master, execution_receipt
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


class _RealRefinementDispatcher(_RealCoreDispatcher):
    """Real-core mismatch -> replan -> DP2 -> audit2 subscription turns."""

    @staticmethod
    def _audit(phase_input: dict[str, object]) -> dict[str, object]:
        director = _CapturedArtifact(phase_input["director_plan"])
        dp = _CapturedArtifact(phase_input["dp_fulfillment"])
        findings = []
        for row in phase_input["materializations"]:
            findings.append(
                {
                    "materialization_id": row["materialization_id"],
                    "event_id": row["event_id"],
                    "director_intent_sha256": row["director_intent_sha256"],
                    "cue_ids": row["cue_ids"],
                    "t0": row["t0"],
                    "t1": row["t1"],
                    "quote": row["quote"],
                    "source_range": row["source_range"],
                    "evidence_sha256": row["media"]["sha256"],
                    "visual_observation": (
                        "逐一實看可信 render 或素材後，畫面與逐字稿的具體敘述一致。"
                    ),
                    "verdict": "match",
                    "rationale": "逐一檢視實際媒體 bytes、畫面內容與負面限制後，確認語意符合。",
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
        elif request.phase == "semantic_audit":
            proposal = self._audit(phase_input)
            title = next(row for row in proposal["findings"] if row["event_id"] == "title-002")
            title["verdict"] = "mismatch"
            title["rationale"] = (
                "找不到可核對的自有檔案，概念卡不能冒充實際畫面證據，"
                "必須回到 Director。"
            )
            session_id = DIRECTOR_SESSION
        elif request.phase == "refinement_decision-001":
            refinement = _CapturedArtifact(phase_input["semantic_refinement"])
            proposal = core_fixtures._refinement_decision_proposal(
                refinement, action="director_replan"
            )
            session_id = DIRECTOR_SESSION
        elif request.phase == "director_replan-002":
            proposal = core_fixtures._director_proposal(work)
            proposal["events"][1].update(
                {
                    "category": "none",
                    "form": "aroll",
                    "description": "可信來源不存在時保留來賓，不用合成卡冒充證據",
                    "on_screen_text": None,
                    "negative_constraints": ["不可用概念卡冒充自有檔案"],
                    "search_angles": [],
                    "decision": "intentional_aroll",
                    "rationale": "稽核證明素材不可得，因此保留 A-roll，避免製造錯誤視覺證據。",
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
            session_id = DIRECTOR_SESSION
        elif request.phase == "dp-002":
            director = _CapturedArtifact(phase_input["director_plan"])
            prior_dp = _CapturedArtifact(phase_input["dp_fulfillment"])
            proposal = {
                "contract": "podcast-highlight-dp-fulfillment-v1",
                "episode_id": director.document["episode_id"],
                "cut_id": director.document["cut_id"],
                "director_plan": director.identity(),
                "implementations": [
                    row
                    for row in prior_dp.document["implementations"]
                    if row["event_id"] != "title-002"
                ],
            }
            session_id = WRONG_SESSION
        else:
            proposal = self._audit(phase_input)
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


def test_dp_prompt_forbids_worker_media_and_requires_trusted_authority(
    episode: tuple[Path, Path],
) -> None:
    request = orchestrator.DispatchRequest(
        phase="dp-002",
        role="dp",
        prompt="",
        working_directory=episode[0],
        proposal_path=episode[0] / "dp-proposal.json",
        proposal_contract="podcast-highlight-dp-fulfillment-v1",
    )
    prompt = orchestrator._phase_prompt(request, episode[1])

    assert "generated/downloaded candidate" not in prompt
    assert "Never download, generate, or write media" in prompt
    assert "authority_asset_id" in prompt
    assert "mode=stock is mandatory" in prompt
    assert "Never relabel stock as provided_asset" in prompt
    assert "do not force an unrelated asset" in prompt
    assert "exactly one implementation for every Director event" in prompt
    assert "implementations=[] is forbidden" in prompt
    assert "planned_stock_video_count is an editorial target" in prompt
    assert "Do not inspect or copy prior worker/trusted proposal outputs" in prompt
    assert "Preserve every non-null Director on_screen_text exactly" in prompt
    assert "Director on_screen_text is null" in prompt
    assert "Stock/provided_asset must preserve null" in prompt
    assert "render_params.title" in prompt
    assert "at most 16 characters" in prompt
    assert "quote_card.quote" in prompt
    assert "must each be a single non-empty line" in prompt
    assert "instead of removing line breaks or truncating exact required text" in prompt
    assert "show_sec is 0.6-8.0 seconds" in prompt
    assert "Obey quantitative Stock requirements" in prompt
    assert "Never use modern-child footage" in prompt
    assert "HyperFrames candidates must be exact spec-only" in prompt


def test_dp_hydration_destination_is_content_addressed_by_raw_proposal(
    episode: tuple[Path, Path],
) -> None:
    root, _request = episode
    job_root = root / "highlights" / "visual-pipeline" / "value-L01" / "jobs" / REVISION_ID
    first = job_root / "workers" / "dp-session" / "first.json"
    second = job_root / "workers" / "dp-session" / "second.json"
    first.parent.mkdir(parents=True)
    first.write_text('{"contract":"proposal","implementations":[]}\n', encoding="utf-8")
    second.write_text(
        '{"contract":"proposal","implementations":[{"event_id":"event-1"}]}\n',
        encoding="utf-8",
    )

    def hydrate(_root: object, **kwargs: object) -> dict[str, object]:
        source = Path(str(kwargs["proposal_path"]))
        destination = Path(str(kwargs["output_path"]))
        document = json.loads(source.read_text(encoding="utf-8"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(document), encoding="utf-8")
        return document

    first_output = orchestrator._hydrate_dp_phase_proposal(
        root,
        job_root,
        cut_id="value-L01",
        revision_id=REVISION_ID,
        phase="dp",
        attempt=1,
        raw_proposal_path=first,
        editorial_master=None,
        hydrator=hydrate,
        runtime_root=None,
    )
    second_output = orchestrator._hydrate_dp_phase_proposal(
        root,
        job_root,
        cut_id="value-L01",
        revision_id=REVISION_ID,
        phase="dp",
        attempt=1,
        raw_proposal_path=second,
        editorial_master=None,
        hydrator=hydrate,
        runtime_root=None,
    )

    assert first_output != second_output
    assert first_output.name == second_output.name == "proposal.json"
    assert first_output.parent.name == hashlib.sha256(first.read_bytes()).hexdigest()
    assert second_output.parent.name == hashlib.sha256(second.read_bytes()).hexdigest()


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
    monkeypatch: pytest.MonkeyPatch,
    episode: tuple[Path, Path],
    _trusted_hydrator_boundary: list[dict[str, object]],
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
    dp_call = next(call for call in dispatcher.calls if call.phase == "dp")
    dp_input = json.loads(
        (dp_call.working_directory / "dp-input.json").read_text(encoding="utf-8")
    )
    assert dp_input["asset_authority"]["identity"]["content_hash"] == "a" * 64
    assert len(_trusted_hydrator_boundary) == 1
    assert _trusted_hydrator_boundary[0]["attempt"] == 1
    assert "asset_authority" not in _trusted_hydrator_boundary[0]


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


def test_default_codex_executable_prefers_newest_codex_app_binary_over_npm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_appdata = tmp_path / "LocalAppData"
    appdata = tmp_path / "AppData"
    older = local_appdata / "OpenAI" / "Codex" / "bin" / "z-older" / "codex.exe"
    newest = local_appdata / "OpenAI" / "Codex" / "bin" / "a-newest" / "codex.exe"
    npm = appdata / "npm" / "codex.cmd"
    for executable in (older, newest, npm):
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(b"test executable")
    os.utime(older, (100, 100))
    os.utime(newest, (200, 200))
    monkeypatch.setattr(
        orchestrator,
        "os",
        SimpleNamespace(
            name="nt",
            environ={"LOCALAPPDATA": str(local_appdata), "APPDATA": str(appdata)},
        ),
    )

    assert Path(orchestrator._default_codex_executable()) == newest


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
    first_work = core_fixtures.init_visual_work_packet(
        episode_root, cut_id="value-L01", editorial_master=master
    )
    core_fixtures._publish_fixture_asset_authority(
        episode_root, first_work.identity()
    )
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
    second_work = core_fixtures.init_visual_work_packet(
        episode_root,
        cut_id="value-L01",
        revision_request=request,
        editorial_master=master,
    )
    core_fixtures._publish_fixture_asset_authority(
        episode_root, second_work.identity()
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


def test_real_core_mismatch_replan_dp2_and_reaudit_reach_one_current(
    tmp_path: Path,
) -> None:
    episode_root, master = core_fixtures._episode(tmp_path)
    seeded_work = orchestrator.visual_pipeline.init_visual_work_packet(
        episode_root,
        cut_id="value-L01",
        editorial_master=master,
    )
    seeded_director_document = core_fixtures._director_proposal(seeded_work)
    seeded_director = SimpleNamespace(
        document=seeded_director_document,
        identity=lambda: {"content_hash": "seed-only-not-canonical"},
    )
    core_fixtures._dp_proposal(episode_root, seeded_director)
    dispatcher = _RealRefinementDispatcher(episode_root)

    result = orchestrator.run_visual_pipeline(
        episode_root,
        cut_id="value-L01",
        dispatcher=dispatcher,
        editorial_master=master,
    )

    assert [(call.phase, call.resume_session_id) for call in dispatcher.calls] == [
        ("director", None),
        ("dp", None),
        ("semantic_audit", DIRECTOR_SESSION),
        ("refinement_decision-001", DIRECTOR_SESSION),
        ("director_replan-002", DIRECTOR_SESSION),
        ("dp-002", None),
        ("semantic_audit-002", DIRECTOR_SESSION),
    ]
    assert result.director_plan.document["events"][1]["decision"] == "intentional_aroll"
    assert result.dp_fulfillment.document["worker_execution"]["session_id"] == WRONG_SESSION
    assert result.semantic_audit.document["worker_execution"]["session_id"] == DIRECTOR_SESSION
    status = orchestrator.visual_pipeline.visual_pipeline_status(
        episode_root, cut_id="value-L01", editorial_master=master
    )
    assert status["status"] == "ready_to_materialize"
    revision_id = result.work_packet.document["revision_id"]
    attempts = (
        episode_root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "revisions"
        / revision_id
        / "attempts"
    )
    assert sorted(path.name for path in attempts.iterdir()) == ["attempt-001", "attempt-002"]


def test_real_core_retry_same_request_reuses_pending_revision_after_pre_session_failure(
    tmp_path: Path,
) -> None:
    episode_root, master = core_fixtures._episode(tmp_path)
    request = (
        episode_root
        / "highlights"
        / "review"
        / "revisions"
        / "request-1"
        / "request.json"
    )
    request.parent.mkdir(parents=True)
    request.write_text(
        json.dumps(
            {
                "component_feedback": [],
                "overall_feedback": {"value-L01": "補足三段具體 Stock 畫面。"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    request_sha256 = hashlib.sha256(request.read_bytes()).hexdigest()

    class FailBeforeThreadStarted:
        name = "integration-codex"

        def __init__(self) -> None:
            self.calls = []

        def dispatch(self, dispatch_request):
            self.calls.append(dispatch_request)
            raise orchestrator.VisualPipelineOrchestrationError(
                "simulated config failure before thread.started"
            )

    first = FailBeforeThreadStarted()
    with pytest.raises(orchestrator.VisualPipelineOrchestrationError, match="thread.started"):
        orchestrator.run_visual_pipeline(
            episode_root,
            cut_id="value-L01",
            revision_request=request,
            dispatcher=first,
            editorial_master=master,
        )
    pending = orchestrator.visual_pipeline.visual_pipeline_status(
        episode_root, cut_id="value-L01", editorial_master=master
    )
    pending_revision = pending["pending_revision_id"]
    revision_root = episode_root / "highlights" / "visual-pipeline" / "value-L01" / "revisions"
    job_root = episode_root / "highlights" / "visual-pipeline" / "value-L01" / "jobs"
    assert pending["status"] == "awaiting_director"
    assert [path.name for path in revision_root.iterdir() if path.is_dir()] == [pending_revision]
    assert [path.name for path in job_root.iterdir() if path.is_dir()] == [pending_revision]
    assert [(call.phase, call.resume_session_id) for call in first.calls] == [("director", None)]

    pending_work = core_fixtures.load_visual_work_packet(
        episode_root,
        cut_id="value-L01",
        revision_id=str(pending_revision),
        editorial_master=master,
    )
    core_fixtures._publish_fixture_asset_authority(
        episode_root, pending_work.identity()
    )
    resumed_dispatcher = _RealCoreDispatcher(episode_root)
    resumed = orchestrator.run_visual_pipeline(
        episode_root,
        cut_id="value-L01",
        revision_request=request,
        dispatcher=resumed_dispatcher,
        editorial_master=master,
    )

    assert resumed.work_packet.document["revision_id"] == pending_revision
    assert [path.name for path in revision_root.iterdir() if path.is_dir()] == [pending_revision]
    assert [path.name for path in job_root.iterdir() if path.is_dir()] == [pending_revision]
    assert [(call.phase, call.resume_session_id) for call in resumed_dispatcher.calls] == [
        ("director", None),
        ("dp", None),
        ("semantic_audit", DIRECTOR_SESSION),
    ]
    assert resumed_dispatcher.calls[-1].resume_session_id == DIRECTOR_SESSION
    assert hashlib.sha256(request.read_bytes()).hexdigest() == request_sha256
    receipt_root = job_root / pending_revision / "receipts"
    assert [path.name for path in receipt_root.glob("director.json")] == ["director.json"]
    assert resumed.director_plan.document["worker_execution"]["session_id"] == DIRECTOR_SESSION
    assert resumed.semantic_audit.document["worker_execution"]["session_id"] == DIRECTOR_SESSION
    assert (
        resumed.semantic_audit.document["worker_execution"]["execution_id"]
        != resumed.director_plan.document["worker_execution"]["execution_id"]
    )


def test_real_core_pending_revision_rejects_a_different_attempt_request(tmp_path: Path) -> None:
    episode_root, master = core_fixtures._episode(tmp_path)
    first_request = (
        episode_root / "highlights" / "review" / "revisions" / "request-1" / "request.json"
    )
    first_request.parent.mkdir(parents=True)
    payload = {
        "component_feedback": [],
        "overall_feedback": {"value-L01": "補足三段具體 Stock 畫面。"},
    }
    first_request.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    class FailBeforeThreadStarted:
        name = "integration-codex"

        def dispatch(self, _dispatch_request):
            raise orchestrator.VisualPipelineOrchestrationError(
                "simulated config failure before thread.started"
            )

    with pytest.raises(orchestrator.VisualPipelineOrchestrationError, match="thread.started"):
        orchestrator.run_visual_pipeline(
            episode_root,
            cut_id="value-L01",
            revision_request=first_request,
            dispatcher=FailBeforeThreadStarted(),
            editorial_master=master,
        )
    pending_before = orchestrator.visual_pipeline.visual_pipeline_status(
        episode_root, cut_id="value-L01", editorial_master=master
    )
    different_request = (
        episode_root / "highlights" / "review" / "revisions" / "request-2" / "request.json"
    )
    different_request.parent.mkdir(parents=True)
    different_request.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    second_dispatcher = _RealCoreDispatcher(episode_root)

    with pytest.raises(
        orchestrator.visual_pipeline.HighlightVisualArtifactConflictError,
        match="another visual revision is still pending",
    ):
        orchestrator.run_visual_pipeline(
            episode_root,
            cut_id="value-L01",
            revision_request=different_request,
            dispatcher=second_dispatcher,
            editorial_master=master,
        )

    pending_after = orchestrator.visual_pipeline.visual_pipeline_status(
        episode_root, cut_id="value-L01", editorial_master=master
    )
    assert pending_after["status"] == "awaiting_director"
    assert pending_after["pending_revision_id"] == pending_before["pending_revision_id"]
    assert second_dispatcher.calls == []


def test_nonzero_after_thread_started_is_preserved_then_fresh_retry_completes(
    tmp_path: Path,
) -> None:
    episode_root, master = core_fixtures._episode(tmp_path)
    request = episode_root / "highlights" / "review" / "revisions" / "request-1" / "request.json"
    request.parent.mkdir(parents=True)
    request.write_text(
        json.dumps(
            {
                "component_feedback": [],
                "overall_feedback": {"value-L01": "補足三段具體 Stock 畫面。"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    delegate = _RealCoreDispatcher(episode_root)

    class NonzeroAfterThreadStarted:
        name = "integration-codex"

        def dispatch(self, dispatch_request):
            result = delegate.dispatch(dispatch_request)
            return orchestrator.DispatchResult(
                session_id=result.session_id,
                returncode=1,
                stdout=result.stdout,
                stderr="simulated service error after thread.started",
            )

    with pytest.raises(orchestrator.VisualPipelineOrchestrationError, match="worker failed"):
        orchestrator.run_visual_pipeline(
            episode_root,
            cut_id="value-L01",
            revision_request=request,
            dispatcher=NonzeroAfterThreadStarted(),
            editorial_master=master,
        )
    status = orchestrator.visual_pipeline.visual_pipeline_status(
        episode_root, cut_id="value-L01", editorial_master=master
    )
    revision_id = status["pending_revision_id"]
    job_root = (
        episode_root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "jobs"
        / revision_id
    )
    assert not (job_root / "receipts" / "director.json").exists()

    pending_work = core_fixtures.load_visual_work_packet(
        episode_root,
        cut_id="value-L01",
        revision_id=str(revision_id),
        editorial_master=master,
    )
    core_fixtures._publish_fixture_asset_authority(episode_root, pending_work.identity())
    retry_dispatcher = _RealCoreDispatcher(episode_root)
    result = orchestrator.run_visual_pipeline(
        episode_root,
        cut_id="value-L01",
        revision_request=request,
        dispatcher=retry_dispatcher,
        editorial_master=master,
    )

    attempt_root = job_root / "receipts" / "director.attempts"
    first_failure = attempt_root / "attempt-001" / "FAILURE.json"
    first_evidence = attempt_root / "attempt-001" / "evidence" / "proposal.json"
    assert first_failure.is_file()
    assert first_evidence.is_file()
    assert (attempt_root / "attempt-002" / "PREPARE.json").is_file()
    assert (job_root / "receipts" / "director.json").is_file()
    assert result.semantic_audit.document["worker_execution"]["session_id"] == DIRECTOR_SESSION


def test_failed_dp_hydration_retires_execution_before_fresh_retry(
    monkeypatch: pytest.MonkeyPatch,
    episode: tuple[Path, Path],
) -> None:
    pipeline = _FakePipeline()
    root, request = episode
    hydration_calls = 0

    def flaky_hydrator(_episode_root, **kwargs):
        nonlocal hydration_calls
        hydration_calls += 1
        if hydration_calls == 1:
            raise orchestrator.TrustedRenderError(
                "DP provided candidate does not reference provided authority"
            )
        proposal_path = Path(str(kwargs["proposal_path"]))
        output_path = Path(str(kwargs["output_path"]))
        document = json.loads(proposal_path.read_text(encoding="utf-8"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(document), encoding="utf-8")
        return document

    monkeypatch.setattr(orchestrator, "visual_pipeline", pipeline)
    first = _FakeDispatcher()
    with pytest.raises(
        orchestrator.VisualPipelineOrchestrationError,
        match="provided candidate does not reference provided authority",
    ):
        orchestrator.run_visual_pipeline(
            root,
            cut_id="value-L01",
            revision_request=request,
            dispatcher=first,
            proposal_hydrator=flaky_hydrator,
        )

    job_root = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "jobs"
        / REVISION_ID
    )
    first_attempt = job_root / "receipts" / "dp.attempts" / "attempt-001"
    assert [call.phase for call in first.calls] == ["director", "dp"]
    assert not (job_root / "receipts" / "dp.json").exists()
    assert (first_attempt / "FAILURE.json").is_file()
    assert (first_attempt / "evidence" / "proposal.json").is_file()
    assert (first_attempt / "evidence" / "execution-receipt.json").is_file()

    second = _FakeDispatcher()
    result = orchestrator.run_visual_pipeline(
        root,
        cut_id="value-L01",
        revision_request=request,
        dispatcher=second,
        proposal_hydrator=flaky_hydrator,
    )

    assert result.semantic_audit is pipeline.audit
    assert hydration_calls == 2
    assert [call.phase for call in second.calls] == ["dp", "semantic_audit"]
    assert (job_root / "receipts" / "dp.attempts" / "attempt-002" / "PREPARE.json").is_file()
    dp_receipt = json.loads((job_root / "receipts" / "dp.json").read_text(encoding="utf-8"))
    assert dp_receipt["worker_identity"]["session_id"] == DP_SESSION
    assert dp_receipt["worker_identity"]["session_id"] != DIRECTOR_SESSION


def test_failed_dp_accept_retires_execution_before_fresh_retry(
    monkeypatch: pytest.MonkeyPatch,
    episode: tuple[Path, Path],
) -> None:
    pipeline = _FakePipeline()
    root, request = episode
    accept_calls = 0
    real_accept = pipeline.accept_dp_fulfillment

    def flaky_accept(*args, **kwargs):
        nonlocal accept_calls
        accept_calls += 1
        if accept_calls == 1:
            raise orchestrator.HighlightVisualContractError(
                "DP must implement every add_visual Director event exactly once"
            )
        return real_accept(*args, **kwargs)

    pipeline.accept_dp_fulfillment = flaky_accept  # type: ignore[method-assign]
    monkeypatch.setattr(orchestrator, "visual_pipeline", pipeline)
    first = _FakeDispatcher()
    with pytest.raises(
        orchestrator.HighlightVisualContractError,
        match="implement every add_visual",
    ):
        orchestrator.run_visual_pipeline(
            root,
            cut_id="value-L01",
            revision_request=request,
            dispatcher=first,
        )

    job_root = (
        root
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "jobs"
        / REVISION_ID
    )
    first_attempt = job_root / "receipts" / "dp.attempts" / "attempt-001"
    assert not (job_root / "receipts" / "dp.json").exists()
    assert (first_attempt / "FAILURE.json").is_file()
    assert (first_attempt / "evidence" / "proposal.json").is_file()
    assert (first_attempt / "evidence" / "execution-receipt.json").is_file()

    second = _FakeDispatcher()
    result = orchestrator.run_visual_pipeline(
        root,
        cut_id="value-L01",
        revision_request=request,
        dispatcher=second,
    )

    assert result.semantic_audit is pipeline.audit
    assert accept_calls == 2
    assert [call.phase for call in second.calls] == ["dp", "semantic_audit"]
    assert (job_root / "receipts" / "dp.attempts" / "attempt-002" / "PREPARE.json").is_file()


def test_host_crash_after_proposal_is_archived_before_fresh_dispatch_retry(
    monkeypatch: pytest.MonkeyPatch,
    episode: tuple[Path, Path],
) -> None:
    pipeline = _FakePipeline()

    class HostCrashAfterProposal(_FakeDispatcher):
        def dispatch(self, request: orchestrator.DispatchRequest):
            self.calls.append(request)
            request.proposal_path.write_text(
                json.dumps(
                    {
                        "contract": request.proposal_contract,
                        "episode_id": "episode",
                        "cut_id": "value-L01",
                        "revision_id": REVISION_ID,
                        "events": [],
                    }
                ),
                encoding="utf-8",
            )
            raise KeyboardInterrupt("simulated host crash")

    first = HostCrashAfterProposal()
    with pytest.raises(KeyboardInterrupt, match="host crash"):
        _run(monkeypatch, episode, pipeline, first)

    monkeypatch.setattr(orchestrator, "_pid_is_active", lambda _pid: False)
    retry = _FakeDispatcher()
    result = _run(monkeypatch, episode, pipeline, retry)

    attempts = (
        episode[0]
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "jobs"
        / REVISION_ID
        / "receipts"
        / "director.attempts"
    )
    assert result.semantic_audit is pipeline.audit
    assert (attempts / "attempt-001" / "evidence" / "proposal.json").is_file()
    assert (attempts / "attempt-001" / "FAILURE.json").is_file()
    assert (attempts / "attempt-002" / "PREPARE.json").is_file()
    assert [call.phase for call in retry.calls] == ["director", "dp", "semantic_audit"]


def test_active_unresolved_dispatch_attempt_blocks_without_touching_orphan(
    monkeypatch: pytest.MonkeyPatch,
    episode: tuple[Path, Path],
) -> None:
    pipeline = _FakePipeline()

    class HostCrashAfterProposal(_FakeDispatcher):
        def dispatch(self, request: orchestrator.DispatchRequest):
            self.calls.append(request)
            request.proposal_path.write_text(
                json.dumps(
                    {
                        "contract": request.proposal_contract,
                        "episode_id": "episode",
                        "cut_id": "value-L01",
                        "revision_id": REVISION_ID,
                        "events": [],
                    }
                ),
                encoding="utf-8",
            )
            raise KeyboardInterrupt("simulated host crash")

    with pytest.raises(KeyboardInterrupt):
        _run(monkeypatch, episode, pipeline, HostCrashAfterProposal())
    orphan = (
        episode[0]
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "jobs"
        / REVISION_ID
        / "workers"
        / "director-session"
        / "director-proposal.json"
    )
    before = orphan.read_bytes()
    monkeypatch.setattr(orchestrator, "_pid_is_active", lambda _pid: True)
    retry = _FakeDispatcher()
    with pytest.raises(orchestrator.VisualPipelineOrchestrationError, match="active unresolved"):
        _run(monkeypatch, episode, pipeline, retry)
    assert orphan.read_bytes() == before
    assert retry.calls == []


def test_legacy_orphan_without_prepare_is_preserved_then_replaced_by_fresh_execution(
    monkeypatch: pytest.MonkeyPatch,
    episode: tuple[Path, Path],
) -> None:
    pipeline = _FakePipeline()
    orphan = (
        episode[0]
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "jobs"
        / REVISION_ID
        / "workers"
        / "director-session"
        / "director-proposal.json"
    )
    orphan.parent.mkdir(parents=True, exist_ok=True)
    legacy_bytes = b'{"legacy":"untrusted-orphan"}'
    orphan.write_bytes(legacy_bytes)

    dispatcher = _FakeDispatcher()
    result = _run(monkeypatch, episode, pipeline, dispatcher)

    attempts = orphan.parents[2] / "receipts" / "director.attempts"
    assert result.semantic_audit is pipeline.audit
    assert (attempts / "attempt-001" / "evidence" / "proposal.json").read_bytes() == legacy_bytes
    assert (attempts / "attempt-001" / "FAILURE.json").is_file()
    assert (attempts / "attempt-002" / "PREPARE.json").is_file()


def test_request_bound_retry_archives_legacy_missing_prepare_chain_before_redispatch(
    monkeypatch: pytest.MonkeyPatch,
    episode: tuple[Path, Path],
) -> None:
    root, _request = episode

    class LegacyPendingPipeline(_FakePipeline):
        def visual_pipeline_status(self, root_path, *, cut_id, editorial_master=None):
            legacy_plan = (
                Path(root_path)
                / "highlights"
                / "visual-pipeline"
                / cut_id
                / "revisions"
                / self.revision_id
                / "DIRECTOR-PLAN.json"
            )
            if legacy_plan.is_file():
                return {
                    "contract": "podcast-highlight-visual-status-v1",
                    "episode_id": "episode",
                    "cut_id": cut_id,
                    "status": "invalid",
                    "pending_revision_id": self.revision_id,
                    "current_revision_id": None,
                    "paths": {},
                    "error": (
                        "trusted execution receipt fields mismatch: expected "
                        "['content_hash', 'contract', 'cut_id', 'episode_id', 'phase', "
                        "'phase_input', 'prepare', 'prompt_sha256', 'proposal', "
                        "'revision_id', 'role', 'stderr', 'stdout', 'worker_identity'], "
                        "got ['content_hash', 'contract', 'cut_id', 'episode_id', "
                        "'phase', 'phase_input', 'prompt_sha256', 'proposal', "
                        "'revision_id', 'role', 'stderr', 'stdout', 'worker_identity']"
                    ),
                }
            return super().visual_pipeline_status(
                root_path, cut_id=cut_id, editorial_master=editorial_master
            )

    pipeline = LegacyPendingPipeline()
    base = root / "highlights" / "visual-pipeline" / "value-L01"
    revision_root = base / "revisions" / REVISION_ID
    job_root = base / "jobs" / REVISION_ID
    receipt_root = job_root / "receipts"
    director_worker = job_root / "workers" / "director-session"
    dp_worker = job_root / "workers" / "dp-session"
    revision_root.mkdir(parents=True)
    receipt_root.mkdir(parents=True)
    director_worker.mkdir(parents=True)
    dp_worker.mkdir(parents=True)
    legacy_plan = revision_root / "DIRECTOR-PLAN.json"
    legacy_dp = revision_root / "DP-FULFILLMENT.json"
    legacy_plan.write_bytes(b"legacy-director-plan")
    legacy_dp.write_bytes(b"legacy-dp-fulfillment")
    authority = revision_root / "attempts" / "attempt-001" / "ASSET-AUTHORITY.json"
    authority.parent.mkdir(parents=True)
    authority.write_bytes(b"request-bound-authority")

    proposal = director_worker / "director-proposal.json"
    phase_input = director_worker / "director-input.json"
    stdout = receipt_root / "director.stdout.jsonl"
    stderr = receipt_root / "director.stderr.txt"
    proposal.write_text('{"legacy":"proposal"}', encoding="utf-8")
    phase_input.write_text('{"legacy":"input"}', encoding="utf-8")
    stdout.write_text('{"type":"thread.started"}\n', encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    legacy_receipt = {
        "contract": orchestrator.EXECUTION_RECEIPT_CONTRACT,
        "episode_id": root.name,
        "cut_id": "value-L01",
        "revision_id": REVISION_ID,
        "phase": "director",
        "role": "director",
        "worker_identity": {
            "worker_id": f"legacy:{DIRECTOR_SESSION}",
            "execution_id": "legacy-execution",
            "role": "director",
            "session_id": DIRECTOR_SESSION,
        },
        "prompt_sha256": "f" * 64,
        "phase_input": orchestrator._identity(root, phase_input),
        "proposal": orchestrator._identity(root, proposal),
        "stdout": orchestrator._identity(root, stdout),
        "stderr": orchestrator._identity(root, stderr),
    }
    legacy_receipt["content_hash"] = orchestrator._content_hash(legacy_receipt)
    (receipt_root / "director.json").write_text(
        json.dumps(legacy_receipt, sort_keys=True), encoding="utf-8"
    )
    (dp_worker / "old-preview.bin").write_bytes(b"legacy-dp-evidence")

    monkeypatch.setattr(orchestrator, "visual_pipeline", pipeline)
    dispatcher = _FakeDispatcher()
    result = orchestrator.run_visual_pipeline(
        root,
        cut_id="value-L01",
        revision_request=episode[1],
        dispatcher=dispatcher,
        resume=True,
    )

    recovery = job_root / "legacy-recovery" / "missing-prepare-director-v1"
    assert result.semantic_audit is pipeline.audit
    assert [call.phase for call in dispatcher.calls] == ["director", "dp", "semantic_audit"]
    assert (recovery / "COMMIT.json").is_file()
    assert (
        recovery
        / "evidence"
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "revisions"
        / REVISION_ID
        / "DIRECTOR-PLAN.json"
    ).read_bytes() == b"legacy-director-plan"
    assert authority.read_bytes() == b"request-bound-authority"


def test_failed_worker_output_verification_uses_attempt_scoped_streams_on_retry(
    monkeypatch: pytest.MonkeyPatch,
    episode: tuple[Path, Path],
) -> None:
    pipeline = _FakePipeline()
    first = _FakeDispatcher(dp_session=DIRECTOR_SESSION)
    with pytest.raises(orchestrator.VisualPipelineOrchestrationError, match="DP session"):
        _run(monkeypatch, episode, pipeline, first)

    retry = _FakeDispatcher()
    result = _run(monkeypatch, episode, pipeline, retry)
    attempts = (
        episode[0]
        / "highlights"
        / "visual-pipeline"
        / "value-L01"
        / "jobs"
        / REVISION_ID
        / "receipts"
        / "dp.attempts"
    )
    assert result.semantic_audit is pipeline.audit
    assert (attempts / "attempt-001" / "evidence" / "stdout.jsonl").is_file()
    assert (attempts / "attempt-001" / "evidence" / "proposal.json").is_file()
    assert (attempts / "attempt-002" / "evidence" / "stdout.jsonl").is_file()
    assert [call.phase for call in retry.calls] == ["dp", "semantic_audit"]


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
