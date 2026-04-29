"""Tests for ``shared.target_keywords`` — the read-only YAML loader.

Mirrors the franky cron's ``load_keywords`` semantics (since both go
through the same Pydantic schema) but the surface tested here is the
bridge UI entry-point: missing file → ``None`` (UI renders empty-state
without crashing); empty keywords list → ``TargetKeywordListV1`` with
``[]``; populated → returns parsed model.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from shared import target_keywords
from shared.schemas.seo import TargetKeywordListV1


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "target-keywords.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_default_path_resolves_to_repo_config():
    """``default_path()`` points at the repo's ``config/target-keywords.yaml``.

    Resolution must work regardless of cwd (uvicorn / pytest may run from
    anywhere) — the function uses ``Path(__file__)`` for that reason.
    """
    p = target_keywords.default_path()
    assert p.is_absolute()
    assert p.name == "target-keywords.yaml"
    assert p.parent.name == "config"


def test_load_returns_none_when_file_missing(tmp_path):
    """Missing YAML → ``None`` so bridge UI renders empty-state."""
    missing = tmp_path / "does-not-exist.yaml"
    assert not missing.exists()
    assert target_keywords.load_target_keywords(missing) is None


def test_load_returns_model_for_empty_keywords(tmp_path):
    """File exists with ``keywords: []`` → returns model with empty list."""
    yaml_path = _write_yaml(
        tmp_path,
        """\
        schema_version: 1
        updated_at: "2026-04-29T00:00:00+08:00"
        keywords: []
        """,
    )
    doc = target_keywords.load_target_keywords(yaml_path)
    assert isinstance(doc, TargetKeywordListV1)
    assert doc.keywords == []
    assert doc.schema_version == 1


def test_load_parses_populated_list(tmp_path):
    """Multi-keyword fixture parses; both required and optional fields work."""
    yaml_path = _write_yaml(
        tmp_path,
        """\
        schema_version: 1
        updated_at: "2026-04-29T00:00:00+08:00"
        keywords:
          - schema_version: 1
            keyword: "深層睡眠"
            keyword_en: "deep sleep"
            site: "shosho.tw"
            added_by: "zoro"
            added_at: "2026-04-29T08:00:00+08:00"
            goal_rank: 5
          - schema_version: 1
            keyword: "間歇性斷食"
            site: "fleet.shosho.tw"
            added_by: "usopp"
            added_at: "2026-04-28T08:00:00+08:00"
        """,
    )
    doc = target_keywords.load_target_keywords(yaml_path)
    assert isinstance(doc, TargetKeywordListV1)
    assert len(doc.keywords) == 2

    first = doc.keywords[0]
    assert first.keyword == "深層睡眠"
    assert first.keyword_en == "deep sleep"
    assert first.site == "shosho.tw"
    assert first.goal_rank == 5
    assert first.added_by == "zoro"

    second = doc.keywords[1]
    assert second.keyword == "間歇性斷食"
    # keyword_en + goal_rank both unset → None per schema default.
    assert second.keyword_en is None
    assert second.goal_rank is None


def test_load_raises_on_schema_violation(tmp_path):
    """Bad shape → ``ValidationError`` (config bug must surface, not hide)."""
    yaml_path = _write_yaml(
        tmp_path,
        """\
        schema_version: 1
        updated_at: "2026-04-29T00:00:00+08:00"
        keywords:
          - keyword: "kw"
            site: "not-a-real-site.com"
            added_by: "zoro"
            added_at: "2026-04-29T08:00:00+08:00"
        """,
    )
    # ``site`` outside the Literal set → Pydantic ValidationError.
    with pytest.raises(Exception) as excinfo:
        target_keywords.load_target_keywords(yaml_path)
    # Pydantic v2 raises ``ValidationError``; assert by type-name to avoid
    # a hard import dep on its private module path.
    assert "ValidationError" in type(excinfo.value).__name__


def test_load_with_no_arg_uses_default_path(monkeypatch, tmp_path):
    """Passing no path → ``default_path()`` is consulted (and result returned)."""
    yaml_path = _write_yaml(
        tmp_path,
        """\
        schema_version: 1
        updated_at: "2026-04-29T00:00:00+08:00"
        keywords: []
        """,
    )
    monkeypatch.setattr(target_keywords, "default_path", lambda: yaml_path)
    doc = target_keywords.load_target_keywords()
    assert isinstance(doc, TargetKeywordListV1)
    assert doc.keywords == []
