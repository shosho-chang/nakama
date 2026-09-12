"""`author_chapters.py` 只驗規則、只落檔——不會替你想章節。

切章是語意工作（讀逐字稿、判斷話題在哪裡轉），由當下執行的 agent 做；腳本負責的是
「什麼樣的章節表算合法」與「落在 `resolve_chapters` 讀得到的地方」。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "author_chapters_under_test", _REPO_ROOT / "scripts" / "author_chapters.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


author_chapters = _load()


def _payload(**overrides) -> dict:
    payload = {
        "source": "editorial-master/v1/master.srt",
        "chapters": [
            {"t0": 0.0, "title": "開場：這集在聊什麼"},
            {"t0": 318.0, "title": "AI 用到極致長什麼樣"},
            {"t0": 1123.0, "title": "回到教育現場"},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def episode(tmp_path):
    d = tmp_path / "20260721 呂冠緯"
    d.mkdir()
    return d


def test_it_lands_where_resolve_chapters_reads(episode):
    out = author_chapters.author(episode, cut_id="full", payload=_payload())

    assert out == episode / "publish" / "chapters" / "full.json"
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema"] == "nakama.publish_chapters.v1"
    assert doc["episode"] == "20260721 呂冠緯"
    assert doc["cut_id"] == "full"
    assert [row["title"] for row in doc["chapters"]][0] == "開場：這集在聊什麼"


def test_the_transcript_it_was_cut_from_is_recorded(episode):
    """換了逐字稿就該重切——沒記來源就沒人知道這份過期了沒。"""
    out = author_chapters.author(episode, cut_id="full", payload=_payload())

    assert json.loads(out.read_text(encoding="utf-8"))["source"] == "editorial-master/v1/master.srt"


def test_a_payload_without_a_source_is_refused(episode):
    payload = _payload()
    del payload["source"]

    with pytest.raises(SystemExit, match="source"):
        author_chapters.author(episode, cut_id="full", payload=payload)


def test_a_chapter_past_the_end_of_the_cut_is_refused(episode):
    """時間戳指到片長以外，YouTube 上點了會跳到最後一格。"""
    with pytest.raises(SystemExit, match="超出片長"):
        author_chapters.author(episode, cut_id="full", payload=_payload(), duration_sec=900.0)


def test_it_stays_inside_a_cut_length_that_covers_every_chapter(episode):
    out = author_chapters.author(
        episode, cut_id="full", payload=_payload(), duration_sec=5231.146667
    )

    assert out.is_file()


def test_youtube_hard_rules_are_refused_before_anything_lands(episode):
    """擋下來就是擋下來——不可以留下半份表。"""
    payload = _payload(chapters=[{"t0": 30.0, "title": "沒有從零開始"}])

    with pytest.raises(ValueError):
        author_chapters.author(episode, cut_id="full", payload=payload)

    assert not (episode / "publish" / "chapters" / "full.json").exists()


def test_a_missing_episode_folder_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="episode 資料夾不存在"):
        author_chapters.author(tmp_path / "不存在", cut_id="full", payload=_payload())
