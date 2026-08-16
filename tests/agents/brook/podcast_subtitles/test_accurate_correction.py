from __future__ import annotations

from agents.brook.podcast_subtitles.accurate_correction import (
    CorrectionReferenceSource,
    CorrectionReviewSelection,
    apply_review_selections,
    bounded_review_packets,
    correct_recognition,
    parse_accurate_correction_json,
    render_accurate_correction_json,
)
from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    EvidenceToken,
    RecognitionEvidence,
)

H_AUDIO = "a" * 64
H_CONFIG = "b" * 64
H_PRIMARY = "c" * 64
H_SECONDARY = "d" * 64


def _evidence(
    adapter: str,
    raw_hash: str,
    tokens: tuple[tuple[str, int, int], ...],
) -> RecognitionEvidence:
    return RecognitionEvidence(
        episode_id="anji",
        invocation_id=f"{adapter}-invocation",
        adapter=adapter,
        model=f"{adapter}-model",
        language="zh-Hant-TW",
        config_hash=H_CONFIG,
        raw_output=ArtifactDigest(
            uri=f"fixture://{adapter}",
            sha256=raw_hash,
            size_bytes=100,
        ),
        raw_output_hash=raw_hash,
        normalized_audio_hash=H_AUDIO,
        tokens=tuple(
            EvidenceToken(
                id=f"{adapter}-{index}",
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                confidence=0.95,
                speaker="speaker-1",
            )
            for index, (text, start_ms, end_ms) in enumerate(tokens)
        ),
    )


def _book(text: str) -> CorrectionReferenceSource:
    return CorrectionReferenceSource(
        source_id="book-taiwan-made",
        kind="book",
        locator="book://台灣製造/chapter-1",
        text=text,
        title="台灣製造",
    )


def _outline(text: str) -> CorrectionReferenceSource:
    return CorrectionReferenceSource(
        source_id="outline-anji",
        kind="outline",
        locator="outline://安吉訪綱",
        text=text,
        title="安吉訪綱",
    )


def _glossary(text: str) -> CorrectionReferenceSource:
    return CorrectionReferenceSource(
        source_id="episode-glossary",
        kind="glossary",
        locator="glossary://anji",
        text=text,
        title="EP119 curated glossary",
    )


def test_same_text_with_different_tokenization_is_consensus() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("冒", 0, 100), ("牌者", 100, 300), ("情結", 300, 500)),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("冒牌", 0, 200), ("者情結", 200, 500)),
    )

    result = correct_recognition(primary=primary, corroborating=corroborating)

    assert result.status == "completed"
    assert result.text == "冒牌者情結"
    assert result.unresolved == ()
    assert [token.start_ms for token in result.tokens] == [0, 100, 200, 300, 400]
    assert [token.end_ms for token in result.tokens] == [100, 200, 300, 400, 500]


def test_equal_length_output_keeps_per_token_primary_and_temporally_local_secondary_refs() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("甲", 0, 100), ("乙", 100, 200), ("丙", 200, 300)),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("甲乙", 0, 180), ("丙", 220, 300)),
    )

    result = correct_recognition(primary=primary, corroborating=corroborating)

    assert result.text == "甲乙丙"
    assert [(token.start_ms, token.end_ms) for token in result.tokens] == [
        (0, 100),
        (100, 200),
        (200, 300),
    ]
    assert [token.source_primary_token_ids for token in result.tokens] == [
        ("qwen-0",),
        ("qwen-1",),
        ("qwen-2",),
    ]
    token_ref_ids = [
        tuple(ref.rsplit(":", 1)[-1] for ref in token.recognition_refs) for token in result.tokens
    ]
    assert token_ref_ids == [
        ("qwen-0", "faster-0"),
        ("qwen-1", "faster-0"),
        ("qwen-2", "faster-1"),
    ]
    assert all(len(token.recognition_refs) <= 2 for token in result.tokens)


def test_true_recognition_difference_remains_reviewable_but_does_not_block() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("今天下雨", 0, 800),))
    corroborating = _evidence("faster", H_SECONDARY, (("今天放晴", 0, 800),))

    result = correct_recognition(primary=primary, corroborating=corroborating)

    assert result.status == "completed_with_review"
    assert result.text == "今天下雨"
    assert len(result.unresolved) == 1
    assert result.unresolved[0].category == "recognition_disagreement"
    assert result.unresolved[0].current == "下雨"
    assert result.unresolved[0].candidates == ("放晴",)
    assert len(result.unresolved[0].recognition_lineage) == 2


