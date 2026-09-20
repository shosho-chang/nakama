from __future__ import annotations

from dataclasses import dataclass

import pytest

from agents.brook.podcast_subtitles import accurate_segmentation as subject
from agents.brook.podcast_subtitles.accurate_segmentation import (
    SentenceBoundaryHint,
    segment_accurate_subtitles,
)
from agents.brook.podcast_subtitles.display_metrics import display_columns
from agents.brook.podcast_subtitles.errors import ProjectionUnsatisfiableError
from agents.brook.podcast_subtitles.profiles import HORIZONTAL_16X9


_TWO_LINE_TEST_PROFILE = HORIZONTAL_16X9.model_copy(
    update={
        "id": "test-two-line-16x9",
        "max_lines": 2,
        "target_line_display_columns": 36,
        "hard_line_display_columns": 44,
    }
)


@dataclass(frozen=True)
class _Token:
    id: str
    text: str
    start_ms: int
    end_ms: int


def _characters(
    text: str,
    *,
    step_ms: int = 180,
    speech_ms: int = 150,
    strong_pause_after: int | None = None,
) -> tuple[_Token, ...]:
    result: list[_Token] = []
    cursor = 0
    for index, character in enumerate(text):
        result.append(_Token(f"token-{index:03d}", character, cursor, cursor + speech_ms))
        cursor += step_ms
        if strong_pause_after == index:
            cursor += 800
    return tuple(result)


def _mixed(parts: tuple[str, ...]) -> tuple[_Token, ...]:
    return tuple(
        _Token(f"mixed-{index}", text, index * 500, index * 500 + 420)
        for index, text in enumerate(parts)
    )


def _rendered_text(result) -> str:
    return "".join(line for cue in result.projection.cues for line in cue.lines)


def test_real_closed_class_chain_yields_at_verified_pause_before_hard_duration_limit() -> None:
    """Regression for the first minimal unsatisfiable window in the Anji episode."""

    texts = tuple("的了對但是在在那之前其實我自己也經歷")
    timings = (
        (759_779, 759_929),
        (759_929, 760_080),
        (761_840, 762_000),
        (762_000, 762_160),
        (762_160, 762_240),
        (762_240, 762_640),
        (762_640, 762_800),
        (762_800, 762_960),
        (762_960, 763_120),
        (763_120, 763_360),
        (763_360, 763_520),
        (763_520, 763_600),
        (763_600, 763_840),
        (763_840, 763_920),
        (763_920, 764_160),
        (764_160, 764_480),
        (766_640, 766_800),
        (766_800, 766_880),
    )
    tokens = tuple(
        _Token(f"anji-000039{46 + index:02d}", text, start_ms, end_ms)
        for index, (text, (start_ms, end_ms)) in enumerate(zip(texts, timings, strict=True))
    )

    result = segment_accurate_subtitles(
        tokens,
        episode_id="20260415-anji",
        generation_id="real-closed-class-chain",
        audio_start_ms=tokens[0].start_ms,
        audio_end_ms=tokens[-1].end_ms,
    )

    assert _rendered_text(result) == "".join(texts)
    assert len(result.projection.cues) >= 2
    assert all(
        cue.end_ms - cue.start_ms <= HORIZONTAL_16X9.max_cue_duration_ms
        for cue in result.projection.cues
    )
    assert any(
        decision.cue_relation == "preferred" and decision.pause_ms >= 1_700
        for decision in result.boundary_decisions
    )


@pytest.mark.parametrize(
    "phrase",
    (
        "不正常人類研究所",
        "冒牌者情結",
        "如果你",
        "學業經歷",
        "在女中之前",
        "人生真實樣貌的一個展現",
    ),
)
def test_known_chinese_phrases_are_not_split_at_cue_or_line_boundaries(phrase: str) -> None:
    text = "前面先交代幾句背景內容" + phrase + "後面繼續補充完整的說明內容"
    tokens = _characters(text)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="anji",
        generation_id=f"phrase-{phrase}",
        protected_terms=(phrase,),
        audio_end_ms=tokens[-1].end_ms,
    )

    assert any(phrase in line for cue in result.projection.cues for line in cue.lines)


