"""Independent three-lens editorial panel for Podcast Carousel Copy Specs."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.brook.podcast_carousel_copy import TranscriptIndex
from shared.schemas.podcast_carousel import PodcastCarouselCopySpecV1

_MODEL = "claude-sonnet-4-6"


class _PanelModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PanelFinding(_PanelModel):
    finding_id: str = Field(min_length=2, max_length=80)
    severity: Literal["high", "medium", "low"]
    page_id: str | None = None
    claim: str = Field(min_length=1)
    page_copy_quote: str | None = None
    evidence_ids: list[str] = Field(min_length=1)
    suggested_change: str = Field(min_length=1)


class PanelReview(_PanelModel):
    lens: Literal["ig_audience", "episode_editorial", "brand_evidence"]
    verdict: Literal["pass", "revise"]
    findings: list[PanelFinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _verdict_matches_findings(self) -> PanelReview:
        if self.verdict == "pass" and self.findings:
            raise ValueError("pass review cannot contain findings")
        if self.verdict == "revise" and not self.findings:
            raise ValueError("revise review requires findings")
        ids = [finding.finding_id for finding in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("finding IDs must be unique within one review")
        return self


class RejectedFinding(_PanelModel):
    finding_id: str
    reason: str
    #: 這條 finding 針對的是**修修在 Review Gate 指定的值**（職稱、封面用哪張照片、
    #: 他改寫的文案…）。修修 2026-09-02 裁決：「只要是我修改的就是最終的決定，
    #: 不用再問。」panel 存在的目的是抓 agent 看不到的錯（歸屬、因果、evidence
    #: 漂移），不是審查頻道主的編輯決定——lens 只有逐字稿，沒有他對受眾與品牌的判斷。
    #:
    #: 標了這個旗標的 finding **仍然完整保留在 reviews 與 verification_rejections
    #: 裡**，只是不再阻擋收斂。這是記錄，不是消音。
    editor_decision: bool = False


class PanelSynthesis(_PanelModel):
    accepted_finding_ids: list[str]
    rejected: list[RejectedFinding] = Field(default_factory=list)
    revision_instructions: list[str]
    blockers: list[str] = Field(default_factory=list)


class PanelResult(_PanelModel):
    episode_id: str = Field(min_length=1)
    revision: str = Field(pattern=r"^r[0-9]{3,}$")
    status: Literal["needs_revision", "blocked", "converged"]
    reviews: dict[str, PanelReview]
    verified_findings: list[PanelFinding]
    verification_rejections: list[RejectedFinding]
    synthesis: PanelSynthesis

    @model_validator(mode="after")
    def _valid_panel_state(self) -> PanelResult:
        expected_lenses = {"ig_audience", "episode_editorial", "brand_evidence"}
        if set(self.reviews) != expected_lenses:
            raise ValueError("panel must contain exactly the three canonical reviewer lenses")
        for lens, review in self.reviews.items():
            if review.lens != lens:
                raise ValueError(f"review lens mismatch: expected {lens}, got {review.lens}")

        unreconciled = [finding for review in self.reviews.values() for finding in review.findings]
        for finding in self.verified_findings:
            try:
                unreconciled.remove(finding)
            except ValueError as error:
                raise ValueError(
                    "panel must reconcile every reviewer finding exactly once"
                ) from error
        if Counter(finding.finding_id for finding in unreconciled) != Counter(
            finding.finding_id for finding in self.verification_rejections
        ):
            raise ValueError("panel must reconcile every reviewer finding exactly once")

        verified_ids = [finding.finding_id for finding in self.verified_findings]
        if len(verified_ids) != len(set(verified_ids)):
            raise ValueError("verified finding IDs must be unique")
        known = set(verified_ids)
        accepted = set(self.synthesis.accepted_finding_ids)
        rejected = {finding.finding_id for finding in self.synthesis.rejected}
        if accepted & rejected:
            raise ValueError("synthesis cannot both accept and reject one finding")
        if accepted | rejected != known:
            raise ValueError("synthesis must account for every verified finding exactly once")

        editor_decisions = {
            finding.finding_id for finding in self.synthesis.rejected if finding.editor_decision
        }
        brand_high = {
            finding.finding_id
            for finding in self.reviews["brand_evidence"].findings
            if finding.severity == "high" and finding.finding_id in known
        }
        # 高嚴重度的 brand finding 預設不可駁回——那道護欄擋掉的是「agent 覺得
        # 沒關係」。唯一的例外是修修自己指定的值：他的決定是最高權限，駁回理由
        # 連同 finding 全文一起留在 panel 裡可稽核。
        if not brand_high.issubset(accepted | editor_decisions):
            raise ValueError(
                "high brand/evidence findings cannot be rejected unless the rejection is "
                "marked editor_decision (the editor's own instruction)"
            )

        expected_status = "converged"
        if self.synthesis.blockers:
            expected_status = "blocked"
        elif accepted or self.synthesis.revision_instructions:
            expected_status = "needs_revision"
        if self.status != expected_status:
            raise ValueError(f"panel status must be {expected_status}")
        if accepted and not self.synthesis.revision_instructions:
            raise ValueError("accepted findings require revision instructions")
        return self


def assert_panel_renderable(
    panel: PanelResult,
    *,
    spec: PodcastCarouselCopySpecV1,
) -> None:
    """Fail closed unless a matching three-lens panel has converged."""

    if panel.episode_id != spec.episode_id:
        raise ValueError("panel episode_id does not match Copy Spec")
    # 一般情況：panel 必須是**這一版**的。例外只有一種——這一版明白宣告它的 AI
    # 生成內容與某一版逐位元組相同（`panel_inherited_from`），差異全部是人類在
    # Review Gate 指定的欄位。那個宣告由修正單的 exact-diff 背書，不是自我聲明。
    allowed = {spec.revision}
    if spec.panel_inherited_from:
        allowed.add(spec.panel_inherited_from)
    if panel.revision not in allowed:
        raise ValueError(
            "panel revision does not match Copy Spec"
            if not spec.panel_inherited_from
            else f"panel revision must be {spec.revision} or the inherited "
            f"{spec.panel_inherited_from}"
        )
    if panel.status == "blocked" or panel.synthesis.blockers:
        raise RuntimeError(f"editorial panel blockers: {panel.synthesis.blockers}")
    if panel.status != "converged":
        raise RuntimeError("editorial panel has not converged; revise and re-review before render")


_LENS_BRIEFS = {
    "ig_audience": (
        "你是 IG 受眾 reviewer。只評 Hook 是否抓人、卡片理解成本、閱讀節奏、"
        "Hook 與後續 ordered points 是否一致，以及看完是否想聽完整節目。"
        "不要替品牌或 evidence lens 投票。"
    ),
    "episode_editorial": (
        "你是 Podcast episode 編輯。檢查這份 Carousel 是否涵蓋整集最值得傳播的"
        "多個重點、Episode Highlight Arc 是否成立、是否漏掉關鍵主題。不要要求逐段摘要。"
    ),
    "brand_evidence": (
        "你是品牌與證據 reviewer。逐項檢查改寫是否改變原意、錯置說話者、"
        "創造不存在的因果、拼接不連續 quote，或讓來賓被斷章取義。"
    ),
}


def _extract_json(raw: str) -> dict:
    matches = re.findall(r"```(?:json)?\s*([\s\S]+?)```", raw)
    return json.loads((matches[-1] if matches else raw).strip())


def _page_copy_strings(spec: PodcastCarouselCopySpecV1, page_id: str) -> list[str]:
    page = next(page for page in spec.pages if page.page_id == page_id)
    payload = page.model_dump(exclude={"evidence", "host_question_evidence"})
    values: list[str] = []

    def collect(value) -> None:
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                collect(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect(child)

    collect(payload)
    return values


def _review_prompt(
    lens: str,
    *,
    spec: PodcastCarouselCopySpecV1,
    transcript: TranscriptIndex,
) -> str:
    return f"""{_LENS_BRIEFS[lens]}