def test_corroborating_omission_is_reported_without_inventing_missing_text() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("我", 0, 100), ("知道", 300, 500)),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("我", 0, 100), ("不", 150, 250), ("知道", 300, 500)),
    )

    result = correct_recognition(primary=primary, corroborating=corroborating)

    assert result.text == "我知道"
    gaps = [item for item in result.unresolved if item.category == "recognition_coverage_gap"]
    assert len(gaps) == 1
    assert "不" in gaps[0].candidates[0]
    assert 0 < gaps[0].start_ms < gaps[0].end_ms < 500


def test_simplified_text_is_projected_to_taiwan_traditional() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("台湾制造", 0, 800),))
    corroborating = _evidence("faster", H_SECONDARY, (("台湾制造", 0, 800),))

    result = correct_recognition(primary=primary, corroborating=corroborating)

    assert result.text == "臺灣製造"
    assert result.status == "completed"
    assert [item.category for item in result.applied] == ["traditional_orthography"]
    mutation = result.applied[0]
    assert len(mutation.recognition_lineage) == 2
    assert mutation.references[0].locator == "opencc:s2tw"


def test_provider_sentence_punctuation_is_removed_but_latin_orthography_is_kept() -> None:
    primary = _evidence(
        "faster",
        H_PRIMARY,
        (("No, we're in a co-working space at angiecreates.io?", 0, 1000),),
    )
    corroborating = _evidence(
        "qwen",
        H_SECONDARY,
        (("No we're in a co-working space at angiecreates.io", 0, 1000),),
    )

    result = correct_recognition(primary=primary, corroborating=corroborating)

    assert result.text == "No we're in a co-working space at angiecreates.io"
    assert result.status == "completed"
    assert [item.category for item in result.applied] == ["traditional_orthography"]


def test_unique_authoritative_strict_homophone_spelling_is_applied() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("冒排者情節", 1000, 2000),))
    corroborating = _evidence("faster", H_SECONDARY, (("冒牌者情結", 1000, 2000),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_book("這一章討論冒牌者情結，以及它如何形成。"),),
    )

    assert result.status == "completed"
    assert result.text == "冒牌者情結"
    assert result.unresolved == ()
    decision = next(
        item for item in result.applied if item.category == "authoritative_book_spelling"
    )
    assert decision.current == "排者情節"
    assert decision.selected == "牌者情結"
    assert len(decision.recognition_lineage) == 2
    assert {item.kind for item in decision.references} == {"book"}
    assert decision.references[0].locator == "book://台灣製造/chapter-1"


def test_competing_authoritative_homophones_are_not_applied() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("冒排者情節", 0, 1000),))
    corroborating = _evidence("faster", H_SECONDARY, (("冒牌者情結", 0, 1000),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_book("版本甲寫冒牌者情結；版本乙寫冒排者情節。"),),
    )

    assert result.text == "冒排者情節"
    assert result.status == "completed_with_review"
    assert not [item for item in result.applied if item.category == "authoritative_book_spelling"]
    assert result.unresolved[0].category == "recognition_disagreement"


def test_outline_can_add_candidate_but_cannot_auto_apply() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("冒排者情節", 0, 1000),))
    corroborating = _evidence("faster", H_SECONDARY, (("冒牌者情結", 0, 1000),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_outline("請安吉談談冒牌者情結。"),),
    )

    assert result.text == "冒排者情節"
    assert not [item for item in result.applied if item.category == "authoritative_book_spelling"]
    issue = result.unresolved[0]
    assert issue.current == "排者情節"
    assert issue.candidates == ("牌者情結",)
    assert {item.kind for item in issue.references} == {"outline"}


def test_reference_never_overwrites_two_recogniser_consensus() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("冒排者情節", 0, 1000),))
    corroborating = _evidence("faster", H_SECONDARY, (("冒排者情節", 0, 1000),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_book("正確書名使用冒牌者情結。"),),
    )

    assert result.status == "completed"
    assert result.text == "冒排者情節"
    assert result.applied == ()
    assert result.unresolved == ()


def test_every_applied_mutation_has_two_asr_lineages_and_reference_evidence() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("台湾的冒排者情節", 0, 1800),))
    corroborating = _evidence("faster", H_SECONDARY, (("台湾的冒牌者情結", 0, 1800),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_book("台灣的冒牌者情結值得研究。"),),
    )

    assert result.applied
    for decision in result.applied:
        assert len(decision.recognition_lineage) == 2
        assert decision.recognition_lineage[0].evidence_hash
        assert decision.recognition_lineage[1].evidence_hash
        assert decision.references
        assert all(reference.source_id and reference.locator for reference in decision.references)