def test_structural_patterns_protect_known_phrases_without_explicit_terms() -> None:
    phrases = ("如果你", "在女中之前", "人生真實樣貌的一個展現")
    text = "接著再說".join(phrases)
    tokens = _characters(text, step_ms=220, speech_ms=180)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="anji",
        generation_id="structural-patterns",
        audio_end_ms=tokens[-1].end_ms,
    )

    rendered_lines = tuple(line for cue in result.projection.cues for line in cue.lines)
    assert all(any(phrase in line for line in rendered_lines) for phrase in phrases)


def test_structural_grammar_cohesion_is_defeasible_not_a_hard_cue_ban() -> None:
    phrase = "在女中之前"
    tokens = _characters(phrase)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="grammar",
        generation_id="defeasible-grammar",
        audio_end_ms=tokens[-1].end_ms,
    )

    internal = result.boundary_decisions[: len(phrase) - 1]
    grammar_only = tuple(
        decision
        for decision in internal
        if "protected_grammar_structure" in decision.reasons
        and "traditional_lexicon_word_cohesion" not in decision.reasons
    )
    assert grammar_only
    assert all(decision.cue_relation != "forbidden" for decision in grammar_only)


def test_connector_prefers_a_boundary_before_it_but_protects_only_its_interior() -> None:
    prefix = "前情補充"
    connector = "但是"
    tokens = _characters(prefix + connector + "後續說明")

    result = segment_accurate_subtitles(
        tokens,
        episode_id="connector",
        generation_id="connector-boundary-policy",
        audio_end_ms=tokens[-1].end_ms,
    )
    decisions = {decision.edge_index: decision for decision in result.boundary_decisions}
    before = decisions[len(prefix)]
    internal = decisions[len(prefix) + 1]
    after = decisions[len(prefix) + len(connector)]

    assert before.cue_relation == "preferred"
    assert "connector_starts_new_clause" in before.reasons
    assert internal.cue_relation == "forbidden"
    assert "connector_internal_cohesion" in internal.reasons
    assert after.cue_relation == "discouraged"
    assert "connector_no_orphan" in after.reasons


@pytest.mark.parametrize("word", ("人類", "行為", "東西", "安吉", "長春"))
def test_traditional_chinese_word_cohesion_prevents_known_bad_cue_cuts(word: str) -> None:
    text = "甲" * 13 + word + "乙" * 13
    tokens = _characters(text)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="traditional-word",
        generation_id=f"traditional-word-{word}",
        audio_end_ms=tokens[-1].end_ms,
    )
    positions = {token.id: index for index, token in enumerate(tokens)}
    cue_edges = {
        positions[cue.token_ids[-1]] + 1 for cue in result.projection.cues[:-1]
    }
    internal_edge = len("甲" * 13) + 1

    assert internal_edge not in cue_edges
    decision = result.boundary_decisions[internal_edge - 1]
    assert decision.cue_relation == "forbidden"
    assert decision.line_relation == "forbidden"
    assert "traditional_lexicon_word_cohesion" in decision.reasons


@pytest.mark.parametrize("word", ("背後", "標籤", "知道", "可能", "層面", "方式"))
def test_focused_lexical_words_are_hard_when_they_fit_display_capacity(word: str) -> None:
    text = "前文交代" + word + "後文補充"
    tokens = _characters(text)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="lexical-hard",
        generation_id=f"lexical-hard-{word}",
        audio_end_ms=tokens[-1].end_ms,
    )

    decisions = {decision.edge_index: decision for decision in result.boundary_decisions}
    word_start = text.index(word)
    for edge_index in range(word_start + 1, word_start + len(word)):
        assert decisions[edge_index].cue_relation == "forbidden"
        assert decisions[edge_index].line_relation == "forbidden"
        assert "traditional_lexicon_word_cohesion" in decisions[edge_index].reasons
    assert any(word in line for cue in result.projection.cues for line in cue.lines)


