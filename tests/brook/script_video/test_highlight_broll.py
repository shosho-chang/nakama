from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agents.brook.script_video.highlight_broll import (
    BrollContractError,
    build_broll_receipt,
    verify_broll_receipt,
)

MASTER = {"contract": "podcast-editorial-master-v1", "content_hash": "a" * 64}


def _provenance(index: int) -> dict[str, str]:
    return {
        "source_url": f"https://stock.example.test/videos/{index}",
        "license_id": f"license-{index}",
        "acquired_at": "2026-08-22T10:00:00+08:00",
    }


@pytest.fixture(autouse=True)
def _stub_probe_outside_media_auth_tests(request, monkeypatch):
    if request.node.name in {
        "test_stock_video_rejects_text_bytes_renamed_as_mp4",
        "test_real_stock_video_records_stream_metadata",
    }:
        return
    monkeypatch.setattr(
        "agents.brook.script_video.highlight_broll.probe_stock_video",
        lambda _path: {
            "duration_seconds": 5.0,
            "video_streams": [
                {"index": 0, "codec_name": "h264", "width": 16, "height": 16}
            ],
        },
    )


def _asset(root: Path, slug: str, body: bytes) -> None:
    path = root / "assets" / "broll" / f"{slug}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _items(count: int) -> list[dict]:
    return [
        {
            "kind": "video",
            "slug": f"asset-{index}",
            "t0": 10.0 + index * 10,
            "t1": 14.0 + index * 10,
            "provenance": _provenance(index),
        }
        for index in range(count)
    ]


@pytest.mark.parametrize("count", [0, 1, 2])
def test_long_highlight_rejects_fewer_than_three_stock_video_events(
    tmp_path: Path, count: int
) -> None:
    items = _items(count) + [
        {"kind": "guest-namecard", "slug": "guest-namecard", "t0": 1.0, "t1": 5.0},
        {"kind": "concept", "slug": "chapter", "t0": 5.0, "t1": 8.0},
    ]
    for index in range(count):
        _asset(tmp_path, f"asset-{index}", f"asset-{index}".encode())

    with pytest.raises(BrollContractError, match=rf"需要至少 3 個 Stock Video.*目前 {count} 個"):
        build_broll_receipt(tmp_path, "value-L01", "long", items, MASTER)


def test_long_highlight_accepts_three_stock_videos_and_records_hashes(tmp_path: Path) -> None:
    items = _items(3) + [
        {"kind": "guest-namecard", "slug": "guest-namecard", "t0": 1.0, "t1": 5.0}
    ]
    for index in range(3):
        _asset(tmp_path, f"asset-{index}", f"asset-{index}".encode())

    receipt = build_broll_receipt(tmp_path, "value-L01", "long", items, MASTER)

    assert receipt["stock_video_count"] == 3
    assert [row["slug"] for row in receipt["stock_videos"]] == [
        "asset-0",
        "asset-1",
        "asset-2",
    ]
    assert receipt["stock_videos"][0]["asset"]["path"] == "assets/broll/asset-0.mp4"
    assert receipt["stock_videos"][0]["asset"]["sha256"] == hashlib.sha256(
        b"asset-0"
    ).hexdigest()
    assert receipt["editorial_master_lineage"] == MASTER


def test_stock_video_rejects_text_bytes_renamed_as_mp4(tmp_path: Path) -> None:
    items = _items(3)
    for index in range(3):
        _asset(tmp_path, f"asset-{index}", f"not-a-video-{index}".encode())

    with pytest.raises(BrollContractError, match="video stream|ffprobe|影片"):
        build_broll_receipt(tmp_path, "value-L01", "long", items, MASTER)


def test_real_stock_video_records_stream_metadata(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg, "test environment must provide ffmpeg"
    items = _items(3)
    colors = ("ff0000", "00ff00", "0000ff")
    for index in range(3):
        path = tmp_path / "assets" / "broll" / f"asset-{index}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x{colors[index]}:s=16x16:d=0.2:r=10",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
        )

    receipt = build_broll_receipt(tmp_path, "value-L01", "long", items, MASTER)

    media = receipt["stock_videos"][0]["asset"]["media"]
    assert media["duration_seconds"] > 0
    assert media["video_streams"][0]["codec_name"] == "h264"
    assert media["video_streams"][0]["width"] == 16


