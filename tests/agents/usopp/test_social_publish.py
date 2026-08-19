from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field

import pytest

from agents.usopp.social_publish import (
    AdapterResult,
    YouTubeCommunityHandoff,
    approve_short_targets,
    dispatch_release,
    ensure_short_targets,
)


@pytest.fixture()
def release_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "state.db"))
    import shared.state as state

    if state._conn is not None:
        state._conn.close()
    state._conn = None
    import shared.release_store as store

    importlib.reload(store)
    yield store
    if state._conn is not None:
        state._conn.close()
    state._conn = None


def _short(store, duration: float) -> dict:
    store.register_release(
        "ep",
        f"short-{duration:g}",
        "short",
        f"short-{duration:g}.mp4",
        file_bytes=123,
        duration_sec=duration,
    )
    return store.get_release("ep", f"short-{duration:g}")


def _approve(release: dict) -> list[dict]:
    return approve_short_targets(
        release,
        {
            "title": "Reviewed title",
            "description": "Reviewed description",
            "publish_at": "2026-08-20T12:00:00+08:00",
        },
    )


@dataclass
class FakeAdapter:
    platform: str
    outcomes: list[AdapterResult | Exception]
    calls: list[dict] = field(default_factory=list)

    def publish(self, *, release, target, idempotency_key, checkpoint):
        self.calls.append(
            {
                "cut_id": release["cut_id"],
                "target_status": target["status"],
                "idempotency_key": idempotency_key,
                "checkpoint_json": target.get("checkpoint_json"),
            }
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            checkpoint({"upload_id": "durable-before-failure"})
            raise outcome
        return outcome


def test_59_second_short_ensures_all_three_eligible_targets(release_store):
    release = _short(release_store, 59)

    targets = ensure_short_targets(release)

    assert [target["platform"] for target in targets] == [
        "youtube",
        "instagram_reels",
        "facebook_reels",
    ]
    assert {target["status"] for target in targets} == {"draft"}
    assert all(len(target["idempotency_key"]) == 64 for target in targets)
    assert {target["adapter"] for target in targets} == {"youtube_data", "meta_graph"}


def test_74_second_short_persists_facebook_ineligible_without_blocking_others(
    release_store,
):
    release = _short(release_store, 74)

    targets = ensure_short_targets(release)

    by_platform = {target["platform"]: target for target in targets}
    assert by_platform["youtube"]["status"] == "draft"
    assert by_platform["instagram_reels"]["status"] == "draft"
    assert by_platform["facebook_reels"]["status"] == "ineligible"
    assert "60 seconds" in by_platform["facebook_reels"]["ineligibility_reason"]


def test_partial_failure_retry_calls_only_failed_facebook(release_store):
    release = _short(release_store, 59)
    _approve(release)
    adapters = {
        "youtube": FakeAdapter("youtube", [AdapterResult("published", external_id="yt-1")]),
        "instagram_reels": FakeAdapter(
            "instagram_reels",
            [AdapterResult("published", external_id="ig-1", url="https://ig.test/p/1")],
        ),
        "facebook_reels": FakeAdapter(
            "facebook_reels",
            [RuntimeError("temporary FB failure"), AdapterResult("published", external_id="fb-1")],
        ),
    }

    first = dispatch_release(release, adapters)
    assert {item["platform"]: item["status"] for item in first} == {
        "facebook_reels": "failed",
        "instagram_reels": "published",
        "youtube": "published",
    }
    persisted = {
        target["platform"]: target
        for target in release_store.get_release("ep", "short-59")["targets"]
    }
    assert json.loads(persisted["facebook_reels"]["checkpoint_json"]) == {
        "upload_id": "durable-before-failure"
    }

    second = dispatch_release(release, adapters)

    assert {item["platform"]: item["called"] for item in second} == {
        "facebook_reels": True,
        "instagram_reels": False,
        "youtube": False,
    }
    assert len(adapters["youtube"].calls) == 1
    assert len(adapters["instagram_reels"].calls) == 1
    assert len(adapters["facebook_reels"].calls) == 2
    assert adapters["facebook_reels"].calls[1]["checkpoint_json"] is not None


def test_74_second_dispatch_never_calls_facebook_adapter(release_store):
    release = _short(release_store, 74)
    _approve(release)
    facebook = FakeAdapter(
        "facebook_reels", [AdapterResult("published", external_id="must-not-run")]
    )
    adapters = {
        "youtube": FakeAdapter("youtube", [AdapterResult("published", external_id="yt")]),
        "instagram_reels": FakeAdapter(
            "instagram_reels", [AdapterResult("published", external_id="ig")]
        ),
        "facebook_reels": facebook,
    }

    results = dispatch_release(release, adapters)

    assert {item["platform"]: item["status"] for item in results}["facebook_reels"] == "ineligible"
    assert facebook.calls == []


def test_youtube_community_requires_handoff_receipt_before_published(release_store):
    release = _short(release_store, 59)
    target_id = release_store.ensure_target(release["id"], "youtube_community")
    release_store.update_target(
        target_id,
        status="approved",
        adapter="browser_handoff",
        idempotency_key="a" * 64,
    )
    handoff = YouTubeCommunityHandoff(
        caption="Community caption",
        asset_paths=("01.png", "02.png"),
        target_url="https://www.youtube.com/@channel/community",
    )
    pending = FakeAdapter(
        "youtube_community",
        [AdapterResult("handoff_pending", checkpoint=handoff.checkpoint())],
    )

    result = dispatch_release(release, {"youtube_community": pending}, ["youtube_community"])

    assert result[0]["status"] == "handoff_pending"
    stored = next(
        target
        for target in release_store.get_release("ep", "short-59")["targets"]
        if target["platform"] == "youtube_community"
    )
    assert stored["status"] == "approved"
    assert json.loads(stored["checkpoint_json"])["state"] == "awaiting_receipt"

    invalid = FakeAdapter("youtube_community", [AdapterResult("published")])
    dispatch_release(release, {"youtube_community": invalid}, ["youtube_community"])
    stored = next(
        target
        for target in release_store.get_release("ep", "short-59")["targets"]
        if target["platform"] == "youtube_community"
    )
    assert stored["status"] == "failed"
    assert "handoff receipt" in stored["error"]

    receipt = FakeAdapter(
        "youtube_community",
        [
            AdapterResult(
                "published",
                receipt_id="community-post-1",
                url="https://youtube.test/post/1",
            )
        ],
    )
    dispatch_release(release, {"youtube_community": receipt}, ["youtube_community"])
    stored = next(
        target
        for target in release_store.get_release("ep", "short-59")["targets"]
        if target["platform"] == "youtube_community"
    )
    assert stored["status"] == "published"
    assert stored["video_id"] == "community-post-1"


def test_target_metadata_survives_connection_refresh(release_store):
    release = _short(release_store, 74)
    ensure_short_targets(release)
    import shared.state as state

    state._conn.close()
    state._conn = None

    refreshed = release_store.get_release("ep", "short-74")
    facebook = next(
        target for target in refreshed["targets"] if target["platform"] == "facebook_reels"
    )
    assert facebook["adapter"] == "meta_graph"
    assert facebook["status"] == "ineligible"
    assert facebook["idempotency_key"]
    assert facebook["ineligibility_reason"]
