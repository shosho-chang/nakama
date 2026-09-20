"""Focused, text-preserving Chinese subtitle segmentation.

This module is deliberately small: it converts corrected, exactly timed tokens
into the existing global semantic projection.  Lexical protection and sentence
signals are projected through character offsets so character-, word-, and mixed
ASR tokenisations receive the same boundary policy.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import Literal, Mapping, Sequence

from shared.schemas.podcast_subtitles_v2 import (
    CanonicalToken,
    ProjectionProfile,
    SemanticBoundaryRelation,
    SemanticUnit,
)

from .display_metrics import display_columns, reading_units
from .errors import ProjectionUnsatisfiableError
from .profiles import HORIZONTAL_16X9
from .semantic_projection import ProjectionResult, project_semantic_units, render_srt

BoundaryChannel = Literal["cue", "line"]
LexicalReviewReason = Literal[
    "lexical_span_exceeds_cue_width",
    "lexical_span_exceeds_line_width",
    "lexical_span_exceeds_cue_duration",
    "lexical_span_exceeds_reading_rate",
    "lexical_span_crosses_speaker_boundary",
    "lexical_constraints_jointly_unsatisfiable",
]
_LexicalConstraintKey = tuple[BoundaryChannel, int, int]

_HARD_SENTENCE_PUNCTUATION = frozenset("。！？!?；;")
_SOFT_SENTENCE_PUNCTUATION = frozenset("，、：:,\n")
_CLOSING_PUNCTUATION = frozenset("，。、！？!?；;：:）」』》〉】〕］}”’")
_OPENING_PUNCTUATION = frozenset("（「『《〈【〔［{“‘")
_CLOSED_CLASS = frozenset(
    {
        "的",
        "了",
        "嗎",
        "呢",
        "吧",
        "把",
        "被",
        "在",
        "從",
        "向",
        "對",
        "跟",
        "與",
        "和",
        "是",
        "而",
        "也",
        "就",
        "才",
        "都",
        "又",
    }
)
_CONNECTORS = frozenset(
    {"但是", "所以", "因為", "如果", "而且", "然後", "不過", "可是", "其實", "或者是"}
)
_DETERMINER_PHRASES = frozenset(
    {
        "一個",
        "這個",
        "那個",
        "每個",
        "另一個",
        "一種",
        "這種",
        "那種",
        "一份",
        "一項",
        "一段",
        "一次",
        "一本",
        "一位",
        "一條",
        "一些",
    }
)
_DEFAULT_PROTECTED_TERMS = (
    "不正常人類研究所",
    "冒牌者情結",
    "學業經歷",
)
_STRUCTURAL_PATTERNS = (
    re.compile(r"(?:如果|假如|只要)(?:你|妳|您|我|他|她|我們|你們|大家)"),
    re.compile(
        r"(?:在|從|向|對|把|被|跟)[^，。！？!?；;\n]{1,12}?"
        r"(?:之前|之後|以前|以後|當中|裡面|上面|下面)"
    ),
    re.compile(
        r"[\u3400-\u9fff]{1,6}的"
        r"(?:一個|一種|一份|一項|一段|這個|那個|每個)"
        r"[\u3400-\u9fff]{1,6}"
    ),
    re.compile(r"[\u3400-\u9fff]{1,8}(?:研究所|情結|經歷|歷程)"),
)
_TITLE_PATTERN = re.compile(r"《[^《》\n]{1,30}》")
# These literals match the length-preserving T2S text used by Jieba POS.
_VERB_COMPLEMENTS = frozenset({"到", "出", "得", "起来", "进", "上", "下", "回"})
_DETERMINER_WORDS = frozenset({"另", "每", "这", "那", "哪", "某", "什么"})
_GOVERNING_WORDS = frozenset({"去", "来", "到", "在", "从", "向", "对", "跟", "与", "和"})
_CLAUSAL_VERBS = frozenset(
    {"觉得", "认为", "知道", "发现", "希望", "想", "说", "讲", "思考"}
)
_MODAL_VERBS = frozenset(
    {"应该", "应当", "需要", "必须", "可以", "能够", "会", "要", "不用"}
)
_POSTPOSITIONAL_TIME_WORDS = frozenset(
    {"以前", "以后", "之前", "之后", "当中", "里面", "上面", "下面"}
)
_PERSONAL_PRONOUNS = frozenset(
    {"我", "我们", "你", "你们", "您", "他", "他们", "她", "她们", "自己", "大家"}
)
# A provider-confirmed sentence end may overrule a probabilistic POS attachment,
# but it must never split immutable lexical evidence such as a name or title.
_HARD_SENTENCE_LEXICAL_BLOCKERS = frozenset(
    {
        "protected_term",
        "protected_title",
        "traditional_lexicon_word_cohesion",
        "connector_internal_cohesion",
        "determiner_internal_cohesion",
        "whitespace_internal_cohesion",
    }
)
_RELATION_PRIORITY: Mapping[SemanticBoundaryRelation, int] = {
    "neutral": 0,
    "discouraged": 1,
    "preferred": 2,
    "forbidden": 3,
}
_UNATTRIBUTED_SPEAKER = "UNATTRIBUTED"


@dataclass(frozen=True, slots=True)
class SentenceBoundaryHint:
    """A caller-confirmed natural ending after one corrected token."""

    after_token_id: str
    strength: float = 1.0

    def __post_init__(self) -> None:
        if not self.after_token_id.strip():
            raise ValueError("sentence boundary hint requires a token ID")
        if not 0 < self.strength <= 1:
            raise ValueError("sentence boundary hint strength must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class BoundaryDecision:
    """The deterministic signals supplied to the global DP at one token edge."""

    edge_index: int
    left_token_id: str
    right_token_id: str
    cue_relation: str
    line_relation: str
    strength: float
    pause_ms: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BoundaryReviewItem:
    """A selected display boundary without a strong semantic or pause basis."""

    code: Literal["forced_low_confidence_boundary", "forced_lexical_boundary"]
    channel: BoundaryChannel
    edge_index: int
    left_token_id: str
    right_token_id: str
    pause_ms: int
    relation: str
    context: str
    reasons: tuple[LexicalReviewReason, ...] = ()


@dataclass(frozen=True, slots=True)
class AccurateSegmentationResult:
    projection: ProjectionResult
    srt_text: str
    boundary_reviews: tuple[BoundaryReviewItem, ...]
    boundary_decisions: tuple[BoundaryDecision, ...]
    semantic_units: tuple[SemanticUnit, ...]


@dataclass(slots=True)
class _EdgeSignal:
    cue_relation: SemanticBoundaryRelation = "neutral"
    line_relation: SemanticBoundaryRelation = "neutral"
    cue_strength: float = 0.0
    line_strength: float = 0.0
    pause_ms: int = 0
    reasons: set[str] = field(default_factory=set)

    @property
    def strength(self) -> float:
        return max(self.cue_strength, self.line_strength)


@dataclass(frozen=True, slots=True)
class _LexicalSpan:
    """One token-edge-addressable word and any hard-capacity exceptions."""

    start_offset: int
    end_offset: int
    edge_indices: tuple[int, ...]
    cue_relaxation_reasons: tuple[LexicalReviewReason, ...]
    line_relaxation_reasons: tuple[LexicalReviewReason, ...]

    def key(self, channel: BoundaryChannel) -> _LexicalConstraintKey:
        return channel, self.start_offset, self.end_offset

    def relaxation_reasons(
        self, channel: BoundaryChannel
    ) -> tuple[LexicalReviewReason, ...]:
        return (
            self.cue_relaxation_reasons
            if channel == "cue"
            else self.line_relaxation_reasons
        )


def _value(item: object, name: str, default: object = None) -> object:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _canonical_tokens(items: Sequence[object]) -> tuple[CanonicalToken, ...]:
    tokens: list[CanonicalToken] = []
    previous_end = -1
    for index, item in enumerate(items):
        if isinstance(item, CanonicalToken):
            token = (
                item
                if item.speaker is not None
                else item.model_copy(update={"speaker": _UNATTRIBUTED_SPEAKER})
            )
        else:
            token_id = _value(item, "id")
            text = _value(item, "text")
            start_ms = _value(item, "start_ms")
            end_ms = _value(item, "end_ms")
            if not isinstance(token_id, str) or not isinstance(text, str):
                raise ValueError(f"tokens[{index}] requires string id/text")
            if (
                not isinstance(start_ms, int)
                or isinstance(start_ms, bool)
                or not isinstance(end_ms, int)
                or isinstance(end_ms, bool)
            ):
                raise ValueError(f"tokens[{index}] requires integer millisecond timing")
            speaker = _value(item, "speaker")
            confidence = _value(item, "confidence")
            token = CanonicalToken(
                id=token_id,
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                timing_basis="recognition",
                speaker=_UNATTRIBUTED_SPEAKER if speaker is None else str(speaker),
                confidence=confidence if isinstance(confidence, (int, float)) else None,
            )
        if token.start_ms is None or token.end_ms is None:
            raise ValueError("accurate segmentation requires exact token timing")
        if token.start_ms < previous_end:
            raise ValueError("accurate segmentation tokens must be monotonic and non-overlapping")
        previous_end = token.end_ms
        tokens.append(token)
    if not tokens:
        raise ValueError("accurate segmentation requires at least one token")
    if len({token.id for token in tokens}) != len(tokens):
        raise ValueError("accurate segmentation token IDs must be unique")
    return tuple(tokens)


def _token_offsets(tokens: Sequence[CanonicalToken]) -> tuple[tuple[int, ...], str]:
    offsets = [0]
    for token in tokens:
        offsets.append(offsets[-1] + len(token.text))
    return tuple(offsets), "".join(token.text for token in tokens)


def _casefold_occurrences(value: str, needle: str) -> tuple[tuple[int, int], ...]:
    folded: list[str] = []
    original: list[tuple[int, int]] = []
    for index, character in enumerate(value):
        mapped = character.casefold()
        folded.append(mapped)
        original.extend((index, index + 1) for _ in mapped)
    folded_value = "".join(folded)
    folded_needle = needle.casefold()
    matches: list[tuple[int, int]] = []
    cursor = 0
    while folded_needle and (position := folded_value.find(folded_needle, cursor)) >= 0:
        end = position + len(folded_needle)
        matches.append((original[position][0], original[end - 1][1]))
        cursor = position + 1
    return tuple(matches)


def _edges_inside(offsets: Sequence[int], start: int, end: int) -> tuple[int, ...]:
    first = max(1, bisect_right(offsets, start))
    stop = min(len(offsets) - 1, bisect_left(offsets, end))
    return tuple(range(first, stop))


def _token_range_for_span(offsets: Sequence[int], start: int, end: int) -> tuple[int, int] | None:
    left = bisect_right(offsets, start) - 1
    right = bisect_left(offsets, end)
    left = max(0, min(left, len(offsets) - 2))
    right = max(left + 1, min(right, len(offsets) - 1))
    if start >= end or offsets[left] >= end or offsets[right] <= start:
        return None
    return left, right


def _apply_relation(
    signal: _EdgeSignal,
    *,
    channel: BoundaryChannel,
    relation: SemanticBoundaryRelation,
    strength: float,
    reason: str,
) -> None:
    relation_name = f"{channel}_relation"
    strength_name = f"{channel}_strength"
    current = getattr(signal, relation_name)
    if _RELATION_PRIORITY[relation] > _RELATION_PRIORITY[current]:
        setattr(signal, relation_name, relation)
        setattr(signal, strength_name, strength)
    elif relation == current:
        setattr(signal, strength_name, max(getattr(signal, strength_name), strength))
    signal.reasons.add(reason)


def _apply_span(
    signals: Sequence[_EdgeSignal],
    tokens: Sequence[CanonicalToken],
    offsets: Sequence[int],
    text: str,
    *,
    start: int,
    end: int,
    profile: ProjectionProfile,
    reason: str,
    protected: bool,
) -> None:
    edges = _edges_inside(offsets, start, end)
    if not edges:
        return
    token_range = _token_range_for_span(offsets, start, end)
    if token_range is None:
        return
    left, right = token_range
    phrase_text = text[start:end]
    phrase_duration = tokens[right - 1].end_ms - tokens[left].start_ms  # type: ignore[operator]
    minimum_reading_ms = round(
        reading_units(phrase_text) / profile.max_reading_units_per_second * 1000
    )
    line_fits = display_columns(phrase_text) <= profile.hard_line_display_columns
    cue_fits = (
        display_columns(phrase_text) <= profile.max_lines * profile.hard_line_display_columns
        and phrase_duration <= profile.max_cue_duration_ms
        and minimum_reading_ms <= profile.max_cue_duration_ms
    )
    for edge_index in edges:
        signal = signals[edge_index]
        _apply_relation(
            signal,
            channel="cue",
            relation="forbidden" if protected and cue_fits else "discouraged",
            strength=1.0 if protected else 0.65,
            reason=reason,
        )
        _apply_relation(
            signal,
            channel="line",
            relation="forbidden" if protected and line_fits else "discouraged",
            strength=1.0 if protected else 0.65,
            reason=reason,
        )


def _jieba_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return local dictionary spans after a length-preserving T2S lookup.

    Jieba's bundled dictionary is primarily Simplified Chinese.  Converting
    each scalar independently recovers Traditional spellings such as ``人類``
    and ``行為`` without changing offsets.  Any non-1:1 mapping is left alone,
    so the returned spans always address the original corrected text.
    """

    try:
        import jieba
        from opencc import OpenCC
    except ImportError:
        return ()
    converter = OpenCC("t2s")
    lookup_text = "".join(
        converted if len(converted := converter.convert(character)) == 1 else character
        for character in text
    )
    spans: list[tuple[int, int]] = []
    # HMM recovers common spoken compounds missing from Jieba's static
    # dictionary (for example ``療癒``).  Splitting one of those compounds is
    # visibly worse than accepting the probabilistic lexical span, while the
    # capacity checks below still make every span defeasible when it cannot fit.
    for _word, start, end in jieba.tokenize(lookup_text, HMM=True):
        original = text[start:end]
        stripped = original.strip()
        if (
            end - start >= 2
            and stripped
            and not any(character in _HARD_SENTENCE_PUNCTUATION for character in stripped)
            and not any(character in _SOFT_SENTENCE_PUNCTUATION for character in stripped)
        ):
            spans.append((start, end))
    return tuple(spans)


