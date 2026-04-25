"""Tests for shared/doc_index.py."""

from __future__ import annotations

import pytest

from shared.doc_index import DocIndex


@pytest.fixture
def repo_with_docs(tmp_path):
    """Build a fake repo layout with docs/ + memory/claude/ + _archive/ files."""
    docs = tmp_path / "docs" / "runbooks"
    docs.mkdir(parents=True)
    (docs / "deploy.md").write_text(
        "---\nname: Deployment Runbook\n---\n# Deploy\n\nHow to deploy R2 backup to VPS.\n",
        encoding="utf-8",
    )
    (docs / "secret.md").write_text(
        "# Secret rotation\n\nSteps for Anthropic API key rotation.\n",
        encoding="utf-8",
    )

    decisions = tmp_path / "docs" / "decisions"
    decisions.mkdir(parents=True)
    (decisions / "ADR-001.md").write_text(
        "# ADR-001: Agent role assignments\n\nRobin owns ingest, Brook owns compose.\n",
        encoding="utf-8",
    )

    mem = tmp_path / "memory" / "claude"
    mem.mkdir(parents=True)
    (mem / "feedback_x.md").write_text(
        "---\nname: feedback X\n---\nA lesson about Slack mrkdwn CJK.\n", encoding="utf-8"
    )
    (mem / "project_active.md").write_text(
        "---\nname: active project\n---\nIn-progress backup work.\n", encoding="utf-8"
    )

    # Excluded subdir — _archive must not appear in index
    archive = mem / "_archive"
    archive.mkdir()
    (archive / "project_old.md").write_text("# Archived\n\nOld memory.\n", encoding="utf-8")

    return tmp_path


@pytest.fixture
def idx(repo_with_docs, tmp_path):
    db_path = tmp_path / "doc_index.db"
    return DocIndex(repo_root=repo_with_docs, db_path=db_path)


# ---- rebuild + walk ---------------------------------------------------------


def test_rebuild_indexes_docs_and_memory_files(idx):
    n = idx.rebuild()
    # 3 docs (deploy, secret, ADR-001) + 2 memory (feedback_x, project_active) = 5
    assert n == 5


def test_rebuild_skips_archive_subdir(idx, repo_with_docs):
    idx.rebuild()
    hits = idx.search("Archived", limit=10)
    # Archive content should NOT show up — file is in excluded dir
    assert all("_archive" not in h.path for h in hits)


def test_rebuild_is_idempotent(idx):
    n1 = idx.rebuild()
    n2 = idx.rebuild()
    assert n1 == n2  # second rebuild deletes + reinserts → same count


# ---- search ----------------------------------------------------------------


def test_search_finds_keyword_in_body(idx):
    idx.rebuild()
    hits = idx.search("R2 backup")
    assert len(hits) >= 1
    paths = [h.path for h in hits]
    assert any("deploy.md" in p for p in paths)


def test_search_finds_keyword_in_title(idx):
    idx.rebuild()
    hits = idx.search("rotation")
    paths = [h.path for h in hits]
    assert any("secret.md" in p for p in paths)


def test_search_includes_snippet_with_highlight(idx):
    idx.rebuild()
    hits = idx.search("Anthropic")
    assert len(hits) >= 1
    snippet = hits[0].snippet
    assert "<mark>" in snippet
    assert "Anthropic" in snippet


def test_search_snippet_escapes_html_in_body(repo_with_docs, tmp_path):
    """Markdown containing literal `<script>` must not leak as raw HTML.

    Repro of the XSS that PR #157 review caught: snippet() -> `| safe` template
    -> stored XSS via any indexed file containing `<script>` literals.
    """
    docs = repo_with_docs / "docs" / "decisions"
    (docs / "ADR-evil.md").write_text(
        "# Evil ADR\n\nDiscussion of canarytokenxyz and <script>alert(1)</script> in the wild.\n",
        encoding="utf-8",
    )
    idx = DocIndex(repo_root=repo_with_docs, db_path=tmp_path / "doc_index.db")
    idx.rebuild()
    hits = idx.search("canarytokenxyz")
    assert len(hits) >= 1
    snippet = hits[0].snippet
    assert "<script>" not in snippet
    assert "&lt;script&gt;" in snippet
    # `<mark>` from sentinel swap is the ONLY allowed raw tag
    assert "<mark>" in snippet


def test_search_filters_by_category(idx):
    idx.rebuild()
    hits = idx.search("backup", category="memory")
    # All hits should be from memory/ category
    for h in hits:
        assert h.category == "memory"


def test_search_empty_query_returns_empty(idx):
    idx.rebuild()
    assert idx.search("") == []
    assert idx.search("   ") == []


def test_search_returns_empty_on_bad_fts5_syntax(idx):
    idx.rebuild()
    # FTS5 raises OperationalError on unbalanced quotes; we soft-fail
    assert idx.search('"unbalanced quote') == []


def test_search_respects_limit(idx):
    idx.rebuild()
    hits = idx.search("backup", limit=1)
    assert len(hits) <= 1


# ---- title extraction ------------------------------------------------------


def test_extract_title_prefers_frontmatter_name():
    text = "---\nname: From Frontmatter\n---\n# H1 Heading\n"
    assert DocIndex._extract_title(text, "fallback") == "From Frontmatter"


def test_extract_title_falls_back_to_h1():
    text = "# Just an H1\n\nbody"
    assert DocIndex._extract_title(text, "fallback") == "Just an H1"


def test_extract_title_falls_back_to_filename():
    text = "no heading no frontmatter"
    assert DocIndex._extract_title(text, "my-file") == "my-file"


# ---- category bucketing ---------------------------------------------------


@pytest.mark.parametrize(
    "rel,expected",
    [
        ("docs/runbooks/deploy.md", "runbooks"),
        ("docs/decisions/ADR-001.md", "decisions"),
        ("docs/plans/quality-uplift.md", "plans"),
        ("memory/claude/feedback_x.md", "memory"),
        ("README.md", "other"),
    ],
)
def test_category_for(rel, expected):
    assert DocIndex._category_for(rel) == expected


# ---- stats ----------------------------------------------------------------


def test_stats_returns_per_category_counts(idx):
    idx.rebuild()
    stats = idx.stats()
    assert stats.get("runbooks", 0) >= 2  # deploy + secret
    assert stats.get("decisions", 0) >= 1  # ADR-001
    assert stats.get("memory", 0) >= 2  # feedback + project (active)
