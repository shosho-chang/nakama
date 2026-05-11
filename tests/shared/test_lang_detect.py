"""Behaviour tests for ``shared.lang_detect`` — verifies the two-language
fold (zh-Hant vs en) holds across realistic samples and the < 30-char
guard returns ``unknown``."""

from __future__ import annotations

import pytest

from shared.lang_detect import detect_lang

# ── zh fixtures ──────────────────────────────────────────────────────────────

ZH_TW_SCIENCE = (
    "粒線體是細胞內負責產生能量的胞器，透過氧化磷酸化將養分轉換為三磷酸腺苷。"
    "近年研究顯示，粒線體功能失調與多種神經退化性疾病的發生密切相關。"
)
ZH_TW_LITERATURE = (
    "他緩緩走向窗邊，望著遠方逐漸西沉的夕陽，心中湧起一股難以名狀的惆悵。"
    "那片橘紅色的天光，像極了童年記憶裡，外婆家後院盛開的鳳凰花。"
)
ZH_TW_POLITICS = (
    "立法院今日針對最新提出的健保改革法案進行二讀討論，多位委員就財源規劃"
    "與分級醫療制度交換意見，預計於下週進行三讀表決。"
)
ZH_CN_TECH = (
    "人工智能正在深刻改变软件开发的工作流程，大语言模型可以协助生成代码、"
    "审查变更、甚至自动修复部分常见的回归问题，提升整体工程效率。"
)
ZH_TW_POETRY = (
    "春眠不覺曉，處處聞啼鳥。夜來風雨聲，花落知多少。"
    "這首孟浩然的《春曉》，描繪了詩人於清晨醒來時所感受到的春日景象。"
)


# ── en fixtures ──────────────────────────────────────────────────────────────

EN_SCIENCE = (
    "Mitochondria are the organelles responsible for ATP production through "
    "oxidative phosphorylation, and recent research has linked mitochondrial "
    "dysfunction to several neurodegenerative diseases."
)
EN_LITERATURE = (
    "He walked slowly to the window, watching the sun set in the distance. "
    "An unnameable sadness rose in his chest, the orange light reminded him "
    "of the flame trees in his grandmother's backyard."
)
EN_TECHNICAL = (
    "Pure-Python language detection runs in user space without native "
    "extensions, which simplifies deployment on Windows where compiling "
    "protobuf-based dependencies is otherwise painful."
)
EN_NARRATIVE = (
    "The two researchers had been arguing about the same paragraph for forty "
    "minutes, and neither was willing to concede that the data could support "
    "more than one reasonable interpretation."
)
EN_NEWS = (
    "Markets opened higher this morning following the central bank's "
    "announcement on monetary policy, with technology stocks leading the "
    "broader index gains during the first hour of trading."
)


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sample",
    [
        ZH_TW_SCIENCE,
        ZH_TW_LITERATURE,
        ZH_TW_POLITICS,
        ZH_CN_TECH,
        ZH_TW_POETRY,
    ],
    ids=["tw-science", "tw-literature", "tw-politics", "cn-tech", "tw-poetry"],
)
def test_zh_samples_label_zh_hant(sample: str) -> None:
    assert detect_lang(sample) == "zh-Hant"


@pytest.mark.parametrize(
    "sample",
    [EN_SCIENCE, EN_LITERATURE, EN_TECHNICAL, EN_NARRATIVE, EN_NEWS],
    ids=["sci", "lit", "tech", "narrative", "news"],
)
def test_en_samples_label_en(sample: str) -> None:
    assert detect_lang(sample) == "en"


def test_short_text_is_unknown() -> None:
    # Below the 30-char guard.
    assert detect_lang("Hello world") == "unknown"
    assert detect_lang("你好世界") == "unknown"


def test_empty_text_is_unknown() -> None:
    assert detect_lang("") == "unknown"
    assert detect_lang("   ") == "unknown"


def test_detection_is_deterministic() -> None:
    """``DetectorFactory.seed = 0`` is set at import; repeated calls on the
    same input must agree (langdetect is randomised without a seed)."""
    sample = ZH_TW_SCIENCE
    first = detect_lang(sample)
    for _ in range(10):
        assert detect_lang(sample) == first
