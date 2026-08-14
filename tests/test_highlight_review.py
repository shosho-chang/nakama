"""Bridge highlight selection gate: authenticated, bounded and auditable."""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _candidate(candidate_id: str, *, veto: bool = False) -> dict:
    return {
        "id": candidate_id,
        "format": "long",
        "variant_group": f"group-{candidate_id}",
        "title": f"候選 {candidate_id}",
        "hook": f"{candidate_id} 的 hook",
        "duration_sec": 120.0,
        "veto": veto,
    }


@pytest.fixture
def episode_root(tmp_path):
    highlights = tmp_path / "ep-001" / "highlights"
    highlights.mkdir(parents=True)
    candidates = [_candidate(f"L{i}") for i in range(1, 7)]
    candidates[2] = _candidate("L3", veto=True)
    (highlights / "candidates.json").write_text(
        json.dumps({"candidates": candidates}, ensure_ascii=False), encoding="utf-8"
    )
    for scorer, base in (("azhe", 90), ("kevin", 80), ("shufen", 70)):
        (highlights / f"review_{scorer}.json").write_text(
            json.dumps(
                {"scores": [{"id": f"L{i}", "total": base - i} for i in range(1, 7)]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    (highlights / "lens_brand.json").write_text(
        json.dumps({"findings": [{"id": "L3", "severity": "veto", "issue": "品牌 veto"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def client(monkeypatch, episode_root):
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(episode_root))
    monkeypatch.setenv("WEB_PASSWORD", "gate-password")
    monkeypatch.setenv("WEB_SECRET", "gate-secret")
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.highlight_review as highlight_module

    importlib.reload(auth_module)
    importlib.reload(highlight_module)
    # The full application imports every optional agent integration.  This test is
    # intentionally an HTTP contract test for the gate itself, not an integration
    # test that requires configured LLM or calendar clients.
    app = FastAPI()
    app.include_router(highlight_module.page_router)
    return TestClient(app)


def _auth_cookie():
    from thousand_sunny.auth import make_token

    return {"nakama_auth": make_token("gate-password")}


def test_auth_redirect(client):
    response = client.get("/bridge/highlights/ep-001", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login?next=/bridge/highlights/ep-001"


def test_root_missing_and_unknown_episode_fail_loud(client, monkeypatch):
    monkeypatch.delenv("PODCAST_EPISODES_ROOT")
    assert client.get("/bridge/highlights/ep-001", cookies=_auth_cookie()).status_code == 503
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", "C:/definitely-not-an-episode-root")
    assert client.get("/bridge/highlights/ep-001", cookies=_auth_cookie()).status_code == 503


def test_shows_at_most_five_ranked_candidates(client):
    response = client.get("/bridge/highlights/ep-001", cookies=_auth_cookie())
    assert response.status_code == 200
    for candidate_id in ("L1", "L2", "L3", "L4", "L5"):
        assert f"候選 {candidate_id}" in response.text
    assert "候選 L6" not in response.text
    assert "品牌 veto" in response.text


def test_decision_requires_exactly_three_distinct_candidates(client):
    response = client.post(
        "/bridge/highlights/ep-001/decide",
        data={"candidate_id": ["L1", "L1", "L2"]},
        cookies=_auth_cookie(),
    )
    assert response.status_code == 400
    assert "exactly three" in response.text


def test_veto_requires_explicit_override(client):
    response = client.post(
        "/bridge/highlights/ep-001/decide",
        data={"candidate_id": ["L1", "L2", "L3"]},
        cookies=_auth_cookie(),
    )
    assert response.status_code == 400
    assert "override_veto" in response.text


def test_writes_compatible_winners_and_append_only_feedback(client, episode_root):
    data = {
        "candidate_id": ["L1", "L2", "L3"],
        "override_veto": ["L3"],
        "feedback_L1": "成片開頭最有力",
        "feedback_L2": "備選仍保留",
        "feedback_L3": "理解風險後仍選",
    }
    response = client.post(
        "/bridge/highlights/ep-001/decide", data=data, cookies=_auth_cookie(), follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/bridge/highlights/ep-001?saved=1"
    highlights = episode_root / "ep-001" / "highlights"
    winners = json.loads((highlights / "winners.json").read_text(encoding="utf-8"))
    assert [winner["id"] for winner in winners["winners"]] == ["L1", "L2", "L3"]
    assert [winner["rank"] for winner in winners["winners"]] == [1, 2, 3]
    assert winners["picked_by"] == "修修 (Bridge highlight review gate)"
    audit = json.loads((highlights / "review_feedback.json").read_text(encoding="utf-8"))
    assert audit["decisions"][0]["feedback"]["L1"] == "成片開頭最有力"
    assert audit["decisions"][0]["override_veto_ids"] == ["L3"]
    response = client.post(
        "/bridge/highlights/ep-001/decide", data=data, cookies=_auth_cookie(), follow_redirects=False
    )
    assert response.status_code == 303
    audit = json.loads((highlights / "review_feedback.json").read_text(encoding="utf-8"))
    assert len(audit["decisions"]) == 2


def test_path_traversal_rejected(client):
    response = client.get("/bridge/highlights/..", cookies=_auth_cookie(), follow_redirects=False)
    assert response.status_code in (404, 405)


def test_accepts_a_chinese_episode_folder_and_keeps_rejection_feedback(client, episode_root):
    source = episode_root / "ep-001"
    episode = episode_root / "20260721 鄭國威"
    source.rename(episode)
    response = client.post(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/decide",
        data={
            "candidate_id": ["L1", "L2", "L4"],
            "feedback_L5": "論點與 L2 重疊，這次不選",
        },
        cookies=_auth_cookie(),
        follow_redirects=False,
    )
    assert response.status_code == 303
    audit = json.loads((episode / "highlights" / "review_feedback.json").read_text(encoding="utf-8"))
    assert audit["decisions"][0]["feedback"]["L5"] == "論點與 L2 重疊，這次不選"
