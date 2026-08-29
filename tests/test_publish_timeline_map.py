"""成品 render 的 timeline 由 Release 說了算（安靜出錯片的護欄）。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.usopp.publish_timeline import (  # noqa: E402
    MAP_RELPATH,
    SCHEMA,
    PublishTimelineError,
    canonical_timeline_from_transactions,
    load_timeline_map,
    resolve_target,
    verify_duration,
)

MAP = {
    "schema": SCHEMA,
    "episode": "20260805 林之晨",
    "cuts": {
        "punch-L04": {
            "timeline": "long3-fresh-20260828-r4-base",
            "release_id": "release-af65a1d7a2ac611eb78be493",
            "release_cut_id": "long3-fresh-20260828-r4",
            "expected_duration_sec": 492.309333,
        }
    },
}


def _write_map(root: Path, payload: dict) -> Path:
    path = root / MAP_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_missing_map_returns_none_so_old_episodes_keep_working(tmp_path):
    assert load_timeline_map(tmp_path) is None


def test_foreign_schema_is_refused(tmp_path):
    _write_map(tmp_path, {**MAP, "schema": "something.else.v9"})
    with pytest.raises(PublishTimelineError):
        load_timeline_map(tmp_path)


def test_resolve_target_carries_the_release_side_id(tmp_path):
    _write_map(tmp_path, MAP)
    target = resolve_target(load_timeline_map(tmp_path), "punch-L04")
    assert target.timeline == "long3-fresh-20260828-r4-base"
    # packaging 叫它 punch-L04，Release 叫它 long3-fresh-…；對應表是唯一把
    # 這兩個 id 綁在一起的地方。
    assert target.release_cut_id == "long3-fresh-20260828-r4"


def test_unregistered_cut_fails_instead_of_falling_back_to_the_guess(tmp_path):
    """回退到 winners.json 的名字猜，正是會 render 出舊剪輯的那條路。"""
    _write_map(tmp_path, MAP)
    with pytest.raises(PublishTimelineError, match="value-L02"):
        resolve_target(load_timeline_map(tmp_path), "value-L02")


def test_entry_missing_a_field_fails_loud(tmp_path):
    broken = {**MAP, "cuts": {"punch-L04": {"timeline": "x", "release_id": "y"}}}
    _write_map(tmp_path, broken)
    with pytest.raises(PublishTimelineError, match="expected_duration_sec"):
        resolve_target(load_timeline_map(tmp_path), "punch-L04")


def test_duration_within_container_rounding_is_accepted(tmp_path):
    """preview mp4 與 timeline frame 數本來就差一兩個 frame（實測 592.900/592.967）。"""
    _write_map(tmp_path, MAP)
    target = resolve_target(load_timeline_map(tmp_path), "punch-L04")
    verify_duration(target, 492.333)


def test_the_actual_20260805_mismatch_is_caught(tmp_path):
    """真實事故：長3 的舊 timeline 是 260s，Release 是 492s，舊碼會照 render。"""
    _write_map(tmp_path, MAP)
    target = resolve_target(load_timeline_map(tmp_path), "punch-L04")
    with pytest.raises(PublishTimelineError, match="260"):
        verify_duration(target, 260.0)


def test_canonical_timeline_read_back_from_a_committed_transaction(tmp_path):
    (tmp_path / "resolve-368cb9c9.json").write_text(
        json.dumps(
            {
                "schema": "nakama.finished-cut-resolve-transaction.v1",
                "payload": {
                    "status": "committed",
                    "transaction_receipt_id": "resolve-receipt-278cda15",
                    "canonical": {"name": "long3-fresh-20260828-r4-base"},
                },
            }
        ),
        encoding="utf-8",
    )
    assert (
        canonical_timeline_from_transactions(tmp_path, "resolve-receipt-278cda15")
        == "long3-fresh-20260828-r4-base"
    )


def test_uncommitted_transaction_is_not_authority(tmp_path):
    (tmp_path / "resolve-abandoned.json").write_text(
        json.dumps(
            {
                "payload": {
                    "status": "rolled_back",
                    "transaction_receipt_id": "resolve-receipt-278cda15",
                    "canonical": {"name": "half-written-base"},
                }
            }
        ),
        encoding="utf-8",
    )
    assert canonical_timeline_from_transactions(tmp_path, "resolve-receipt-278cda15") is None


def test_migrated_release_has_no_transaction_to_read(tmp_path):
    assert canonical_timeline_from_transactions(tmp_path / "nope", "any") is None
