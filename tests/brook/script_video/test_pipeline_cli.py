"""Pipeline CLI provenance + cleanup subcommand tests (ADR-050 D3/D4).

Covers:
- _DATA_ROOT is repo-root anchored (cwd-independent)
- _require_episode fails loud with creation guidance when episode.yaml missing
- _record_stage stamps ISO timestamps into episode.yaml stages: map
- cleanup subcommand: clap fixture WAV → out/cleanup.fcpxml ripple-delete timeline
"""

from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from agents.brook.script_video import pipeline

_CLAP_FIXTURE = Path(__file__).parents[2] / "fixtures" / "script_video" / "clap_marker_audio.wav"


# ---------------------------------------------------------------------------
# _DATA_ROOT anchoring
# ---------------------------------------------------------------------------


def test_data_root_is_repo_anchored() -> None:
    """cwd-relative data root silently writes to the wrong place (ADR-050 D4)."""
    assert pipeline._DATA_ROOT.is_absolute()
    repo_root = Path(pipeline.__file__).resolve().parents[3]
    assert pipeline._DATA_ROOT == repo_root / "data" / "script_video"
    assert (repo_root / "pyproject.toml").exists(), "parents[3] must be the repo root"


# ---------------------------------------------------------------------------
# _require_episode
# ---------------------------------------------------------------------------


def test_require_episode_fails_loud_without_episode_yaml(tmp_path: Path) -> None:
    ep_dir = tmp_path / "ep-x"
    ep_dir.mkdir()
    with pytest.raises(SystemExit, match="episode.yaml not found"):
        pipeline._require_episode(ep_dir)


def test_require_episode_message_contains_creation_recipe(tmp_path: Path) -> None:
    ep_dir = tmp_path / "ep-x"
    ep_dir.mkdir()
    with pytest.raises(SystemExit) as exc:
        pipeline._require_episode(ep_dir)
    msg = str(exc.value)
    assert "mkdir -p" in msg
    assert "episode.yaml" in msg


def test_require_episode_returns_parsed_meta(tmp_path: Path) -> None:
    ep_dir = tmp_path / "ep-x"
    ep_dir.mkdir()
    (ep_dir / "episode.yaml").write_text("id: ep-x\ntitle: 測試\n", encoding="utf-8")
    meta = pipeline._require_episode(ep_dir)
    assert meta["id"] == "ep-x"
    assert meta["title"] == "測試"


# ---------------------------------------------------------------------------
# _record_stage
# ---------------------------------------------------------------------------


