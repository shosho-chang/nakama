"""Stage 6 contracts for publishing an approved Podcast Carousel."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.schemas.podcast_carousel import ArtifactReceipt

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^r[0-9]{3,}$")

CarouselPublishPlatform = Literal["instagram", "facebook_page", "youtube_community"]
CarouselPublishStrategy = Literal["meta_api", "agent_browser", "agent_browser_manual"]
CarouselPublishCapability = Literal["meta_api", "browser_session"]


class PublishModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CarouselPublishAsset(PublishModel):
    page_id: str = Field(min_length=2, max_length=64)
    page_number: int = Field(gt=0)
    image: ArtifactReceipt


class CarouselPublishTarget(PublishModel):
    platform: CarouselPublishPlatform
    strategy: CarouselPublishStrategy
    configuration_state: Literal["configured", "agent_browser_required", "manual_only"]
    required_executor_capabilities: list[CarouselPublishCapability] = Field(min_length=1)
    note: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _strategy_matches_capability(self) -> CarouselPublishTarget:
        expected = {
            "meta_api": ("configured", {"meta_api"}),
            "agent_browser": ("agent_browser_required", {"browser_session"}),
            "agent_browser_manual": ("manual_only", {"browser_session"}),
        }
        state, capabilities = expected[self.strategy]
        if self.configuration_state != state:
            raise ValueError("publish target configuration state does not match strategy")
        if set(self.required_executor_capabilities) != capabilities:
            raise ValueError("publish target capabilities do not match strategy")
        if self.platform == "youtube_community" and self.strategy != "agent_browser_manual":
            raise ValueError("YouTube Community has no supported publish API")
        return self


class CarouselPublishClaim(PublishModel):
    executor: Literal["codex", "claude_code"]
    executor_id: str = Field(min_length=1, max_length=200)
    executor_capabilities: list[CarouselPublishCapability] = Field(min_length=1)
    claim_token: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{7,127}$")
    claimed_at: datetime
    lease_seconds: int = Field(gt=0, le=86400)
    lease_expires_at: datetime

    @model_validator(mode="after")
    def _valid_claim(self) -> CarouselPublishClaim:
        if len(self.executor_capabilities) != len(set(self.executor_capabilities)):
            raise ValueError("executor capabilities must be unique")
        if self.lease_expires_at <= self.claimed_at:
            raise ValueError("publish claim lease must expire after it is acquired")
        return self


class CarouselPublishProgress(PublishModel):
    sequence: int = Field(gt=0)
    step: str = Field(min_length=1, max_length=120)
    progress_percent: int = Field(ge=0, le=100)
    message: str = Field(default="", max_length=1200)
    recorded_at: datetime


class CarouselPublishPlatformResult(PublishModel):
    platform: CarouselPublishPlatform
    strategy: CarouselPublishStrategy
    status: Literal["published", "failed"]
    receipt_id: str | None = Field(default=None, min_length=1, max_length=500)
    permalink: str | None = Field(default=None, min_length=1, max_length=2000)
    error: str | None = Field(default=None, min_length=1, max_length=4000)
    completed_at: datetime

    @field_validator("permalink")
    @classmethod
    def _valid_permalink(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("publish permalink must be an HTTP(S) URL")
        return value

    @model_validator(mode="after")
    def _valid_result(self) -> CarouselPublishPlatformResult:
        if self.status == "published":
            if not (self.receipt_id or self.permalink) or self.error:
                raise ValueError("published platform result requires a receipt or permalink")
        elif not self.error or self.receipt_id or self.permalink:
            raise ValueError("failed platform result requires only an error")
        return self


class CarouselPublishJobV1(PublishModel):
    """Agent-neutral job created only after the Stage 5 approval gate closes."""

    schema_name: Literal["nakama.podcast_carousel_publish_job.v1"] = (
        "nakama.podcast_carousel_publish_job.v1"
    )
    job_id: str = Field(pattern=r"^pj-[0-9a-f]{32}$")
    episode_id: str = Field(min_length=1)
    source_revision: str
    source_manifest_sha256: str
    approval_revision_number: int = Field(gt=0)
    approved_at: datetime
    request_fingerprint: str
    caption: str = Field(min_length=1, max_length=5000)
    assets: list[CarouselPublishAsset] = Field(min_length=1, max_length=20)
    targets: list[CarouselPublishTarget] = Field(min_length=1)
    status: Literal["queued", "claimed", "in_progress", "completed", "failed"] = "queued"
    created_at: datetime
    updated_at: datetime
    claim: CarouselPublishClaim | None = None
    progress: list[CarouselPublishProgress] = Field(default_factory=list)
    results: list[CarouselPublishPlatformResult] = Field(default_factory=list)
    error: str | None = Field(default=None, min_length=1, max_length=4000)

    @field_validator("source_revision")
    @classmethod
    def _valid_revision(cls, value: str) -> str:
        if not _REVISION_RE.fullmatch(value):
            raise ValueError("source_revision must use rNNN format")
        return value

    @field_validator("source_manifest_sha256", "request_fingerprint")
    @classmethod
    def _valid_sha256(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("publish hash must be a lowercase SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def _valid_job(self) -> CarouselPublishJobV1:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if [asset.page_number for asset in self.assets] != list(range(1, len(self.assets) + 1)):
            raise ValueError("publish assets must be contiguous and one-based")
        page_ids = [asset.page_id for asset in self.assets]
        if len(page_ids) != len(set(page_ids)):
            raise ValueError("publish asset page IDs must be unique")
        platforms = [target.platform for target in self.targets]
        if len(platforms) != len(set(platforms)):
            raise ValueError("publish target platforms must be unique")
        if platforms != sorted(platforms):
            raise ValueError("publish targets must use deterministic platform order")
        if [item.sequence for item in self.progress] != list(range(1, len(self.progress) + 1)):
            raise ValueError("publish progress sequence must be contiguous and one-based")
        percents = [item.progress_percent for item in self.progress]
        if percents != sorted(percents):
            raise ValueError("publish progress percent cannot decrease")

        if self.status == "queued":
            if self.claim or self.progress or self.results or self.error:
                raise ValueError("queued publish job cannot contain execution state")
        elif self.status == "claimed":
            if not self.claim or self.progress or self.results or self.error:
                raise ValueError("claimed publish job requires only claim metadata")
        elif self.status == "in_progress":
            if not self.claim or not self.progress or self.results or self.error:
                raise ValueError("in_progress publish job requires claim and progress")
        elif self.status == "completed":
            if not self.claim or not self.progress or not self.results or self.error:
                raise ValueError("completed publish job requires claim, progress, and results")
            result_platforms = [result.platform for result in self.results]
            if result_platforms != platforms:
                raise ValueError("completed publish job requires one ordered result per target")
            target_strategies = {target.platform: target.strategy for target in self.targets}
            if any(
                result.strategy != target_strategies[result.platform] for result in self.results
            ):
                raise ValueError("publish result strategy must match its target")
        elif not self.claim or not self.error or self.results:
            raise ValueError("failed publish job requires claim and job-level error")
        return self