@pytest.mark.parametrize(
    "phrase,forbidden_fragment",
    (
        ("她在過程中慢慢梳理過去療癒自己", "療\n癒"),
        ("今天邀請到《臺灣製造》的作者安吉", "《臺灣\n製造》"),
        ("她終於達到另一個里程碑", "另一個\n里程碑"),
    ),
)
def test_visible_spoken_phrases_never_split_at_known_bad_edges(
    phrase: str,
    forbidden_fragment: str,
) -> None:
    result = segment_accurate_subtitles(
        _characters(phrase),
        episode_id="visible-boundary",
        generation_id=phrase,
    )
    rendered_with_breaks = "\n".join(
        line for cue in result.projection.cues for line in cue.lines
    )

    assert forbidden_fragment not in rendered_with_breaks


@pytest.mark.parametrize(
    "text,protected_edges",
    (
        ("今天邀請到一位來賓", ("邀請|到", "到|一位", "一位|來賓")),
        ("看起來非常的光鮮亮麗", ("非常|的", "的|光鮮亮麗")),
        ("達到另一個里程碑的時候", ("達到|另一個", "另一個|里程碑", "里程碑|的", "的|時候")),
        ("花蓮的一些計畫", ("花蓮|的", "的|一些", "一些|計畫")),
        ("40多個小朋友", ("40|多", "多|個", "個|小朋友")),
        ("然後所以上一個國家", ("然後|所以",)),
        ("她深深地覺得自己不夠好", ("深深地|覺得",)),
        ("她常常會做出一些事情", ("常常|會",)),
        ("來幫我們分享", ("幫|我們",)),
        ("怎麼樣讓自己前進", ("讓|自己",)),
        ("本尊來幫我們分享", ("來|幫",)),
        ("我們對性別分工", ("我們|對",)),
        ("傳統家庭應該要怎麼樣", ("應該|要",)),
        ("我是修修每期節目都會介紹來賓", ("每期|節目",)),
        ("我覺得完全打破框架", ("完全|打破",)),
        ("我覺得進入這個社群", ("進入|這個",)),
        ("好像已經沒有什麼意思", ("沒有|什麼",)),
        ("你不用想這麼多", ("不用|想",)),
        ("這個社群會完全改觀", ("社群|會", "會|完全", "完全|改觀")),
        ("我跟保羅都在思考", ("保羅|都", "都|在")),
        ("傳統家庭應該要持續運行", ("家庭|應該", "應該|要")),
        ("需要思考說接下來要做什麼", ("思考|說",)),
        ("帶著小孩去遊牧", ("小孩|去", "去|遊牧")),
        ("他的創作者朋友們", ("創作者|朋友",)),
        ("他就講說你們可以出發", ("講|說",)),
        ("找到TravelingVillage以後", ("Village|以後",)),
        ("我每次都會期待", ("我|每次",)),
        ("就是覺得都經過很久了", ("覺得|都", "都|經過")),
        ("就是覺得都經過了這麼多年", ("經過|了",)),
        ("就是覺得都經過了這麼多年", ("了|這麼",)),
        ("在生米血的頭兩年", ("頭|兩年",)),
        ("我的coolmoms朋友", ("我的|cool",)),
    ),
)
def test_short_syntactic_attachment_edges_are_indivisible(
    text: str,
    protected_edges: tuple[str, ...],
) -> None:
    tokens = _characters(text)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="syntactic-attachment",
        generation_id=text,
        audio_end_ms=tokens[-1].end_ms,
    )

    decisions = {decision.edge_index: decision for decision in result.boundary_decisions}
    for protected_edge in protected_edges:
        left, right = protected_edge.split("|")
        edge_index = text.index(left + right) + len(left)
        assert decisions[edge_index].cue_relation == "forbidden"
        assert decisions[edge_index].line_relation == "forbidden"