def test_json_render_parse_round_trip_is_canonical() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("今天下雨", 0, 800),))
    corroborating = _evidence("faster", H_SECONDARY, (("今天放晴", 0, 800),))
    result = correct_recognition(primary=primary, corroborating=corroborating)

    rendered = render_accurate_correction_json(result)
    replayed = parse_accurate_correction_json(rendered)

    assert replayed == result
    assert render_accurate_correction_json(replayed) == rendered


def test_multiple_glossary_fragments_cannot_rewrite_one_large_asr_difference() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("不正常人類就說我是JoJo", 0, 1600),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("不正常人類研究所我是修修", 0, 1600),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("節目名稱：不正常人類研究所\n主持人：修修"),),
    )

    assert result.text == "不正常人類就說我是JoJo"
    assert not any(item.current != item.selected for item in result.applied)
    assert result.unresolved


def test_outline_exact_candidate_is_review_evidence_not_mutation_authority() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("冒排者情結", 0, 1000),))
    corroborating = _evidence("faster", H_SECONDARY, (("冒牌者情結", 0, 1000),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_outline("冒牌者情結"),),
    )

    assert result.text == "冒排者情結"
    assert not result.applied
    assert result.unresolved
    assert {item.kind for item in result.unresolved[0].references} == {"outline"}


def test_outline_near_match_without_an_exact_asr_literal_cannot_apply() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("冒排者情節", 0, 1000),))
    corroborating = _evidence("faster", H_SECONDARY, (("冒牌者情節", 0, 1000),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_outline("冒牌者情結"),),
    )

    assert result.text == "冒排者情節"
    assert not result.applied
    assert result.unresolved


def test_two_matching_asrs_can_use_one_unique_near_glossary_term() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("修休", 0, 500),))
    corroborating = _evidence("faster", H_SECONDARY, (("修休", 0, 500),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("主持人：修修"),),
    )

    assert result.text == "修修"
    assert result.status == "completed"
    assert result.applied[0].category == "asr_supported_curated_glossary"


def test_embedded_consensus_han_glossary_term_is_corrected_without_accepting_rest() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("今天寶羅也來了但是外面下雨", 0, 1_200),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("今天寶羅也來了但是外面放晴", 0, 1_200),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("人物：保羅"),),
    )

    assert result.text == "今天保羅也來了但是外面下雨"
    assert [item.selected for item in result.applied] == ["保羅"]
    assert result.unresolved
    assert result.unresolved[0].current == "下雨"
    assert result.unresolved[0].candidates == ("放晴",)


def test_primary_only_strict_homophone_uses_episode_attested_authoritative_name() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("我認識保羅", 0, 500), ("後來寶羅就離開了", 1_500, 2_200)),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("我認識保羅", 0, 500), ("後來就離開了", 1_500, 2_200)),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(
            _book("保羅後來寫了一本書。"),
            _glossary("人物：保羅"),
        ),
    )

    assert result.text == "我認識保羅後來保羅就離開了"
    decision = next(item for item in result.applied if item.current == "寶羅")
    assert decision.selected == "保羅"
    assert {item.kind for item in decision.references} == {"book", "glossary"}
    assert result.unresolved


def test_book_attested_common_homophone_is_not_rewritten_as_a_name() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("我認識保羅", 0, 500), ("內容包羅萬象", 1_500, 2_200)),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("我認識保羅", 0, 500), ("內容萬象", 1_500, 2_200)),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(
            _book("保羅是作者；這本書內容包羅萬象。"),
            _glossary("人物：保羅"),
        ),
    )

    assert result.text == "我認識保羅內容包羅萬象"
    assert not any(item.current == "包羅" for item in result.applied)
    assert result.unresolved


def test_embedded_ascii_glossary_case_is_corrected_without_accepting_rest() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("我們每天有一個活動叫做nest然後外面下雨", 0, 1_200),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("我們每天有一個活動叫做Nest然後外面放晴", 0, 1_200),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("活動：Nest"),),
    )

    assert result.text == "我們每天有一個活動叫做Nest然後外面下雨"
    assert any(item.current == "nest" and item.selected == "Nest" for item in result.applied)
    assert result.unresolved


