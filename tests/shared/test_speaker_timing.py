"""shared.speaker_timing — speaker-timing token loader (legacy words.json / ADR-063 Memo)."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

import shared.speaker_timing as speaker_timing
from shared.speaker_timing import load_speaker_timing_tokens


def _memo_selection(ledger_path):
    return SimpleNamespace(
        mode="memo-dual-audit-v1",
        handoff=SimpleNamespace(release_ledger_path=ledger_path),
    )


def test_legacy_words_json_wins_without_touching_memo(tmp_path, monkeypatch):
    (tmp_path / "subs").mkdir()
    words = [{"word": "嗨", "start": 0.5, "end": 0.9}]
    (tmp_path / "subs" / "words.json").write_text(
        json.dumps({"words": words}, ensure_ascii=False), encoding="utf-8"
    )

    def _boom(self, episode_dir):
        raise AssertionError("有 words.json 時不應開 Memo release")

    monkeypatch.setattr(speaker_timing.Stage5SubtitleRequest, "open", _boom)

    assert load_speaker_timing_tokens(tmp_path) == words


def test_speaker_tokens_use_hash_bound_memo_release_without_legacy(tmp_path, monkeypatch):
    evidence = {
        "contract": "memo-recognition-evidence-v1",
        "normalized_audio_sha256": "audio-sha",
        "tokens": [
            {"text": "第一句", "start_ms": 1000, "end_ms": 2000},
            {"text": "第二句", "start_ms": 2000, "end_ms": 3500},
        ],
    }
    evidence_raw = json.dumps(evidence, ensure_ascii=False).encode()
    evidence_path = tmp_path / "subtitle-v2" / "memo-recognition.v1.json"
    evidence_path.parent.mkdir()
    evidence_path.write_bytes(evidence_raw)
    ledger = {
        "normalized_audio_sha256": "audio-sha",
        "inputs": {
            "memo_recognition_evidence": {
                "path": "subtitle-v2/memo-recognition.v1.json",
                "sha256": hashlib.sha256(evidence_raw).hexdigest(),
                "size_bytes": len(evidence_raw),
            }
        },
    }
    ledger_path = tmp_path / "subtitle-release" / "memo-dual-audit-v1" / "release-ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    monkeypatch.setattr(
        speaker_timing.Stage5SubtitleRequest,
        "open",
        lambda self, episode_dir: _memo_selection(ledger_path),
    )

    assert load_speaker_timing_tokens(tmp_path) == [
        {"word": "第一句", "start": 1.0, "end": 2.0},
        {"word": "第二句", "start": 2.0, "end": 3.5},
    ]


def test_speaker_tokens_reject_tampered_memo_evidence(tmp_path, monkeypatch):
    evidence_path = tmp_path / "memo.json"
    evidence_path.write_text("{}", encoding="utf-8")
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "normalized_audio_sha256": "audio-sha",
                "inputs": {
                    "memo_recognition_evidence": {
                        "path": "memo.json",
                        "sha256": "0" * 64,
                        "size_bytes": 2,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        speaker_timing.Stage5SubtitleRequest,
        "open",
        lambda self, episode_dir: _memo_selection(ledger_path),
    )

    with pytest.raises(SystemExit, match="bytes"):
        load_speaker_timing_tokens(tmp_path)


def test_no_words_json_and_no_memo_release_fails_loud(tmp_path, monkeypatch):
    monkeypatch.setattr(
        speaker_timing.Stage5SubtitleRequest,
        "open",
        lambda self, episode_dir: SimpleNamespace(mode="legacy", handoff=SimpleNamespace()),
    )

    with pytest.raises(SystemExit, match="speaker timing evidence"):
        load_speaker_timing_tokens(tmp_path)
