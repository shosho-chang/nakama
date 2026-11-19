"""/bridge/youtube-packaging 路由：頁面、素材白名單、存配方、標題紀錄衝突、列表導向。"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

SLUG = "20261027-wealth-ladder"


def _packages(recipe="youtube_book") -> dict:
    return {
        "episode": "20261027 財富階梯 AI Agent 版",
        "generated_at": "2026-11-12T00:00:00+00:00",
        "cuts": [
            {
                "cut_id": "full",
                "format": "long",
                "information_origin": "full_text",
                "visual_recipe": recipe,
                "aspect": "16:9",
                "citations": [],
                "brand_flags": [],
                "packages": [],
                "titles": [
                    {
                        "text": f"標題{i}",
                        "archetype_id": "T-A1",
                        "angle_combo": ["x"],
                        "payoff": "p",
                        "cite": "c",
                        "rank": i,
                        **({"panel_note": "n"} if i >= 4 else {}),
                    }
                    for i in range(1, 6)
                ],
            }
        ],
    }


@pytest.fixture
def vault(tmp_path) -> Path:
    ep = tmp_path / "Attachments" / "packaging" / SLUG
    (ep / "assets").mkdir(parents=True)
    (ep / "full").mkdir()
    (ep / "packages.json").write_text(json.dumps(_packages(), ensure_ascii=False), encoding="utf-8")
    (ep / "full" / "bigtext.json").write_text(
        json.dumps(
            {
                "options": [
                    {"lead": "什麼時候可以", "main": "喝星巴克？", "hl": "星巴克"},
                    {"lead": "什麼時候可以", "main": "喝星巴克？", "hl": "星巴克"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    Image.new("RGB", (40, 30), "red").save(ep / "assets" / "bg-money.jpg")
    Image.new("RGB", (30, 40), "white").save(ep / "assets" / "feature.jpg")
    cut = tmp_path / "Attachments" / "cutouts" / "shosho" / "explaining"
    cut.mkdir(parents=True)
    Image.new("RGBA", (30, 40)).save(cut / "2.png")
    return tmp_path


@pytest.fixture
def client(monkeypatch, vault):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("VAULT_PATH", str(vault))
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.packaging as pkg_module
    import thousand_sunny.routers.youtube_packaging as yt_module

    importlib.reload(auth_module)
    importlib.reload(yt_module)
    importlib.reload(pkg_module)
    app = FastAPI()
    app.include_router(pkg_module.page_router)
    app.include_router(yt_module.page_router)
    return TestClient(app)


def _recipe():
    return {
        "bg": {"src": f"Attachments/packaging/{SLUG}/assets/bg-money.jpg"},
        "feature": {
            "src": f"Attachments/packaging/{SLUG}/assets/feature.jpg",
            "cx": 975,
            "cy": 372,
            "h": 520,
        },
        "person": {
            "src": "Attachments/cutouts/shosho/explaining/2.png",
            "cx": 420,
            "top": 44,
            "h": 860,
        },
        "text": {"x": 48, "y": 486, "size": 124, "lead": "a", "main": "b", "hl": "b"},
    }


def _boot(html: str) -> dict:
    start = html.index('id="yt-boot">') + len('id="yt-boot">')
    return json.loads(html[start : html.index("</script>", start)])


def test_page_lists_titles_bigtexts_and_assets(client):
    r = client.get(f"/bridge/youtube-packaging/{SLUG}")
    assert r.status_code == 200
    boot = _boot(r.text)
    assert [t["text"] for t in boot["tracking"]["titles"]] == [f"標題{i}" for i in range(1, 6)]
    assert len(boot["bigtexts"]) == 1  # 重複的大字只列一次
    assert boot["lists"]["backgrounds"] == [f"Attachments/packaging/{SLUG}/assets/bg-money.jpg"]
    assert boot["lists"]["features"] == [f"Attachments/packaging/{SLUG}/assets/feature.jpg"]
    assert boot["lists"]["cutouts"] == ["Attachments/cutouts/shosho/explaining/2.png"]
    assert boot["cover"] is None and boot["status"] == {"has_recipe": False}
    assert boot["lists"]["guests"] == []


def test_guest_layer_listed_saved_and_checked(client, vault):
    assets = vault / "Attachments" / "packaging" / SLUG / "assets"
    Image.new("RGBA", (30, 40)).save(assets / "guest-ray.png")
    boot = _boot(client.get(f"/bridge/youtube-packaging/{SLUG}").text)
    guest = f"Attachments/packaging/{SLUG}/assets/guest-ray.png"
    assert boot["lists"]["guests"] == [guest]
    rec = _recipe()
    rec["guest"] = {"src": guest, "cx": 1040, "top": 110, "h": 760}
    assert (
        client.post(f"/bridge/youtube-packaging/{SLUG}/cover", json={"recipe": rec}).status_code
        == 200
    )
    doc = json.loads((assets.parent / "youtube_cover.json").read_text(encoding="utf-8"))
    assert doc["recipe"]["guest"]["src"] == guest
    rec["guest"]["src"] = f"Attachments/packaging/{SLUG}/assets/guest-nobody.png"
    assert (
        client.post(f"/bridge/youtube-packaging/{SLUG}/cover", json={"recipe": rec}).status_code
        == 404
    )


def test_asset_whitelist(client, vault):
    ok = client.get(
        f"/bridge/youtube-packaging/{SLUG}/asset",
        params={"p": "Attachments/cutouts/shosho/explaining/2.png"},
    )
    assert ok.status_code == 200
    (vault / "secret.png").write_bytes(b"x")
    for bad in (
        "secret.png",
        "Attachments/cutouts/shosho/../../secret.png",
        "Attachments/packaging/other-ep/assets/bg.jpg",
    ):
        assert (
            client.get(f"/bridge/youtube-packaging/{SLUG}/asset", params={"p": bad}).status_code
            == 404
        )


def test_save_cover_sets_requested_at_and_keeps_render_fields(client, vault):
    r = client.post(f"/bridge/youtube-packaging/{SLUG}/cover", json={"recipe": _recipe()})
    assert r.status_code == 200 and r.json()["needs_render"] is True
    path = vault / "Attachments" / "packaging" / SLUG / "youtube_cover.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["schema"] == "nakama.youtube_cover.v1" and doc["requested_at"]
    doc["rendered_at"] = "2030-01-01T00:00:00+00:00"
    path.write_text(json.dumps(doc), encoding="utf-8")
    client.post(f"/bridge/youtube-packaging/{SLUG}/cover", json={"recipe": _recipe()})
    assert json.loads(path.read_text(encoding="utf-8"))["rendered_at"].startswith("2030")


def test_save_cover_rejects_missing_or_foreign_assets(client):
    rec = _recipe()
    rec["bg"]["src"] = f"Attachments/packaging/{SLUG}/assets/nope.jpg"
    assert (
        client.post(f"/bridge/youtube-packaging/{SLUG}/cover", json={"recipe": rec}).status_code
        == 404
    )
    rec = _recipe()
    rec["person"]["src"] = "C:/Users/x.png"
    assert (
        client.post(f"/bridge/youtube-packaging/{SLUG}/cover", json={"recipe": rec}).status_code
        == 422
    )


def test_titles_save_and_conflict(client):
    boot = _boot(client.get(f"/bridge/youtube-packaging/{SLUG}").text)
    tr = boot["tracking"]
    tr["titles"][0]["text"] = "改過的標題"
    tr["titles"][0]["published"] = True
    tr["video_url"] = "https://youtu.be/abc"
    r = client.post(
        f"/bridge/youtube-packaging/{SLUG}/titles", json={**tr, "base_updated_at": None}
    )
    assert r.status_code == 200
    saved = r.json()
    t0 = saved["titles"][0]
    assert t0["published_at"] and t0["edited_at"] and t0["ab"] is True
    # 舊頁面（base 是 None）再存 → 409，不覆蓋
    stale = client.post(
        f"/bridge/youtube-packaging/{SLUG}/titles", json={**tr, "base_updated_at": None}
    )
    assert stale.status_code == 409 and stale.json()["current"]["titles"][0]["text"] == "改過的標題"
    # 帶正確 base、送空網址 → 保留已記錄的網址
    nxt = {**saved, "video_url": "", "base_updated_at": saved["updated_at"]}
    r2 = client.post(f"/bridge/youtube-packaging/{SLUG}/titles", json=nxt)
    assert r2.status_code == 200 and r2.json()["video_url"] == "https://youtu.be/abc"


def test_titles_ab_limit(client):
    tr = _boot(client.get(f"/bridge/youtube-packaging/{SLUG}").text)["tracking"]
    for t in tr["titles"][:4]:
        t["published"] = True
    r = client.post(
        f"/bridge/youtube-packaging/{SLUG}/titles", json={**tr, "base_updated_at": None}
    )
    assert r.status_code == 422


def test_conflict_file_blocks(client, vault):
    (vault / "Attachments" / "packaging" / SLUG / "title_tracking.sync-conflict-1.json").write_text(
        "{}"
    )
    assert client.get(f"/bridge/youtube-packaging/{SLUG}").status_code == 409


def test_packaging_list_and_board_route_youtube_episodes(client):
    r = client.get("/bridge/packaging")
    assert f'href="/bridge/youtube-packaging/{SLUG}"' in r.text
    b = client.get(f"/bridge/packaging/{SLUG}", follow_redirects=False)
    assert b.status_code == 303 and b.headers["location"] == f"/bridge/youtube-packaging/{SLUG}"
