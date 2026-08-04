"""publish_upload 純函數測試（真上傳靠修修 approve 後首跑 UAT）。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from publish_upload import build_insert_body, to_utc_iso  # noqa: E402


def test_to_utc_iso_converts_taipei():
    assert to_utc_iso("2026-08-10T20:00:00+08:00") == "2026-08-10T12:00:00Z"


def test_to_utc_iso_rejects_naive():
    """排程是硬承諾——缺時區的時間不能用猜的。"""
    with pytest.raises(ValueError):
        to_utc_iso("2026-08-10T20:00:00")


TARGET = {
    "title": "腦科學家的腦腐自救 3 步",
    "description": "hook…\n\n⏱ 00:00 開場",
    "publish_at": "2026-08-10T20:00:00+08:00",
}
RELEASE = {"cut_id": "punch-L5"}


def test_build_insert_body_private_with_schedule():
    body = build_insert_body(TARGET, RELEASE)
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"] == "2026-08-10T12:00:00Z"
    assert body["snippet"]["defaultAudioLanguage"] == "zh-TW"


def test_build_insert_body_no_schedule_stays_private():
    body = build_insert_body({**TARGET, "publish_at": None}, RELEASE)
    assert "publishAt" not in body["status"]
    assert body["status"]["privacyStatus"] == "private"


def test_build_insert_body_requires_copy():
    """title/description 未回填 = Slice 2 沒跑——不拿工作代號充當發布標題。"""
    with pytest.raises(ValueError):
        build_insert_body({"title": None, "description": None}, RELEASE)
