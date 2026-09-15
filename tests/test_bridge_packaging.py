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

import hashlib
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.schemas.packaging import RenderRequestV1


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


def _write_composition_receipt(
    vault: Path,
    *,
    cut_id: str = "punch-L1",
    rank: int = 1,
    host_bbox: dict | None = None,
    guest_bbox: dict | None = None,
    title_bbox: dict | None = None,
    create_center_asset: bool = True,
) -> Path:
    ep = vault / "Attachments" / "packaging" / "20260723-xieboran"
    receipts = ep / "composition_receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "nakama.long_thumbnail_composition.v2",
        "episode": "20260723 謝伯讓",
        "cut_id": cut_id,
        "package_rank": rank,
        "thumbnail_png": (f"Attachments/packaging/20260723-xieboran/pkg-punch-L1-{rank}.png"),
        "canvas_width": 1280,
        "canvas_height": 720,
        "center_visual_asset": (
            f"Attachments/packaging/20260723-xieboran/center-{cut_id}-r{rank}.png"
        ),
        "protected_center_bbox": {"x": 301, "y": 132.5, "width": 678, "height": 455},
        "host_bbox": host_bbox or {"x": 0, "y": 40, "width": 380, "height": 680},
        "guest_bbox": guest_bbox or {"x": 900, "y": 40, "width": 380, "height": 680},
        "title_bbox": title_bbox,
        "max_protected_overlap_ratio": 1.0,
    }
    center_path = ep / f"center-{cut_id}-r{rank}.png"
    thumbnail_path = ep / f"pkg-punch-L1-{rank}.png"
    if not thumbnail_path.exists():
        thumbnail_path.write_bytes(b"thumbnail")
    if create_center_asset:
        center_path.write_bytes(b"center visual")
    sidecar_path = ep / f"pkg-punch-L1-{rank}.png.composition.json"
    sidecar = {
        "schema": "nakama.thumbnail_composition_measurement.v1",
        "composition": "thumbnail_reaction",
        "renderer": {"name": "hyperframes", "version": "0.6.42"},
        "png_sha256": hashlib.sha256(thumbnail_path.read_bytes()).hexdigest(),
        "assets": {
            "prop_image_data_url": {
                "sha256": hashlib.sha256(center_path.read_bytes()).hexdigest()
                if center_path.exists()
                else "0" * 64
            }
        },
        "canvas": {"width": 1280, "height": 720},
        "bboxes": {
            "protected_center_bbox": payload["protected_center_bbox"],
            "host_bbox": payload["host_bbox"],
            "guest_bbox": payload["guest_bbox"],
            "title_bbox": payload["title_bbox"],
        },
    }
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    payload.update(
        {
            "thumbnail_sha256": hashlib.sha256(thumbnail_path.read_bytes()).hexdigest(),
            "center_visual_sha256": hashlib.sha256(center_path.read_bytes()).hexdigest()
            if center_path.exists()
            else "0" * 64,
            "measurement_sidecar": (f"Attachments/packaging/20260723-xieboran/{sidecar_path.name}"),
            "measurement_sidecar_sha256": hashlib.sha256(sidecar_path.read_bytes()).hexdigest(),
            "renderer_identity": "hyperframes@0.6.42",
        }
    )
    path = receipts / f"{cut_id}-r{rank}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def vault(tmp_path):
    ep = tmp_path / "Attachments" / "packaging" / "20260723-xieboran"
    ep.mkdir(parents=True)
    (ep / "packages.json").write_text(
        json.dumps(_packages_data(), ensure_ascii=False), encoding="utf-8"
    )
    for rank in (1, 2, 3):
        _write_composition_receipt(tmp_path, rank=rank)
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


def _stub_publish_prep(monkeypatch) -> None:
    """approve 成功後會啟動桌機側 publish_prep 匯出；測試環境以 no-op 取代。"""
    import thousand_sunny.routers.packaging as pkg_module

    monkeypatch.delenv("PODCAST_EPISODES_ROOT", raising=False)
    monkeypatch.setattr(pkg_module, "_ensure_publish_prep", lambda episode, cut_id: None)


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


def test_board_shows_live_composition_verification(client):
    response = client.get("/bridge/packaging/20260723-xieboran")

    assert response.status_code == 200
    assert response.text.count("版面已驗證") == 3


def test_board_accepts_people_bleeding_past_canvas_edges(client, vault):
    _write_composition_receipt(
        vault,
        host_bbox={"x": -654, "y": -120, "width": 1237, "height": 1142},
        guest_bbox={"x": 400, "y": -145, "width": 1377, "height": 1177},
    )

    response = client.get("/bridge/packaging/20260723-xieboran")

    assert response.status_code == 200
    assert response.text.count("版面已驗證") == 3


