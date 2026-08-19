"""release_store 五法測試（tmp DB via DB_PATH env——shared.config 慣例）。"""

import importlib
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

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
