"""release_store 五法測試（tmp DB via DB_PATH env——shared.config 慣例）。"""

import importlib
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """每測試獨立 DB：重設 shared.state 的 module singleton conn。"""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "state.db"))
    import shared.state as state

    state._conn = None
    import shared.release_store as rs

    importlib.reload(rs)
    yield rs
    state._conn = None


def _set_anchor(store, episode: str, cut_id: str, anchor: datetime | None):
    current = store.get_release_campaign_anchor(episode, cut_id)
    return store.set_release_campaign_anchor(
        episode,
        cut_id,
        anchor,
        expected_anchor_token=current.expected_token,
    )


def test_register_release_upsert(store):
    rid1 = store.register_release(
        "20260723 謝伯讓",
        "punch-L5",
        "long",
        r"G:\x\a.mp4",
        work_title="腦腐對策",
        file_bytes=100,
        duration_sec=752.3,
    )
    # 重跑（re-render 後檔案變大）→ 同一列更新，不重複建
    rid2 = store.register_release(
        "20260723 謝伯讓",
        "punch-L5",
        "long",
        r"G:\x\a.mp4",
        work_title="腦腐對策",
        file_bytes=200,
        duration_sec=752.3,
    )
    assert rid1 == rid2
    rel = store.get_release("20260723 謝伯讓", "punch-L5")
    assert rel["file_bytes"] == 200


def test_register_release_rejects_bad_format(store):
    with pytest.raises(ValueError):
        store.register_release("ep", "x", "vertical", "f.mp4")


def test_ensure_target_idempotent_and_preserves_state(store):
    rid = store.register_release("ep", "punch-S1", "short", "f.mp4")
    tid = store.ensure_target(rid, "youtube")
    # 修修填了文案 + 排程
    store.update_target(tid, title="真標題", status="approved", publish_at="2026-08-10T12:00:00Z")
    # re-render 重跑 ensure_target → 不得清掉既有狀態
    tid2 = store.ensure_target(rid, "youtube")
    assert tid == tid2
    rel = store.get_release("ep", "punch-S1")
    t = rel["targets"][0]
    assert t["title"] == "真標題"
    assert t["status"] == "approved"


def test_update_target_whitelist_and_status_domain(store):
    rid = store.register_release("ep", "c1", "long", "f.mp4")
    tid = store.ensure_target(rid)
    with pytest.raises(ValueError):
        store.update_target(tid, platform="ig")  # 白名單外
    with pytest.raises(ValueError):
        store.update_target(tid, status="done")  # 值域外
    with pytest.raises(ValueError):
        store.update_target(999999, status="approved")  # 不存在 fail loud


def test_claim_target_has_exactly_one_concurrent_winner(store):
    release_id = store.register_release("ep", "S01", "short", "f.mp4")
    target_id = store.ensure_target(release_id, "instagram_reels")
    store.update_target(target_id, status="approved")
    now = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)

    adapter_calls = []

    def claim_then_call_adapter(_):
        claim = store.claim_target(target_id, now=now)
        if claim is not None:
            adapter_calls.append(claim["id"])
        return claim

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim_then_call_adapter, range(2)))

    assert sum(claim is not None for claim in claims) == 1
    assert adapter_calls == [target_id]
    assert store.get_release("ep", "S01")["targets"][0]["status"] == "uploading"


def test_claim_target_rejects_fresh_uploading_and_reclaims_stale_checkpoint(store):
    release_id = store.register_release("ep", "S01", "short", "f.mp4")
    target_id = store.ensure_target(release_id, "instagram_reels")
    store.update_target(
        target_id,
        status="approved",
        checkpoint_json='{"container_id":"ig-resume"}',
    )
    started = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    first = store.claim_target(target_id, now=started)

    fresh = store.claim_target(
        target_id,
        now=started + store.TARGET_CLAIM_STALE_AFTER - timedelta(seconds=1),
    )
    stale = store.claim_target(
        target_id,
        now=started + store.TARGET_CLAIM_STALE_AFTER + timedelta(seconds=1),
    )

    assert first is not None
    assert fresh is None
    assert stale is not None
    assert stale["checkpoint_json"] == '{"container_id":"ig-resume"}'


@pytest.mark.parametrize("status", ["draft", "failed", "uploaded", "published", "ineligible"])
def test_claim_target_rejects_nonclaimable_terminal_or_unapproved_status(store, status):
    release_id = store.register_release("ep", status, "short", "f.mp4")
    target_id = store.ensure_target(release_id, "instagram_reels")
    store.update_target(target_id, status=status)

    assert store.claim_target(target_id, now=datetime(2026, 8, 20, tzinfo=UTC)) is None