def test_secondary_exact_cross_script_glossary_term_replaces_only_that_term() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("我應該怎麼做帕克斯接下來應該怎麼寫作", 0, 1_200),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("我應該怎麼做Podcast接下來應該怎麼寫作", 0, 1_200),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("媒介：Podcast"),),
    )

    assert result.text == "我應該怎麼做Podcast接下來應該怎麼寫作"
    decision = next(item for item in result.applied if item.selected == "Podcast")
    assert decision.current == "帕克斯"
    assert {item.kind for item in decision.references} == {"glossary"}
    assert result.unresolved == ()


def test_secondary_exact_glossary_repairs_duplicated_numeral_phrase() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("那時候連續三十三十天發文挑戰然後每天寫一點", 0, 1_200),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("那時候連續30天發文挑戰然後每天寫一點", 0, 1_200),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("挑戰：30 天發文挑戰"),),
    )

    assert result.text == "那時候連續30 天發文挑戰然後每天寫一點"
    decision = next(item for item in result.applied if item.selected == "30 天發文挑戰")
    assert decision.current == "三十三十天發文挑戰"
    assert result.unresolved == ()


def test_secondary_exact_ascii_glossary_repairs_missing_prefix() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("我想要用健身帶給別人powerment但是做不到", 0, 1_200),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("我想要用健身帶給別人empowerment但是還做不到", 0, 1_200),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("概念：empowerment"),),
    )

    assert result.text == "我想要用健身帶給別人empowerment但是做不到"
    assert any(
        item.current == "powerment" and item.selected == "empowerment" for item in result.applied
    )
    assert result.unresolved


def test_two_wrong_latin_name_hypotheses_need_authoritative_and_glossary_literal() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("牆上寫Be Here Now是Rundas的這句話", 0, 1_200),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("牆上寫Be Here Now是Roundhouse的這句話", 0, 1_200),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(
            _book("美國靈性導師拉姆．達斯（Ram Dass）的話。"),
            _glossary("作者：Ram Dass"),
        ),
    )

    assert result.text == "牆上寫Be Here Now是Ram Dass的這句話"
    decision = next(item for item in result.applied if item.selected == "Ram Dass")
    assert decision.current == "Rundas"
    assert {item.kind for item in decision.references} == {"book", "glossary"}
    assert result.unresolved == ()


def test_two_wrong_latin_name_hypotheses_cannot_use_glossary_without_book() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("牆上寫Be Here Now是Rundas的這句話", 0, 1_200),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("牆上寫Be Here Now是Roundhouse的這句話", 0, 1_200),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("作者：Ram Dass"),),
    )

    assert result.text == "牆上寫Be Here Now是Rundas的這句話"
    assert not any(item.selected == "Ram Dass" for item in result.applied)
    assert result.unresolved


def test_reference_only_sentence_is_never_inserted() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("今天來聊天", 0, 800),))
    corroborating = _evidence("faster", H_SECONDARY, (("今天來聊天", 0, 800),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("這是一句沒有被辨識器聽到的完整句子"),),
    )

    assert result.text == "今天來聊天"
    assert result.applied == ()


def test_glossary_preserves_official_ascii_internal_space_and_timing() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("RamDass", 120, 920),))
    corroborating = _evidence("faster", H_SECONDARY, (("Ram Dass", 120, 920),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("作者：Ram Dass"),),
    )

    assert result.text == "Ram Dass"
    assert "".join(token.text for token in result.tokens) == "Ram Dass"
    assert result.tokens[0].start_ms == 120
    assert result.tokens[-1].end_ms == 920
    assert {token.source_primary_token_ids for token in result.tokens} == {("qwen-0",)}
    assert result.applied[0].selected == "Ram Dass"
    assert result.applied[0].references[0].excerpt == "Ram Dass"
    assert parse_accurate_correction_json(render_accurate_correction_json(result)) == result


def test_glossary_preserves_official_chinese_middle_dot() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("保羅米勒", 0, 600),))
    corroborating = _evidence("faster", H_SECONDARY, (("保羅米勒", 0, 600),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("作者：保羅．米勒"),),
    )

    assert result.text == "保羅．米勒"
    assert result.status == "completed"
    assert result.tokens[0].start_ms == 0
    assert result.tokens[-1].end_ms == 600


def test_glossary_preserves_official_ascii_case_when_asr_case_differs() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("ram dass", 0, 800),))
    corroborating = _evidence("faster", H_SECONDARY, (("RAMDASS", 0, 800),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("Ram Dass"),),
    )

    assert result.text == "Ram Dass"
    assert result.status == "completed"
    assert result.applied[0].selected == "Ram Dass"