def test_record_stage_stamps_iso_timestamp(tmp_path: Path) -> None:
    ep_dir = tmp_path / "ep-x"
    ep_dir.mkdir()
    (ep_dir / "episode.yaml").write_text("id: ep-x\ntitle: 測試\n", encoding="utf-8")

    pipeline._record_stage(ep_dir, "cleanup")
    pipeline._record_stage(ep_dir, "plan")

    meta = yaml.safe_load((ep_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert meta["id"] == "ep-x"  # existing fields preserved
    assert set(meta["stages"]) == {"cleanup", "plan"}
    for ts in meta["stages"].values():
        assert ts.startswith("20")  # ISO date
        assert "T" in ts


def test_record_stage_overwrites_same_stage(tmp_path: Path) -> None:
    ep_dir = tmp_path / "ep-x"
    ep_dir.mkdir()
    (ep_dir / "episode.yaml").write_text("id: ep-x\n", encoding="utf-8")
    pipeline._record_stage(ep_dir, "plan")
    first = yaml.safe_load((ep_dir / "episode.yaml").read_text(encoding="utf-8"))["stages"]["plan"]
    pipeline._record_stage(ep_dir, "plan")
    meta = yaml.safe_load((ep_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert list(meta["stages"]) == ["plan"]
    assert meta["stages"]["plan"] >= first


# ---------------------------------------------------------------------------
# cleanup subcommand
# ---------------------------------------------------------------------------


def _make_episode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point _DATA_ROOT at tmp_path and build a minimal episode dir."""
    monkeypatch.setattr(pipeline, "_DATA_ROOT", tmp_path)
    ep_dir = tmp_path / "ep-clap"
    (ep_dir / "out").mkdir(parents=True)
    (ep_dir / "episode.yaml").write_text("id: ep-clap\ntitle: clap smoke\n", encoding="utf-8")
    # raw_recording.mp4 只是 asset path reference（emit 不讀內容）；音訊直接
    # 放 fixture WAV 讓 cleanup 跳過 ffmpeg 抽取。
    (ep_dir / "raw_recording.mp4").touch()
    shutil.copy(_CLAP_FIXTURE, ep_dir / "aroll-audio.wav")
    return ep_dir


def test_cleanup_subcommand_emits_ripple_fcpxml(tmp_path, monkeypatch) -> None:
    ep_dir = _make_episode(tmp_path, monkeypatch)

    rc = pipeline.main(["--episode", "ep-clap", "cleanup"])

    assert rc == 0
    out = ep_dir / "out" / "cleanup.fcpxml"
    assert out.exists(), "cleanup.fcpxml not written"
    root = ET.parse(out).getroot()
    assert root.tag == "fcpxml"
    clips = root.findall("library/event/project/sequence/spine/asset-clip")
    # clap fixture contains marker(s) → at least one cut → ≥1 kept segment
    assert len(clips) >= 1
    # output name is cleanup.fcpxml, NOT episode.fcpxml (ADR-050 D4 檔名區隔)
    assert not (ep_dir / "out" / "episode.fcpxml").exists()


def test_cleanup_records_stage(tmp_path, monkeypatch) -> None:
    ep_dir = _make_episode(tmp_path, monkeypatch)
    pipeline.main(["--episode", "ep-clap", "cleanup"])
    meta = yaml.safe_load((ep_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert "cleanup" in meta.get("stages", {})


def test_cleanup_fails_loud_without_episode_yaml(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "_DATA_ROOT", tmp_path)
    ep_dir = tmp_path / "ep-bare"
    (ep_dir / "out").mkdir(parents=True)
    (ep_dir / "raw_recording.mp4").touch()
    with pytest.raises(SystemExit, match="episode.yaml not found"):
        pipeline.main(["--episode", "ep-bare", "cleanup"])


def test_cleanup_errors_when_raw_recording_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "_DATA_ROOT", tmp_path)
    ep_dir = tmp_path / "ep-norec"
    ep_dir.mkdir()
    (ep_dir / "episode.yaml").write_text("id: ep-norec\n", encoding="utf-8")
    rc = pipeline.main(["--episode", "ep-norec", "cleanup"])
    assert rc == 1


# ---------------------------------------------------------------------------
# cleanup — script-anchored mode（words.json + script.md 存在時）
# ---------------------------------------------------------------------------

_SCRIPT_TEXT = "今天想跟大家聊三個增肌的觀念。第一個觀念是蛋白質要吃夠，不是吃多。"


def _char_words(text: str, start: float, end: float) -> list[dict]:
    span = end - start
    n = len(text)
    return [
        {
            "word": ch,
            "start": round(start + span * i / n, 3),
            "end": round(start + span * (i + 1) / n, 3),
        }
        for i, ch in enumerate(text)
    ]


def _make_script_anchored_episode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """clap fixture（events @ 2.0s / 5.0s）＋合成 words ＋逐字稿。

    words 佈局對齊 fixture 的兩個拍手事件：
      失敗 take1（0.3–1.8s）→ 👏@2.0 → 失敗 take2（2.6–4.6s）→ 👏@5.0 →
      retake 成功（5.8–7.8s）
    """
    ep_dir = _make_episode(tmp_path, monkeypatch)
    words = (
        _char_words("第一個觀念是蛋白質要", 0.3, 1.8)
        + _char_words("第一個觀念是蛋白質要吃", 2.6, 4.6)
        + _char_words("第一個觀念是蛋白質要吃夠不是吃多", 5.8, 7.8)
    )
    (ep_dir / "words.json").write_text(
        json.dumps({"words": words}, ensure_ascii=False), encoding="utf-8"
    )
    (ep_dir / "script.md").write_text(_SCRIPT_TEXT, encoding="utf-8")
    return ep_dir


def test_cleanup_script_anchored_emits_fcpxml_and_corrected_srt(tmp_path, monkeypatch) -> None:
    ep_dir = _make_script_anchored_episode(tmp_path, monkeypatch)

    rc = pipeline.main(["--episode", "ep-clap", "cleanup"])

    assert rc == 0
    root = ET.parse(ep_dir / "out" / "cleanup.fcpxml").getroot()
    clips = root.findall("library/event/project/sequence/spine/asset-clip")
    assert len(clips) >= 1
    # corrected SRT：文字全對（含逐字稿的「不是吃多」）、失敗 take 已剔除
    srt = (ep_dir / "transcript.srt").read_text(encoding="utf-8")
    assert "不是吃多" in srt
    assert srt.count("第一個觀念") == 1
    meta = yaml.safe_load((ep_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert "cleanup" in meta.get("stages", {})


def test_cleanup_script_anchored_without_script_skips_srt(tmp_path, monkeypatch) -> None:
    ep_dir = _make_script_anchored_episode(tmp_path, monkeypatch)
    (ep_dir / "script.md").unlink()

    rc = pipeline.main(["--episode", "ep-clap", "cleanup"])

    assert rc == 0
    assert (ep_dir / "out" / "cleanup.fcpxml").exists()
    assert not (ep_dir / "transcript.srt").exists()


# ---------------------------------------------------------------------------
# correct-srt subcommand
# ---------------------------------------------------------------------------


def test_correct_srt_subcommand_writes_corrected_srt(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "_DATA_ROOT", tmp_path)
    ep_dir = tmp_path / "ep-srt"
    ep_dir.mkdir()
    (ep_dir / "episode.yaml").write_text("id: ep-srt\n", encoding="utf-8")
    # ASR 錯字：「蛋白值」
    words = _char_words("今天想跟大家聊三個增肌的觀念", 0.5, 3.3) + _char_words(
        "第一個觀念是蛋白值要吃夠不是吃多", 5.0, 8.2
    )
    (ep_dir / "words.json").write_text(
        json.dumps({"words": words}, ensure_ascii=False), encoding="utf-8"
    )
    (ep_dir / "script.md").write_text(_SCRIPT_TEXT, encoding="utf-8")

    rc = pipeline.main(["--episode", "ep-srt", "correct-srt"])

    assert rc == 0
    srt = (ep_dir / "transcript.srt").read_text(encoding="utf-8")
    assert "蛋白質" in srt
    assert "蛋白值" not in srt


def test_correct_srt_errors_without_words_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "_DATA_ROOT", tmp_path)
    ep_dir = tmp_path / "ep-nowords"
    ep_dir.mkdir()
    (ep_dir / "episode.yaml").write_text("id: ep-nowords\n", encoding="utf-8")
    (ep_dir / "script.md").write_text(_SCRIPT_TEXT, encoding="utf-8")
    assert pipeline.main(["--episode", "ep-nowords", "correct-srt"]) == 1