def test_board_serves_cutouts_from_its_own_mounted_route(client, vault_with_cutouts):
    response = client.get("/bridge/packaging/20260723-xieboran/cutout/host_v1_serious.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_board_uses_packaging_cutout_route(client, vault_with_cutouts):
    response = client.get("/bridge/packaging/20260723-xieboran")

    assert response.status_code == 200
    assert "/bridge/packaging/20260723-xieboran/cutout/host_v1_serious.png" in response.text
    assert "/bridge/projects/gate/thumbnail/cutout/" not in response.text


def test_board_shows_blocked_composition_reason(client, vault):
    (
        vault
        / "Attachments"
        / "packaging"
        / "20260723-xieboran"
        / "composition_receipts"
        / "punch-L1-r1.json"
    ).unlink()

    response = client.get("/bridge/packaging/20260723-xieboran")

    assert response.status_code == 200
    assert "版面有疑慮 · 你的 Approve 仍然說了算" in response.text
    assert "composition receipt" in response.text


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

    # 短片的改字欄在它自己的 tab 上（2026-09-14 起每一集都有 tab）。改標題這件事
    # 沒有消失，只是換了位置——這條測試就是用來守住「換位置 ≠ 拿掉」。
    short_board = client.get("/bridge/packaging/20260723-xieboran?cut=punch-S1")
    assert short_board.status_code == 200
    assert "短片標題" in short_board.text


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


def test_approve_writes_approval_file_and_reload_shows_state(client, vault, monkeypatch):
    _write_composition_receipt(vault, rank=2)
    _stub_publish_prep(monkeypatch)
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


def test_approve_long_highlight_is_not_vetoed_by_a_missing_composition_receipt(client):
    """人的核准是最終判斷；composition receipt 是看板上的診斷，不是否決權。

    （2026-08-29 起：receipt 缺漏只在板上顯示 UNVERIFIED，按下 Approve 仍然成立；
    真正會擋下來的是結構性缺漏——見 test_approve_requires_primary_package。）
    """
    r = client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303


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


def test_board_says_nothing_when_the_brief_is_missing(client):
    """沒有速覽就什麼都不說。

    舊行為是印「（這支還沒有內容速覽）」——那句在說「本來想給你這支在講什麼，但
    桌機端還沒生」，對站在 gate 前面的人給不出任何能做的事（修修 2026-09-15 點名）。
    """
    body = client.get("/bridge/packaging/20260723-xieboran").text
    assert "這支還沒有內容速覽" not in body
    assert "pkg-brief-missing" not in body
    assert "Approve" in body  # 速覽缺席不影響裁決


def test_corrupt_brief_does_not_block_board(client, vault):
    """速覽是輔助資訊——它壞了不該擋掉裁決（approve 表單仍要在）。"""
    _write_brief(vault, "punch-L1", "{not json")
    r = client.get("/bridge/packaging/20260723-xieboran")
    assert r.status_code == 200
    assert "brief 壞檔" in r.text
    assert "Approve" in r.text


def test_title_edit_is_always_visible_and_distinguishes_youtube_title(client):
    """YouTube title editing must be visible beside packaging, not hidden in details."""
    r = client.post(
        "/bridge/packaging/20260723-xieboran/title",
        data={"cut_id": "punch-L1", "title_text": "改個字看看", "rank": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "edited=punch-L1" in r.headers["location"]

    body = client.get("/bridge/packaging/20260723-xieboran?edited=punch-L1").text
    assert '<section class="pkg-title-edit" id="title-edit-punch-L1">' in body
    assert "YouTube 影片標題" in body
    # 「（不會改封面大字）」用否定句防誤會，改成直接指路（修修 2026-09-15）
    assert "不會改封面大字" not in body
    assert "封面上的大字在下面〈組封面〉改" in body
    assert "Package #1" in body
    assert 'name="title_text"' in body


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


def test_variant_select_keeps_existing_approval(client, vault_with_variants, monkeypatch):
    _write_composition_receipt(vault_with_variants, rank=2)
    _stub_publish_prep(monkeypatch)
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


def test_approve_does_not_wipe_selected_variant(client, vault_with_variants, monkeypatch):
    """2026-08-14 browser UAT：勾完變體再 approve，選擇整個不見。"""
    _write_composition_receipt(vault_with_variants, rank=1)
    _stub_publish_prep(monkeypatch)
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
    """2026-08-14 browser UAT：只挑變體時 board 顯示 REJECTED，會誤導。

    2026-09-15 Reject 拿掉之後只剩兩態，這條順勢守住「沒有任何一條路會再寫出
    REJECTED」——包含那個舊誤標：沒 decision 又沒挑過東西也曾被歸進 REJECTED。
    """
    client.post(
        "/bridge/packaging/20260723-xieboran/variant",
        data={"cut_id": "punch-L1", "selected_variant": "r1-a"},
        follow_redirects=False,
    )
    board = client.get("/bridge/packaging/20260723-xieboran")
    assert "PENDING" in board.text
    assert "REJECTED" not in board.text


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


@pytest.fixture
def vault_with_all_cutouts(vault_with_cutouts):
    root = vault_with_cutouts / "Attachments" / "cutouts" / "podcast" / "20260723-xieboran"
    records = []
    for role in ("host", "guest"):
        for n in range(1, 10):
            emotion = ("serious", "explaining", "laughing")[(n - 1) % 3]
            name = f"{role}_v{n}_{emotion}.png"
            (root / name).write_bytes(bytes.fromhex("89504e470d0a1a0a"))
            records.append(
                {
                    "file": name,
                    "role": role,
                    "emotion": emotion,
                    "output_sha256": f"{n:064x}",
                }
            )
    (root / "cutouts_manifest.json").write_text(
        json.dumps(
            {
                "records": records,
                # Deliberately only v7-v9: picker must not use this map as a filter.
                "validated": {
                    f"{role}_v{n}_{('serious', 'explaining', 'laughing')[(n - 1) % 3]}.png": {}
                    for role in ("host", "guest")
                    for n in range(7, 10)
                },
            }
        ),
        encoding="utf-8",
    )
    path = vault_with_cutouts / "Attachments" / "packaging" / "20260723-xieboran" / "packages.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    package = payload["cuts"][0]["packages"][2]
    package["host_cutout"] = "Attachments/cutouts/podcast/20260723-xieboran/host_v6_laughing.png"
    package["guest_cutout"] = "Attachments/cutouts/podcast/20260723-xieboran/guest_v6_laughing.png"
    package["render_recipe"] = {
        "title_rank": 3,
        "host_cutout": package["host_cutout"],
        "guest_cutout": package["guest_cutout"],
        "big_text": ["分工是昆蟲", "人要變通才"],
        "highlight_text": "變通才",
        "title_max_width": 580,
        "guest_credit": "《逆分工》共同作者 林之晨",
        "requested_at": "2026-08-21T08:05:28+00:00",
        "geometry": {
            "host_height_pct": 112,
            "host_x_pct": -30,
            "host_y_pct": 0,
            "guest_height_pct": 112,
            "guest_x_pct": -18,
            "guest_y_pct": 0,
        },
        "geometry_manual": True,
        "book_cover": "Attachments/packaging/20260723-xieboran/book-cover.png",
        "book_cover_opacity": 0.42,
        "book_cover_brightness": 0.38,
        "book_cover_height_pct": 100,
    }
    (path.parent / "book-cover.png").write_bytes(bytes.fromhex("89504e470d0a1a0a"))
    (path.parent / "not-referenced.png").write_bytes(bytes.fromhex("89504e470d0a1a0a"))
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return vault_with_cutouts


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
    """真的超出範圍（>400）才報「超出範圍」。"""
    r = _compose(client, geometry_mode="manual", **{**_GEO, "host_height_pct": "500"})
    assert r.status_code == 400
    assert "超出範圍" in r.text


def test_compose_reports_missing_geometry_distinctly_from_out_of_range(client, vault_with_cutouts):
    """高度 0＝前端沒送這個角色的欄位，訊息不可跟「超出範圍」混為一談。

    修修 2026-09-04 卡在這裡很久：預覽用 STAGE_DEFAULT 畫得好好的，送出的卻是
    空字串（落到 Form 預設 0.0），而錯誤訊息一律說「位置/大小超出範圍」，把
    client 沒填值誤導成他自己調錯，於是他反覆檢查根本沒問題的拖曳結果。
    """
    r = _compose(client, geometry_mode="manual", **{**_GEO, "guest_height_pct": "0"})
    assert r.status_code == 400
    assert "沒有送出" in r.text
    assert "guest" in r.text
    assert "超出範圍" not in r.text


def test_board_renders_layout_stage(client, vault_with_cutouts):
    """排版舞台要真的畫得出來：素材路徑、六個數字欄、手動勾選框。"""
    _compose(client, geometry_mode="manual", **_GEO)
    board = client.get("/bridge/packaging/20260723-xieboran")
    assert "/bridge/packaging/still-asset/bg" in board.text
    assert 'data-geo="host_height"' in board.text
    assert 'data-geo="guest_y"' in board.text
    assert 'name="geometry_mode"' in board.text
    assert "140.0" in board.text  # 欄位帶著存過的值回來


def test_layout_stage_exposes_rule_of_thirds_and_explicit_layer_controls(
    client, vault_with_cutouts
):
    """排版不能靠猜透明 PNG 的 hit-area：格線與三層控制都要明確可操作。"""
    board = client.get("/bridge/packaging/20260723-xieboran")

    assert board.status_code == 200
    for line in ("v1", "v2", "h1", "h2"):
        assert f'class="st-grid-line st-grid-line--{line}"' in board.text
    for role in ("center", "host", "guest"):
        assert f'data-layer-select="{role}"' in board.text
    assert 'data-layer-scale="down"' in board.text
    assert 'data-layer-scale="up"' in board.text
    assert 'class="pkg-render-progress"' in board.text
    assert 'aria-live="polite"' in board.text
    assert 'role="progressbar"' in board.text


def test_render_status_tracks_exact_recipe_and_terminal_thumbnail(
    client, vault_with_cutouts, monkeypatch, tmp_path
):
    import thousand_sunny.routers.packaging as pkg_module

    assert _compose(client, package_rank="1").status_code == 303
    req = _saved_req(vault_with_cutouts)
    requested_at = req["requested_at"]
    state_path = tmp_path / "render-watcher-state.json"
    monkeypatch.setattr(pkg_module, "_render_watcher_state_path", lambda: state_path)
    endpoint = "/bridge/packaging/20260723-xieboran/render-status/punch-L1/1"

    queued = client.get(endpoint, params={"requested_at": requested_at})
    assert queued.status_code == 200
    assert queued.json()["status"] == "queued"

    key = "20260723-xieboran/punch-L1/r1"
    for status in ("running", "failed"):
        state_path.write_text(
            json.dumps(
                {
                    key: {
                        "requested_at": requested_at,
                        "status": status,
                        "last_error": "renderer stopped" if status == "failed" else None,
                    }
                }
            ),
            encoding="utf-8",
        )
        response = client.get(endpoint, params={"requested_at": requested_at})
        assert response.status_code == 200
        assert response.json()["status"] == status
        if status == "failed":
            # 原始 stderr 搬到 error_detail，畫面上只顯示人話。2026-09-14 修修在
            # 進度條上讀到一整段 Python traceback，以為系統壞了——實際上只是
            # 冷啟動逾時。證物要留，但不該是他讀到的第一句。
            body = response.json()
            assert body["error"] is None
            assert body["error_detail"] == "renderer stopped"
            assert body["message"] == "封面 render 失敗"

    state_path.write_text(
        json.dumps(
            {
                key: {
                    "requested_at": requested_at,
                    "status": "done",
                    "rendered_at": "2026-08-27T14:00:00+00:00",
                    "last_error": None,
                }
            }
        ),
        encoding="utf-8",
    )
    done = client.get(endpoint, params={"requested_at": requested_at})
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    assert done.json()["thumbnail_url"].startswith(
        "/bridge/packaging/20260723-xieboran/thumbnail/pkg-punch-L1-1.png?v="
    )


def test_render_status_fails_closed_for_wrong_request_or_route(
    client, vault_with_cutouts, monkeypatch, tmp_path
):
    import thousand_sunny.routers.packaging as pkg_module

    assert _compose(client, package_rank="1").status_code == 303
    monkeypatch.setattr(
        pkg_module, "_render_watcher_state_path", lambda: tmp_path / "missing-state.json"
    )
    base = "/bridge/packaging/20260723-xieboran/render-status"

    assert (
        client.get(
            f"{base}/punch-L1/1",
            params={"requested_at": "2026-01-01T00:00:00+00:00"},
        ).status_code
        == 409
    )
    assert client.get(f"{base}/wrong-cut/1", params={"requested_at": "x"}).status_code == 404
    assert client.get(f"{base}/punch-L1/3", params={"requested_at": "x"}).status_code == 409


def test_render_status_requires_bridge_auth(client, monkeypatch):
    import thousand_sunny.routers.packaging as pkg_module

    monkeypatch.setattr(pkg_module, "check_auth", lambda _: False)
    response = client.get(
        "/bridge/packaging/20260723-xieboran/render-status/punch-L1/1",
        params={"requested_at": "2026-08-27T13:17:07+00:00"},
    )
    assert response.status_code == 401


def test_board_hydrates_each_legacy_n2_package_from_its_own_receipt(client, vault_with_cutouts):
    """舊 Long package 沒 recipe 時，編輯器仍要從該 rank receipt 還原中央圖。"""
    board = client.get("/bridge/packaging/20260723-xieboran")

    assert board.status_code == 200
    assert 'class="st-center st-adjustable"' in board.text
    assert 'class="st-center-handle st-adjustable"' in board.text
    assert 'aria-label="拖曳橘框"' in board.text
    assert 'data-role="center"' in board.text
    for rank in (1, 2, 3):
        assert (
            f'"center_visual_asset": '
            f'"Attachments/packaging/20260723-xieboran/center-punch-L1-r{rank}.png"'
        ) in board.text
        assert f"/center-visual/punch-L1/{rank}" in board.text
        assert (
            client.get(f"/bridge/packaging/20260723-xieboran/center-visual/punch-L1/{rank}").content
            == b"center visual"
        )


def test_compose_saves_n2_center_asset_and_manual_geometry(client, vault_with_cutouts):
    response = _compose(
        client,
        package_rank="2",
        composition="thumbnail_reaction",
        big_text_1="",
        big_text_2="",
        highlight_text="",
        center_visual_asset=("Attachments/packaging/20260723-xieboran/center-punch-L1-r2.png"),
        center_width_pct="56.5",
        center_height_px="430",
        center_x_pct="52.0",
        center_y_pct="47.5",
        geometry_mode="manual",
        **_GEO,
    )

    assert response.status_code == 303
    path = vault_with_cutouts / "Attachments" / "packaging" / "20260723-xieboran" / "packages.json"
    packages = json.loads(path.read_text(encoding="utf-8"))["cuts"][0]["packages"]
    assert packages[0].get("render_recipe") is None
    assert packages[2].get("render_recipe") is None
    recipe = packages[1]["render_recipe"]
    assert recipe["composition"] == "thumbnail_reaction"
    assert recipe["big_text"] == []
    assert recipe["center_visual_asset"].endswith("center-punch-L1-r2.png")
    assert recipe["center_geometry"] == {
        "width_pct": 56.5,
        "height_px": 430.0,
        "x_pct": 52.0,
        "y_pct": 47.5,
    }


def test_compose_rejects_center_visual_from_another_package_rank(client, vault_with_cutouts):
    response = _compose(
        client,
        package_rank="2",
        composition="thumbnail_reaction",
        big_text_1="",
        big_text_2="",
        highlight_text="",
        center_visual_asset=("Attachments/packaging/20260723-xieboran/center-punch-L1-r1.png"),
        center_width_pct="53",
        center_height_px="455",
        center_x_pct="50",
        center_y_pct="50",
        geometry_mode="manual",
        **_GEO,
    )

    assert response.status_code == 409
    assert "package" in response.text.lower()


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


def test_compose_keeps_approval_state(client, vault_with_cutouts, monkeypatch):
    _write_composition_receipt(vault_with_cutouts, rank=3)
    _stub_publish_prep(monkeypatch)
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


def test_board_lists_all_existing_manifest_records_in_vertical_picker(
    client, vault_with_all_cutouts
):
    board = client.get("/bridge/packaging/20260723-xieboran")

    assert board.status_code == 200
    for role in ("host", "guest"):
        for n in range(1, 10):
            assert f"{role}_v{n}_" in board.text
    assert "pkg-cutout-grid" in board.text
    assert "grid-template-columns: repeat(7, minmax(0, 1fr))" in board.text
    assert "aspect-ratio: 3 / 4" in board.text
    assert "object-fit: contain" in board.text


def test_cutout_preview_urls_are_content_versioned(client, vault_with_all_cutouts):
    board = client.get("/bridge/packaging/20260723-xieboran")
    expected = hashlib.sha256(bytes.fromhex("89504e470d0a1a0a")).hexdigest()

    assert board.status_code == 200
    assert "guest_v6_laughing.png?v=" + expected in board.text
    assert 'data-preview-url="/bridge/packaging/20260723-xieboran/cutout/' in board.text
    # 檔名收進 title，畫面上只留表情——同一件事以前印兩次。
    assert 'title="guest_v6_laughing.png">laughing<' in board.text


def test_package_three_recipe_is_loaded_and_switchable(client, vault_with_all_cutouts):
    board = client.get("/bridge/packaging/20260723-xieboran")

    assert board.status_code == 200
    assert 'data-package-rank="3"' in board.text
    assert "host_v6_laughing.png" in board.text
    assert "guest_v6_laughing.png" in board.text
    assert '"host_x_pct": -30' in board.text
    assert '"guest_x_pct": -18' in board.text
    assert '"host_height_pct": 112' in board.text
    assert "loadPackageRecipe" in board.text


def test_package_rank_query_selects_that_editor(client, vault_with_all_cutouts):
    board = client.get("/bridge/packaging/20260723-xieboran?package_rank=1")

    rank_one = board.text.index('data-package-rank="1"')
    rank_three = board.text.index('data-package-rank="3"')
    assert 'aria-selected="true"' in board.text[rank_one : rank_one + 220]
    assert 'aria-selected="false"' in board.text[rank_three : rank_three + 220]


def test_stage_previews_only_episode_local_recipe_referenced_book_cover(
    client, vault_with_all_cutouts
):
    board = client.get("/bridge/packaging/20260723-xieboran?package_rank=3")
    expected = "/bridge/packaging/20260723-xieboran/recipe-asset/book-cover.png"

    assert expected in board.text
    assert "syncStageBook" in board.text
    assert client.get(expected).status_code == 200
    assert (
        client.get(
            "/bridge/packaging/20260723-xieboran/recipe-asset/not-referenced.png"
        ).status_code
        == 404
    )


def test_compose_rejects_book_cover_outside_episode(client, vault_with_all_cutouts):
    other = vault_with_all_cutouts / "Attachments" / "packaging" / "another-episode" / "book.png"
    other.parent.mkdir()
    other.write_bytes(bytes.fromhex("89504e470d0a1a0a"))

    response = _compose(
        client,
        package_rank="3",
        book_cover="Attachments/packaging/another-episode/book.png",
        host_cutout="Attachments/cutouts/podcast/20260723-xieboran/host_v6_laughing.png",
        guest_cutout="Attachments/cutouts/podcast/20260723-xieboran/guest_v6_laughing.png",
    )

    assert response.status_code == 403
    assert "episode" in response.text


def test_compose_updates_only_the_selected_package_recipe(client, vault_with_all_cutouts):
    response = _compose(
        client,
        package_rank="3",
        title_rank="3",
        host_cutout=("Attachments/cutouts/podcast/20260723-xieboran/host_v6_laughing.png"),
        guest_cutout=("Attachments/cutouts/podcast/20260723-xieboran/guest_v6_laughing.png"),
        geometry_mode="manual",
        **_GEO,
    )
    assert response.status_code == 303
    path = (
        vault_with_all_cutouts / "Attachments" / "packaging" / "20260723-xieboran" / "packages.json"
    )
    packages = json.loads(path.read_text(encoding="utf-8"))["cuts"][0]["packages"]

    assert packages[0].get("render_recipe") is None
    assert packages[1].get("render_recipe") is None
    assert packages[2]["render_recipe"]["host_cutout"].endswith("host_v6_laughing.png")
    assert packages[2]["render_recipe"]["geometry"]["host_height_pct"] == 140.0


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
    """面板只渲染被選中的那支；tab 列仍然列出全部（否則就沒有導覽了）。

    2026-09-14 短片也有了自己的 tab，所以「punch-S1 完全不出現」不再是正確的
    斷言——它會以連結形式出現在 tab 列上。真正要守的是**面板**沒有渲染它。
    """
    response = router_client.get("/bridge/packaging/20260723-xieboran?cut=punch-L1")

    assert response.status_code == 200
    assert 'aria-labelledby="cut-punch-L1"' in response.text
    assert 'aria-labelledby="cut-punch-S1"' not in response.text
    # 短片仍然點得到
    assert "?cut=punch-S1" in response.text


def _write_parallel_packaging_manifest(vault: Path, raw: str | None = None) -> Path:
    path = vault / "Attachments" / "packaging" / "20260723-xieboran" / "manifest.json"
    if raw is None:
        payload = {
            "cuts": {
                "full": {"emitted": "2026-08-27T01:00:00+00:00"},
                "value-L01": {
                    "rank": 1,
                    "title": "第一支 Long Highlight",
                    "video": {"status": "running"},
                    "packaging": {"status": "queued"},
                },
                "value-L02": {
                    "rank": 2,
                    "title": "第二支 Long Highlight",
                    "video": {"status": "queued"},
                    "packaging": {"status": "queued"},
                },
                "punch-L04": {
                    "rank": 3,
                    "title": "第三支 Long Highlight",
                    "video": {"status": "queued"},
                    "packaging": {"status": "failed"},
                },
            }
        }
        raw = json.dumps(payload, ensure_ascii=False)
    path.write_text(raw, encoding="utf-8")
    return path


def test_manifest_enables_full_and_three_long_tabs_with_pending_panels(router_client, vault):
    packages_path = vault / "Attachments" / "packaging" / "20260723-xieboran" / "packages.json"
    packages = json.loads(packages_path.read_text(encoding="utf-8"))
    packages["cuts"][0]["cut_id"] = "full"
    packages_path.write_text(json.dumps(packages, ensure_ascii=False), encoding="utf-8")
    _write_parallel_packaging_manifest(vault)

    board = router_client.get("/bridge/packaging/20260723-xieboran")

    assert board.status_code == 200
    assert 'role="tablist"' in board.text
    # Full + Long 1-3 + 這個 fixture 的那支短片 = 5。短片在 2026-09-14 之前完全
    # 沒有 tab，而 tab 一開頁面就只渲染選中的那支——等於短片整個點不到。
    assert board.text.count('class="pkg-tab" role="tab"') == 5
    assert ">Full<" in board.text
    assert ">Long 1<" in board.text
    assert ">Long 2<" in board.text
    assert ">Long 3<" in board.text
    assert 'aria-selected="true"' in board.text
    assert "?cut=punch-S1" in board.text

    pending = router_client.get("/bridge/packaging/20260723-xieboran?cut=value-L01")
    assert pending.status_code == 200
    assert "第一支 Long Highlight" in pending.text
    assert "Packaging 製作中" in pending.text
    assert "QUEUED" in pending.text


@pytest.mark.parametrize(
    "raw",
    [
        "{broken",
        '{"cuts":{"full":{},"full":{"emitted":"2026-08-27T01:00:00Z"}}}',
        '{"cuts":{"value-L01":{"rank":1},"value-L02":{"rank":1}}}',
    ],
)
def test_packaging_manifest_malformed_or_duplicate_fails_closed(router_client, vault, raw):
    _write_parallel_packaging_manifest(vault, raw)

    response = router_client.get("/bridge/packaging/20260723-xieboran")

    assert response.status_code == 422
    assert "manifest.json" in response.text


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
                "thumbnail_path": ("Attachments/packaging/20260723-xieboran/pkg-punch-L1-2.png"),
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


def test_pending_board_applies_packaging_after_render_finishes(router_client, vault, monkeypatch):
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
        lambda episode, cut_id: {"targets": [{"id": 88, "platform": "youtube", "status": "draft"}]},
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
                "thumbnail_path": ("Attachments/packaging/20260723-xieboran/pkg-punch-L1-3.png"),
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


def test_human_can_approve_long_package_without_composition_receipt(
    router_client, vault, monkeypatch
):
    """Composition evidence is advisory once a human explicitly approves."""
    import thousand_sunny.routers.packaging as pkg_module

    monkeypatch.setattr(pkg_module, "_release_from_receipt", lambda episode, cut_id: None)
    monkeypatch.setattr(pkg_module, "_ensure_publish_prep", lambda episode, cut_id: None)
    (
        vault
        / "Attachments"
        / "packaging"
        / "20260723-xieboran"
        / "composition_receipts"
        / "punch-L1-r1.json"
    ).unlink()
    response = router_client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_full_episode_does_not_require_long_highlight_composition_receipt(
    router_client, vault, monkeypatch
):
    """N1 full episodes must not be routed through the N2 reaction receipt gate."""
    import thousand_sunny.routers.packaging as pkg_module

    ep = vault / "Attachments" / "packaging" / "20260723-xieboran"
    packages_path = ep / "packages.json"
    payload = json.loads(packages_path.read_text(encoding="utf-8"))
    payload["cuts"][0]["cut_id"] = "full"
    packages_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pkg_module, "_release_from_receipt", lambda episode, cut_id: None)
    monkeypatch.setattr(pkg_module, "_ensure_publish_prep", lambda episode, cut_id: None)

    board = router_client.get("/bridge/packaging/20260723-xieboran")
    assert board.status_code == 200
    assert "N1 FULL EPISODE · COMPOSITION GATE NOT APPLICABLE" in board.text
    assert "Approve（人工決定優先）" in board.text
    assert "COMPOSITION BLOCKED：中央主圖或保護區尚未通過驗證。" not in board.text

    response = router_client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "full", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_human_can_override_occluded_center_visual_warning(router_client, vault, monkeypatch):
    import thousand_sunny.routers.packaging as pkg_module

    _write_composition_receipt(
        vault,
        host_bbox={"x": 300, "y": 40, "width": 380, "height": 680},
    )
    monkeypatch.setattr(pkg_module, "_release_from_receipt", lambda episode, cut_id: None)
    monkeypatch.setattr(pkg_module, "_ensure_publish_prep", lambda episode, cut_id: None)

    response = router_client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303


@pytest.mark.parametrize("tamper", ["legacy-v1", "thumbnail-bytes"])
def test_human_can_override_legacy_or_tampered_composition_warning(
    router_client, vault, monkeypatch, tamper
):
    import thousand_sunny.routers.packaging as pkg_module

    ep = vault / "Attachments" / "packaging" / "20260723-xieboran"
    if tamper == "legacy-v1":
        receipt = ep / "composition_receipts" / "punch-L1-r1.json"
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        payload["schema"] = "nakama.long_thumbnail_composition.v1"
        receipt.write_text(json.dumps(payload), encoding="utf-8")
    else:
        (ep / "pkg-punch-L1-1.png").write_bytes(b"tampered after receipt")
    monkeypatch.setattr(pkg_module, "_release_from_receipt", lambda episode, cut_id: None)
    monkeypatch.setattr(pkg_module, "_ensure_publish_prep", lambda episode, cut_id: None)

    response = router_client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_human_can_override_missing_center_visual_asset_warning(router_client, vault, monkeypatch):
    import thousand_sunny.routers.packaging as pkg_module

    _write_composition_receipt(vault, create_center_asset=False)
    (vault / "Attachments" / "packaging" / "20260723-xieboran" / "center-punch-L1-r1.png").unlink()
    monkeypatch.setattr(pkg_module, "_release_from_receipt", lambda episode, cut_id: None)
    monkeypatch.setattr(pkg_module, "_ensure_publish_prep", lambda episode, cut_id: None)

    response = router_client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_valid_long_composition_can_be_approved(router_client, monkeypatch):
    import thousand_sunny.routers.packaging as pkg_module

    monkeypatch.setattr(
        pkg_module,
        "get_release",
        lambda episode, cut_id: {"targets": [{"id": 42, "platform": "youtube", "status": "draft"}]},
    )
    monkeypatch.setattr(pkg_module, "update_target", lambda target_id, **fields: None)

    response = router_client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "2"},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_short_approval_does_not_require_composition_receipt(router_client, monkeypatch):
    import thousand_sunny.routers.packaging as pkg_module

    monkeypatch.setattr(
        pkg_module,
        "get_release",
        lambda episode, cut_id: {"targets": [{"id": 42, "platform": "youtube", "status": "draft"}]},
    )
    monkeypatch.setattr(pkg_module, "update_target", lambda target_id, **fields: None)

    response = router_client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-S1", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_packaging_approval_starts_missing_description_draft(router_client, monkeypatch):
    import thousand_sunny.routers.packaging as pkg_module

    release = {
        "targets": [
            {
                "id": 42,
                "platform": "youtube",
                "status": "draft",
                "description": "",
                "error": None,
            }
        ]
    }
    started = []
    monkeypatch.setattr(pkg_module, "get_release", lambda episode, cut_id: release)
    monkeypatch.setattr(pkg_module, "update_target", lambda target_id, **fields: None)
    monkeypatch.setattr(
        pkg_module,
        "_start_description_draft",
        lambda episode, cut_id, target_id: started.append((episode, cut_id, target_id)),
    )

    response = router_client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "description_pending=1" in response.headers["location"]
    assert started == [("20260723 謝伯讓", "punch-L1", 42)]


def test_description_interruption_is_visible_and_retryable(client, monkeypatch):
    import thousand_sunny.routers.packaging as pkg_module

    client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )
    monkeypatch.setattr(
        pkg_module,
        "get_release",
        lambda episode, cut_id: {
            "targets": [
                {
                    "id": 42,
                    "platform": "youtube",
                    "status": "draft",
                    "description": "",
                    "error": "DESCRIPTION_DRAFT_INTERRUPTED: RuntimeError: subscription unavailable",
                }
            ]
        },
    )

    response = client.get("/bridge/packaging/20260723-xieboran?cut=punch-L1&description_pending=1")

    assert response.status_code == 200
    assert "DESCRIPTION INTERRUPTED" in response.text
    assert "subscription unavailable" in response.text
    assert "重試產生 Description" in response.text


