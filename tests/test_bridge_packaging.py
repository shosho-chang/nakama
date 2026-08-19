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
from pathlib import Path

import pytest
from fastapi import FastAPI
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
    monkeypatch.setattr(pkg_module, "_ensure_publish_prep", lambda episode, cut_id: None)
    importlib.reload(app_module)
    return TestClient(app_module.app)


@pytest.fixture
def router_client(monkeypatch, vault):
    """Isolated router app for the packaging-to-publish handoff."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("VAULT_PATH", str(vault))
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.packaging as pkg_module

    importlib.reload(auth_module)
    importlib.reload(pkg_module)
    monkeypatch.setattr(pkg_module, "_ensure_publish_prep", lambda episode, cut_id: None)
    app = FastAPI()
    app.include_router(pkg_module.page_router)
    return TestClient(app)


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


# ---------------------------------------------------------------------------
# 封面變體勾選（修修 2026-08-14：臉與封面大字都要能挑）
# ---------------------------------------------------------------------------


def _variant(vid: str, n: int) -> dict:
    return {
        "variant_id": vid,
        "thumbnail_png": f"Attachments/packaging/20260723-xieboran/var-{vid}.png",
        "host_cutout": "Attachments/cutouts/shosho/surprised/1.png",
        "guest_cutout": "Attachments/cutouts/podcast/20260723-xieboran/guest_v1_thoughtful.png",
        "big_text": ["沒有資源", "怎麼活下來"],
        "highlight_text": "活下來",
    }


@pytest.fixture
def vault_with_variants(vault):
    path = vault / "Attachments" / "packaging" / "20260723-xieboran" / "packages.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cuts"][0]["packages"][0]["variants"] = [_variant("r1-a", 1), _variant("r1-b", 1)]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return vault


def test_variant_select_writes_approval_without_approving(client, vault_with_variants):
    r = client.post(
        "/bridge/packaging/20260723-xieboran/variant",
        data={"cut_id": "punch-L1", "selected_variant": "r1-b"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    saved = json.loads(
        (
            vault_with_variants
            / "Attachments"
            / "packaging"
            / "20260723-xieboran"
            / "approval.json"
        ).read_text(encoding="utf-8")
    )
    entry = saved["approvals"][0]
    assert entry["selected_variant"] == "r1-b"
    assert entry["approved"] is False  # 挑臉不等於拍板


def test_variant_select_keeps_existing_approval(client, vault_with_variants):
    client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "2"},
        follow_redirects=False,
    )
    client.post(
        "/bridge/packaging/20260723-xieboran/variant",
        data={"cut_id": "punch-L1", "selected_variant": "r1-a"},
        follow_redirects=False,
    )
    saved = json.loads(
        (
            vault_with_variants
            / "Attachments"
            / "packaging"
            / "20260723-xieboran"
            / "approval.json"
        ).read_text(encoding="utf-8")
    )
    entry = saved["approvals"][0]
    assert entry["approved"] is True and entry["primary_package"] == 2
    assert entry["selected_variant"] == "r1-a"


def test_variant_unknown_id_404(client, vault_with_variants):
    r = client.post(
        "/bridge/packaging/20260723-xieboran/variant",
        data={"cut_id": "punch-L1", "selected_variant": "nope"},
        follow_redirects=False,
    )
    assert r.status_code == 404


def test_bigtext_request_saved_and_rendered_back(client, vault_with_variants):
    client.post(
        "/bridge/packaging/20260723-xieboran/variant",
        data={"cut_id": "punch-L1", "bigtext_request": "沒有資源／怎麼[活下來]"},
        follow_redirects=False,
    )
    board = client.get("/bridge/packaging/20260723-xieboran")
    assert "沒有資源／怎麼[活下來]" in board.text


def test_board_shows_variant_thumbnails(client, vault_with_variants):
    board = client.get("/bridge/packaging/20260723-xieboran")
    assert "var-r1-a.png" in board.text and "var-r1-b.png" in board.text


def test_approve_does_not_wipe_selected_variant(client, vault_with_variants):
    """2026-08-14 browser UAT：勾完變體再 approve，選擇整個不見。"""
    client.post(
        "/bridge/packaging/20260723-xieboran/variant",
        data={"cut_id": "punch-L1", "selected_variant": "r1-b", "bigtext_request": "大字／[重出]"},
        follow_redirects=False,
    )
    client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )
    saved = json.loads(
        (
            vault_with_variants
            / "Attachments"
            / "packaging"
            / "20260723-xieboran"
            / "approval.json"
        ).read_text(encoding="utf-8")
    )
    entry = saved["approvals"][0]
    assert entry["approved"] is True
    assert entry["selected_variant"] == "r1-b"
    assert entry["bigtext_request"] == "大字／[重出]"


def test_variant_pick_alone_is_not_a_rejection(client, vault_with_variants):
    """2026-08-14 browser UAT：只挑變體時 board 顯示 REJECTED，會誤導。"""
    client.post(
        "/bridge/packaging/20260723-xieboran/variant",
        data={"cut_id": "punch-L1", "selected_variant": "r1-a"},
        follow_redirects=False,
    )
    board = client.get("/bridge/packaging/20260723-xieboran")
    assert "PENDING" in board.text
    assert "REJECTED" not in board.text
    # 真的按 Reject 才是 REJECTED
    client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "reject", "reject_note": "臉不對"},
        follow_redirects=False,
    )
    assert "REJECTED" in client.get("/bridge/packaging/20260723-xieboran").text


def test_legacy_approval_without_decision_still_shows_rejected(client, vault):
    """舊檔沒有 decision 欄位 → 用 approved 回退判讀，既有集數顯示不變。"""
    ep = vault / "Attachments" / "packaging" / "20260723-xieboran"
    (ep / "approval.json").write_text(
        json.dumps(
            {
                "episode": "20260723 謝伯讓",
                "approvals": [
                    {
                        "cut_id": "punch-L1",
                        "approved": False,
                        "primary_package": 1,
                        "reject_note": "舊檔",
                        "decided_at": "2026-07-30T00:00:00+00:00",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert "REJECTED" in client.get("/bridge/packaging/20260723-xieboran").text


# ---------------------------------------------------------------------------
# 組配方 → 桌機 render 一次（修修 2026-08-14：先選定再出圖）
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_with_cutouts(vault):
    d = vault / "Attachments" / "cutouts" / "podcast" / "20260723-xieboran"
    d.mkdir(parents=True)
    for name in ("host_v1_serious.png", "host_v2_laughing.png", "guest_v1_serious.png"):
        (d / name).write_bytes(bytes.fromhex("89504e470d0a1a0a"))
    (d / "cutouts_manifest.json").write_text(
        json.dumps(
            {
                "validated": {
                    n: {}
                    for n in ("host_v1_serious.png", "host_v2_laughing.png", "guest_v1_serious.png")
                }
            }
        ),
        encoding="utf-8",
    )
    return vault


def _compose(client, **over):
    data = {
        "cut_id": "punch-L1",
        "title_rank": "2",
        "host_cutout": "Attachments/cutouts/podcast/20260723-xieboran/host_v2_laughing.png",
        "guest_cutout": "Attachments/cutouts/podcast/20260723-xieboran/guest_v1_serious.png",
        "big_text_1": "沒有資源",
        "big_text_2": "怎麼活下來",
        "highlight_text": "活下來",
    }
    data.update(over)
    return client.post(
        "/bridge/packaging/20260723-xieboran/compose", data=data, follow_redirects=False
    )


def test_compose_writes_render_request(client, vault_with_cutouts):
    assert _compose(client).status_code == 303
    saved = json.loads(
        (
            vault_with_cutouts / "Attachments" / "packaging" / "20260723-xieboran" / "approval.json"
        ).read_text(encoding="utf-8")
    )
    req = saved["approvals"][0]["render_request"]
    assert req["title_rank"] == 2
    assert req["big_text"] == ["沒有資源", "怎麼活下來"]
    assert req["highlight_text"] == "活下來"
    assert req["host_cutout"].endswith("host_v2_laughing.png")
    assert req["rendered_png"] is None  # 還沒出圖


def test_compose_accepts_three_lines(client, vault_with_cutouts):
    """三行大字（修修 2026-08-15「第一支片／別求成功／別求爆紅」）。

    schema 本來就允許 1–3 行，表單卻只開兩格——第三段只能被丟掉或硬塞進同一行
    （九字一行會讓整塊字級從 100px 縮到 64px）。補上第三格讓它進得來也回得去。
    """
    r = _compose(
        client,
        big_text_1="第一支片",
        big_text_2="別求成功",
        big_text_3="別求爆紅",
        highlight_text="別求爆紅",
    )
    assert r.status_code == 303
    saved = json.loads(
        (
            vault_with_cutouts / "Attachments" / "packaging" / "20260723-xieboran" / "approval.json"
        ).read_text(encoding="utf-8")
    )
    req = saved["approvals"][0]["render_request"]
    assert req["big_text"] == ["第一支片", "別求成功", "別求爆紅"]
    # 表單要能把三行讀回格子裡，否則下次按存配方就掉一行
    board = client.get("/bridge/packaging/20260723-xieboran")
    assert 'name="big_text_3"' in board.text
    assert board.text.count("別求爆紅") >= 2  # 第三格 + 橘框詞


def test_saved_recipe_is_pending_not_rejected(client, vault_with_cutouts):
    """存配方 ≠ 退件（2026-08-15 browser UAT）。

    舊檔回退判讀原本只看 selected_variant / bigtext_request，剛存好配方的新集數
    三欄皆空 → 被判成 REJECTED，修修會以為自己退過件。
    """
    _compose(client)
    board = client.get("/bridge/packaging/20260723-xieboran")
    assert "PENDING" in board.text
    assert "REJECTED" not in board.text


_GEO = {
    "host_height_pct": "140.0",
    "host_x_pct": "-26.6",
    "host_y_pct": "-34.5",
    "guest_height_pct": "113.8",
    "guest_x_pct": "-25.4",
    "guest_y_pct": "-1.3",
}


def _saved_req(vault):
    saved = json.loads(
        (vault / "Attachments" / "packaging" / "20260723-xieboran" / "approval.json").read_text(
            encoding="utf-8"
        )
    )
    return saved["approvals"][0]["render_request"]


def test_compose_saves_manual_geometry(client, vault_with_cutouts):
    """修修在預覽上拖完的位置要原封不動進 render_request（2026-08-15）。"""
    assert _compose(client, geometry_mode="manual", **_GEO).status_code == 303
    req = _saved_req(vault_with_cutouts)
    assert req["geometry_manual"] is True
    assert req["geometry"]["host_height_pct"] == 140.0
    assert req["geometry"]["guest_y_pct"] == -1.3


def test_compose_auto_keeps_geometry_but_unlocks_it(client, vault_with_cutouts):
    """不勾「用我調的位置」= 交還 solver，但數字留著當下次拖曳的起點。

    solver 每次 render 完都會把解出來的位置寫回 geometry；要是沒有 geometry_manual
    這個旗標，第一次寫回就等於把自己鎖死——之後換一張臉也不會重新解算。
    """
    _compose(client, geometry_mode="manual", **_GEO)
    _compose(client)  # 第二次不帶 geometry_mode → auto
    req = _saved_req(vault_with_cutouts)
    assert req["geometry_manual"] is False
    assert req["geometry"]["host_height_pct"] == 140.0  # 起點還在


def test_compose_saves_title_max_width(client, vault_with_cutouts):
    """大字寬度＝字級旋鈕（修修 2026-08-15：「封面抬頭的大小可以讓我調整嗎」）。

    composition 整塊縮字：fontSize = 100 * title_max_width / 行寬。他的 7 字大字
    在 580 下被縮到 82px，兩端跑到臉底下；調寬就是調字級。
    """
    assert _compose(client, title_max_width="720").status_code == 303
    assert _saved_req(vault_with_cutouts)["title_max_width"] == 720
    board = client.get("/bridge/packaging/20260723-xieboran")
    assert 'name="title_max_width"' in board.text
    assert 'value="720"' in board.text


def test_compose_saves_guest_credit(client, vault_with_cutouts):
    """來賓抬頭進配方（2026-08-15 回歸）。

    抬頭以前只活在桌機端的 spec 檔，render 端靠 glob 上一份 spec 撈。中間產物一搬
    進 _work/ 就撈不到 → 空字串 → composition 的 `#credit:empty{display:none}`
    把整行收掉，封面上的抬頭直接消失。收進配方就不靠檔案系統的巧合了。
    """
    assert _compose(client, guest_credit="泛科學知識長 鄭國威").status_code == 303
    assert _saved_req(vault_with_cutouts)["guest_credit"] == "泛科學知識長 鄭國威"
    board = client.get("/bridge/packaging/20260723-xieboran")
    assert 'name="guest_credit"' in board.text
    assert "泛科學知識長 鄭國威" in board.text


def test_compose_defaults_title_max_width(client, vault_with_cutouts):
    _compose(client)
    assert _saved_req(vault_with_cutouts)["title_max_width"] == 580


def test_compose_rejects_absurd_title_max_width(client, vault_with_cutouts):
    assert _compose(client, title_max_width="4000").status_code == 422


def test_geometry_inputs_use_step_any(client, vault_with_cutouts):
    """step 必須是 any（2026-08-15 browser UAT）。

    Chrome 的 step 基準點是初始 value，不是 0——step="0.1" 配上兩位小數的種子值
    會讓合法值變成 -21.69/-21.59/…，拖曳出來的數字幾乎都落在格子外，按存配方
    就跳「請輸入有效值」。修修回報的「數字不符合」就是這個。
    """
    _compose(client, geometry_mode="manual", **_GEO)
    board = client.get("/bridge/packaging/20260723-xieboran")
    assert 'step="any" data-geo=' in board.text
    assert 'step="0.1" data-geo=' not in board.text


def test_compose_rejects_out_of_range_geometry(client, vault_with_cutouts):
    r = _compose(client, geometry_mode="manual", **{**_GEO, "host_height_pct": "0"})
    assert r.status_code == 400
    assert "超出範圍" in r.text


def test_board_renders_layout_stage(client, vault_with_cutouts):
    """排版舞台要真的畫得出來：素材路徑、六個數字欄、手動勾選框。"""
    _compose(client, geometry_mode="manual", **_GEO)
    board = client.get("/bridge/packaging/20260723-xieboran")
    assert "/bridge/thumbnail/still-asset/bg" in board.text
    assert 'data-geo="host_height"' in board.text
    assert 'data-geo="guest_y"' in board.text
    assert 'name="geometry_mode"' in board.text
    assert "140.0" in board.text  # 欄位帶著存過的值回來


def test_compose_rejects_highlight_not_in_big_text(client, vault_with_cutouts):
    r = _compose(client, highlight_text="不存在")
    assert r.status_code == 400
    assert "不會有框" in r.text


def test_compose_rejects_unknown_cutout(client, vault_with_cutouts):
    r = _compose(client, host_cutout="Attachments/cutouts/podcast/20260723-xieboran/nope.png")
    assert r.status_code == 404


def test_compose_rejects_empty_big_text(client, vault_with_cutouts):
    r = _compose(client, big_text_1="", big_text_2="", highlight_text="")
    assert r.status_code == 400


def test_compose_keeps_approval_state(client, vault_with_cutouts):
    client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "3"},
        follow_redirects=False,
    )
    _compose(client)
    saved = json.loads(
        (
            vault_with_cutouts / "Attachments" / "packaging" / "20260723-xieboran" / "approval.json"
        ).read_text(encoding="utf-8")
    )
    entry = saved["approvals"][0]
    assert entry["approved"] is True and entry["primary_package"] == 3
    assert entry["render_request"]["title_rank"] == 2


def test_board_lists_cutout_choices(client, vault_with_cutouts):
    board = client.get("/bridge/packaging/20260723-xieboran")
    assert "host_v2_laughing.png" in board.text
    assert "guest_v1_serious.png" in board.text
    assert "存配方" in board.text


def test_title_edit_records_original_when_key_exists_as_null(client, vault):
    """2026-08-14 UAT：packages.json 帶 original_text: null 時，setdefault 不會寫入。"""
    path = vault / "Attachments" / "packaging" / "20260723-xieboran" / "packages.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for tt in data["cuts"][0]["titles"]:
        tt["original_text"] = None
        tt["edited_at"] = None
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    client.post(
        "/bridge/packaging/20260723-xieboran/title",
        data={"cut_id": "punch-L1", "rank": "2", "title_text": "改過的標題"},
        follow_redirects=False,
    )
    saved = json.loads(path.read_text(encoding="utf-8"))
    target = next(t for t in saved["cuts"][0]["titles"] if t["rank"] == 2)
    assert target["text"] == "改過的標題"
    assert target["original_text"] == "標題 rank 2"
    assert target["edited_at"] is not None


def test_focused_board_only_shows_selected_cut(router_client):
    response = router_client.get(
        "/bridge/packaging/20260723-xieboran?cut=punch-L1"
    )

    assert response.status_code == 200
    assert "punch-L1" in response.text
    assert "punch-S1" not in response.text


def test_packaging_approval_hands_selected_title_and_thumbnail_to_publish(
    router_client, monkeypatch
):
    import thousand_sunny.routers.packaging as pkg_module

    updates: list[tuple[int, dict]] = []
    monkeypatch.setattr(
        pkg_module,
        "get_release",
        lambda episode, cut_id: {
            "episode": episode,
            "cut_id": cut_id,
            "targets": [{"id": 42, "platform": "youtube", "status": "draft"}],
        },
    )
    monkeypatch.setattr(
        pkg_module,
        "update_target",
        lambda target_id, **fields: updates.append((target_id, fields)),
    )

    response = router_client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "2"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/bridge/publish/20260723%20%E8%AC%9D%E4%BC%AF%E8%AE%93/punch-L1"
    )
    assert updates == [
        (
            42,
            {
                "title": "標題 rank 2",
                "thumbnail_path": (
                    "Attachments/packaging/20260723-xieboran/pkg-punch-L1-2.png"
                ),
            },
        )
    ]


def test_packaging_approval_waits_for_full_resolution_release(router_client, monkeypatch):
    import thousand_sunny.routers.packaging as pkg_module

    monkeypatch.setattr(pkg_module, "get_release", lambda episode, cut_id: None)
    starts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        pkg_module,
        "_ensure_publish_prep",
        lambda episode, cut_id: starts.append((episode, cut_id)),
        raising=False,
    )
    response = router_client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/bridge/packaging/20260723-xieboran?cut=punch-L1&release_pending=1"
    )
    assert starts == [("20260723 謝伯讓", "punch-L1")]


def test_pending_board_polls_without_full_page_reload(router_client, monkeypatch):
    import thousand_sunny.routers.packaging as pkg_module

    monkeypatch.setattr(pkg_module, "get_release", lambda episode, cut_id: None)
    response = router_client.get(
        "/bridge/packaging/20260723-xieboran?cut=punch-L1&release_pending=1"
    )

    assert response.status_code == 200
    assert "window.location.reload()" not in response.text
    assert "fetch(window.location.href" in response.text


def test_pending_board_applies_packaging_after_render_finishes(
    router_client, vault, monkeypatch
):
    import thousand_sunny.routers.packaging as pkg_module

    approval = {
        "episode": "20260723 謝伯讓",
        "approvals": [
            {
                "cut_id": "punch-L1",
                "approved": True,
                "primary_package": 3,
                "reject_note": None,
                "decided_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
    }
    path = vault / "Attachments" / "packaging" / "20260723-xieboran" / "approval.json"
    path.write_text(json.dumps(approval, ensure_ascii=False), encoding="utf-8")
    updates: list[tuple[int, dict]] = []
    monkeypatch.setattr(
        pkg_module,
        "get_release",
        lambda episode, cut_id: {
            "targets": [{"id": 88, "platform": "youtube", "status": "draft"}]
        },
    )
    monkeypatch.setattr(
        pkg_module,
        "update_target",
        lambda target_id, **fields: updates.append((target_id, fields)),
    )

    response = router_client.get(
        "/bridge/packaging/20260723-xieboran?cut=punch-L1&release_pending=1",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/bridge/publish/20260723%20%E8%AC%9D%E4%BC%AF%E8%AE%93/punch-L1"
    )
    assert updates == [
        (
            88,
            {
                "title": "標題 rank 3",
                "thumbnail_path": (
                    "Attachments/packaging/20260723-xieboran/pkg-punch-L1-3.png"
                ),
            },
        )
    ]


def test_render_receipt_is_registered_by_web_runtime(monkeypatch, tmp_path):
    import thousand_sunny.routers.packaging as pkg_module

    episodes = tmp_path / "episodes"
    exports = episodes / "20260721 鄭國威" / "highlights" / "exports"
    exports.mkdir(parents=True)
    video = exports / "R11.mp4"
    video.write_bytes(b"full-resolution-master")
    receipt = exports / ".publish_prep_R11.json"
    receipt.write_text(
        json.dumps(
            {
                "status": "rendered",
                "episode": "20260721 鄭國威",
                "cuts": [
                    {
                        "cut_id": "R11",
                        "format": "long",
                        "work_title": "職人精神",
                        "file": str(video),
                        "file_bytes": video.stat().st_size,
                        "duration_sec": 421.4,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state: dict[str, object] = {"release": None, "registered": None}
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(episodes))
    monkeypatch.setattr(pkg_module, "get_release", lambda episode, cut_id: state["release"])

    def register(*args, **kwargs):
        state["registered"] = (args, kwargs)
        return 7

    def ensure(release_id, platform):
        state["release"] = {
            "id": release_id,
            "episode": "20260721 鄭國威",
            "cut_id": "R11",
            "targets": [{"id": 9, "platform": platform, "status": "draft"}],
        }
        return 9

    monkeypatch.setattr(pkg_module, "register_release", register)
    monkeypatch.setattr(pkg_module, "ensure_target", ensure)

    release = pkg_module._release_from_receipt("20260721 鄭國威", "R11")

    assert release == state["release"]
    args, kwargs = state["registered"]
    assert args[:3] == ("20260721 鄭國威", "R11", "long")
    assert Path(args[3]) == video
    assert kwargs["file_bytes"] == video.stat().st_size
