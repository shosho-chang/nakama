"""Tests for shared.project_indexer (Tier C D2 FS-direct reader)."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.project_indexer import (
    ProjectIndexer,
    ProjectNotFoundError,
    normalize_slug,
)

YOUTUBE_PROJECT = """---
type: project
content_type: youtube
created: 2026-04-10
status: active
priority: first
area: work
search_topic: 肌酸
quarter:
parent_kr:
publish_date:
one_sentence: |
  探討肌酸對非運動族群的潛在用途。
hook_text: |
  你以為肌酸只是健身房裡那罐白白的粉？
title_candidates:
  - "肌酸不只練肌肉"
  - "65 歲開始吃肌酸"
thumbnail_concept: |
  健身房 vs 大腦
reviews:
  storyteller:
    run_at: 2026-05-24T22:15:00+08:00
    score: 4
    summary: Hook 抓得很穩
    suggestions:
      - 把第 2 段改成具體例子
pomodoro:
  est_total: 12
  actual_total: 8
tags:
  - project
  - youtube
---

# 肌酸的妙用

<!-- vault:human-only-section -->
## 專案描述
肌酸有效。
"""


PODCAST_PROJECT = """---
type: project
content_type: podcast
created: 2026-04-15
status: paused
priority: medium
area: work
search_topic: 睡眠
tags:
  - project
  - podcast
---

# 睡眠 ep
"""


META_AGENT_WORKSPACE = """---
type: agent-workspace
agent: brook
created: 2026-04-22
---

# Brook 風格訓練
"""


NON_PROJECT_NOTE = """---
type: note
created: 2026-04-15
---

# 散落筆記
"""


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    proj_dir = tmp_path / "Projects"
    proj_dir.mkdir(parents=True)
    (proj_dir / "肌酸的妙用.md").write_text(YOUTUBE_PROJECT, encoding="utf-8")
    (proj_dir / "睡眠.md").write_text(PODCAST_PROJECT, encoding="utf-8")
    (proj_dir / "Brook 風格訓練.md").write_text(META_AGENT_WORKSPACE, encoding="utf-8")
    (proj_dir / "散落筆記.md").write_text(NON_PROJECT_NOTE, encoding="utf-8")
    (proj_dir / ".sync-conflict-bogus.md").write_text("---\ntype: project\n---", encoding="utf-8")
    return tmp_path


class TestListAll:
    def test_returns_only_type_project(self, vault: Path):
        idx = ProjectIndexer(vault)
        entries = idx.list_all()
        slugs = [e.slug for e in entries]
        # Both projects present; agent-workspace + note excluded
        assert "肌酸的妙用" in slugs
        assert "睡眠" in slugs
        assert "Brook 風格訓練" not in slugs  # type: agent-workspace
        assert "散落筆記" not in slugs  # type: note
        assert ".sync-conflict-bogus" not in slugs  # filename skip

    def test_sorts_active_priority_first_above_paused_medium(self, vault: Path):
        idx = ProjectIndexer(vault)
        entries = idx.list_all()
        # active+first comes before paused+medium
        assert entries[0].slug == "肌酸的妙用"
        assert entries[1].slug == "睡眠"

    def test_archived_excluded_by_default(self, vault: Path):
        archived = vault / "Projects" / "Archived 專案.md"
        archived.write_text(
            "---\ntype: project\ncontent_type: youtube\nstatus: archived\ntags: [project]\n---\n",
            encoding="utf-8",
        )
        idx = ProjectIndexer(vault)
        entries = idx.list_all()
        assert "Archived 專案" not in [e.slug for e in entries]
        entries_all = idx.list_all(include_archived=True)
        assert "Archived 專案" in [e.slug for e in entries_all]

    def test_missing_projects_dir_returns_empty(self, tmp_path: Path):
        idx = ProjectIndexer(tmp_path)
        assert idx.list_all() == []


class TestGet:
    def test_returns_entry_with_full_fields(self, vault: Path):
        idx = ProjectIndexer(vault)
        e = idx.get("肌酸的妙用")
        assert e.slug == "肌酸的妙用"
        assert e.content_type == "youtube"
        assert e.status == "active"
        assert e.priority == "first"
        assert e.one_sentence.startswith("探討肌酸")
        assert e.hook_text.startswith("你以為肌酸")
        assert e.title_candidates == ("肌酸不只練肌肉", "65 歲開始吃肌酸")
        assert e.pomodoro_est_total == 12
        assert e.pomodoro_actual_total == 8
        assert "youtube" in e.tags

    def test_reviews_extracted_per_persona(self, vault: Path):
        idx = ProjectIndexer(vault)
        e = idx.get("肌酸的妙用")
        assert len(e.reviews) == 1
        r = e.reviews[0]
        assert r.persona == "storyteller"
        assert r.score == 4
        assert "Hook" in r.summary

    def test_reviews_list_shape_returns_latest(self, vault: Path):
        """v2 list-shape reviews — indexer surfaces the last entry as latest."""
        project_path = vault / "Projects" / "list-shape.md"
        project_path.write_text(
            """---
type: project
content_type: blog
created: 2026-05-20
status: active
priority: medium
area: work
search_topic: 測試
reviews:
  storyteller:
    - run_at: 2026-05-20T10:00:00+08:00
      score: 3
      summary: 初版 hook 一般
      suggestions: ["加數字"]
    - run_at: 2026-05-24T10:00:00+08:00
      score: 5
      summary: 改寫後 hook 強烈
      suggestions: ["保持"]
tags:
  - project
---
# x
""",
            encoding="utf-8",
        )
        idx = ProjectIndexer(vault)
        e = idx.get("list-shape")
        assert len(e.reviews) == 1
        # Indexer surfaces the LATEST = last entry
        assert e.reviews[0].score == 5
        assert "改寫後" in e.reviews[0].summary

    def test_unknown_slug_raises(self, vault: Path):
        idx = ProjectIndexer(vault)
        with pytest.raises(ProjectNotFoundError):
            idx.get("does not exist")


class TestLoadBody:
    def test_returns_body_without_frontmatter(self, vault: Path):
        idx = ProjectIndexer(vault)
        body = idx.load_body("肌酸的妙用")
        assert body.startswith("\n# 肌酸的妙用") or body.startswith("# 肌酸的妙用")
        assert "type: project" not in body
        assert "<!-- vault:human-only-section -->" in body


class TestNormalizeSlug:
    def test_nfc_round_trip(self):
        # NFD-decomposed CJK should normalize to NFC
        nfd = "肉酸"  # 肉酸 already NFC-equivalent; smoke test
        out = normalize_slug(nfd)
        # NFC form should be canonical — check no change to ASCII
        assert normalize_slug("hello") == "hello"
        # Idempotent
        assert normalize_slug(out) == out