def test_description_generation_status_is_visible(client, monkeypatch, tmp_path):
    import thousand_sunny.routers.packaging as pkg_module
    from shared.background_job import atomic_job_write, new_job

    monkeypatch.setenv("NAKAMA_DATA_DIR", str(tmp_path / "data"))

    client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )
    monkeypatch.setattr(
        pkg_module,
        "get_release",
        lambda episode, cut_id: {
            "targets": [
                {
                    "id": 42,
                    "platform": "youtube",
                    "status": "draft",
                    "description": "",
                    "error": "DESCRIPTION_DRAFT_GENERATING",
                }
            ]
        },
    )
    job_path = pkg_module._description_job_path("20260723 謝伯讓", "punch-L1")
    atomic_job_write(
        job_path,
        new_job(
            status="generating",
            timeout_seconds=900,
            episode="20260723 謝伯讓",
            cut_id="punch-L1",
            target_id=42,
        ),
    )

    response = client.get("/bridge/packaging/20260723-xieboran?cut=punch-L1&description_pending=1")

    assert response.status_code == 200
    assert "正在產生 Description 草稿" in response.text
    assert "pollDescription" in response.text


_PROVENANCE = {
    "supply": "envato",
    "source": "https://elements.envato.com/photo-placeholder-ABC123",
    "query": "cockatiel on indoor play stand",
    "why": "扣回 03:29 那個 beat：被照顧得好好的寵物就是「圈養」的畫面",
}


