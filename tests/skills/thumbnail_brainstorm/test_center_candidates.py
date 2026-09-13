"""中央卡候選池——gate 上那排可以點的圖庫縮圖（修修 2026-08-29）。"""

from __future__ import annotations

import importlib.util
import json
import sys
from io import BytesIO
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


stage_center_candidates = _load("stage_center_candidates")


def _png_bytes(width: int, height: int) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (90, 90, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def staged(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    packaging = tmp_path / "packaging"
    packaging.mkdir()
    (packaging / "packages.json").write_text(
        json.dumps({"episode": "20260805 林之晨"}, ensure_ascii=False), encoding="utf-8"
    )
    return vault, packaging


def _result(item_id: str, *, preview: str = "https://cdn.example/p.png", **overrides) -> dict:
    row = {
        "preview_url": preview,
        "item_url": f"https://elements.envato.com/a-pampered-dog-{item_id}",
        "title": "golden retriever lying on a yellow sofa",
        "author": "LightFieldStudios",
        "query": "pampered dog on sofa",
    }
    row.update(overrides)
    return row


def test_staged_pool_records_where_each_candidate_came_from(monkeypatch, staged):
    """候選池就是後面 center_provenance 的來源——來歷跟著圖一起進來。"""
    vault, packaging = staged
    monkeypatch.setattr(stage_center_candidates, "_fetch", lambda url: _png_bytes(1600, 900))

    out = stage_center_candidates.stage(
        packaging, "punch-L04", "20260805-linzhichen", "20260805 林之晨", [_result("22KBKWG")]
    )

    pool = json.loads(out.read_text(encoding="utf-8"))
    assert pool["schema"] == "nakama.center_card_candidates.v1"
    row = pool["candidates"][0]
    assert row["candidate_id"] == "22KBKWG"
    assert row["source"].endswith("22KBKWG")
    assert row["query"] == "pampered dog on sofa"
    assert row["width"] == 1600 and row["height"] == 900
    staged_png = vault / row["preview_png"]
    assert staged_png.is_file()


def test_pool_lands_in_both_the_working_set_and_the_vault(monkeypatch, staged):
    """Bridge 只讀 vault，桌機端讀 working set——兩邊都要有（ADR-054 D10）。"""
    vault, packaging = staged
    monkeypatch.setattr(stage_center_candidates, "_fetch", lambda url: _png_bytes(1600, 900))

    stage_center_candidates.stage(
        packaging, "punch-L04", "20260805-linzhichen", "20260805 林之晨", [_result("22KBKWG")]
    )

    working = packaging / "center-candidates" / "punch-L04.json"
    mirrored = (
        vault
        / "Attachments"
        / "packaging"
        / "20260805-linzhichen"
        / "center-candidates"
        / "punch-L04.json"
    )
    assert working.read_text(encoding="utf-8") == mirrored.read_text(encoding="utf-8")


def test_portrait_results_are_dropped_before_they_reach_the_gate(monkeypatch, staged):
    """中央卡是橫的；直式在 attach 那關本來就會被擋，不該浪費修修一次點擊。"""
    _, packaging = staged
    sizes = {
        "https://cdn.example/tall.png": (1080, 1920),
        "https://cdn.example/wide.png": (1600, 900),
    }
    monkeypatch.setattr(stage_center_candidates, "_fetch", lambda url: _png_bytes(*sizes[url]))

    out = stage_center_candidates.stage(
        packaging,
        "punch-L04",
        "20260805-linzhichen",
        "20260805 林之晨",
        [
            _result("TALLONE", preview="https://cdn.example/tall.png"),
            _result("WIDEONE", preview="https://cdn.example/wide.png"),
        ],
    )

    pool = json.loads(out.read_text(encoding="utf-8"))
    assert [row["candidate_id"] for row in pool["candidates"]] == ["WIDEONE"]


def test_a_url_that_is_not_an_elements_item_page_is_refused():
    """品項 id 是來歷的錨點——湊不出 id 的網址不准收錄。"""
    with pytest.raises(SystemExit, match="Elements 品項網址"):
        stage_center_candidates.item_id("https://example.com/some-image")


def test_preview_downloads_must_be_https(monkeypatch, staged):
    _, packaging = staged
    with pytest.raises(SystemExit, match="只收 https"):
        stage_center_candidates.stage(
            packaging,
            "punch-L04",
            "20260805-linzhichen",
            "20260805 林之晨",
            [_result("22KBKWG", preview="http://cdn.example/p.png")],
        )


def test_a_second_search_round_adds_to_the_pool_instead_of_replacing_it(monkeypatch, staged):
    """補搜是常態，不是重來。

    修修 2026-08-29「來源的圖要多一點」，一條概念要搜 2–3 個說法、湊到二三十張，
    不可能一次搜完。舊版每跑一次就用這一批重建整個池子，第二輪補搜等於把第一輪
    全部丟掉——2026-09-12 三支長片各只有 9／8／6 張、正要補搜時才發現。
    """
    _vault, packaging = staged
    monkeypatch.setattr(stage_center_candidates, "_fetch", lambda url: _png_bytes(1600, 900))

    stage_center_candidates.stage(
        packaging, "punch-L04", "20260805-linzhichen", "20260805 林之晨", [_result("22KBKWG")]
    )
    out = stage_center_candidates.stage(
        packaging, "punch-L04", "20260805-linzhichen", "20260805 林之晨", [_result("ML6MZD5")]
    )

    pool = json.loads(out.read_text(encoding="utf-8"))
    assert [row["candidate_id"] for row in pool["candidates"]] == ["22KBKWG", "ML6MZD5"]


def test_the_same_item_twice_is_not_staged_twice(monkeypatch, staged):
    """同一張圖在兩個搜尋詞底下都出現是常事，gate 上不該看到兩份。"""
    _vault, packaging = staged
    monkeypatch.setattr(stage_center_candidates, "_fetch", lambda url: _png_bytes(1600, 900))

    stage_center_candidates.stage(
        packaging, "punch-L04", "20260805-linzhichen", "20260805 林之晨", [_result("22KBKWG")]
    )
    out = stage_center_candidates.stage(
        packaging,
        "punch-L04",
        "20260805-linzhichen",
        "20260805 林之晨",
        [_result("22KBKWG", query="另一個搜尋詞"), _result("ML6MZD5")],
    )

    pool = json.loads(out.read_text(encoding="utf-8"))
    assert [row["candidate_id"] for row in pool["candidates"]] == ["22KBKWG", "ML6MZD5"]
    # 既有那筆保留第一次的來歷，不被第二個搜尋詞覆寫。
    assert pool["candidates"][0]["query"] == "pampered dog on sofa"


def test_a_corrupt_pool_is_not_silently_rebuilt(monkeypatch, staged):
    """重建等於把既有候選丟掉——壞損要吵，不要自己修好。"""
    _vault, packaging = staged
    monkeypatch.setattr(stage_center_candidates, "_fetch", lambda url: _png_bytes(1600, 900))
    pool_path = packaging / "center-candidates" / "punch-L04.json"
    pool_path.parent.mkdir(parents=True, exist_ok=True)
    pool_path.write_text("{ 這不是合法 JSON", encoding="utf-8")

    with pytest.raises(SystemExit, match="讀不回來"):
        stage_center_candidates.stage(
            packaging, "punch-L04", "20260805-linzhichen", "20260805 林之晨", [_result("22KBKWG")]
        )
