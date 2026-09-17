"""agent 路徑的中央卡安裝（Step 4 自己配封面）。

這支腳本先前**一條測試都沒有**——所以 2026-09-17 的複審才會發現它對超寬素材完全
不設防，而且沒有任何東西會變紅。gate 路徑的對應測試在 `test_fetch_licensed_center.py`。
"""

from __future__ import annotations

import importlib.util
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


installer = _load("install_center_asset")

from shared import center_card  # noqa: E402

SLUG = "20260901-suyuxin"


def _image(path: Path, width: int, height: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), (100, 100, 100)).save(path)
    return path


def _install(src: Path, tmp_path: Path, **kwargs) -> Path:
    return installer.install(
        src,
        episode_slug=SLUG,
        episode_dir=tmp_path / "footage",
        cut_id="punch-L02",
        rank=3,
        vault_root=tmp_path / "vault",
        **kwargs,
    )


def test_an_ordinary_stock_photo_installs_at_card_size(tmp_path):
    out = _install(_image(tmp_path / "src" / "ok.jpg", 6000, 4000), tmp_path)

    with Image.open(out) as card:
        assert card.size == (center_card.CARD_W, center_card.CARD_H)
    assert (tmp_path / "footage" / "packaging" / out.name).is_file(), "工作目錄那份也要有"


def test_a_portrait_original_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="直式"):
        _install(_image(tmp_path / "src" / "tall.jpg", 1080, 1920), tmp_path)


def test_a_preview_sized_file_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="長邊"):
        _install(_image(tmp_path / "src" / "preview.jpg", 600, 400), tmp_path)


def test_a_panorama_that_would_lose_the_subject_is_refused(tmp_path):
    """橫式、長邊也夠大，但比例太扁——裁進卡片只留得下 37%，兩側 63% 被切在框外。

    這條是 2026-09-17 複審抓到的洞：gate 路徑補了留存率檢查、agent 路徑沒補，而
    `composition_receipt` 那道紅線量的是已裁好的檔案、恆為 1.0，所以這種素材一路
    放行到合成。
    """
    with pytest.raises(SystemExit, match="留得下"):
        _install(_image(tmp_path / "src" / "wide.jpg", 4000, 1000), tmp_path)


def test_the_anchor_cannot_buy_its_way_past_the_retention_guard(tmp_path):
    """`anchor` 只挪得動裁切窗的位置，救不了「窗本身就裝不下重點」。

    不釘這條的話，下一個人遇到擋門很容易以為「那就 --anchor left 吧」，而那只是把
    丟掉的 63% 換成另外 63%。
    """
    wide = _image(tmp_path / "src" / "wide.jpg", 4000, 1000)
    for anchor in ("center", "left", "right", "top"):
        with pytest.raises(SystemExit, match="留得下"):
            _install(wide, tmp_path, anchor=anchor)


def test_nothing_lands_when_the_source_is_refused(tmp_path):
    """被擋下來的素材不可以留下半個成品——vault 與工作目錄都要乾淨。"""
    with pytest.raises(SystemExit):
        _install(_image(tmp_path / "src" / "wide.jpg", 4000, 1000), tmp_path)

    assert not list((tmp_path / "vault").rglob("center-*")), "vault 不該有東西落地"
    assert not list((tmp_path / "footage").rglob("center-*")), "工作目錄不該有東西落地"
