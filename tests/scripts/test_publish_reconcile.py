from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import publish_reconcile
from shared.release_store import (
    ensure_target,
    get_release,
    register_release,
    update_target,
)

NOW = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)


def _release(
    *,
    episode: str = "episode",
    cut: str = "S01",
    anchor: datetime | None = NOW - timedelta(minutes=1),
    targets: tuple[tuple[str, str, str | None], ...] = (("youtube", "uploaded", "yt-1"),),
):
    release_id = register_release(episode, cut, "short", "unused.mp4")
    for platform, status, video_id in targets:
        target_id = ensure_target(release_id, platform)
        update_target(target_id, status=status, video_id=video_id)
    if anchor is not None:
        # Native Arms lock schedule editing, so fixtures materialise the already
        # accepted shared anchor exactly as the uploader has stored it.
        for target in get_release(episode, cut)["targets"]:
            update_target(target["id"], publish_at=anchor.isoformat())
    return get_release(episode, cut)


def _observation(outcome, evidence, *, certain=True, permalink=None, error=None):
    return SimpleNamespace(
        outcome=outcome,
        evidence_category=evidence,
        certain=certain,
        permalink=permalink,
        error=error,
    )


def test_dry_run_candidate_is_zero_network_heartbeat_and_mutation(monkeypatch):
    before = _release()
    builder = MagicMock(side_effect=AssertionError("dry-run must not initialize observers"))
    success = MagicMock(side_effect=AssertionError("dry-run must not write heartbeat"))
    failure = MagicMock(side_effect=AssertionError("dry-run must not write heartbeat"))
    monkeypatch.setattr(publish_reconcile, "_live_observers", builder)

    code, report = publish_reconcile.run_cycle(
        now=NOW,
        record_success=success,
        record_failure=failure,
    )

    assert code == 0
    assert report["dry_run"] is True
    assert report["candidates"] == [
        {
            "episode": "episode",
            "cut_id": "S01",
            "platform": "youtube",
            "target_id": before["targets"][0]["id"],
            "anchor_at": (NOW - timedelta(minutes=1)).isoformat(),
            "status": "uploaded",
            "evidence_category": "observation_required",
        }
    ]
    assert get_release("episode", "S01") == before
    builder.assert_not_called()
    success.assert_not_called()
    failure.assert_not_called()
    assert "yt-1" not in repr(report)


def test_exact_scope_is_paired_and_missing_release_fails_closed():
    with pytest.raises(ValueError, match="provided together"):
        publish_reconcile.scan_outcomes(now=NOW, episode="episode")
    with pytest.raises(ValueError, match="does not exist"):
        publish_reconcile.scan_outcomes(now=NOW, episode="missing", cut="S01")


def test_scan_excludes_future_non_uploaded_instagram_missing_id_and_divergent_anchor():
    _release(cut="future", anchor=NOW + timedelta(hours=1))
    _release(cut="draft", targets=(("youtube", "draft", "yt-draft"),))
    _release(cut="instagram", targets=(("instagram_reels", "uploaded", "ig-1"),))
    _release(cut="missing-id", targets=(("facebook_reels", "uploaded", None),))
    divergent = _release(
        cut="divergent",
        targets=(("youtube", "uploaded", "yt-a"), ("facebook_reels", "uploaded", "fb-a")),
    )
    update_target(divergent["targets"][0]["id"], publish_at=(NOW - timedelta(days=1)).isoformat())

    report = publish_reconcile.scan_outcomes(now=NOW)

    assert report["candidates"] == []
    assert {item["code"] for item in report["diagnostics"]} >= {
        "missing_video_identity",
        "divergent_campaign_anchor",
    }


def test_execute_never_observes_ineligible_targets():
    _release(cut="future", anchor=NOW + timedelta(hours=1))
    _release(cut="draft", targets=(("youtube", "draft", "yt-draft"),))
    _release(cut="instagram", targets=(("instagram_reels", "uploaded", "ig-1"),))
    observer = MagicMock(side_effect=AssertionError("ineligible target observed"))

    code, report = publish_reconcile.run_cycle(
        execute=True,
        now=NOW,
        observers={"youtube": observer, "facebook_reels": observer},
        record_success=MagicMock(),
        record_failure=MagicMock(),
    )

    assert code == 0
    assert report["results"] == []
    observer.assert_not_called()


