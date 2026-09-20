"""One fail-closed entry point for every accepted Source Program route.

The binding deliberately carries local paths only as transport.  Logical
identity comes from canonical receipt bytes.  Verification reopens the exact
output and, for a published export, freshly replays the complete decode before
granting the path that may be handed to Auphonic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from shared.schemas.podcast_subtitles_v2 import ArtifactDigest

from .published_source_program import (
    PublishedSourceProgramIntegrityError,
)
from .published_source_program import (
    canonical_receipt_bytes as published_receipt_bytes,
)
from .published_source_program import (
    load_receipt_bytes as load_published_receipt,
)
from .published_source_program import (
    verify_and_replay as verify_published_receipt,
)
from .source_program import (
    SourceProgramIntegrityError,
    SourceProgramReceiptV1,
    source_program_receipt_bytes,
    verify_source_program_receipt,
)

_CAPABILITY_SEAL = object()

SourceProgramKind = Literal[
    "resolve_direct_lossless_render",
    "published_export_audio_decode",
]
SourceProgramQuality = Literal[
    "lossless_timeline_render",
    "lossy_published_export_decoded_to_pcm",
]


class SourceProgramBindingError(ValueError):
    """Receipt bytes or their exact transport paths cannot be verified."""


@dataclass(frozen=True, slots=True)
class SourceProgramBinding:
    """Untrusted transport values required to replay one Source Program receipt."""

    kind: SourceProgramKind
    receipt_bytes: bytes
    output_path: Path
    published_source_path: Path | None = None
    ffmpeg_executable: Path | None = None
    ffprobe_executable: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.receipt_bytes, bytes) or not self.receipt_bytes:
            raise ValueError("Source Program receipt_bytes must be non-empty immutable bytes")
        object.__setattr__(self, "output_path", Path(self.output_path))
        optional = (
            self.published_source_path,
            self.ffmpeg_executable,
            self.ffprobe_executable,
        )
        if self.kind == "resolve_direct_lossless_render":
            if any(value is not None for value in optional):
                raise ValueError(
                    "direct Source Program must not carry published-export transports"
                )
        elif self.kind == "published_export_audio_decode":
            if any(value is None for value in optional):
                raise ValueError(
                    "published Source Program requires source, ffmpeg, and ffprobe paths"
                )
            object.__setattr__(
                self, "published_source_path", Path(self.published_source_path)  # type: ignore[arg-type]
            )
            object.__setattr__(
                self, "ffmpeg_executable", Path(self.ffmpeg_executable)  # type: ignore[arg-type]
            )
            object.__setattr__(
                self, "ffprobe_executable", Path(self.ffprobe_executable)  # type: ignore[arg-type]
            )
        else:  # pragma: no cover - Literal plus runtime construction guard
            raise ValueError("unknown Source Program kind")


@dataclass(frozen=True, slots=True)
class VerifiedSourceProgramBinding:
    """Capability returned only after receipt and current bytes replay successfully."""

    kind: SourceProgramKind
    source_quality: SourceProgramQuality
    receipt_bytes: bytes
    receipt_hash: str
    output_path: Path
    output: ArtifactDigest
    sample_frames: int
    duration_ms: int
    binding: SourceProgramBinding
    _seal: object | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if len(self.receipt_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.receipt_hash
        ):
            raise ValueError("Source Program receipt hash must be lowercase SHA-256")
        if self.sample_frames <= 0 or self.duration_ms <= 0:
            raise ValueError("Source Program clock must be positive")
        object.__setattr__(self, "output_path", Path(self.output_path).resolve())


def _seal_capability(capability: VerifiedSourceProgramBinding) -> VerifiedSourceProgramBinding:
    object.__setattr__(capability, "_seal", _CAPABILITY_SEAL)
    return capability


def verify_source_program_binding(
    binding: SourceProgramBinding,
    *,
    _runner: Any = None,
) -> VerifiedSourceProgramBinding:
    """Freshly verify one direct or published Source Program transport."""

    if not isinstance(binding, SourceProgramBinding):
        raise SourceProgramBindingError("Source Program binding has the wrong type")
    if binding.kind == "resolve_direct_lossless_render":
        try:
            verified = verify_source_program_receipt(
                receipt=binding.receipt_bytes,
                output_path=binding.output_path,
            )
            # ``verify_source_program_receipt`` proves canonical input bytes.
            canonical = source_program_receipt_bytes(
                SourceProgramReceiptV1.model_validate_json(binding.receipt_bytes)
            )
        except (SourceProgramIntegrityError, ValueError, TypeError) as exc:
            raise SourceProgramBindingError(
                f"direct Source Program verification failed: {exc}"
            ) from exc
        if canonical != binding.receipt_bytes:
            raise SourceProgramBindingError("direct Source Program receipt is not canonical")
        return _seal_capability(VerifiedSourceProgramBinding(
            kind=binding.kind,
            source_quality="lossless_timeline_render",
            receipt_bytes=canonical,
            receipt_hash=verified.receipt_hash,
            output_path=verified.path,
            output=verified.source,
            sample_frames=verified.sample_frames,
            duration_ms=verified.duration_ms,
            binding=binding,
        ))

    assert binding.published_source_path is not None
    assert binding.ffmpeg_executable is not None
    assert binding.ffprobe_executable is not None
    try:
        receipt = load_published_receipt(binding.receipt_bytes)
        replay_arguments = {
            "source_path": binding.published_source_path,
            "output_path": binding.output_path,
            "ffmpeg_executable": binding.ffmpeg_executable,
            "ffprobe_executable": binding.ffprobe_executable,
        }
        if _runner is not None:
            replay_arguments["_runner"] = _runner
        verified_receipt = verify_published_receipt(receipt, **replay_arguments)
        canonical = published_receipt_bytes(verified_receipt)
    except (PublishedSourceProgramIntegrityError, ValueError, TypeError) as exc:
        raise SourceProgramBindingError(
            f"published Source Program verification failed: {exc}"
        ) from exc
    if canonical != binding.receipt_bytes:
        raise SourceProgramBindingError("published Source Program receipt is not canonical")
    sample_frames = verified_receipt.output_facts.sample_frames
    return _seal_capability(VerifiedSourceProgramBinding(
        kind=binding.kind,
        source_quality="lossy_published_export_decoded_to_pcm",
        receipt_bytes=canonical,
        receipt_hash=verified_receipt.content_hash,
        output_path=binding.output_path,
        output=verified_receipt.output,
        sample_frames=sample_frames,
        duration_ms=(sample_frames * 1_000 + 24_000) // 48_000,
        binding=binding,
    ))


def reverify_source_program_capability(
    capability: VerifiedSourceProgramBinding,
) -> VerifiedSourceProgramBinding:
    """Recheck a same-process capability without repeating a published decode.

    A fresh process cannot reconstruct the private seal and must call
    :func:`verify_source_program_binding`, which performs the complete replay.
    The cheap same-process check still rehashes/probes current output bytes.
    """

    if (
        not isinstance(capability, VerifiedSourceProgramBinding)
        or capability._seal is not _CAPABILITY_SEAL
    ):
        raise SourceProgramBindingError(
            "Source Program capability was not granted by a fresh receipt verification"
        )
    binding = capability.binding
    if binding.kind != capability.kind or binding.output_path.resolve() != capability.output_path:
        raise SourceProgramBindingError("Source Program capability transport identity drifted")
    if binding.kind == "resolve_direct_lossless_render":
        try:
            verified = verify_source_program_receipt(
                receipt=binding.receipt_bytes,
                output_path=binding.output_path,
            )
        except (SourceProgramIntegrityError, ValueError, TypeError) as exc:
            raise SourceProgramBindingError(
                f"direct Source Program capability replay failed: {exc}"
            ) from exc
        if (
            verified.receipt_hash != capability.receipt_hash
            or verified.source != capability.output
            or verified.sample_frames != capability.sample_frames
            or verified.duration_ms != capability.duration_ms
        ):
            raise SourceProgramBindingError("direct Source Program capability identity drifted")
        return capability

    # Published verification already performed the full decode/replay before
    # the capability was granted.  In the same process, rehash every bound
    # materialized file and reparse canonical receipt bytes; no paid boundary
    # can be crossed on stale/replaced content.
    try:
        receipt = load_published_receipt(binding.receipt_bytes)
        from .hashing import measure_regular_file

        checks = (
            (binding.output_path, receipt.output, "decoded output"),
            (binding.published_source_path, receipt.request.source, "published source"),
            (binding.ffmpeg_executable, receipt.request.ffmpeg.artifact, "ffmpeg executable"),
            (binding.ffprobe_executable, receipt.request.ffprobe.artifact, "ffprobe executable"),
        )
        for path, expected, label in checks:
            if path is None:
                raise SourceProgramBindingError(f"published Source Program lacks {label} path")
            digest, size_bytes = measure_regular_file(path)
            if digest != expected.sha256 or size_bytes != expected.size_bytes:
                raise SourceProgramBindingError(
                    f"published Source Program {label} content drifted"
                )
    except SourceProgramBindingError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise SourceProgramBindingError(
            f"published Source Program capability replay failed: {exc}"
        ) from exc
    if (
        receipt.content_hash != capability.receipt_hash
        or receipt.output != capability.output
        or receipt.output_facts.sample_frames != capability.sample_frames
    ):
        raise SourceProgramBindingError("published Source Program capability identity drifted")
    return capability


__all__ = [
    "SourceProgramBinding",
    "SourceProgramBindingError",
    "SourceProgramKind",
    "SourceProgramQuality",
    "VerifiedSourceProgramBinding",
    "reverify_source_program_capability",
    "verify_source_program_binding",
]
