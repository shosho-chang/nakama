"""Provenance linter — Centaur 規格 v0.2 §7 紅線 2 & 5 enforcement（N520 凍結 / N524 升級真檢查）.

紅線（v0.2 §7）：
  2. 每個事實宣稱附 citation，溯源回 ``KB/Raw/`` 或 ``KB/Annotations/`` 錨點。
  5. Concept / Output 的終端證據只能是 Sources / Raw / Annotations，**不得**以
     另一個 Concept / Output 作為事實來源（防 citation laundering / wiki 自我餵食）。

歷史：N520 把這個 module 留成**有意的 placeholder**——介面凍結、``is_terminal_evidence``
原子函式可用且已測，但 :meth:`ProvenanceLinter.lint_page` 回傳 ``status="deferred"``，
把逐 claim 的 citation 抽取與 laundering 偵測延到 N524（route C ingest 接線）。本檔
即 N524 的升級：``lint_page`` 從 deferred stub 變成真 enforcement，在 Concept/Output
寫入前攔截紅線 5 違規（terminal evidence 指向另一個 Concept/Output → reject）。

**紅線 5 的「終端證據」與「概念關係」之分**（本 linter 的核心判定）：

- *終端證據* = 「這個事實宣稱的根據是什麼」。它落在頁面的 **evidence 位置**：
  ``## Sources`` 區塊的 bullet、``source:`` 行、frontmatter ``mentioned_in``。
  這些位置的 citation **必須**指向 Sources / Raw / Annotations。指向 Concept/Output
  即違規——那是拿衍生頁當事實來源（wiki 自我餵食）。
- *概念關係* = 「這個概念跟哪些概念有關」。它落在 ``## Related Concepts`` /
  ``## Sub-concepts`` / ``## 文獻分歧`` 等 **relation 位置**，指向其他 Concept 是
  **合法且高價值**的（v0.2 §3 typed edges）。本 linter **不**碰這些位置。

換言之：紅線 5 攔的是「evidence 欄裡塞 Concept/Output」，不是「概念之間互鏈」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 紅線 2/5 認可的「終端證據」來源層（事實的根，不可再往上指 Concept/Output）。
TERMINAL_EVIDENCE_PREFIXES = ("KB/Raw/", "KB/Annotations/", "KB/Wiki/Sources/")

#: 不可作為終端證據的「衍生層」（紅線 5：禁止 Concept→Concept / Output 自我餵食）。
DERIVED_LAYER_PREFIXES = ("KB/Wiki/Concepts/", "KB/Wiki/Outputs/")

#: 本 linter 覆蓋的紅線編號（對照 v0.2 §7）。
ENFORCED_RED_LINES = (2, 5)

#: evidence 位置的 H2 區塊標題（這些區塊內的 citation 視為終端證據，受紅線 5 拘束）。
#: ``## Sources`` 是 concept aggregator 的 source 清單（kb_writer H2_ORDER）；
#: ``## Evidence`` 是 promotion_renderer / Output 頁的證據區。
EVIDENCE_SECTION_HEADINGS = ("## Sources", "## Evidence")

#: relation 位置的 H2 區塊標題（指向其他 Concept 合法，**不**受紅線 5 拘束）。
#: 列出來是為了文件化意圖；判定上採白名單反面——只有 EVIDENCE 區塊受檢。
RELATION_SECTION_HEADINGS = (
    "## Related Concepts",
    "## Sub-concepts",
    "## Field-level Controversies",
    "## 文獻分歧 / Discussion",
)

#: 衍生層 wikilink 的辨識前綴（``[[Concepts/foo]]`` / ``[[Outputs/bar]]``）。
#: 大小寫不敏感，容忍 ``KB/Wiki/`` 完整前綴或裸 ``Concepts/`` 子路徑。
_DERIVED_WIKILINK_PATTERNS = (
    re.compile(r"^(?:kb/wiki/)?concepts/", re.IGNORECASE),
    re.compile(r"^(?:kb/wiki/)?outputs/", re.IGNORECASE),
)

#: 終端證據 wikilink 的辨識前綴（``[[Sources/...]]`` / ``[[Raw/...]]`` / ``[[Annotations/...]]``）。
_TERMINAL_WIKILINK_PATTERNS = (
    re.compile(r"^(?:kb/wiki/)?sources/", re.IGNORECASE),
    re.compile(r"^(?:kb/)?raw/", re.IGNORECASE),
    re.compile(r"^(?:kb/)?annotations/", re.IGNORECASE),
)

#: 抓 markdown 內 ``[[target]]`` / ``[[target|alias]]`` 的 target。
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")

#: 抓 ``source: `path`` `` / ``source: path`` 行的路徑（promotion_renderer evidence 格式）。
_SOURCE_LINE_RE = re.compile(r"^\s*source:\s*`?([^`\n]+?)`?\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class ProvenanceFinding:
    """單筆 provenance 違規。"""

    red_line: int  # 2 或 5
    page_path: str
    message: str
    citation: str | None = None


@dataclass
class ProvenanceReport:
    """一次 lint 的結果。

    ``status``：``"clean"``（檢查過、無違規）/ ``"violations"``（有違規）/
    ``"skipped"``（非 Concept/Output 頁，紅線 5 不適用）。N520 的 ``"deferred"``
    已在 N524 退役——不再有「未檢查但假裝 ok」的狀態。
    """

    page_path: str
    status: str  # "clean" | "violations" | "skipped"
    findings: list[ProvenanceFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """clean 或 skipped 都算「未擋下」；只有 violations 是 fail。"""
        return self.status != "violations"


def is_terminal_evidence(citation_path: str) -> bool:
    """``citation_path`` 是否為紅線 2/5 認可的終端證據層（Raw/Annotations/Sources）。

    此純函式是紅線 5 判定的原子操作。輸入為 **vault-relative 完整路徑**
    （``KB/Wiki/Sources/foo`` 之類）；裸 wikilink target（``Sources/foo``）的分類走
    :func:`_classify_wikilink_target`。
    """
    norm = citation_path.replace("\\", "/")
    if norm.startswith(DERIVED_LAYER_PREFIXES):
        return False
    return norm.startswith(TERMINAL_EVIDENCE_PREFIXES)


def _classify_wikilink_target(target: str) -> str:
    """把 wikilink target 分成 ``"terminal"`` / ``"derived"`` / ``"unknown"``。

    - ``"terminal"``：指向 Sources / Raw / Annotations（合法終端證據）。
    - ``"derived"``：指向 Concepts / Outputs（紅線 5：不可當終端證據）。
    - ``"unknown"``：裸 stem（``[[foo]]``，無子目錄前綴）——無法從 link 本身斷定層級，
      視為 unknown，**不**判違規（避免誤殺：concept page 的 ``## Sources`` 常以
      ``[[stem]]`` 形式 cite source 頁，stem 不帶 ``Sources/`` 前綴）。
    """
    norm = target.strip().replace("\\", "/").lstrip("/")
    for pat in _DERIVED_WIKILINK_PATTERNS:
        if pat.match(norm):
            return "derived"
    for pat in _TERMINAL_WIKILINK_PATTERNS:
        if pat.match(norm):
            return "terminal"
    return "unknown"


def _is_concept_or_output_page(page_path: str) -> bool:
    """頁面本身是否為 Concept / Output（紅線 5 只拘束這兩層的終端證據）。"""
    norm = page_path.replace("\\", "/")
    return any(prefix in f"/{norm}" for prefix in ("/KB/Wiki/Concepts/", "/KB/Wiki/Outputs/"))


def _evidence_section_bodies(body: str) -> list[str]:
    """切出 body 內所有 evidence 區塊（``## Sources`` / ``## Evidence``）的內文。

    用 H2 邊界切；evidence 區一路吃到下一個 ``## `` heading 或檔尾。relation 區塊
    （``## Related Concepts`` 等）不在回傳內——它們指向 Concept 合法。
    """
    sections: list[str] = []
    lines = body.split("\n")
    current_heading: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        if current_heading in EVIDENCE_SECTION_HEADINGS and buf:
            sections.append("\n".join(buf))

    for line in lines:
        if line.startswith("## "):
            _flush()
            current_heading = line.strip()
            buf = []
        else:
            buf.append(line)
    _flush()
    return sections


def extract_evidence_citations(body: str) -> list[str]:
    """抽出 body 內**終端證據位置**的 citation（wikilink target + ``source:`` 路徑）。

    終端證據位置 = ``## Sources`` / ``## Evidence`` 區塊內的 ``[[...]]``，加上任何
    ``source: `path` `` 行（promotion_renderer evidence 格式，不限區塊）。relation
    區塊的 wikilink 刻意排除——那是概念互鏈，不是事實來源。

    回傳順序 = 文件出現序（deterministic，便於測試）。
    """
    citations: list[str] = []
    for section in _evidence_section_bodies(body):
        for m in _WIKILINK_RE.finditer(section):
            citations.append(m.group(1).strip())
    for m in _SOURCE_LINE_RE.finditer(body):
        citations.append(m.group(1).strip())
    return citations


class ProvenanceLinter:
    """紅線 2/5 的 enforcement（N524：真檢查，取代 N520 deferred stub）。"""

    def lint_page(
        self,
        page_path: str,
        body: str,
        *,
        mentioned_in: list[str] | None = None,
    ) -> ProvenanceReport:
        """檢查單頁的 citation provenance（紅線 5）。

        只對 Concept / Output 頁生效（其他頁回 ``status="skipped"``）。檢查兩個
        終端證據來源：

        1. **body evidence 位置**：``## Sources`` / ``## Evidence`` 區塊內的 wikilink，
           加上 ``source:`` 行。
        2. **frontmatter ``mentioned_in``**：concept aggregator 的 source 清單。

        任一終端證據 citation 指向 Concepts / Outputs → 一筆紅線 5 finding。
        裸 stem（``[[foo]]``，無子目錄）視為 unknown，不判違規（無法從 link 斷層級）。

        Args:
            page_path: 被檢頁的 vault-relative 路徑（決定是否 in-scope）。
            body: 頁面正文（不含 frontmatter）。
            mentioned_in: frontmatter ``mentioned_in`` wikilink 清單（選填）。

        Returns:
            ``ProvenanceReport``。``violations`` → caller 應 reject 寫入。
        """
        if not _is_concept_or_output_page(page_path):
            return ProvenanceReport(page_path=page_path, status="skipped")

        findings: list[ProvenanceFinding] = []

        for citation in extract_evidence_citations(body):
            if _classify_wikilink_target(citation) == "derived":
                findings.append(
                    ProvenanceFinding(
                        red_line=5,
                        page_path=page_path,
                        citation=citation,
                        message=(
                            f"終端證據 `[[{citation}]]` 指向 Concept/Output 層——紅線 5："
                            "Concept/Output 不得以另一個 Concept/Output 作為事實來源"
                            "（wiki 自我餵食）。終端證據只能 cite Sources/Raw/Annotations。"
                        ),
                    )
                )

        for link in mentioned_in or []:
            target = _wikilink_target(link)
            if _classify_wikilink_target(target) == "derived":
                findings.append(
                    ProvenanceFinding(
                        red_line=5,
                        page_path=page_path,
                        citation=link,
                        message=(
                            f"frontmatter mentioned_in `{link}` 指向 Concept/Output 層——"
                            "紅線 5：終端證據只能 cite Sources/Raw/Annotations。"
                        ),
                    )
                )

        status = "violations" if findings else "clean"
        return ProvenanceReport(page_path=page_path, status=status, findings=findings)


class ProvenanceViolation(Exception):
    """紅線 5 違規被攔下時 raise（caller reject 寫入）。

    攜帶 :class:`ProvenanceReport`，讓 caller / 測試可檢視逐筆 finding。
    """

    def __init__(self, report: ProvenanceReport) -> None:
        self.report = report
        citations = ", ".join(f"`{f.citation}`" for f in report.findings if f.citation)
        super().__init__(
            f"紅線 5 違規 in {report.page_path}：終端證據指向 Concept/Output（{citations}）。"
            "拒絕寫入——concept/output 不得以衍生頁作為事實來源。"
        )


def _wikilink_target(link: str) -> str:
    """從 ``[[target]]`` / ``[[target|alias]]`` / 裸字串 取 target。

    ``mentioned_in`` 條目可能已是裸 target（``Sources/foo``）或完整 wikilink
    （``[[Concepts/bar]]``）；兩種都正規化成 target 字串。
    """
    m = _WIKILINK_RE.search(link)
    if m:
        return m.group(1).strip()
    return link.strip()