def test_confirm_target_outcome_has_one_uploaded_same_video_winner(store):
    release_id = store.register_release("ep", "S01", "short", "f.mp4")
    target_id = store.ensure_target(release_id, "youtube")
    store.update_target(target_id, status="uploaded", video_id="yt-1")
    observed = store.get_release("ep", "S01")["targets"][0]

    first = store.confirm_target_outcome(
        target_id,
        expected_video_id="yt-1",
        expected_updated_at=observed["updated_at"],
        status="published",
        url="https://youtu.be/yt-1",
    )
    stale = store.confirm_target_outcome(
        target_id,
        expected_video_id="yt-1",
        expected_updated_at=observed["updated_at"],
        status="failed",
        error="must not overwrite",
    )

    target = store.get_release("ep", "S01")["targets"][0]
    assert first is True
    assert stale is False
    assert target["status"] == "published"
    assert target["video_id"] == "yt-1"
    assert target["url"] == "https://youtu.be/yt-1"
    assert target["error"] is None


def test_confirm_target_outcome_rejects_changed_video_identity(store):
    release_id = store.register_release("ep", "S01", "short", "f.mp4")
    target_id = store.ensure_target(release_id, "facebook_reels")
    store.update_target(target_id, status="uploaded", video_id="fb-new")
    observed = store.get_release("ep", "S01")["targets"][0]

    changed = store.confirm_target_outcome(
        target_id,
        expected_video_id="fb-old",
        expected_updated_at=observed["updated_at"],
        status="published",
        url="https://facebook.example/reel/new",
    )

    target = store.get_release("ep", "S01")["targets"][0]
    assert changed is False
    assert target["status"] == "uploaded"
    assert target["video_id"] == "fb-new"
    assert target["url"] is None


def test_confirm_target_outcome_has_exactly_one_concurrent_winner(store):
    release_id = store.register_release("ep", "S01", "short", "f.mp4")
    target_id = store.ensure_target(release_id, "youtube")
    store.update_target(target_id, status="uploaded", video_id="yt-1")
    observed = store.get_release("ep", "S01")["targets"][0]
    ready = Barrier(2)

    def confirm(status):
        ready.wait()
        return store.confirm_target_outcome(
            target_id,
            expected_video_id="yt-1",
            expected_updated_at=observed["updated_at"],
            status=status,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(confirm, ("published", "failed")))

    assert sorted(results) == [False, True]
    assert store.get_release("ep", "S01")["targets"][0]["status"] in {
        "published",
        "failed",
    }


def test_confirm_target_outcome_does_not_share_update_target_transaction(store):
    release_id = store.register_release("ep", "S01", "short", "f.mp4")
    youtube_id = store.ensure_target(release_id, "youtube")
    instagram_id = store.ensure_target(release_id, "instagram_reels")
    store.update_target(youtube_id, status="uploaded", video_id="yt-1")
    observed = next(
        target
        for target in store.get_release("ep", "S01")["targets"]
        if target["platform"] == "youtube"
    )
    ready = Barrier(2)

    def confirm():
        ready.wait()
        return store.confirm_target_outcome(
            youtube_id,
            expected_video_id="yt-1",
            expected_updated_at=observed["updated_at"],
            status="published",
        )

    def update_sibling():
        ready.wait()
        store.update_target(instagram_id, status="approved")

    with ThreadPoolExecutor(max_workers=2) as executor:
        confirm_future = executor.submit(confirm)
        update_future = executor.submit(update_sibling)
        assert confirm_future.result() is True
        assert update_future.result() is None

    targets = {target["platform"]: target for target in store.get_release("ep", "S01")["targets"]}
    assert targets["youtube"]["status"] == "published"
    assert targets["instagram_reels"]["status"] == "approved"


def test_social_target_metadata_and_ineligible_status_persist(store):
    rid = store.register_release("ep", "c1", "short", "f.mp4")
    tid = store.ensure_target(rid, "facebook_reels")
    store.update_target(
        tid,
        status="ineligible",
        adapter="meta_graph",
        idempotency_key="a" * 64,
        checkpoint_json='{"upload_id":"u-1"}',
        ineligibility_reason="over platform duration limit",
    )

    target = store.get_release("ep", "c1")["targets"][0]
    assert target["status"] == "ineligible"
    assert target["adapter"] == "meta_graph"
    assert target["idempotency_key"] == "a" * 64
    assert target["checkpoint_json"] == '{"upload_id":"u-1"}'
    assert target["ineligibility_reason"] == "over platform duration limit"


def test_list_releases_with_target_summary(store):
    rid = store.register_release("ep", "c1", "long", "f.mp4")
    store.ensure_target(rid, "youtube")
    rows = store.list_releases("ep")
    assert len(rows) == 1
    assert rows[0]["target_status"] == {"youtube": "draft"}


