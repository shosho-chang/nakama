"""Bridge highlight selection gate: authenticated, bounded and auditable."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _candidate(candidate_id: str, *, veto: bool = False) -> dict:
    index = int(candidate_id[1:])
    return {
        "id": candidate_id,
        "format": "long",
        "variant_group": f"group-{candidate_id}",
        "title": f"候選 {candidate_id}",
        "hook": f"{candidate_id} 的 hook",
        "rationale": f"reason {candidate_id}",
        "transcript": f"transcript evidence {candidate_id}",
        "t_start": index * 100,
        "t_end": index * 100 + 120,
        "duration_sec": 120.0,
        "veto": veto,
    }


def _full_episode_packages(episode_id: str) -> dict:
    titles = [
        {
            "text": f"完整節目標題 {rank}",
            "archetype_id": "T-A3",
            "angle_combo": ["完整節目"],
            "payoff": "完整訪談",
            "cite": "full.srt#1",
            "rank": rank,
            "panel_note": "備選" if rank >= 4 else None,
        }
        for rank in range(1, 6)
    ]
    packages = [
        {
            "title_rank": rank,
            "thumbnail_png": f"Attachments/packaging/ep-001/pkg-full-{rank}.png",
            "thumb_archetype_id": "T-V8",
            "joint_pairing_id": "JP-1",
            "host_cutout": "Attachments/cutouts/host.png",
            "guest_cutout": "Attachments/cutouts/guest.png",
        }
        for rank in range(1, 4)
    ]
    return {
        "episode": episode_id,
        "generated_at": "2026-08-27T01:00:00+00:00",
        "cuts": [
            {
                "cut_id": "full",
                "format": "long",
                "information_origin": "full_text",
                "visual_recipe": "podcast",
                "aspect": "16:9",
                "titles": titles,
                "packages": packages,
                "citations": [],
                "brand_flags": [],
            }
        ],
    }


@pytest.fixture
def episode_root(tmp_path):
    highlights = tmp_path / "ep-001" / "highlights"
    highlights.mkdir(parents=True)
    candidates = [_candidate(f"L{i}") for i in range(1, 7)]
    candidates[2] = _candidate("L3", veto=True)
    candidates_path = highlights / "candidates.json"
    candidates_path.write_text(
        json.dumps(
            {
                "editorial_master_lineage": {
                    "contract": "podcast-editorial-master-v1",
                    "episode_id": "ep-001",
                    "content_hash": "1" * 64,
                    "master_media_sha256": "2" * 64,
                    "master_srt_sha256": "3" * 64,
                    "editorial_master_receipt": ("editorial-master/v1/EDITORIAL-MASTER.json"),
                },
                "candidates": candidates,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source_sha256 = hashlib.sha256(candidates_path.read_bytes()).hexdigest()
    (tmp_path / "ep-001" / "Default_program.mp4").write_bytes(b"test-video")
    for scorer, base in (("azhe", 90), ("kevin", 80), ("shufen", 70)):
        (highlights / f"review_{scorer}.json").write_text(
            json.dumps(
                {
                    "source_sha256": source_sha256,
                    "scores": [{"id": f"L{i}", "total": base - i} for i in range(1, 7)],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    (highlights / "lens_brand.json").write_text(
        json.dumps(
            {
                "source_sha256": source_sha256,
                "findings": [
                    {
                        "id": f"L{i}",
                        "severity": "veto" if i == 3 else None,
                        "issue": "品牌 veto" if i == 3 else "",
                    }
                    for i in range(1, 7)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (highlights / "lens_renee.json").write_text(
        json.dumps(
            {
                "lens": "renee",
                "source_sha256": source_sha256,
                "findings": [
                    {
                        "id": f"L{i}",
                        "hook_risk": "",
                        "retention_risk": "",
                        "boundary_action": "keep",
                    }
                    for i in range(1, 7)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def client(monkeypatch, episode_root):
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(episode_root))
    vault = episode_root / "vault"
    vault.mkdir(exist_ok=True)
    monkeypatch.setenv("VAULT_PATH", str(vault))
    monkeypatch.setenv("WEB_PASSWORD", "gate-password")
    monkeypatch.setenv("WEB_SECRET", "gate-secret")
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.highlight_review as highlight_module

    importlib.reload(auth_module)
    importlib.reload(highlight_module)
    _install_verified_master(monkeypatch, episode_root)
    # The full application imports every optional agent integration.  This test is
    # intentionally an HTTP contract test for the gate itself, not an integration
    # test that requires configured LLM or calendar clients.
    app = FastAPI()
    app.include_router(highlight_module.page_router)
    return TestClient(app)


def _auth_cookie():
    from thousand_sunny.auth import make_token

    return {"nakama_auth": make_token("gate-password")}


def _rebind_review_inputs(candidates_path) -> None:
    """Keep evidence fresh when a test intentionally mutates candidate bytes."""

    source_sha256 = hashlib.sha256(candidates_path.read_bytes()).hexdigest()
    highlights = candidates_path.parent
    for name in (
        "review_azhe.json",
        "review_kevin.json",
        "review_shufen.json",
        "lens_brand.json",
        "lens_renee.json",
    ):
        path = highlights / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["source_sha256"] = source_sha256
        path.write_text(json.dumps(payload), encoding="utf-8")


def _install_verified_master(monkeypatch, episode_root):
    import thousand_sunny.routers.highlight_review as highlight_module

    episode = episode_root / "ep-001"
    media = episode / "editorial-master" / "v1" / "master.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"master-video")
    lineage = json.loads((episode / "highlights" / "candidates.json").read_text(encoding="utf-8"))[
        "editorial_master_lineage"
    ]
    selection = SimpleNamespace(media_path=media, identity=lambda: lineage)
    monkeypatch.setattr(highlight_module.EditorialMasterRequest, "open", lambda self: selection)
    return media, lineage


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


def test_invalid_candidate_timecodes_fail_loud(client, episode_root):
    candidates_path = episode_root / "ep-001" / "highlights" / "candidates.json"
    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    payload["candidates"][0]["t_end"] = "not-a-number"
    candidates_path.write_text(json.dumps(payload), encoding="utf-8")
    _rebind_review_inputs(candidates_path)

    response = client.get("/bridge/highlights/ep-001", cookies=_auth_cookie())
    assert response.status_code == 422
    assert "invalid timecodes" in response.text


def test_decision_requires_exactly_three_distinct_candidates(client):
    response = client.post(
        "/bridge/highlights/ep-001/decide",
        data={"candidate_id": ["L1", "L1", "L2"]},
        cookies=_auth_cookie(),
    )
    assert response.status_code == 400
    assert "exactly three" in response.text


def test_review_page_exposes_preview_and_evidence(client):
    response = client.get("/bridge/highlights/ep-001", cookies=_auth_cookie())
    assert response.status_code == 200
    assert 'id="highlight-player"' in response.text
    assert "transcript evidence L1" in response.text
    assert 'data-start="100.0"' in response.text


def test_authenticated_program_media_endpoint(client):
    assert client.get("/bridge/highlights/ep-001/media").status_code == 401
    response = client.get("/bridge/highlights/ep-001/media", cookies=_auth_cookie())
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"


def test_program_media_serves_verified_editorial_master_not_raw(client, episode_root, monkeypatch):
    media, _ = _install_verified_master(monkeypatch, episode_root)

    response = client.get("/bridge/highlights/ep-001/media", cookies=_auth_cookie())

    assert response.status_code == 200
    assert response.content == media.read_bytes()
    assert response.content != (episode_root / "ep-001" / "Default_program.mp4").read_bytes()


def test_program_media_does_not_fallback_to_raw_when_master_verification_fails(client, monkeypatch):
    import thousand_sunny.routers.highlight_review as highlight_module

    def fail_open(_request):
        raise highlight_module.EditorialMasterContractError("tampered master")

    monkeypatch.setattr(highlight_module.EditorialMasterRequest, "open", fail_open)

    response = client.get("/bridge/highlights/ep-001/media", cookies=_auth_cookie())

    assert response.status_code == 422
    assert "tampered master" in response.text


@pytest.mark.parametrize(
    "url",
    [
        "/bridge/highlights/ep-001",
        "/bridge/highlights/ep-001/media",
    ],
)
def test_page_and_media_reject_stale_candidate_master_lineage(client, episode_root, url):
    candidates_path = episode_root / "ep-001" / "highlights" / "candidates.json"
    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    payload["editorial_master_lineage"]["content_hash"] = "f" * 64
    candidates_path.write_text(json.dumps(payload), encoding="utf-8")
    _rebind_review_inputs(candidates_path)

    response = client.get(url, cookies=_auth_cookie())

    assert response.status_code == 422
    assert "lineage" in response.text


def test_decide_rejects_stale_master_lineage_before_writing(client, episode_root):
    candidates_path = episode_root / "ep-001" / "highlights" / "candidates.json"
    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    payload["editorial_master_lineage"]["content_hash"] = "f" * 64
    candidates_path.write_text(json.dumps(payload), encoding="utf-8")
    _rebind_review_inputs(candidates_path)

    response = client.post(
        "/bridge/highlights/ep-001/decide",
        data={"candidate_id": ["L1", "L2", "L4"]},
        cookies=_auth_cookie(),
        follow_redirects=False,
    )

    assert response.status_code == 422
    highlights = episode_root / "ep-001" / "highlights"
    assert not (highlights / "winners.json").exists()
    assert not (highlights / "review_feedback.json").exists()


def test_decide_rechecks_master_immediately_before_durable_write(client, episode_root, monkeypatch):
    import thousand_sunny.routers.highlight_review as highlight_module

    candidates_path = episode_root / "ep-001" / "highlights" / "candidates.json"
    lineage = json.loads(candidates_path.read_text(encoding="utf-8"))["editorial_master_lineage"]
    calls = 0

    def changing_master(_request):
        nonlocal calls
        calls += 1
        current = lineage if calls == 1 else {**lineage, "content_hash": "f" * 64}
        return SimpleNamespace(
            media_path=episode_root / "ep-001" / "editorial-master" / "v1" / "master.mp4",
            identity=lambda: current,
        )

    monkeypatch.setattr(highlight_module.EditorialMasterRequest, "open", changing_master)

    response = client.post(
        "/bridge/highlights/ep-001/decide",
        data={"candidate_id": ["L1", "L2", "L4"]},
        cookies=_auth_cookie(),
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert calls == 2
    assert not (episode_root / "ep-001" / "highlights" / "winners.json").exists()


def test_program_media_supports_range_requests_for_seeking(client):
    response = client.get(
        "/bridge/highlights/ep-001/media",
        headers={"Range": "bytes=2-5"},
        cookies=_auth_cookie(),
    )
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 2-5/12"
    assert response.content == b"ster"


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
        "/bridge/highlights/ep-001/decide",
        data=data,
        cookies=_auth_cookie(),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/bridge/highlights/ep-001?saved=1"
    highlights = episode_root / "ep-001" / "highlights"
    winners = json.loads((highlights / "winners.json").read_text(encoding="utf-8"))
    assert [winner["id"] for winner in winners["winners"]] == ["L1", "L2", "L3"]
    assert [winner["rank"] for winner in winners["winners"]] == [1, 2, 3]
    assert winners["picked_by"] == "修修 (Bridge highlight review gate)"
    work_plan = json.loads((highlights / "packaging-plan.json").read_text(encoding="utf-8"))
    assert work_plan["schema"] == "nakama.highlight_parallel_work_plan.v1"
    assert [row["cut_id"] for row in work_plan["cuts"]] == ["L1", "L2", "L3"]
    assert [row["rank"] for row in work_plan["cuts"]] == [1, 2, 3]
    assert {row["video"]["status"] for row in work_plan["cuts"]} == {"queued"}
    assert {row["packaging"]["status"] for row in work_plan["cuts"]} == {"queued"}
    audit = json.loads((highlights / "review_feedback.json").read_text(encoding="utf-8"))
    assert audit["decisions"][0]["feedback"]["L1"] == "成片開頭最有力"
    assert audit["decisions"][0]["override_veto_ids"] == ["L3"]
    response = client.post(
        "/bridge/highlights/ep-001/decide",
        data=data,
        cookies=_auth_cookie(),
        follow_redirects=False,
    )
    assert response.status_code == 303
    audit = json.loads((highlights / "review_feedback.json").read_text(encoding="utf-8"))
    assert len(audit["decisions"]) == 2


def test_shortlist_decision_mirrors_parallel_jobs_to_resolved_packaging_manifest(
    client, episode_root
):
    packaging_dir = episode_root / "vault" / "Attachments" / "packaging" / "ep-001"
    packaging_dir.mkdir(parents=True)
    (packaging_dir / "packages.json").write_text(
        json.dumps(_full_episode_packages("ep-001"), ensure_ascii=False), encoding="utf-8"
    )
    (packaging_dir / "manifest.json").write_text(
        json.dumps({"cuts": {"full": {"emitted": "2026-08-27T01:00:00+00:00"}}}),
        encoding="utf-8",
    )

    response = client.post(
        "/bridge/highlights/ep-001/decide",
        data={"candidate_id": ["L1", "L2", "L4"]},
        cookies=_auth_cookie(),
        follow_redirects=False,
    )

    assert response.status_code == 303
    manifest = json.loads((packaging_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "emitted" in manifest["cuts"]["full"]
    assert [manifest["cuts"][cut_id]["rank"] for cut_id in ("L1", "L2", "L4")] == [
        1,
        2,
        3,
    ]
    assert manifest["cuts"]["L1"]["video"]["status"] == "queued"
    assert manifest["cuts"]["L1"]["packaging"]["status"] == "queued"


def test_parallel_plan_marks_existing_video_export_ready_and_other_cuts_queued(
    client, episode_root
):
    exports = episode_root / "ep-001" / "highlights" / "exports"
    exports.mkdir()
    (exports / "L1.mp4").write_bytes(b"finished-long-highlight")

    response = client.post(
        "/bridge/highlights/ep-001/decide",
        data={"candidate_id": ["L1", "L2", "L4"]},
        cookies=_auth_cookie(),
        follow_redirects=False,
    )

    assert response.status_code == 303
    plan = json.loads(
        (episode_root / "ep-001" / "highlights" / "packaging-plan.json").read_text(encoding="utf-8")
    )
    by_cut = {row["cut_id"]: row for row in plan["cuts"]}
    assert by_cut["L1"]["video"]["status"] == "ready"
    assert by_cut["L2"]["video"]["status"] == "queued"
    assert by_cut["L4"]["video"]["status"] == "queued"
    assert {row["packaging"]["status"] for row in plan["cuts"]} == {"queued"}


def test_shortlist_decision_fails_closed_on_broken_packaging_manifest(client, episode_root):
    packaging_dir = episode_root / "vault" / "Attachments" / "packaging" / "ep-001"
    packaging_dir.mkdir(parents=True)
    (packaging_dir / "packages.json").write_text(
        json.dumps(_full_episode_packages("ep-001"), ensure_ascii=False), encoding="utf-8"
    )
    (packaging_dir / "manifest.json").write_text("{broken", encoding="utf-8")

    response = client.post(
        "/bridge/highlights/ep-001/decide",
        data={"candidate_id": ["L1", "L2", "L4"]},
        cookies=_auth_cookie(),
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert "manifest.json" in response.text
    assert not (episode_root / "ep-001" / "highlights" / "winners.json").exists()


def test_explicit_selection_order_becomes_winner_rank(client, episode_root):
    response = client.post(
        "/bridge/highlights/ep-001/decide",
        data={
            "candidate_id": ["L1", "L2", "L4"],
            "selection_order": "L4,L1,L2",
        },
        cookies=_auth_cookie(),
        follow_redirects=False,
    )
    assert response.status_code == 303
    winners = json.loads(
        (episode_root / "ep-001" / "highlights" / "winners.json").read_text(encoding="utf-8")
    )
    assert [winner["id"] for winner in winners["winners"]] == ["L4", "L1", "L2"]


def test_selection_order_must_match_checked_candidates(client):
    response = client.post(
        "/bridge/highlights/ep-001/decide",
        data={
            "candidate_id": ["L1", "L2", "L4"],
            "selection_order": "L4,L1,L5",
        },
        cookies=_auth_cookie(),
    )
    assert response.status_code == 400
    assert "selection order" in response.text


def test_path_traversal_rejected(client):
    response = client.get("/bridge/highlights/..", cookies=_auth_cookie(), follow_redirects=False)
    assert response.status_code in (404, 405)


def test_selection_page_links_to_finished_review_when_a_review_packet_is_ready(
    client, episode_root
):
    """入口只需要認得出「有東西可以審」——短片 packet 與長片 v3 Release 都算。

    舊的 v1 manifest 檔已由 v3 current Release 取代（ADR-066），所以這裡用短片
    packet 當就緒訊號：純短片的一集也必須看得到入口。
    """
    review = episode_root / "ep-001" / "highlights" / "review"
    cut_dir = review / "KS1"
    cut_dir.mkdir(parents=True)
    preview = cut_dir / "KS1_preview.mp4"
    preview.write_bytes(b"review-preview")
    (cut_dir / "subs.srt").write_text(
        "1" + chr(10) + "00:00:00,000 --> 00:00:01,000" + chr(10) + "測試" + chr(10),
        encoding="utf-8",
    )
    (cut_dir / "events.json").write_text(
        json.dumps(
            {
                "timeline": "短1 - 測試（緊·導播）",
                "duration_sec": 12.0,
                "preview": preview.name,
                "events": [
                    {"type": "card-tier2", "slug": "測試", "t0": 0.0, "t1": 3.0, "note": ""}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = client.get("/bridge/highlights/ep-001", cookies=_auth_cookie())

    assert response.status_code == 200
    assert 'href="/bridge/highlights/ep-001/finished"' in response.text
    assert "進入初剪 Review" in response.text


def test_review_selection_uses_background_without_accent_left_rail():
    """選中的 cut tab 用整塊底色表示，不是左側一條 accent 色條。"""
    template = (
        Path(__file__).resolve().parent.parent
        / "thousand_sunny/templates/bridge/finished_review.html"
    ).read_text(encoding="utf-8")

    selected_rule = next(
        line for line in template.splitlines() if 'cut-tab[aria-selected="true"]' in line
    )
    assert "var(--sho-accent-soft)" in selected_rule
    cut_tab_rules = [line for line in template.splitlines() if ".cut-tab" in line]
    assert cut_tab_rules
    assert not any("border-left" in line for line in cut_tab_rules)


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
    audit = json.loads(
        (episode / "highlights" / "review_feedback.json").read_text(encoding="utf-8")
    )
    assert audit["decisions"][0]["feedback"]["L5"] == "論點與 L2 重疊，這次不選"