def test_punctuation_only_glossary_term_fails_closed() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("保羅米勒", 0, 600),))
    corroborating = _evidence("faster", H_SECONDARY, (("保羅米勒", 0, 600),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("作者：．"),),
    )

    assert result.text == "保羅米勒"
    assert result.applied == ()


def test_unresolved_review_packet_has_bounded_surrounding_context() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("前言", 0, 200), ("甲", 200, 400), ("結尾", 400, 600)),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("前言", 0, 200), ("乙", 200, 400), ("結尾", 400, 600)),
    )
    result = correct_recognition(primary=primary, corroborating=corroborating)

    packets = bounded_review_packets(result, radius=2)

    assert len(packets) == 1
    assert packets[0].before == "前言"
    assert packets[0].current == "甲"
    assert packets[0].after == "結尾"
    assert packets[0].candidates == ("乙",)
    assert packets[0].window_current == "前言甲結尾"
    assert packets[0].window_candidates == ("前言乙結尾",)


def test_real_clip_008_explicit_glossary_selects_jingying() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("就不是這種類型的精英", 1_715_350, 1_722_000),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("就不是這種類型的靜音", 1_715_350, 1_722_000),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("詞彙：菁英"),),
    )

    assert result.text == "就不是這種類型的菁英"
    assert result.unresolved == ()
    assert any(
        item.category == "asr_supported_curated_glossary" and item.selected == "菁英"
        for item in result.applied
    )


def test_book_phrase_does_not_expand_one_character_asr_difference() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("這個是臺誇張了", 0, 1_000),))
    corroborating = _evidence("faster", H_SECONDARY, (("這個是太誇張了", 0, 1_000),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_book("這件事是太誇張。"),),
    )

    assert result.text == "這個是臺誇張了"
    assert not any(item.current != item.selected for item in result.applied)


def test_book_ngram_does_not_phonetically_rewrite_asr_fragment() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("路上深深的覺得", 0, 1_000),))
    corroborating = _evidence("faster", H_SECONDARY, (("上深深地覺得", 0, 1_000),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_book("填完資料再往上申請。"),),
    )

    assert result.text == "路上深深的覺得"
    assert not any(item.current != item.selected for item in result.applied)


def test_common_one_character_book_hit_needs_both_consensus_anchors() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("光鮮亮麗哦臺中女中", 0, 1_000),))
    corroborating = _evidence("faster", H_SECONDARY, (("光鮮亮麗有臺中女中", 0, 1_000),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_book("她有臺中的求學經驗。"),),
    )

    assert result.text == "光鮮亮麗哦臺中女中"
    assert not any(item.current != item.selected for item in result.applied)


def test_reference_phrase_cannot_turn_secondary_only_text_into_an_insertion() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("然後可以繼續", 0, 1_000),))
    corroborating = _evidence("faster", H_SECONDARY, (("然後我的可以繼續", 0, 1_000),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_book("然後我的想法改變了。"),),
    )

    assert result.text == "然後可以繼續"
    assert result.unresolved[0].category == "recognition_coverage_gap"


def test_glossary_name_cannot_replace_an_unrelated_chinese_word() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("我跟朋友一個有趣的人", 0, 1_000),))
    corroborating = _evidence("faster", H_SECONDARY, (("我跟Paul有一個有趣的人", 0, 1_000),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("受訪者：Paul"),),
    )

    assert result.text == "我跟朋友一個有趣的人"
    assert not any(item.current != item.selected for item in result.applied)


def test_glossary_name_cannot_replace_a_pronoun() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("我跟我一樣", 0, 1_000),))
    corroborating = _evidence("faster", H_SECONDARY, (("我跟Paul一樣", 0, 1_000),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("受訪者：Paul"),),
    )

    assert result.text == "我跟我一樣"
    assert not any(item.current != item.selected for item in result.applied)


def test_ascii_glossary_requires_a_complete_asr_token() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("traveling的一樣", 0, 1_000),))
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("raveling Village一樣", 0, 1_000),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(
            _book("The company is called Traveling Village."),
            _glossary("公司：Traveling Village"),
        ),
    )

    assert result.text == "traveling的一樣"
    assert not any(item.current != item.selected for item in result.applied)


def test_glossary_cannot_supply_only_a_substring_of_a_complete_term() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("今天來賓呢是臺灣制", 0, 1_000),))
    corroborating = _evidence("faster", H_SECONDARY, (("今天來賓是臺灣製", 0, 1_000),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("書名：台灣製造"),),
    )

    assert result.text == "今天來賓呢是臺灣制"
    assert not any(item.current != item.selected for item in result.applied)


