"""Lifecycle contract for the Finished Cut Production v3 review watcher."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.finished_review_watcher import pending_revision_jobs, run_revision_job

_COMMAND_ID = "targeted-revision:" + "a" * 32
_REQUEST_ID = "finished-revision:" + "b" * 64


class _Application:
    def __init__(
        self,
        *,
        states: tuple[str, ...] = ("pending",),
        request_error: Exception | None = None,
        advance_error: Exception | None = None,
        reason_code: str | None = None,
    ) -> None:
        self.states = list(states)
        self.request_error = request_error
        self.advance_error = advance_error
        self.reason_code = reason_code
        self.revision_calls: list[tuple[str, str, str]] = []
        self.advance_calls: list[str] = []

    def request_revision(self, release_id: str, event_id: str, feedback: str) -> str:
        self.revision_calls.append((release_id, event_id, feedback))
        if self.request_error is not None:
            raise self.request_error
        return _COMMAND_ID

    def advance(self, command_id: str) -> object:
        self.advance_calls.append(command_id)
        if self.advance_error is not None:
            raise self.advance_error
        state = self.states.pop(0) if self.states else "pending"
        return SimpleNamespace(state=state, reason_code=self.reason_code)


class _Factory:
    def __init__(self, application: _Application) -> None:
        self.application = application
        self.calls: list[tuple[object, str]] = []

    def __call__(self, paths: object, episode_id: str) -> _Application:
        self.calls.append((paths, episode_id))
        return self.application


def _job(*, status: str = "queued", command_id: str | None = None) -> dict[str, object]:
    return {
        "contract": "finished-cut-production-revision.v3",
        "request_id": _REQUEST_ID,
        "status": status,
        "command_id": command_id,
        "production_state": None if status == "queued" else status,
        "reason_code": None,
        "requested_at": "2026-08-28T01:00:00+00:00",
        "updated_at": None,
        "error": None,
        "episode_id": "20260805 林之晨",
        "source_manifest_sha256": "c" * 64,
        "release_id": "release-L03",
        "cut_id": "value-L03",
        "event_id": "event-hero",
        "feedback": "修改 Hero title：補上完整主詞。",
    }


def _write_feedback(
    root: Path,
    *,
    job: dict[str, object] | None = None,
) -> Path:
    review = root / "20260805 林之晨" / "highlights" / "review"
    review.mkdir(parents=True)
    path = review / "finished_review_feedback.v3.json"
    path.write_text(
        json.dumps(
            {
                "schema": "nakama.finished_cut_review_feedback.v3",
                "episode_id": "20260805 林之晨",
                "revisions": [
                    {
                        "revision": 1,
                        "manifest_sha256": "c" * 64,
                        "revision_jobs": [job or _job()],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _saved_job(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))["revisions"][0]["revision_jobs"][0]


def test_pending_jobs_reads_only_the_exact_v3_feedback_file(tmp_path: Path) -> None:
    path = _write_feedback(tmp_path)
    historical = path.parent / "finished_review_feedback.v1.json"
    historical.write_text(
        json.dumps(
            {
                "schema": "nakama.finished_cut_review_feedback.v1",
                "revisions": [{"revision_job": {"status": "queued"}}],
            }
        ),
        encoding="utf-8",
    )

    jobs = pending_revision_jobs(tmp_path)

    assert len(jobs) == 1
    assert jobs[0]["feedback_path"] == path
    assert jobs[0]["request_id"] == _REQUEST_ID


def test_queued_job_registers_once_then_advances_same_durable_command(
    tmp_path: Path,
) -> None:
    path = _write_feedback(tmp_path)
    application = _Application(states=("pending", "preview_ready"))
    factory = _Factory(application)

    first = pending_revision_jobs(tmp_path)[0]
    assert run_revision_job(first, application_factory=factory) is True
    after_first = _saved_job(path)
    assert after_first["status"] == "pending"
    assert after_first["command_id"] == _COMMAND_ID
    assert application.revision_calls == [
        ("release-L03", "event-hero", "修改 Hero title：補上完整主詞。")
    ]
    assert application.advance_calls == [_COMMAND_ID]

    second = pending_revision_jobs(tmp_path)[0]
    assert run_revision_job(second, application_factory=factory) is True
    assert _saved_job(path)["status"] == "preview_ready"
    assert application.revision_calls == [
        ("release-L03", "event-hero", "修改 Hero title：補上完整主詞。")
    ]
    assert application.advance_calls == [_COMMAND_ID, _COMMAND_ID]


def test_registration_failure_is_durable_needs_review_and_never_retried(
    tmp_path: Path,
) -> None:
    path = _write_feedback(tmp_path)
    application = _Application(request_error=RuntimeError("current Release changed"))
    factory = _Factory(application)

    work = pending_revision_jobs(tmp_path)[0]
    assert run_revision_job(work, application_factory=factory) is False

    saved = _saved_job(path)
    assert saved["status"] == "needs_review"
    assert saved["reason_code"] == "revision_registration_failed"
    assert "current Release changed" in str(saved["error"])
    assert pending_revision_jobs(tmp_path) == []
    assert len(application.revision_calls) == 1
    assert application.advance_calls == []


def test_composition_failure_after_claim_is_fail_closed_without_redispatch(
    tmp_path: Path,
) -> None:
    path = _write_feedback(tmp_path)
    calls = 0

    def broken_factory(_paths: object, _episode_id: str) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("production dependencies unavailable")

    work = pending_revision_jobs(tmp_path)[0]
    assert run_revision_job(work, application_factory=broken_factory) is False

    saved = _saved_job(path)
    assert saved["status"] == "needs_review"
    assert saved["reason_code"] == "production_composition_failed"
    assert pending_revision_jobs(tmp_path) == []
    assert calls == 1


def test_advance_failure_keeps_existing_command_and_stops_automatic_pickup(
    tmp_path: Path,
) -> None:
    path = _write_feedback(tmp_path, job=_job(status="pending", command_id=_COMMAND_ID))
    application = _Application(advance_error=TimeoutError("worker timed out"))

    work = pending_revision_jobs(tmp_path)[0]
    assert run_revision_job(work, application_factory=_Factory(application)) is False

    saved = _saved_job(path)
    assert saved["status"] == "needs_review"
    assert saved["command_id"] == _COMMAND_ID
    assert saved["reason_code"] == "revision_advance_failed"
    assert application.revision_calls == []
    assert application.advance_calls == [_COMMAND_ID]
    assert pending_revision_jobs(tmp_path) == []


def test_pending_with_typed_blocker_stops_in_needs_review(tmp_path: Path) -> None:
    path = _write_feedback(tmp_path, job=_job(status="pending", command_id=_COMMAND_ID))
    application = _Application(reason_code="resolve_binding_not_configured")

    work = pending_revision_jobs(tmp_path)[0]
    assert run_revision_job(work, application_factory=_Factory(application)) is True

    saved = _saved_job(path)
    assert saved["status"] == "needs_review"
    assert saved["production_state"] == "pending"
    assert saved["reason_code"] == "resolve_binding_not_configured"
    assert pending_revision_jobs(tmp_path) == []


def test_existing_registration_claim_prevents_a_second_command_mint(
    tmp_path: Path,
) -> None:
    path = _write_feedback(tmp_path)
    claim_dir = path.parent / "revision-claims-v3"
    claim_dir.mkdir()
    (claim_dir / f"{'b' * 64}.json").write_text(
        '{"contract":"finished-cut-production-revision-claim.v1"}',
        encoding="utf-8",
    )
    application = _Application()

    work = pending_revision_jobs(tmp_path)[0]
    assert run_revision_job(work, application_factory=_Factory(application)) is False

    saved = _saved_job(path)
    assert saved["status"] == "needs_review"
    assert saved["reason_code"] == "revision_registration_indeterminate"
    assert application.revision_calls == []
    assert application.advance_calls == []


def test_invalid_v3_job_fails_closed_instead_of_becoming_work(tmp_path: Path) -> None:
    invalid = _job()
    invalid["release_id"] = "../historical-release"
    _write_feedback(tmp_path, job=invalid)

    with pytest.raises(RuntimeError, match="release_id is invalid"):
        pending_revision_jobs(tmp_path)
