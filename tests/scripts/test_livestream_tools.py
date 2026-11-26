"""`livestream_tools.py`：背景模式用的 sha256／canonical-json／timing-qc。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import wave
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "livestream_tools_under_test", _REPO_ROOT / "scripts" / "livestream_tools.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()

from agents.brook.podcast_subtitles.hashing import canonical_json_bytes  # noqa: E402


def _srt(*cues: tuple[str, str, str], bom: bool = False) -> str:
    body = "\n\n".join(f"{i}\n{a} --> {b}\n{t}" for i, (a, b, t) in enumerate(cues, 1))
    return ("﻿" if bom else "") + body + "\n"


def _wav(path: Path, seconds: float) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(1000)
        w.writeframes(b"\x00\x00" * int(seconds * 1000))
    return path


# ---- sha256 -------------------------------------------------------------------


def test_sha256_prints_raw_bytes_digest(tmp_path, capsys):
    f = tmp_path / "review.json"
    f.write_bytes(b'{"a":1}\n')
    assert mod.main(["sha256", str(f)]) == 0
    assert capsys.readouterr().out.strip() == hashlib.sha256(b'{"a":1}\n').hexdigest()


def test_sha256_missing_file_exit_2(tmp_path):
    assert mod.main(["sha256", str(tmp_path / "none.json")]) == 2


# ---- canonical-json -----------------------------------------------------------


def test_canonical_json_rewrites_in_place_and_prints_new_digest(tmp_path, capsys):
    episode = tmp_path / "2026-11-19 18.52.50 AI Agent"
    work = episode / "subtitle-work"
    work.mkdir(parents=True)
    f = work / "audit-a.json"
    value = {"worker_id": "cue-worker-a", "accepted": True, "items": [{"b": 2, "a": "中文"}]}
    f.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert mod.main(["canonical-json", str(f), "--episode", str(episode)]) == 0

    expected = canonical_json_bytes(value)
    assert f.read_bytes() == expected
    assert not f.read_bytes().endswith(b"\n")
    assert capsys.readouterr().out.strip() == hashlib.sha256(expected).hexdigest()
    assert [p.name for p in work.iterdir()] == ["audit-a.json"]  # 沒留暫存檔


def test_canonical_json_rejects_file_outside_episode(tmp_path):
    episode = tmp_path / "episode"
    episode.mkdir()
    outside = tmp_path / "other.json"
    outside.write_text('{"a": 1}', encoding="utf-8")
    assert mod.main(["canonical-json", str(outside), "--episode", str(episode)]) == 2
    assert outside.read_text(encoding="utf-8") == '{"a": 1}'


def test_canonical_json_rejects_duplicate_keys_and_bad_json(tmp_path):
    episode = tmp_path / "episode"
    episode.mkdir()
    dup = episode / "dup.json"
    dup.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    assert mod.main(["canonical-json", str(dup), "--episode", str(episode)]) == 1
    assert dup.read_text(encoding="utf-8") == '{"a": 1, "a": 2}'

    bad = episode / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert mod.main(["canonical-json", str(bad), "--episode", str(episode)]) == 1


# ---- timing-qc ----------------------------------------------------------------


def test_timing_qc_clean_srt_with_bom_and_wav(tmp_path, capsys):
    srt = tmp_path / "memo.srt"
    srt.write_text(
        _srt(
            ("00:00:00,000", "00:00:02,000", "開場"),
            ("00:00:02,000", "00:00:04,500", "第二句"),
            bom=True,
        ),
        encoding="utf-8",
    )
    wav = _wav(tmp_path / "normalized.wav", 5)

    assert mod.main(["timing-qc", str(srt), "--wav", str(wav)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "ok": True,
        "cue_count": 2,
        "non_positive_duration_count": 0,
        "overlap_count": 0,
        "out_of_audio_count": 0,
        "last_end_ms": 4500,
        "audio_duration_ms": 5000,
        "parse_errors": [],
    }


def test_timing_qc_flags_zero_negative_overlap_and_past_audio(tmp_path, capsys):
    srt = tmp_path / "memo.srt"
    srt.write_text(
        _srt(
            ("00:00:00,000", "00:00:02,000", "一"),
            ("00:00:01,500", "00:00:03,000", "重疊"),
            ("00:00:03,000", "00:00:03,000", "零長度"),
            ("00:00:04,000", "00:00:03,900", "負長度"),
            ("00:00:04,000", "00:00:06,000", "超出音檔"),
        ),
        encoding="utf-8",
    )
    wav = _wav(tmp_path / "normalized.wav", 5)

    assert mod.main(["timing-qc", str(srt), "--wav", str(wav)]) == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] is False
    assert summary["non_positive_duration_count"] == 2
    assert summary["overlap_count"] >= 1
    assert summary["out_of_audio_count"] == 1


def test_timing_qc_without_wav_skips_audio_check(tmp_path, capsys):
    srt = tmp_path / "memo.srt"
    srt.write_text(_srt(("00:00:00,000", "00:59:00,000", "長句")), encoding="utf-8")
    assert mod.main(["timing-qc", str(srt)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["out_of_audio_count"] is None
    assert summary["audio_duration_ms"] is None


def test_timing_qc_reports_malformed_blocks(tmp_path, capsys):
    srt = tmp_path / "memo.srt"
    srt.write_text(
        "1\n00:00:00,000 -> 00:00:01,000\n壞掉\n\n2\n00:00:01,000 --> 00:00:02,000\n\n",
        encoding="utf-8",
    )
    assert mod.main(["timing-qc", str(srt)]) == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["parse_errors"] == ["cue 1: 時間行格式不對", "cue 2: 沒有文字"]
