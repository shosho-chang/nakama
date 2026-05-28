"""Tests for ADR-038 §D7 storyboard LCS diff."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

from agents.foundry import edit_log
from agents.foundry.storyboard_diff import diff_storyboards, format_diff


def _beat(bid: int, layout: str = "full_broll", broll: str = "stock") -> dict:
    return {"beat_id": bid, "layout": layout, "broll": broll}


def test_identical_storyboards_all_keep():
    sb = [_beat(1), _beat(2), _beat(3)]
    rows = diff_storyboards(sb, [_beat(1), _beat(2), _beat(3)])
    assert [op for op, _, _ in rows] == ["=", "=", "="]
    assert [bid for _, bid, _ in rows] == [1, 2, 3]


def test_append_beat_emits_plus_at_tail():
    before = [_beat(1), _beat(2)]
    after = [_beat(1), _beat(2), _beat(3)]
    rows = diff_storyboards(before, after)
    assert [op for op, _, _ in rows] == ["=", "=", "+"]
    assert rows[-1][1] == 3


def test_delete_middle_beat_emits_minus():
    before = [_beat(1), _beat(2), _beat(3)]
    after = [_beat(1), _beat(3)]
    rows = diff_storyboards(before, after)
    ops = [op for op, _, _ in rows]
    # beat_id 2 disappears between two keeps
    assert ops == ["=", "-", "="]
    assert rows[1][1] == 2


def test_modify_same_beat_id_emits_minus_then_plus():
    before = [_beat(1), _beat(2, layout="full_broll"), _beat(3)]
    after = [_beat(1), _beat(2, layout="picture_in_picture"), _beat(3)]
    rows = diff_storyboards(before, after)
    ops_ids = [(op, bid) for op, bid, _ in rows]
    # Same beat_id 2 with different content → '-' then '+' at the same position.
    assert ops_ids == [("=", 1), ("-", 2), ("+", 2), ("=", 3)]
    # The '-' carries the old content, '+' carries the new.
    assert rows[1][2]["layout"] == "full_broll"
    assert rows[2][2]["layout"] == "picture_in_picture"


def test_reorder_beats_treated_as_remove_and_add():
    # Pure reorder: LCS picks the longest common subsequence; the rest become +/-.
    before = [_beat(1), _beat(2), _beat(3)]
    after = [_beat(3), _beat(1), _beat(2)]
    rows = diff_storyboards(before, after)
    ops = [op for op, _, _ in rows]
    # LCS of (1,2,3) and (3,1,2) has length 2, so exactly one '-' and one '+'.
    assert ops.count("-") == 1
    assert ops.count("+") == 1
    assert ops.count("=") == 2
    # Order preserved: every emitted beat_id sequence reconstructs the inputs.
    kept_from_before = [bid for op, bid, _ in rows if op in {"=", "-"}]
    kept_from_after = [bid for op, bid, _ in rows if op in {"=", "+"}]
    assert kept_from_before == [1, 2, 3]
    assert kept_from_after == [3, 1, 2]


def test_empty_before_all_plus():
    after = [_beat(1), _beat(2)]
    rows = diff_storyboards([], after)
    assert [op for op, _, _ in rows] == ["+", "+"]


def test_empty_after_all_minus():
    before = [_beat(1), _beat(2)]
    rows = diff_storyboards(before, [])
    assert [op for op, _, _ in rows] == ["-", "-"]


def test_format_diff_renders_per_row():
    rows = diff_storyboards([_beat(1)], [_beat(1, layout="picture_in_picture")])
    text = format_diff(rows)
    assert "- beat_id=1" in text
    assert "+ beat_id=1" in text
    assert "picture_in_picture" in text


# --- edit_log enrichment -----------------------------------------------------


def test_edit_log_includes_diff_when_storyboards_provided(tmp_path, monkeypatch):
    monkeypatch.setattr(edit_log, "_LOG_DIR", tmp_path)
    before_sb = [_beat(1), _beat(2)]
    after_sb = [_beat(1), _beat(2, layout="picture_in_picture")]
    edit_log.append_entry(
        episode_id="ep-test",
        beat_id=2,
        action="replan",
        before={"layout": "full_broll"},
        after={"layout": "picture_in_picture"},
        user_note="more focus on face",
        storyboard_before=before_sb,
        storyboard_after=after_sb,
    )
    entries = edit_log.read_entries("ep-test")
    assert len(entries) == 1
    diff = entries[0]["diff"]
    assert diff is not None
    ops = [row[0] for row in diff]
    assert ops == ["=", "-", "+"]


def test_edit_log_diff_is_none_when_storyboards_omitted(tmp_path, monkeypatch):
    monkeypatch.setattr(edit_log, "_LOG_DIR", tmp_path)
    edit_log.append_entry(
        episode_id="ep-test",
        beat_id=2,
        action="replan",
        before={"layout": "full_broll"},
        after={"layout": "picture_in_picture"},
    )
    entries = edit_log.read_entries("ep-test")
    assert entries[0]["diff"] is None


def test_edit_log_reader_backward_compat_for_pre_d7_entries(tmp_path, monkeypatch):
    """Entries written before ADR-038 §D7 lack the ``diff`` field; reader must
    fill it in as ``None`` rather than KeyError."""
    monkeypatch.setattr(edit_log, "_LOG_DIR", tmp_path)
    legacy = {
        "timestamp": "2026-05-26T10:32:14+08:00",
        "episode_id": "ep-legacy",
        "beat_id": 7,
        "action": "replan",
        "before": {"layout": "full_broll"},
        "after": {"layout": "picture_in_picture"},
        "user_note": "x",
        # NO "diff" key — pre-D7 format
    }
    (tmp_path / "ep-legacy.jsonl").write_text(
        json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    entries = edit_log.read_entries("ep-legacy")
    assert entries[0]["diff"] is None


# --- CLI smoke ---------------------------------------------------------------


def test_diff_cli_subcommand_runs(tmp_path):
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text(yaml.dump([_beat(1), _beat(2)], allow_unicode=True), encoding="utf-8")
    b.write_text(
        yaml.dump([_beat(1), _beat(2, layout="picture_in_picture")], allow_unicode=True),
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agents.foundry",
            "--episode",
            "unused",
            "diff",
            str(a),
            str(b),
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "= beat_id=1" in out
    assert "- beat_id=2" in out
    assert "+ beat_id=2" in out