def test_glossary_homophone_index_never_extracts_an_arbitrary_ngram() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("出外遊目", 0, 1_000),))
    corroborating = _evidence("faster", H_SECONDARY, (("數位遊牧", 0, 1_000),))

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_glossary("數位遊牧"),),
    )

    assert result.text == "出外遊目"
    assert not any(item.current != item.selected for item in result.applied)


def test_real_clip_009_contextual_book_phrase_does_not_hide_disagreement() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("那就是我最大的自我破壞來源", 1_909_290, 1_917_000),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("那就是我最大的自我迫害來源", 1_909_290, 1_917_000),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_book("原來這種自我破壞的衝動有個名字。"),),
    )

    assert result.text == "那就是我最大的自我破壞來源"
    assert result.unresolved
    assert not any(item.category == "reference_supported_primary" for item in result.applied)


def test_real_clip_010_exact_secondary_reference_selects_zhengda() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("所以那時候其實考上浙大", 2_141_130, 2_148_000),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("所以那時候其實考上政大", 2_141_130, 2_148_000),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(
            _book("我已經念到政大了。"),
            _outline("連考上政大都決定重考。"),
            _glossary("學校：政大"),
        ),
    )

    assert result.text == "所以那時候其實考上政大"
    assert result.unresolved == ()
    assert any(item.selected == "政大" for item in result.applied)


def test_real_clip_013_book_context_cannot_resolve_unequal_asr_phrases() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("至少我可以不會餓死這樣", 2_957_060, 2_965_000),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("至少我可以不願意餓死這樣", 2_957_060, 2_965_000),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_book("固定的收入至少讓我不會餓死。"),),
    )

    assert result.text == "至少我可以不會餓死這樣"
    assert result.unresolved
    assert not any(item.category == "reference_supported_primary" for item in result.applied)


def test_real_clip_019_two_wrong_latin_candidates_is_high_priority_defer() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("牆上寫Be Here Now是Rumours的", 4_419_230, 4_430_000),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("牆上寫Be Here Now是Roundhouse的", 4_419_230, 4_430_000),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        references=(_book("美國靈性導師拉姆．達斯（Ram Dass）的話。"),),
    )

    assert result.status == "completed_with_review"
    assert result.text == "牆上寫Be Here Now是Rumours的"
    assert len(result.unresolved) == 1
    assert result.unresolved[0].category == "recognition_disagreement_high_priority"
    packet = bounded_review_packets(result)[0]
    assert packet.priority == "high"
    assert packet.window_current == "牆上寫Be Here Now是Rumours的"
    assert packet.window_candidates == ("牆上寫Be Here Now是Roundhouse的",)


def test_lexical_alignment_emits_one_exact_local_difference() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        tuple(
            (character, index * 100, (index + 1) * 100)
            for index, character in enumerate("今天真的下雨了")
        ),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("今天", 0, 200), ("真的", 200, 400), ("放晴", 400, 600), ("了", 600, 700)),
    )

    result = correct_recognition(primary=primary, corroborating=corroborating)

    assert len(result.unresolved) == 1
    assert result.unresolved[0].current == "下雨"
    assert result.unresolved[0].candidates == ("放晴",)
    assert result.unresolved[0].target_start_char == 4
    assert result.unresolved[0].target_end_char == 6


def test_secondary_only_text_requires_exact_confirmation_before_insertion() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("我", 0, 100), ("知道", 300, 500)))
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("我", 0, 100), ("不", 150, 250), ("知道", 300, 500)),
    )

    without_confirmation = correct_recognition(
        primary=primary,
        corroborating=corroborating,
    )
    with_confirmation = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        confirmation=_evidence(
            "qwen-confirmation",
            "e" * 64,
            (("我", 0, 100), ("不", 150, 250), ("知道", 300, 500)),
        ),
    )

    assert without_confirmation.text == "我知道"
    assert without_confirmation.unresolved[0].category == "recognition_coverage_gap"
    assert with_confirmation.text == "我不知道"
    assert with_confirmation.unresolved == ()
    assert any(
        item.category == "audio_confirmed_secondary_coverage" for item in with_confirmation.applied
    )