def test_provider_clause_cohesion_moves_cue_to_verified_sentence_edge() -> None:
    clauses = (
        "我們先把背景交代清楚",
        "接著開始說明主要故事",
        "最後回到今天核心重點",
    )
    tokens = _characters("".join(clauses))
    first_edge = len(clauses[0])
    second_edge = first_edge + len(clauses[1])
    hints = (
        SentenceBoundaryHint(tokens[first_edge - 1].id),
        SentenceBoundaryHint(tokens[second_edge - 1].id),
    )

    result = segment_accurate_subtitles(
        tokens,
        episode_id="provider-clause",
        generation_id="provider-clause-cohesion",
        sentence_hints=hints,
        audio_end_ms=tokens[-1].end_ms,
    )
    positions = {token.id: index for index, token in enumerate(tokens)}
    cue_edges = {
        positions[cue.token_ids[-1]] + 1 for cue in result.projection.cues[:-1]
    }

    assert cue_edges
    assert cue_edges <= {first_edge, second_edge}
    assert not result.boundary_reviews
    internal = result.boundary_decisions[first_edge]
    assert internal.cue_relation in {"discouraged", "forbidden"}
    assert "provider_clause_cohesion" in internal.reasons


def test_two_line_sized_provider_clause_may_form_multiple_cues_at_safe_edges() -> None:
    clause = "這是一個長度超過單行但仍然小於雙行上限的完整句子"
    assert _TWO_LINE_TEST_PROFILE.hard_line_display_columns < display_columns(clause)
    assert display_columns(clause) <= (
        _TWO_LINE_TEST_PROFILE.max_lines
        * _TWO_LINE_TEST_PROFILE.hard_line_display_columns
    )
    suffix = "接著是下一句"
    tokens = _characters(clause + suffix)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="long-provider-clause",
        generation_id="long-provider-clause",
        sentence_hints=(SentenceBoundaryHint(tokens[len(clause) - 1].id),),
        profile=_TWO_LINE_TEST_PROFILE,
        audio_end_ms=tokens[-1].end_ms,
    )

    assert any(
        decision.cue_relation == "discouraged"
        and "provider_clause_cohesion" in decision.reasons
        for decision in result.boundary_decisions[: len(clause) - 1]
    )


def test_provider_hint_inside_title_does_not_make_adjacent_clauses_unsatisfiable() -> None:
    left = "可以去訂閱你的在substack對《"
    right = "臺灣製造》《臺灣製造》電子報"
    tokens = _characters(left + right)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="provider-title",
        generation_id="provider-title-crossing-hint",
        sentence_hints=(SentenceBoundaryHint(tokens[len(left) - 1].id, 0.75),),
        audio_end_ms=tokens[-1].end_ms,
    )

    assert _rendered_text(result) == left + right
    selected_text = "\n".join(
        line for cue in result.projection.cues for line in cue.lines
    )
    assert "《\n臺灣製造》" not in selected_text


def test_hard_sentence_hint_after_particle_is_not_swallowed_by_syntax() -> None:
    first = "我覺得超棒的"
    second = "如果你也常常覺得自己不夠好"
    tokens = _characters(first + second)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="hard-sentence-particle",
        generation_id="hard-sentence-particle",
        sentence_hints=(SentenceBoundaryHint(tokens[len(first) - 1].id, 1.0),),
        audio_end_ms=tokens[-1].end_ms,
    )

    positions = {token.id: index for index, token in enumerate(tokens)}
    cue_edges = {
        positions[cue.token_ids[-1]] + 1 for cue in result.projection.cues[:-1]
    }
    assert len(first) in cue_edges


