# ruff: noqa: E501  — fixture 標題與錯誤訊息含 CJK 長行。
"""Packaging gate router tests（ADR-054 D10/D11，issue #1071）。

Coverage:
- 列表：空 vault 空清單、正常集數統計、sync-conflict fail loud（列 error 不吞）
- board：正常渲染（package 卡 / 落選 rank4-5 / brand_flags）、conflict 409、壞 JSON 422
- approve：寫 approval.json（ApprovalFileV1 upsert）、reject 帶 note、重整後狀態正確
- title 改字（長短片皆可，修修 2026-07-30）：落 packages.json 且整檔重驗、
  長片記 original_text/edited_at、重複改字保留最初原句、空字串 400
- 內容速覽 brief：有就渲染、缺就顯示提示、壞檔不擋 board
- ApprovalFileV1：cut_id 唯一性
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


def _title(rank: int, panel_note: str | None = None) -> dict:
    return {
        "text": f"標題 rank {rank}",
        "archetype_id": "T-A3",
        "angle_combo": ["反直覺"],
        "payoff": "看完你會改觀",
        "cite": "srt/punch-L1_r003.srt#12",
        "rank": rank,
        "panel_note": panel_note,
    }


def _package(n: int) -> dict:
    return {
        "title_rank": n,
        "thumbnail_png": f"Attachments/packaging/20260723-xieboran/pkg-punch-L1-{n}.png",
        "thumb_archetype_id": "T-V8",
        "joint_pairing_id": "JP-1",
        "host_cutout": "Attachments/cutouts/shosho/surprised/1.png",
        "guest_cutout": "Attachments/cutouts/podcast/20260723-xieboran/guest_v1_thoughtful.png",
    }


def _packages_data() -> dict:
    return {
        "episode": "20260723 謝伯讓",
        "generated_at": "2026-07-27T12:00:00+08:00",
        "cuts": [
            {
                "cut_id": "punch-L1",
                "format": "long",
                "information_origin": "full_text",
                "visual_recipe": "podcast",
                "aspect": "16:9",
                "titles": [
                    _title(1),
                    _title(2),
                    _title(3),
                    _title(4, "角度重複，缺乏差異化"),
                    _title(5, "數字缺乏支撐"),
                ],
                "packages": [_package(1), _package(2), _package(3)],
                "citations": [],
                "brand_flags": ["宣稱療效需 hedge"],
                "title_trace_ref": "packaging/punch-L1/title_trace.json",
            },
            {
                "cut_id": "punch-S1",
                "format": "short",
                "information_origin": "full_text",
                "visual_recipe": "podcast",
                "aspect": "16:9",
                "titles": [_title(1)],
                "packages": [],
                "thumbnail": None,
            },
        ],
    }


@pytest.fixture
def vault(tmp_path):
    ep = tmp_path / "Attachments" / "packaging" / "20260723-xieboran"
    ep.mkdir(parents=True)
    (ep / "packages.json").write_text(
        json.dumps(_packages_data(), ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def client(monkeypatch, vault):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(vault))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.packaging as pkg_module

    importlib.reload(auth_module)
    importlib.reload(pkg_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------


def test_list_empty_vault(client, vault):
    import shutil

    shutil.rmtree(vault / "Attachments" / "packaging")
    r = client.get("/bridge/packaging")
    assert r.status_code == 200
    assert "目前沒有任何 packaging 產出" in r.text


def test_list_shows_episode_counts(client):
    r = client.get("/bridge/packaging")
    assert r.status_code == 200
    assert "20260723 謝伯讓" in r.text
    assert "PENDING" in r.text


def test_list_sync_conflict_fails_loud(client, vault):
    ep = vault / "Attachments" / "packaging" / "20260723-xieboran"
    (ep / "packages.sync-conflict-20260728-010101-ABCDEF.json").write_text("{}", encoding="utf-8")
    r = client.get("/bridge/packaging")
    assert r.status_code == 200
    assert "Syncthing conflict" in r.text
    # conflict 集不可點進 board（無連結）
    assert 'href="/bridge/packaging/20260723-xieboran"' not in r.text


# ---------------------------------------------------------------------------
# board
# ---------------------------------------------------------------------------


def test_board_renders_packages_runners_and_flags(client):
    r = client.get("/bridge/packaging/20260723-xieboran")
    assert r.status_code == 200
    assert "標題 rank 1" in r.text
    assert "pkg-punch-L1-1.png" in r.text
    assert "角度重複，缺乏差異化" in r.text  # rank4 panel_note
    assert "宣稱療效需 hedge" in r.text  # brand flag
    assert "短片標題" in r.text  # short cut 可改字欄


def test_board_conflict_409(client, vault):
    ep = vault / "Attachments" / "packaging" / "20260723-xieboran"
    (ep / "approval.sync-conflict-x.json").write_text("{}", encoding="utf-8")
    r = client.get("/bridge/packaging/20260723-xieboran")
    assert r.status_code == 409


def test_board_bad_json_422(client, vault):
    ep = vault / "Attachments" / "packaging" / "20260723-xieboran"
    (ep / "packages.json").write_text("{not json", encoding="utf-8")
    r = client.get("/bridge/packaging/20260723-xieboran")
    assert r.status_code == 422


def test_board_unknown_episode_404(client):
    assert client.get("/bridge/packaging/nope-000").status_code == 404


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


def test_approve_writes_approval_file_and_reload_shows_state(client, vault):
    r = client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "2"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    from shared.schemas.packaging import parse_approval_file

    ap = parse_approval_file(
        vault / "Attachments" / "packaging" / "20260723-xieboran" / "approval.json"
    )
    assert ap.episode == "20260723 謝伯讓"
    entry = next(a for a in ap.approvals if a.cut_id == "punch-L1")
    assert entry.approved is True
    assert entry.primary_package == 2

    board = client.get("/bridge/packaging/20260723-xieboran")
    assert "APPROVED · PKG 2" in board.text

    lst = client.get("/bridge/packaging")
    assert ">1<" in lst.text or "1</td>" in lst.text.replace(" ", "")


def test_reject_with_note_upserts(client, vault):
    client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )
    client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "reject", "reject_note": "三張表情太像，重抽"},
        follow_redirects=False,
    )
    from shared.schemas.packaging import parse_approval_file

    ap = parse_approval_file(
        vault / "Attachments" / "packaging" / "20260723-xieboran" / "approval.json"
    )
    assert len([a for a in ap.approvals if a.cut_id == "punch-L1"]) == 1
    entry = next(a for a in ap.approvals if a.cut_id == "punch-L1")
    assert entry.approved is False
    assert entry.reject_note == "三張表情太像，重抽"

    board = client.get("/bridge/packaging/20260723-xieboran")
    assert "REJECTED" in board.text


def test_approve_requires_primary_package(client):
    r = client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_approve_unknown_cut_404(client):
    r = client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "nope-L9", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# short-title
# ---------------------------------------------------------------------------


def _packages(vault):
    return json.loads(
        (vault / "Attachments" / "packaging" / "20260723-xieboran" / "packages.json").read_text(
            encoding="utf-8"
        )
    )


def test_short_title_edit_persists_and_revalidates(client, vault):
    r = client.post(
        "/bridge/packaging/20260723-xieboran/title",
        data={"cut_id": "punch-S1", "title_text": "手機把你的腦腐掉了"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    short = next(c for c in _packages(vault)["cuts"] if c["cut_id"] == "punch-S1")
    assert short["titles"][0]["text"] == "手機把你的腦腐掉了"
    # thumbnail: null 顯式欄位在 round-trip 後仍在（schema 不對稱驗證仍過）
    assert "thumbnail" in short and short["thumbnail"] is None


def test_long_title_edit_allowed_and_records_original(client, vault):
    """修修 2026-07-30：長片也要能在 gate 手改字。

    D11「UI 零 LLM」禁的是 LLM 生成（VPS 叫不到桌機 Cowork），不禁人工編輯；
    舊版擋長片是實作自加的限制，ADR 無此決定。
    """
    r = client.post(
        "/bridge/packaging/20260723-xieboran/title",
        data={"cut_id": "punch-L1", "title_text": "改過的長片標題", "rank": "2"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    cut = next(c for c in _packages(vault)["cuts"] if c["cut_id"] == "punch-L1")
    t2 = next(t for t in cut["titles"] if t["rank"] == 2)
    assert t2["text"] == "改過的長片標題"
    # 原句必須留著 — 否則推導鏈會謊稱手改文字是 panel 產出
    assert t2["original_text"] and t2["original_text"] != "改過的長片標題"
    assert t2["edited_at"]
    t1 = next(t for t in cut["titles"] if t["rank"] == 1)
    assert not t1.get("original_text")


def test_long_title_repeat_edit_keeps_first_original(client, vault):
    for text in ("第一次改", "第二次改"):
        client.post(
            "/bridge/packaging/20260723-xieboran/title",
            data={"cut_id": "punch-L1", "title_text": text, "rank": "3"},
            follow_redirects=False,
        )
    cut = next(c for c in _packages(vault)["cuts"] if c["cut_id"] == "punch-L1")
    t3 = next(t for t in cut["titles"] if t["rank"] == 3)
    assert t3["text"] == "第二次改"
    assert t3["original_text"] not in ("第一次改", "第二次改")


def test_title_edit_empty_text_400(client):
    r = client.post(
        "/bridge/packaging/20260723-xieboran/title",
        data={"cut_id": "punch-L1", "title_text": "   ", "rank": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# schema container
# ---------------------------------------------------------------------------


def test_approval_file_rejects_duplicate_cut_ids():
    from pydantic import ValidationError

    from shared.schemas.packaging import ApprovalFileV1, ApprovalV1

    entry = ApprovalV1(
        cut_id="punch-L1",
        approved=True,
        primary_package=1,
        reject_note=None,
        decided_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ValidationError):
        ApprovalFileV1(episode="ep", approvals=[entry, entry])


# ---------------------------------------------------------------------------
# 內容速覽（brief）
# ---------------------------------------------------------------------------


def _write_brief(vault, cut_id: str, payload: dict | str):
    d = vault / "Attachments" / "packaging" / "20260723-xieboran" / "briefs"
    d.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    (d / f"{cut_id}.json").write_text(text, encoding="utf-8")


def test_board_renders_brief_when_present(client, vault):
    """修修 2026-07-30：「我不太清楚這支影片在講什麼，所以也沒辦法判斷」。"""
    _write_brief(
        vault,
        "punch-L1",
        {
            "cut_id": "punch-L1",
            "one_liner": "談該不該把大腦外包給 AI",
            "duration": "10:16",
            "beats": [{"at": "03:40", "what": "改用健康當判準"}],
            "quotes": [{"at": "01:35", "speaker": "謝伯讓", "text": "我們直接把能力外包給AI"}],
            "caution": "02:24 那句是轉述極端派立場",
        },
    )
    body = client.get("/bridge/packaging/20260723-xieboran").text
    assert "這支在講什麼" in body
    assert "談該不該把大腦外包給 AI" in body
    assert "03:40" in body and "改用健康當判準" in body
    assert "我們直接把能力外包給AI" in body
    assert "轉述極端派立場" in body


def test_board_shows_hint_when_brief_missing(client):
    body = client.get("/bridge/packaging/20260723-xieboran").text
    assert "無內容速覽" in body


def test_corrupt_brief_does_not_block_board(client, vault):
    """速覽是輔助資訊——它壞了不該擋掉裁決（approve 表單仍要在）。"""
    _write_brief(vault, "punch-L1", "{not json")
    r = client.get("/bridge/packaging/20260723-xieboran")
    assert r.status_code == 200
    assert "brief 壞檔" in r.text
    assert "Approve" in r.text


def test_title_edit_redirect_keeps_section_open(client):
    """改完一條後 <details> 會因重載收起，要再點一次才能改下一條
    （2026-07-30 browser UAT 抓到）→ redirect 帶 ?edited=<cut_id>，template 保持展開。"""
    r = client.post(
        "/bridge/packaging/20260723-xieboran/title",
        data={"cut_id": "punch-L1", "title_text": "改個字看看", "rank": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "edited=punch-L1" in r.headers["location"]

    body = client.get("/bridge/packaging/20260723-xieboran?edited=punch-L1").text
    # 該支的改字區帶 open；其他支不帶
    assert 'id="title-edit-punch-L1"' in body
    marker = body.split('id="title-edit-punch-L1"')[1][:80]
    assert "open" in marker


# ---------------------------------------------------------------------------
# nav 入口（2026-07-30 修修：「VPS 上審封面跟 TITLE 的入口在哪裡？」）
# ---------------------------------------------------------------------------


def test_packaging_pages_mark_own_nav_active(client):
    """gate 的兩頁都要把自己標成 active，不是借用 BROOK。"""
    for path in ("/bridge/packaging", "/bridge/packaging/20260723-xieboran"):
        body = client.get(path).text
        assert 'href="/bridge/packaging"' in body, f"{path} 缺 packaging nav 連結"
        # active 標記落在 packaging 這條，而非 brook
        seg = body.split('href="/bridge/packaging"')[1][:80]
        assert 'class="active"' in seg, f"{path} 的 packaging nav 沒標 active：{seg!r}"