def test_locally_confirmed_primary_only_text_is_deleted_without_accepting_rest() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("我真的不不明白然後外面下雨", 0, 800),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("我真的不明白然後外面放晴", 0, 800),),
    )
    confirmation = _evidence(
        "faster-confirmation",
        "e" * 64,
        (("我真的不明白然後外面颳風", 0, 800),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        confirmation=confirmation,
    )

    assert result.text == "我真的不明白然後外面下雨"
    deletion = next(
        item for item in result.applied if item.category == "audio_confirmed_secondary_coverage"
    )
    assert deletion.current == "不"
    assert deletion.selected == ""
    assert len(deletion.recognition_lineage) == 3
    assert result.unresolved


def test_locally_confirmed_secondary_insertion_does_not_accept_other_differences() -> None:
    primary = _evidence(
        "qwen",
        H_PRIMARY,
        (("我今天真的知道可是外面下雨", 0, 800),),
    )
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("我今天真的不知道可是外面放晴", 0, 800),),
    )
    confirmation = _evidence(
        "qwen-confirmation",
        "e" * 64,
        (("我今天真的不知道可是外面颳風", 0, 800),),
    )

    result = correct_recognition(
        primary=primary,
        corroborating=corroborating,
        confirmation=confirmation,
    )

    assert result.text == "我今天真的不知道可是外面下雨"
    insertion = next(
        item for item in result.applied if item.category == "audio_confirmed_secondary_coverage"
    )
    assert insertion.current == ""
    assert insertion.selected == "不"
    assert len(insertion.recognition_lineage) == 3
    assert result.unresolved


def test_review_selection_can_only_choose_a_stored_local_candidate() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("今天下雨", 0, 800),))
    corroborating = _evidence("faster", H_SECONDARY, (("今天放晴", 0, 800),))
    result = correct_recognition(primary=primary, corroborating=corroborating)
    decision_id = result.unresolved[0].id
    assert "-local-" in decision_id

    selected = apply_review_selections(
        result,
        (
            CorrectionReviewSelection(
                decision_id=decision_id,
                choice="candidate",
                candidate_index=0,
            ),
        ),
    )

    assert selected.text == "今天放晴"
    assert selected.unresolved == ()
    assert selected.applied[-1].category == "bounded_review_candidate_selection"

    try:
        apply_review_selections(
            result,
            (
                CorrectionReviewSelection(
                    decision_id=decision_id.split("-local-", maxsplit=1)[0],
                    choice="candidate",
                    candidate_index=0,
                ),
            ),
        )
    except ValueError as exc:
        assert "unknown decision" in str(exc)
    else:
        raise AssertionError("an ordinal-only legacy decision id must fail closed")


def test_selecting_one_of_two_local_differences_keeps_the_other_unchanged() -> None:
    for primary_text, secondary_text, first_candidate, expected in (
        (
            "今天下雨但是外面很冷",
            "今天放晴但是外面很熱",
            "放晴",
            "今天放晴但是外面很冷",
        ),
        (
            "今天放晴但是外面很熱",
            "今天下雨但是外面很冷",
            "下雨",
            "今天下雨但是外面很熱",
        ),
    ):
        result = correct_recognition(
            primary=_evidence("primary", H_PRIMARY, ((primary_text, 0, 1_200),)),
            corroborating=_evidence(
                "corroborating",
                H_SECONDARY,
                ((secondary_text, 0, 1_200),),
            ),
        )
        assert len(result.unresolved) == 2
        decision = next(item for item in result.unresolved if item.candidates == (first_candidate,))

        selected = apply_review_selections(
            result,
            (
                CorrectionReviewSelection(
                    decision_id=decision.id,
                    choice="candidate",
                    candidate_index=0,
                ),
            ),
        )

        assert selected.text == expected
        assert len(selected.unresolved) == 1
        assert selected.unresolved[0].current in {"冷", "熱"}


def test_retaining_one_local_current_resolves_only_that_decision_without_mutation() -> None:
    result = correct_recognition(
        primary=_evidence(
            "primary",
            H_PRIMARY,
            (("今天下雨但是外面很冷", 0, 1_200),),
        ),
        corroborating=_evidence(
            "corroborating",
            H_SECONDARY,
            (("今天放晴但是外面很熱", 0, 1_200),),
        ),
    )

    selected = apply_review_selections(
        result,
        (
            CorrectionReviewSelection(
                decision_id=result.unresolved[0].id,
                choice="current",
            ),
        ),
    )

    assert selected.text == result.text
    assert len(selected.unresolved) == 1
    assert selected.applied[-1].selected == selected.applied[-1].current