def test_release_campaign_anchor_schedules_all_targets_at_one_utc_instant(store):
    release_id = store.register_release("ep", "S01", "short", "f.mp4")
    target_ids = [
        store.ensure_target(release_id, platform)
        for platform in ("youtube", "instagram_reels", "facebook_reels")
    ]
    store.update_target(target_ids[0], status="approved")
    store.update_target(target_ids[1], status="failed")
    anchor = datetime(2026, 8, 25, 9, 0, tzinfo=timezone(timedelta(hours=8)))

    snapshot = _set_anchor(store, "ep", "S01", anchor)

    release = store.get_release("ep", "S01")
    assert {target["publish_at"] for target in release["targets"]} == {"2026-08-25T01:00:00+00:00"}
    assert [target["status"] for target in release["targets"]] == [
        "draft",
        "failed",
        "approved",
    ]
    assert snapshot.state == "shared"
    assert snapshot.anchor_at == datetime(2026, 8, 25, 1, 0, tzinfo=UTC)


def test_release_campaign_anchor_unschedule_clears_every_target(store):
    release_id = store.register_release("ep", "S01", "short", "f.mp4")
    for platform in ("youtube", "instagram_reels", "facebook_reels"):
        store.ensure_target(release_id, platform)
    _set_anchor(store, "ep", "S01", datetime(2026, 8, 25, tzinfo=UTC))

    snapshot = _set_anchor(store, "ep", "S01", None)

    assert snapshot.state == "none"
    assert snapshot.anchor_at is None
    assert all(value is None for _, value in snapshot.target_anchors)


@pytest.mark.parametrize("locked_status", ["uploading", "uploaded", "published"])
def test_release_campaign_anchor_rejects_locked_target_without_partial_write(store, locked_status):
    release_id = store.register_release("ep", "S01", "short", "f.mp4")
    youtube = store.ensure_target(release_id, "youtube")
    instagram = store.ensure_target(release_id, "instagram_reels")
    store.update_target(youtube, publish_at="2026-08-20T01:00:00+00:00")
    store.update_target(
        instagram,
        status=locked_status,
        publish_at="2026-08-20T01:00:00+00:00",
    )

    with pytest.raises(ValueError, match="已鎖定"):
        _set_anchor(store, "ep", "S01", datetime(2026, 8, 25, tzinfo=UTC))

    assert {target["publish_at"] for target in store.get_release("ep", "S01")["targets"]} == {
        "2026-08-20T01:00:00+00:00"
    }


def test_release_campaign_anchor_surfaces_divergent_and_missing_target_values(store):
    release_id = store.register_release("ep", "S01", "short", "f.mp4")
    youtube = store.ensure_target(release_id, "youtube")
    store.ensure_target(release_id, "instagram_reels")
    none_snapshot = store.get_release_campaign_anchor("ep", "S01")
    store.update_target(youtube, publish_at="2026-08-20T01:00:00+00:00")

    snapshot = store.get_release_campaign_anchor("ep", "S01")

    assert snapshot.state == "divergent"
    assert snapshot.anchor_at is None
    assert snapshot.expected_token != none_snapshot.expected_token
    assert snapshot.target_anchors == (
        ("instagram_reels", None),
        ("youtube", "2026-08-20T01:00:00+00:00"),
    )


def test_release_campaign_anchor_rejects_stale_open_page_without_overwrite(store):
    release_id = store.register_release("ep", "S01", "short", "f.mp4")
    for platform in ("youtube", "instagram_reels", "facebook_reels"):
        store.ensure_target(release_id, platform)
    open_page = store.get_release_campaign_anchor("ep", "S01")
    first_writer_anchor = datetime(2026, 8, 25, 1, tzinfo=UTC)
    store.set_release_campaign_anchor(
        "ep",
        "S01",
        first_writer_anchor,
        expected_anchor_token=open_page.expected_token,
    )

    with pytest.raises(ValueError, match="stale Campaign Anchor"):
        store.set_release_campaign_anchor(
            "ep",
            "S01",
            datetime(2026, 8, 26, 1, tzinfo=UTC),
            expected_anchor_token=open_page.expected_token,
        )

    current = store.get_release_campaign_anchor("ep", "S01")
    assert current.anchor_at == first_writer_anchor


def test_release_campaign_anchor_fails_closed_for_invalid_release_shape(store):
    with pytest.raises(ValueError, match="不存在"):
        store.set_release_campaign_anchor(
            "missing", "S01", None, expected_anchor_token="release-anchor-v1:missing"
        )

    store.register_release("ep", "S01", "short", "f.mp4")
    with pytest.raises(ValueError, match="沒有 targets"):
        store.set_release_campaign_anchor(
            "ep", "S01", None, expected_anchor_token="release-anchor-v1:no-targets"
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        store.set_release_campaign_anchor(
            "ep",
            "S01",
            datetime(2026, 8, 25, 9, 0),
            expected_anchor_token="release-anchor-v1:unused",
        )
