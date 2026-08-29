"""把候選預覽換成正式授權檔（修修 2026-08-29：「所有的素材下載都要你幫我做」）。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))


def _load(name: str):
    path = _REPO / ".claude" / "skills" / "thumbnail-brainstorm" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch = _load("fetch_licensed_center")

SLUG = "20260805-linzhichen"
PREVIEW = f"Attachments/packaging/{SLUG}/center-candidates/punch-L04-PHGMVY9.jpg"


def _image(path: Path, width: int, height: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), (100, 100, 100)).save(path)
    return path


@pytest.fixture
def episode(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    ep = vault / "Attachments" / "packaging" / SLUG
    (ep / "center-candidates").mkdir(parents=True)
    _image(ep / "center-candidates" / "punch-L04-PHGMVY9.jpg", 600, 400)

    (ep / "center-candidates" / "punch-L04.json").write_text(
        json.dumps(
            {
                "schema": "nakama.center_card_candidates.v1",
                "episode": "20260805 林之晨",
                "cut_id": "punch-L04",
                "generated_at": "2026-08-29T12:00:00+00:00",
                "candidates": [
                    {
                        "candidate_id": "PHGMVY9",
                        "preview_png": PREVIEW,
                        "width": 600,
                        "height": 400,
                        "title": "Dog face behind bars",
                        "author": "NomadSoul1",
                        "supply": "envato",
                        "source": "https://elements.envato.com/dog-face-behind-bars-PHGMVY9",
                        "query": "labrador in kennel cage bars",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    recipe = {
        "composition": "thumbnail_reaction",
        "title_rank": 1,
        "host_cutout": "h.png",
        "guest_cutout": "g.png",
        "center_visual_asset": PREVIEW,
        "requested_at": "2026-08-29T12:48:58Z",
        "rendered_png": "stale.png",
    }
    (ep / "packages.json").write_text(
        json.dumps(
            {
                "episode": "20260805 林之晨",
                "cuts": [
                    {
                        "cut_id": "punch-L04",
                        "packages": [{"title_rank": 1, "render_recipe": dict(recipe)}],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (ep / "approval.json").write_text(
        json.dumps(
            {
                "episode": "20260805 林之晨",
                "approvals": [
                    {
                        "cut_id": "punch-L04",
                        "center_search_request": "我要拉布拉多",
                        "render_request": dict(recipe),
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return ep, tmp_path


def test_pending_lists_what_is_waiting_and_where_to_get_it(episode):
    ep, _ = episode

    rows = fetch.pending_rows(ep)

    assert len(rows) == 1
    assert rows[0]["cut_id"] == "punch-L04"
    assert rows[0]["source"].endswith("PHGMVY9")


def test_install_repoints_every_copy_the_renderer_reads(episode):
    """approval.json 是 watcher 撿的那一份——只改 packages.json 等於沒改。"""
    ep, tmp_path = episode
    licensed = _image(tmp_path / "dl" / "dog-behind-bars.jpg", 6016, 4016)

    result = fetch.install(SLUG, "punch-L04", 1, licensed)

    asset = f"Attachments/packaging/{SLUG}/center-punch-L04-r1.jpg"
    assert result["asset"] == asset
    packages = json.loads((ep / "packages.json").read_text(encoding="utf-8"))
    recipe = packages["cuts"][0]["packages"][0]["render_recipe"]
    approval = json.loads((ep / "approval.json").read_text(encoding="utf-8"))
    request = approval["approvals"][0]["render_request"]
    assert recipe["center_visual_asset"] == asset
    assert request["center_visual_asset"] == asset
    assert (ep / "center-punch-L04-r1.jpg").is_file()


def test_install_bumps_requested_at_so_the_watcher_picks_it_up_again(episode):
    """沿用舊 requested_at 的話 watcher 會判定做過了，靜靜不動。"""
    ep, tmp_path = episode
    licensed = _image(tmp_path / "dl" / "a.jpg", 4000, 2500)

    result = fetch.install(SLUG, "punch-L04", 1, licensed)

    assert result["requested_at"] != "2026-08-29T12:48:58Z"
    packages = json.loads((ep / "packages.json").read_text(encoding="utf-8"))
    assert packages["cuts"][0]["packages"][0]["render_recipe"]["rendered_png"] is None


def test_install_rescues_provenance_from_the_pool_before_the_link_is_lost(episode):
    """換掉檔名之後，候選池是唯一還記得這張圖哪來的地方——趁現在抄進配方。"""
    ep, tmp_path = episode
    licensed = _image(tmp_path / "dl" / "a.jpg", 4000, 2500)

    result = fetch.install(SLUG, "punch-L04", 1, licensed)

    assert result["provenance"]["source"].endswith("PHGMVY9")
    assert result["provenance"]["query"] == "labrador in kennel cage bars"


def test_a_preview_sized_file_is_refused(episode):
    """下錯成浮水印預覽時要停——它有 600px 級的長邊。"""
    _, tmp_path = episode
    preview = _image(tmp_path / "dl" / "preview.jpg", 600, 400)

    with pytest.raises(fetch.CenterFetchError, match="長邊"):
        fetch.install(SLUG, "punch-L04", 1, preview)


def test_a_portrait_original_is_refused(episode):
    _, tmp_path = episode
    portrait = _image(tmp_path / "dl" / "tall.jpg", 2000, 3000)

    with pytest.raises(fetch.CenterFetchError, match="直式"):
        fetch.install(SLUG, "punch-L04", 1, portrait)


def test_newest_download_finds_the_file_the_browser_just_saved(tmp_path):
    """修修的瀏覽器落點是 E:\\ 根目錄，不是 ~/Downloads——這裡用 tmp 驗行為。"""
    _image(tmp_path / "old.jpg", 3000, 2000)
    newest = _image(tmp_path / "new.jpg", 3200, 2100)
    import os

    os.utime(tmp_path / "old.jpg", (0, 0))

    assert fetch.newest_download(tmp_path).name == newest.name


def test_nothing_downloaded_says_so_instead_of_guessing(tmp_path):
    with pytest.raises(fetch.CenterFetchError, match="沒有"):
        fetch.newest_download(tmp_path)
