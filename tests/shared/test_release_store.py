"""release_store 五法測試（tmp DB via DB_PATH env——shared.config 慣例）。"""

import importlib
import sys
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
