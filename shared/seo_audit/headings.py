"""Heading hierarchy deterministic checks（H1-H3）。

Slice D.1 §附錄 A 3 條 rule：
    H1 H1 唯一
    H2 H2/H3 階層不跳級（H1→H3 算跳）
    H3 H 結構合理（內文 ≥ 1 個 H2）

Note: 「H1 含 focus keyword 語義」是 LLM semantic check（§附錄 C 第 1 條），
不在這裡。`focus_keyword=None` 沿用 ADR-009 §D9 簽名一致性，本 module 暫無
使用點，但保留參數讓 D.2 skill 可以統一傳。
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from shared.seo_audit.types import AuditCheck


def check_headings(
    soup: BeautifulSoup,
    focus_keyword: str | None = None,  # noqa: ARG001 — reserved for D.2 keyword sub-checks
) -> list[AuditCheck]:
    """跑 H1-H3 共 3 條 heading check。"""
    return [
        _check_h1_unique(soup),
        _check_no_skip(soup),
        _check_has_h2(soup),
    ]


def _check_h1_unique(soup: BeautifulSoup) -> AuditCheck:
    h1s = soup.find_all("h1")
    count = len(h1s)
    if count == 1:
        return AuditCheck(
            rule_id="H1",
            name="H1 唯一",
            category="headings",
            severity="critical",
            status="pass",
            actual="1 個 H1",
            expected="== 1",
            fix_suggestion="",
        )
    if count == 0:
        return AuditCheck(
            rule_id="H1",
            name="H1 唯一",
            category="headings",
            severity="critical",
            status="fail",
            actual="0 個 H1",
            expected="== 1",
            fix_suggestion="補一個明確 H1（與 <title> 一致或語義相近）",
        )
    return AuditCheck(
        rule_id="H1",
        name="H1 唯一",
        category="headings",
        severity="critical",
        status="fail",
        actual=f"{count} 個 H1",
        expected="== 1",
        fix_suggestion="多 H1：合併或將次要降為 H2",
        details={"h1_texts": [h.get_text(strip=True) for h in h1s]},
    )


_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def _check_no_skip(soup: BeautifulSoup) -> AuditCheck:
    skips: list[tuple[str, str]] = []
    prev_level = 0
    for tag in soup.find_all(_HEADING_TAGS):
        level = int(tag.name[1])
        if prev_level and level > prev_level + 1:
            skips.append((f"H{prev_level}", tag.name.upper()))
        prev_level = level

    if not skips:
        return AuditCheck(
            rule_id="H2",
            name="heading 階層不跳級",
            category="headings",
            severity="warning",
            status="pass",
            actual="無跳級",
            expected="無跳級（H1→H3 / H2→H4 等）",
            fix_suggestion="",
        )
    sample = ", ".join(f"{a}→{b}" for a, b in skips[:3])
    return AuditCheck(
        rule_id="H2",
        name="heading 階層不跳級",
        category="headings",
        severity="warning",
        status="warn",
        actual=f"{len(skips)} 處跳級（前 3：{sample}）",
        expected="無跳級",
        fix_suggestion="補中介 heading 層或降階",
        details={"skips": skips},
    )


def _check_has_h2(soup: BeautifulSoup) -> AuditCheck:
    h2_count = len(soup.find_all("h2"))
    if h2_count >= 1:
        return AuditCheck(
            rule_id="H3",
            name="H 結構合理（內文 ≥ 1 個 H2）",
            category="headings",
            severity="info",
            status="pass",
            actual=f"{h2_count} 個 H2",
            expected="≥ 1 H2",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="H3",
        name="H 結構合理（內文 ≥ 1 個 H2）",
        category="headings",
        severity="info",
        status="warn",
        actual="0 個 H2",
        expected="≥ 1 H2（長文章建議每 ~300 字一個）",
        fix_suggestion="拆章節，每段加 H2 提升可掃讀性",
    )
