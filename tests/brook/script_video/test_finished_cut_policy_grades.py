"""分級只寫在一張表裡，而且那張表真的被讀（ADR-069 階段 7）。

在這之前分級散在兩處，而且其中一處是死的：`BLOCKING_DIAGNOSTICS` 當時那四筆全部在
`validate` 裡提前 `return PolicyDecision("needs_review", …)`，走不到 `decide()`；
而唯一真的走到 `decide()` 的硬擋——章節卡投影——被 5bdc499b 從名單裡移掉了。名單看
起來有四道防線，實際上一道都不在，唯一該擋的那條反而放行。

這一組測試鎖三件事：每個 code 都分過級、`decide()` 真的按表擋、以及「前置條件」為
什麼不能被降級（下一行就 index 它驗過的東西）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import get_args

import pytest

from agents.brook.script_video.finished_cut_production import _policy as policy_module
from agents.brook.script_video.finished_cut_production._context import (
    CanonicalSection,
    CutSourceRange,
    EditorialCutContext,
)
from agents.brook.script_video.finished_cut_production._policy import (
    BLOCKING_DIAGNOSTICS,
    DIAGNOSTIC_GRADES,
    PolicyDiagnostic,
    PolicyDiagnosticCode,
    decide,
)


def test_every_declared_diagnostic_code_has_exactly_one_grade() -> None:
    declared = set(get_args(PolicyDiagnosticCode))

    assert set(DIAGNOSTIC_GRADES) == declared
    assert len(DIAGNOSTIC_GRADES) == len(declared)


def test_every_grade_is_one_of_the_three() -> None:
    assert set(DIAGNOSTIC_GRADES.values()) <= {"precondition", "blocking", "warning"}


def test_the_blocking_set_is_derived_from_the_table_not_copied() -> None:
    # 抄一份就會像 5bdc499b 那樣：兩邊各自漂走，而沒有人發現名單已經空了。
    assert BLOCKING_DIAGNOSTICS == frozenset(
        code for code, grade in DIAGNOSTIC_GRADES.items() if grade == "blocking"
    )


def test_the_chapter_transition_rule_is_the_only_thing_decide_blocks_on() -> None:
    """修修 2026-09-12 裁決：這一條升回 blocking，其餘降警告。"""

    assert BLOCKING_DIAGNOSTICS == frozenset({"chapter_transition_projection_mismatch"})


def test_decide_actually_blocks_on_a_blocking_grade() -> None:
    # 這一條以前永遠不會紅：名單裡的四筆都走不到 `decide()`，所以 `blocking`
    # 那個 tuple 恆為空，`decide()` 只會回 accepted 或 accepted_with_warnings。
    decision = decide(
        (
            PolicyDiagnostic("chapter_transition_projection_mismatch", "章節標題被改寫"),
            PolicyDiagnostic("visual_gap_exceeded", "中段沒有畫面"),
        )
    )

    assert decision.status == "needs_review"


def test_decide_lets_a_warning_only_run_through() -> None:
    decision = decide((PolicyDiagnostic("visual_gap_exceeded", "中段沒有畫面"),))

    assert decision.status == "accepted_with_warnings"
    assert decision.diagnostics[0].code == "visual_gap_exceeded"


def test_no_diagnostics_is_plainly_accepted() -> None:
    assert decide(()).status == "accepted"


def _context(**overrides) -> EditorialCutContext:
    defaults = {
        "episode_id": "episode-1",
        "cut_id": "value-L04",
        "format": "long",
        "editorial_master_id": "c" * 64,
        "tight_cut_id": "tight-1",
        "duration_sec": 600.0,
        "source_ranges": (CutSourceRange(0.0, 600.0),),
        "cues": (),
        "sections": (CanonicalSection("section-1", "開場", 0.0),),
    }
    return EditorialCutContext(**{**defaults, **overrides})


@pytest.mark.parametrize(
    ("code", "context"),
    [
        # duration 與來源範圍總和對不上：後面的覆蓋率、節奏、章節都拿 duration 當分母。
        ("source_range_sum_mismatch", _context(duration_sec=540.0)),
        # 沒有章節資料：下一行就 `sections[0]`。
        ("canonical_sections_missing", _context(sections=())),
        # 第一段不從 0 開始：章節卡的配對從這裡推。
        (
            "first_section_not_zero",
            _context(sections=(CanonicalSection("section-1", "開場", 12.0),)),
        ),
    ],
)
def test_a_precondition_stops_validate_before_the_rules_that_index_it(
    code: PolicyDiagnosticCode,
    context: EditorialCutContext,
) -> None:
    """前置條件降不了級——降了只是把 IndexError 推到更下游、訊息更難懂。

    所以它們不走 `decide()`，`validate` 當場 return；分級表把這件事寫成
    `precondition`，而不是假裝它們是 blocking（那張名單讀不到它們）。
    """

    decision = policy_module.LongV2Policy().validate(
        replace(_long_input(), context=context, components=())
    )

    assert decision.status == "needs_review"
    assert [diagnostic.code for diagnostic in decision.diagnostics] == [code]
    assert DIAGNOSTIC_GRADES[code] == "precondition"


def _long_input():
    from tests.brook.script_video.test_finished_cut_policy import _long_input as build

    return build()