三個 reviewer 互不知情。你只能提出自己 lens 發現的問題，不得猜其他 reviewer 的結論。
每個 finding 必須引用 Copy Spec 實際 page_id 與原字串，或以 page_id=null 指出整集遺漏；
並附一個以上 transcript evidence block ID。沒有可查證證據就不要報。

輸出純 JSON：
{{
  "lens": "{lens}",
  "verdict": "pass 或 revise",
  "findings": [{{
    "finding_id": "{lens}-01",
    "severity": "high|medium|low",
    "page_id": "point-x 或 null",
    "claim": "問題",
    "page_copy_quote": "Copy Spec 的原字串；整集遺漏時 null",
    "evidence_ids": ["B0001"],
    "suggested_change": "可執行修改"
  }}]
}}

## Copy Spec
{spec.model_dump_json(indent=2)}

## 完整 evidence transcript
{transcript.prompt_text()}
"""


def verify_findings(
    reviews: dict[str, PanelReview],
    *,
    spec: PodcastCarouselCopySpecV1,
    transcript: TranscriptIndex,
) -> tuple[list[PanelFinding], list[RejectedFinding]]:
    page_ids = {page.page_id for page in spec.pages}
    seen: set[str] = set()
    verified: list[PanelFinding] = []
    rejected: list[RejectedFinding] = []
    for lens, review in reviews.items():
        if review.lens != lens:
            raise ValueError(f"review lens mismatch: expected {lens}, got {review.lens}")
        for finding in review.findings:
            reason = ""
            if finding.finding_id in seen:
                reason = "duplicate finding_id across panel"
            elif finding.page_id is not None and finding.page_id not in page_ids:
                reason = "unknown page_id"
            elif any(value not in transcript.by_id for value in finding.evidence_ids):
                reason = "unknown transcript evidence"
            elif finding.page_id is not None:
                if not finding.page_copy_quote:
                    reason = "page finding requires page_copy_quote"
                elif not any(
                    finding.page_copy_quote in value
                    for value in _page_copy_strings(spec, finding.page_id)
                ):
                    reason = "page_copy_quote is not present in Copy Spec"
            if reason:
                rejected.append(RejectedFinding(finding_id=finding.finding_id, reason=reason))
            else:
                verified.append(finding)
                seen.add(finding.finding_id)
    return verified, rejected


def _synthesis_prompt(findings: list[PanelFinding]) -> str:
    return f"""你是 Podcast Carousel 主編。以下 findings 已逐項通過原文與 page 查證。
