"""Tests for agents/brook/style_profile_loader.py."""

from __future__ import annotations

import re

import pytest

from agents.brook.style_profile_loader import (
    StyleProfile,
    available_categories,
    detect_category,
    load_style_profile,
)

PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9-]+@\d+\.\d+\.\d+$")


def test_available_categories_includes_three_seed_profiles():
    cats = available_categories()
    assert "book-review" in cats
    assert "people" in cats
    assert "science" in cats


@pytest.mark.parametrize("category", ["book-review", "people", "science"])
def test_load_each_seed_profile(category):
    profile = load_style_profile(category)
    assert isinstance(profile, StyleProfile)
    assert profile.category == category
    assert PROFILE_ID_PATTERN.match(profile.profile_id), (
        f"profile_id {profile.profile_id!r} 不符 DraftV1.style_profile_id pattern"
    )
    assert profile.body.strip(), "profile body 不應為空（md 載入失敗？）"
    assert profile.word_count_min > 0
    assert profile.word_count_max >= profile.word_count_min


def test_book_review_forbids_emoji():
    """_extraction-notes.md §2 硬規則：書評類完全不用 emoji。"""
    profile = load_style_profile("book-review")
    assert profile.forbid_emoji is True


def test_load_unknown_category_raises():
    with pytest.raises(FileNotFoundError):
        load_style_profile("nonexistent-category")


def test_detect_book_review_by_keyword():
    assert detect_category("這本書讓我收穫滿滿") == "book-review"


def test_detect_people_by_keyword():
    assert detect_category("不正常人類研究所 EP42 的來賓訪談") == "people"


def test_detect_science_by_keyword():
    assert detect_category("最新研究：Zone 2 訓練對粒線體的影響") == "science"


def test_detect_returns_none_when_ambiguous():
    # 同時命中 book-review（這本書）+ people（訪談）各一次 → tie → None
    result = detect_category("這本書整理了訪談內容")
    assert result is None


def test_detect_returns_none_when_no_match():
    assert detect_category("純隨機文字 quack zoot blob") is None


def test_primary_category_matches_draft_v1_literal():
    """StyleProfile.primary_category 必須是 DraftV1.primary_category Literal 成員。"""
    from shared.schemas.publishing import DraftV1

    allowed = set(DraftV1.model_fields["primary_category"].annotation.__args__)
    for category in ["book-review", "people", "science"]:
        profile = load_style_profile(category)
        assert profile.primary_category in allowed, (
            f"{category} primary_category={profile.primary_category!r} "
            f"不在 DraftV1.primary_category Literal 內"
        )
