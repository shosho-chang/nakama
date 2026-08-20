from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from agents.usopp.social_publish import AdapterResult
from scripts.publish_due import JOB_NAME, main, run_cycle, scan_due
from shared.heartbeat import get_heartbeat
from shared.release_store import (
    TARGET_CLAIM_STALE_AFTER,
    claim_target,
    ensure_target,
    get_release,
    get_release_campaign_anchor,
    register_release,
    set_release_campaign_anchor,
    update_target,
)


@dataclass
class FakeInstagramAdapter:
    platform: str = "instagram_reels"
    calls: list[str] = field(default_factory=list)

    def publish(self, *, release, target, idempotency_key, checkpoint):
        del target, idempotency_key, checkpoint
        self.calls.append(release["cut_id"])
        return AdapterResult("published", external_id=f"ig-{release['cut_id']}")


def _short(cut_id: str, anchor: datetime, *, status: str = "approved") -> dict:
    release_id = register_release("episode", cut_id, "short", f"{cut_id}.mp4")
    for platform in ("youtube", "instagram_reels", "facebook_reels"):
        target_id = ensure_target(release_id, platform)
        update_target(
            target_id,
            status=status if platform == "instagram_reels" else "uploaded",
            title="title",
            description="description",
        )
    # Campaign Anchor is normally locked after native upload; materialize the
    # already-approved shared intent directly before simulating native outcomes.
    for target in get_release("episode", cut_id)["targets"]:
        update_target(target["id"], status="approved")
    current = get_release_campaign_anchor("episode", cut_id)
    set_release_campaign_anchor(
        "episode",
        cut_id,
        anchor,
        expected_anchor_token=current.expected_token,
    )
    for target in get_release("episode", cut_id)["targets"]:
        update_target(
            target["id"],
            status=status if target["platform"] == "instagram_reels" else "uploaded",
        )
    return get_release("episode", cut_id)


def test_before_anchor_scan_and_execute_make_zero_adapter_calls():
    now = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    _short("S1", now + timedelta(hours=1))
    adapter = FakeInstagramAdapter()

    plan = scan_due(now=now)
    code, payload = run_cycle(
        execute=True,
        now=now,
        adapters={"instagram_reels": adapter},
        record_success=lambda _: None,
        record_failure=lambda *_: None,
    )

    assert plan["candidates"] == []
    assert payload["results"] == []
    assert code == 0
    assert adapter.calls == []


def test_at_anchor_instagram_is_dispatched_exactly_once():
    now = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    _short("S1", now)
    adapter = FakeInstagramAdapter()

    first_code, first = run_cycle(
        execute=True,
        now=now,
        adapters={"instagram_reels": adapter},
        record_success=lambda _: None,
        record_failure=lambda *_: None,
    )
    second_code, second = run_cycle(
        execute=True,
        now=now + timedelta(minutes=1),
        adapters={"instagram_reels": adapter},
        record_success=lambda _: None,
        record_failure=lambda *_: None,
    )

    assert first_code == second_code == 0
    assert first["results"][0]["status"] == "published"
    assert second["results"] == []
    assert adapter.calls == ["S1"]


def test_scan_candidate_rescheduled_to_future_is_not_dispatched(monkeypatch):
    import scripts.publish_due as due

    now = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    _short("S-rescheduled", now)
    adapter = FakeInstagramAdapter()
    real_scan_due = due.scan_due

    def scan_then_reschedule(*, now):
        plan = real_scan_due(now=now)
        future = now + timedelta(hours=1)
        for target in get_release("episode", "S-rescheduled")["targets"]:
            update_target(target["id"], publish_at=future.isoformat())
        return plan

    monkeypatch.setattr(due, "scan_due", scan_then_reschedule)

    code, payload = due.run_cycle(
        execute=True,
        now=now,
        adapters={"instagram_reels": adapter},
        record_success=lambda _: None,
        record_failure=lambda *_: None,
    )

    instagram = next(
        target
        for target in get_release("episode", "S-rescheduled")["targets"]
        if target["platform"] == "instagram_reels"
    )
    assert code == 0
    assert payload["results"][0]["called"] is False
    assert adapter.calls == []
    assert instagram["status"] == "approved"


def test_reschedule_between_due_reread_and_claim_is_not_dispatched(monkeypatch):
    import scripts.publish_due as due

    now = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    _short("S-claim-race", now)
    adapter = FakeInstagramAdapter()
    real_dispatch_release = due.dispatch_release

    def reschedule_then_dispatch(*args, **kwargs):
        future = now + timedelta(hours=1)
        for target in get_release("episode", "S-claim-race")["targets"]:
            update_target(target["id"], publish_at=future.isoformat())
        return real_dispatch_release(*args, **kwargs)

    monkeypatch.setattr(due, "dispatch_release", reschedule_then_dispatch)

    code, payload = due.run_cycle(
        execute=True,
        now=now,
        adapters={"instagram_reels": adapter},
        record_success=lambda _: None,
        record_failure=lambda *_: None,
    )

    assert code == 0
    assert payload["results"][0]["called"] is False
    assert adapter.calls == []
    instagram = next(
        target
        for target in get_release("episode", "S-claim-race")["targets"]
        if target["platform"] == "instagram_reels"
    )
    assert instagram["status"] == "approved"


