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


class _BasePage(CarouselModel):
    page_id: str
    evidence: list[TranscriptEvidence] = Field(min_length=1)

    @field_validator("page_id")
    @classmethod
    def _valid_page_id(cls, value: str) -> str:
        if not _PAGE_ID_RE.fullmatch(value):
            raise ValueError("page_id must be stable lowercase kebab-case")
        return value


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
    pages: list[CarouselPage] = Field(min_length=5, max_length=20)
    publish_compatibility: Literal["api_compatible", "manual_only"]

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
    feedback_items: list[CarouselCorrectionItem] = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    claim: CarouselCorrectionClaim | None = None
    progress: list[CarouselCorrectionProgress] = Field(default_factory=list)
    result_revision: str | None = None
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
        page_ids = [item.page_id for item in self.feedback_items]
        if len(page_ids) != len(set(page_ids)):
            raise ValueError("correction feedback page IDs must be unique")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if [item.sequence for item in self.progress] != list(range(1, len(self.progress) + 1)):
            raise ValueError("correction progress sequence must be contiguous and one-based")
        percents = [item.progress_percent for item in self.progress]
        if percents != sorted(percents):
            raise ValueError("correction progress percent cannot decrease")

        if self.status == "queued":
            if self.claim or self.progress or self.result_revision or self.error:
                raise ValueError("queued correction job cannot contain execution state")
        elif self.status == "claimed":
            if not self.claim or self.progress or self.result_revision or self.error:
                raise ValueError("claimed correction job requires only claim metadata")
        elif self.status == "in_progress":
            if not self.claim or not self.progress or self.result_revision or self.error:
                raise ValueError("in_progress correction job requires claim and progress")
        elif self.status == "completed":
            if not self.claim or not self.progress or not self.result_revision or self.error:
                raise ValueError(
                    "completed correction job requires claim, progress, and result revision"
                )
            if int(self.result_revision[1:]) <= int(self.source_revision[1:]):
                raise ValueError("result_revision must be newer than source_revision")
        elif not self.claim or not self.error or self.result_revision:
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
