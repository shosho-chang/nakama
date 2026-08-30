"""shared.shortform_broll — 短片素材層 gate（ADR-067）。

授權照驗（收據 ＋ SHA-256），語意換成逐字稿錨定，另加短片專屬的直式要求。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from shared.shortform_broll import (
    ACQUISITION_CONTRACT,
    CONTRACT,
    ShortformBrollError,
    verify_shortform_broll,
)

CUES = [
    {"n": 7, "t0": 17.425, "t1": 18.191, "text": "不只人類愛玩"},
    {"n": 8, "t0": 18.191, "t1": 19.118, "text": "狗也愛玩"},
    {"n": 9, "t0": 19.118, "t1": 21.251, "text": "甚至連鳥都愛玩"},
]
LINEAGE = {"contract": "podcast-editorial-master-v1", "episode_id": "ep"}


def _make_clip(path: Path, width: int, height: int, seconds: float = 4.0) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:  # pragma: no cover - 環境沒有 ffmpeg 就跳過整組
        pytest.skip("需要 ffmpeg 產生測試素材")
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={width}x{height}:rate=30:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            str(path),
            "-y",
        ],
        check=True,
    )


def _stage(tmp_path: Path, slug: str, *, width: int, height: int, receipt: bool = True) -> Path:
    assets = tmp_path / "assets" / "broll"
    assets.mkdir(parents=True, exist_ok=True)
    media = assets / f"{slug}.mp4"
    _make_clip(media, width, height)
    if receipt:
        (assets / f"{slug}.acquisition.json").write_text(
            json.dumps(
                {
                    "contract": ACQUISITION_CONTRACT,
                    "asset_id": slug,
                    "provider": "pexels",
                    "provider_item_id": "1",
                    "source_url": "https://www.pexels.com/video/1/",
                    "license": "Pexels license: https://www.pexels.com/license/",
                    "source_class": "licensed_stock",
                    "original_media": {
                        "bytes": media.stat().st_size,
                        "path": f"assets/broll/{slug}.mp4",
                        "sha256": hashlib.sha256(media.read_bytes()).hexdigest(),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return media


def _item(slug: str = "birds", **over) -> dict:
    item = {
        "kind": "video",
        "slug": slug,
        "t0": 19.2,
        "t1": 21.2,
        "source_cues": [9],
        "src_in": 0.0,
    }
    item.update(over)
    return item


def _verify(tmp_path: Path, items: list[dict], **kw) -> dict:
    return verify_shortform_broll(
        tmp_path, "punch-S02", items, editorial_master_lineage=LINEAGE, cues=CUES, **kw
    )


def test_vertical_stock_with_receipt_passes_and_is_stamped(tmp_path):
    _stage(tmp_path, "birds", width=360, height=640)
    items = [_item()]
    receipt = _verify(tmp_path, items)
    assert receipt["contract"] == CONTRACT
    assert receipt["stock_video_count"] == 1
    projection = items[0]["visual_materialization"]
    assert projection["authority"] == CONTRACT
    assert projection["cue_ids"] == [9]
    assert projection["quote"] == "甚至連鳥都愛玩"
    assert projection["media"]["path"] == "assets/broll/birds.mp4"
    assert projection["provenance"]["provider"] == "pexels"


def test_landscape_stock_is_refused(tmp_path):
    """修修 2026-08-30：短片的素材是要直式的。"""
    _stage(tmp_path, "birds", width=640, height=360)
    with pytest.raises(ShortformBrollError, match="直式"):
        _verify(tmp_path, [_item()])


def test_missing_acquisition_receipt_is_refused(tmp_path):
    _stage(tmp_path, "birds", width=360, height=640, receipt=False)
    with pytest.raises(ShortformBrollError, match="授權收據"):
        _verify(tmp_path, [_item()])


def test_tampered_media_is_refused(tmp_path):
    media = _stage(tmp_path, "birds", width=360, height=640)
    media.write_bytes(media.read_bytes() + b"\x00")
    with pytest.raises(ShortformBrollError, match="SHA-256"):
        _verify(tmp_path, [_item()])


def test_placement_must_sit_inside_its_source_cues(tmp_path):
    """素材必須對齊當下那句話，不是對齊整支的大主題。"""
    _stage(tmp_path, "birds", width=360, height=640)
    with pytest.raises(ShortformBrollError, match="沒有包在 source_cues"):
        _verify(tmp_path, [_item(t0=15.0, t1=17.0)])


def test_source_cues_are_required(tmp_path):
    _stage(tmp_path, "birds", width=360, height=640)
    with pytest.raises(ShortformBrollError, match="缺 source_cues"):
        _verify(tmp_path, [_item(source_cues=[])])


def test_broll_may_not_cover_a_punch_zoom(tmp_path):
    """SKILL Step 9：衝突時縮短 punch 讓位 footage——所以這裡要你改企劃。"""
    _stage(tmp_path, "birds", width=360, height=640)
    with pytest.raises(ShortformBrollError, match="punch"):
        _verify(tmp_path, [_item()], punches=[{"t0": 19.0, "t1": 22.0}])


def test_broll_may_not_cover_the_split_opener(tmp_path):
    _stage(tmp_path, "opener", width=360, height=640)
    with pytest.raises(ShortformBrollError, match="開場上下分割"):
        _verify(
            tmp_path,
            [_item("opener", t0=17.5, t1=19.0, source_cues=[7, 8])],
            opener_sec=19.0,
        )


def test_items_may_not_overlap_each_other(tmp_path):
    _stage(tmp_path, "a", width=360, height=640)
    _stage(tmp_path, "b", width=360, height=640)
    with pytest.raises(ShortformBrollError, match="重疊"):
        _verify(
            tmp_path,
            [_item("a", t0=19.2, t1=21.2), _item("b", t0=20.0, t1=21.2)],
        )


def test_unsupported_kind_is_refused_not_silently_passed(tmp_path):
    with pytest.raises(ShortformBrollError, match="還不在短片素材層裡"):
        _verify(tmp_path, [{"kind": "sticker", "slug": "s", "t0": 19.2, "t1": 21.2}])


def test_badge_is_structural_and_skipped(tmp_path):
    receipt = _verify(tmp_path, [{"kind": "badge", "slug": "brand-badge-8s", "t0": 0.0, "t1": 7.4}])
    assert receipt["stock_video_count"] == 0


def test_src_in_may_not_run_past_the_asset(tmp_path):
    _stage(tmp_path, "birds", width=360, height=640)
    with pytest.raises(ShortformBrollError, match="超過素材長度"):
        _verify(tmp_path, [_item(src_in=3.5)])


def test_receipt_is_stable_across_repeated_verification(tmp_path):
    """物化前後會再驗一次並比對——蓋章必須是冪等的，否則永遠不相等。"""
    _stage(tmp_path, "birds", width=360, height=640)
    items = [_item()]
    first = _verify(tmp_path, items)
    second = _verify(tmp_path, items)
    assert first == second