def test_hard_sentence_hint_overrides_probabilistic_verb_pronoun_attachment() -> None:
    first = "我知道"
    second = "他已經準備好了"
    tokens = _characters(first + second, step_ms=400, speech_ms=350)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="hard-sentence-syntax",
        generation_id="hard-sentence-syntax",
        sentence_hints=(SentenceBoundaryHint(tokens[len(first) - 1].id, 1.0),),
        audio_end_ms=tokens[-1].end_ms,
    )

    positions = {token.id: index for index, token in enumerate(tokens)}
    cue_edges = {
        positions[cue.token_ids[-1]] + 1 for cue in result.projection.cues[:-1]
    }
    decision = result.boundary_decisions[len(first) - 1]

    assert len(first) in cue_edges
    assert decision.cue_relation == "preferred"
    assert "hard_sentence_overrides_syntax" in decision.reasons


def test_consecutive_hard_sentence_ends_keep_short_tail_with_previous_sentence() -> None:
    first = "前一句已經完整說完"
    short_tail = "這樣"
    second = "然後開始下一個完整句子"
    tokens = _characters(first + short_tail + second, step_ms=160, speech_ms=140)
    first_edge = len(first)
    tail_edge = len(first + short_tail)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="hard-tail-cluster",
        generation_id="hard-tail-cluster",
        sentence_hints=(
            SentenceBoundaryHint(tokens[first_edge - 1].id, 1.0),
            SentenceBoundaryHint(tokens[tail_edge - 1].id, 1.0),
        ),
        audio_end_ms=tokens[-1].end_ms,
    )

    positions = {token.id: index for index, token in enumerate(tokens)}
    cue_edges = {
        positions[cue.token_ids[-1]] + 1 for cue in result.projection.cues[:-1]
    }

    assert tail_edge in cue_edges
    assert first_edge not in cue_edges


def test_subsecond_complete_sentence_with_real_timing_is_not_joined_to_next() -> None:
    first = "前一句已經完整說完"
    short_sentence = "還是要定居"
    second = "然後開始說明下一個想法"
    tokens = _characters(first + short_sentence + second, step_ms=180, speech_ms=150)
    first_edge = len(first)
    short_edge = len(first + short_sentence)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="short-complete-sentence",
        generation_id="short-complete-sentence",
        sentence_hints=(
            SentenceBoundaryHint(tokens[first_edge - 1].id, 1.0),
            SentenceBoundaryHint(tokens[short_edge - 1].id, 1.0),
        ),
        audio_end_ms=tokens[-1].end_ms,
    )

    positions = {token.id: index for index, token in enumerate(tokens)}
    cue_edges = {
        positions[cue.token_ids[-1]] + 1 for cue in result.projection.cues[:-1]
    }

    assert first_edge in cue_edges
    assert short_edge in cue_edges


def test_copular_name_does_not_swallow_following_clause_starter() -> None:
    text = "我是修修每期節目都會介紹來賓"
    tokens = _characters(text)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="copular-name",
        generation_id="copular-name-clause-starter",
        audio_end_ms=tokens[-1].end_ms,
    )

    edge_index = text.index("修修每期") + len("修修")
    decision = result.boundary_decisions[edge_index - 1]

    assert decision.cue_relation != "forbidden"
    assert decision.line_relation != "forbidden"


def test_nominal_determiner_does_not_swallow_following_predicate() -> None:
    text = "寫這本書在過程中慢慢梳理"
    tokens = _characters(text)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="nominal-determiner",
        generation_id="nominal-determiner-predicate",
        sentence_hints=(
            SentenceBoundaryHint(tokens[text.index("書在")].id, 0.75),
        ),
        audio_end_ms=tokens[-1].end_ms,
    )

    edge_index = text.index("書在") + 1
    decision = result.boundary_decisions[edge_index - 1]

    assert decision.cue_relation == "preferred"
    assert decision.line_relation == "preferred"