def test_duplicate_platform_video_identity_is_diagnostic_and_not_observed():
    _release(cut="S01", targets=(("youtube", "uploaded", "yt-shared"),))
    _release(cut="S02", targets=(("youtube", "uploaded", "yt-shared"),))

    report = publish_reconcile.scan_outcomes(now=NOW)

    assert report["candidates"] == []
    assert [item["code"] for item in report["diagnostics"]].count(
        "duplicate_platform_identity"
    ) == 2
    assert "yt-shared" not in repr(report)


def test_exact_scope_duplicate_identity_checks_other_terminal_release():
    _release(cut="S01", targets=(("youtube", "uploaded", "yt-shared"),))
    _release(cut="S02", targets=(("youtube", "published", "yt-shared"),))

    report = publish_reconcile.scan_outcomes(
        now=NOW,
        episode="episode",
        cut="S01",
    )

    assert report["candidates"] == []
    assert [item["code"] for item in report["diagnostics"]] == ["duplicate_platform_identity"]


@pytest.mark.parametrize(
    ("platform", "observation", "expected_status", "expected_code"),
    [
        ("youtube", _observation("published", "public"), "published", 0),
        ("youtube", _observation("pending", "processing"), "uploaded", 0),
        (
            "youtube",
            _observation("failed", "processing_failed", error="explicit rejection"),
            "failed",
            0,
        ),
        (
            "facebook_reels",
            _observation(
                "published",
                "public",
                permalink="https://facebook.example/reel/1",
            ),
            "published",
            0,
        ),
        ("facebook_reels", _observation("pending", "scheduled"), "uploaded", 0),
        (
            "facebook_reels",
            _observation("pending", "unsafe_permalink", certain=False),
            "uploaded",
            1,
        ),
    ],
)
def test_execute_confirms_only_certain_outcomes(
    platform, observation, expected_status, expected_code
):
    release = _release(targets=((platform, "uploaded", "external-1"),))
    observer = MagicMock(return_value=observation)
    success = MagicMock()
    failure = MagicMock()

    code, report = publish_reconcile.run_cycle(
        execute=True,
        now=NOW,
        observers={platform: observer},
        record_success=success,
        record_failure=failure,
    )

    stored = get_release("episode", "S01")["targets"][0]
    assert code == expected_code
    assert stored["status"] == expected_status
    assert stored["video_id"] == "external-1"
    assert report["results"][0]["evidence_category"] == observation.evidence_category
    observer.assert_called_once_with("external-1")
    if expected_code:
        failure.assert_called_once()
        success.assert_not_called()
    else:
        success.assert_called_once_with(publish_reconcile.JOB_NAME)
        failure.assert_not_called()
    assert "external-1" not in repr(report)
    assert release["targets"][0]["id"] == stored["id"]


def test_transport_uncertainty_does_not_block_sibling_or_mark_failed():
    _release(
        targets=(
            ("youtube", "uploaded", "yt-1"),
            ("facebook_reels", "uploaded", "fb-1"),
        )
    )
    observers = {
        "youtube": MagicMock(side_effect=PermissionError("secret auth detail")),
        "facebook_reels": MagicMock(
            return_value=_observation(
                "published", "public", permalink="https://facebook.example/reel/1"
            )
        ),
    }
    failure = MagicMock()

    code, report = publish_reconcile.run_cycle(
        execute=True,
        now=NOW,
        observers=observers,
        record_success=MagicMock(),
        record_failure=failure,
    )

    targets = {target["platform"]: target for target in get_release("episode", "S01")["targets"]}
    assert code == 1
    assert targets["youtube"]["status"] == "uploaded"
    assert targets["facebook_reels"]["status"] == "published"
    assert {item["evidence_category"] for item in report["results"]} == {
        "observation_error",
        "public",
    }
    assert "secret auth detail" not in repr(report)
    failure.assert_called_once()


