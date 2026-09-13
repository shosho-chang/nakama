"""開機掃一次 queued 修正單——建單當下觸發只涵蓋一半的情形。

2026-09-10 實際發生：修修在 Bridge 上送出修改單的時候 `NAKAMA_CAROUSEL_AUTORUN`
還沒開，那張單就永遠孤在 `queued`，UI 只顯示「等待 agent 認領」，而現實中沒有
那個 agent。Bridge 沒開、當機、或 flag 事後才打開，全部是同一個洞。
"""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from shared.schemas.podcast_carousel import CarouselCorrectionJobV1


def _job_payload(job_id: str, *, created_at: datetime, **overrides) -> dict:
    payload = {
        "job_id": job_id,
        "episode_id": "20260901 蘇予昕",
        "source_revision": "r001",
        "source_manifest_sha256": "b" * 64,
        "created_at": created_at,
        "updated_at": created_at,
        "copy_edits": [
            {
                "page_id": "hook",
                "role": "hook",
                "artifact_sha256": "c" * 64,
                "fields": {"bridge": "改過的承接文字"},
            }
        ],
    }
    payload.update(overrides)
    return CarouselCorrectionJobV1.model_validate(payload).model_dump(mode="json")


def _write_job(root: Path, episode: str, payload: dict) -> None:
    directory = root / episode / "ig-carousel" / "correction_jobs"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{payload['job_id']}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def carousel_module(monkeypatch, tmp_path):
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(tmp_path))
    monkeypatch.setenv("NAKAMA_CAROUSEL_AUTORUN", "1")
    monkeypatch.setenv("WEB_PASSWORD", "sweep-password")
    monkeypatch.setenv("WEB_SECRET", "sweep-secret")
    import thousand_sunny.routers.carousel_review as module

    importlib.reload(module)
    return module


def test_sweep_finds_an_orphaned_queued_job(carousel_module, tmp_path):
    _write_job(
        tmp_path,
        "20260901 蘇予昕",
        _job_payload("cj-" + "a" * 32, created_at=datetime.now(UTC)),
    )
    assert carousel_module.sweep_queued_autorunnable_jobs() == [
        ("20260901 蘇予昕", "cj-" + "a" * 32)
    ]


def test_sweep_is_ordered_oldest_first_across_episodes(carousel_module, tmp_path):
    now = datetime.now(UTC)
    _write_job(
        tmp_path,
        "20260901 蘇予昕",
        _job_payload("cj-" + "b" * 32, created_at=now),
    )
    _write_job(
        tmp_path,
        "20260805 林之晨",
        _job_payload(
            "cj-" + "a" * 32,
            created_at=now - timedelta(hours=2),
            episode_id="20260805 林之晨",
        ),
    )
    assert carousel_module.sweep_queued_autorunnable_jobs() == [
        ("20260805 林之晨", "cj-" + "a" * 32),
        ("20260901 蘇予昕", "cj-" + "b" * 32),
    ]


def test_sweep_skips_jobs_that_already_have_an_executor(carousel_module, tmp_path):
    """`claimed` / `in_progress` 已經有人在跑，再撿一次就是搶同一張單。"""
    now = datetime.now(UTC)
    _write_job(
        tmp_path,
        "20260901 蘇予昕",
        _job_payload(
            "cj-" + "a" * 32,
            created_at=now,
            status="claimed",
            source_manifest_receipt={
                "path": "ig-carousel/r001/manifest.json",
                "bytes": 2048,
                "sha256": "e" * 64,
            },
            claim={
                "executor": "claude_code",
                "executor_id": "desktop",
                "claim_token": "d" * 32,
                "claimed_at": now,
                "lease_seconds": 3600,
                "lease_expires_at": now + timedelta(hours=1),
            },
        ),
    )
    assert carousel_module.sweep_queued_autorunnable_jobs() == []


def test_sweep_leaves_free_text_jobs_for_an_agent(carousel_module, tmp_path):
    """自由文字的修改意見機械套不了——那張單本來就該留在 queued 等 agent。"""
    _write_job(
        tmp_path,
        "20260901 蘇予昕",
        _job_payload(
            "cj-" + "a" * 32,
            created_at=datetime.now(UTC),
            copy_edits=[],
            feedback_items=[
                {"page_id": "hook", "artifact_sha256": "c" * 64, "feedback": "這句太繞了"}
            ],
        ),
    )
    assert carousel_module.sweep_queued_autorunnable_jobs() == []


def test_sweep_is_closed_when_autorun_is_off(carousel_module, tmp_path, monkeypatch):
    """VPS 沒有 Chrome 也沒有 footage 磁碟：撿起來只會認領完就 failed，比不撿更糟。"""
    monkeypatch.setenv("NAKAMA_CAROUSEL_AUTORUN", "0")
    _write_job(
        tmp_path,
        "20260901 蘇予昕",
        _job_payload("cj-" + "a" * 32, created_at=datetime.now(UTC)),
    )
    assert carousel_module.sweep_queued_autorunnable_jobs() == []


def test_sweep_survives_an_unconfigured_episode_root(carousel_module, monkeypatch):
    monkeypatch.delenv("PODCAST_EPISODES_ROOT")
    assert carousel_module.sweep_queued_autorunnable_jobs() == []
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", "C:/definitely-not-an-episode-root")
    assert carousel_module.sweep_queued_autorunnable_jobs() == []


def test_sweep_runs_every_job_it_finds(carousel_module, tmp_path, monkeypatch):
    now = datetime.now(UTC)
    _write_job(tmp_path, "20260901 蘇予昕", _job_payload("cj-" + "b" * 32, created_at=now))
    _write_job(
        tmp_path,
        "20260805 林之晨",
        _job_payload(
            "cj-" + "a" * 32,
            created_at=now - timedelta(hours=2),
            episode_id="20260805 林之晨",
        ),
    )
    ran: list[tuple[str, str]] = []
    monkeypatch.setattr(
        carousel_module,
        "_autorun_structured_job",
        lambda slug, job_id: ran.append((slug, job_id)),
    )
    carousel_module.run_queued_autorun_sweep()
    assert ran == [
        ("20260805 林之晨", "cj-" + "a" * 32),
        ("20260901 蘇予昕", "cj-" + "b" * 32),
    ]
