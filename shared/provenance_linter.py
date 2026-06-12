"""Provenance linter — Centaur 規格 v0.2 §7 紅線 2 & 5 的治理 scaffolding（N520）.

本 module 在 N520 階段是**有意的 placeholder**：它把紅線 2 / 5 的契約寫成
可被 import、可被測試的 surface，但實際 enforcement 延到 N524（route C ingest
接線）才實作。Codex + Gemini panel 一致指出：N520 task prompt 宣稱地基「讓後續
任何 task 都踩不過紅線」，但若只 tripwire 機械路徑（紅線 1），紅線 2/5 會無聲落空。
這個 stub 讓那份承諾「concrete 且 testable」——後續 task 有明確掛載點，不會各自
重新發明 provenance 檢查。

紅線（v0.2 §7）：
  2. 每個事實宣稱附 citation，溯源回 ``KB/Raw/`` 或 ``KB/Annotations/`` 錨點。
  5. Concept / Output 的終端證據只能是 Sources / Raw / Annotations，**不得**以
     另一個 Concept / Output 作為事實來源（防 citation laundering / wiki 自我餵食）。

N524 將實作 :meth:`ProvenanceLinter.lint_page` 的真檢查；N520 只凍結介面 +
``status="deferred"`` 的回傳，讓 caller 與測試可以先接線。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 紅線 2/5 認可的「終端證據」來源層（事實的根，不可再往上指 Concept/Output）。
TERMINAL_EVIDENCE_PREFIXES = ("KB/Raw/", "KB/Annotations/", "KB/Wiki/Sources/")

#: 不可作為終端證據的「衍生層」（紅線 5：禁止 Concept→Concept / Output 自我餵食）。
DERIVED_LAYER_PREFIXES = ("KB/Wiki/Concepts/", "KB/Wiki/Outputs/")

#: 本 linter 覆蓋的紅線編號（對照 v0.2 §7）。
ENFORCED_RED_LINES = (2, 5)


@dataclass(frozen=True)
class ProvenanceFinding:
    """單筆 provenance 違規（或 deferred placeholder）。"""

    red_line: int  # 2 或 5
    page_path: str
    message: str
    citation: str | None = None


@dataclass
class ProvenanceReport:
    """一次 lint 的結果。``status="deferred"`` 代表 N520 placeholder 尚未真檢查。"""

    page_path: str
    status: str  # "clean" | "violations" | "deferred"
    findings: list[ProvenanceFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """clean 或 deferred 都算「未擋下」；只有 violations 是 fail。"""
        return self.status != "violations"


def is_terminal_evidence(citation_path: str) -> bool:
    """``citation_path`` 是否為紅線 2/5 認可的終端證據層（Raw/Annotations/Sources）。

    此純函式在 N520 即可用且已測試——它是紅線 5 判定的原子操作，N524 的
    :meth:`ProvenanceLinter.lint_page` 會以它為基礎。
    """
    norm = citation_path.replace("\\", "/")
    if norm.startswith(DERIVED_LAYER_PREFIXES):
        return False
    return norm.startswith(TERMINAL_EVIDENCE_PREFIXES)


class ProvenanceLinter:
    """紅線 2/5 的檢查掛載點。N520 = scaffolding；真 enforcement 在 N524。"""

    def lint_page(self, page_path: str, body: str) -> ProvenanceReport:
        """檢查單頁的 citation provenance（紅線 2/5）。

        N520 階段回傳 ``status="deferred"``——介面凍結、caller 可接線，但實際
        逐 claim 的 citation 抽取與 laundering 偵測延到 N524 實作。**不要**在
        N520 假裝有檢查（會給出假的安全感）；deferred 是誠實狀態。

        Returns:
            ProvenanceReport(status="deferred")，附一筆說明 finding。
        """
        return ProvenanceReport(
            page_path=page_path,
            status="deferred",
            findings=[
                ProvenanceFinding(
                    red_line=5,
                    page_path=page_path,
                    message=(
                        "provenance enforcement deferred to N524 (route C ingest); "
                        "N520 只凍結介面。is_terminal_evidence() 已可用。"
                    ),
                )
            ],
        )