請決定如何收斂成一次修訂，不以平均分或多數決消除少數 lens。

硬規則：
- brand_evidence 的 high finding 必須 accepted。
- 同一問題可合併成一條 revision instruction，但 finding ID 仍逐一列 accepted/rejected。
- rejected 必須寫具體 editorial 理由，不能寫「不喜歡」。
- blockers 只放在無法靠現有逐字稿修正的問題。

輸出純 JSON：
{{
  "accepted_finding_ids": ["..."],
  "rejected": [{{"finding_id":"...","reason":"..."}}],
  "revision_instructions": ["..."],
  "blockers": []
}}

Verified findings:
{json.dumps([value.model_dump() for value in findings], ensure_ascii=False, indent=2)}
"""


def run_panel(
    *,
    spec: PodcastCarouselCopySpecV1,
    transcript: TranscriptIndex,
    reviewer_call: Callable[[str, str], str] | None = None,
    synthesis_call: Callable[[str], str] | None = None,
) -> PanelResult:
    """Optionally run provider-backed reviewers, verify, then synthesize.

    This explicit library API is not used by the canonical CLI. The normal
    workflow dispatches three sub-agents and persists their panel result for
    the deterministic runner to validate.
    """

    def default_reviewer(_lens: str, prompt: str) -> str:
        from shared.llm import ask_multi

        return ask_multi(
            [{"role": "user", "content": prompt}],
            system="你是獨立盲審 reviewer。只輸出指定 JSON，不得虛構引用。",
            model=_MODEL,
            max_tokens=4096,
        )

    call = reviewer_call or default_reviewer

    def one(lens: str) -> tuple[str, PanelReview]:
        raw = call(lens, _review_prompt(lens, spec=spec, transcript=transcript))
        review = PanelReview.model_validate(_extract_json(raw))
        return lens, review

    with ThreadPoolExecutor(max_workers=3) as executor:
        reviews = dict(executor.map(one, _LENS_BRIEFS))
    verified, verification_rejections = verify_findings(
        reviews,
        spec=spec,
        transcript=transcript,
    )

    if not verified:
        synthesis = PanelSynthesis(
            accepted_finding_ids=[],
            rejected=[],
            revision_instructions=[],
            blockers=[],
        )
    else:
        if synthesis_call is None:
            from shared.llm import ask_multi

            def call_synthesis(prompt: str) -> str:
                return ask_multi(
                    [{"role": "user", "content": prompt}],
                    system="你是主編。只輸出指定 JSON，所有判斷必須限於已查證 findings。",
                    model=_MODEL,
                    max_tokens=4096,
                )
        else:
            call_synthesis = synthesis_call
        synthesis = PanelSynthesis.model_validate(
            _extract_json(call_synthesis(_synthesis_prompt(verified)))
        )
        known = {finding.finding_id for finding in verified}
        accepted = set(synthesis.accepted_finding_ids)
        editorial_rejected = {finding.finding_id for finding in synthesis.rejected}
        if accepted & editorial_rejected:
            raise ValueError("synthesis cannot both accept and reject one finding")
        if accepted | editorial_rejected != known:
            raise ValueError("synthesis must account for every verified finding exactly once")
        required = {
            finding.finding_id
            for finding in reviews["brand_evidence"].findings
            if finding.severity == "high" and finding.finding_id in known
        }
        if not required.issubset(accepted):
            raise ValueError("high brand/evidence findings cannot be rejected")

    status = "converged"
    if synthesis.blockers:
        status = "blocked"
    elif synthesis.accepted_finding_ids or synthesis.revision_instructions:
        status = "needs_revision"
    return PanelResult(
        episode_id=spec.episode_id,
        revision=spec.revision,
        status=status,
        reviews=reviews,
        verified_findings=verified,
        verification_rejections=verification_rejections,
        synthesis=synthesis,
    )
