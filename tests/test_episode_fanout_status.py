"""派工盤點要把完整版算進去。

2026-09-18 實查：`releases` 22 筆、`cut_id=full` 0 筆，八集沒有一集發過完整版。
這支工具是「他說定稿了之後我第一件事跑的」，而它的派工圖裡沒有完整版——所以
它會對一個完整版還沒派的集數說「沒有待派的工作了」。20260901 蘇予昕實測正是如此：
完整版標題與封面早就做好，匯出與登錄從來沒發生。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "episode_fanout_status", _REPO / "scripts" / "episode_fanout_status.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["episode_fanout_status"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def episode(tmp_path, monkeypatch):
    """一集封存好、miners 還沒跑的 episode。

    `_master` 換掉是因為真的那條會 sha256 整份 8–10 GB 的 master.mp4；
    這裡要驗的是派工盤點，不是封存契約。
    """
    module = _load()
    root = tmp_path / "20260722 李海碩"
    (root / "highlights").mkdir(parents=True)
    monkeypatch.setattr(module, "_master", lambda episode_dir: (True, "deadbeef1234"))
    monkeypatch.setattr(module, "_releases", lambda episode_id: {})
    return module, root


def test_full_cut_is_dispatchable_before_the_miners_run(episode, capsys):
    """完整版的 timeline 封存當下就定了，跟 miners 沒有先後關係。"""
    module, root = episode

    module.report(root)

    out = capsys.readouterr().out
    assert "候選開採　還沒跑" in out
    assert "完整版" in out
    assert "--cut full" in out


def test_full_cut_is_listed_with_the_other_cuts(episode, capsys, monkeypatch):
    """有候選之後，完整版要跟精華並列，而且排在最前面。"""
    module, root = episode
    hl = root / "highlights"
    (hl / "candidates.json").write_text(
        json.dumps({"candidates": [{"id": "punch-L01", "format": "long", "title": "x"}]}),
        encoding="utf-8",
    )
    won = {"long": ["punch-L01"], "short": []}
    monkeypatch.setattr(module, "_winners", lambda hl_dir, fmt: won[fmt])
    monkeypatch.setattr(module, "_panel", lambda hl_dir, fmt: (True, "ok"))

    module.report(root)

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith("  ")]
    cut_lines = [line for line in lines if "標題" in line]
    assert cut_lines, out
    assert cut_lines[0].strip().startswith("full")


def test_full_cut_that_still_needs_exporting_is_not_reported_as_done(episode, capsys, monkeypatch):
    """標題封面做完但沒匯出 ≠ 沒事了。蘇予昕就卡在這個狀態。"""
    module, root = episode
    hl = root / "highlights"
    (hl / "candidates.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")
    monkeypatch.setattr(module, "_candidates", lambda hl_dir: {"long": 1})
    monkeypatch.setattr(module, "_winners", lambda hl_dir, fmt: [])
    monkeypatch.setattr(module, "_panel", lambda hl_dir, fmt: (True, "ok"))
    packaging = root / "packaging"
    packaging.mkdir()
    (packaging / "packages.json").write_text(
        json.dumps(
            {
                "episode": root.name,
                "cuts": [
                    {
                        "cut_id": "full",
                        "titles": [{"rank": n} for n in range(1, 6)],
                        "packages": [{"title_rank": n} for n in (1, 2, 3)],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    module.report(root)

    out = capsys.readouterr().out
    assert "full" in out
    assert "匯出 ⬜" in out
    assert "沒有待派的工作了" not in out
    assert "--cut full" in out