def _receipt_payload(vault, **overrides) -> dict:
    _write_composition_receipt(vault)
    receipt = (
        vault
        / "Attachments"
        / "packaging"
        / "20260723-xieboran"
        / "composition_receipts"
        / "punch-L1-r1.json"
    )
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload.update(overrides)
    return payload


def test_v3_receipt_carries_the_center_card_provenance(vault):
    from thousand_sunny.routers.packaging import LongThumbnailCompositionReceiptV2

    payload = _receipt_payload(
        vault,
        schema="nakama.long_thumbnail_composition.v3",
        center_provenance=_PROVENANCE,
    )
    parsed = LongThumbnailCompositionReceiptV2.model_validate(payload)
    assert parsed.center_provenance is not None
    assert parsed.center_provenance.supply == "envato"


def test_v3_receipt_without_provenance_is_rejected(vault):
    """v3 就是為了這個欄位才存在——少了它就不是 v3。"""
    from pydantic import ValidationError

    from thousand_sunny.routers.packaging import LongThumbnailCompositionReceiptV2

    payload = _receipt_payload(vault, schema="nakama.long_thumbnail_composition.v3")
    with pytest.raises(ValidationError, match="center_provenance"):
        LongThumbnailCompositionReceiptV2.model_validate(payload)


def test_legacy_v2_receipt_still_gates_without_provenance(vault):
    """2026-08-29 之前的 12 份 receipt 都是 v2，其中三份已核准——不追溯作廢。"""
    from thousand_sunny.routers.packaging import LongThumbnailCompositionReceiptV2

    parsed = LongThumbnailCompositionReceiptV2.model_validate(_receipt_payload(vault))
    assert parsed.center_provenance is None


