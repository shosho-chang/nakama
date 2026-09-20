"""Candidate-free temporal sampling for transcript-quality human annotation.

The builder operates only on one exact normalized PCM WAV.  It never imports
recognition, correction, reference, QC, or candidate modules.  A completed
workspace is immutable and exactly replayed from its private selection secret;
partial, extra, conflicting, or tampered state is rejected rather than repaired.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import io
import math
import os
import platform
import secrets
import stat
import sys
import tempfile
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal, TypeAlias

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes
from .transcript_gold import (
    AudioClipBinding,
    TranscriptAnnotationPacket,
    TranscriptAnnotationProtocol,
    load_annotation_packet,
)

SELECTION_ALGORITHM_ID = "hmac-sha256-temporal-strata-v1"
TEMPORAL_STRATA_COUNT = 20
SELECTION_POLICY_V1 = "temporal-stratified-v1"
SELECTION_POLICY_V2 = "silence-aligned-v2"
SELECTION_ALGORITHM_ID_V2 = "hmac-sha256-silence-aligned-temporal-strata-v2"
ENERGY_GRID_DURATION_MS_V2 = 10
BOUNDARY_WINDOW_DURATION_MS_V2 = 250
BOUNDARY_RMS_MAX_PPM_V2 = 18_000
INTERIOR_ACTIVE_RMS_MIN_PPM_V2 = 10_000
INTERIOR_ACTIVE_RATIO_MIN_PPM_V2 = 200_000
_PPM_SCALE = 1_000_000
_DECLARATION_NAME = "sampling-declaration.v1.json"
_SECRET_NAME = "selection-secret.v1.json"
_PACKET_NAME = "transcript-annotation-packet.v1.json"
_SUMMARY_NAME = "summary.v1.json"
_CLIP_DIRECTORY = "clips"
_DECLARATION_NAME_V2 = "sampling-declaration.v2.json"
_SECRET_NAME_V2 = "selection-secret.v2.json"
_PACKET_NAME_V2 = "transcript-annotation-packet.v2.json"
_SUMMARY_NAME_V2 = "summary.v2.json"
_CLIP_DIRECTORY_V2 = "clips-v2"
_SHA256_HEX = frozenset("0123456789abcdef")


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _require_identifier(label: str, value: str) -> str:
    if not value or value.strip() != value or any(ord(character) < 32 for character in value):
        raise ValueError(f"{label} must be a non-empty canonical identifier")
    return value


def _require_sha256(label: str, value: str) -> str:
    if len(value) != 64 or any(character not in _SHA256_HEX for character in value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def _artifact_hash(model: BaseModel, *, field_name: str, kind: str) -> str:
    return hash_object(
        {
            "artifact_kind": kind,
            **model.model_dump(mode="json", exclude={field_name}),
        }
    )


class EligibleFrameStratumV1(_StrictFrozenModel):
    """Complete arithmetic representation of eligible starts in one stratum."""

    schema_version: Literal[1] = 1
    stratum_index: int
    stratum_start_frame: int
    stratum_end_frame: int
    first_eligible_start_frame: int
    last_eligible_start_frame: int
    eligible_position_step_frames: int
    eligible_position_count: int

    @model_validator(mode="after")
    def _valid(self) -> EligibleFrameStratumV1:
        if self.stratum_index < 0 or self.stratum_index >= TEMPORAL_STRATA_COUNT:
            raise ValueError("eligible stratum_index is outside the frozen 20 strata")
        if (
            self.stratum_start_frame < 0
            or self.stratum_end_frame <= self.stratum_start_frame
            or self.first_eligible_start_frame < self.stratum_start_frame
            or self.last_eligible_start_frame < self.first_eligible_start_frame
            or self.eligible_position_step_frames < 1
            or self.eligible_position_count < 1
        ):
            raise ValueError("eligible frame stratum has invalid bounds")
        if (
            self.last_eligible_start_frame - self.first_eligible_start_frame
        ) % self.eligible_position_step_frames:
            raise ValueError("eligible frame stratum is not step-aligned")
        expected_count = (
            self.last_eligible_start_frame - self.first_eligible_start_frame
        ) // self.eligible_position_step_frames + 1
        if self.eligible_position_count != expected_count:
            raise ValueError("eligible_position_count does not describe the exact range")
        return self


class SelectedFrameIntervalV1(_StrictFrozenModel):
    """One selected exact PCM frame interval and its lossless millisecond view."""

    schema_version: Literal[1] = 1
    clip_id: str
    stratum_index: int
    start_frame: int
    end_frame: int
    start_ms: int
    end_ms: int

    @field_validator("clip_id")
    @classmethod
    def _clip_id(cls, value: str) -> str:
        return _require_identifier("clip_id", value)

    @model_validator(mode="after")
    def _valid(self) -> SelectedFrameIntervalV1:
        if self.stratum_index < 0 or self.stratum_index >= TEMPORAL_STRATA_COUNT:
            raise ValueError("selected stratum_index is outside the frozen 20 strata")
        if self.start_frame < 0 or self.end_frame <= self.start_frame:
            raise ValueError("selected frame interval has invalid frame bounds")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("selected frame interval has invalid millisecond bounds")
        return self


class SamplingDeclarationV1(_StrictFrozenModel):
    """Immutable candidate-independent sampling declaration for one exact WAV."""

    _HASH_KIND: ClassVar[str] = "transcript_pilot_sampling_declaration"

    schema_version: Literal[1] = 1
    episode_id: str
    normalized_audio_hash: str
    normalized_audio_size_bytes: int
    wav_compression: Literal["NONE"] = "NONE"
    sample_rate_hz: int
    channel_count: int
    sample_width_bytes: int
    frame_count: int
    duration_floor_ms: int = Field(
        description="floor(frame_count * 1000 / sample_rate_hz); sub-ms tail retained"
    )
    selection_algorithm_id: Literal["hmac-sha256-temporal-strata-v1"] = (
        SELECTION_ALGORITHM_ID
    )
    margin_duration_ms: int
    margin_frames: int
    clip_duration_ms: int
    clip_frames: int
    temporal_strata_count: Literal[20] = TEMPORAL_STRATA_COUNT
    eligible_positions: tuple[EligibleFrameStratumV1, ...]
    selection_seed_commitment: str
    selected_intervals: tuple[SelectedFrameIntervalV1, ...]
    declaration_hash: str

    @field_validator("episode_id")
    @classmethod
    def _episode_id(cls, value: str) -> str:
        return _require_identifier("episode_id", value)

    @field_validator(
        "normalized_audio_hash", "selection_seed_commitment", "declaration_hash"
    )
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _valid(self) -> SamplingDeclarationV1:
        if (
            self.normalized_audio_size_bytes < 1
            or self.sample_rate_hz < 1
            or self.channel_count < 1
            or self.sample_width_bytes not in {1, 2, 3, 4}
            or self.frame_count < 1
            or self.duration_floor_ms < 1
            or self.margin_duration_ms < 0
            or self.margin_frames < 0
            or self.clip_duration_ms < 1
            or self.clip_frames < 1
        ):
            raise ValueError("sampling declaration WAV or duration values are invalid")
        if self.duration_floor_ms != self.frame_count * 1000 // self.sample_rate_hz:
            raise ValueError("sampling declaration duration_floor_ms is not the PCM floor")
        if self.margin_duration_ms * self.sample_rate_hz != self.margin_frames * 1000:
            raise ValueError("sampling declaration margin is not frame-exact")
        if self.clip_duration_ms * self.sample_rate_hz != self.clip_frames * 1000:
            raise ValueError("sampling declaration clip duration is not frame-exact")
        if len(self.eligible_positions) != TEMPORAL_STRATA_COUNT:
            raise ValueError("sampling declaration requires exactly 20 eligible strata")
        if len(self.selected_intervals) != TEMPORAL_STRATA_COUNT:
            raise ValueError("sampling declaration requires exactly 20 selections")
        if tuple(item.stratum_index for item in self.eligible_positions) != tuple(
            range(TEMPORAL_STRATA_COUNT)
        ):
            raise ValueError("eligible strata must be in exact canonical order")
        if tuple(item.stratum_index for item in self.selected_intervals) != tuple(
            range(TEMPORAL_STRATA_COUNT)
        ):
            raise ValueError("selected intervals must be in exact canonical stratum order")
        if (
            self.eligible_positions[0].stratum_start_frame != self.margin_frames
            or self.eligible_positions[-1].stratum_end_frame
            > self.frame_count - self.margin_frames
        ):
            raise ValueError("eligible strata escape the declared PCM margins")
        for left, right in zip(self.eligible_positions, self.eligible_positions[1:]):
            if left.stratum_end_frame != right.stratum_start_frame:
                raise ValueError("eligible strata must form one contiguous temporal partition")
        for eligible, selected in zip(
            self.eligible_positions, self.selected_intervals, strict=True
        ):
            expected_clip_id = f"clip-{selected.stratum_index + 1:03d}"
            if selected.clip_id != expected_clip_id:
                raise ValueError("selected interval clip_id is not canonical")
            if selected.end_frame - selected.start_frame != self.clip_frames:
                raise ValueError("selected interval duration differs from declared clip_frames")
            if (
                selected.start_frame < eligible.first_eligible_start_frame
                or selected.start_frame > eligible.last_eligible_start_frame
                or (
                    selected.start_frame - eligible.first_eligible_start_frame
                )
                % eligible.eligible_position_step_frames
                or selected.end_frame > eligible.stratum_end_frame
                or selected.start_frame * 1000
                != selected.start_ms * self.sample_rate_hz
                or selected.end_frame * 1000 != selected.end_ms * self.sample_rate_hz
            ):
                raise ValueError("selected interval is outside its exact eligible positions")
        for left, right in zip(self.selected_intervals, self.selected_intervals[1:]):
            if left.end_frame > right.start_frame:
                raise ValueError("sampling declaration selected intervals overlap")
        if self.declaration_hash != _artifact_hash(
            self,
            field_name="declaration_hash",
            kind=self._HASH_KIND,
        ):
            raise ValueError("sampling declaration_hash mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class SelectionSecretV1(_StrictFrozenModel):
    """Private replay material; never copied into the annotation packet."""

    _HASH_KIND: ClassVar[str] = "transcript_pilot_selection_secret"

    schema_version: Literal[1] = 1
    episode_id: str
    selection_context_hash: str
    selection_seed_hex: str
    selection_nonce_hex: str
    selection_seed_commitment: str
    sampling_declaration_hash: str
    secret_record_hash: str

    @field_validator("episode_id")
    @classmethod
    def _episode_id(cls, value: str) -> str:
        return _require_identifier("episode_id", value)

    @field_validator(
        "selection_context_hash",
        "selection_seed_commitment",
        "sampling_declaration_hash",
        "secret_record_hash",
    )
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @field_validator("selection_seed_hex", "selection_nonce_hex")
    @classmethod
    def _secrets(cls, value: str, info: Any) -> str:
        if (
            len(value) < 64
            or len(value) % 2
            or any(character not in _SHA256_HEX for character in value)
        ):
            raise ValueError(f"{info.field_name} must encode at least 256 bits")
        return value

    @model_validator(mode="after")
    def _valid(self) -> SelectionSecretV1:
        if self.secret_record_hash != _artifact_hash(
            self,
            field_name="secret_record_hash",
            kind=self._HASH_KIND,
        ):
            raise ValueError("selection secret_record_hash mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class PilotArtifactBindingV1(_StrictFrozenModel):
    relative_path: str
    sha256: str
    size_bytes: int

    @field_validator("relative_path")
    @classmethod
    def _path(cls, value: str) -> str:
        if (
            not value
            or "\\" in value
            or Path(value).is_absolute()
            or ".." in Path(value).parts
        ):
            raise ValueError("pilot artifact path must be safe and relative")
        return value

    @field_validator("sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _require_sha256("sha256", value)

    @model_validator(mode="after")
    def _valid(self) -> PilotArtifactBindingV1:
        if self.size_bytes < 1:
            raise ValueError("pilot artifact size must be positive")
        return self


class TranscriptPilotSummaryV1(_StrictFrozenModel):
    _HASH_KIND: ClassVar[str] = "transcript_pilot_summary"

    schema_version: Literal[1] = 1
    status: Literal["complete"] = "complete"
    episode_id: str
    normalized_audio_hash: str
    sampling_declaration_hash: str
    annotation_packet_hash: str
    selection_secret_record_hash: str
    clip_count: Literal[20] = TEMPORAL_STRATA_COUNT
    artifacts: tuple[PilotArtifactBindingV1, ...]
    summary_hash: str

    @field_validator("episode_id")
    @classmethod
    def _episode_id(cls, value: str) -> str:
        return _require_identifier("episode_id", value)

    @field_validator(
        "normalized_audio_hash",
        "sampling_declaration_hash",
        "annotation_packet_hash",
        "selection_secret_record_hash",
        "summary_hash",
    )
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _valid(self) -> TranscriptPilotSummaryV1:
        paths = tuple(item.relative_path for item in self.artifacts)
        if len(paths) != 23 or paths != tuple(sorted(paths)) or len(set(paths)) != len(paths):
            raise ValueError("pilot summary must bind the canonical 23 non-summary artifacts")
        if self.summary_hash != _artifact_hash(
            self,
            field_name="summary_hash",
            kind=self._HASH_KIND,
        ):
            raise ValueError("pilot summary_hash mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class PcmAnalysisRuntimeIdentityV2(_StrictFrozenModel):
    """Exact implementation identity used for candidate-free energy analysis."""

    schema_version: Literal[2] = 2
    python_implementation: str
    python_version: str
    numpy_version: str
    byte_order: Literal["little", "big"]
    integer_accumulator: Literal["python-arbitrary-precision"] = "python-arbitrary-precision"
    vector_decoder: Literal["numpy-explicit-little-endian-v1"] = (
        "numpy-explicit-little-endian-v1"
    )

    @field_validator("python_implementation", "python_version", "numpy_version")
    @classmethod
    def _identifiers(cls, value: str, info: Any) -> str:
        return _require_identifier(info.field_name, value)


class EligibleSilenceAlignedStratumV2(_StrictFrozenModel):
    """One complete 10 ms-grid search domain and its audio-derived eligible count."""

    schema_version: Literal[2] = 2
    stratum_index: int
    stratum_start_frame: int
    stratum_end_frame: int
    first_analyzed_start_frame: int
    last_analyzed_start_frame: int
    analyzed_position_step_frames: int
    analyzed_position_count: int
    eligible_position_count: int

    @model_validator(mode="after")
    def _valid(self) -> EligibleSilenceAlignedStratumV2:
        if self.stratum_index < 0 or self.stratum_index >= TEMPORAL_STRATA_COUNT:
            raise ValueError("silence-aligned stratum_index is outside the frozen 20 strata")
        if (
            self.stratum_start_frame < 0
            or self.stratum_end_frame <= self.stratum_start_frame
            or self.first_analyzed_start_frame < self.stratum_start_frame
            or self.last_analyzed_start_frame < self.first_analyzed_start_frame
            or self.analyzed_position_step_frames < 1
            or self.analyzed_position_count < 1
            or self.eligible_position_count < 1
            or self.eligible_position_count > self.analyzed_position_count
        ):
            raise ValueError("silence-aligned stratum has invalid search counts or bounds")
        if (
            self.last_analyzed_start_frame - self.first_analyzed_start_frame
        ) % self.analyzed_position_step_frames:
            raise ValueError("silence-aligned stratum search domain is not grid-aligned")
        expected = (
            self.last_analyzed_start_frame - self.first_analyzed_start_frame
        ) // self.analyzed_position_step_frames + 1
        if self.analyzed_position_count != expected:
            raise ValueError("silence-aligned analyzed_position_count is not exact")
        return self


class SelectedSilenceAlignedIntervalV2(_StrictFrozenModel):
    """Selected frame interval plus exact integer energy-gate observations."""

    schema_version: Literal[2] = 2
    clip_id: str
    stratum_index: int
    eligible_ordinal: int
    start_frame: int
    end_frame: int
    start_ms: int
    end_ms: int
    leading_boundary_sum_squares: int
    trailing_boundary_sum_squares: int
    boundary_sample_count: int
    leading_boundary_rms_ppm_floor: int
    trailing_boundary_rms_ppm_floor: int
    interior_cell_count: int
    interior_active_cell_count: int
    interior_active_ratio_ppm_floor: int

    @field_validator("clip_id")
    @classmethod
    def _clip_id(cls, value: str) -> str:
        return _require_identifier("clip_id", value)

    @model_validator(mode="after")
    def _valid(self) -> SelectedSilenceAlignedIntervalV2:
        if self.stratum_index < 0 or self.stratum_index >= TEMPORAL_STRATA_COUNT:
            raise ValueError("selected V2 stratum_index is outside the frozen 20 strata")
        if (
            self.eligible_ordinal < 0
            or self.start_frame < 0
            or self.end_frame <= self.start_frame
            or self.start_ms < 0
            or self.end_ms <= self.start_ms
            or self.leading_boundary_sum_squares < 0
            or self.trailing_boundary_sum_squares < 0
            or self.boundary_sample_count < 1
            or self.leading_boundary_rms_ppm_floor < 0
            or self.trailing_boundary_rms_ppm_floor < 0
            or self.interior_cell_count < 1
            or self.interior_active_cell_count < 0
            or self.interior_active_cell_count > self.interior_cell_count
            or self.interior_active_ratio_ppm_floor < 0
        ):
            raise ValueError("selected V2 interval has invalid energy observations")
        if self.interior_active_ratio_ppm_floor != (
            self.interior_active_cell_count * _PPM_SCALE // self.interior_cell_count
        ):
            raise ValueError("selected V2 interior occupancy ratio is not exact")
        return self


class SamplingDeclarationV2(_StrictFrozenModel):
    """Audio-only, silence-aligned sampling declaration for one exact PCM WAV."""

    _HASH_KIND: ClassVar[str] = "transcript_pilot_sampling_declaration_v2"

    schema_version: Literal[2] = 2
    selection_policy: Literal["silence-aligned-v2"] = SELECTION_POLICY_V2
    episode_id: str
    normalized_audio_hash: str
    normalized_audio_size_bytes: int
    wav_compression: Literal["NONE"] = "NONE"
    pcm_sample_encoding: Literal[
        "wav-pcm-u8-or-signed-little-endian-two-complement"
    ] = "wav-pcm-u8-or-signed-little-endian-two-complement"
    pcm_channel_aggregation: Literal["all-channel-samples-equal-weight"] = (
        "all-channel-samples-equal-weight"
    )
    rms_gate_arithmetic: Literal["integer-mean-square-cross-multiply-v1"] = (
        "integer-mean-square-cross-multiply-v1"
    )
    sample_rate_hz: int
    channel_count: int
    sample_width_bytes: int
    full_scale_units: int
    frame_count: int
    duration_floor_ms: int = Field(
        description="floor(frame_count * 1000 / sample_rate_hz); sub-ms tail retained"
    )
    selection_algorithm_id: Literal[
        "hmac-sha256-silence-aligned-temporal-strata-v2"
    ] = SELECTION_ALGORITHM_ID_V2
    margin_duration_ms: int
    margin_frames: int
    clip_duration_ms: int
    clip_frames: int
    energy_grid_duration_ms: Literal[10] = ENERGY_GRID_DURATION_MS_V2
    energy_grid_frames: int
    boundary_window_duration_ms: Literal[250] = BOUNDARY_WINDOW_DURATION_MS_V2
    boundary_window_frames: int
    boundary_rms_max_ppm: Literal[18000] = BOUNDARY_RMS_MAX_PPM_V2
    interior_active_rms_min_ppm: Literal[10000] = INTERIOR_ACTIVE_RMS_MIN_PPM_V2
    interior_active_ratio_min_ppm: Literal[200000] = (
        INTERIOR_ACTIVE_RATIO_MIN_PPM_V2
    )
    temporal_strata_count: Literal[20] = TEMPORAL_STRATA_COUNT
    pcm_analysis_code_hash: str
    pcm_analysis_runtime_identity: PcmAnalysisRuntimeIdentityV2
    eligible_strata: tuple[EligibleSilenceAlignedStratumV2, ...]
    selection_seed_commitment: str
    selected_intervals: tuple[SelectedSilenceAlignedIntervalV2, ...]
    declaration_hash: str

    @field_validator("episode_id")
    @classmethod
    def _episode_id(cls, value: str) -> str:
        return _require_identifier("episode_id", value)

    @field_validator(
        "normalized_audio_hash",
        "pcm_analysis_code_hash",
        "selection_seed_commitment",
        "declaration_hash",
    )
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _valid(self) -> SamplingDeclarationV2:
        if (
            self.normalized_audio_size_bytes < 1
            or self.sample_rate_hz < 1
            or self.channel_count < 1
            or self.sample_width_bytes not in {1, 2, 3}
            or self.full_scale_units != 1 << (self.sample_width_bytes * 8 - 1)
            or self.frame_count < 1
            or self.duration_floor_ms != self.frame_count * 1000 // self.sample_rate_hz
            or self.margin_duration_ms < 0
            or self.margin_frames < 0
            or self.clip_duration_ms < 1
            or self.clip_frames < 1
        ):
            raise ValueError("V2 sampling declaration WAV or duration values are invalid")
        exact_durations = (
            (self.margin_duration_ms, self.margin_frames),
            (self.clip_duration_ms, self.clip_frames),
            (self.energy_grid_duration_ms, self.energy_grid_frames),
            (self.boundary_window_duration_ms, self.boundary_window_frames),
        )
        if any(
            duration_ms * self.sample_rate_hz != frames * 1000
            for duration_ms, frames in exact_durations
        ):
            raise ValueError("V2 sampling durations are not exact PCM frame durations")
        if (
            self.margin_frames % self.energy_grid_frames
            or self.clip_frames % self.energy_grid_frames
            or self.boundary_window_frames % self.energy_grid_frames
            or self.clip_frames <= 2 * self.boundary_window_frames
        ):
            raise ValueError("V2 sampling geometry is not a valid 10 ms grid")
        if len(self.eligible_strata) != 20 or tuple(
            item.stratum_index for item in self.eligible_strata
        ) != tuple(range(20)):
            raise ValueError("V2 declaration requires 20 canonical eligible strata")
        if len(self.selected_intervals) != 20 or tuple(
            item.stratum_index for item in self.selected_intervals
        ) != tuple(range(20)):
            raise ValueError("V2 declaration requires 20 canonical selected intervals")
        for left, right in zip(self.eligible_strata, self.eligible_strata[1:]):
            if left.stratum_end_frame != right.stratum_start_frame:
                raise ValueError("V2 eligible strata must be a contiguous partition")
        boundary_sample_count = self.boundary_window_frames * self.channel_count
        interior_cell_count = (
            self.clip_frames - 2 * self.boundary_window_frames
        ) // self.energy_grid_frames
        for stratum, selected in zip(
            self.eligible_strata, self.selected_intervals, strict=True
        ):
            if (
                selected.clip_id != f"clip-{selected.stratum_index + 1:03d}"
                or selected.eligible_ordinal >= stratum.eligible_position_count
                or selected.start_frame < stratum.first_analyzed_start_frame
                or selected.start_frame > stratum.last_analyzed_start_frame
                or (
                    selected.start_frame - stratum.first_analyzed_start_frame
                )
                % stratum.analyzed_position_step_frames
                or selected.end_frame - selected.start_frame != self.clip_frames
                or selected.end_frame > stratum.stratum_end_frame
                or selected.start_frame * 1000 != selected.start_ms * self.sample_rate_hz
                or selected.end_frame * 1000 != selected.end_ms * self.sample_rate_hz
                or selected.boundary_sample_count != boundary_sample_count
                or selected.interior_cell_count != interior_cell_count
                or selected.leading_boundary_rms_ppm_floor > self.boundary_rms_max_ppm
                or selected.trailing_boundary_rms_ppm_floor > self.boundary_rms_max_ppm
                or selected.interior_active_ratio_ppm_floor
                < self.interior_active_ratio_min_ppm
            ):
                raise ValueError("V2 selected interval does not satisfy its frozen gates")
            for sum_squares in (
                selected.leading_boundary_sum_squares,
                selected.trailing_boundary_sum_squares,
            ):
                if not _rms_at_most_ppm_v2(
                    sum_squares=sum_squares,
                    sample_count=boundary_sample_count,
                    full_scale_units=self.full_scale_units,
                    threshold_ppm=self.boundary_rms_max_ppm,
                ):
                    raise ValueError("V2 selected interval boundary is not silence-aligned")
            if (
                selected.leading_boundary_rms_ppm_floor
                != _rms_ppm_floor_v2(
                    sum_squares=selected.leading_boundary_sum_squares,
                    sample_count=boundary_sample_count,
                    full_scale_units=self.full_scale_units,
                )
                or selected.trailing_boundary_rms_ppm_floor
                != _rms_ppm_floor_v2(
                    sum_squares=selected.trailing_boundary_sum_squares,
                    sample_count=boundary_sample_count,
                    full_scale_units=self.full_scale_units,
                )
            ):
                raise ValueError("V2 selected boundary RMS receipt is not exact")
        for left, right in zip(self.selected_intervals, self.selected_intervals[1:]):
            if left.end_frame > right.start_frame:
                raise ValueError("V2 selected intervals overlap")
        if self.declaration_hash != _artifact_hash(
            self,
            field_name="declaration_hash",
            kind=self._HASH_KIND,
        ):
            raise ValueError("V2 sampling declaration_hash mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class SelectionSecretV2(_StrictFrozenModel):
    """Private replay material for one V2 silence-aligned selection."""

    _HASH_KIND: ClassVar[str] = "transcript_pilot_selection_secret_v2"

    schema_version: Literal[2] = 2
    selection_policy: Literal["silence-aligned-v2"] = SELECTION_POLICY_V2
    episode_id: str
    selection_context_hash: str
    selection_seed_hex: str
    selection_nonce_hex: str
    selection_seed_commitment: str
    sampling_declaration_hash: str
    secret_record_hash: str

    @field_validator("episode_id")
    @classmethod
    def _episode_id(cls, value: str) -> str:
        return _require_identifier("episode_id", value)

    @field_validator(
        "selection_context_hash",
        "selection_seed_commitment",
        "sampling_declaration_hash",
        "secret_record_hash",
    )
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @field_validator("selection_seed_hex", "selection_nonce_hex")
    @classmethod
    def _secrets(cls, value: str, info: Any) -> str:
        if (
            len(value) < 64
            or len(value) % 2
            or any(character not in _SHA256_HEX for character in value)
        ):
            raise ValueError(f"{info.field_name} must encode at least 256 bits")
        return value

    @model_validator(mode="after")
    def _valid(self) -> SelectionSecretV2:
        if self.secret_record_hash != _artifact_hash(
            self,
            field_name="secret_record_hash",
            kind=self._HASH_KIND,
        ):
            raise ValueError("V2 selection secret_record_hash mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class TranscriptPilotSummaryV2(_StrictFrozenModel):
    _HASH_KIND: ClassVar[str] = "transcript_pilot_summary_v2"

    schema_version: Literal[2] = 2
    selection_policy: Literal["silence-aligned-v2"] = SELECTION_POLICY_V2
    status: Literal["complete"] = "complete"
    episode_id: str
    normalized_audio_hash: str
    sampling_declaration_hash: str
    annotation_packet_hash: str
    selection_secret_record_hash: str
    clip_count: Literal[20] = TEMPORAL_STRATA_COUNT
    artifacts: tuple[PilotArtifactBindingV1, ...]
    summary_hash: str

    @field_validator("episode_id")
    @classmethod
    def _episode_id(cls, value: str) -> str:
        return _require_identifier("episode_id", value)

    @field_validator(
        "normalized_audio_hash",
        "sampling_declaration_hash",
        "annotation_packet_hash",
        "selection_secret_record_hash",
        "summary_hash",
    )
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _valid(self) -> TranscriptPilotSummaryV2:
        paths = tuple(item.relative_path for item in self.artifacts)
        if len(paths) != 23 or paths != tuple(sorted(paths)) or len(set(paths)) != len(paths):
            raise ValueError("V2 summary must bind the canonical 23 non-summary artifacts")
        if self.summary_hash != _artifact_hash(
            self,
            field_name="summary_hash",
            kind=self._HASH_KIND,
        ):
            raise ValueError("V2 pilot summary_hash mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


@dataclass(frozen=True, slots=True)
class TranscriptPilotRequest:
    workspace_root: Path
    normalized_wav_path: Path
    episode_id: str
    packet_id: str
    planned_candidate_roots: tuple[Path, ...]
    selection_policy: Literal[
        "temporal-stratified-v1", "silence-aligned-v2"
    ] = SELECTION_POLICY_V1
    instruction_profile_id: str = "audio-only-transcript-v1"
    protocol: TranscriptAnnotationProtocol = field(
        default_factory=lambda: TranscriptAnnotationProtocol(
            protocol_id="two-pass-third-adjudication-v1"
        )
    )
    margin_duration_ms: int = 30_000
    clip_duration_ms: int = 15_000
    selection_seed: bytes | None = None
    selection_nonce: bytes | None = None
    expected_normalized_audio_hash: str | None = None
    expected_normalized_audio_size_bytes: int | None = None
    expected_sample_rate_hz: int | None = None
    expected_channel_count: int | None = None
    expected_sample_width_bytes: int | None = None
    expected_frame_count: int | None = None
    expected_duration_floor_ms: int | None = None


@dataclass(frozen=True, slots=True)
class TranscriptPilotResult:
    declaration: SamplingDeclarationV1
    secret: SelectionSecretV1
    annotation_packet: TranscriptAnnotationPacket
    summary: TranscriptPilotSummaryV1
    workspace_root: Path
    declaration_path: Path
    secret_path: Path
    annotation_packet_path: Path
    summary_path: Path
    clip_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class TranscriptPilotResultV2:
    declaration: SamplingDeclarationV2
    secret: SelectionSecretV2
    annotation_packet: TranscriptAnnotationPacket
    summary: TranscriptPilotSummaryV2
    workspace_root: Path
    declaration_path: Path
    secret_path: Path
    annotation_packet_path: Path
    summary_path: Path
    clip_paths: tuple[Path, ...]


TranscriptPilotResultAny: TypeAlias = TranscriptPilotResult | TranscriptPilotResultV2


@dataclass(frozen=True, slots=True)
class _WavInfo:
    sha256: str
    size_bytes: int
    sample_rate_hz: int
    channel_count: int
    sample_width_bytes: int
    frame_count: int
    duration_floor_ms: int


def _is_reparse_or_link(path: Path) -> bool:
    metadata = path.lstat()
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return stat.S_ISLNK(metadata.st_mode) or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _assert_no_link_ancestors(path: Path) -> None:
    current = path.absolute()
    for candidate in (current, *current.parents):
        if candidate.exists() and _is_reparse_or_link(candidate):
            raise ValueError(f"pilot path traverses a link or reparse point: {candidate}")


def _assert_candidate_roots_absent(request: TranscriptPilotRequest) -> None:
    if not request.planned_candidate_roots:
        raise ValueError("at least one planned candidate root is required")
    workspace = request.workspace_root.absolute()
    normalized = request.normalized_wav_path.absolute()
    seen: set[str] = set()
    for root in request.planned_candidate_roots:
        candidate = root.absolute()
        key = os.path.normcase(str(candidate))
        if key in seen:
            raise ValueError("planned candidate roots must be unique")
        seen.add(key)
        _assert_no_link_ancestors(candidate)
        if candidate.exists():
            raise ValueError("planned candidate roots must not exist before pilot sampling")
        if workspace == candidate or workspace.is_relative_to(candidate):
            raise ValueError("pilot workspace must be outside planned candidate roots")
        if normalized == candidate or normalized.is_relative_to(candidate):
            raise ValueError("normalized WAV must be outside planned candidate roots")


def _measure_wav(path: Path) -> _WavInfo:
    _assert_no_link_ancestors(path)
    if not path.is_file() or _is_reparse_or_link(path):
        raise ValueError("normalized audio must be a non-link regular file")
    before = path.stat()
    digest = hash_file(path)
    try:
        with wave.open(str(path), "rb") as reader:
            if reader.getcomptype() != "NONE":
                raise ValueError("normalized WAV must use uncompressed PCM")
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            sample_rate = reader.getframerate()
            frame_count = reader.getnframes()
            if (
                channels < 1
                or sample_width not in {1, 2, 3, 4}
                or sample_rate < 1
                or frame_count < 1
            ):
                raise ValueError("normalized PCM WAV has invalid topology")
            expected_pcm_bytes = channels * sample_width * frame_count
            observed_pcm_bytes = 0
            frames_per_chunk = max(1, (1024 * 1024) // (channels * sample_width))
            while observed_pcm_bytes < expected_pcm_bytes:
                chunk = reader.readframes(frames_per_chunk)
                if not chunk:
                    break
                observed_pcm_bytes += len(chunk)
            if observed_pcm_bytes != expected_pcm_bytes or reader.readframes(1):
                raise ValueError("normalized PCM WAV frame payload is incomplete")
    except (EOFError, wave.Error) as exc:
        raise ValueError("normalized audio must be a valid uncompressed PCM WAV") from exc
    after = path.stat()
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("normalized WAV changed during streaming validation")
    return _WavInfo(
        sha256=digest,
        size_bytes=after.st_size,
        sample_rate_hz=sample_rate,
        channel_count=channels,
        sample_width_bytes=sample_width,
        frame_count=frame_count,
        duration_floor_ms=frame_count * 1000 // sample_rate,
    )


def _validate_expected_wav(request: TranscriptPilotRequest, wav: _WavInfo) -> None:
    if request.expected_normalized_audio_hash is not None:
        _require_sha256(
            "expected_normalized_audio_hash",
            request.expected_normalized_audio_hash,
        )
    expectations = {
        "normalized_audio_hash": (request.expected_normalized_audio_hash, wav.sha256),
        "normalized_audio_size_bytes": (
            request.expected_normalized_audio_size_bytes,
            wav.size_bytes,
        ),
        "sample_rate_hz": (request.expected_sample_rate_hz, wav.sample_rate_hz),
        "channel_count": (request.expected_channel_count, wav.channel_count),
        "sample_width_bytes": (
            request.expected_sample_width_bytes,
            wav.sample_width_bytes,
        ),
        "frame_count": (request.expected_frame_count, wav.frame_count),
        "duration_floor_ms": (
            request.expected_duration_floor_ms,
            wav.duration_floor_ms,
        ),
    }
    for label, (expected, observed) in expectations.items():
        if expected is not None and expected != observed:
            raise ValueError(f"normalized WAV {label} differs from caller expectation")


def _eligible_strata(
    wav: _WavInfo,
    *,
    margin_duration_ms: int,
    clip_duration_ms: int,
) -> tuple[int, int, tuple[EligibleFrameStratumV1, ...]]:
    if margin_duration_ms < 0 or clip_duration_ms < 1:
        raise ValueError("pilot margin and clip durations are invalid")
    margin_numerator = margin_duration_ms * wav.sample_rate_hz
    clip_numerator = clip_duration_ms * wav.sample_rate_hz
    if margin_numerator % 1000 or clip_numerator % 1000:
        raise ValueError("pilot durations must map exactly to PCM frames")
    margin_frames = margin_numerator // 1000
    clip_frames = clip_numerator // 1000
    frame_step = wav.sample_rate_hz // math.gcd(wav.sample_rate_hz, 1000)
    if margin_frames % frame_step or clip_frames % frame_step:
        raise ValueError("pilot configured durations must have exact millisecond bounds")
    usable_end_frame = (
        (wav.frame_count - margin_frames) // frame_step * frame_step
    )
    usable_frames = usable_end_frame - margin_frames
    usable_units = usable_frames // frame_step
    clip_units = clip_frames // frame_step
    if usable_frames <= 0 or usable_units < TEMPORAL_STRATA_COUNT * clip_units:
        raise ValueError("normalized WAV is too short for 20 non-overlapping pilot strata")
    result: list[EligibleFrameStratumV1] = []
    for index in range(TEMPORAL_STRATA_COUNT):
        start_units = index * usable_units // TEMPORAL_STRATA_COUNT
        end_units = (index + 1) * usable_units // TEMPORAL_STRATA_COUNT
        stratum_start = margin_frames + start_units * frame_step
        stratum_end = margin_frames + end_units * frame_step
        last_start = stratum_end - clip_frames
        if last_start < stratum_start:
            raise ValueError("one temporal stratum cannot contain the configured clip")
        result.append(
            EligibleFrameStratumV1(
                stratum_index=index,
                stratum_start_frame=stratum_start,
                stratum_end_frame=stratum_end,
                first_eligible_start_frame=stratum_start,
                last_eligible_start_frame=last_start,
                eligible_position_step_frames=frame_step,
                eligible_position_count=(last_start - stratum_start) // frame_step + 1,
            )
        )
    return margin_frames, clip_frames, tuple(result)


def _rms_at_most_ppm_v2(
    *,
    sum_squares: int,
    sample_count: int,
    full_scale_units: int,
    threshold_ppm: int,
) -> bool:
    """Compare RMS to a full-scale PPM ceiling without roots or floating point."""

    if sum_squares < 0 or sample_count < 1 or full_scale_units < 1 or threshold_ppm < 0:
        raise ValueError("integer RMS ceiling received invalid values")
    return sum_squares * _PPM_SCALE**2 <= (
        sample_count * full_scale_units**2 * threshold_ppm**2
    )


def _rms_at_least_ppm_v2(
    *,
    sum_squares: int,
    sample_count: int,
    full_scale_units: int,
    threshold_ppm: int,
) -> bool:
    """Compare RMS to a full-scale PPM floor without roots or floating point."""

    if sum_squares < 0 or sample_count < 1 or full_scale_units < 1 or threshold_ppm < 0:
        raise ValueError("integer RMS floor received invalid values")
    return sum_squares * _PPM_SCALE**2 >= (
        sample_count * full_scale_units**2 * threshold_ppm**2
    )


def _rms_ppm_floor_v2(
    *,
    sum_squares: int,
    sample_count: int,
    full_scale_units: int,
) -> int:
    if sum_squares < 0 or sample_count < 1 or full_scale_units < 1:
        raise ValueError("integer RMS measurement received invalid values")
    quotient = sum_squares * _PPM_SCALE**2 // (
        sample_count * full_scale_units**2
    )
    return math.isqrt(quotient)


def _decode_pcm_samples_v2(payload: bytes, *, sample_width_bytes: int) -> np.ndarray:
    """Decode WAV PCM to signed int64 samples with explicit little-endian semantics."""

    if sample_width_bytes == 1:
        return np.frombuffer(payload, dtype=np.uint8).astype(np.int64) - 128
    if sample_width_bytes == 2:
        if len(payload) % 2:
            raise ValueError("16-bit PCM analysis payload is not sample-aligned")
        return np.frombuffer(payload, dtype="<i2").astype(np.int64)
    if sample_width_bytes == 3:
        if len(payload) % 3:
            raise ValueError("24-bit PCM analysis payload is not sample-aligned")
        octets = np.frombuffer(payload, dtype=np.uint8).reshape(-1, 3).astype(np.int64)
        unsigned = octets[:, 0] | (octets[:, 1] << 8) | (octets[:, 2] << 16)
        return (unsigned ^ 0x800000) - 0x800000
    raise ValueError("silence-aligned V2 supports 8-, 16-, or 24-bit PCM WAV")


def _scan_grid_sum_squares_v2(
    path: Path,
    wav: _WavInfo,
    *,
    grid_frames: int,
) -> np.ndarray:
    """Stream exact per-grid all-channel sum-of-squares; never read the whole WAV."""

    if wav.sample_width_bytes not in {1, 2, 3} or grid_frames < 1:
        raise ValueError("silence-aligned V2 PCM grid is unsupported")
    complete_cells = wav.frame_count // grid_frames
    if complete_cells < 1:
        raise ValueError("normalized WAV contains no complete V2 energy grid cell")
    result = np.empty(complete_cells, dtype=np.int64)
    cells_per_batch = 256
    cursor = 0
    try:
        with wave.open(str(path), "rb") as reader:
            if (
                reader.getcomptype() != "NONE"
                or reader.getframerate() != wav.sample_rate_hz
                or reader.getnchannels() != wav.channel_count
                or reader.getsampwidth() != wav.sample_width_bytes
                or reader.getnframes() != wav.frame_count
            ):
                raise ValueError("normalized WAV topology changed before V2 energy analysis")
            while cursor < complete_cells:
                batch_cells = min(cells_per_batch, complete_cells - cursor)
                frame_count = batch_cells * grid_frames
                pcm = reader.readframes(frame_count)
                expected_bytes = frame_count * wav.channel_count * wav.sample_width_bytes
                if len(pcm) != expected_bytes:
                    raise ValueError("normalized WAV truncated during V2 energy analysis")
                samples = _decode_pcm_samples_v2(
                    pcm,
                    sample_width_bytes=wav.sample_width_bytes,
                )
                samples_per_cell = grid_frames * wav.channel_count
                cells = samples.reshape(batch_cells, samples_per_cell)
                squared = cells * cells
                result[cursor : cursor + batch_cells] = np.sum(
                    squared,
                    axis=1,
                    dtype=np.int64,
                )
                cursor += batch_cells
    except (EOFError, wave.Error) as exc:
        raise ValueError("normalized WAV is unreadable during V2 energy analysis") from exc
    return result


@dataclass(frozen=True, slots=True)
class _V2SearchDomain:
    stratum_index: int
    stratum_start_frame: int
    stratum_end_frame: int
    first_start_frame: int
    last_start_frame: int
    step_frames: int


@dataclass(frozen=True, slots=True)
class _V2EnergyAnalysis:
    grid_sum_squares: np.ndarray
    boundary_quiet: bytes
    active_prefix: array[int]
    grid_frames: int
    boundary_frames: int
    clip_frames: int
    full_scale_units: int
    eligible_starts: tuple[tuple[int, ...], ...]


def _v2_search_domains(
    wav: _WavInfo,
    *,
    margin_duration_ms: int,
    clip_duration_ms: int,
) -> tuple[int, int, int, int, tuple[_V2SearchDomain, ...]]:
    values_ms = (
        margin_duration_ms,
        clip_duration_ms,
        ENERGY_GRID_DURATION_MS_V2,
        BOUNDARY_WINDOW_DURATION_MS_V2,
    )
    if margin_duration_ms < 0 or clip_duration_ms <= 2 * BOUNDARY_WINDOW_DURATION_MS_V2:
        raise ValueError("V2 margin or clip duration is incompatible with boundary windows")
    numerators = tuple(value * wav.sample_rate_hz for value in values_ms)
    if any(value % 1000 for value in numerators):
        raise ValueError("V2 durations must map exactly to PCM frames")
    margin_frames, clip_frames, grid_frames, boundary_frames = tuple(
        value // 1000 for value in numerators
    )
    if (
        margin_frames % grid_frames
        or clip_frames % grid_frames
        or boundary_frames % grid_frames
    ):
        raise ValueError("V2 margin, clip, and boundary windows must align to the 10 ms grid")
    usable_end_frame = (wav.frame_count - margin_frames) // grid_frames * grid_frames
    usable_frames = usable_end_frame - margin_frames
    usable_cells = usable_frames // grid_frames
    clip_cells = clip_frames // grid_frames
    if usable_frames <= 0 or usable_cells < TEMPORAL_STRATA_COUNT * clip_cells:
        raise ValueError("normalized WAV is too short for 20 silence-aligned V2 strata")
    domains: list[_V2SearchDomain] = []
    for index in range(TEMPORAL_STRATA_COUNT):
        start_cells = index * usable_cells // TEMPORAL_STRATA_COUNT
        end_cells = (index + 1) * usable_cells // TEMPORAL_STRATA_COUNT
        stratum_start = margin_frames + start_cells * grid_frames
        stratum_end = margin_frames + end_cells * grid_frames
        last_start = stratum_end - clip_frames
        if last_start < stratum_start:
            raise ValueError("one V2 temporal stratum cannot contain the configured clip")
        domains.append(
            _V2SearchDomain(
                stratum_index=index,
                stratum_start_frame=stratum_start,
                stratum_end_frame=stratum_end,
                first_start_frame=stratum_start,
                last_start_frame=last_start,
                step_frames=grid_frames,
            )
        )
    return margin_frames, clip_frames, grid_frames, boundary_frames, tuple(domains)


def _analyze_v2_eligibility(
    path: Path,
    wav: _WavInfo,
    *,
    clip_frames: int,
    grid_frames: int,
    boundary_frames: int,
    domains: tuple[_V2SearchDomain, ...],
) -> _V2EnergyAnalysis:
    energy = _scan_grid_sum_squares_v2(path, wav, grid_frames=grid_frames)
    full_scale_units = 1 << (wav.sample_width_bytes * 8 - 1)
    grid_sample_count = grid_frames * wav.channel_count
    boundary_cells = boundary_frames // grid_frames
    clip_cells = clip_frames // grid_frames
    interior_cells = clip_cells - 2 * boundary_cells
    if interior_cells < 1:
        raise ValueError("V2 clip has no interior energy cells")

    quiet = bytearray(max(0, len(energy) - boundary_cells + 1))
    rolling = sum(int(value) for value in energy[:boundary_cells])
    boundary_sample_count = boundary_frames * wav.channel_count
    for index in range(len(quiet)):
        quiet[index] = _rms_at_most_ppm_v2(
            sum_squares=rolling,
            sample_count=boundary_sample_count,
            full_scale_units=full_scale_units,
            threshold_ppm=BOUNDARY_RMS_MAX_PPM_V2,
        )
        if index + boundary_cells < len(energy):
            rolling += int(energy[index + boundary_cells]) - int(energy[index])

    active_prefix = array("Q", [0])
    active_count = 0
    for sum_squares in energy:
        if _rms_at_least_ppm_v2(
            sum_squares=int(sum_squares),
            sample_count=grid_sample_count,
            full_scale_units=full_scale_units,
            threshold_ppm=INTERIOR_ACTIVE_RMS_MIN_PPM_V2,
        ):
            active_count += 1
        active_prefix.append(active_count)

    eligible_by_stratum: list[tuple[int, ...]] = []
    for domain in domains:
        eligible: list[int] = []
        for start_frame in range(
            domain.first_start_frame,
            domain.last_start_frame + 1,
            domain.step_frames,
        ):
            start_cell = start_frame // grid_frames
            end_cell = start_cell + clip_cells
            trailing_boundary_cell = end_cell - boundary_cells
            interior_start = start_cell + boundary_cells
            interior_end = trailing_boundary_cell
            interior_active = active_prefix[interior_end] - active_prefix[interior_start]
            if (
                quiet[start_cell]
                and quiet[trailing_boundary_cell]
                and interior_active * _PPM_SCALE
                >= interior_cells * INTERIOR_ACTIVE_RATIO_MIN_PPM_V2
            ):
                eligible.append(start_frame)
        if not eligible:
            raise ValueError(
                "silence-aligned V2 found no eligible start in temporal stratum "
                f"{domain.stratum_index}"
            )
        eligible_by_stratum.append(tuple(eligible))
    return _V2EnergyAnalysis(
        grid_sum_squares=energy,
        boundary_quiet=bytes(quiet),
        active_prefix=active_prefix,
        grid_frames=grid_frames,
        boundary_frames=boundary_frames,
        clip_frames=clip_frames,
        full_scale_units=full_scale_units,
        eligible_starts=tuple(eligible_by_stratum),
    )


def _pcm_analysis_code_hash_v2() -> str:
    functions = (
        _rms_at_most_ppm_v2,
        _rms_at_least_ppm_v2,
        _rms_ppm_floor_v2,
        _decode_pcm_samples_v2,
        _scan_grid_sum_squares_v2,
        _v2_search_domains,
        _analyze_v2_eligibility,
    )
    return hash_object(
        {
            "artifact_kind": "transcript_pilot_pcm_analysis_code_v2",
            "source": tuple(inspect.getsource(function) for function in functions),
        }
    )


def _pcm_analysis_runtime_identity_v2() -> PcmAnalysisRuntimeIdentityV2:
    return PcmAnalysisRuntimeIdentityV2(
        python_implementation=platform.python_implementation(),
        python_version=platform.python_version(),
        numpy_version=np.__version__,
        byte_order=sys.byteorder,
    )


def _secret_bytes(value: bytes | None, *, label: str) -> bytes:
    material = secrets.token_bytes(32) if value is None else bytes(value)
    if len(material) < 32:
        raise ValueError(f"{label} must contain at least 256 bits")
    return material


def _selection_index(
    *,
    seed: bytes,
    nonce: bytes,
    context_hash: str,
    stratum_index: int,
    population: int,
) -> int:
    if population < 1:
        raise ValueError("selection population must be positive")
    limit = (1 << 256) // population * population
    counter = 0
    while True:
        message = (
            b"transcript-pilot-v1\x00"
            + nonce
            + bytes.fromhex(context_hash)
            + stratum_index.to_bytes(2, "big")
            + counter.to_bytes(4, "big")
        )
        draw = int.from_bytes(hmac.new(seed, message, hashlib.sha256).digest(), "big")
        if draw < limit:
            return draw % population
        counter += 1


def _build_declaration(
    request: TranscriptPilotRequest,
    wav: _WavInfo,
    *,
    seed: bytes,
    nonce: bytes,
) -> tuple[SamplingDeclarationV1, SelectionSecretV1]:
    margin_frames, clip_frames, eligible = _eligible_strata(
        wav,
        margin_duration_ms=request.margin_duration_ms,
        clip_duration_ms=request.clip_duration_ms,
    )
    public_context = {
        "schema_version": 1,
        "episode_id": request.episode_id,
        "normalized_audio_hash": wav.sha256,
        "normalized_audio_size_bytes": wav.size_bytes,
        "sample_rate_hz": wav.sample_rate_hz,
        "channel_count": wav.channel_count,
        "sample_width_bytes": wav.sample_width_bytes,
        "frame_count": wav.frame_count,
        "selection_algorithm_id": SELECTION_ALGORITHM_ID,
        "margin_duration_ms": request.margin_duration_ms,
        "clip_duration_ms": request.clip_duration_ms,
        "eligible_positions": eligible,
    }
    context_hash = hash_object(
        {"artifact_kind": "transcript_pilot_selection_context", **public_context}
    )
    commitment = hash_object(
        {
            "artifact_kind": "transcript_pilot_selection_seed_commitment",
            "schema_version": 1,
            "selection_context_hash": context_hash,
            "selection_seed_hex": seed.hex(),
            "selection_nonce_hex": nonce.hex(),
        }
    )
    selected: list[SelectedFrameIntervalV1] = []
    for item in eligible:
        offset = _selection_index(
            seed=seed,
            nonce=nonce,
            context_hash=context_hash,
            stratum_index=item.stratum_index,
            population=item.eligible_position_count,
        )
        start_frame = (
            item.first_eligible_start_frame + offset * item.eligible_position_step_frames
        )
        end_frame = start_frame + clip_frames
        selected.append(
            SelectedFrameIntervalV1(
                clip_id=f"clip-{item.stratum_index + 1:03d}",
                stratum_index=item.stratum_index,
                start_frame=start_frame,
                end_frame=end_frame,
                start_ms=start_frame * 1000 // wav.sample_rate_hz,
                end_ms=end_frame * 1000 // wav.sample_rate_hz,
            )
        )
    declaration_payload = {
        "schema_version": 1,
        "episode_id": request.episode_id,
        "normalized_audio_hash": wav.sha256,
        "normalized_audio_size_bytes": wav.size_bytes,
        "wav_compression": "NONE",
        "sample_rate_hz": wav.sample_rate_hz,
        "channel_count": wav.channel_count,
        "sample_width_bytes": wav.sample_width_bytes,
        "frame_count": wav.frame_count,
        "duration_floor_ms": wav.duration_floor_ms,
        "selection_algorithm_id": SELECTION_ALGORITHM_ID,
        "margin_duration_ms": request.margin_duration_ms,
        "margin_frames": margin_frames,
        "clip_duration_ms": request.clip_duration_ms,
        "clip_frames": clip_frames,
        "temporal_strata_count": TEMPORAL_STRATA_COUNT,
        "eligible_positions": eligible,
        "selection_seed_commitment": commitment,
        "selected_intervals": tuple(selected),
    }
    declaration_hash = hash_object(
        {
            "artifact_kind": SamplingDeclarationV1._HASH_KIND,
            **declaration_payload,
        }
    )
    declaration = SamplingDeclarationV1(
        **declaration_payload,
        declaration_hash=declaration_hash,
    )
    secret_payload = {
        "schema_version": 1,
        "episode_id": request.episode_id,
        "selection_context_hash": context_hash,
        "selection_seed_hex": seed.hex(),
        "selection_nonce_hex": nonce.hex(),
        "selection_seed_commitment": commitment,
        "sampling_declaration_hash": declaration.declaration_hash,
    }
    secret = SelectionSecretV1(
        **secret_payload,
        secret_record_hash=hash_object(
            {"artifact_kind": SelectionSecretV1._HASH_KIND, **secret_payload}
        ),
    )
    return declaration, secret


def _selection_index_v2(
    *,
    seed: bytes,
    nonce: bytes,
    context_hash: str,
    stratum_index: int,
    population: int,
) -> int:
    if population < 1:
        raise ValueError("V2 selection population must be positive")
    limit = (1 << 256) // population * population
    counter = 0
    while True:
        message = (
            b"transcript-pilot-silence-aligned-v2\x00"
            + nonce
            + bytes.fromhex(context_hash)
            + stratum_index.to_bytes(2, "big")
            + counter.to_bytes(4, "big")
        )
        draw = int.from_bytes(hmac.new(seed, message, hashlib.sha256).digest(), "big")
        if draw < limit:
            return draw % population
        counter += 1


def _selected_interval_v2(
    *,
    wav: _WavInfo,
    analysis: _V2EnergyAnalysis,
    stratum_index: int,
    eligible_ordinal: int,
    start_frame: int,
) -> SelectedSilenceAlignedIntervalV2:
    grid_frames = analysis.grid_frames
    boundary_frames = analysis.boundary_frames
    clip_frames = analysis.clip_frames
    boundary_cells = boundary_frames // grid_frames
    clip_cells = clip_frames // grid_frames
    start_cell = start_frame // grid_frames
    end_cell = start_cell + clip_cells
    trailing_cell = end_cell - boundary_cells
    leading_sum = sum(
        int(value)
        for value in analysis.grid_sum_squares[start_cell : start_cell + boundary_cells]
    )
    trailing_sum = sum(
        int(value)
        for value in analysis.grid_sum_squares[trailing_cell:end_cell]
    )
    interior_start = start_cell + boundary_cells
    interior_end = trailing_cell
    interior_count = interior_end - interior_start
    interior_active = (
        analysis.active_prefix[interior_end] - analysis.active_prefix[interior_start]
    )
    boundary_sample_count = boundary_frames * wav.channel_count
    end_frame = start_frame + clip_frames
    return SelectedSilenceAlignedIntervalV2(
        clip_id=f"clip-{stratum_index + 1:03d}",
        stratum_index=stratum_index,
        eligible_ordinal=eligible_ordinal,
        start_frame=start_frame,
        end_frame=end_frame,
        start_ms=start_frame * 1000 // wav.sample_rate_hz,
        end_ms=end_frame * 1000 // wav.sample_rate_hz,
        leading_boundary_sum_squares=leading_sum,
        trailing_boundary_sum_squares=trailing_sum,
        boundary_sample_count=boundary_sample_count,
        leading_boundary_rms_ppm_floor=_rms_ppm_floor_v2(
            sum_squares=leading_sum,
            sample_count=boundary_sample_count,
            full_scale_units=analysis.full_scale_units,
        ),
        trailing_boundary_rms_ppm_floor=_rms_ppm_floor_v2(
            sum_squares=trailing_sum,
            sample_count=boundary_sample_count,
            full_scale_units=analysis.full_scale_units,
        ),
        interior_cell_count=interior_count,
        interior_active_cell_count=interior_active,
        interior_active_ratio_ppm_floor=(
            interior_active * _PPM_SCALE // interior_count
        ),
    )


def _build_declaration_v2(
    request: TranscriptPilotRequest,
    wav: _WavInfo,
    *,
    seed: bytes,
    nonce: bytes,
) -> tuple[SamplingDeclarationV2, SelectionSecretV2]:
    if wav.sample_width_bytes not in {1, 2, 3}:
        raise ValueError("silence-aligned V2 supports 8-, 16-, or 24-bit PCM WAV")
    (
        margin_frames,
        clip_frames,
        grid_frames,
        boundary_frames,
        domains,
    ) = _v2_search_domains(
        wav,
        margin_duration_ms=request.margin_duration_ms,
        clip_duration_ms=request.clip_duration_ms,
    )
    analysis = _analyze_v2_eligibility(
        request.normalized_wav_path,
        wav,
        clip_frames=clip_frames,
        grid_frames=grid_frames,
        boundary_frames=boundary_frames,
        domains=domains,
    )
    eligible_strata = tuple(
        EligibleSilenceAlignedStratumV2(
            stratum_index=domain.stratum_index,
            stratum_start_frame=domain.stratum_start_frame,
            stratum_end_frame=domain.stratum_end_frame,
            first_analyzed_start_frame=domain.first_start_frame,
            last_analyzed_start_frame=domain.last_start_frame,
            analyzed_position_step_frames=domain.step_frames,
            analyzed_position_count=(
                (domain.last_start_frame - domain.first_start_frame)
                // domain.step_frames
                + 1
            ),
            eligible_position_count=len(eligible),
        )
        for domain, eligible in zip(domains, analysis.eligible_starts, strict=True)
    )
    runtime_identity = _pcm_analysis_runtime_identity_v2()
    analysis_code_hash = _pcm_analysis_code_hash_v2()
    public_context = {
        "schema_version": 2,
        "selection_policy": SELECTION_POLICY_V2,
        "episode_id": request.episode_id,
        "normalized_audio_hash": wav.sha256,
        "normalized_audio_size_bytes": wav.size_bytes,
        "sample_rate_hz": wav.sample_rate_hz,
        "channel_count": wav.channel_count,
        "sample_width_bytes": wav.sample_width_bytes,
        "full_scale_units": analysis.full_scale_units,
        "frame_count": wav.frame_count,
        "margin_duration_ms": request.margin_duration_ms,
        "clip_duration_ms": request.clip_duration_ms,
        "energy_grid_duration_ms": ENERGY_GRID_DURATION_MS_V2,
        "boundary_window_duration_ms": BOUNDARY_WINDOW_DURATION_MS_V2,
        "boundary_rms_max_ppm": BOUNDARY_RMS_MAX_PPM_V2,
        "interior_active_rms_min_ppm": INTERIOR_ACTIVE_RMS_MIN_PPM_V2,
        "interior_active_ratio_min_ppm": INTERIOR_ACTIVE_RATIO_MIN_PPM_V2,
        "selection_algorithm_id": SELECTION_ALGORITHM_ID_V2,
        "pcm_analysis_code_hash": analysis_code_hash,
        "pcm_analysis_runtime_identity": runtime_identity,
        "eligible_strata": eligible_strata,
    }
    context_hash = hash_object(
        {"artifact_kind": "transcript_pilot_selection_context_v2", **public_context}
    )
    commitment = hash_object(
        {
            "artifact_kind": "transcript_pilot_selection_seed_commitment_v2",
            "schema_version": 2,
            "selection_context_hash": context_hash,
            "selection_seed_hex": seed.hex(),
            "selection_nonce_hex": nonce.hex(),
        }
    )
    selected: list[SelectedSilenceAlignedIntervalV2] = []
    for stratum_index, eligible in enumerate(analysis.eligible_starts):
        ordinal = _selection_index_v2(
            seed=seed,
            nonce=nonce,
            context_hash=context_hash,
            stratum_index=stratum_index,
            population=len(eligible),
        )
        selected.append(
            _selected_interval_v2(
                wav=wav,
                analysis=analysis,
                stratum_index=stratum_index,
                eligible_ordinal=ordinal,
                start_frame=eligible[ordinal],
            )
        )
    declaration_payload = {
        "schema_version": 2,
        "selection_policy": SELECTION_POLICY_V2,
        "episode_id": request.episode_id,
        "normalized_audio_hash": wav.sha256,
        "normalized_audio_size_bytes": wav.size_bytes,
        "wav_compression": "NONE",
        "pcm_sample_encoding": "wav-pcm-u8-or-signed-little-endian-two-complement",
        "pcm_channel_aggregation": "all-channel-samples-equal-weight",
        "rms_gate_arithmetic": "integer-mean-square-cross-multiply-v1",
        "sample_rate_hz": wav.sample_rate_hz,
        "channel_count": wav.channel_count,
        "sample_width_bytes": wav.sample_width_bytes,
        "full_scale_units": analysis.full_scale_units,
        "frame_count": wav.frame_count,
        "duration_floor_ms": wav.duration_floor_ms,
        "selection_algorithm_id": SELECTION_ALGORITHM_ID_V2,
        "margin_duration_ms": request.margin_duration_ms,
        "margin_frames": margin_frames,
        "clip_duration_ms": request.clip_duration_ms,
        "clip_frames": clip_frames,
        "energy_grid_duration_ms": ENERGY_GRID_DURATION_MS_V2,
        "energy_grid_frames": grid_frames,
        "boundary_window_duration_ms": BOUNDARY_WINDOW_DURATION_MS_V2,
        "boundary_window_frames": boundary_frames,
        "boundary_rms_max_ppm": BOUNDARY_RMS_MAX_PPM_V2,
        "interior_active_rms_min_ppm": INTERIOR_ACTIVE_RMS_MIN_PPM_V2,
        "interior_active_ratio_min_ppm": INTERIOR_ACTIVE_RATIO_MIN_PPM_V2,
        "temporal_strata_count": TEMPORAL_STRATA_COUNT,
        "pcm_analysis_code_hash": analysis_code_hash,
        "pcm_analysis_runtime_identity": runtime_identity,
        "eligible_strata": eligible_strata,
        "selection_seed_commitment": commitment,
        "selected_intervals": tuple(selected),
    }
    declaration = SamplingDeclarationV2(
        **declaration_payload,
        declaration_hash=hash_object(
            {"artifact_kind": SamplingDeclarationV2._HASH_KIND, **declaration_payload}
        ),
    )
    secret_payload = {
        "schema_version": 2,
        "selection_policy": SELECTION_POLICY_V2,
        "episode_id": request.episode_id,
        "selection_context_hash": context_hash,
        "selection_seed_hex": seed.hex(),
        "selection_nonce_hex": nonce.hex(),
        "selection_seed_commitment": commitment,
        "sampling_declaration_hash": declaration.declaration_hash,
    }
    secret = SelectionSecretV2(
        **secret_payload,
        secret_record_hash=hash_object(
            {"artifact_kind": SelectionSecretV2._HASH_KIND, **secret_payload}
        ),
    )
    return declaration, secret


def _extract_clip_bytes(
    path: Path,
    wav: _WavInfo,
    interval: SelectedFrameIntervalV1 | SelectedSilenceAlignedIntervalV2,
) -> bytes:
    try:
        with wave.open(str(path), "rb") as reader:
            if (
                reader.getcomptype() != "NONE"
                or reader.getframerate() != wav.sample_rate_hz
                or reader.getnchannels() != wav.channel_count
                or reader.getsampwidth() != wav.sample_width_bytes
                or reader.getnframes() != wav.frame_count
            ):
                raise ValueError("normalized WAV topology changed before clip extraction")
            reader.setpos(interval.start_frame)
            pcm = reader.readframes(interval.end_frame - interval.start_frame)
    except (EOFError, wave.Error) as exc:
        raise ValueError("normalized WAV could not replay selected PCM frames") from exc
    expected_size = (
        (interval.end_frame - interval.start_frame)
        * wav.channel_count
        * wav.sample_width_bytes
    )
    if len(pcm) != expected_size:
        raise ValueError("normalized WAV selected PCM frames are truncated")
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(wav.channel_count)
        writer.setsampwidth(wav.sample_width_bytes)
        writer.setframerate(wav.sample_rate_hz)
        writer.setcomptype("NONE", "not compressed")
        writer.writeframes(pcm)
    return output.getvalue()


def _write_new(path: Path, payload: bytes, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    if private:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _binding(relative_path: str, payload: bytes) -> PilotArtifactBindingV1:
    return PilotArtifactBindingV1(
        relative_path=relative_path,
        sha256=sha256_bytes(payload),
        size_bytes=len(payload),
    )


def _canonical_model(path: Path, model: type[_StrictFrozenModel]) -> _StrictFrozenModel:
    raw = path.read_bytes()
    try:
        value = model.model_validate_json(raw)
    except ValueError as exc:
        raise ValueError(f"invalid {model.__name__} artifact") from exc
    if raw != canonical_json_bytes(value):
        raise ValueError(f"{model.__name__} artifact is not exact canonical JSON")
    return value


def _expected_workspace_files() -> tuple[str, ...]:
    return tuple(
        sorted(
            (
                _DECLARATION_NAME,
                _PACKET_NAME,
                _SECRET_NAME,
                _SUMMARY_NAME,
                *(
                    f"{_CLIP_DIRECTORY}/clip-{index:03d}.wav"
                    for index in range(1, 21)
                ),
            )
        )
    )


def _expected_workspace_files_v2() -> tuple[str, ...]:
    return tuple(
        sorted(
            (
                _DECLARATION_NAME_V2,
                _PACKET_NAME_V2,
                _SECRET_NAME_V2,
                _SUMMARY_NAME_V2,
                *(
                    f"{_CLIP_DIRECTORY_V2}/clip-{index:03d}.wav"
                    for index in range(1, 21)
                ),
            )
        )
    )


def _workspace_file_set(root: Path) -> tuple[str, ...]:
    if not root.is_dir() or _is_reparse_or_link(root):
        raise ValueError("pilot workspace must be a non-link directory")
    files: list[str] = []
    for path in root.rglob("*"):
        if _is_reparse_or_link(path):
            raise ValueError("pilot workspace contains a link or reparse point")
        if path.is_file():
            files.append(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise ValueError("pilot workspace contains a non-regular artifact")
    return tuple(sorted(files))


def _quarantine_prefix(root: Path) -> str:
    return f".{root.name}.transcript-pilot-quarantine-"


def _assert_no_quarantine_residue(root: Path) -> None:
    parent = root.absolute().parent
    if not parent.exists():
        return
    if not parent.is_dir() or _is_reparse_or_link(parent):
        raise ValueError("pilot workspace parent must be a non-link directory")
    prefix = _quarantine_prefix(root)
    if any(path.name.startswith(prefix) for path in parent.iterdir()):
        raise ValueError(
            "pilot pre-publication quarantine residue exists; inspect it before retry"
        )


def _result(
    root: Path,
    declaration: SamplingDeclarationV1,
    secret: SelectionSecretV1,
    packet: TranscriptAnnotationPacket,
    summary: TranscriptPilotSummaryV1,
) -> TranscriptPilotResult:
    return TranscriptPilotResult(
        declaration=declaration,
        secret=secret,
        annotation_packet=packet,
        summary=summary,
        workspace_root=root,
        declaration_path=root / _DECLARATION_NAME,
        secret_path=root / _SECRET_NAME,
        annotation_packet_path=root / _PACKET_NAME,
        summary_path=root / _SUMMARY_NAME,
        clip_paths=tuple(
            root / _CLIP_DIRECTORY / f"clip-{index:03d}.wav" for index in range(1, 21)
        ),
    )


def _result_v2(
    root: Path,
    declaration: SamplingDeclarationV2,
    secret: SelectionSecretV2,
    packet: TranscriptAnnotationPacket,
    summary: TranscriptPilotSummaryV2,
) -> TranscriptPilotResultV2:
    return TranscriptPilotResultV2(
        declaration=declaration,
        secret=secret,
        annotation_packet=packet,
        summary=summary,
        workspace_root=root,
        declaration_path=root / _DECLARATION_NAME_V2,
        secret_path=root / _SECRET_NAME_V2,
        annotation_packet_path=root / _PACKET_NAME_V2,
        summary_path=root / _SUMMARY_NAME_V2,
        clip_paths=tuple(
            root / _CLIP_DIRECTORY_V2 / f"clip-{index:03d}.wav"
            for index in range(1, 21)
        ),
    )


def _verify_existing(
    request: TranscriptPilotRequest,
    wav: _WavInfo,
) -> TranscriptPilotResult:
    root = request.workspace_root
    if _workspace_file_set(root) != _expected_workspace_files():
        raise ValueError("pilot workspace is partial or contains unexpected residue")
    declaration = _canonical_model(
        root / _DECLARATION_NAME, SamplingDeclarationV1
    )
    secret = _canonical_model(root / _SECRET_NAME, SelectionSecretV1)
    summary = _canonical_model(root / _SUMMARY_NAME, TranscriptPilotSummaryV1)
    assert isinstance(declaration, SamplingDeclarationV1)
    assert isinstance(secret, SelectionSecretV1)
    assert isinstance(summary, TranscriptPilotSummaryV1)
    try:
        packet = load_annotation_packet(root / _PACKET_NAME)
    except ValueError as exc:
        raise ValueError("invalid TranscriptAnnotationPacket artifact") from exc
    stored_seed = bytes.fromhex(secret.selection_seed_hex)
    stored_nonce = bytes.fromhex(secret.selection_nonce_hex)
    if request.selection_seed is not None and bytes(request.selection_seed) != stored_seed:
        raise ValueError("pilot replay selection seed conflicts with immutable workspace")
    if request.selection_nonce is not None and bytes(request.selection_nonce) != stored_nonce:
        raise ValueError("pilot replay selection nonce conflicts with immutable workspace")
    expected_declaration, expected_secret = _build_declaration(
        request,
        wav,
        seed=stored_seed,
        nonce=stored_nonce,
    )
    if declaration != expected_declaration or secret != expected_secret:
        raise ValueError("pilot replay declaration, input, configuration, or secret conflicts")
    if (
        packet.packet_id != request.packet_id
        or packet.episode_id != request.episode_id
        or packet.instruction_profile_id != request.instruction_profile_id
        or packet.protocol != request.protocol
        or packet.normalized_audio_hash != wav.sha256
        or packet.normalized_audio_size_bytes != wav.size_bytes
        or packet.sampling_declaration_hash != declaration.declaration_hash
    ):
        raise ValueError("pilot annotation packet conflicts with immutable request")
    artifact_bindings: list[PilotArtifactBindingV1] = []
    for name in (_DECLARATION_NAME, _PACKET_NAME, _SECRET_NAME):
        raw = (root / name).read_bytes()
        artifact_bindings.append(_binding(name, raw))
    if len(packet.clips) != TEMPORAL_STRATA_COUNT:
        raise ValueError("pilot annotation packet does not contain exactly 20 clips")
    for interval, clip_binding in zip(
        declaration.selected_intervals, packet.clips, strict=True
    ):
        expected_clip = _extract_clip_bytes(request.normalized_wav_path, wav, interval)
        relative_path = f"{_CLIP_DIRECTORY}/{interval.clip_id}.wav"
        clip_path = root / relative_path
        if hash_file(clip_path) != sha256_bytes(expected_clip) or clip_path.stat().st_size != len(
            expected_clip
        ):
            raise ValueError("pilot clip differs from fresh exact WAV re-extraction")
        if (
            clip_binding.clip_id != interval.clip_id
            or clip_binding.start_ms != interval.start_ms
            or clip_binding.end_ms != interval.end_ms
            or clip_binding.clip_audio_hash != sha256_bytes(expected_clip)
            or clip_binding.clip_audio_size_bytes != len(expected_clip)
        ):
            raise ValueError("pilot annotation packet clip binding differs from declaration")
        artifact_bindings.append(_binding(relative_path, expected_clip))
    artifact_bindings.sort(key=lambda item: item.relative_path)
    expected_summary_payload = {
        "schema_version": 1,
        "status": "complete",
        "episode_id": request.episode_id,
        "normalized_audio_hash": wav.sha256,
        "sampling_declaration_hash": declaration.declaration_hash,
        "annotation_packet_hash": packet.packet_hash,
        "selection_secret_record_hash": secret.secret_record_hash,
        "clip_count": TEMPORAL_STRATA_COUNT,
        "artifacts": tuple(artifact_bindings),
    }
    expected_summary = TranscriptPilotSummaryV1(
        **expected_summary_payload,
        summary_hash=hash_object(
            {
                "artifact_kind": TranscriptPilotSummaryV1._HASH_KIND,
                **expected_summary_payload,
            }
        ),
    )
    if summary != expected_summary:
        raise ValueError("pilot machine summary differs from exact workspace artifacts")
    if _measure_wav(request.normalized_wav_path) != wav:
        raise ValueError("normalized WAV changed during exact pilot replay")
    _assert_candidate_roots_absent(request)
    return _result(root, declaration, secret, packet, summary)


def _verify_existing_v2(
    request: TranscriptPilotRequest,
    wav: _WavInfo,
) -> TranscriptPilotResultV2:
    root = request.workspace_root
    if _workspace_file_set(root) != _expected_workspace_files_v2():
        raise ValueError("V2 pilot workspace is partial or contains unexpected residue")
    declaration = _canonical_model(root / _DECLARATION_NAME_V2, SamplingDeclarationV2)
    secret = _canonical_model(root / _SECRET_NAME_V2, SelectionSecretV2)
    summary = _canonical_model(root / _SUMMARY_NAME_V2, TranscriptPilotSummaryV2)
    assert isinstance(declaration, SamplingDeclarationV2)
    assert isinstance(secret, SelectionSecretV2)
    assert isinstance(summary, TranscriptPilotSummaryV2)
    try:
        packet = load_annotation_packet(root / _PACKET_NAME_V2)
    except ValueError as exc:
        raise ValueError("invalid V2 TranscriptAnnotationPacket artifact") from exc
    stored_seed = bytes.fromhex(secret.selection_seed_hex)
    stored_nonce = bytes.fromhex(secret.selection_nonce_hex)
    if request.selection_seed is not None and bytes(request.selection_seed) != stored_seed:
        raise ValueError("V2 pilot replay selection seed conflicts with immutable workspace")
    if request.selection_nonce is not None and bytes(request.selection_nonce) != stored_nonce:
        raise ValueError("V2 pilot replay selection nonce conflicts with immutable workspace")
    expected_declaration, expected_secret = _build_declaration_v2(
        request,
        wav,
        seed=stored_seed,
        nonce=stored_nonce,
    )
    if declaration != expected_declaration or secret != expected_secret:
        raise ValueError("V2 pilot replay audio analysis, declaration, or secret conflicts")
    if (
        packet.packet_id != request.packet_id
        or packet.episode_id != request.episode_id
        or packet.instruction_profile_id != request.instruction_profile_id
        or packet.protocol != request.protocol
        or packet.normalized_audio_hash != wav.sha256
        or packet.normalized_audio_size_bytes != wav.size_bytes
        or packet.sampling_declaration_hash != declaration.declaration_hash
    ):
        raise ValueError("V2 pilot annotation packet conflicts with immutable request")
    artifact_bindings: list[PilotArtifactBindingV1] = []
    for name in (_DECLARATION_NAME_V2, _PACKET_NAME_V2, _SECRET_NAME_V2):
        raw = (root / name).read_bytes()
        artifact_bindings.append(_binding(name, raw))
    if len(packet.clips) != TEMPORAL_STRATA_COUNT:
        raise ValueError("V2 annotation packet does not contain exactly 20 clips")
    for interval, clip_binding in zip(
        declaration.selected_intervals,
        packet.clips,
        strict=True,
    ):
        expected_clip = _extract_clip_bytes(request.normalized_wav_path, wav, interval)
        relative_path = f"{_CLIP_DIRECTORY_V2}/{interval.clip_id}.wav"
        clip_path = root / relative_path
        if hash_file(clip_path) != sha256_bytes(expected_clip) or clip_path.stat().st_size != len(
            expected_clip
        ):
            raise ValueError("V2 pilot clip differs from fresh exact WAV re-extraction")
        if (
            clip_binding.clip_id != interval.clip_id
            or clip_binding.start_ms != interval.start_ms
            or clip_binding.end_ms != interval.end_ms
            or clip_binding.clip_audio_hash != sha256_bytes(expected_clip)
            or clip_binding.clip_audio_size_bytes != len(expected_clip)
        ):
            raise ValueError("V2 packet clip binding differs from silence-aligned declaration")
        artifact_bindings.append(_binding(relative_path, expected_clip))
    artifact_bindings.sort(key=lambda item: item.relative_path)
    expected_summary_payload = {
        "schema_version": 2,
        "selection_policy": SELECTION_POLICY_V2,
        "status": "complete",
        "episode_id": request.episode_id,
        "normalized_audio_hash": wav.sha256,
        "sampling_declaration_hash": declaration.declaration_hash,
        "annotation_packet_hash": packet.packet_hash,
        "selection_secret_record_hash": secret.secret_record_hash,
        "clip_count": TEMPORAL_STRATA_COUNT,
        "artifacts": tuple(artifact_bindings),
    }
    expected_summary = TranscriptPilotSummaryV2(
        **expected_summary_payload,
        summary_hash=hash_object(
            {
                "artifact_kind": TranscriptPilotSummaryV2._HASH_KIND,
                **expected_summary_payload,
            }
        ),
    )
    if summary != expected_summary:
        raise ValueError("V2 machine summary differs from exact workspace artifacts")
    if _measure_wav(request.normalized_wav_path) != wav:
        raise ValueError("normalized WAV changed during exact V2 pilot replay")
    _assert_candidate_roots_absent(request)
    return _result_v2(root, declaration, secret, packet, summary)


def _build_transcript_pilot_v1(request: TranscriptPilotRequest) -> TranscriptPilotResult:
    """Build or exactly replay one immutable candidate-free pilot workspace."""

    _require_identifier("episode_id", request.episode_id)
    _require_identifier("packet_id", request.packet_id)
    _require_identifier("instruction_profile_id", request.instruction_profile_id)
    _assert_candidate_roots_absent(request)
    _assert_no_link_ancestors(request.workspace_root)
    _assert_no_quarantine_residue(request.workspace_root)
    wav = _measure_wav(request.normalized_wav_path)
    _validate_expected_wav(request, wav)
    if request.workspace_root.exists():
        return _verify_existing(request, wav)

    seed = _secret_bytes(request.selection_seed, label="selection_seed")
    nonce = _secret_bytes(request.selection_nonce, label="selection_nonce")
    declaration, secret = _build_declaration(
        request,
        wav,
        seed=seed,
        nonce=nonce,
    )
    parent = request.workspace_root.absolute().parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=_quarantine_prefix(request.workspace_root), dir=parent)
    )
    try:
        declaration_bytes = declaration.canonical_bytes()
        secret_bytes = secret.canonical_bytes()
        _write_new(temporary / _DECLARATION_NAME, declaration_bytes)
        _write_new(temporary / _SECRET_NAME, secret_bytes, private=True)
        bindings: list[AudioClipBinding] = []
        artifact_bindings = [
            _binding(_DECLARATION_NAME, declaration_bytes),
            _binding(_SECRET_NAME, secret_bytes),
        ]
        for interval in declaration.selected_intervals:
            clip_bytes = _extract_clip_bytes(request.normalized_wav_path, wav, interval)
            relative_path = f"{_CLIP_DIRECTORY}/{interval.clip_id}.wav"
            _write_new(temporary / relative_path, clip_bytes)
            artifact_bindings.append(_binding(relative_path, clip_bytes))
            bindings.append(
                AudioClipBinding.build(
                    clip_id=interval.clip_id,
                    start_ms=interval.start_ms,
                    end_ms=interval.end_ms,
                    clip_audio_hash=sha256_bytes(clip_bytes),
                    clip_audio_size_bytes=len(clip_bytes),
                    normalized_audio_hash=wav.sha256,
                    normalized_audio_size_bytes=wav.size_bytes,
                )
            )
        packet = TranscriptAnnotationPacket.build(
            packet_id=request.packet_id,
            episode_id=request.episode_id,
            normalized_audio_hash=wav.sha256,
            normalized_audio_size_bytes=wav.size_bytes,
            sampling_declaration_hash=declaration.declaration_hash,
            instruction_profile_id=request.instruction_profile_id,
            protocol=request.protocol,
            clips=tuple(bindings),
        )
        packet_bytes = packet.canonical_bytes()
        _write_new(temporary / _PACKET_NAME, packet_bytes)
        artifact_bindings.append(_binding(_PACKET_NAME, packet_bytes))
        artifact_bindings.sort(key=lambda item: item.relative_path)
        summary_payload = {
            "schema_version": 1,
            "status": "complete",
            "episode_id": request.episode_id,
            "normalized_audio_hash": wav.sha256,
            "sampling_declaration_hash": declaration.declaration_hash,
            "annotation_packet_hash": packet.packet_hash,
            "selection_secret_record_hash": secret.secret_record_hash,
            "clip_count": TEMPORAL_STRATA_COUNT,
            "artifacts": tuple(artifact_bindings),
        }
        summary = TranscriptPilotSummaryV1(
            **summary_payload,
            summary_hash=hash_object(
                {
                    "artifact_kind": TranscriptPilotSummaryV1._HASH_KIND,
                    **summary_payload,
                }
            ),
        )
        _write_new(temporary / _SUMMARY_NAME, summary.canonical_bytes())
        if _workspace_file_set(temporary) != _expected_workspace_files():
            raise ValueError("new pilot workspace is incomplete before publication")
        if _measure_wav(request.normalized_wav_path) != wav:
            raise ValueError("normalized WAV changed during pilot clip extraction")
        _assert_candidate_roots_absent(request)
        if request.workspace_root.exists():
            raise ValueError("pilot workspace appeared during immutable publication")
        os.replace(temporary, request.workspace_root)
    except Exception as exc:
        if temporary.exists():
            raise ValueError(
                "pilot pre-publication failed; forensic quarantine residue preserved at "
                f"{temporary}"
            ) from exc
        raise
    return _verify_existing(request, wav)


def _build_transcript_pilot_v2(request: TranscriptPilotRequest) -> TranscriptPilotResultV2:
    """Build or exactly replay one silence-aligned candidate-free V2 workspace."""

    _require_identifier("episode_id", request.episode_id)
    _require_identifier("packet_id", request.packet_id)
    _require_identifier("instruction_profile_id", request.instruction_profile_id)
    _assert_candidate_roots_absent(request)
    _assert_no_link_ancestors(request.workspace_root)
    _assert_no_quarantine_residue(request.workspace_root)
    wav = _measure_wav(request.normalized_wav_path)
    _validate_expected_wav(request, wav)
    if request.workspace_root.exists():
        return _verify_existing_v2(request, wav)

    seed = _secret_bytes(request.selection_seed, label="selection_seed")
    nonce = _secret_bytes(request.selection_nonce, label="selection_nonce")
    declaration, secret = _build_declaration_v2(
        request,
        wav,
        seed=seed,
        nonce=nonce,
    )
    parent = request.workspace_root.absolute().parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=_quarantine_prefix(request.workspace_root), dir=parent)
    )
    try:
        declaration_bytes = declaration.canonical_bytes()
        secret_bytes = secret.canonical_bytes()
        _write_new(temporary / _DECLARATION_NAME_V2, declaration_bytes)
        _write_new(temporary / _SECRET_NAME_V2, secret_bytes, private=True)
        bindings: list[AudioClipBinding] = []
        artifact_bindings = [
            _binding(_DECLARATION_NAME_V2, declaration_bytes),
            _binding(_SECRET_NAME_V2, secret_bytes),
        ]
        for interval in declaration.selected_intervals:
            clip_bytes = _extract_clip_bytes(request.normalized_wav_path, wav, interval)
            relative_path = f"{_CLIP_DIRECTORY_V2}/{interval.clip_id}.wav"
            _write_new(temporary / relative_path, clip_bytes)
            artifact_bindings.append(_binding(relative_path, clip_bytes))
            bindings.append(
                AudioClipBinding.build(
                    clip_id=interval.clip_id,
                    start_ms=interval.start_ms,
                    end_ms=interval.end_ms,
                    clip_audio_hash=sha256_bytes(clip_bytes),
                    clip_audio_size_bytes=len(clip_bytes),
                    normalized_audio_hash=wav.sha256,
                    normalized_audio_size_bytes=wav.size_bytes,
                )
            )
        packet = TranscriptAnnotationPacket.build(
            packet_id=request.packet_id,
            episode_id=request.episode_id,
            normalized_audio_hash=wav.sha256,
            normalized_audio_size_bytes=wav.size_bytes,
            sampling_declaration_hash=declaration.declaration_hash,
            instruction_profile_id=request.instruction_profile_id,
            protocol=request.protocol,
            clips=tuple(bindings),
        )
        packet_bytes = packet.canonical_bytes()
        _write_new(temporary / _PACKET_NAME_V2, packet_bytes)
        artifact_bindings.append(_binding(_PACKET_NAME_V2, packet_bytes))
        artifact_bindings.sort(key=lambda item: item.relative_path)
        summary_payload = {
            "schema_version": 2,
            "selection_policy": SELECTION_POLICY_V2,
            "status": "complete",
            "episode_id": request.episode_id,
            "normalized_audio_hash": wav.sha256,
            "sampling_declaration_hash": declaration.declaration_hash,
            "annotation_packet_hash": packet.packet_hash,
            "selection_secret_record_hash": secret.secret_record_hash,
            "clip_count": TEMPORAL_STRATA_COUNT,
            "artifacts": tuple(artifact_bindings),
        }
        summary = TranscriptPilotSummaryV2(
            **summary_payload,
            summary_hash=hash_object(
                {
                    "artifact_kind": TranscriptPilotSummaryV2._HASH_KIND,
                    **summary_payload,
                }
            ),
        )
        _write_new(temporary / _SUMMARY_NAME_V2, summary.canonical_bytes())
        if _workspace_file_set(temporary) != _expected_workspace_files_v2():
            raise ValueError("new V2 pilot workspace is incomplete before publication")
        if _measure_wav(request.normalized_wav_path) != wav:
            raise ValueError("normalized WAV changed during V2 energy analysis or extraction")
        _assert_candidate_roots_absent(request)
        if request.workspace_root.exists():
            raise ValueError("V2 pilot workspace appeared during immutable publication")
        os.replace(temporary, request.workspace_root)
    except Exception as exc:
        if temporary.exists():
            raise ValueError(
                "V2 pilot pre-publication failed; forensic quarantine residue preserved at "
                f"{temporary}"
            ) from exc
        raise
    return _verify_existing_v2(request, wav)


def build_transcript_pilot(request: TranscriptPilotRequest) -> TranscriptPilotResultAny:
    """Dispatch one explicit immutable candidate-free sampling policy."""

    if request.selection_policy == SELECTION_POLICY_V1:
        return _build_transcript_pilot_v1(request)
    if request.selection_policy == SELECTION_POLICY_V2:
        return _build_transcript_pilot_v2(request)
    raise ValueError(f"unsupported transcript pilot selection policy: {request.selection_policy}")


__all__ = [
    "BOUNDARY_RMS_MAX_PPM_V2",
    "BOUNDARY_WINDOW_DURATION_MS_V2",
    "EligibleSilenceAlignedStratumV2",
    "EligibleFrameStratumV1",
    "ENERGY_GRID_DURATION_MS_V2",
    "INTERIOR_ACTIVE_RATIO_MIN_PPM_V2",
    "INTERIOR_ACTIVE_RMS_MIN_PPM_V2",
    "PcmAnalysisRuntimeIdentityV2",
    "PilotArtifactBindingV1",
    "SELECTION_ALGORITHM_ID",
    "SELECTION_ALGORITHM_ID_V2",
    "SELECTION_POLICY_V1",
    "SELECTION_POLICY_V2",
    "SamplingDeclarationV1",
    "SamplingDeclarationV2",
    "SelectedFrameIntervalV1",
    "SelectedSilenceAlignedIntervalV2",
    "SelectionSecretV1",
    "SelectionSecretV2",
    "TEMPORAL_STRATA_COUNT",
    "TranscriptPilotRequest",
    "TranscriptPilotResult",
    "TranscriptPilotResultAny",
    "TranscriptPilotResultV2",
    "TranscriptPilotSummaryV1",
    "TranscriptPilotSummaryV2",
    "build_transcript_pilot",
]
