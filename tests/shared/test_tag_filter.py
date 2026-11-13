"""Tests for shared/tag_filter.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.tag_filter import (
    TagRegistry,
    filter_tags,
    load_registry,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _reset_tag_filter_cache():
    reset_cache()
    yield
    reset_cache()


def _write_registry(
    tmp_path: Path,
    *,
    strict: bool,
    whitelist: list[str],
    blacklist: list[str],
) -> Path:
    path = tmp_path / "tag-whitelist.yaml"
    path.write_text(
        "strict_whitelist: "
        + ("true" if strict else "false")
        + "\nwhitelist:\n"
        + "".join(f"  - {t}\n" for t in whitelist)
        + "blacklist:\n"
        + "".join(f"  - {t}\n" for t in blacklist),
        encoding="utf-8",
    )
    return path


def test_load_registry_reads_yaml(tmp_path):
    path = _write_registry(tmp_path, strict=True, whitelist=["a", "b"], blacklist=["x"])
    reg = load_registry(path)
    assert reg.strict_whitelist is True
    assert reg.whitelist == frozenset({"a", "b"})
    assert reg.blacklist == frozenset({"x"})


def test_load_registry_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_registry(tmp_path / "does-not-exist.yaml")


def test_accept_whitelisted_tags():
    reg = TagRegistry(
        strict_whitelist=True,
        whitelist=frozenset({"book-review", "health"}),
        blacklist=frozenset(),
    )
    result = filter_tags(["book-review", "health"], registry=reg)
    assert result.accepted == ["book-review", "health"]
    assert result.rejected == []


def test_reject_blacklisted_tags():
    reg = TagRegistry(
        strict_whitelist=False,
        whitelist=frozenset({"health"}),
        blacklist=frozenset({"cancer-cure"}),
    )
    result = filter_tags(["health", "cancer-cure"], registry=reg)
    assert result.accepted == ["health"]
    assert result.rejected == [("cancer-cure", "blacklisted")]


def test_strict_whitelist_rejects_unknown():
    reg = TagRegistry(
        strict_whitelist=True,
        whitelist=frozenset({"book-review"}),
        blacklist=frozenset(),
    )
    result = filter_tags(["book-review", "unknown-tag"], registry=reg)
    assert result.accepted == ["book-review"]
    assert result.rejected == [("unknown-tag", "not_in_whitelist")]


def test_non_strict_accepts_unknown():
    """Phase 1 seed 不完整，non-strict 允許未匹配 tag 通過。"""
    reg = TagRegistry(
        strict_whitelist=False,
        whitelist=frozenset({"book-review"}),
        blacklist=frozenset(),
    )
    result = filter_tags(["book-review", "unknown-tag"], registry=reg)
    assert result.accepted == ["book-review", "unknown-tag"]
    assert result.rejected == []


def test_deduplicates_preserving_order():
    reg = TagRegistry(
        strict_whitelist=False,
        whitelist=frozenset({"a", "b"}),
        blacklist=frozenset(),
    )
    result = filter_tags(["a", "b", "a"], registry=reg)
    assert result.accepted == ["a", "b"]
    assert result.rejected == [("a", "duplicate")]


def test_blacklist_beats_whitelist():
    """一個 tag 同時出現在 whitelist + blacklist 時，blacklist 勝。"""
    reg = TagRegistry(
        strict_whitelist=True,
        whitelist=frozenset({"foo"}),
        blacklist=frozenset({"foo"}),
    )
    result = filter_tags(["foo"], registry=reg)
    assert result.accepted == []
    assert result.rejected == [("foo", "blacklisted")]


def test_max_tags_truncates_and_marks_overflow():
    reg = TagRegistry(
        strict_whitelist=False,
        whitelist=frozenset({"a", "b", "c", "d"}),
        blacklist=frozenset(),
    )
    result = filter_tags(["a", "b", "c", "d"], max_tags=2, registry=reg)
    assert result.accepted == ["a", "b"]
    assert result.rejected == [("c", "over_limit"), ("d", "over_limit")]


def test_empty_input():
    reg = TagRegistry(
        strict_whitelist=True,
        whitelist=frozenset({"a"}),
        blacklist=frozenset(),
    )
    result = filter_tags([], registry=reg)
    assert result.accepted == []
    assert result.rejected == []


def test_repo_tag_whitelist_yaml_loads():
    """sanity check：repo 內的 config/tag-whitelist.yaml 本身可被載入。"""
    reg = load_registry()
    assert "book-review" in reg.whitelist
    assert "cancer-cure" in reg.blacklist
