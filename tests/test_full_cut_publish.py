"""完整版（cut_id=full）走發布線。

packaging 早就做得出完整版的標題與封面，但 publish 從來沒接過它——`releases`
22 筆裡 0 筆是 full（2026-09-18 實查）。這一組測試守住接上去之後的四個接縫：

1. 完整版是哪一條 timeline → 讀 Editorial Master 的 receipt，不重算 hash
2. `publish_prep --cut full` 繞開 winners/candidates
3. 完整版的字幕來源是 `editorial-master/v1/master.srt`
4. 分章那道「有對應表就回空」的閘，只對對應表自己列到的 cut 生效
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]

from agents.brook.script_video.editorial_master import (  # noqa: E402
    editorial_master_timeline,
)
from agents.usopp.publish_timeline import plan_subtitle  # noqa: E402
from agents.usopp.video_description import resolve_chapters  # noqa: E402

TIMELINE = {
    "name": "20260901 蘇予昕 - 三機 - final",
    "uid": "2fc28739-8e32-443d-a910-f8f087876650",
    "fps": "3E+1",
    "start_frame": 0,
    "end_frame": 184408,
    "duration_frames": 184408,
    "duration_sec": 6146.933333333333,
    "snapshot_sha256": "a" * 64,
}


def _load_publish_prep():
    spec = importlib.util.spec_from_file_location(
        "publish_prep", _REPO / "scripts" / "publish_prep.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["publish_prep"] = module
    sys.path.insert(0, str(_REPO / "scripts"))
    spec.loader.exec_module(module)
    return module


def _sealed(tmp_path: Path, episode: str = "20260901 蘇予昕", **overrides) -> Path:
    """一集封存好的 episode 目錄。**故意不建 master.mp4**——讀 timeline 不該碰它。"""
    root = tmp_path / episode
    version = root / "editorial-master" / "v1"
    version.mkdir(parents=True)
    receipt = {
        "contract": "podcast-editorial-master-v1",
        "episode_id": episode,
        "project": {"name": episode},
        "timeline": dict(TIMELINE),
        "approval": {
            "human_approved": True,
            "approved_by": "shosho",
            "approved_at": "2026-09-05T09:51:59.381388+00:00",
        },
        "artifacts": {},
        "content_hash": "b" * 64,
        "stage5_subtitle_identity": {},
    }
    receipt.update(overrides)
    (version / "EDITORIAL-MASTER.json").write_text(
        json.dumps(receipt, ensure_ascii=False), encoding="utf-8"
    )
    return root


# ---------------------------------------------------------------------------
# 1. 讀封存記下的 timeline
# ---------------------------------------------------------------------------


def test_timeline_comes_from_the_receipt_without_touching_the_master_media(tmp_path):
    """8–10 GB 的 master.mp4 連存在都不必——board 每 5 秒重整一次，不能重讀十 GB。"""
    root = _sealed(tmp_path)

    timeline = editorial_master_timeline(root)

    assert timeline is not None
    assert timeline["name"] == "20260901 蘇予昕 - 三機 - final"
    assert timeline["duration_sec"] == pytest.approx(6146.9333, abs=1e-3)
    assert not (root / "editorial-master" / "v1" / "master.mp4").exists()


def test_timeline_name_is_not_derivable_from_the_episode_folder(tmp_path):
    """蘇予昕的實檔就是這樣——所以這個資訊只有封存記得，猜不出來。"""
    root = _sealed(tmp_path)

    assert editorial_master_timeline(root)["name"] != root.name


def test_unsealed_episode_is_not_an_error(tmp_path):
    """ADR-064 之前的集數本來就沒有，回 None 是常態。"""
    root = tmp_path / "20260723 謝伯讓"
    root.mkdir()

    assert editorial_master_timeline(root) is None


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"contract": "something-else"}, id="契約不符"),
        pytest.param({"episode_id": "20260101 別集"}, id="別集的 receipt"),
        pytest.param({"approval": {"human_approved": False}}, id="沒有人核准過"),
        pytest.param({"timeline": {"uid": "x"}}, id="timeline 沒有名字"),
    ],
)
def test_a_receipt_that_cannot_be_trusted_reads_as_unsealed(tmp_path, overrides):
    assert editorial_master_timeline(_sealed(tmp_path, **overrides)) is None


# ---------------------------------------------------------------------------
# 2. publish_prep 的完整版分支
# ---------------------------------------------------------------------------


def test_full_cut_bypasses_winners_entirely(tmp_path):
    """完整版不是精華挑選的產物，不該出現在 winners/candidates 裡。"""
    module = _load_publish_prep()
    root = _sealed(tmp_path)
    assert not (root / "highlights").exists()  # 連 winners 檔都沒有

    cut = module.full_cut(root)

    assert cut["id"] == "full"
    assert cut["format"] == "long"
    assert cut["editorial_master_timeline"]["name"] == TIMELINE["name"]


def test_full_cut_without_a_master_says_what_to_do(tmp_path):
    module = _load_publish_prep()
    root = tmp_path / "20260723 謝伯讓"
    root.mkdir()

    with pytest.raises(SystemExit) as excinfo:
        module.full_cut(root)

    assert "Editorial Master" in str(excinfo.value)
    assert "seal" in str(excinfo.value)


def test_render_timeout_scales_with_the_material(tmp_path):
    """固定 3600 秒比 102 分鐘的素材還短——等於要求 render 跑贏 1.7× realtime。"""
    module = _load_publish_prep()

    assert module.render_timeout_sec(752.3) == 3600  # 12 分鐘長精華：維持下限
    assert module.render_timeout_sec(6146.9) > 6146.9  # 完整版：一定大於素材長度


# ---------------------------------------------------------------------------
# 3. 完整版的字幕來源
# ---------------------------------------------------------------------------


def test_full_subtitle_is_the_sealed_master_srt(tmp_path):
    """沒有這條，描述生成會 FileNotFoundError、CC 會永久 failed 且補不回來。"""
    root = _sealed(tmp_path)
    srt = root / "editorial-master" / "v1" / "master.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n大家好\n", encoding="utf-8")

    assert plan_subtitle(root, "full") == srt


def test_full_subtitle_is_none_when_the_episode_is_not_sealed(tmp_path):
    root = tmp_path / "20260723 謝伯讓"
    root.mkdir()

    assert plan_subtitle(root, "full") is None


# ---------------------------------------------------------------------------
# 4. 分章那道閘
# ---------------------------------------------------------------------------


def _with_chapters(root: Path, cut_id: str) -> None:
    path = root / "publish" / "chapters" / f"{cut_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "nakama.publish_chapters.v1",
                "episode": root.name,
                "cut_id": cut_id,
                "generated_at": "2026-09-18T00:00:00+00:00",
                "source": "editorial-master/v1/master.srt",
                "chapters": [
                    {"t0": 0.0, "title": "開場"},
                    {"t0": 600.0, "title": "中段"},
                    {"t0": 1200.0, "title": "收尾"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _with_timeline_map(root: Path, cuts: dict) -> None:
    path = root / "highlights" / "publish-timelines.v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": "nakama.publish_timelines.v1", "cuts": cuts}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_a_timeline_map_no_longer_silences_chapters_for_cuts_it_never_mentions(tmp_path):
    """完整版從來不在對應表裡。原本這裡回空章節，102 分鐘的影片會安靜地零分章上架。"""
    root = _sealed(tmp_path)
    _with_chapters(root, "full")
    _with_timeline_map(root, {"punch-L04": {"timeline": "長1 - x", "expected_duration_sec": 693.1}})

    assert len(resolve_chapters(root, "full")) == 3


def test_a_cut_the_map_does_mention_still_defers_to_it(tmp_path):
    """對應表對它自己列到的 cut 仍然有發言權——它說沒有分章就是沒有。"""
    root = _sealed(tmp_path)
    _with_chapters(root, "punch-L04")
    _with_timeline_map(root, {"punch-L04": {"timeline": "長1 - x", "expected_duration_sec": 693.1}})

    assert resolve_chapters(root, "punch-L04") == []


# ---------------------------------------------------------------------------
# 5. 核准前的可行性判斷
# ---------------------------------------------------------------------------


def test_unsealed_episode_is_refused_before_anything_is_written(tmp_path, monkeypatch):
    """訊息要講得出下一步。舊版是先寫核准再失敗，留下「board 說已核准、releases 空」。"""
    from fastapi import HTTPException

    from thousand_sunny.routers import packaging

    root = tmp_path / "20260723 謝伯讓"
    root.mkdir()
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(tmp_path))

    with pytest.raises(HTTPException) as excinfo:
        packaging._require_editorial_master(root.name)

    assert excinfo.value.status_code == 409
    assert "我完成 Editorial Master 了" in excinfo.value.detail


def test_sealed_episode_passes_the_guard(tmp_path, monkeypatch):
    from thousand_sunny.routers import packaging

    root = _sealed(tmp_path)
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(tmp_path))

    packaging._require_editorial_master(root.name)  # 不該 raise