def test_terminal_failure_persists_stable_evidence_not_observer_secret():
    _release()
    sentinel = "Bearer secret-token C:/private/media.mp4"
    failure = MagicMock()

    code, report = publish_reconcile.run_cycle(
        execute=True,
        now=NOW,
        observers={
            "youtube": MagicMock(
                return_value=_observation(
                    "failed",
                    "processing_failed",
                    error=sentinel,
                )
            )
        },
        record_success=MagicMock(),
        record_failure=failure,
    )

    target = get_release("episode", "S01")["targets"][0]
    assert code == 0
    assert target["status"] == "failed"
    assert target["error"] == "YouTube outcome confirmed: processing_failed"
    assert sentinel not in repr(report)
    assert sentinel not in repr(failure.call_args_list)


def test_cas_miss_does_not_overwrite_concurrent_target_change():
    release = _release()
    target_id = release["targets"][0]["id"]

    def concurrent_observer(_video_id):
        update_target(target_id, status="failed", error="operator result")
        return _observation("published", "public")

    success = MagicMock()
    failure = MagicMock()
    code, report = publish_reconcile.run_cycle(
        execute=True,
        now=NOW,
        observers={"youtube": concurrent_observer},
        record_success=success,
        record_failure=failure,
    )

    target = get_release("episode", "S01")["targets"][0]
    assert code == 0
    assert target["status"] == "failed"
    assert target["error"] == "operator result"
    assert report["results"][0]["evidence_category"] == "stale_snapshot"
    success.assert_called_once_with(publish_reconcile.JOB_NAME)
    failure.assert_not_called()


def test_cas_rechecks_duplicate_identity_created_after_scan():
    _release()

    def duplicate_then_public(_video_id):
        release_id = register_release("episode", "S02", "short", "unused.mp4")
        duplicate_id = ensure_target(release_id, "youtube")
        update_target(
            duplicate_id,
            status="uploaded",
            video_id="yt-1",
            publish_at=(NOW - timedelta(minutes=1)).isoformat(),
        )
        return _observation("published", "public")

    success = MagicMock()
    code, report = publish_reconcile.run_cycle(
        execute=True,
        now=NOW,
        observers={"youtube": duplicate_then_public},
        record_success=success,
        record_failure=MagicMock(),
    )

    assert code == 0
    assert get_release("episode", "S01")["targets"][0]["status"] == "uploaded"
    assert get_release("episode", "S02")["targets"][0]["status"] == "uploaded"
    assert report["results"][0]["evidence_category"] == "stale_snapshot"
    success.assert_called_once_with(publish_reconcile.JOB_NAME)


def test_execute_no_work_records_success_without_initializing_observers(monkeypatch):
    success = MagicMock()
    builder = MagicMock(side_effect=AssertionError("no candidates, no clients"))
    monkeypatch.setattr(publish_reconcile, "_live_observers", builder)

    code, report = publish_reconcile.run_cycle(
        execute=True,
        now=NOW,
        record_success=success,
        record_failure=MagicMock(),
    )

    assert code == 0
    assert report["results"] == []
    builder.assert_not_called()
    success.assert_called_once_with(publish_reconcile.JOB_NAME)


def test_exact_scope_execute_never_writes_global_heartbeat():
    _release()
    success = MagicMock()
    failure = MagicMock()

    code, report = publish_reconcile.run_cycle(
        execute=True,
        now=NOW,
        episode="episode",
        cut="S01",
        observers={"youtube": lambda _video_id: _observation("published", "public")},
        record_success=success,
        record_failure=failure,
    )

    assert code == 0
    assert report["heartbeat_scope"] == "suppressed_exact_scope"
    success.assert_not_called()
    failure.assert_not_called()


