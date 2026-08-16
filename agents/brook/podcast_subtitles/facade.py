"""Public operator vocabulary for the Memo-first Subtitle V2 delivery path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from shared.schemas.podcast_subtitles_v2 import CorrectionDecision

from .errors import GenerationNotFoundError
from .hashing import hash_object
from .module import (
    CreateRequest,
    GenerationOutcome,
    PodcastSubtitleV2,
    ProjectOutcome,
    ProjectRequest,
    ResolveRequest,
)
from .native_resolution import ResolveNativeRequest


@dataclass(frozen=True, slots=True)
class StatusView:
    state: Literal[
        "not_started",
        "partial",
        "complete_pending_activation",
        "complete",
        "revised",
        "active_with_partial_create",
        "active_with_unactivated_complete",
    ]
    active_generation_id: str | None = None
    transcript_status: str | None = None
    revision: int | None = None
    unresolved_issue_count: int | None = None
    content_hash: str | None = None
    reference_source_ids: tuple[str, ...] = ()
    reference_enrollment_hash: str | None = None
    reference_retriever_config_hash: str | None = None
    reference_manifest_sha256s: tuple[str, ...] = ()
    checkpoint_stage: str | None = None
    checkpoint_id: str | None = None
    operation_key: str | None = None
    checkpoint_generation_id: str | None = None
    expected_active_generation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewView:
    generation_id: str
    issues: tuple[dict[str, Any], ...]


class PodcastSubtitleFacade:
    """Delivery verbs over one :class:`PodcastSubtitleV2` instance."""

    def __init__(self, module: PodcastSubtitleV2) -> None:
        self.module = module

    def run(self, request: CreateRequest) -> GenerationOutcome:
        return self.module.create(request)

    def status(self) -> StatusView:
        checkpoint = self.module.store.load_latest_create_checkpoint()
        try:
            generation_id = self.module.store.active_generation_id()
        except GenerationNotFoundError:
            if checkpoint is None:
                return StatusView(state="not_started")
            item = checkpoint.checkpoint
            if item.stage == "complete":
                self.module._load_terminal_create_checkpoint(
                    checkpoint,
                    require_active=False,
                )
            return StatusView(
                state=("complete_pending_activation" if item.stage == "complete" else "partial"),
                checkpoint_stage=item.stage,
                checkpoint_id=item.id,
                operation_key=item.operation_key,
                checkpoint_generation_id=item.generation_id,
                expected_active_generation_id=item.expected_active_generation_id,
            )
        if (
            checkpoint is not None
            and checkpoint.checkpoint.stage == "complete"
            and checkpoint.checkpoint.generation_id == generation_id
        ):
            result, enrolled_artifacts = self.module._load_terminal_create_checkpoint(
                checkpoint,
                require_active=True,
            )
        else:
            if checkpoint is not None and checkpoint.checkpoint.stage == "complete":
                self.module._load_terminal_create_checkpoint(
                    checkpoint,
                    require_active=False,
                )
            loaded = self.module._load_generation(generation_id, require_active=True)
            result = loaded.result
            enrolled_artifacts = loaded.references.enrollments
        transcript = result.transcript
        if checkpoint is None:
            status_state = "complete"
        elif (
            checkpoint.checkpoint.stage == "complete"
            and checkpoint.checkpoint.generation_id == generation_id
        ):
            status_state = "complete"
        elif checkpoint.checkpoint.stage == "complete":
            checkpoint_generation_id = checkpoint.checkpoint.generation_id
            status_state = (
                "revised"
                if checkpoint_generation_id is not None
                and self.module._generation_descends_from(
                    transcript,
                    ancestor_generation_id=checkpoint_generation_id,
                )
                else "active_with_unactivated_complete"
            )
        else:
            status_state = "active_with_partial_create"
        return StatusView(
            state=status_state,
            active_generation_id=generation_id,
            transcript_status=transcript.status,
            revision=transcript.revision,
            unresolved_issue_count=sum(
                issue.status == "unresolved" for issue in transcript.review_issues
            ),
            content_hash=transcript.content_hash,
            reference_source_ids=tuple(item.source_id for item in enrolled_artifacts),
            reference_enrollment_hash=hash_object(enrolled_artifacts),
            reference_retriever_config_hash=(
                self.module._reference_retriever_identity.config_hash
                if self.module._reference_retriever_identity is not None
                else None
            ),
            reference_manifest_sha256s=tuple(
                sorted(
                    {
                        item.enrollment_manifest_sha256
                        for item in enrolled_artifacts
                        if item.enrollment_manifest_sha256 is not None
                    }
                )
            ),
            checkpoint_stage=(checkpoint.checkpoint.stage if checkpoint is not None else None),
            checkpoint_id=(checkpoint.checkpoint.id if checkpoint is not None else None),
            operation_key=(checkpoint.checkpoint.operation_key if checkpoint is not None else None),
            checkpoint_generation_id=(
                checkpoint.checkpoint.generation_id if checkpoint is not None else None
            ),
            expected_active_generation_id=(
                checkpoint.checkpoint.expected_active_generation_id
                if checkpoint is not None
                else None
            ),
        )

    def review(self, generation_id: str | None = None) -> ReviewView:
        selected = generation_id or self.module.store.active_generation_id()
        result = self.module._load_generation(selected, require_active=generation_id is None).result
        issues = tuple(
            issue.model_dump(mode="json")
            for issue in result.transcript.review_issues
            if issue.status == "unresolved"
        )
        return ReviewView(generation_id=selected, issues=issues)

    def decide(
        self,
        generation_id: str,
        decision: CorrectionDecision,
    ) -> GenerationOutcome:
        return self.module.resolve(
            ResolveRequest(generation_id=generation_id, decisions=(decision,))
        )

    def decide_native(self, request: ResolveNativeRequest) -> GenerationOutcome:
        return self.module.resolve_native(request)

    def project(self, request: ProjectRequest) -> ProjectOutcome:
        return self.module.project(request)


__all__ = ["PodcastSubtitleFacade", "ReviewView", "StatusView"]
