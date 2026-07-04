"""Foundry storyboard Bridge UI router (ADR-032 §7, PR-5)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.brook.script_video import edit_log
from thousand_sunny.routers import brook_video


@pytest.fixture(autouse=True)
def _no_auth(monkeypatch):
    # Empty WEB_PASSWORD => check_auth returns True.
    monkeypatch.delenv("WEB_PASSWORD", raising=False)


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    """Point router data root + edit_log dir at tmp_path."""
    brook_video._set_data_root(tmp_path / "data")
    monkeypatch.setattr(edit_log, "_LOG_DIR", tmp_path / "edit_log")
    monkeypatch.setattr(brook_video, "_EXAMPLES_DIR", tmp_path / "examples")
    return tmp_path


@pytest.fixture
def render_calls(monkeypatch):
    """Capture background render dispatches without spawning subprocesses."""
    calls: list[tuple[str, int]] = []

    async def _fake(episode_id, beat_id):
        calls.append((episode_id, beat_id))

    monkeypatch.setattr(brook_video, "_render_beat", _fake)
    return calls


@pytest.fixture
def client(isolated_root):
    app = FastAPI()
    app.include_router(brook_video.page_router)
    return TestClient(app)


def _seed_storyboard(root: Path, episode_id: str, beats: list[dict]) -> Path:
    ep_dir = root / "data" / episode_id
    ep_dir.mkdir(parents=True)
    sb = ep_dir / "storyboard.yaml"
    sb.write_text(
        yaml.dump(beats, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    return sb


def _beat(beat_id: int, **over) -> dict:
    base = {
        "beat_id": beat_id,
        "start_quote": f"start {beat_id}",
        "end_quote": f"end {beat_id}",
        "broll_decision": "cutaway",
        "layout": "full_broll",
        "broll": {
            "render_target": "hyperframes",
            "component": "bigstat",
            "params": {"value": "42%"},
            "transitions": {"in_transition": None, "out_transition": None},
        },
        "status": {"text_approved": False, "render_status": "pending", "visual_approved": False},
        "user_notes": [],
    }
    base.update(over)
    return base


def test_storyboard_page_renders_table(client, isolated_root):
    _seed_storyboard(isolated_root, "ep-1", [_beat(1), _beat(2)])
    res = client.get("/brook/video/ep-1")
    assert res.status_code == 200
    assert "ep-1" in res.text
    assert 'data-beat-id="1"' in res.text
    assert 'data-beat-id="2"' in res.text


def test_status_endpoint_returns_chip_payload(client, isolated_root):
    _seed_storyboard(
        isolated_root,
        "ep-2",
        [
            _beat(
                7, status={"text_approved": True, "render_status": "done", "visual_approved": False}
            )
        ],
    )
    res = client.get("/brook/video/ep-2/status")
    assert res.status_code == 200
    data = res.json()
    assert len(data["beats"]) == 1
    assert data["beats"][0]["render_status"] == "done"
    assert data["beats"][0]["text_approved"] is True


def test_approve_sets_text_approved_and_enqueues(client, isolated_root, render_calls):
    sb = _seed_storyboard(isolated_root, "ep-3", [_beat(5)])
    res = client.post("/brook/video/ep-3/beat/5/approve", follow_redirects=False)
    assert res.status_code == 303
    saved = yaml.safe_load(sb.read_text(encoding="utf-8"))
    assert saved[0]["status"]["text_approved"] is True
    assert saved[0]["status"]["render_status"] == "rendering"
    assert ("ep-3", 5) in render_calls


def test_edit_updates_layout_and_does_not_write_edit_log(client, isolated_root, render_calls):
    sb = _seed_storyboard(isolated_root, "ep-4", [_beat(3)])
    res = client.post(
        "/brook/video/ep-4/beat/3/edit",
        data={"layout": "side_overlay_l", "component": "bigstat", "params": '{"value": "99%"}'},
        follow_redirects=False,
    )
    assert res.status_code == 303
    saved = yaml.safe_load(sb.read_text(encoding="utf-8"))
    assert saved[0]["layout"] == "side_overlay_l"
    assert saved[0]["broll"]["params"] == {"value": "99%"}
    assert saved[0]["status"]["text_approved"] is True
    # edit-fields does NOT write edit_log (ADR §9)
    assert edit_log.read_entries("ep-4") == []


def test_replan_writes_edit_log_and_clears_approval(client, isolated_root):
    sb = _seed_storyboard(
        isolated_root,
        "ep-5",
        [
            _beat(
                2, status={"text_approved": True, "render_status": "done", "visual_approved": False}
            )
        ],
    )
    res = client.post(
        "/brook/video/ep-5/beat/2/replan",
        data={"note": "too generic"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    saved = yaml.safe_load(sb.read_text(encoding="utf-8"))
    assert saved[0]["status"]["text_approved"] is False
    assert saved[0]["status"]["render_status"] == "pending"
    entries = edit_log.read_entries("ep-5")
    assert len(entries) == 1
    assert entries[0]["action"] == "replan"
    assert entries[0]["user_note"] == "too generic"


def test_replan_invokes_agent_and_records_edit_ops(client, isolated_root, monkeypatch):
    """ADR-038 §D3: Bridge endpoint runs replan_agent, applies edits, records ops."""
    from agents.brook.script_video import replan_agent
    from agents.brook.script_video.beat_editor import SetLayout

    _seed_storyboard(
        isolated_root,
        "ep-replan-agent",
        [
            _beat(
                7,
                status={
                    "text_approved": True,
                    "render_status": "done",
                    "visual_approved": False,
                },
            )
        ],
    )

    def fake_run(storyboard, beat_id, note, **kwargs):
        return replan_agent.ReplanResult(
            edits=[SetLayout(beat_id=beat_id, layout="side_overlay_l")],
            iterations=1,
            tokens_used=150,
            terminated_reason="end_turn",
        )

    monkeypatch.setattr(replan_agent, "run", fake_run)
    res = client.post(
        "/brook/video/ep-replan-agent/beat/7/replan",
        data={"note": "make it side overlay"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    # storyboard updated by the edit
    saved = yaml.safe_load(
        (isolated_root / "data" / "ep-replan-agent" / "storyboard.yaml").read_text(encoding="utf-8")
    )
    assert saved[0]["layout"] == "side_overlay_l"
    # edit_log entry contains edit_ops AND diff
    entries = edit_log.read_entries("ep-replan-agent")
    assert len(entries) == 1
    assert entries[0]["edit_ops"] is not None
    assert entries[0]["edit_ops"][0]["op"] == "set_layout"
    assert entries[0]["edit_ops"][0]["layout"] == "side_overlay_l"
    assert entries[0]["diff"] is not None


def test_batch_approve_all_text_marks_all_pending(client, isolated_root, render_calls):
    sb = _seed_storyboard(
        isolated_root,
        "ep-6",
        [
            _beat(1),
            _beat(2),
            _beat(
                3, status={"text_approved": True, "render_status": "done", "visual_approved": False}
            ),
        ],
    )
    res = client.post("/brook/video/ep-6/batch/approve-all-text", follow_redirects=False)
    assert res.status_code == 303
    saved = yaml.safe_load(sb.read_text(encoding="utf-8"))
    assert all(b["status"]["text_approved"] for b in saved)
    # Only the previously-pending beats are enqueued for render.
    queued = {bid for _, bid in render_calls}
    assert queued == {1, 2}


def test_batch_render_approved_reenqueues_failed(client, isolated_root, render_calls):
    sb = _seed_storyboard(
        isolated_root,
        "ep-7",
        [
            _beat(
                1,
                status={"text_approved": True, "render_status": "failed", "visual_approved": False},
            ),
            _beat(
                2,
                status={
                    "text_approved": False,
                    "render_status": "pending",
                    "visual_approved": False,
                },
            ),
            _beat(
                3, status={"text_approved": True, "render_status": "done", "visual_approved": False}
            ),
        ],
    )
    res = client.post("/brook/video/ep-7/batch/render-approved", follow_redirects=False)
    assert res.status_code == 303
    queued = {bid for _, bid in render_calls}
    assert queued == {1}  # only text_approved + (pending|failed)
    saved = yaml.safe_load(sb.read_text(encoding="utf-8"))
    assert saved[0]["status"]["render_status"] == "rendering"


def test_batch_finalize_passing_sets_visual_approved(client, isolated_root):
    sb = _seed_storyboard(
        isolated_root,
        "ep-8",
        [
            _beat(
                1, status={"text_approved": True, "render_status": "done", "visual_approved": False}
            ),
            _beat(
                2,
                status={
                    "text_approved": True,
                    "render_status": "rendering",
                    "visual_approved": False,
                },
            ),
        ],
    )
    res = client.post("/brook/video/ep-8/batch/finalize-passing", follow_redirects=False)
    assert res.status_code == 303
    saved = yaml.safe_load(sb.read_text(encoding="utf-8"))
    assert saved[0]["status"]["visual_approved"] is True
    assert saved[1]["status"]["visual_approved"] is False


def test_visual_replan_writes_edit_log_with_visual_tag(client, isolated_root):
    _seed_storyboard(
        isolated_root,
        "ep-9",
        [
            _beat(
                4, status={"text_approved": True, "render_status": "done", "visual_approved": False}
            )
        ],
    )
    res = client.post(
        "/brook/video/ep-9/beat/4/visual/replan",
        data={"note": "color off"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    entries = edit_log.read_entries("ep-9")
    assert len(entries) == 1
    assert entries[0]["action"] == "replan-visual"


def test_promote_to_example_writes_yaml_and_index(client, isolated_root):
    _seed_storyboard(isolated_root, "ep-10", [_beat(8)])
    # Drive one replan to populate edit_log.
    client.post(
        "/brook/video/ep-10/beat/8/replan", data={"note": "promote-me"}, follow_redirects=False
    )
    res = client.post(
        "/brook/video/ep-10/edit-log/0/promote",
        data={"tag": "stat-callout"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    examples_dir = brook_video._EXAMPLES_DIR
    yamls = list(examples_dir.glob("*-beat8-stat-callout.yaml"))
    assert len(yamls) == 1
    index = yaml.safe_load((examples_dir / "_index.yaml").read_text(encoding="utf-8"))
    assert any(e["tag"] == "stat-callout" for e in index["examples"])


def test_storyboard_404_when_missing(client, isolated_root):
    res = client.get("/brook/video/does-not-exist")
    assert res.status_code == 404