def test_live_observer_setup_system_exit_is_redacted_and_sibling_continues(monkeypatch):
    _release(
        targets=(
            ("youtube", "uploaded", "yt-1"),
            ("facebook_reels", "uploaded", "fb-1"),
        )
    )
    sentinel = "C:/secret/youtube_token.json"
    monkeypatch.setattr(
        publish_reconcile,
        "load_youtube_observer",
        MagicMock(side_effect=SystemExit(sentinel)),
    )
    monkeypatch.setattr(
        publish_reconcile,
        "build_meta_client",
        lambda: SimpleNamespace(
            observe_facebook_reel=lambda _video_id: _observation(
                "published",
                "public",
                permalink="https://facebook.example/reel/1",
            )
        ),
    )
    failure = MagicMock()

    code, report = publish_reconcile.run_cycle(
        execute=True,
        now=NOW,
        record_success=MagicMock(),
        record_failure=failure,
    )

    targets = {target["platform"]: target for target in get_release("episode", "S01")["targets"]}
    assert code == 1
    assert targets["youtube"]["status"] == "uploaded"
    assert targets["facebook_reels"]["status"] == "published"
    assert "youtube_observer_setup_failed" in repr(report)
    assert sentinel not in repr(report)
    assert sentinel not in repr(failure.call_args_list)


def test_cas_error_is_redacted_and_does_not_block_sibling(monkeypatch):
    _release(
        targets=(
            ("youtube", "uploaded", "yt-1"),
            ("facebook_reels", "uploaded", "fb-1"),
        )
    )
    sentinel = "sqlite failure C:/private/state.db"
    real_confirm = publish_reconcile.confirm_target_outcome
    calls = 0

    def flaky_confirm(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(sentinel)
        return real_confirm(*args, **kwargs)

    monkeypatch.setattr(publish_reconcile, "confirm_target_outcome", flaky_confirm)
    observers = {
        "youtube": lambda _video_id: _observation("published", "public"),
        "facebook_reels": lambda _video_id: _observation(
            "published",
            "public",
            permalink="https://facebook.example/reel/1",
        ),
    }
    failure = MagicMock()

    code, report = publish_reconcile.run_cycle(
        execute=True,
        now=NOW,
        observers=observers,
        record_success=MagicMock(),
        record_failure=failure,
    )

    statuses = {target["status"] for target in get_release("episode", "S01")["targets"]}
    assert code == 1
    assert statuses == {"uploaded", "published"}
    assert "cas_error" in repr(report)
    assert sentinel not in repr(report)
    assert sentinel not in repr(failure.call_args_list)


def test_cas_miss_readback_error_is_redacted_and_does_not_block_sibling(monkeypatch):
    _release(
        targets=(
            ("youtube", "uploaded", "yt-1"),
            ("facebook_reels", "uploaded", "fb-1"),
        )
    )
    sentinel = "readback failed C:/private/state.db"
    real_confirm = publish_reconcile.confirm_target_outcome
    real_get_release = publish_reconcile.get_release
    confirm_calls = 0

    def first_cas_misses(*args, **kwargs):
        nonlocal confirm_calls
        confirm_calls += 1
        if confirm_calls == 1:
            return False
        return real_confirm(*args, **kwargs)

    readback_calls = 0

    def one_readback_fails(*args, **kwargs):
        nonlocal readback_calls
        readback_calls += 1
        if readback_calls == 1:
            raise RuntimeError(sentinel)
        return real_get_release(*args, **kwargs)

    def facebook_observer(_video_id):
        monkeypatch.setattr(publish_reconcile, "get_release", one_readback_fails)
        return _observation(
            "published",
            "public",
            permalink="https://facebook.example/reel/1",
        )

    monkeypatch.setattr(publish_reconcile, "confirm_target_outcome", first_cas_misses)
    failure = MagicMock()
    code, report = publish_reconcile.run_cycle(
        execute=True,
        now=NOW,
        observers={
            "facebook_reels": facebook_observer,
            "youtube": lambda _video_id: _observation("published", "public"),
        },
        record_success=MagicMock(),
        record_failure=failure,
    )

    targets = {target["platform"]: target for target in get_release("episode", "S01")["targets"]}
    assert code == 1
    assert targets["facebook_reels"]["status"] == "uploaded"
    assert targets["youtube"]["status"] == "published"
    assert "cas_error" in repr(report)
    assert sentinel not in repr(report)
    assert sentinel not in repr(failure.call_args_list)
