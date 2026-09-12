"""一個 reason code 回答一類問題（ADR-069 階段 5）。

盤點那天這個模組有 48 個 error code，其中只有 5 個真的在生產線上 fire 過。最會長的
是四個家族——Editorial Master 身分 8 個、Resolve 綁定 9 個、語意派工 9 個、drift
3 個——它們各自指向**同一個判斷**，只是把「到底哪裡不對」編進了 code 字串裡。

於是操作的人要背一張對照表，而程式多出二十幾個可以拼錯、可以忘記加進 Literal 的
字串。ADR-069 的規則：`reason_code` 回答「是哪一類事情不對」，訊息與 `diagnostic`
回答「到底哪裡不對」。

這支測試用 AST 掃出模組裡每一個 `reason_code=` 的字面值，跟名單逐字比對。名單長回
去就會紅——它擋的不是某一個 bug，是這個家族再一次分裂。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_MODULE_ROOT = (
    pathlib.Path(__file__).resolve().parents[3]
    / "agents"
    / "brook"
    / "script_video"
    / "finished_cut_production"
)

#: 四個收斂後的家族。每一個都只回答一個問題。
COLLAPSED_FAMILIES = {
    #: 這支 Editorial Master 還是登錄那一支嗎？（收據、cache、媒體雜湊、片長、
    #: 專案歸屬——全部都是這一個問題的不同問法。）
    "editorial_master_mismatch",
    #: 登錄的那條 Resolve timeline 還在、還是同一條嗎？（綁定、UID、名稱、格率、
    #: 片長。UID＋名稱的精確匹配留著，只是不再各自有一個 code。）
    "resolve_binding_mismatch",
    #: 語意派工沒有拿到終局結果。行為分支在 `SemanticDispatchOutcome.state`
    #: （ledger 三態），不在 code。
    "semantic_dispatch_failed",
    #: 登錄之後有人動過自己的軌。來源範圍、字幕軌、格率都是那條 timeline 上
    #: 受保護的東西，不是三件事。
    "protected_track_drift",
}

#: 家族之外的 code，每一個都對應一個**不同**的判斷。
DISTINCT_CODES = {
    #: 同名的檔案 bytes 被換過。素材收據簡化之後（ADR-069 階段 7 砍掉 URL
    #: profile），這是唯一還在保護素材來歷的那道鎖，所以它有自己的名字——
    #: 跟「reference 綁錯」（`final_asset_identity_mismatch`）是兩件事。
    "asset_digest_mismatch",
    "authority_chain_mismatch",
    "final_asset_identity_mismatch",
    "final_asset_unavailable",
    "materialization_journal_conflict",
    "materialization_journal_incomplete",
    "materialization_journal_invalid",
    "materialization_plan_missing",
    "plan_record_staging_failed",
    "preview_probe_failed",
    "preview_transaction_mismatch",
    "production_run_missing",
    "production_run_not_review_ready",
    "resolve_prepare_failed",
    "source_range_outside_editorial_master",
    "stock_not_landscape_16_9",
    "subtitle_staging_conflict",
    "subtitle_staging_failed",
}

EXPECTED_CODES = COLLAPSED_FAMILIES | DISTINCT_CODES

#: `reason_code=(...)` 的條件式裡會出現這些字面值，它們是被比較的值，不是 code。
_NOT_CODES = {"incomplete", "needs_review"}


def _reason_code_literals() -> dict[str, set[str]]:
    """Every literal that reaches a `reason_code=` keyword, by module."""

    found: dict[str, set[str]] = {}
    for path in sorted(_MODULE_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        codes = {
            node.value
            for keyword in ast.walk(tree)
            if isinstance(keyword, ast.keyword) and keyword.arg == "reason_code"
            for node in ast.walk(keyword.value)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        } - _NOT_CODES
        if codes:
            found[path.name] = codes
    return found


def test_the_module_raises_exactly_the_collapsed_reason_code_vocabulary() -> None:
    raised = set().union(*_reason_code_literals().values())

    assert raised == EXPECTED_CODES


def test_no_retired_family_member_survives_anywhere_in_the_module() -> None:
    """折疊掉的那些字串一個都不准留——留一個就是留一條回頭路。"""

    retired = {
        "editorial_master_cache_invalid",
        "editorial_master_contract_invalid",
        "editorial_master_identity_mismatch",
        "editorial_master_media_drift",
        "editorial_master_verification_failed",
        "editorial_master_project_mismatch",
        "editorial_master_duration_invalid",
        "editorial_master_content_identity_mismatch",
        "resolve_project_identity_mismatch",
        "canonical_timeline_unknown",
        "canonical_timeline_ambiguous",
        "canonical_timeline_live_drift",
        "canonical_identity_mismatch",
        "canonical_binding_unknown",
        "canonical_binding_ambiguous",
        "canonical_authority_failed",
        "timeline_frame_rate_unavailable",
        "timeline_duration_drift",
        "semantic_dispatch_error",
        "semantic_dispatch_incomplete",
        "semantic_dispatch_indeterminate",
        "source_range_drift",
        "subtitle_contract_drift",
        "frame_rate_drift",
    }
    sources = {
        path.name: path.read_text(encoding="utf-8") for path in sorted(_MODULE_ROOT.glob("*.py"))
    }

    offenders = {
        (name, code) for name, source in sources.items() for code in retired if code in source
    }

    assert offenders == set()


def test_a_reason_code_is_never_computed_from_a_diagnostic_kind() -> None:
    """`reason_code=f"semantic_{code}"` 那種寫法讓一個家族長出九個字串。

    code 是**封閉集合**才擋得住拼錯與遺漏；f-string 一寫，集合就不封閉了，
    而上面那支枚舉測試也會跟著失效（AST 看不到組出來的字串）。
    """

    computed: list[tuple[str, int]] = []
    for path in sorted(_MODULE_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for keyword in ast.walk(tree):
            if not (isinstance(keyword, ast.keyword) and keyword.arg == "reason_code"):
                continue
            for node in ast.walk(keyword.value):
                if isinstance(node, ast.JoinedStr):
                    computed.append((path.name, node.lineno))

    assert computed == []


@pytest.mark.parametrize("family", sorted(COLLAPSED_FAMILIES))
def test_each_collapsed_family_is_actually_used(family: str) -> None:
    """折疊後的 code 必須真的有人 raise——不然這份名單是願望清單，不是事實。"""

    raised = set().union(*_reason_code_literals().values())

    assert family in raised
