"""seo_audit dataclasses — Slice D.1 凍結（ADR-009 Phase 1.5）。

`AuditCheck` = 單條 rule 結果，frozen 讓 list aggregation 不擔心 mutation；
`AuditResult` = 一次 audit 的 aggregator，提供 status counts properties 給
markdown report 和 D.2 skill 用。

`actual` / `expected` 都是人類可讀字串（給 markdown report 直接渲染）；schema
級結構化欄位若需要再透過 `details: dict` 攜帶（例：JSON-LD parse error 細節
塞 `details["json_error"]`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CheckCategory = Literal[
    "metadata",
    "opengraph",
    "headings",
    "images",
    "structure",
    "schema",
    "performance",
    "semantic",
    "fetch",
]
CheckSeverity = Literal["critical", "warning", "info"]
CheckStatus = Literal["pass", "warn", "fail", "skip"]


@dataclass(frozen=True)
class AuditCheck:
    """單一 deterministic / semantic check 結果。"""

    rule_id: str  # 例 "M1", "H1", "SC2"
    name: str  # 例 "title 長度 50-60"
    category: CheckCategory
    severity: CheckSeverity
    status: CheckStatus
    actual: str
    expected: str
    fix_suggestion: str
    details: dict = field(default_factory=dict)


@dataclass
class AuditResult:
    """aggregator — Slice D.2 audit.py 主流程把所有 module 結果塞進來。"""

    url: str
    fetched_at: str  # ISO 8601 UTC
    checks: list[AuditCheck] = field(default_factory=list)

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "pass")

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "warn")

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "fail")

    @property
    def skip_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "skip")
