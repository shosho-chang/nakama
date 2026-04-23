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


def test_detect_ascii_keyword_respects_word_boundary():
    """`EP`（people detect_keywords）不能吃到 step / help / deep 等內嵌字串。"""
    # help / deep / step 都含 "ep" substring 但都不是獨立詞
    assert detect_category("help me deep thinking at each step") is None


def test_detect_ascii_numeric_keyword_respects_word_boundary():
    """`168`（science detect_keywords）不能吃到 1680 / 2168 等較長數字。"""
    assert detect_category("ascii code 1680 in unicode spec 21681") is None


def test_detect_ascii_keyword_matches_on_boundary():
    """正確被 `EP42` → `EP` 命中當 people 訊號。"""
    assert detect_category("podcast EP42 來賓分享") == "people"


def test_detect_case_insensitive_for_ascii():
    """Isbn / isbn / ISBN 都算命中 book-review。"""
    assert detect_category("isbn 978-0-123-45678-9 的那本書") == "book-review"


def test_detect_science_sleep_domain():
    """科普四大領域之一的睡眠主題，應命中 science（seed 階段漏收錄）。"""
    assert detect_category("睡眠不足對皮質醇的長期影響") == "science"


def test_detect_science_metabolic_signature_terms():
    """BDNF / mTOR / 褪黑激素 等代謝神經生化簽名詞應穩定打中 science。"""
    assert detect_category("BDNF 與 mTOR 的交互作用") == "science"


def test_detect_science_across_domains_accumulates():
    """單篇同時觸及多個子領域（飲食+情緒）時 hit count 應累加而非互消。"""
    # 果糖 + 發炎 + 多巴胺 三個 hits
    assert detect_category("果糖、發炎反應、多巴胺獎勵迴路") == "science"


def test_detect_people_via_episode_marker():
    """人物文常用「本集」「這集」導引，不只是 EP{n}。"""
    assert detect_category("本集來賓聊了創業八年的心路") == "people"


def test_detect_book_review_via_goodreads():
    """Goodreads / 推薦序 / 書單 等延伸簽名詞應命中 book-review。"""
    assert detect_category("這本書在 Goodreads 上超過十萬評分") == "book-review"


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