def test_soft_sentence_hint_at_determiner_attachment_is_not_selected() -> None:
    text = "然後第一個我覺得很重要"
    tokens = _characters(text)
    edge_index = text.index("個我") + 1

    result = segment_accurate_subtitles(
        tokens,
        episode_id="soft-hint-cohesion",
        generation_id="soft-hint-cohesion",
        sentence_hints=(SentenceBoundaryHint(tokens[edge_index - 1].id, 0.75),),
        audio_end_ms=tokens[-1].end_ms,
    )

    decision = result.boundary_decisions[edge_index - 1]
    selected_edges = {
        int(cue.token_ids[-1].split("-")[-1]) + 1
        for cue in result.projection.cues[:-1]
    }

    assert "determiner_attaches_to_head" in decision.reasons
    assert "sentence_boundary_hint" in decision.reasons
    assert edge_index not in selected_edges


def test_demonstrative_clause_starter_is_not_glued_to_previous_verb() -> None:
    text = "如果你覺得不值得那這一集會給你力量"
    tokens = _characters(text)
    edge_index = text.index("得那") + 1

    result = segment_accurate_subtitles(
        tokens,
        episode_id="demonstrative-clause",
        generation_id="demonstrative-clause",
        sentence_hints=(SentenceBoundaryHint(tokens[edge_index - 1].id, 0.75),),
        audio_end_ms=tokens[-1].end_ms,
    )

    decision = result.boundary_decisions[edge_index - 1]

    assert decision.cue_relation == "preferred"
    assert "syntactic_attachment_cohesion" not in decision.reasons


@pytest.mark.parametrize(
    "text,seam,reason",
    (
        ("更接受自己現在的樣子我覺得超棒", "樣子|我", "fresh_subject_boundary"),
        ("這是一個孤單的過程我每次都會想起", "過程|我", "fresh_subject_boundary"),
        ("你不是才剛回來嗎嗯我回來了", "嗎|嗯", "question_response_boundary"),
        ("我沒有我的coolmoms你是不是也一樣", "coolmoms|你", "fresh_subject_boundary"),
        ("你不用想這麼多我覺得人生會改變", "多|我", "fresh_subject_boundary"),
        ("持續運行的方式然後開始下一個主題", "方式|然後", "completed_clause_connector_boundary"),
        ("大家都超開心的然後保羅離開了", "的|然後", "completed_clause_connector_boundary"),
        ("我是修修每期節目都會介紹來賓", "修修|每期", "copular_intro_topic_boundary"),
    ),
)
def test_high_confidence_missing_sentence_seams_are_inferred(
    text: str,
    seam: str,
    reason: str,
) -> None:
    tokens = _characters(text)
    left, right = seam.split("|")
    edge_index = text.index(left + right) + len(left)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="inferred-sentence",
        generation_id=text,
        audio_end_ms=tokens[-1].end_ms,
    )

    decision = result.boundary_decisions[edge_index - 1]

    assert decision.cue_relation == "preferred"
    assert decision.line_relation == "preferred"
    assert reason in decision.reasons


@pytest.mark.parametrize(
    "text,seam",
    (
        ("每期節目我都會介紹來賓", "節目|我"),
        ("有些人他們已經遊牧多年", "人|他們"),
        ("因為他的創作者朋友們我們都是單身", "們|我們"),
    ),
)
def test_topic_resumption_prefers_line_without_forcing_sentence_cue(
    text: str,
    seam: str,
) -> None:
    tokens = _characters(text)
    left, right = seam.split("|")
    edge_index = text.index(left + right) + len(left)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="topic-resumption",
        generation_id=text,
        audio_end_ms=tokens[-1].end_ms,
    )

    decision = result.boundary_decisions[edge_index - 1]
    positions = {token.id: index for index, token in enumerate(tokens)}
    cue_edges = {
        positions[cue.token_ids[-1]] + 1 for cue in result.projection.cues[:-1]
    }

    assert decision.line_relation == "preferred"
    assert decision.cue_relation == "forbidden"
    assert "topic_resumption_boundary" in decision.reasons
    assert "inferred_sentence_boundary" not in decision.reasons
    assert edge_index not in cue_edges


