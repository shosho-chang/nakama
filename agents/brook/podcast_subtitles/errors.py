"""Fail-closed errors for the Podcast Subtitle V2 deterministic core."""

from __future__ import annotations

from typing import Any


class SubtitleV2Error(Exception):
    """Base class for deterministic Subtitle V2 failures."""


class IntegrityError(SubtitleV2Error):
    """Stored or projected content does not match its declared identity."""


class ArtifactHashMismatchError(IntegrityError):
    """An artifact's bytes do not match the hash recorded in its manifest."""


class LedgerIntegrityError(IntegrityError):
    """The correction ledger is truncated, malformed, or has a broken hash chain."""


class LedgerConflictError(SubtitleV2Error):
    """A decision ID was reused with different content."""


class StaleFingerprintError(LedgerConflictError):
    """A decision targets text that no longer has the reviewed fingerprint."""


class GenerationNotFoundError(SubtitleV2Error, FileNotFoundError):
    """The requested immutable generation does not exist."""


class GenerationConflictError(SubtitleV2Error, FileExistsError):
    """A generation ID already exists with different content."""


class GenerationIsolationError(IntegrityError):
    """An artifact or active pointer belongs to another generation."""


class ResolutionTransactionError(IntegrityError):
    """A durable correction transaction is incomplete or inconsistent."""


class NativeAcceptanceRequiredError(SubtitleV2Error):
    """Legacy resolution cannot consume a native Full Audit Generation."""


class ProjectionUnsatisfiableError(SubtitleV2Error):
    """No cue partition can satisfy every hard projection constraint."""


class NeedsAlignmentError(ProjectionUnsatisfiableError):
    """A coarse corrected AudioSpan must be evidence-aligned before projection."""


class QualityGateError(IntegrityError):
    """A display projection failed at least one blocking quality gate."""

    def __init__(self, report: Any) -> None:
        self.report = report
        issues = getattr(report, "findings", getattr(report, "issues", ()))
        codes = [str(getattr(issue, "code", issue)) for issue in issues]
        detail = ", ".join(codes) if codes else "unknown gate failure"
        super().__init__(f"projection blocked by quality gates: {detail}")


__all__ = [
    "ArtifactHashMismatchError",
    "GenerationConflictError",
    "GenerationIsolationError",
    "GenerationNotFoundError",
    "IntegrityError",
    "LedgerConflictError",
    "LedgerIntegrityError",
    "NeedsAlignmentError",
    "NativeAcceptanceRequiredError",
    "ProjectionUnsatisfiableError",
    "QualityGateError",
    "ResolutionTransactionError",
    "StaleFingerprintError",
    "SubtitleV2Error",
]
