"""Replayable grouping for provider timestamps that contain point observations.

Recognition providers sometimes attach text to ``start == end`` timestamps.
The point observations remain immutable raw Evidence.  This module projects an
ordered observation stream into positive-duration groups by attaching points to
an adjacent observed interval; it never fabricates an epsilon timestamp.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..hashing import hash_object

TimingGroupingMethod = Literal[
    "qwen_forced_aligner_bounded_zero_duration_groups_v1",
    "faster_whisper_word_timestamps_bounded_zero_duration_groups_v1",
]
TimingBasis = Literal[
    "forced_alignment_exact",
    "forced_alignment_bounded_group",
    "provider_word_timestamp_exact",
    "provider_word_timestamp_bounded_group",
]


@dataclass(frozen=True, slots=True)
class TimingGroupingPolicy:
    method: TimingGroupingMethod
    exact_timing_basis: TimingBasis
    grouped_timing_basis: TimingBasis


FORCED_ALIGNMENT_GROUPING_POLICY = TimingGroupingPolicy(
    method="qwen_forced_aligner_bounded_zero_duration_groups_v1",
    exact_timing_basis="forced_alignment_exact",
    grouped_timing_basis="forced_alignment_bounded_group",
)
FASTER_WHISPER_GROUPING_POLICY = TimingGroupingPolicy(
    method="faster_whisper_word_timestamps_bounded_zero_duration_groups_v1",
    exact_timing_basis="provider_word_timestamp_exact",
    grouped_timing_basis="provider_word_timestamp_bounded_group",
)
_POLICY_BY_METHOD = {
    policy.method: policy
    for policy in (FORCED_ALIGNMENT_GROUPING_POLICY, FASTER_WHISPER_GROUPING_POLICY)
}


class _TimingContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TimedObservation(_TimingContract):
    index: int = Field(ge=0)
    text: str = Field(min_length=1)
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _nonnegative_duration(self) -> TimedObservation:
        if self.end_seconds < self.start_seconds:
            raise ValueError("timestamp observation has negative duration")
        return self


class TimedObservationGroup(_TimingContract):
    index: int = Field(ge=0)
    source_observation_indices: tuple[int, ...] = Field(min_length=1)
    text: str = Field(min_length=1)
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)
    timing_basis: TimingBasis

    @model_validator(mode="after")
    def _positive_duration(self) -> TimedObservationGroup:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("timestamp group requires a positive outer interval")
        return self


def _partition_indices(observations: Sequence[TimedObservation]) -> tuple[tuple[int, ...], ...]:
    if not observations:
        raise ValueError("timestamp grouping requires observations")
    if tuple(item.index for item in observations) != tuple(range(len(observations))):
        raise ValueError("timestamp observation indices must be ordered and contiguous")

    previous_end = 0.0
    source_groups: list[tuple[int, ...]] = []
    pending_points: list[int] = []
    for position, observation in enumerate(observations):
        if observation.start_seconds < previous_end:
            raise ValueError("timestamp observations overlap or move backwards")
        previous_end = observation.end_seconds
        if observation.end_seconds == observation.start_seconds:
            pending_points.append(position)
            continue
        source_groups.append(tuple((*pending_points, position)))
        pending_points.clear()
    if pending_points:
        if not source_groups:
            raise ValueError("timestamp observations have no positive-duration anchor")
        source_groups[-1] = tuple((*source_groups[-1], *pending_points))
    return tuple(source_groups)


class TimedObservationGroupingReceipt(_TimingContract):
    schema_version: Literal[1]
    method: TimingGroupingMethod
    observations: tuple[TimedObservation, ...] = Field(min_length=1)
    groups: tuple[TimedObservationGroup, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _exact_deterministic_partition(self) -> TimedObservationGroupingReceipt:
        policy = _POLICY_BY_METHOD[self.method]
        expected_source_groups = _partition_indices(self.observations)
        if tuple(group.index for group in self.groups) != tuple(range(len(self.groups))):
            raise ValueError("timestamp group indices must be ordered and contiguous")
        if tuple(group.source_observation_indices for group in self.groups) != (
            expected_source_groups
        ):
            raise ValueError("timestamp groups do not replay deterministically")

        for group in self.groups:
            source = tuple(self.observations[index] for index in group.source_observation_indices)
            expected_basis = (
                policy.exact_timing_basis
                if len(source) == 1 and source[0].end_seconds > source[0].start_seconds
                else policy.grouped_timing_basis
            )
            if (
                group.text != "".join(item.text for item in source)
                or group.start_seconds != min(item.start_seconds for item in source)
                or group.end_seconds != max(item.end_seconds for item in source)
                or group.timing_basis != expected_basis
            ):
                raise ValueError("timestamp group differs from its raw observations")
        if "".join(group.text for group in self.groups) != "".join(
            item.text for item in self.observations
        ):
            raise ValueError("timestamp grouping did not preserve exact source text")
        return self

    @property
    def content_hash(self) -> str:
        """Canonical identity of the complete replayable receipt."""

        return hash_object(self.model_dump(mode="json"))


def build_timed_observation_grouping(
    observations: Sequence[TimedObservation],
    *,
    policy: TimingGroupingPolicy,
) -> TimedObservationGroupingReceipt:
    """Build the only valid grouping for an ordered raw observation stream."""

    if policy not in _POLICY_BY_METHOD.values():
        raise ValueError("timestamp grouping policy is not a closed production policy")
    typed = tuple(observations)
    source_groups = _partition_indices(typed)
    groups = tuple(
        TimedObservationGroup(
            index=index,
            source_observation_indices=source_indices,
            text="".join(typed[source_index].text for source_index in source_indices),
            start_seconds=min(typed[source_index].start_seconds for source_index in source_indices),
            end_seconds=max(typed[source_index].end_seconds for source_index in source_indices),
            timing_basis=(
                policy.exact_timing_basis
                if len(source_indices) == 1
                else policy.grouped_timing_basis
            ),
        )
        for index, source_indices in enumerate(source_groups)
    )
    return TimedObservationGroupingReceipt(
        schema_version=1,
        method=policy.method,
        observations=typed,
        groups=groups,
    )


__all__ = [
    "FASTER_WHISPER_GROUPING_POLICY",
    "FORCED_ALIGNMENT_GROUPING_POLICY",
    "TimedObservation",
    "TimedObservationGroup",
    "TimedObservationGroupingReceipt",
    "TimingGroupingPolicy",
    "build_timed_observation_grouping",
]
