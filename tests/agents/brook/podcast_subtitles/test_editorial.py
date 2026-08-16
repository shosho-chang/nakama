from __future__ import annotations

from agents.brook.podcast_subtitles.editorial import (
    editorial_detector_identity,
    inspect_editorial_text,
)


def test_house_style_allows_only_balanced_book_and_quote_delimiters() -> None:
    assert inspect_editorial_text("她說「請讀《大腦簡史》」") == ()

    findings = inspect_editorial_text("她說：《大腦簡史》。")

    assert [finding.code for finding in findings] == [
        "forbidden_punctuation",
        "forbidden_punctuation",
    ]
    assert [finding.observed for finding in findings] == ["：", "。"]


def test_ascii_lexical_punctuation_is_allowed_only_inside_one_word() -> None:
    assert inspect_editorial_text("we'reco-workingatangiecreates.io") == ()

    findings = inspect_editorial_text("No,we'redone?")

    assert [finding.observed for finding in findings] == [",", "?"]


def test_mismatched_or_unclosed_house_delimiters_are_review_signals() -> None:
    mismatched = inspect_editorial_text("《書名")
    crossed = inspect_editorial_text("《書名」")

    assert [(item.code, item.positions, item.observed) for item in mismatched] == [
        ("unbalanced_house_delimiter", (0,), "《")
    ]
    assert [(item.code, item.positions, item.observed) for item in crossed] == [
        ("unbalanced_house_delimiter", (0, 3), "《」")
    ]


def test_traditional_detection_never_rewrites_and_avoids_taiwan_phrase_mutation() -> None:
    findings = inspect_editorial_text("这是错误")

    assert [finding.code for finding in findings] == [
        "simplified_chinese_suspected",
        "simplified_chinese_suspected",
    ]
    assert [(finding.observed, finding.detector_output) for finding in findings] == [
        ("这", "這"),
        ("错误", "錯誤"),
    ]
    assert inspect_editorial_text("類型與對象") == ()
    assert inspect_editorial_text("社群與尿床") == ()
    assert inspect_editorial_text("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申") == ()


def test_traditional_detection_uses_phrase_context_for_ambiguous_characters() -> None:
    findings = inspect_editorial_text("一周后云端里面")

    assert [finding.code for finding in findings] == [
        "simplified_chinese_suspected",
        "simplified_chinese_suspected",
    ]
    assert [
        (finding.positions, finding.observed, finding.detector_output)
        for finding in findings
    ] == [
        ((3,), "云", "雲"),
        ((5,), "里", "裡"),
    ]


def test_traditional_detection_preserves_valid_taiwan_contexts() -> None:
    for text in ("社群", "尿床", "皇后", "公里", "干涉", "天干地支"):
        assert inspect_editorial_text(text) == ()


def test_traditional_detection_preserves_formal_correction_gold_text() -> None:
    for text in ("類型", "約會對象", "競爭的對象", "老娘就是棒"):
        assert inspect_editorial_text(text) == ()


def test_detector_can_be_injected_and_expansion_is_conservatively_addressed() -> None:
    findings = inspect_editorial_text(
        "甲乙丙",
        convert_to_traditional=lambda _text: "甲替換丙",
    )

    assert findings[0].positions == (1,)
    assert findings[0].observed == "乙"
    assert findings[0].detector_output == "替換"


def test_detector_identity_is_sha256_and_repeatable() -> None:
    first = editorial_detector_identity()

    assert len(first) == 64
    assert first == editorial_detector_identity()
