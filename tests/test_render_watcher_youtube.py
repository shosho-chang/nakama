"""render_watcher 的主頻道 YouTube 封面分支。"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import render_watcher as rw  # noqa: E402

SLUG = "20261027-wealth-ladder"


def _write_cover(vault: Path, requested_at, rendered_at=None, slug=SLUG):
    d = vault / "Attachments" / "packaging" / slug
    d.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": "nakama.youtube_cover.v1",
        "episode": "ep",
        "recipe": {
            "bg": {"src": f"Attachments/packaging/{slug}/assets/bg.jpg"},
            "feature": {
                "src": f"Attachments/packaging/{slug}/assets/feature.jpg",
                "cx": 1,
                "cy": 1,
                "h": 500,
            },
            "person": {
                "src": "Attachments/cutouts/shosho/explaining/2.png",
                "cx": 1,
                "top": 1,
                "h": 800,
            },
            "text": {"x": 1, "y": 1, "size": 100},
        },
        "requested_at": requested_at.isoformat() if requested_at else None,
        "rendered_at": rendered_at.isoformat() if rendered_at else None,
    }
    (d / "youtube_cover.json").write_text(json.dumps(doc), encoding="utf-8")
    return d


def test_pending_when_requested_after_render(tmp_path):
    now = datetime.now(timezone.utc)
    _write_cover(tmp_path, now)  # 從沒出過圖
    _write_cover(tmp_path, now, now + timedelta(seconds=1), slug="done")  # 已出圖
    _write_cover(tmp_path, None, slug="never-saved")  # 沒存過配方
    jobs = rw.pending_youtube_covers(tmp_path, {})
    assert [j["slug"] for j in jobs] == [SLUG]


def test_conflict_and_broken_files_are_skipped(tmp_path):
    now = datetime.now(timezone.utc)
    d = _write_cover(tmp_path, now)
    (d / "youtube_cover.sync-conflict-20261112.json").write_text("{}", encoding="utf-8")
    bad = tmp_path / "Attachments" / "packaging" / "bad"
    bad.mkdir(parents=True)
    (bad / "youtube_cover.json").write_text("{not json", encoding="utf-8")
    assert rw.pending_youtube_covers(tmp_path, {}) == []


def test_failed_attempt_not_retried_until_saved_again(tmp_path, monkeypatch):
    monkeypatch.setattr(rw, "get_vault_path", lambda: tmp_path)
    now = datetime.now(timezone.utc)
    _write_cover(tmp_path, now)
    state, state_path = {}, tmp_path / "state.json"
    job = rw.pending_youtube_covers(tmp_path, state)[0]

    def boom(slug, vault):
        raise RuntimeError("chrome crashed")

    assert rw.render_youtube_one(job, state, state_path, None, render=boom) is False
    assert rw.pending_youtube_covers(tmp_path, state) == []  # 同一份配方不重試
    _write_cover(tmp_path, now + timedelta(seconds=30))  # 修修再存一次
    assert [j["slug"] for j in rw.pending_youtube_covers(tmp_path, state)] == [SLUG]


def test_success_records_state(tmp_path, monkeypatch):
    monkeypatch.setattr(rw, "get_vault_path", lambda: tmp_path)
    _write_cover(tmp_path, datetime.now(timezone.utc))
    state, state_path = {}, tmp_path / "state.json"
    job = rw.pending_youtube_covers(tmp_path, state)[0]
    calls = []
    ok = rw.render_youtube_one(
        job, state, state_path, None, render=lambda s, v: calls.append(s) or {"png": "x.png"}
    )
    assert ok and calls == [SLUG]
    assert (
        json.loads(state_path.read_text(encoding="utf-8"))[f"youtube_cover:{SLUG}"]["requested_at"]
        == job["requested_at"]
    )
