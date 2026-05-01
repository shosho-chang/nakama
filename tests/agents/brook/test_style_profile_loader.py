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


BLOG_CATEGORIES = ["book-review", "people", "science"]
PLATFORM_CATEGORIES = ["fb-post", "ig-carousel"]
ALL_CATEGORIES = BLOG_CATEGORIES + PLATFORM_CATEGORIES


def test_available_categories_includes_three_seed_profiles():
    cats = available_categories()
    assert "book-review" in cats
    assert "people" in cats
    assert "science" in cats


def test_available_categories_includes_line1_platform_profiles():
    """Line 1 podcast repurpose 的 FB / IG renderer profile（Slice 5+6）。"""
    cats = available_categories()
    assert "fb-post" in cats
    assert "ig-carousel" in cats


@pytest.mark.parametrize("category", ALL_CATEGORIES)
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
    """Blog publishing profiles 的 primary_category 必須是 DraftV1 Literal 成員。

    Platform profiles（fb-post / ig-carousel）不走 DraftV1 publishing path，
    primary_category 故意設成自身 category 名（不在 Literal 內）— 見下一個 test。
    """
    from shared.schemas.publishing import DraftV1

    allowed = set(DraftV1.model_fields["primary_category"].annotation.__args__)
    for category in BLOG_CATEGORIES:
        profile = load_style_profile(category)
        assert profile.primary_category in allowed, (
            f"{category} primary_category={profile.primary_category!r} "
            f"不在 DraftV1.primary_category Literal 內"
        )


def test_platform_profile_primary_category_not_in_draft_v1_literal():
    """Platform profile（FB/IG）誤用走 blog publishing path 會被 pydantic 擋下。

    Slice 7/8 的 FBRenderer / IGRenderer 不應建構 DraftV1 — 此測試 ensure 任何
    accidental call to DraftV1(primary_category=profile.primary_category) 會 raise，
    避免 platform-specific 內容被當 blog post 推進 publishing queue。
    """
    from shared.schemas.publishing import DraftV1

    allowed = set(DraftV1.model_fields["primary_category"].annotation.__args__)
    for category in PLATFORM_CATEGORIES:
        profile = load_style_profile(category)
        assert profile.primary_category not in allowed, (
            f"{category} primary_category={profile.primary_category!r} 落入 DraftV1 Literal — "
            f"platform profile 應使用獨立命名（如 {category}）以阻擋 blog publishing 誤用"
        )


@pytest.mark.parametrize("category", PLATFORM_CATEGORIES)
def test_platform_profile_does_not_pollute_detect_category(category):
    """Platform profile detect_keywords 必須空（不參與 topic→category 自動分類）。

    detect_category 用於 blog 編寫流程的 topic auto-routing；FB/IG renderer 直接
    load_style_profile("fb-post" / "ig-carousel") 取用，不該被 topic 字串誤觸。
    """
    profile = load_style_profile(category)
    assert profile.detect_keywords == (), (
        f"{category}.detect_keywords={profile.detect_keywords} 應為空 tuple — "
        f"platform profile 不參與 detect_category"
    )


def test_detect_category_unaffected_by_platform_profiles():
    """加入 fb-post / ig-carousel 後，detect_category 對既有 topic 行為不變。"""
    # 三類經典案例（同 test_detect_*_by_keyword 系列）
    assert detect_category("這本書讓我收穫滿滿") == "book-review"
    assert detect_category("不正常人類研究所 EP42 的來賓訪談") == "people"
    assert detect_category("最新研究：Zone 2 訓練對粒線體的影響") == "science"
    # FB / IG / podcast / carousel 字面也不會誤命中 platform profile
    assert detect_category("發 FB 貼文宣傳這件事") is None
    assert detect_category("IG carousel 排版設計") is None