def test_zero_provider_hints_do_not_apply_whole_transcript_clause_cohesion() -> None:
    tokens = _characters("完全沒有提供者標點提示的普通內容")

    result = segment_accurate_subtitles(
        tokens,
        episode_id="zero-provider-hints",
        generation_id="zero-provider-hints",
        sentence_hints=(),
        audio_end_ms=tokens[-1].end_ms,
    )

    assert all(
        "provider_clause_cohesion" not in decision.reasons
        for decision in result.boundary_decisions
    )


def test_lexical_span_that_exceeds_line_capacity_is_explicitly_reviewed() -> None:
    word = "supercalifragilisticexpialidociousboundaryextension"
    assert display_columns(word) > _TWO_LINE_TEST_PROFILE.hard_line_display_columns
    assert display_columns(word) <= (
        _TWO_LINE_TEST_PROFILE.max_lines
        * _TWO_LINE_TEST_PROFILE.hard_line_display_columns
    )
    tokens = _characters(word, step_ms=100, speech_ms=90)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="lexical-line-capacity",
        generation_id="lexical-line-capacity",
        profile=_TWO_LINE_TEST_PROFILE,
        audio_end_ms=tokens[-1].end_ms,
    )

    assert len(result.projection.cues) == 1
    assert len(result.projection.cues[0].lines) == 2
    lexical_reviews = [
        item for item in result.boundary_reviews if item.code == "forced_lexical_boundary"
    ]
    assert len(lexical_reviews) == 1
    assert lexical_reviews[0].channel == "line"
    assert "lexical_span_exceeds_line_width" in lexical_reviews[0].reasons


def test_jointly_unsatisfiable_lexical_constraint_is_relaxed_with_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    word = "supercalifragilisticexpialidociousboundary"
    tokens = _characters(word, step_ms=100, speech_ms=90)
    original_project = subject.project_semantic_units

    def project_with_external_hard_conflict(tokens, units, profile, **kwargs):
        if any(
            unit.kind == "boundary_pair"
            and unit.cue_boundary_relation == "forbidden"
            for unit in units
        ):
            raise ProjectionUnsatisfiableError("simulated joint hard-constraint conflict")
        return original_project(tokens, units, profile, **kwargs)

    monkeypatch.setattr(subject, "project_semantic_units", project_with_external_hard_conflict)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="lexical-joint-conflict",
        generation_id="lexical-joint-conflict",
        profile=HORIZONTAL_16X9.model_copy(
            update={
                "profile_version": 4,
                "max_lines": 2,
                "target_line_display_columns": 26,
                "hard_line_display_columns": 36,
                "target_cue_display_columns": None,
                "minimum_preferred_cue_display_columns": None,
                "maximum_preferred_cue_display_columns": None,
            }
        ),
        audio_end_ms=tokens[-1].end_ms,
    )

    lexical_reviews = [
        item for item in result.boundary_reviews if item.code == "forced_lexical_boundary"
    ]
    assert any(
        item.channel == "cue"
        and "lexical_constraints_jointly_unsatisfiable" in item.reasons
        for item in lexical_reviews
    )


def test_offset_mapping_protects_a_term_across_mixed_asr_tokens() -> None:
    tokens = _mixed(("開場不正常", "人類研究", "所接著談話", "還有其他內容"))

    result = segment_accurate_subtitles(
        tokens,
        episode_id="anji",
        generation_id="mixed-token-offsets",
        protected_terms=("不正常人類研究所",),
        audio_end_ms=tokens[-1].end_ms,
    )

    decisions = {decision.edge_index: decision for decision in result.boundary_decisions}
    assert decisions[1].cue_relation == "forbidden"
    assert decisions[2].cue_relation == "forbidden"
    assert "protected_term" in decisions[1].reasons
    assert "protected_term" in decisions[2].reasons