def test_remaining_local_target_shifts_after_unequal_length_selection() -> None:
    result = correct_recognition(
        primary=_evidence(
            "primary",
            H_PRIMARY,
            (("今天rain但是外面cold", 0, 1_200),),
        ),
        corroborating=_evidence(
            "corroborating",
            H_SECONDARY,
            (("今天sunny但是外面hot", 0, 1_200),),
        ),
    )
    first = next(item for item in result.unresolved if item.candidates == ("sunny",))
    second = next(item for item in result.unresolved if item.candidates == ("hot",))

    once = apply_review_selections(
        result,
        (
            CorrectionReviewSelection(
                decision_id=first.id,
                choice="candidate",
                candidate_index=0,
            ),
        ),
    )

    assert once.text == "今天sunny但是外面cold"
    assert once.unresolved[0].id == second.id
    assert (
        once.text[once.unresolved[0].target_start_char : once.unresolved[0].target_end_char]
        == "cold"
    )

    twice = apply_review_selections(
        once,
        (
            CorrectionReviewSelection(
                decision_id=second.id,
                choice="candidate",
                candidate_index=0,
            ),
        ),
    )
    assert twice.text == "今天sunny但是外面hot"
    assert twice.unresolved == ()


def test_local_candidate_index_is_closed_to_the_stored_tuple() -> None:
    result = correct_recognition(
        primary=_evidence("primary", H_PRIMARY, (("今天下雨", 0, 800),)),
        corroborating=_evidence(
            "corroborating",
            H_SECONDARY,
            (("今天放晴", 0, 800),),
        ),
    )

    try:
        apply_review_selections(
            result,
            (
                CorrectionReviewSelection(
                    decision_id=result.unresolved[0].id,
                    choice="candidate",
                    candidate_index=1,
                ),
            ),
        )
    except ValueError as exc:
        assert "out of range" in str(exc)
    else:
        raise AssertionError("review selection must stay inside the stored candidate tuple")


def test_coverage_review_cannot_select_secondary_without_audio_confirmation() -> None:
    primary = _evidence("qwen", H_PRIMARY, (("我", 0, 100), ("知道", 300, 500)))
    corroborating = _evidence(
        "faster",
        H_SECONDARY,
        (("我", 0, 100), ("不", 150, 250), ("知道", 300, 500)),
    )
    result = correct_recognition(primary=primary, corroborating=corroborating)

    try:
        apply_review_selections(
            result,
            (
                CorrectionReviewSelection(
                    decision_id=result.unresolved[0].id,
                    choice="candidate",
                    candidate_index=0,
                ),
            ),
        )
    except ValueError as exc:
        assert "audio confirmation" in str(exc)
    else:
        raise AssertionError("coverage insertion must not be selectable without audio confirmation")


def test_zero_width_coverage_packet_is_exact_and_current_choice_is_safe() -> None:
    result = correct_recognition(
        primary=_evidence("primary", H_PRIMARY, (("我知道", 0, 500),)),
        corroborating=_evidence(
            "corroborating",
            H_SECONDARY,
            (("我不知道", 0, 500),),
        ),
    )
    packet = bounded_review_packets(result)[0]
    decision = result.unresolved[0]

    assert packet.current == ""
    assert packet.candidates == ("不",)
    assert packet.before.endswith("我")
    assert packet.after.startswith("知道")
    assert packet.allowed_choices == ("current", "defer")
    assert decision.target_start_char == decision.target_end_char

    retained = apply_review_selections(
        result,
        (
            CorrectionReviewSelection(
                decision_id=decision.id,
                choice="current",
            ),
        ),
    )
    assert retained.text == result.text == "我知道"
    assert retained.unresolved == ()


def test_empty_hallucination_candidate_is_stored_but_cannot_be_selected() -> None:
    result = correct_recognition(
        primary=_evidence("primary", H_PRIMARY, (("我不知道", 0, 500),)),
        corroborating=_evidence(
            "corroborating",
            H_SECONDARY,
            (("我知道", 0, 500),),
        ),
    )
    packet = bounded_review_packets(result)[0]

    assert packet.current == "不"
    assert packet.candidates == ("",)
    assert packet.allowed_choices == ("current", "defer")
    try:
        apply_review_selections(
            result,
            (
                CorrectionReviewSelection(
                    decision_id=result.unresolved[0].id,
                    choice="candidate",
                    candidate_index=0,
                ),
            ),
        )
    except ValueError as exc:
        assert "audio confirmation" in str(exc)
    else:
        raise AssertionError("an unconfirmed deletion candidate must fail closed")
