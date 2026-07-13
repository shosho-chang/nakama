"""``KB/Wiki/Outputs/`` 寫入儲存層 — D-19 復活 + D-18 確認式 write-back（N524）.

Centaur 規格 v0.2 D-19：復活 ADR-028 §3 刪掉的 ``KB/Wiki/Outputs/``，作為查詢
write-back（P-9）的落點。D-18：write-back 是**確認式**——修修點頭才寫入，``log.md``
無論如何記一行。

**本 module 的範圍 = 儲存層**（task prompt §2.4）：只鋪「人說值得存之後，把 Output
頁寫進 vault」的寫入函式。**query workflow 本身**（P-8 回答 → P-9 蒸餾 → 問修修
「值得存嗎？」）另開 task，不在這裡。

紅線：Output 頁是衍生層，受紅線 5 拘束——終端證據只能 cite Sources/Raw/Annotations，
不得以另一個 Concept/Output 作為事實來源。寫入前過 :class:`ProvenanceLinter`
（同 concept 走的那道關），違規即 reject。**絕不寫 KB/Permanent/**（紅線 1）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import yaml

from shared.config import get_vault_path
from shared.log import get_logger
from shared.permanent_layer import assert_not_permanent_target
from shared.provenance_linter import ProvenanceLinter, ProvenanceViolation

logger = get_logger("nakama.output_writer")

#: Output 頁落點（D-19 復活）。
KB_OUTPUTS_DIR = "KB/Wiki/Outputs"

#: slug 防 path traversal（CJK + 英數 + dash/underscore；首字須為 word/CJK char）。
_SAFE_SLUG_RE = re.compile(r"^[\w一-鿿][\w\-一-鿿]*$")


@dataclass
class OutputWriteResult:
    """一次 write-back 的結果。"""

    slug: str
    relative_path: str
    written: bool


def _validate_slug(slug: str) -> None:
    if not isinstance(slug, str) or not _SAFE_SLUG_RE.fullmatch(slug):
        raise ValueError(f"unsafe output slug: {slug!r}")


def write_output_page(
    slug: str,
    *,
    title: str,
    body: str,
    from_query: str | None = None,
    confidence: str = "medium",
    source_refs: list[str] | None = None,
    today: date | None = None,
) -> OutputWriteResult:
    """把確認過的查詢蒸餾結果寫成 ``KB/Wiki/Outputs/{slug}.md``（D-18 確認式儲存層）.

    **呼叫前提**：修修已確認「值得存」（確認 workflow 在 query task，不在此）。本函式
    只負責寫——它不問、不蒸餾、不呼叫 LLM。

    Args:
        slug: 檔名 stem（防 traversal）。
        title: 頁面標題。
        body: 蒸餾後的 markdown 正文（含 ``## Sources`` / ``## Evidence`` 終端證據）。
        from_query: 來源查詢日期（P-9 frontmatter ``from_query``）。
        confidence: ``low`` / ``medium`` / ``high``。
        source_refs: frontmatter ``source_refs``（終端證據路徑，受紅線 5 拘束）。
        today: 注入今日（測試用）；None → 真今日。

    Returns:
        ``OutputWriteResult(written=True)``。

    Raises:
        ProvenanceViolation: body / source_refs 的終端證據指向 Concept/Output（紅線 5）。
        PermanentWriteViolation: 解析路徑落在 KB/Permanent/（紅線 1，防呆）。
        ValueError: slug 不合法。
    """
    _validate_slug(slug)
    today = today or date.today()
    relative = f"{KB_OUTPUTS_DIR}/{slug}.md"

    # 紅線 1 防呆：Output slug 不可被 coerce 進永久層（slug 已過 traversal 檢查，
    # 此處是 defence-in-depth chokepoint）。
    assert_not_permanent_target(relative)

    # 紅線 5：Output 是衍生層，終端證據只能 cite Sources/Raw/Annotations。
    report = ProvenanceLinter().lint_page(
        relative,
        body,
        mentioned_in=[str(s) for s in (source_refs or [])],
    )
    if report.status == "violations":
        for finding in report.findings:
            logger.error(
                "output provenance violation (red line 5)",
                extra={"slug": slug, "citation": finding.citation},
            )
        raise ProvenanceViolation(report)

    fm = {
        "type": "output",
        "title": title,
        "author": "agent_query",
        "from_query": from_query or str(today),
        "confidence": confidence,
        "source_refs": list(source_refs or []),
        "created": str(today),
        "updated": str(today),
    }
    fm_str = yaml.safe_dump(
        fm, allow_unicode=True, default_flow_style=False, sort_keys=False, width=10**9
    ).strip()
    content = f"---\n{fm_str}\n---\n\n{body.rstrip()}\n"

    dest = get_vault_path() / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    logger.info("output page written", extra={"slug": slug, "path": relative})
    return OutputWriteResult(slug=slug, relative_path=relative, written=True)
