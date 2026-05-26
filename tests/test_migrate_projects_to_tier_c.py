"""Tests for scripts.migrate_projects_to_tier_c (Tier C migration)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.migrate_projects_to_tier_c import migrate_file

LEGACY_YOUTUBE = """---
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
tags:
  - project
  - youtube
---

# 肌酸的妙用

## 🎯 對應 OKR
- **季度計畫**：`= this.quarter`
- **關鍵結果**：`= this.parent_kr`

## ✅ Tasks

```base
filters:
  and:
    - file.hasTag("task")
```

## 📊 番茄統計

```dataviewjs
const tasks = ...;
```

---

<!-- vault:human-only-section -->
## 👄 One Sentence About This Video

探討肌酸對非運動族群的潛在用途。

## 📚 KB Research

```dataviewjs
// kb stuff
```

## 🗝️ Keyword Research & Title Ideas

```dataviewjs
// zoro stuff
```

%%KW-START%%
old zoro blob 30KB
%%KW-END%%

## Script / Outline

開頭...

## 專案筆記

備註
"""


LEGACY_RESEARCH = """---
type: project
content_type: research
created: 2026-04-15
status: active
priority: medium
area: work
target_date:
tags:
  - project
  - research
---

# 蛋白質攝取量

## ✅ Tasks
old

## 👄 One Sentence About This Video
研究每日蛋白質攝取上限。

## Script / Outline
...
"""


META_WORKSPACE = """---
type: agent-workspace
agent: brook
total_posts: 192
---

# Brook 風格訓練
"""


ALREADY_MIGRATED = """---
type: project
content_type: youtube
created: 2026-04-01
status: active
priority: medium
area: work
search_topic: x
tags:
  - project
  - youtube
one_sentence: already lifted
hook_text: ''
title_candidates: []
thumbnail_concept: ''
pomodoro:
  est_total: 0
  actual_total: 0
---

# x
"""


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    proj_dir = tmp_path / "Projects"
    proj_dir.mkdir(parents=True)
    (proj_dir / "肌酸的妙用.md").write_text(LEGACY_YOUTUBE, encoding="utf-8")
    (proj_dir / "蛋白質攝取量.md").write_text(LEGACY_RESEARCH, encoding="utf-8")
    (proj_dir / "Brook 風格訓練.md").write_text(META_WORKSPACE, encoding="utf-8")
    (proj_dir / "已遷.md").write_text(ALREADY_MIGRATED, encoding="utf-8")
    # No TaskNotes — pomodoro rollup should be (0, 0)
    return tmp_path


class TestMigrateFile:
    def test_lifts_one_sentence_youtube(self, vault: Path):
        path = vault / "Projects" / "肌酸的妙用.md"
        res = migrate_file(path, vault=vault)
        assert not res.skipped
        fm = yaml.safe_load(res.new_content.split("---")[1])
        assert fm["one_sentence"].strip() == "探討肌酸對非運動族群的潛在用途。"
        assert "## 👄 One Sentence" not in res.new_content

    def test_strips_dataviewjs_sections(self, vault: Path):
        path = vault / "Projects" / "肌酸的妙用.md"
        res = migrate_file(path, vault=vault)
        # All Bases/dataviewjs sections gone
        assert "## 🎯 對應 OKR" not in res.new_content
        assert "## ✅ Tasks" not in res.new_content
        assert "## 📊 番茄統計" not in res.new_content
        assert "## 📚 KB Research" not in res.new_content
        assert "## 🗝️ Keyword Research" not in res.new_content
        # Body legacy KW markers gone
        assert "%%KW-START%%" not in res.new_content
        assert "%%KW-END%%" not in res.new_content
        # Human content preserved
        assert "## Script / Outline" in res.new_content
        assert "## 專案筆記" in res.new_content
        assert "開頭..." in res.new_content

    def test_research_kept_as_research(self, vault: Path):
        path = vault / "Projects" / "蛋白質攝取量.md"
        res = migrate_file(path, vault=vault)
        assert not res.skipped
        fm = yaml.safe_load(res.new_content.split("---")[1])
        # content_type unchanged
        assert fm["content_type"] == "research"
        # target_date preserved (we don't drop in migration; new writes don't add it)
        assert "target_date" in fm
        # one_sentence lifted
        assert fm["one_sentence"].strip() == "研究每日蛋白質攝取上限。"

    def test_skips_agent_workspace(self, vault: Path):
        path = vault / "Projects" / "Brook 風格訓練.md"
        res = migrate_file(path, vault=vault)
        assert res.skipped
        assert "agent-workspace" in res.skip_reason

    def test_skips_already_migrated(self, vault: Path):
        path = vault / "Projects" / "已遷.md"
        res = migrate_file(path, vault=vault)
        assert res.skipped
        assert "already" in res.skip_reason

    def test_adds_pomodoro_rollup_field(self, vault: Path):
        path = vault / "Projects" / "肌酸的妙用.md"
        res = migrate_file(path, vault=vault)
        fm = yaml.safe_load(res.new_content.split("---")[1])
        assert "pomodoro" in fm
        # No TaskNotes -> (0, 0)
        assert fm["pomodoro"]["est_total"] == 0
        assert fm["pomodoro"]["actual_total"] == 0

    def test_adds_empty_gamma_placeholders(self, vault: Path):
        path = vault / "Projects" / "肌酸的妙用.md"
        res = migrate_file(path, vault=vault)
        fm = yaml.safe_load(res.new_content.split("---")[1])
        assert "hook_text" in fm
        assert "title_candidates" in fm
        assert "thumbnail_concept" in fm

    def test_idempotent(self, vault: Path):
        path = vault / "Projects" / "肌酸的妙用.md"
        res1 = migrate_file(path, vault=vault)
        # Apply, then re-migrate the result
        path.write_text(res1.new_content, encoding="utf-8")
        res2 = migrate_file(path, vault=vault)
        assert res2.skipped
        assert "already migrated" in res2.skip_reason

    def test_strips_agent_marker_blob_variant(self, vault: Path):
        # Same body but using newer marker family
        seed = LEGACY_YOUTUBE.replace(
            "%%KW-START%%\nold zoro blob 30KB\n%%KW-END%%",
            "%%agent-zoro-keywords-start%%\nnewer blob\n%%agent-zoro-keywords-end%%",
        )
        path = vault / "Projects" / "新標記.md"
        path.write_text(seed, encoding="utf-8")
        res = migrate_file(path, vault=vault)
        assert "%%agent-zoro-keywords-start%%" not in res.new_content
        assert "%%agent-zoro-keywords-end%%" not in res.new_content