def test_v2_receipt_carrying_provenance_is_rejected_as_hand_edited(vault):
    from pydantic import ValidationError

    from thousand_sunny.routers.packaging import LongThumbnailCompositionReceiptV2

    payload = _receipt_payload(vault, center_provenance=_PROVENANCE)
    with pytest.raises(ValidationError, match="schema 版號"):
        LongThumbnailCompositionReceiptV2.model_validate(payload)


def _stage_candidate_pool(vault, *, candidate_id="22KBKWG", width=1600, height=900):
    """一支 cut 的中央卡候選池——gate 上那排可以點的圖庫縮圖。"""
    ep = vault / "Attachments" / "packaging" / "20260723-xieboran"
    pool_dir = ep / "center-candidates"
    pool_dir.mkdir(parents=True, exist_ok=True)
    name = f"punch-L1-{candidate_id}.png"
    (pool_dir / name).write_bytes(bytes.fromhex("89504e470d0a1a0a"))
    pool_dir.joinpath("punch-L1.json").write_text(
        json.dumps(
            {
                "schema": "nakama.center_card_candidates.v1",
                "episode": "20260723 謝伯讓",
                "cut_id": "punch-L1",
                "generated_at": "2026-08-29T12:00:00+00:00",
                "candidates": [
                    {
                        "candidate_id": candidate_id,
                        "preview_png": (
                            f"Attachments/packaging/20260723-xieboran/center-candidates/{name}"
                        ),
                        "width": width,
                        "height": height,
                        "title": "golden retriever lying on a yellow sofa",
                        "author": "LightFieldStudios",
                        "supply": "envato",
                        "source": f"https://elements.envato.com/a-pampered-dog-{candidate_id}",
                        "query": "pampered dog on sofa",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return f"Attachments/packaging/20260723-xieboran/center-candidates/{name}"


def _compose_center(client, center_visual_asset):
    return _compose(
        client,
        package_rank="2",
        composition="thumbnail_reaction",
        big_text_1="",
        big_text_2="",
        highlight_text="",
        center_visual_asset=center_visual_asset,
        center_width_pct="53",
        center_height_px="455",
        center_x_pct="50",
        center_y_pct="50",
        geometry_mode="manual",
        **_GEO,
    )


def test_compose_accepts_a_center_image_picked_from_the_candidate_pool(client, vault_with_cutouts):
    """修修 2026-08-29 要的就是這個：在 gate 上把中央圖換掉。"""
    asset = _stage_candidate_pool(vault_with_cutouts)

    assert _compose_center(client, asset).status_code == 303

    path = vault_with_cutouts / "Attachments" / "packaging" / "20260723-xieboran" / "packages.json"
    recipe = json.loads(path.read_text(encoding="utf-8"))["cuts"][0]["packages"][1]["render_recipe"]
    assert recipe["center_visual_asset"] == asset


def test_compose_still_refuses_a_center_path_outside_the_pool(client, vault_with_cutouts):
    """放寬到候選池，不是放寬成任意 vault 路徑輸入。"""
    _stage_candidate_pool(vault_with_cutouts)

    response = _compose_center(client, "Attachments/packaging/20260723-xieboran/not-referenced.png")
    assert response.status_code == 409


def test_candidate_preview_is_served_and_unknown_ids_are_not(client, vault_with_cutouts):
    _stage_candidate_pool(vault_with_cutouts)
    base = "/bridge/packaging/20260723-xieboran/center-candidate/punch-L1"

    assert client.get(f"{base}/22KBKWG").status_code == 200
    assert client.get(f"{base}/NOSUCHID").status_code == 404


def test_board_offers_the_pool_as_clickable_thumbnails(client, vault_with_cutouts):
    _stage_candidate_pool(vault_with_cutouts)

    board = client.get("/bridge/packaging/20260723-xieboran")

    assert "data-center-pool" in board.text
    assert "center-candidate/punch-L1/22KBKWG" in board.text


def test_a_broken_pool_file_does_not_take_the_gate_down(client, vault_with_cutouts):
    """候選池是錦上添花——壞掉時 gate 仍然要能核准。"""
    ep = vault_with_cutouts / "Attachments" / "packaging" / "20260723-xieboran"
    pool = ep / "center-candidates"
    pool.mkdir(parents=True, exist_ok=True)
    (pool / "punch-L1.json").write_text("{ not json", encoding="utf-8")

    board = client.get("/bridge/packaging/20260723-xieboran")

    assert board.status_code == 200
    # 版面標題那串字也出現在 JS 註解裡——用挑圖容器本身當判準才是真的沒渲染。
    assert "data-center-pool" not in board.text


def test_center_search_request_is_queued_for_the_desktop(client, vault_with_cutouts):
    """候選都不滿意時寫下需求——Bridge 叫不到圖庫，只能排請求（同 bigtext_request）。"""
    response = client.post(
        "/bridge/packaging/20260723-xieboran/center-search",
        data={"cut_id": "punch-L1", "center_search_request": "我要拉布拉多，不要鸚鵡"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = json.loads(
        (
            vault_with_cutouts / "Attachments" / "packaging" / "20260723-xieboran" / "approval.json"
        ).read_text(encoding="utf-8")
    )
    entry = next(a for a in saved["approvals"] if a["cut_id"] == "punch-L1")
    assert entry["center_search_request"] == "我要拉布拉多，不要鸚鵡"
    # 排需求不是裁決——沒按過 Approve/Reject 就不該把 decision 寫成什麼
    assert entry["decision"] is None


def test_center_search_needs_actual_words(client, vault_with_cutouts):
    response = client.post(
        "/bridge/packaging/20260723-xieboran/center-search",
        data={"cut_id": "punch-L1", "center_search_request": "   "},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_center_search_redirect_keeps_the_cut_in_focus(client, vault_with_cutouts):
    """存完跳回 Full tab 是 2026-08-29 的實際災情——看起來像什麼都沒發生。"""
    response = client.post(
        "/bridge/packaging/20260723-xieboran/center-search",
        data={"cut_id": "punch-L1", "center_search_request": "要看得到柵欄"},
        follow_redirects=False,
    )
    assert "cut=punch-L1" in response.headers["location"]


def test_compose_redirect_keeps_the_cut_in_focus(client, vault_with_cutouts):
    response = _compose(client)

    assert response.status_code == 303
    location = response.headers["location"]
    assert "cut=punch-L1" in location and "composed=punch-L1" in location


def test_a_queued_search_request_is_shown_with_its_timing_caveat(client, vault_with_cutouts):
    client.post(
        "/bridge/packaging/20260723-xieboran/center-search",
        data={"cut_id": "punch-L1", "center_search_request": "我要拉布拉多"},
        follow_redirects=False,
    )

    board = client.get("/bridge/packaging/20260723-xieboran?cut=punch-L1")

    assert "我要拉布拉多" in board.text
    assert "不是即時的" in board.text


def _saved_recipe(vault, rank=2):
    path = vault / "Attachments" / "packaging" / "20260723-xieboran" / "packages.json"
    packages = json.loads(path.read_text(encoding="utf-8"))["cuts"][0]["packages"]
    return next(p for p in packages if p["title_rank"] == rank)["render_recipe"]


def test_picking_a_candidate_records_its_provenance_in_the_recipe(client, vault_with_cutouts):
    """來歷要跟著配方走——桌機端會把預覽換成授權檔，一換檔名就回溯不到候選了。"""
    asset = _stage_candidate_pool(vault_with_cutouts)

    assert _compose_center(client, asset).status_code == 303

    provenance = _saved_recipe(vault_with_cutouts)["center_provenance"]
    assert provenance["supply"] == "envato"
    assert provenance["source"].endswith("22KBKWG")
    assert provenance["query"] == "pampered dog on sofa"


def test_the_reason_recorded_is_the_users_own_search_request(client, vault_with_cutouts):
    """修修打的找圖需求就是他要這張圖的理由——用他的原話，不要編一個。"""
    asset = _stage_candidate_pool(vault_with_cutouts)
    client.post(
        "/bridge/packaging/20260723-xieboran/center-search",
        data={"cut_id": "punch-L1", "center_search_request": "我要拉布拉多，要看得到柵欄"},
        follow_redirects=False,
    )

    _compose_center(client, asset)

    assert (
        _saved_recipe(vault_with_cutouts)["center_provenance"]["why"]
        == "我要拉布拉多，要看得到柵欄"
    )


def test_picking_without_a_reason_records_that_fact_rather_than_inventing_one(
    client, vault_with_cutouts
):
    asset = _stage_candidate_pool(vault_with_cutouts)

    _compose_center(client, asset)

    why = _saved_recipe(vault_with_cutouts)["center_provenance"]["why"]
    assert "未附理由" in why


def test_keeping_the_existing_centre_carries_its_provenance_forward(client, vault_with_cutouts):
    """挑的是原本那張就沿用舊來歷，不無中生有。"""
    response = _compose_center(
        client, "Attachments/packaging/20260723-xieboran/center-punch-L1-r2.png"
    )

    assert response.status_code == 303
    # 這個 fixture 的舊配方沒有來歷（v2 時代的資料），所以維持 None——不編造
    assert _saved_recipe(vault_with_cutouts)["center_provenance"] is None


def _board_css() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[1]
        / "thousand_sunny"
        / "templates"
        / "bridge"
        / "packaging_board.html"
    ).read_text(encoding="utf-8")


def test_selected_state_outranks_the_ghost_button_rule():
    """`body.sho .sho-btn--ghost` 是 (0,2,1)——選中規則沒有 body.sho 前綴就會被蓋掉。

    2026-08-29 修修回報「點下去都沒有顯示我目前正在處於哪一個選項」，圖層按鈕與
    package 分頁兩處都中。
    """
    css = _board_css()
    for selector in (
        'body.sho .pkg-recipe-tab[aria-selected="true"]',
        'body.sho .pkg-layer-button[aria-pressed="true"]',
    ):
        assert selector in css, selector
    # 沒有前綴的舊寫法不該復活
    assert '\n  .pkg-layer-button[aria-pressed="true"] {' not in css
    assert '\n  .pkg-recipe-tab[aria-selected="true"] {' not in css


def test_stage_material_width_limits_outrank_the_global_img_reset():
    """`.sho img { max-width: 100% }` 是 (0,1,1)，會蓋掉舞台自己宣告的上限。

    後果不是「預覽放不大」而已——composition 沒有寬度上限，所以超過撞牆點之後
    成品仍在放大，而預覽凍住：所見不等於所得（修修 2026-08-29 回報）。
    """
    css = _board_css()
    assert "body.sho .st-person { max-width: none; }" in css
    assert "body.sho .st-book { max-width: 50%; }" in css


def test_the_preview_does_not_cap_people_where_the_composition_does_not():
    """兩個 composition 的 #host/#guest 都沒有 max-width——預覽也不可以有。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "video" / "compositions"
    for name in ("thumbnail_reaction", "thumbnail_full"):
        block = (root / name / "index.html").read_text(encoding="utf-8")
        start = block.index("#host, #guest {")
        assert "max-width" not in block[start : start + 200], name


def _heartbeat_state(**over):
    from datetime import datetime, timezone

    row = {
        "episode_slug": "20260723-xieboran",
        "cut_id": "punch-L1",
        "package_rank": None,
        "seen_at": datetime.now(timezone.utc).isoformat(),
        "pid": 123,
    }
    row.update(over)
    return {"_watchers": {"k": row}}


def test_a_live_watcher_covering_the_cut_is_recognised():
    from thousand_sunny.routers.packaging import _watcher_covering

    assert _watcher_covering(_heartbeat_state(), "20260723-xieboran", "punch-L1", 1) is not None


def test_a_watcher_bound_to_another_cut_does_not_count():
    """2026-08-29 的實際情況：跑著的 watcher 綁在別的 cut，這支永遠不會被撿走。"""
    from thousand_sunny.routers.packaging import _watcher_covering

    state = _heartbeat_state(cut_id="value-L01")
    assert _watcher_covering(state, "20260723-xieboran", "punch-L1", 1) is None


def test_a_watcher_that_stopped_reporting_does_not_count():
    from datetime import datetime, timedelta, timezone

    from thousand_sunny.routers.packaging import _watcher_covering

    stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    assert (
        _watcher_covering(_heartbeat_state(seen_at=stale), "20260723-xieboran", "punch-L1", 1)
        is None
    )


def test_an_unscoped_watcher_covers_everything():
    from thousand_sunny.routers.packaging import _watcher_covering

    state = _heartbeat_state(episode_slug=None, cut_id=None)
    assert _watcher_covering(state, "20260723-xieboran", "punch-L1", 1) is not None


# --- titles-only 草稿是合法狀態，湊滿由 gate 守 -------------------------------
# `CutV1` 原本要求長片「剛好 3 個 package」。封面是一個一個補上的，中間必然經過
# 0/1/2 個，而那段中間態會讓**整個 packaging 頁** 422——連同已經配好封面的別支。
# 規則從檔案 schema 移到 approve gate（2026-09-10）。


def _draft_vault(vault: Path, *, packages: list[dict]) -> None:
    data = _packages_data()
    data["cuts"][0]["packages"] = packages
    (vault / "Attachments" / "packaging" / "20260723-xieboran" / "packages.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def test_board_renders_a_titles_only_long_draft(client, vault):
    """封面還沒配的長片不該把看板打掛——標題本來就先出來。"""
    _draft_vault(vault, packages=[])
    r = client.get("/bridge/packaging/20260723-xieboran")
    assert r.status_code == 200
    assert "punch-L1" in r.text


def test_board_renders_a_partially_packaged_long_cut(client, vault):
    _draft_vault(vault, packages=[_package(1)])
    r = client.get("/bridge/packaging/20260723-xieboran")
    assert r.status_code == 200


def test_approve_still_refuses_a_long_cut_with_no_package(client, vault):
    """schema 放寬了，這一關就是唯一守門的地方——它必須真的擋得住。"""
    _draft_vault(vault, packages=[])
    r = client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert "thumbnail-brainstorm" in r.json()["detail"]


def test_approve_refuses_a_rank_that_has_no_package_yet(client, vault):
    _draft_vault(vault, packages=[_package(1)])
    r = client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "2"},
        follow_redirects=False,
    )
    assert r.status_code == 409
    # 已經配好的那一個仍然核准得了。
    ok = client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={"cut_id": "punch-L1", "decision": "approve", "primary_package": "1"},
        follow_redirects=False,
    )
    assert ok.status_code == 303


def test_render_recipe_carries_text_block_position():
    """字塊位置要能被記住，而且手動旗標跟人物幾何分開。

    2026-09-14 修修：「我希望也能調整這一整個中間的文字區塊的位置以及大小」。
    大小早就有 title_max_width；位置在此之前水平由 render 端自動收斂、垂直在
    composition 裡寫死成 top: 44%，gate 上完全碰不到。
    """
    req = RenderRequestV1(
        title_rank=1,
        host_cutout="Attachments/cutouts/podcast/ep/host_1_serious.png",
        guest_cutout="Attachments/cutouts/podcast/ep/guest_1_serious.png",
        big_text=["13 歲前", "先別給 AI"],
        highlight_text="13 歲前",
        text_center_pct=41.5,
        text_top_pct=52.0,
        text_position_manual=True,
        requested_at=datetime.now(timezone.utc),
    )

    assert req.text_center_pct == 41.5
    assert req.text_top_pct == 52.0
    assert req.text_position_manual is True
    # 只挪字不該連帶鎖住人物的自動解算，反之亦然。
    assert req.geometry_manual is False

    assert RenderRequestV1.model_validate(req.model_dump(mode="json")) == req


def test_text_block_defaults_match_the_composition():
    """預設值必須跟 composition 的 CSS 對齊，否則預覽與成品從第一秒就不同位置。"""
    req = RenderRequestV1(
        title_rank=1,
        host_cutout="Attachments/cutouts/podcast/ep/host_1_serious.png",
        guest_cutout="Attachments/cutouts/podcast/ep/guest_1_serious.png",
        big_text=["一行"],
        requested_at=datetime.now(timezone.utc),
    )
    assert (req.text_center_pct, req.text_top_pct) == (50.0, 44.0)
    assert req.text_position_manual is False

    root = Path(__file__).resolve().parents[1]
    composition = (root / "video/compositions/thumbnail_full/index.html").read_text(
        encoding="utf-8"
    )
    assert "var(--text-y, 44%)" in composition
    assert "var(--text-x, 50%)" in composition
    assert '"text_top_pct":          { "type": "number", "default": 44 }' in composition

    board = (root / "thousand_sunny/templates/bridge/packaging_board.html").read_text(
        encoding="utf-8"
    )
    # 字塊要跟臉、中央圖一樣是可選取可拖曳的圖層——修修點不到它正是這次的起因。
    assert 'data-layer-select="text"' in board
    assert 'class="st-text st-adjustable" data-role="text"' in board
    assert "var(--text-y, 44%)" in board


def test_compose_saves_manual_text_block_position(client, vault_with_cutouts):
    """拖過的字塊位置要原封不動進 render_request（修修 2026-09-14）。

    在此之前字塊是舞台上唯一不能選、不能拖的元件：臉可以、中央圖可以，只有大字
    不行。水平位置由 render 端的遮蔽平衡自動收斂，垂直位置在 composition 裡寫死
    成 top: 44%，gate 完全碰不到。
    """
    assert (
        _compose(
            client,
            text_position_mode="manual",
            text_center_pct="45.21",
            text_top_pct="52.52",
        ).status_code
        == 303
    )
    req = _saved_req(vault_with_cutouts)
    assert req["text_position_manual"] is True
    assert req["text_center_pct"] == 45.21
    assert req["text_top_pct"] == 52.52


def test_text_position_manual_is_independent_of_geometry_manual(client, vault_with_cutouts):
    """只挪字不該連帶鎖住人物的自動解算，反之亦然。

    兩者共用一個旗標的話，修修挪一下大字就等於把臉的位置也鎖死，之後換一張臉
    也不會重新解算——那正是 geometry_manual 當初要避免的坑，不該在字塊上重演。
    """
    _compose(client, text_position_mode="manual", text_center_pct="45.21", text_top_pct="52.52")
    req = _saved_req(vault_with_cutouts)
    assert req["text_position_manual"] is True
    assert req["geometry_manual"] is False

    _compose(client, geometry_mode="manual", **_GEO)
    req = _saved_req(vault_with_cutouts)
    assert req["geometry_manual"] is True
    assert req["text_position_manual"] is False


def test_compose_defaults_leave_text_position_automatic(client, vault_with_cutouts):
    """沒碰過字塊 → 維持自動：render 端照樣跑遮蔽平衡收斂水平位置。"""
    assert _compose(client).status_code == 303
    req = _saved_req(vault_with_cutouts)
    assert req["text_position_manual"] is False
    assert req["text_center_pct"] == 50.0
    assert req["text_top_pct"] == 44.0


def test_tabs_appear_without_a_resume_ledger(router_client, vault):
    """沒有 manifest.json 也要有 tab——那是 2026-09-14 兩種版面的唯一成因。

    帳本寫在工作目錄，沒有任何程式碼把它鏡射到 vault，而這一頁是去 vault 讀它。
    七集裡只有一集湊巧有，於是同一個 packaging 頁長出兩種版面。修修看到後問
    「為什麼會有差別」，並指定要 tab 那一版。

    tab 需要的東西 packages.json 全都有，而它每一集都在——所以退回它，而不是去
    補那六個帳本檔（補了下一集還是會缺）。
    """
    ep = vault / "Attachments" / "packaging" / "20260723-xieboran"
    assert not (ep / "manifest.json").is_file()

    board = router_client.get("/bridge/packaging/20260723-xieboran")

    assert board.status_code == 200
    assert 'role="tablist"' in board.text
    assert ">Long 1<" in board.text


def test_ledger_free_tabs_are_ready_not_guessed(router_client, vault):
    """退回 packages.json 時每個 tab 都是 ready——那是既成事實，不是猜的。

    原本的 docstring 寫 "without inventing ready assets"；這條守住那句話在
    fallback 之後仍然成立：只有真的在 packages.json 裡的 cut 才會長出 tab。
    """
    assert not (
        vault / "Attachments" / "packaging" / "20260723-xieboran" / "manifest.json"
    ).is_file()

    board = router_client.get("/bridge/packaging/20260723-xieboran")

    assert board.status_code == 200
    # 只看 tab 上的狀態標記；頁面 JS 裡有一份狀態字典也含這些字眼。
    assert 'pkg-tab-status">QUEUED<' not in board.text
    assert 'pkg-tab-status">RUNNING<' not in board.text
    assert 'pkg-tab-status">READY<' in board.text


def test_render_timeout_is_explained_not_dumped(client, vault, monkeypatch):
    """冷啟動逾時要講人話，並告訴他怎麼做——不要把 traceback 丟到他臉上。

    2026-09-14 修修按下「存配方」後看到的是：

        封面 render 失敗：^^^^^ File "C:\\Python314\\Lib\\subprocess.py", line 1664,
        in _communicate raise TimeoutExpired(self.args, orig_timeout)
        subprocess.TimeoutExpired: Command '[...]' timed out after 600 seconds

    他的結論是「系統壞了」。實際上那次只是第一次跑 N2 版式、算圖環境冷啟動超過
    600 秒；同一張暖機後 10–14 秒就出來，重按一次就過。
    """
    from thousand_sunny.routers.packaging import _render_failure_sentence

    raw = (
        "Traceback (most recent call last):\n"
        '  File "C:\\Python314\\Lib\\subprocess.py", line 1664, in _communicate\n'
        "    raise TimeoutExpired(self.args, orig_timeout)\n"
        "subprocess.TimeoutExpired: Command '[...]' timed out after 600 seconds"
    )
    sentence = _render_failure_sentence(raw)

    assert "逾時" in sentence
    assert "再按一次" in sentence
    # 人話裡不能挾帶任何機器碎片。
    for machine in ("Traceback", "subprocess", "TimeoutExpired", "C:\\", ".py"):
        assert machine not in sentence


def test_unknown_render_errors_are_not_given_invented_explanations():
    """認不出來的錯誤就老實說「失敗」。

    硬替不認得的 stderr 編一個人話說明，會比原始 traceback 更誤導——前者看起來
    像診斷，後者至少誠實地說「我也不知道」。
    """
    from thousand_sunny.routers.packaging import _render_failure_sentence

    assert _render_failure_sentence("something nobody has seen before") == "封面 render 失敗"
    assert _render_failure_sentence(None) == "封面 render 失敗"
    assert "素材" in _render_failure_sentence("FileNotFoundError: ...")


def test_board_does_not_show_machine_identifiers(client):
    """盤點過的機器訊息不該回到畫面上（修修 2026-09-14 逐條列的那 13 條）。

    這些東西不是沒有價值——追查時有用——所以它們搬進 title 或 <details>，
    不是被刪掉。這條守的是「不要再印在他眼前」。
    """
    body = client.get("/bridge/packaging/20260723-xieboran").text

    # 內部代號與 CLI 名稱
    assert "gate 零 LLM" not in body
    assert "title-brainstorm --batch" not in body
    assert "scripts/packaging_brief.py" not in body
    assert "N2 橘框與中央圖" not in body
    assert "render recipe；目前只帶入" not in body
    # 機器狀態語
    assert "COMPOSITION VERIFIED" not in body
    assert "等待查詢" not in body
    # 把網址當標題印
    assert "/bridge/packaging/20260723-xieboran · package 裁決" not in body

    # 反過來：人話要在
    assert "封面與標題裁決" in body
    assert "版面已驗證" in body


# ---------------------------------------------------------------------------
# 存配方之後畫面要說話（2026-09-14 修修：「按下『存配方』，但是什麼事情都沒有發生」）
# ---------------------------------------------------------------------------


def _set_saved_recipe(vault: Path, *, rank: int = 1, **fields) -> None:
    """直接改 packages.json 裡那份**已存**配方的欄位（模擬桌機端回填）。"""
    path = vault / "Attachments" / "packaging" / "20260723-xieboran" / "packages.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    recipe = data["cuts"][0]["packages"][rank - 1]["render_recipe"]
    assert recipe is not None, "先 compose 才有配方可以改"
    recipe.update(fields)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_saved_recipe_says_the_cover_is_now_out_of_date(client, vault_with_cutouts):
    """修修的處境：配方存進去了，但下面那張封面是上一版配方出的。

    gate 只寫配方、不出圖（ADR-054 D11），所以存完就是原地刷新、預覽幾乎沒差。
    沒有這行字，「存好了但還沒出圖」跟「按了沒反應」在畫面上一模一樣。
    """
    assert _compose(client, package_rank="1").status_code == 303

    board = client.get("/bridge/packaging/20260723-xieboran")

    assert board.status_code == 200
    assert 'data-recipe-state="stale"' in board.text
    assert "配方比封面新，需重出圖" in board.text


def test_saved_recipe_without_any_cover_says_it_has_not_been_rendered(client, vault_with_cutouts):
    """還沒出過圖的 package：講「尚未出圖」，不是「封面過期」——沒有封面可以過期。"""
    ep = vault_with_cutouts / "Attachments" / "packaging" / "20260723-xieboran"
    (ep / "pkg-punch-L1-1.png").unlink()
    assert _compose(client, package_rank="1").status_code == 303

    board = client.get("/bridge/packaging/20260723-xieboran")

    assert 'data-recipe-state="unrendered"' in board.text
    assert "配方已存 · 尚未出圖" in board.text


def test_recipe_marked_rendered_once_the_desktop_writes_the_png_back(client, vault_with_cutouts):
    """桌機端回填 rendered_png＝這份配方自己出的圖，第三態要消失。"""
    assert _compose(client, package_rank="1").status_code == 303
    _set_saved_recipe(
        vault_with_cutouts,
        rendered_png="Attachments/packaging/20260723-xieboran/pkg-punch-L1-1.png",
    )

    board = client.get("/bridge/packaging/20260723-xieboran")

    assert 'data-recipe-state="rendered"' in board.text
    assert "已出圖 · 與配方相符" in board.text
    assert "配方比封面新，需重出圖" not in board.text


def test_package_without_a_saved_recipe_claims_no_state_at_all(client, vault_with_cutouts):
    """沒人按過「存配方」就沒有三態可言。

    board 會為舊 N2 package 從 receipt 水合出一份唯讀 recipe 餵編輯器；那份不是
    存下來的配方，把它標成「配方比封面新」等於憑空生出一條待辦。
    """
    board = client.get("/bridge/packaging/20260723-xieboran")

    assert board.status_code == 200
    assert "data-recipe-state" in board.text  # 元素在（hidden），只是沒有值
    assert 'data-recipe-state=""' in board.text
    assert "配方比封面新，需重出圖" not in board.text
    assert "配方已存 · 尚未出圖" not in board.text


def test_render_status_carries_the_same_three_state_as_the_board(
    client, vault_with_cutouts, monkeypatch, tmp_path
):
    """輪詢與整頁重載必須由同一次判定產生，否則兩句話會互相打臉。

    進度條寫「新封面已完成」、旁邊的狀態卻停在「尚未出圖」，人不知道要信哪個。
    """
    import thousand_sunny.routers.packaging as pkg_module

    monkeypatch.setattr(
        pkg_module, "_render_watcher_state_path", lambda: tmp_path / "missing-state.json"
    )
    assert _compose(client, package_rank="1").status_code == 303
    requested_at = _saved_req(vault_with_cutouts)["requested_at"]
    endpoint = "/bridge/packaging/20260723-xieboran/render-status/punch-L1/1"

    stale = client.get(endpoint, params={"requested_at": requested_at})
    assert stale.status_code == 200
    assert stale.json()["recipe_state"]["state"] == "stale"

    _set_saved_recipe(
        vault_with_cutouts,
        rendered_png="Attachments/packaging/20260723-xieboran/pkg-punch-L1-1.png",
    )
    rendered = client.get(endpoint, params={"requested_at": requested_at})
    assert rendered.json()["recipe_state"]["state"] == "rendered"
    assert rendered.json()["recipe_state"]["label"] == "已出圖 · 與配方相符"


# ---------------------------------------------------------------------------
# 橘框詞打錯不能默默消失（2026-09-14 修修：「我無法點選『橘框』這個圖層」）
# ---------------------------------------------------------------------------


def test_highlight_word_gets_a_live_verdict_next_to_its_input(client):
    """打錯一個字，橘框就從預覽消失且不報錯——判定結果要寫在格子底下。"""
    board = client.get("/bridge/packaging/20260723-xieboran")

    assert board.status_code == 200
    assert 'id="hl-verdict-punch-L1"' in board.text
    assert 'aria-describedby="hl-verdict-punch-L1"' in board.text
    assert "data-hl-verdict" in board.text
    assert "這個詞不在大字裡，不會有橘框" in board.text
    assert "會框在${where}" in board.text


def test_highlight_verdict_reuses_the_preview_match_instead_of_redoing_it():
    """判定與預覽必須共用同一次 `line.indexOf(hl)`。

    寫成兩份的話，預覽的橘框與底下那行提示遲早會各說各話——而修修看到的正是
    橘框消失、沒有任何解釋。這條守住「規則只有一份」。
    """
    board = Path("thousand_sunny/templates/bridge/packaging_board.html").read_text(encoding="utf-8")

    assert board.count("line.indexOf(hl)") == 1
    # 判定值由 syncStageText 算完後交出去，setHighlightVerdict 自己不做比對
    syncer = board.split("function syncStageText(stage)")[1].split("function fitStageTitle")[0]
    assert "setHighlightVerdict(form, hl, framedLine, occurrences)" in syncer
    verdict = board.split("function setHighlightVerdict(")[1].split("function syncStageText")[0]
    assert "indexOf" not in verdict


# ---------------------------------------------------------------------------
# Reject 退場（修修 2026-09-15：「reject note 這個框框以及 reject 按鈕完全都不用了，
# 我不知道這裡的 reject 按下去會有什麼行為」）
# ---------------------------------------------------------------------------


def test_gate_offers_no_way_to_reject(client):
    """畫面上不再有 Reject：沒有按鈕、沒有理由欄、沒有 revision 狀態。"""
    body = client.get("/bridge/packaging/20260723-xieboran").text

    assert "Approve" in body
    assert "Reject" not in body
    assert "REJECT NOTE" not in body
    assert 'name="reject_note"' not in body
    assert "REVISION" not in body
    assert "revision/retry" not in body


def test_approve_endpoint_no_longer_accepts_a_rejection(client, vault, monkeypatch):
    """就算有人手工 POST decision=reject，也不會寫出否決或 revision job。

    Reject 的整條後端（watcher 的 run_revision_job）一起拿掉了；如果這裡還認得
    decision=reject，就會排出一筆永遠不會有人處理的 job，把那支 cut 卡死。
    """
    _write_composition_receipt(vault, rank=1)
    _stub_publish_prep(monkeypatch)
    response = client.post(
        "/bridge/packaging/20260723-xieboran/approve",
        data={
            "cut_id": "punch-L1",
            "decision": "reject",
            "reject_note": "三張表情太像，重抽",
            "primary_package": "1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    entry = json.loads(
        (vault / "Attachments" / "packaging" / "20260723-xieboran" / "approval.json").read_text(
            encoding="utf-8"
        )
    )["approvals"][0]
    assert entry["approved"] is True
    assert entry["decision"] == "approve"
    assert entry["revision_job"] is None
    assert entry["reject_note"] is None


def test_old_episodes_carrying_a_revision_job_still_open(client, vault):
    """舊檔的 reject_note／revision_job 必須還讀得動，而且不再被當成一種狀態。

    schema 是 extra="forbid"：把欄位拔掉會讓帶著它們的既有 approval.json 直接驗證
    失敗、整個 board 422。真實 vault 裡就有一筆（20260805 林之晨 full）。
    """
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
                        "reject_note": "封面套錯版面了",
                        "decided_at": "2026-08-21T00:00:00+00:00",
                        "decision": "reject",
                        "revision_job": {
                            "contract": "packaging-revision-job-v1",
                            "request_id": "revision-" + "a" * 16,
                            "feedback": "封面套錯版面了",
                            "requested_at": "2026-08-21T00:00:00+00:00",
                            "source_packages_sha256": "b" * 64,
                            "source_assets": {},
                            "status": "ready_for_review",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    board = client.get("/bridge/packaging/20260723-xieboran")

    assert board.status_code == 200
    assert "PENDING" in board.text
    assert "REJECTED" not in board.text
    assert "封面套錯版面了" not in board.text


def test_a_queued_legacy_revision_no_longer_blocks_saving_a_recipe(client, vault_with_cutouts):
    """殘留的 queued 舊 job 不可以把 cut 鎖死。

    原本 compose 遇到 queued/running 會回 409「revision 正在處理，完成後再存配方」。
    處理它的 worker 已經不存在了，那道閘留著就是永久封鎖。
    """
    ep = vault_with_cutouts / "Attachments" / "packaging" / "20260723-xieboran"
    (ep / "approval.json").write_text(
        json.dumps(
            {
                "episode": "20260723 謝伯讓",
                "approvals": [
                    {
                        "cut_id": "punch-L1",
                        "approved": False,
                        "primary_package": 1,
                        "decided_at": "2026-08-21T00:00:00+00:00",
                        "revision_job": {
                            "contract": "packaging-revision-job-v1",
                            "request_id": "revision-" + "c" * 16,
                            "feedback": "重做",
                            "requested_at": "2026-08-21T00:00:00+00:00",
                            "source_packages_sha256": "d" * 64,
                            "source_assets": {},
                            "status": "queued",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert _compose(client, package_rank="1").status_code == 303