def test_offset_mapping_keeps_a_closed_class_word_from_dangling_across_mixed_tokens() -> None:
    tokens = _mixed(("這是我的", "真實想法", "也值得說明"))

    result = segment_accurate_subtitles(
        tokens,
        episode_id="closed-class",
        generation_id="mixed-closed-class",
        audio_end_ms=tokens[-1].end_ms,
    )

    edge = result.boundary_decisions[0]
    assert edge.cue_relation in {"discouraged", "forbidden"}
    assert "closed_class_no_orphan" in edge.reasons


def test_long_protected_phrase_stays_in_one_single_line_cue() -> None:
    phrase = "這是一段需要完整保留語意而且長度足以跨越單行限制的專有名稱"
    assert 44 < display_columns(phrase) <= HORIZONTAL_16X9.hard_line_display_columns
    tokens = _characters(phrase, step_ms=190, speech_ms=170)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="long-term",
        generation_id="two-line-protected-term",
        protected_terms=(phrase,),
        audio_end_ms=tokens[-1].end_ms,
    )

    assert len(result.projection.cues) == 1
    assert result.projection.cues[0].lines == (phrase,)


def test_projection_is_exact_copy_with_exactly_one_line_per_cue_and_valid_srt() -> None:
    text = "字幕不可以改寫校對完成的文字而且每一個字都必須照原本順序輸出"
    tokens = _characters(text)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="exact-copy",
        generation_id="exact-copy",
        audio_end_ms=tokens[-1].end_ms,
    )

    assert _rendered_text(result) == text
    assert all(len(cue.lines) == 1 for cue in result.projection.cues)
    assert result.srt_text.endswith("\n")
    assert " --> " in result.srt_text


def test_projection_never_strands_preserved_whitespace_at_display_boundary() -> None:
    tokens = _mixed(("前半段文字 ", "後半段文字", " 還有最後一段"))

    result = segment_accurate_subtitles(
        tokens,
        episode_id="whitespace",
        generation_id="whitespace-cohesion",
        audio_end_ms=tokens[-1].end_ms,
    )

    assert _rendered_text(result) == "前半段文字 後半段文字 還有最後一段"
    assert all(
        line == line.strip()
        for cue in result.projection.cues
        for line in cue.lines
    )


def test_unavoidable_weak_cut_is_exposed_for_review() -> None:
    text = "甲乙丙丁戊己庚辛壬癸" * 5
    tokens = _characters(text, step_ms=130, speech_ms=120)

    result = segment_accurate_subtitles(
        tokens,
        episode_id="weak-cut",
        generation_id="weak-cut",
        audio_end_ms=tokens[-1].end_ms,
    )

    assert len(result.projection.cues) > 1
    assert any(
        item.code == "forced_low_confidence_boundary" and item.channel == "cue"
        for item in result.boundary_reviews
    )


def test_real_pause_becomes_a_preferred_dp_boundary_and_needs_no_review() -> None:
    left = "前半段自然說完"
    right = "後半段接著開始"
    text = left + right
    tokens = _characters(
        text,
        step_ms=180,
        speech_ms=150,
        strong_pause_after=len(left) - 1,
    )

    result = segment_accurate_subtitles(
        tokens,
        episode_id="pause",
        generation_id="pause",
        audio_end_ms=tokens[-1].end_ms,
    )

    decision = result.boundary_decisions[len(left) - 1]
    assert decision.pause_ms >= 800
    assert decision.cue_relation == "preferred"
    assert "timing_pause_preferred" in decision.reasons
    assert all(item.edge_index != len(left) for item in result.boundary_reviews)
