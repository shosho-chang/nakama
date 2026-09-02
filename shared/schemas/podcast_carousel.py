"""Contracts for the episode-first Podcast IG Carousel flow (ADR-063/064)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^r[0-9]{3,}$")
_PAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
#: 素材檔名。渲染時會做 `cutouts_dir / name`，所以這個值一旦能由外部指定，
#: 就必須是**單純的檔名**——不得含路徑分隔符或 `..`，否則是路徑穿越。
_CUTOUT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(?:png|PNG)$")

CAROUSEL_DISPLAY_COPY_FIELDS: dict[str, tuple[str, ...]] = {
    "cover": ("headline", "emphasis", "guest_name", "guest_title"),
    "hook": ("question", "emphasis", "bridge"),
    "point": ("headline", "emphasis", "body"),
    "quote": ("host_question", "text", "emphasis", "guest_name"),
    "cta": ("episode_topic", "emphasis"),
}
#: 素材欄位——**可以編輯，但不是打字的**。與上面的顯示文案分開兩張表，因為
#: `CAROUSEL_DISPLAY_COPY_FIELDS` 同時決定編輯器要渲染哪些文字輸入框；把選圖
#: 混進去會變成要人手打檔名。
#:
#: 修修 2026-09-02：「我希望在這個 review 的頁面上方，就有一個把 cutout 列出來的
#: 地方，讓我可以重新做選擇。因為我可能會重複選擇不同的卡，看看整個畫面的感覺。」
#: 原本選圖被刻意排除在編輯之外，換一張得繞回 agent 重寫 Copy Spec——那正是他
#: 反映的摩擦。素材本身早已在 `packaging/cutouts/` 且經過授權，換的只是用哪一張，
#: 不觸及任何逐字稿宣稱，所以走人類欄位、不進 panel。
CAROUSEL_ASSET_FIELDS: dict[str, tuple[str, ...]] = {
    "cover": ("cutout",),
    "quote": ("guest_cutout",),
}
CAROUSEL_REQUIRED_REVIEWS = ("ig_audience", "episode_editorial", "brand_evidence")
CAROUSEL_TEXT_LAYOUT_REGIONS: dict[str, tuple[str, ...]] = {
    "cover": ("headline",),
    "hook": ("question", "bridge"),
    "point": ("headline", "body"),
    "quote": ("text", "host_question"),
    "cta": ("episode_topic",),
}
CAROUSEL_TEXT_SAFE_RECTS: dict[tuple[str, str], tuple[int, int, int, int]] = {
    ("cover", "headline"): (48, 120, 1040, 760),
    ("hook", "question"): (48, 160, 1032, 820),
    ("hook", "bridge"): (48, 400, 1032, 980),
    ("point", "headline"): (48, 160, 1032, 760),
    ("point", "body"): (48, 400, 1032, 980),
    ("quote", "text"): (48, 160, 1032, 980),
    ("quote", "host_question"): (448, 180, 992, 460),
    ("cta", "episode_topic"): (48, 540, 1032, 820),
}


class CarouselModel(BaseModel):
    """Fail closed on contract drift."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TranscriptEvidence(CarouselModel):
    """Immutable source excerpt plus a reliable timeline locator."""

    evidence_id: str = Field(min_length=2, max_length=80)
    source_path: str = Field(min_length=1)
    source_sha256: str
    speaker: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1)
    t0: float = Field(ge=0)
    t1: float = Field(gt=0)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    contiguous: bool = True

    @field_validator("source_sha256")
    @classmethod
    def _valid_sha256(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("source_sha256 must be a lowercase SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def _valid_range(self) -> TranscriptEvidence:
        if self.t1 <= self.t0:
            raise ValueError("evidence t1 must be greater than t0")
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("line_start and line_end must be provided together")
        if self.line_start is not None and self.line_end < self.line_start:
            raise ValueError("line_end must be greater than or equal to line_start")
        return self


class EpisodeMetadata(CarouselModel):
    number: int = Field(gt=0)
    topic: str = Field(min_length=1)
    guest_name: str = Field(min_length=1)
    guest_title: str = Field(min_length=1)


class CoverLayoutOverride(CarouselModel):
    """Deterministic cover geometry in the 1080px render coordinate system."""

    guest_right_px: int = Field(default=-260, ge=-540, le=240)
    guest_bottom_px: int = Field(default=-130, ge=-400, le=240)
    guest_height_px: int = Field(default=900, ge=480, le=1400)
    title_font_size_px: int = Field(default=106, ge=72, le=160)


class GuestLayoutOverride(CarouselModel):
    """金句卡的來賓去背照幾何，與封面同一組欄位、同一個 1080px 座標系。

    修修 2026-09-02：「金句那邊也按照你現在建議的修法去修。」在此之前金句的
    去背照位置寫死在算圖 CSS 裡（`.quote-a .guest{right:-50px;bottom:-10px;
    height:440px}`），schema 沒有欄位、bridge 沒有綁拖曳、編輯器沒有控制項——
    所以在金句那張怎麼拖都沒反應，不是壞掉，是根本沒做。

    刻意**不給 default**：A 版與 B 版的算圖預設值不同（B 版在 `.guest-panel`
    內、right -18 / bottom -20 / height 430），寫一組 default 一定會對其中一個
    版型說謊。要嘛沒有 override、完全照算圖 CSS，要嘛三個值都由編輯器從預覽
    量到的基準值帶進來，講清楚是誰決定的。
    """

    guest_right_px: int = Field(ge=-540, le=240)
    guest_bottom_px: int = Field(ge=-400, le=240)
    guest_height_px: int = Field(ge=200, le=1000)


class TextLayoutOverrideV1(CarouselModel):
    """One text region in canonical 1080px coordinates; height remains content-driven."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    x_px: int = Field(ge=0, le=1000)
    y_px: int = Field(ge=0, le=1080)
    width_px: int = Field(ge=80, le=1080)
    font_start_px: int = Field(ge=24, le=160)
    lines: list[str] | None = Field(default=None, min_length=1, max_length=20)

    @field_validator("x_px", "y_px", "width_px")
    @classmethod
    def _four_pixel_grid(cls, value: int) -> int:
        if value % 4:
            raise ValueError("text layout coordinates and width must use the 4px grid")
        return value

    @field_validator("font_start_px")
    @classmethod
    def _two_pixel_type_step(cls, value: int) -> int:
        if value % 2:
            raise ValueError("text layout font size must use a 2px step")
        return value

    @field_validator("lines")
    @classmethod
    def _valid_manual_lines(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        if any("\r" in line or "\n" in line for line in value):
            raise ValueError("manual layout lines cannot contain CR or newline characters")
        if any(not line.strip() for line in value):
            raise ValueError("manual layout lines cannot contain blank lines")
        return value


class PageTextLayoutOverrideV1(CarouselModel):
    """A stable page/region override stored in the Copy Spec."""

    page_id: str
    role: Literal["cover", "hook", "point", "quote", "cta"]
    region: str = Field(min_length=1, max_length=40)
    values: TextLayoutOverrideV1

    @model_validator(mode="after")
    def _allowlisted_region(self) -> PageTextLayoutOverrideV1:
        if self.region not in CAROUSEL_TEXT_LAYOUT_REGIONS[self.role]:
            raise ValueError(f"text layout region is not editable for {self.role}: {self.region}")
        left, top, right, bottom = CAROUSEL_TEXT_SAFE_RECTS[(self.role, self.region)]
        if not (
            left <= self.values.x_px
            and top <= self.values.y_px <= bottom
            and self.values.x_px + self.values.width_px <= right
        ):
            raise ValueError(
                f"text layout must remain inside the safe rect for {self.role}.{self.region}"
            )
        return self


class CarouselLayoutOverridesV1(CarouselModel):
    cover: CoverLayoutOverride | None = None
    quote: GuestLayoutOverride | None = None
    text_regions: list[PageTextLayoutOverrideV1] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_text_regions(self) -> CarouselLayoutOverridesV1:
        keys = [(item.page_id, item.region) for item in self.text_regions]
        if len(keys) != len(set(keys)):
            raise ValueError("text layout page/region pairs must be unique")
        return self


class _BasePage(CarouselModel):
    page_id: str
    evidence: list[TranscriptEvidence] = Field(min_length=1)

    @field_validator("page_id")
    @classmethod
    def _valid_page_id(cls, value: str) -> str:
        if not _PAGE_ID_RE.fullmatch(value):
            raise ValueError("page_id must be stable lowercase kebab-case")
        return value

    @model_validator(mode="after")
    def _single_line_display_copy(self) -> _BasePage:
        for name in CAROUSEL_DISPLAY_COPY_FIELDS[self.role]:
            value = getattr(self, name, None)
            if value is not None and ("\r" in value or "\n" in value):
                raise ValueError(f"display copy cannot contain CR/LF: {self.role}.{name}")
        return self


def _assert_emphasis(emphasis: str, *fields: str) -> None:
    if not any(emphasis in value for value in fields):
        raise ValueError("emphasis must be an exact substring of display copy on the same page")


class CoverPage(_BasePage):
    role: Literal["cover"] = "cover"
    headline: str = Field(min_length=1)
    emphasis: str = Field(min_length=1)
    guest_name: str = Field(min_length=1)
    guest_title: str = Field(min_length=1)
    cutout: str = Field(min_length=1)

    @model_validator(mode="after")
    def _emphasis_in_copy(self) -> CoverPage:
        _assert_emphasis(self.emphasis, self.headline)
        return self


class HookPage(_BasePage):
    role: Literal["hook"] = "hook"
    question: str = Field(min_length=1)
    emphasis: str = Field(min_length=1)
    bridge: str = Field(min_length=1)

    @model_validator(mode="after")
    def _emphasis_in_copy(self) -> HookPage:
        _assert_emphasis(self.emphasis, self.question)
        return self


class PointPage(_BasePage):
    role: Literal["point"] = "point"
    headline: str = Field(min_length=1)
    emphasis: str = Field(min_length=1)
    body: str = Field(min_length=1)

    @model_validator(mode="after")
    def _emphasis_in_copy(self) -> PointPage:
        if self.emphasis not in self.headline:
            raise ValueError("point emphasis must be in headline")
        return self


class QuotePage(_BasePage):
    role: Literal["quote"] = "quote"
    variant: Literal["A", "B"]
    text: str = Field(min_length=1)
    emphasis: str = Field(min_length=1)
    guest_name: str = Field(min_length=1)
    guest_cutout: str = Field(min_length=1)
    host_question: str | None = None
    host_question_evidence: list[TranscriptEvidence] = Field(default_factory=list)
    host_cutout: str | None = None

    @model_validator(mode="after")
    def _valid_variant(self) -> QuotePage:
        _assert_emphasis(self.emphasis, self.text)
        if not all(item.contiguous for item in self.evidence):
            raise ValueError("a guest quote may only use contiguous evidence")
        if self.variant == "B":
            if not self.host_question or not self.host_question_evidence or not self.host_cutout:
                raise ValueError("variant B requires host question, evidence, and cutout")
            if not all(item.contiguous for item in self.host_question_evidence):
                raise ValueError("a host question may only use contiguous evidence")
        elif self.host_question or self.host_question_evidence or self.host_cutout:
            raise ValueError("variant A cannot contain host-question fields")
        return self


class CTAPage(_BasePage):
    role: Literal["cta"] = "cta"
    episode_topic: str = Field(min_length=1)
    emphasis: str = Field(min_length=1)
    platforms: tuple[Literal["apple_podcasts", "spotify", "youtube"], ...] = (
        "apple_podcasts",
        "spotify",
        "youtube",
    )

    @model_validator(mode="after")
    def _valid_cta(self) -> CTAPage:
        _assert_emphasis(self.emphasis, self.episode_topic)
        if self.platforms != ("apple_podcasts", "spotify", "youtube"):
            raise ValueError("Podcast Carousel v1 uses the three fixed CTA platforms")
        return self


CarouselPage = Annotated[
    Union[CoverPage, HookPage, PointPage, QuotePage, CTAPage],
    Field(discriminator="role"),
]


class PodcastCarouselCopySpecV1(CarouselModel):
    schema_name: Literal["nakama.podcast_carousel_copy_spec.v1"] = (
        "nakama.podcast_carousel_copy_spec.v1"
    )
    template_id: Literal["podcast_episode_v1"] = "podcast_episode_v1"
    episode_id: str = Field(min_length=1)
    revision: str
    episode: EpisodeMetadata
    editorial_direction_path: str | None = None
    #: 「這一版的 AI 生成內容與 rNNN 逐位元組相同，由那一版的 panel 治理。」
    #:
    #: 修修 2026-09-02 裁決：「Agent review 審的是 AI 的生成內容，人類 review 之後的
    #: 成果根本不應該再觸發這個 review。」他在 Review Gate 改一個職稱，不該觸發三個
    #: agent、六輪審查——而在契約互鎖下那張單根本完成不了（見
    #: `podcast_carousel_correction_job._assert_structured_edits_applied`）。
    #:
    #: 這個宣告**不是信任聲明**：修正單的 exact-diff 會證明結果等於來源加上人類明確
    #: 要求的欄位，其餘一字未動。既然 AI 那半邊沒變，重跑 panel 只會得到同一個答案。
    panel_inherited_from: str | None = Field(default=None, pattern=r"^r[0-9]{3,}$")
    pages: list[CarouselPage] = Field(min_length=5, max_length=20)
    publish_compatibility: Literal["api_compatible", "manual_only"]
    layout_overrides: CarouselLayoutOverridesV1 = Field(default_factory=CarouselLayoutOverridesV1)

    @field_validator("revision")
    @classmethod
    def _valid_revision(cls, value: str) -> str:
        if not _REVISION_RE.fullmatch(value):
            raise ValueError("revision must use rNNN format")
        return value

    @model_validator(mode="after")
    def _valid_sequence(self) -> PodcastCarouselCopySpecV1:
        roles = [page.role for page in self.pages]
        if roles[0:2] != ["cover", "hook"]:
            raise ValueError("page sequence must start with cover then hook")
        if roles[-2:] != ["quote", "cta"]:
            raise ValueError("page sequence must end with quote then cta")
        middle = roles[2:-2]
        if "point" not in middle:
            raise ValueError("content sequence must contain at least one point")
        if any(role != "point" for role in middle):
            raise ValueError("Podcast Carousel v1 middle pages must all be point pages")
        page_ids = [page.page_id for page in self.pages]
        if len(page_ids) != len(set(page_ids)):
            raise ValueError("page_id must be unique within a Copy Spec")
        page_by_id = {page.page_id: page for page in self.pages}
        for item in self.layout_overrides.text_regions:
            page = page_by_id.get(item.page_id)
            if page is None or page.role != item.role:
                raise ValueError(f"text layout page/role does not match Copy Spec: {item.page_id}")
            if item.region == "host_question" and not getattr(page, "host_question", None):
                raise ValueError("host_question layout requires quote variant B")
            if item.values.lines is not None:
                display_copy = getattr(page, item.region)
                if "\r" in display_copy or "\n" in display_copy:
                    raise ValueError("manual line breaks belong in layout lines, not display copy")
                if "".join(item.values.lines) != display_copy:
                    raise ValueError("manual layout lines must concatenate exactly to display copy")
                emphasis = getattr(page, "emphasis", None)
                if item.region in {"headline", "question", "text", "episode_topic"}:
                    emphasis_start = display_copy.index(emphasis)
                    emphasis_end = emphasis_start + len(emphasis)
                    cursor = 0
                    preserves_occurrence = False
                    for line in item.values.lines:
                        line_end = cursor + len(line)
                        if cursor <= emphasis_start and emphasis_end <= line_end:
                            preserves_occurrence = True
                            break
                        cursor = line_end
                    if not preserves_occurrence:
                        raise ValueError("manual layout lines cannot split emphasis across lines")
        expected = "api_compatible" if len(self.pages) <= 10 else "manual_only"
        if self.publish_compatibility != expected:
            raise ValueError(f"publish_compatibility must be {expected} for this page count")
        quote = self.pages[-2]
        expected_variant = "A" if self.episode.number % 2 else "B"
        if isinstance(quote, QuotePage) and quote.variant != expected_variant:
            # Human override and B→A evidence fallback remain possible, but must be explicit.
            # The orchestrator records either reason in ``variant_override_reason`` below.
            if not self.variant_override_reason:
                raise ValueError("non-alternating quote variant requires variant_override_reason")
        return self

    variant_override_reason: str | None = None


class ArtifactReceipt(CarouselModel):
    path: str = Field(min_length=1)
    bytes: int = Field(gt=0)
    sha256: str

    @field_validator("sha256")
    @classmethod
    def _valid_sha256(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must be a lowercase SHA-256 hex digest")
        return value


class TemplateSnapshot(CarouselModel):
    template_id: Literal["podcast_episode_v1"] = "podcast_episode_v1"
    root: str = Field(min_length=1)
    sha256: str

    @field_validator("sha256")
    @classmethod
    def _valid_sha256(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must be a lowercase SHA-256 hex digest")
        return value


class PageFitDiagnostic(CarouselModel):
    status: Literal["fit", "needs_review"]
    regions: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class CarouselReviewPage(CarouselModel):
    page_id: str
    page_number: int = Field(gt=0)
    role: Literal["cover", "hook", "point", "quote", "cta"]
    content_sha256: str
    image: ArtifactReceipt
    fit: PageFitDiagnostic
    copy_page: CarouselPage

    @model_validator(mode="after")
    def _page_matches_copy(self) -> CarouselReviewPage:
        if self.page_id != self.copy_page.page_id or self.role != self.copy_page.role:
            raise ValueError("review page identity must match embedded copy")
        if not _SHA256_RE.fullmatch(self.content_sha256):
            raise ValueError("content_sha256 must be a lowercase SHA-256 hex digest")
        return self


class CarouselReviewManifestV1(CarouselModel):
    schema_name: Literal["nakama.podcast_carousel_review_manifest.v1"] = (
        "nakama.podcast_carousel_review_manifest.v1"
    )
    episode_id: str = Field(min_length=1)
    stage: Literal[5] = 5
    revision: str
    copy_spec: ArtifactReceipt
    render_input: ArtifactReceipt | None = None
    template: TemplateSnapshot
    publish_compatibility: Literal["api_compatible", "manual_only"]
    pages: list[CarouselReviewPage] = Field(min_length=5, max_length=20)
    gate: Literal["carousel_review"] = "carousel_review"

    @model_validator(mode="after")
    def _valid_manifest(self) -> CarouselReviewManifestV1:
        if not _REVISION_RE.fullmatch(self.revision):
            raise ValueError("revision must use rNNN format")
        ids = [page.page_id for page in self.pages]
        if len(ids) != len(set(ids)):
            raise ValueError("review page IDs must be unique")
        if [page.page_number for page in self.pages] != list(range(1, len(self.pages) + 1)):
            raise ValueError("page_number must be contiguous and one-based")
        expected = "api_compatible" if len(self.pages) <= 10 else "manual_only"
        if self.publish_compatibility != expected:
            raise ValueError(f"publish_compatibility must be {expected} for this page count")
        return self


class CarouselPageDecision(CarouselModel):
    page_id: str
    status: Literal["pending", "approved", "needs_changes"] = "pending"
    feedback: str = Field(default="", max_length=1200)
    artifact_sha256: str

    @field_validator("artifact_sha256")
    @classmethod
    def _valid_sha256(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("artifact_sha256 must be a lowercase SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def _feedback_required(self) -> CarouselPageDecision:
        if self.status == "needs_changes" and not self.feedback:
            raise ValueError("needs_changes requires feedback")
        return self


class CarouselFeedbackRevision(CarouselModel):
    revision_number: int = Field(gt=0)
    created_at: datetime
    carousel_revision: str
    manifest_sha256: str
    decision: Literal["draft", "approved"]
    pages: list[CarouselPageDecision] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid_feedback_revision(self) -> CarouselFeedbackRevision:
        if not _REVISION_RE.fullmatch(self.carousel_revision):
            raise ValueError("carousel_revision must use rNNN format")
        if not _SHA256_RE.fullmatch(self.manifest_sha256):
            raise ValueError("manifest_sha256 must be a lowercase SHA-256 hex digest")
        ids = [page.page_id for page in self.pages]
        if len(ids) != len(set(ids)):
            raise ValueError("feedback page IDs must be unique")
        if self.decision == "approved" and any(page.status != "approved" for page in self.pages):
            raise ValueError("approved carousel requires every page to be approved")
        return self


class CarouselReviewFeedbackV1(CarouselModel):
    schema_name: Literal["nakama.podcast_carousel_review_feedback.v1"] = (
        "nakama.podcast_carousel_review_feedback.v1"
    )
    episode_id: str = Field(min_length=1)
    revisions: list[CarouselFeedbackRevision] = Field(default_factory=list)


class CarouselCorrectionItem(CarouselModel):
    """One non-empty, revision-bound correction requested by the reviewer."""

    page_id: str
    artifact_sha256: str
    feedback: str = Field(min_length=1, max_length=1200)

    @field_validator("page_id")
    @classmethod
    def _valid_page_id(cls, value: str) -> str:
        if not _PAGE_ID_RE.fullmatch(value):
            raise ValueError("page_id must be stable lowercase kebab-case")
        return value

    @field_validator("artifact_sha256")
    @classmethod
    def _valid_sha256(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("artifact_sha256 must be a lowercase SHA-256 hex digest")
        return value


class CarouselCopyEdit(CarouselModel):
    """One allowlisted display-copy patch bound to the rendered page artifact."""

    page_id: str
    role: Literal["cover", "hook", "point", "quote", "cta"]
    artifact_sha256: str
    fields: dict[str, str] = Field(min_length=1, max_length=5)

    @field_validator("page_id")
    @classmethod
    def _valid_page_id(cls, value: str) -> str:
        if not _PAGE_ID_RE.fullmatch(value):
            raise ValueError("page_id must be stable lowercase kebab-case")
        return value

    @field_validator("artifact_sha256")
    @classmethod
    def _valid_artifact_sha256(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("artifact_sha256 must be a lowercase SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def _allowlisted_display_fields(self) -> CarouselCopyEdit:
        asset_fields = set(CAROUSEL_ASSET_FIELDS.get(self.role, ()))
        editable = set(CAROUSEL_DISPLAY_COPY_FIELDS[self.role]) | asset_fields
        invalid = set(self.fields) - editable
        if invalid:
            raise ValueError(
                f"display-copy fields are not editable for {self.role}: {sorted(invalid)}"
            )
        for name in asset_fields & set(self.fields):
            if not _CUTOUT_RE.fullmatch(self.fields[name]):
                raise ValueError(f"{name} must be a bare cutout filename inside packaging/cutouts")
        if any(not value.strip() for value in self.fields.values()):
            raise ValueError("edited display-copy fields cannot be empty")
        if any("\r" in value or "\n" in value for value in self.fields.values()):
            raise ValueError("edited display-copy fields cannot contain CR/LF")
        return self


class CarouselCoverLayoutEdit(CarouselModel):
    """Revision-bound cover layout edit; cutout identity is deliberately absent."""

    page_id: Literal["cover"] = "cover"
    artifact_sha256: str
    values: CoverLayoutOverride

    @field_validator("artifact_sha256")
    @classmethod
    def _valid_artifact_sha256(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("artifact_sha256 must be a lowercase SHA-256 hex digest")
        return value


class CarouselTextLayoutEdit(PageTextLayoutOverrideV1):
    """Revision-bound text-region layout edit."""

    artifact_sha256: str

    @field_validator("artifact_sha256")
    @classmethod
    def _valid_artifact_sha256(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("artifact_sha256 must be a lowercase SHA-256 hex digest")
        return value


class CarouselQuoteLayoutEdit(CarouselModel):
    """Revision-bound quote guest-cutout geometry; cutout identity stays in copy edits."""

    page_id: str
    artifact_sha256: str
    values: GuestLayoutOverride

    @field_validator("page_id")
    @classmethod
    def _valid_page_id(cls, value: str) -> str:
        if not _PAGE_ID_RE.fullmatch(value):
            raise ValueError("page_id must be stable lowercase kebab-case")
        return value

    @field_validator("artifact_sha256")
    @classmethod
    def _valid_artifact_sha256(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("artifact_sha256 must be a lowercase SHA-256 hex digest")
        return value


class CarouselEditorApplyRequest(CarouselModel):
    manifest_sha256: str
    copy_edits: list[CarouselCopyEdit] = Field(default_factory=list)
    layout_overrides: CarouselCoverLayoutEdit | None = None
    quote_layout_overrides: CarouselQuoteLayoutEdit | None = None
    text_layout_overrides: list[CarouselTextLayoutEdit] = Field(default_factory=list)

    @field_validator("manifest_sha256")
    @classmethod
    def _valid_manifest_sha256(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("manifest_sha256 must be a lowercase SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def _non_empty_edit(self) -> CarouselEditorApplyRequest:
        if (
            not self.copy_edits
            and self.layout_overrides is None
            and self.quote_layout_overrides is None
            and not self.text_layout_overrides
        ):
            raise ValueError("at least one structured carousel edit is required")
        page_ids = [item.page_id for item in self.copy_edits]
        if len(page_ids) != len(set(page_ids)):
            raise ValueError("structured copy edit page IDs must be unique")
        layout_keys = [(item.page_id, item.region) for item in self.text_layout_overrides]
        if len(layout_keys) != len(set(layout_keys)):
            raise ValueError("structured text layout page/region pairs must be unique")
        return self


class CarouselCorrectionClaim(CarouselModel):
    executor: Literal["codex", "claude_code"]
    executor_id: str = Field(min_length=1, max_length=200)
    claim_token: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{7,127}$")
    claimed_at: datetime
    lease_seconds: int = Field(gt=0, le=86400)
    lease_expires_at: datetime

    @model_validator(mode="after")
    def _valid_lease(self) -> CarouselCorrectionClaim:
        if self.lease_expires_at <= self.claimed_at:
            raise ValueError("claim lease must expire after it is acquired")
        return self


class CarouselCorrectionProgress(CarouselModel):
    sequence: int = Field(gt=0)
    step: str = Field(min_length=1, max_length=120)
    progress_percent: int = Field(ge=0, le=100)
    message: str = Field(default="", max_length=1200)
    recorded_at: datetime


class CarouselReviewerReceipt(CarouselModel):
    """One independently persisted reviewer result bound to its worker identity."""

    lens: Literal["ig_audience", "episode_editorial", "brand_evidence"]
    reviewer_id: str = Field(min_length=1, max_length=200)
    review: ArtifactReceipt


class CarouselCorrectionCompletionEvidence(CarouselModel):
    """Immutable receipts required to close a correction job."""

    result_manifest: ArtifactReceipt
    panel_result: ArtifactReceipt
    reviewers: tuple[CarouselReviewerReceipt, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def _three_independent_reviewers(self) -> CarouselCorrectionCompletionEvidence:
        lenses = [item.lens for item in self.reviewers]
        if set(lenses) != set(CAROUSEL_REQUIRED_REVIEWS) or len(lenses) != len(set(lenses)):
            raise ValueError("completion requires exactly the three canonical reviewer lenses")
        reviewer_ids = [item.reviewer_id for item in self.reviewers]
        if len(reviewer_ids) != len(set(reviewer_ids)):
            raise ValueError("completion reviewer identities must be unique")
        review_paths = [item.review.path for item in self.reviewers]
        if len(review_paths) != len(set(review_paths)):
            raise ValueError("completion reviewer artifacts must be distinct")
        return self


class CarouselCorrectionJobV1(CarouselModel):
    """Platform-neutral hand-off between the Review App and a local executor."""

    schema_name: Literal["nakama.podcast_carousel_correction_job.v1"] = (
        "nakama.podcast_carousel_correction_job.v1"
    )
    job_id: str = Field(pattern=r"^cj-[0-9a-f]{32}$")
    episode_id: str = Field(min_length=1)
    source_revision: str
    source_manifest_sha256: str
    status: Literal["queued", "claimed", "in_progress", "completed", "failed"] = "queued"
    feedback_items: list[CarouselCorrectionItem] = Field(default_factory=list)
    copy_edits: list[CarouselCopyEdit] = Field(default_factory=list)
    layout_overrides: CarouselCoverLayoutEdit | None = None
    quote_layout_overrides: CarouselQuoteLayoutEdit | None = None
    text_layout_overrides: list[CarouselTextLayoutEdit] = Field(default_factory=list)
    required_reviews: tuple[
        Literal["ig_audience"],
        Literal["episode_editorial"],
        Literal["brand_evidence"],
    ] = CAROUSEL_REQUIRED_REVIEWS
    created_at: datetime
    updated_at: datetime
    claim: CarouselCorrectionClaim | None = None
    source_manifest_receipt: ArtifactReceipt | None = None
    progress: list[CarouselCorrectionProgress] = Field(default_factory=list)
    result_revision: str | None = None
    completion_evidence: CarouselCorrectionCompletionEvidence | None = None
    error: str | None = Field(default=None, min_length=1, max_length=4000)

    @field_validator("source_revision")
    @classmethod
    def _valid_source_revision(cls, value: str) -> str:
        if not _REVISION_RE.fullmatch(value):
            raise ValueError("source_revision must use rNNN format")
        return value

    @field_validator("source_manifest_sha256")
    @classmethod
    def _valid_manifest_sha256(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("source_manifest_sha256 must be a lowercase SHA-256 hex digest")
        return value

    @field_validator("result_revision")
    @classmethod
    def _valid_result_revision(cls, value: str | None) -> str | None:
        if value is not None and not _REVISION_RE.fullmatch(value):
            raise ValueError("result_revision must use rNNN format")
        return value

    @model_validator(mode="after")
    def _valid_job_state(self) -> CarouselCorrectionJobV1:
        if (
            not self.feedback_items
            and not self.copy_edits
            and self.layout_overrides is None
            and self.quote_layout_overrides is None
            and not self.text_layout_overrides
        ):
            raise ValueError("correction job requires feedback or a structured edit")
        page_ids = [item.page_id for item in self.feedback_items]
        if len(page_ids) != len(set(page_ids)):
            raise ValueError("correction feedback page IDs must be unique")
        copy_page_ids = [item.page_id for item in self.copy_edits]
        if len(copy_page_ids) != len(set(copy_page_ids)):
            raise ValueError("correction copy edit page IDs must be unique")
        text_layout_keys = [(item.page_id, item.region) for item in self.text_layout_overrides]
        if len(text_layout_keys) != len(set(text_layout_keys)):
            raise ValueError("correction text layout page/region pairs must be unique")
        if self.required_reviews != CAROUSEL_REQUIRED_REVIEWS:
            raise ValueError("all three independent carousel reviews are required")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if [item.sequence for item in self.progress] != list(range(1, len(self.progress) + 1)):
            raise ValueError("correction progress sequence must be contiguous and one-based")
        percents = [item.progress_percent for item in self.progress]
        if percents != sorted(percents):
            raise ValueError("correction progress percent cannot decrease")

        if self.status == "queued":
            if (
                self.claim
                or self.source_manifest_receipt
                or self.progress
                or self.result_revision
                or self.completion_evidence
                or self.error
            ):
                raise ValueError("queued correction job cannot contain execution state")
        elif self.status == "claimed":
            if (
                not self.claim
                or not self.source_manifest_receipt
                or self.progress
                or self.result_revision
                or self.completion_evidence
                or self.error
            ):
                raise ValueError("claimed correction job requires only claim metadata")
        elif self.status == "in_progress":
            if (
                not self.claim
                or not self.source_manifest_receipt
                or not self.progress
                or self.result_revision
                or self.completion_evidence
                or self.error
            ):
                raise ValueError("in_progress correction job requires claim and progress")
        elif self.status == "completed":
            if (
                not self.claim
                or not self.source_manifest_receipt
                or not self.progress
                or not self.result_revision
                or not self.completion_evidence
                or self.error
            ):
                raise ValueError(
                    "completed correction job requires claim, progress, result revision, "
                    "and verified completion evidence"
                )
            if int(self.result_revision[1:]) <= int(self.source_revision[1:]):
                raise ValueError("result_revision must be newer than source_revision")
        elif (
            not self.claim
            or not self.source_manifest_receipt
            or not self.error
            or self.result_revision
            or self.completion_evidence
        ):
            raise ValueError("failed correction job requires claim and error only")
        return self


def receipt_for(path: Path) -> ArtifactReceipt:
    """Build an artifact receipt without loading a large file into memory."""

    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return ArtifactReceipt(
        path=str(path.resolve()),
        bytes=path.stat().st_size,
        sha256=digest.hexdigest(),
    )