def test_stock_video_requires_license_provenance(tmp_path: Path) -> None:
    items = _items(3)
    items[0].pop("provenance")
    for index in range(3):
        _asset(tmp_path, f"asset-{index}", f"asset-{index}".encode())

    with pytest.raises(BrollContractError, match="缺少 provenance"):
        build_broll_receipt(tmp_path, "value-L01", "long", items, MASTER)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("source_url", "source_url"),
        ("acquired_at", "acquired_at"),
        ("license_id", "license_url.*terms_url.*license_id"),
    ],
)
def test_stock_video_requires_each_provenance_dimension(
    tmp_path: Path, field: str, message: str
) -> None:
    items = _items(3)
    items[0]["provenance"].pop(field)
    for index in range(3):
        _asset(tmp_path, f"asset-{index}", f"asset-{index}".encode())

    with pytest.raises(BrollContractError, match=message):
        build_broll_receipt(tmp_path, "value-L01", "long", items, MASTER)


def test_verify_rejects_provenance_tamper(tmp_path: Path) -> None:
    items = _items(3)
    for index in range(3):
        _asset(tmp_path, f"asset-{index}", f"asset-{index}".encode())
    receipt = build_broll_receipt(tmp_path, "value-L01", "long", items, MASTER)
    path = tmp_path / "highlights" / "tighten" / "value-L01_broll_materialization.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")
    items[0]["provenance"]["license_id"] = "tampered-license"

    with pytest.raises(BrollContractError, match="receipt.*不一致"):
        verify_broll_receipt(tmp_path, "value-L01", "long", items, MASTER)


def test_photo_and_generated_treatments_do_not_count_as_stock_video(tmp_path: Path) -> None:
    items = [
        {"kind": "photo", "slug": f"photo-{index}", "t0": 10 + index * 10, "t1": 14 + index * 10}
        for index in range(3)
    ] + [
        {"kind": "guest-namecard", "slug": "guest-namecard", "t0": 1.0, "t1": 5.0},
        {"kind": "concept", "slug": "chapter", "t0": 5.0, "t1": 8.0},
        {"kind": "badge", "slug": "brand", "t0": 8.0, "t1": 16.0},
    ]
    for index in range(3):
        _asset(tmp_path, f"photo-{index}", f"photo-{index}".encode())

    with pytest.raises(BrollContractError, match="目前 0 個"):
        build_broll_receipt(tmp_path, "value-L01", "long", items, MASTER)


def test_duplicate_asset_content_is_rejected_even_with_different_slugs(tmp_path: Path) -> None:
    items = _items(3)
    for index in range(3):
        _asset(tmp_path, f"asset-{index}", b"same" if index < 2 else b"other")

    with pytest.raises(BrollContractError, match="重複素材"):
        build_broll_receipt(tmp_path, "value-L01", "long", items, MASTER)


def test_missing_stock_video_file_is_rejected(tmp_path: Path) -> None:
    items = _items(3)
    _asset(tmp_path, "asset-0", b"asset-0")
    _asset(tmp_path, "asset-2", b"asset-2")

    with pytest.raises(BrollContractError, match="asset-1.*目前 0 個"):
        build_broll_receipt(tmp_path, "value-L01", "long", items, MASTER)


@pytest.mark.parametrize("slug", ["../escape", "folder/file", "wild*", "wild?"])
def test_asset_slug_path_escape_and_glob_metacharacters_are_rejected(
    tmp_path: Path, slug: str
) -> None:
    items = _items(3)
    items[0]["slug"] = slug
    for index in range(1, 3):
        _asset(tmp_path, f"asset-{index}", f"asset-{index}".encode())

    with pytest.raises(BrollContractError, match="slug"):
        build_broll_receipt(tmp_path, "value-L01", "long", items, MASTER)


def test_verify_rejects_asset_hash_drift(tmp_path: Path) -> None:
    items = _items(3)
    for index in range(3):
        _asset(tmp_path, f"asset-{index}", f"asset-{index}".encode())
    receipt = build_broll_receipt(tmp_path, "value-L01", "long", items, MASTER)
    path = tmp_path / "highlights" / "tighten" / "value-L01_broll_materialization.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")
    _asset(tmp_path, "asset-1", b"drift")

    with pytest.raises(BrollContractError, match="receipt.*不一致"):
        verify_broll_receipt(tmp_path, "value-L01", "long", items, MASTER)
