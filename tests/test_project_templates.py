"""Tests for shared.project_templates — 建立時套樣板 + 任務列表的階段順序。

修修 2026-09-10：「建立 project 的時候可以有地方讓我選擇是哪一種 project…
在成立這個 project 的同時，也能把它相對應的任務先全部建起來。」
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import yaml

from shared.project_index import ProjectError, find_project
from shared.project_templates import (
    TemplateError,
    create_project_with_template,
    find_template,
    kind_label,
    load_templates,
    stage_rank,
)

TEMPLATES = """
templates:
  podcast:
    label: Podcast · 訪談集
    stages:
      - { name: 訪綱撰寫, pomodoros: 3 }
      - { name: 節目錄製, pomodoros: 4 }
      - { name: 後製與上架, pomodoros: 8 }
  bare:
    label: 只有名字的樣板
    stages: [第一關, 第二關]
"""


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    cfg = tmp_path / "project-templates.yaml"
    io.open(cfg, "w", encoding="utf-8", newline="\n").write(TEMPLATES)
    monkeypatch.setenv("NAKAMA_PROJECT_TEMPLATES", str(cfg))
    (tmp_path / "Projects").mkdir()
    (tmp_path / "TaskNotes" / "Tasks").mkdir(parents=True)
    return tmp_path


def _fm(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])


class TestLoadTemplates:
    def test_parses_both_stage_shapes(self, vault: Path):
        tpl = find_template("podcast")
        assert tpl.label == "Podcast · 訪談集"
        assert [s.name for s in tpl.stages] == ["訪綱撰寫", "節目錄製", "後製與上架"]
        assert [s.pomodoros for s in tpl.stages] == [3, 4, 8]
        # a bare string stage is legal and defaults its estimate
        bare = find_template("bare")
        assert [s.name for s in bare.stages] == ["第一關", "第二關"]
        assert bare.stages[0].pomodoros == 4

    def test_unknown_kind_is_none(self, vault: Path):
        assert find_template("nope") is None
        assert find_template("") is None

    def test_missing_file_degrades_to_empty(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("NAKAMA_PROJECT_TEMPLATES", str(tmp_path / "gone.yaml"))
        assert load_templates() == {}

    def test_kind_label_falls_back_to_the_raw_key(self, vault: Path):
        """A project keeps working after its template is deleted from the YAML."""
        assert kind_label("podcast") == "Podcast · 訪談集"
        assert kind_label("retired-kind") == "retired-kind"


class TestCreateFromTemplate:
    def test_creates_stub_plus_every_stage_task(self, vault: Path):
        entry, paths = create_project_with_template(vault, "【Pod】蘇予昕", "podcast")
        assert entry.kind == "podcast"
        assert _fm(vault / "Projects" / "【Pod】蘇予昕.md")["kind"] == "podcast"
        assert [p.name for p in paths] == [
            "【Pod】蘇予昕 - 訪綱撰寫.md",
            "【Pod】蘇予昕 - 節目錄製.md",
            "【Pod】蘇予昕 - 後製與上架.md",
        ]
        fm = _fm(paths[0])
        assert fm["stage"] == "訪綱撰寫"
        assert fm["預估🍅"] == 3
        assert fm["projects"] == ["[[【Pod】蘇予昕]]"]

    def test_no_kind_creates_a_bare_project(self, vault: Path):
        entry, paths = create_project_with_template(vault, "自由專案", "")
        assert entry.kind == ""
        assert paths == []
        assert "kind" not in _fm(vault / "Projects" / "自由專案.md")

    def test_unknown_kind_raises(self, vault: Path):
        with pytest.raises(TemplateError):
            create_project_with_template(vault, "X", "nope")
        assert find_project(vault, "X") is None

    def test_task_collision_writes_nothing_at_all(self, vault: Path):
        """A clash on stage 3 must not leave a half-built project behind."""
        (vault / "TaskNotes" / "Tasks" / "【Pod】A - 後製與上架.md").write_text(
            "---\ntitle: 佔位\n---\n", encoding="utf-8"
        )
        with pytest.raises(TemplateError, match="已存在"):
            create_project_with_template(vault, "【Pod】A", "podcast")
        assert find_project(vault, "【Pod】A") is None
        assert not (vault / "TaskNotes" / "Tasks" / "【Pod】A - 訪綱撰寫.md").exists()

    def test_duplicate_stage_names_are_refused_before_any_write(
        self, vault: Path, tmp_path: Path, monkeypatch
    ):
        """Two stages sharing a name map to ONE filename — the second write would
        fail with the stub already on disk (review 2026-09-10)."""
        dup = tmp_path / "dup.yaml"
        dup.write_text(
            "templates:\n  dup:\n    label: D\n    stages: [同名, 別的, 同名]\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("NAKAMA_PROJECT_TEMPLATES", str(dup))
        with pytest.raises(TemplateError, match="重複"):
            create_project_with_template(vault, "X", "dup")
        assert find_project(vault, "X") is None
        assert not list((vault / "TaskNotes" / "Tasks").glob("X*"))

    def test_invalid_name_still_raises_project_error(self, vault: Path):
        with pytest.raises(ProjectError):
            create_project_with_template(vault, "[Pod] 壞名字", "podcast")


class TestStageRank:
    """``stage:`` survives only to ORDER the task list — the rail and the
    「其他任務」 split that also used it were removed (修修 2026-09-11)."""

    def test_rank_follows_template_order(self, vault: Path):
        create_project_with_template(vault, "P", "podcast")
        entry = find_project(vault, "P")
        assert stage_rank(entry) == {"訪綱撰寫": 0, "節目錄製": 1, "後製與上架": 2}

    def test_reordering_the_yaml_reorders_existing_projects(
        self, vault: Path, tmp_path: Path, monkeypatch
    ):
        """Order lives in the template, never in the task file — so editing the
        YAML re-sorts projects that already exist."""
        create_project_with_template(vault, "P", "podcast")
        flipped = tmp_path / "flipped.yaml"
        flipped.write_text(
            "templates:\n  podcast:\n    label: P\n    stages: [後製與上架, 節目錄製, 訪綱撰寫]\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("NAKAMA_PROJECT_TEMPLATES", str(flipped))
        entry = find_project(vault, "P")
        assert stage_rank(entry)["後製與上架"] == 0
        assert stage_rank(entry)["訪綱撰寫"] == 2

    def test_no_kind_has_no_ranking(self, vault: Path):
        create_project_with_template(vault, "自由專案", "")
        assert stage_rank(find_project(vault, "自由專案")) == {}

    def test_retired_template_has_no_ranking(self, vault: Path, tmp_path: Path, monkeypatch):
        create_project_with_template(vault, "P", "podcast")
        empty = tmp_path / "empty.yaml"
        empty.write_text("templates: {}\n", encoding="utf-8")
        monkeypatch.setenv("NAKAMA_PROJECT_TEMPLATES", str(empty))
        assert stage_rank(find_project(vault, "P")) == {}


def test_shipped_yaml_is_valid():
    """The file we actually ship must parse and define the kinds 修修 asked for."""
    tpls = load_templates()
    assert set(tpls) >= {"podcast", "youtube-book", "youtube-health"}
    assert [s.name for s in tpls["podcast"].stages] == ["訪綱撰寫", "節目錄製", "後製與上架"]
    assert [s.name for s in tpls["youtube-health"].stages] == ["前期研究", "拍攝", "後製", "上架"]