def test_stale_uploading_is_planned_for_checkpoint_resume_but_fresh_is_not():
    started = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    release = _short("S1", started)
    instagram = next(
        target for target in release["targets"] if target["platform"] == "instagram_reels"
    )
    update_target(instagram["id"], checkpoint_json='{"container_id":"resume-me"}')
    assert claim_target(instagram["id"], now=started) is not None

    fresh = scan_due(now=started + TARGET_CLAIM_STALE_AFTER - timedelta(seconds=1))
    stale = scan_due(now=started + TARGET_CLAIM_STALE_AFTER + timedelta(seconds=1))

    assert fresh["candidates"] == []
    assert stale["candidates"][0]["claim_reason"] == "stale_uploading_resume"


def test_scan_excludes_long_missing_divergent_and_ineligible_deterministically():
    now = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    register_release("episode", "L1", "long", "long.mp4")
    register_release("episode", "S-no-targets", "short", "no-targets.mp4")
    missing_id = register_release("episode", "S-missing", "short", "missing.mp4")
    ensure_target(missing_id, "instagram_reels")
    divergent_id = register_release("episode", "S-divergent", "short", "divergent.mp4")
    first = ensure_target(divergent_id, "instagram_reels")
    second = ensure_target(divergent_id, "youtube")
    update_target(first, status="approved", publish_at=now.isoformat())
    update_target(second, status="approved", publish_at=(now + timedelta(hours=1)).isoformat())
    _short("S-ineligible", now, status="ineligible")

    plan = scan_due(now=now)

    assert plan["candidates"] == []
    assert plan["counts"] == {
        "excluded_divergent_anchor": 1,
        "excluded_instagram_ineligible": 1,
        "excluded_missing_anchor": 1,
        "excluded_non_short": 1,
        "excluded_targets_missing": 1,
    }


def test_default_invocation_is_dry_run_and_writes_no_heartbeat(capsys):
    now = datetime.now(UTC) - timedelta(minutes=1)
    _short("S-dry", now)

    assert main([]) == 0
    assert '"dry_run": true' in capsys.readouterr().out
    assert get_heartbeat(JOB_NAME) is None
    instagram = next(
        target
        for target in get_release("episode", "S-dry")["targets"]
        if target["platform"] == "instagram_reels"
    )
    assert instagram["status"] == "approved"


def test_live_no_work_records_success_and_due_failed_records_failure_without_retry():
    now = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    successes = []
    failures = []
    code, _ = run_cycle(
        execute=True,
        now=now,
        adapters={},
        record_success=successes.append,
        record_failure=lambda *args: failures.append(args),
    )
    _short("S-failed", now, status="failed")
    adapter = FakeInstagramAdapter()
    failed_code, payload = run_cycle(
        execute=True,
        now=now,
        adapters={"instagram_reels": adapter},
        record_success=successes.append,
        record_failure=lambda *args: failures.append(args),
    )

    assert code == 0
    assert successes == [JOB_NAME]
    assert failed_code == 1
    assert payload["failed"][0]["status"] == "failed"
    assert failures[-1][0] == JOB_NAME
    assert adapter.calls == []


@dataclass
class FailingThenSuccessfulAdapter:
    platform: str = "instagram_reels"
    calls: list[str] = field(default_factory=list)

    def publish(self, *, release, target, idempotency_key, checkpoint):
        del target, idempotency_key, checkpoint
        self.calls.append(release["cut_id"])
        if release["cut_id"] == "S-bad":
            raise RuntimeError("synthetic failure")
        return AdapterResult("published", external_id="ig-ok")


def test_one_release_failure_does_not_block_other_due_release():
    now = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    _short("S-bad", now)
    _short("S-good", now)
    adapter = FailingThenSuccessfulAdapter()

    code, payload = run_cycle(
        execute=True,
        now=now,
        adapters={"instagram_reels": adapter},
        record_success=lambda _: None,
        record_failure=lambda *_: None,
    )

    assert code == 1
    assert adapter.calls == ["S-bad", "S-good"]
    assert {item["cut_id"]: item["status"] for item in payload["results"]} == {
        "S-bad": "failed",
        "S-good": "published",
    }


def test_watch_rejects_nonpositive_interval_and_stops_cleanly(monkeypatch):
    with pytest.raises(SystemExit):
        main(["--watch", "--execute", "--poll-seconds", "0"])

    import scripts.publish_due as due

    monkeypatch.setattr(
        due,
        "run_cycle",
        lambda **kwargs: (0, {"dry_run": not kwargs["execute"], "results": []}),
    )
    monkeypatch.setattr(due.time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt))
    assert main(["--watch", "--execute", "--poll-seconds", "1"]) == 0


def test_live_scan_failure_records_secret_free_failure_and_returns_nonzero(monkeypatch):
    import scripts.publish_due as due

    failures = []
    monkeypatch.setattr(
        due,
        "scan_due",
        lambda **_: (_ for _ in ()).throw(RuntimeError("token=secret")),
    )

    code, payload = due.run_cycle(
        execute=True,
        now=datetime(2026, 8, 20, tzinfo=UTC),
        record_success=lambda _: None,
        record_failure=lambda *args: failures.append(args),
    )

    assert code == 1
    assert payload["scan_error"] == "RuntimeError"
    assert failures == [(JOB_NAME, "due scan failed: RuntimeError")]
    assert "secret" not in repr(payload) + repr(failures)
