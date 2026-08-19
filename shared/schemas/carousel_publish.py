"""Stage 6 contracts for publishing an approved Podcast Carousel."""

from __future__ import annotations

import re
from datetime import datetime, timezone
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


class CarouselPublishReleaseBundle(PublishModel):
    """Immutable execution inputs captured when the publish job is created."""

    request: ArtifactReceipt
    source_manifest: ArtifactReceipt
    created_at: datetime


class CarouselPublishTarget(PublishModel):
    platform: CarouselPublishPlatform
    strategy: CarouselPublishStrategy
    configuration_state: Literal["configured", "agent_browser_required", "manual_only"]
    required_executor_capabilities: list[CarouselPublishCapability] = Field(min_length=1)
    note: str = Field(min_length=1, max_length=500)
    eligible: bool = True
    ineligibility_reason: str | None = Field(default=None, min_length=1, max_length=500)

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
        if self.eligible and self.ineligibility_reason:
            raise ValueError("eligible publish target cannot have an ineligibility reason")
        if not self.eligible and not self.ineligibility_reason:
            raise ValueError("ineligible publish target requires a reason")
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
    idempotency_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    attempt_id: str | None = Field(default=None, pattern=r"^pa-[0-9a-f]{32}$")
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


class CarouselPublishTargetState(PublishModel):
    platform: CarouselPublishPlatform
    strategy: CarouselPublishStrategy
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["pending", "in_progress", "published", "failed"] = "pending"
    attempt_count: int = Field(default=0, ge=0)
    attempt_id: str | None = Field(default=None, pattern=r"^pa-[0-9a-f]{32}$")
    reconcile_required: bool = False
    checkpoint: CarouselPublishPlatformResult | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def _valid_target_state(self) -> CarouselPublishTargetState:
        if self.status == "pending":
            if (
                self.checkpoint is not None
                or self.attempt_id is not None
                or self.reconcile_required
            ):
                raise ValueError("pending publish target cannot have execution state")
        elif self.status == "in_progress":
            if self.attempt_count < 1 or self.checkpoint is not None:
                raise ValueError(
                    "in-progress publish target requires an attempt without checkpoint"
                )
        elif self.checkpoint is None or self.checkpoint.status != self.status:
            raise ValueError("terminal publish target state requires a matching checkpoint")
        if self.checkpoint is not None and (
            self.checkpoint.platform != self.platform or self.checkpoint.strategy != self.strategy
        ):
            raise ValueError("publish target checkpoint identity mismatch")
        if (
            self.checkpoint is not None
            and self.attempt_id is not None
            and (
                self.checkpoint.idempotency_key != self.idempotency_key
                or self.checkpoint.attempt_id != self.attempt_id
            )
        ):
            raise ValueError("publish target checkpoint attempt binding mismatch")
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
    source_publish_compatibility: Literal["api_compatible", "manual_only"] | None = None
    approval_revision_number: int = Field(gt=0)
    approved_at: datetime
    request_fingerprint: str
    campaign_anchor_at: datetime | None = None
    retry_of_job_id: str | None = Field(default=None, pattern=r"^pj-[0-9a-f]{32}$")
    caption: str = Field(min_length=1, max_length=5000)
    assets: list[CarouselPublishAsset] = Field(min_length=1, max_length=20)
    release_bundle: CarouselPublishReleaseBundle | None = None
    targets: list[CarouselPublishTarget] = Field(min_length=1)
    status: Literal["queued", "claimed", "in_progress", "completed", "failed", "superseded"] = (
        "queued"
    )
    created_at: datetime
    updated_at: datetime
    claim: CarouselPublishClaim | None = None
    progress: list[CarouselPublishProgress] = Field(default_factory=list)
    target_states: list[CarouselPublishTargetState] = Field(default_factory=list)
    results: list[CarouselPublishPlatformResult] = Field(default_factory=list)
    error: str | None = Field(default=None, min_length=1, max_length=4000)
    superseded_at: datetime | None = None
    superseded_reason: str | None = Field(default=None, min_length=1, max_length=1000)

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

    @field_validator("campaign_anchor_at")
    @classmethod
    def _valid_campaign_anchor(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("campaign_anchor_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _valid_job(self) -> CarouselPublishJobV1:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.retry_of_job_id == self.job_id:
            raise ValueError("publish retry cannot reference itself")
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
        if any(not target.eligible for target in self.targets):
            raise ValueError("publish job cannot contain ineligible targets")
        if (
            self.source_publish_compatibility is not None
            and len(self.assets) > 10
            and "youtube_community" in platforms
        ):
            raise ValueError("YouTube Community accepts at most 10 carousel images")
        if self.source_publish_compatibility == "manual_only" and any(
            target.strategy == "meta_api" for target in self.targets
        ):
            raise ValueError("manual_only carousel cannot use Meta API strategy")
        if [item.sequence for item in self.progress] != list(range(1, len(self.progress) + 1)):
            raise ValueError("publish progress sequence must be contiguous and one-based")
        percents = [item.progress_percent for item in self.progress]
        if percents != sorted(percents):
            raise ValueError("publish progress percent cannot decrease")

        result_platforms = [result.platform for result in self.results]
        if len(result_platforms) != len(set(result_platforms)):
            raise ValueError("publish results must have unique platforms")
        if result_platforms != sorted(result_platforms):
            raise ValueError("publish results must use deterministic platform order")
        if not set(result_platforms).issubset(platforms):
            raise ValueError("publish result platform must be selected")
        target_strategies = {target.platform: target.strategy for target in self.targets}
        if any(result.strategy != target_strategies[result.platform] for result in self.results):
            raise ValueError("publish result strategy must match its target")

        if self.target_states:
            state_platforms = [state.platform for state in self.target_states]
            if state_platforms != platforms:
                raise ValueError("publish target states must match ordered targets")
            if any(
                state.strategy != target_strategies[state.platform] for state in self.target_states
            ):
                raise ValueError("publish target state strategy must match its target")
            checkpoint_results = [
                state.checkpoint for state in self.target_states if state.checkpoint is not None
            ]
            if checkpoint_results != self.results:
                raise ValueError("publish results must mirror target checkpoints")
            if self.release_bundle is not None and any(
                state.status == "in_progress" and state.attempt_id is None
                for state in self.target_states
            ):
                raise ValueError("bundled in-progress publish target requires an attempt ID")

        if self.status == "queued":
            if self.claim or self.progress or self.error:
                raise ValueError("queued publish job cannot contain active execution state")
            if any(state.status in {"in_progress", "failed"} for state in self.target_states):
                raise ValueError("queued publish job can carry only pending or published targets")
        elif self.status == "claimed":
            if not self.claim or self.progress or self.error:
                raise ValueError("claimed publish job requires claim metadata")
            if any(state.status in {"in_progress", "failed"} for state in self.target_states):
                raise ValueError("claimed publish job cannot carry active or failed targets")
        elif self.status == "in_progress":
            if not self.claim or self.error:
                raise ValueError("in_progress publish job requires claim without job error")
            if not self.progress and not any(
                state.status != "pending" for state in self.target_states
            ):
                raise ValueError("in_progress publish job requires progress or target state")
        elif self.status == "completed":
            if not self.claim or not self.results or self.error:
                raise ValueError("completed publish job requires claim and results")
            if result_platforms != platforms:
                raise ValueError("completed publish job requires one ordered result per target")
        elif self.status == "failed":
            if not self.claim or not self.error:
                raise ValueError("failed publish job requires claim and job-level error")
        elif (
            self.claim
            or self.progress
            or self.error
            or not self.superseded_at
            or not self.superseded_reason
        ):
            raise ValueError("superseded publish job requires supersession metadata")
        if self.status != "superseded" and (self.superseded_at or self.superseded_reason):
            raise ValueError("non-superseded publish job cannot have supersession metadata")
        return self