def _jieba_pos_items(text: str) -> tuple[tuple[str, str, int, int], ...]:
    """Return length-preserving T2S Jieba POS items with original offsets."""

    try:
        import jieba.posseg as posseg
        from opencc import OpenCC
    except ImportError:
        return ()
    converter = OpenCC("t2s")
    lookup_text = "".join(
        converted if len(converted := converter.convert(character)) == 1 else character
        for character in text
    )
    items: list[tuple[str, str, int, int]] = []
    cursor = 0
    for pair in posseg.cut(lookup_text, HMM=True):
        start = cursor
        cursor += len(pair.word)
        items.append((pair.word, pair.flag, start, cursor))
    if cursor != len(text):  # pragma: no cover - POS tokenizer contract guard
        return ()
    return tuple(items)


def _syntactic_attachment_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return bounded POS-backed spans whose adjacent words depend on each other.

    The spans are assembled from Jieba POS tokens rather than greedy character
    regexes.  This keeps productive constructions general while preventing a
    missing punctuation mark from turning half a sentence into one hard unit.
    """

    items = _jieba_pos_items(text)
    if not items:
        return ()

    spans: set[tuple[int, int]] = set()
    # Jieba may tag a name as a verb (the episode host name ``修修`` is one
    # real example).  A complement immediately after the copula is nominal in
    # this construction regardless of that lexical tag; remember that context
    # so the following pronoun/determiner is not falsely glued to the name.
    copular_complements = {
        index
        for index in range(1, len(items))
        if items[index - 1][0] == "是"
    }

    def attach(left: int, right: int) -> None:
        if 0 <= left < right < len(items):
            spans.add((items[left][2], items[right][3]))

    for index, (word, flag, _start, _end) in enumerate(items):
        previous = items[index - 1] if index else None
        following = items[index + 1] if index + 1 < len(items) else None

        # Verb + result/direction complement, and verb + determiner object.
        if previous and previous[1].startswith("v") and (
            word in _VERB_COMPLEMENTS
            or flag.startswith(("m", "q"))
            or (
                word in _DETERMINER_WORDS
                and word != "那"
            )
            # Jieba normally emits multi-character demonstratives such as
            # ``这个`` as one pronoun token.  They still belong to the verb's
            # object phrase (``进入这个社群``).  Keep the single discourse
            # starter ``那`` defeasible so ``不值得／那这一集`` remains legal.
            or (
                len(word) > 1
                and word.startswith(("这", "那"))
                and flag.startswith("r")
            )
        ):
            attach(index - 1, index)

        if previous and previous[0] in _GOVERNING_WORDS and flag.startswith(
            ("n", "r", "m", "v")
        ):
            attach(index - 1, index)

        # An adverb directly completing a cognitive/reporting verb is a tight
        # local dependency (``覺得都...``).  A whole subordinate clause after
        # ``說/覺得`` is only a soft dependency and must remain a legal cue seam
        # when duration forces a split; hardening v/r here creates transitive
        # no-break chains across an entire sentence.
        if previous and previous[0] in _CLAUSAL_VERBS and flag.startswith("d"):
            attach(index - 1, index)

        if previous and previous[1].startswith(("n", "r")) and word == "是":
            attach(index - 1, index)
        if previous and previous[0] == "是" and flag.startswith(
            ("a", "m", "n", "r", "v")
        ):
            attach(index - 1, index)

        # A subject pronoun cannot be stranded before its adverb/predicate.
        if previous and previous[1].startswith("r") and flag.startswith(("d", "n", "v")):
            attach(index - 1, index)
        if (
            previous
            and previous[0] in _PERSONAL_PRONOUNS
            and flag.startswith("p")
        ):
            attach(index - 1, index)
        if (
            previous
            and word in _PERSONAL_PRONOUNS
            and previous[1].startswith("m")
        ):
            attach(index - 1, index)

        # Productive predicate attachments must not be cut merely to approach
        # the visual target: ``深深地+觉得``, ``常常+会``, ``帮+我们`` and
        # ``让+自己``.  These POS rules are deliberately only two words wide;
        # a verified hard sentence end can overrule them below.
        if previous and (
            previous[1] in {"d", "ad"} or previous[1].startswith("i")
        ) and (
            flag.startswith(("a", "n", "p", "v"))
            or word in _PERSONAL_PRONOUNS
        ):
            attach(index - 1, index)
        if (
            previous
            and previous[1].startswith(("n", "r"))
            and (word in _MODAL_VERBS or word in {"都", "也", "才", "就"})
        ):
            attach(index - 1, index)
        if (
            previous
            and previous[1].startswith(("n", "r"))
            and word in {"去", "来"}
            and following is not None
            and following[1].startswith(("n", "v"))
        ):
            attach(index - 1, index)
        if (
            previous
            and index - 1 not in copular_complements
            and previous[1].startswith("v")
            and word in _PERSONAL_PRONOUNS
        ):
            attach(index - 1, index)

        if (
            previous
            and previous[1].startswith("v")
            and previous[0].endswith(("来", "去"))
            and flag.startswith("v")
        ):
            attach(index - 1, index)

        if previous and previous[0] in _MODAL_VERBS and flag.startswith(
            ("a", "d", "r", "v")
        ):
            attach(index - 1, index)

        # Aspect particles close the preceding predicate and must never start
        # a new subtitle (``都经过／了`` is unreadable).  Likewise, bounded
        # duration phrases such as ``头两年`` are one temporal constituent.
        if previous and (
            flag in {"ug", "ul", "uz"}
            or word in {"了", "着"}
        ):
            attach(index - 1, index)
        if (
            previous
            and (
                previous[1] in {"ug", "ul", "uz"}
                or previous[0] in {"了", "着"}
            )
            and (
                flag.startswith(("m", "r", "t"))
                or word.startswith(("这", "那"))
            )
        ):
            attach(index - 1, index)
        if (
            previous
            and previous[0] in {"头", "前", "后"}
            and flag.startswith("m")
        ):
            attach(index - 1, index)

        if previous and previous[0] in {"讲", "听", "思考"} and word == "说":
            attach(index - 1, index)

        if (
            previous
            and word in _POSTPOSITIONAL_TIME_WORDS
            and previous[1].startswith(("eng", "n", "r"))
        ):
            attach(index - 1, index)

        if (
            previous
            and previous[0] in _PERSONAL_PRONOUNS
            and (
                word.startswith(tuple(_DETERMINER_WORDS))
                or flag.startswith(("m", "r"))
            )
        ):
            attach(index - 1, index)

        if previous and (word == "来说" or flag == "k"):
            attach(index - 1, index)

        if (
            previous
            and previous[1].startswith("n")
            and flag.startswith("n")
            and len(previous[0] + word) <= 6
            and len(previous[0]) >= 2
            and len(word) >= 2
        ):
            attach(index - 1, index)

        if (
            previous
            and previous[0].startswith(("这", "那"))
            and flag.startswith(("m", "n", "t"))
        ):
            attach(index - 1, index)

        # Determiners/numbers/classifiers attach to each other and to their
        # immediate noun head: ``另+一個+里程碑``, ``40+多個+小朋友``.
        if previous and (
            previous[0] in _DETERMINER_WORDS
            or previous[1].startswith(("m", "q"))
        ) and (flag.startswith(("m", "q", "n")) or word in _DETERMINER_WORDS):
            attach(index - 1, index)

        # The structural particle belongs to both sides.  When its right side
        # is a determiner, include exactly one following noun head as well.
        if (
            word == "的"
            and previous
            and following
            and following[1].startswith(("eng", "n", "m", "q", "a", "r", "v"))
        ):
            attach(index - 1, index + 1)
            if (
                following[1].startswith(("m", "q"))
                and index + 2 < len(items)
                and items[index + 2][1].startswith("n")
            ):
                attach(index, index + 2)

        # Repeated discourse markers launch one clause together; neither may
        # become a one-word subtitle line.
        if previous and previous[1].startswith("c") and flag.startswith("c"):
            attach(index - 1, index)

    return tuple(sorted(spans))


def _inferred_sentence_offsets(
    text: str,
    *,
    known_boundary_offsets: Sequence[int] = (),
) -> tuple[tuple[int, str], ...]:
    """Infer high-confidence missing sentence seams from bounded POS context.

    Provider punctuation remains the primary source.  These rules cover only
    local structures where joining the two sides changes the clause analysis:
    a completed nominal/adjectival clause followed by a fresh subject, a
    question particle followed by a response, and a copular introduction
    followed by a recurring time/topic starter.
    """

    items = _jieba_pos_items(text)
    if not items:
        return ()
    inferred: dict[int, str] = {}
    known = tuple(sorted(set(known_boundary_offsets)))
    known_index = 0
    clause_start_offset = 0
    response_starters = ("嗯", "对", "好", "是", "不是", "没有", "没错")
    question_endings = ("吗", "呢", "吧", "是不是", "对不对")
    question_predicates = {"是不是", "有没有", "要不要", "能不能", "会不会"}
    topic_starters = ("每", "今天", "现在", "接下来")
    discourse_starters = {"然后", "但是", "所以", "可是", "不过", "而且"}

    for index in range(1, len(items)):
        previous = items[index - 1]
        current = items[index]
        following = items[index + 1] if index + 1 < len(items) else None
        offset = current[2]

        # A provider separator before this word starts a fresh local clause.
        # Use only boundaries strictly before the candidate so a soft comma at
        # the candidate itself does not erase the left-hand evidence.
        while known_index < len(known) and known[known_index] < offset:
            clause_start_offset = known[known_index]
            known_index += 1

        left_clause = tuple(
            item
            for item in items[:index]
            if item[2] >= clause_start_offset
        )
        left_has_predicate = any(
            item[1].startswith(("a", "v")) for item in left_clause
        )

        if previous[0].endswith(question_endings) and current[0].startswith(
            response_starters
        ):
            inferred[offset] = "question_response_boundary"
            continue

        if (
            current[0] in discourse_starters
            and previous[0] not in discourse_starters
            and (
                previous[1].startswith(("a", "eng", "n", "v"))
                or previous[0] in {"的", "了"}
            )
            and left_has_predicate
        ):
            inferred[offset] = "completed_clause_connector_boundary"
            clause_start_offset = offset
            continue

        if (
            current[0] in _PERSONAL_PRONOUNS
            and previous[1].startswith(("a", "eng", "f", "k", "m", "n", "q", "t"))
            and following is not None
            and (
                following[1].startswith(("a", "d", "t", "v"))
                or following[0].startswith("每")
                or following[0] in question_predicates
            )
        ):
            # A completed predicate followed by a new subject is a missing
            # sentence seam (``接受现在的样子／我觉得``).  A bare topic noun
            # followed by a resumptive pronoun is one clause instead
            # (``每期节目／我都会``, ``有些人／他们是``): prefer only a
            # physical line there and never force a flashing one-phrase cue.
            if left_has_predicate:
                inferred[offset] = "fresh_subject_boundary"
                clause_start_offset = offset
            else:
                inferred[offset] = "topic_resumption_boundary"
            continue

        if (
            current[0].startswith(topic_starters)
            and index >= 2
            and items[index - 2][0] == "是"
        ):
            inferred[offset] = "copular_intro_topic_boundary"
            clause_start_offset = offset

    return tuple(sorted(inferred.items()))


def _lexical_spans(
    tokens: Sequence[CanonicalToken],
    offsets: Sequence[int],
    text: str,
    profile: ProjectionProfile,
) -> tuple[_LexicalSpan, ...]:
    """Classify Jieba evidence as hard unless a display gate proves it cannot fit.

    Capacity is measured over complete canonical tokens, not merely the scalar
    substring returned by Jieba.  A lexical span that crosses a mixed ASR token
    edge can only be kept cohesive by retaining those complete tokens together.
    """

    spans: list[_LexicalSpan] = []
    for start, end in _jieba_spans(text):
        edges = _edges_inside(offsets, start, end)
        if not edges:
            continue
        token_range = _token_range_for_span(offsets, start, end)
        if token_range is None:  # pragma: no cover - guarded by ``edges``
            continue
        left, right = token_range
        covered_tokens = tokens[left:right]
        covered_text = "".join(token.text for token in covered_tokens)
        covered_columns = display_columns(covered_text)
        assert covered_tokens[0].start_ms is not None
        assert covered_tokens[-1].end_ms is not None
        covered_duration_ms = (
            covered_tokens[-1].end_ms - covered_tokens[0].start_ms
        )
        minimum_reading_ms = (
            reading_units(covered_text)
            / profile.max_reading_units_per_second
            * 1000
        )

        cue_reasons: list[LexicalReviewReason] = []
        if covered_columns > profile.max_lines * profile.hard_line_display_columns:
            cue_reasons.append("lexical_span_exceeds_cue_width")
        if covered_duration_ms > profile.max_cue_duration_ms:
            cue_reasons.append("lexical_span_exceeds_cue_duration")
        if minimum_reading_ms > profile.max_cue_duration_ms:
            cue_reasons.append("lexical_span_exceeds_reading_rate")
        if len({token.speaker for token in covered_tokens}) != 1:
            cue_reasons.append("lexical_span_crosses_speaker_boundary")

        line_reasons: list[LexicalReviewReason] = []
        if covered_columns > profile.hard_line_display_columns:
            line_reasons.append("lexical_span_exceeds_line_width")
        spans.append(
            _LexicalSpan(
                start_offset=start,
                end_offset=end,
                edge_indices=edges,
                cue_relaxation_reasons=tuple(cue_reasons),
                line_relaxation_reasons=tuple(line_reasons),
            )
        )
    return tuple(spans)


def _apply_lexical_spans(
    signals: Sequence[_EdgeSignal],
    spans: Sequence[_LexicalSpan],
    *,
    relaxations: frozenset[_LexicalConstraintKey],
) -> None:
    for span in spans:
        for channel in ("cue", "line"):
            capacity_reasons = span.relaxation_reasons(channel)
            explicitly_relaxed = span.key(channel) in relaxations
            relation: SemanticBoundaryRelation = (
                "discouraged" if capacity_reasons or explicitly_relaxed else "forbidden"
            )
            for edge_index in span.edge_indices:
                _apply_relation(
                    signals[edge_index],
                    channel=channel,
                    relation=relation,
                    strength=0.95 if relation == "discouraged" else 1.0,
                    reason="traditional_lexicon_word_cohesion",
                )


def _build_signals(
    tokens: Sequence[CanonicalToken],
    *,
    protected_terms: Sequence[str],
    sentence_hints: Sequence[SentenceBoundaryHint],
    profile: ProjectionProfile,
    pause_preference_ms: int,
    lexical_relaxations: frozenset[_LexicalConstraintKey] = frozenset(),
) -> tuple[tuple[_EdgeSignal, ...], tuple[int, ...], tuple[_LexicalSpan, ...]]:
    offsets, text = _token_offsets(tokens)
    signals = tuple(_EdgeSignal() for _ in range(len(tokens)))
    protected_spans: list[tuple[int, int, str]] = []
    seen_terms: set[str] = set()
    for term in (*_DEFAULT_PROTECTED_TERMS, *tuple(protected_terms)):
        if not isinstance(term, str) or not term.strip():
            raise ValueError("protected terms must be non-blank strings")
        key = term.casefold()
        if key in seen_terms:
            continue
        seen_terms.add(key)
        protected_spans.extend(
            (start, end, "protected_term") for start, end in _casefold_occurrences(text, term)
        )
    for pattern in _STRUCTURAL_PATTERNS:
        protected_spans.extend(
            (match.start(), match.end(), "protected_grammar_structure")
            for match in pattern.finditer(text)
        )
    protected_spans.extend(
        (match.start(), match.end(), "protected_title")
        for match in _TITLE_PATTERN.finditer(text)
    )
    protected_spans.extend(
        (start, end, "syntactic_attachment_cohesion")
        for start, end in _syntactic_attachment_spans(text)
    )
    for start, end, reason in protected_spans:
        _apply_span(
            signals,
            tokens,
            offsets,
            text,
            start=start,
            end=end,
            profile=profile,
            reason=reason,
            # Grammar patterns are useful cohesion evidence, but unlike an
            # explicit protected term they are not an indivisible name.  A
            # long pattern must therefore yield to timing/readability gates.
            protected=reason
            in {
                "protected_term",
                "protected_title",
                "syntactic_attachment_cohesion",
            },
        )
    lexical_spans = _lexical_spans(tokens, offsets, text, profile)
    _apply_lexical_spans(
        signals,
        lexical_spans,
        relaxations=lexical_relaxations,
    )

    # Closed-class words are attached by scalar offset as well as by token
    # identity.  This covers character and mixed ASR tokens without assuming
    # that the recognizer emitted one word per token.
    edge_by_offset = {offset: index for index, offset in enumerate(offsets[1:-1], start=1)}
    for word in _CLOSED_CLASS:
        for start, end in _casefold_occurrences(text, word):
            for offset in (start, end):
                edge_index = edge_by_offset.get(offset)
                if edge_index is None:
                    continue
                _apply_relation(
                    signals[edge_index],
                    channel="cue",
                    relation="discouraged",
                    strength=0.8,
                    reason="closed_class_no_orphan",
                )
                _apply_relation(
                    signals[edge_index],
                    channel="line",
                    relation="discouraged",
                    strength=0.7,
                    reason="closed_class_no_orphan",
                )

    for connector in _CONNECTORS:
        for start, end in _casefold_occurrences(text, connector):
            before = edge_by_offset.get(start)
            if before is not None:
                for channel in ("cue", "line"):
                    _apply_relation(
                        signals[before],
                        channel=channel,
                        relation="preferred",
                        strength=0.8,
                        reason="connector_starts_new_clause",
                    )
            for edge_index in _edges_inside(offsets, start, end):
                for channel in ("cue", "line"):
                    _apply_relation(
                        signals[edge_index],
                        channel=channel,
                        relation="forbidden",
                        strength=1.0,
                        reason="connector_internal_cohesion",
                    )
            after = edge_by_offset.get(end)
            if after is not None:
                for channel in ("cue", "line"):
                    _apply_relation(
                        signals[after],
                        channel=channel,
                        relation="discouraged",
                        strength=0.8,
                        reason="connector_no_orphan",
                    )

    # A determiner or classifier belongs with the following noun phrase.  It
    # may begin a new cue, but it must not be stranded at the end of one merely
    # to hit a visual width target (``另一個／里程碑``, ``一些／事情``).
    for phrase in _DETERMINER_PHRASES:
        for start, end in _casefold_occurrences(text, phrase):
            for edge_index in _edges_inside(offsets, start, end):
                for channel in ("cue", "line"):
                    _apply_relation(
                        signals[edge_index],
                        channel=channel,
                        relation="forbidden",
                        strength=1.0,
                        reason="determiner_internal_cohesion",
                    )
            after = edge_by_offset.get(end)
            if after is not None:
                for channel in ("cue", "line"):
                    _apply_relation(
                        signals[after],
                        channel=channel,
                        relation="discouraged",
                        strength=0.95,
                        reason="determiner_attaches_to_head",
                    )
    positions = {token.id: index for index, token in enumerate(tokens)}
    for hint in sentence_hints:
        try:
            edge_index = positions[hint.after_token_id] + 1
        except KeyError as exc:
            raise ValueError(f"sentence hint references unknown token {exc.args[0]!r}") from exc
        if edge_index >= len(tokens):
            continue
        signal = signals[edge_index]
        hard_hint_overrides_syntax = (
            hint.strength == 1.0
            and "syntactic_attachment_cohesion" in signal.reasons
            and not signal.reasons.intersection(_HARD_SENTENCE_LEXICAL_BLOCKERS)
        )
        for channel in ("cue", "line"):
            relation_name = f"{channel}_relation"
            if (
                getattr(signal, relation_name) != "forbidden"
                or hard_hint_overrides_syntax
            ):
                setattr(signal, relation_name, "preferred")
                setattr(
                    signal,
                    f"{channel}_strength",
                    max(getattr(signal, f"{channel}_strength"), hint.strength),
                )
        signal.reasons.add("sentence_boundary_hint")
        if hard_hint_overrides_syntax:
            signal.reasons.add("hard_sentence_overrides_syntax")

    # A verified provider separator defines a spoken clause.  Preserve the
    # complete clause whenever it fits the actual cue/line capacity; otherwise
    # retain a strong but defeasible preference.  This prevents the visual
    # target from slicing a short clause merely to make neighbouring cues equal
    # length (for example ``邀請／到`` or ``做出／一些``).
    hinted_edges = sorted(
        {
            positions[hint.after_token_id] + 1: hint.strength
            for hint in sentence_hints
            if positions[hint.after_token_id] + 1 < len(tokens)
            # A provider separator that lands inside a protected lexical
            # span is an alignment artefact, not a real clause boundary.
            # Keeping it here would create two independently indivisible
            # provider clauses while the protected span also requires them
            # to remain joined, which can make an otherwise valid two-line
            # cue impossible (for example ``對《／臺灣製造》``).
            and signals[positions[hint.after_token_id] + 1].cue_relation
            != "forbidden"
        }.items()
    )
    if hinted_edges:
        previous_hint_edge = 0
        for hint_edge, hint_strength in (*hinted_edges, (len(tokens), 0.75)):
            clause_tokens = tokens[previous_hint_edge:hint_edge]
            clause_text = "".join(token.text for token in clause_tokens)
            clause_columns = display_columns(clause_text)
            clause_duration_ms = (
                clause_tokens[-1].end_ms - clause_tokens[0].start_ms
                if clause_tokens
                else 0
            )
            clause_minimum_reading_ms = round(
                reading_units(clause_text)
                / profile.max_reading_units_per_second
                * 1000
            )
            cue_fits = bool(clause_tokens) and (
                # A provider sentence may be longer than one subtitle cue.
                # Making every two-line-sized clause indivisible forces the
                # wrapper to hit an exact visual midpoint, even when that
                # midpoint is inside a word or grammatical phrase.  Only a
                # clause that fits one physical line is atomic; longer
                # clauses retain a strong preference but may form multiple
                # cues at safer lexical edges.
                clause_columns <= profile.hard_line_display_columns
                and clause_duration_ms <= profile.max_cue_duration_ms
                and clause_minimum_reading_ms <= profile.max_cue_duration_ms
                and clause_duration_ms >= profile.min_cue_duration_ms
                and clause_duration_ms >= clause_minimum_reading_ms
                and len({token.speaker for token in clause_tokens}) == 1
            )
            line_fits = bool(clause_tokens) and (
                clause_columns <= profile.hard_line_display_columns
            )
            clause_strength = max(0.65, hint_strength)
            for edge_index in range(previous_hint_edge + 1, hint_edge):
                _apply_relation(
                    signals[edge_index],
                    channel="cue",
                    relation="forbidden" if cue_fits else "discouraged",
                    strength=clause_strength,
                    reason="provider_clause_cohesion",
                )
                _apply_relation(
                    signals[edge_index],
                    channel="line",
                    relation="forbidden" if line_fits else "discouraged",
                    strength=clause_strength,
                    reason="provider_clause_cohesion",
                )
            previous_hint_edge = hint_edge

    known_boundary_offsets = tuple(
        offsets[positions[hint.after_token_id] + 1]
        for hint in sentence_hints
        if positions[hint.after_token_id] + 1 < len(tokens)
    )
    hard_hint_offsets = {
        offsets[positions[hint.after_token_id] + 1]
        for hint in sentence_hints
        if hint.strength == 1.0
        and positions[hint.after_token_id] + 1 < len(tokens)
    }
    for offset, reason in _inferred_sentence_offsets(
        text,
        known_boundary_offsets=known_boundary_offsets,
    ):
        edge_index = edge_by_offset.get(offset)
        if edge_index is None:
            continue
        signal = signals[edge_index]
        if signal.reasons.intersection(_HARD_SENTENCE_LEXICAL_BLOCKERS):
            continue
        if reason == "topic_resumption_boundary" and offset not in hard_hint_offsets:
            # This is one clause, not a sentence end.  A cue boundary would
            # strand the topic (``有些人`` / ``每期节目`` / ``第一个``), while
            # a line break is useful when the complete cue needs wrapping.
            signal.cue_relation = "forbidden"
            signal.cue_strength = 1.0
            channels = ("line",)
        else:
            channels = ("cue", "line")
        for channel in channels:
            setattr(signal, f"{channel}_relation", "preferred")
            setattr(
                signal,
                f"{channel}_strength",
                max(getattr(signal, f"{channel}_strength"), 1.0),
            )
        signal.reasons.add(reason)
        if reason != "topic_resumption_boundary":
            signal.reasons.add("inferred_sentence_boundary")

    for edge_index in range(1, len(tokens)):
        left = tokens[edge_index - 1]
        right = tokens[edge_index]
        signal = signals[edge_index]
        assert left.end_ms is not None and right.start_ms is not None
        signal.pause_ms = max(0, right.start_ms - left.end_ms)
        left_text = left.text.rstrip()
        right_text = right.text.lstrip()
        if (
            left.text != left.text.rstrip()
            or right.text != right.text.lstrip()
            or left.text.isspace()
            or right.text.isspace()
        ):
            for channel in ("cue", "line"):
                _apply_relation(
                    signal,
                    channel=channel,
                    relation="forbidden",
                    strength=1.0,
                    reason="whitespace_internal_cohesion",
                )
        if right_text and right_text[0] in _CLOSING_PUNCTUATION:
            for channel in ("cue", "line"):
                _apply_relation(
                    signal,
                    channel=channel,
                    relation="forbidden",
                    strength=1.0,
                    reason="punctuation_attaches_left",
                )
        if left_text and left_text[-1] in _OPENING_PUNCTUATION:
            for channel in ("cue", "line"):
                _apply_relation(
                    signal,
                    channel=channel,
                    relation="forbidden",
                    strength=1.0,
                    reason="punctuation_attaches_right",
                )
        punctuation_strength = 0.0
        if left_text and left_text[-1] in _HARD_SENTENCE_PUNCTUATION:
            punctuation_strength = 1.0
        elif left_text and left_text[-1] in _SOFT_SENTENCE_PUNCTUATION:
            punctuation_strength = 0.75
        if punctuation_strength:
            for channel in ("cue", "line"):
                _apply_relation(
                    signal,
                    channel=channel,
                    relation="preferred",
                    strength=punctuation_strength,
                    reason="punctuation_boundary",
                )
        if signal.pause_ms >= pause_preference_ms:
            pause_strength = min(
                1.0,
                max(0.35, signal.pause_ms / profile.pause_reward_saturation_ms),
            )
            for channel in ("cue", "line"):
                _apply_relation(
                    signal,
                    channel=channel,
                    relation="preferred",
                    strength=pause_strength,
                    reason="timing_pause_preferred",
                )

    for index, token in enumerate(tokens):
        stripped = token.text.strip()
        if stripped not in _CLOSED_CLASS and stripped not in _CONNECTORS:
            continue
        for edge_index in (index, index + 1):
            if not 1 <= edge_index < len(tokens):
                continue
            is_connector = stripped in _CONNECTORS
            is_before_connector = is_connector and edge_index == index
            _apply_relation(
                signals[edge_index],
                channel="cue",
                relation="preferred" if is_before_connector else "discouraged",
                strength=0.8,
                reason=(
                    "connector_starts_new_clause"
                    if is_before_connector
                    else "connector_no_orphan"
                    if is_connector
                    else "closed_class_no_orphan"
                ),
            )
            _apply_relation(
                signals[edge_index],
                channel="line",
                relation="preferred" if is_before_connector else "discouraged",
                strength=0.7,
                reason=(
                    "connector_starts_new_clause"
                    if is_before_connector
                    else "connector_no_orphan"
                    if is_connector
                    else "closed_class_no_orphan"
                ),
            )
    return signals, offsets, lexical_spans


def _semantic_units(
    tokens: Sequence[CanonicalToken], signals: Sequence[_EdgeSignal]
) -> tuple[SemanticUnit, ...]:
    units: list[SemanticUnit] = []
    for edge_index in range(1, len(tokens)):
        signal = signals[edge_index]
        if signal.cue_relation == "neutral" and signal.line_relation == "neutral":
            continue
        units.append(
            SemanticUnit(
                id=f"accurate-boundary-{edge_index:06d}",
                token_ids=(tokens[edge_index - 1].id, tokens[edge_index].id),
                kind="boundary_pair",
                strength=signal.strength,
                forbid_cue_breaks=signal.cue_relation == "forbidden",
                forbid_line_breaks=signal.line_relation == "forbidden",
                cue_boundary_relation=signal.cue_relation,
                line_boundary_relation=signal.line_relation,
            )
        )
    return tuple(units)


def _mandatory_sentence_edges(
    tokens: Sequence[CanonicalToken],
    signals: Sequence[_EdgeSignal],
    sentence_hints: Sequence[SentenceBoundaryHint],
    *,
    profile: ProjectionProfile,
) -> tuple[int, ...]:
    """Return hard provider sentence ends that can form readable cue runs.

    A strength-1 provider separator represents sentence-final punctuation, not
    merely a favourable visual seam.  It is mandatory unless lexical evidence
    proves the cross-ASR projection landed inside a protected phrase.  Very
    short consecutive utterances are accumulated until the run reaches the
    profile's minimum duration; forcing each 200--300 ms response into its own
    flashing cue would make the projection unsatisfiable and less readable.
    """

    positions = {token.id: index for index, token in enumerate(tokens)}
    candidates = sorted(
        {
            positions[hint.after_token_id] + 1
            for hint in sentence_hints
            if hint.strength == 1.0
            and positions[hint.after_token_id] + 1 < len(tokens)
            and signals[positions[hint.after_token_id] + 1].cue_relation
            != "forbidden"
        }
        | {
            edge_index
            for edge_index in range(1, len(tokens))
            if "inferred_sentence_boundary" in signals[edge_index].reasons
            and signals[edge_index].cue_relation == "preferred"
            and (
                tokens[edge_index - 1].end_ms - tokens[0].start_ms
                >= profile.min_cue_duration_ms
            )
            and (
                tokens[-1].end_ms - tokens[edge_index].start_ms
                >= profile.min_cue_duration_ms
            )
        }
    )
    if not candidates:
        return ()

    # Consecutive hard ends can describe a sentence plus a short spoken tail
    # (``……地方。這樣。然後……``).  Keeping the first edge strands the tail and
    # incorrectly glues it to the next sentence.  Cluster ends whose intervening
    # speech is shorter than one readable cue and retain the last edge instead.
    clustered: list[int] = []
    cluster_last = candidates[0]
    for edge in candidates[1:]:
        between_duration_ms = (
            tokens[edge - 1].end_ms - tokens[cluster_last].start_ms  # type: ignore[operator]
        )
        if between_duration_ms < profile.min_cue_duration_ms:
            cluster_last = edge
            continue
        clustered.append(cluster_last)
        cluster_last = edge
    clustered.append(cluster_last)

    mandatory: list[int] = []
    run_start = 0
    for edge in clustered:
        run_duration_ms = tokens[edge - 1].end_ms - tokens[run_start].start_ms  # type: ignore[operator]
        if run_duration_ms < profile.min_cue_duration_ms:
            continue
        mandatory.append(edge)
        run_start = edge

    if mandatory:
        tail_duration_ms = tokens[-1].end_ms - tokens[mandatory[-1]].start_ms  # type: ignore[operator]
        if tail_duration_ms < profile.min_cue_duration_ms:
            mandatory.pop()
    return tuple(mandatory)


def _selected_boundaries(
    tokens: Sequence[CanonicalToken], projection: ProjectionResult
) -> tuple[tuple[BoundaryChannel, int], ...]:
    positions = {token.id: index for index, token in enumerate(tokens)}
    selected: list[tuple[BoundaryChannel, int]] = []
    for cue in projection.cues:
        start = positions[cue.token_ids[0]]
        end = positions[cue.token_ids[-1]] + 1
        if end < len(tokens):
            selected.append(("cue", end))
        scalar_cursor = 0
        for line in cue.lines[:-1]:
            scalar_cursor += len(line)
            accumulated = 0
            for cut in range(start + 1, end):
                accumulated += len(tokens[cut - 1].text)
                if accumulated == scalar_cursor:
                    selected.append(("line", cut))
                    break
    return tuple(selected)


def _reviews(
    tokens: Sequence[CanonicalToken],
    projection: ProjectionResult,
    signals: Sequence[_EdgeSignal],
    lexical_spans: Sequence[_LexicalSpan],
    *,
    strong_pause_ms: int,
    lexical_relaxations: frozenset[_LexicalConstraintKey],
) -> tuple[BoundaryReviewItem, ...]:
    reviews: list[BoundaryReviewItem] = []
    lexical_by_edge: dict[int, list[_LexicalSpan]] = {}
    for span in lexical_spans:
        for edge_index in span.edge_indices:
            lexical_by_edge.setdefault(edge_index, []).append(span)
    for channel, edge_index in _selected_boundaries(tokens, projection):
        left = tokens[edge_index - 1]
        right = tokens[edge_index]
        signal = signals[edge_index]
        relation = getattr(signal, f"{channel}_relation")
        if left.speaker != right.speaker:
            relation = "mandatory"
        context = "".join(
            token.text
            for token in tokens[max(0, edge_index - 3) : min(len(tokens), edge_index + 3)]
        )
        lexical_reasons: set[LexicalReviewReason] = set()
        lexical_hit = False
        for span in lexical_by_edge.get(edge_index, ()):
            lexical_hit = True
            reasons = span.relaxation_reasons(channel)
            if not reasons and span.key(channel) in lexical_relaxations:
                reasons = ("lexical_constraints_jointly_unsatisfiable",)
            if not reasons:
                raise RuntimeError(
                    "accurate segmentation selected a hard lexical boundary"
                )
            lexical_reasons.update(reasons)
        if lexical_hit:
            reviews.append(
                BoundaryReviewItem(
                    code="forced_lexical_boundary",
                    channel=channel,
                    edge_index=edge_index,
                    left_token_id=left.id,
                    right_token_id=right.id,
                    pause_ms=signal.pause_ms,
                    relation=relation,
                    context=context,
                    reasons=tuple(sorted(lexical_reasons)),
                )
            )
            continue
        if relation in {"preferred", "mandatory"} or signal.pause_ms >= strong_pause_ms:
            continue
        reviews.append(
            BoundaryReviewItem(
                code="forced_low_confidence_boundary",
                channel=channel,
                edge_index=edge_index,
                left_token_id=left.id,
                right_token_id=right.id,
                pause_ms=signal.pause_ms,
                relation=relation,
                context=context,
            )
        )
    return tuple(reviews)


def segment_accurate_subtitles(
    timed_tokens: Sequence[object],
    *,
    episode_id: str,
    generation_id: str,
    protected_terms: Sequence[str] = (),
    sentence_hints: Sequence[SentenceBoundaryHint] = (),
    profile: ProjectionProfile = HORIZONTAL_16X9,
    audio_start_ms: int = 0,
    audio_end_ms: int | None = None,
    pause_preference_ms: int = 240,
    strong_pause_ms: int = 450,
) -> AccurateSegmentationResult:
    """Project corrected tokens into natural, exact-copy SRT with weak-cut review."""

    if pause_preference_ms < 0 or strong_pause_ms < pause_preference_ms:
        raise ValueError("pause thresholds must be ordered non-negative milliseconds")
    tokens = _canonical_tokens(timed_tokens)

    def project_with_relaxations(
        relaxations: frozenset[_LexicalConstraintKey],
    ) -> tuple[
        tuple[_EdgeSignal, ...],
        tuple[_LexicalSpan, ...],
        tuple[SemanticUnit, ...],
        ProjectionResult,
    ]:
        built_signals, _offsets, built_lexical_spans = _build_signals(
            tokens,
            protected_terms=protected_terms,
            sentence_hints=sentence_hints,
            profile=profile,
            pause_preference_ms=pause_preference_ms,
            lexical_relaxations=relaxations,
        )
        built_units = _semantic_units(tokens, built_signals)
        mandatory_cue_edges = _mandatory_sentence_edges(
            tokens,
            built_signals,
            sentence_hints,
            profile=profile,
        )
        built_projection = project_semantic_units(
            tokens,
            built_units,
            profile,
            episode_id=episode_id,
            generation_id=generation_id,
            audio_start_ms=audio_start_ms,
            audio_end_ms=audio_end_ms,
            mandatory_cue_boundaries=mandatory_cue_edges,
        )
        return built_signals, built_lexical_spans, built_units, built_projection

    lexical_relaxations: frozenset[_LexicalConstraintKey] = frozenset()
    try:
        signals, lexical_spans, units, projection = project_with_relaxations(
            lexical_relaxations
        )
    except ProjectionUnsatisfiableError as hard_lexical_error:
        # Distinguish an inherently unsatisfiable projection from an otherwise
        # legal projection whose individually valid lexical constraints cannot
        # all hold together.  The all-soft attempt is diagnostic and no lexical
        # cut from it is accepted silently.
        _, _, baseline_lexical_spans = _build_signals(
            tokens,
            protected_terms=protected_terms,
            sentence_hints=sentence_hints,
            profile=profile,
            pause_preference_ms=pause_preference_ms,
        )
        all_hard_eligible = frozenset(
            span.key(channel)
            for span in baseline_lexical_spans
            for channel in ("cue", "line")
            if not span.relaxation_reasons(channel)
        )
        if not all_hard_eligible:
            raise
        try:
            soft_signals, soft_spans, soft_units, soft_projection = (
                project_with_relaxations(all_hard_eligible)
            )
        except ProjectionUnsatisfiableError:
            raise hard_lexical_error

        selected = set(_selected_boundaries(tokens, soft_projection))
        lexical_relaxations = frozenset(
            span.key(channel)
            for span in soft_spans
            for channel in ("cue", "line")
            if not span.relaxation_reasons(channel)
            if any((channel, edge_index) in selected for edge_index in span.edge_indices)
        )
        if not lexical_relaxations:
            raise hard_lexical_error

        # Re-harden every unnecessary softening.  The remaining deterministic
        # set is irreducible: removing any one relaxation makes the complete
        # hard-constraint system unsatisfiable again.
        signals, lexical_spans, units, projection = (
            soft_signals,
            soft_spans,
            soft_units,
            soft_projection,
        )
        for constraint in sorted(lexical_relaxations):
            trial_relaxations = lexical_relaxations - {constraint}
            try:
                trial = project_with_relaxations(trial_relaxations)
            except ProjectionUnsatisfiableError:
                continue
            lexical_relaxations = trial_relaxations
            signals, lexical_spans, units, projection = trial
    source_text = "".join(token.text for token in tokens)
    rendered_text = "".join(line for cue in projection.cues for line in cue.lines)
    if rendered_text != source_text or projection.token_ids != tuple(token.id for token in tokens):
        raise RuntimeError("accurate segmentation changed corrected token text or order")
    if any(len(cue.lines) > profile.max_lines for cue in projection.cues):
        raise RuntimeError("accurate segmentation exceeded the projection line limit")
    decisions = tuple(
        BoundaryDecision(
            edge_index=index,
            left_token_id=tokens[index - 1].id,
            right_token_id=tokens[index].id,
            cue_relation=(
                "mandatory"
                if tokens[index - 1].speaker != tokens[index].speaker
                else signals[index].cue_relation
            ),
            line_relation=(
                "mandatory"
                if tokens[index - 1].speaker != tokens[index].speaker
                else signals[index].line_relation
            ),
            strength=signals[index].strength,
            pause_ms=signals[index].pause_ms,
            reasons=tuple(sorted(signals[index].reasons)),
        )
        for index in range(1, len(tokens))
    )
    return AccurateSegmentationResult(
        projection=projection,
        srt_text=render_srt(projection),
        boundary_reviews=_reviews(
            tokens,
            projection,
            signals,
            lexical_spans,
            strong_pause_ms=strong_pause_ms,
            lexical_relaxations=lexical_relaxations,
        ),
        boundary_decisions=decisions,
        semantic_units=units,
    )


__all__ = [
    "AccurateSegmentationResult",
    "BoundaryDecision",
    "BoundaryReviewItem",
    "SentenceBoundaryHint",
    "segment_accurate_subtitles",
]
