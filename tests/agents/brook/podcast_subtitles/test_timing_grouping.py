from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agents.brook.podcast_subtitles.adapters.timing_grouping import (
    FORCED_ALIGNMENT_GROUPING_POLICY,
    TimedObservation,
    TimedObservationGroupingReceipt,
    build_timed_observation_grouping,
)


def test_zero_duration_observations_are_bounded_by_the_next_real_interval() -> None:
    receipt = build_timed_observation_grouping(
        (
            TimedObservation(index=0, text="首", start_seconds=0.0, end_seconds=0.0),
            TimedObservation(index=1, text="連", start_seconds=0.0, end_seconds=0.0),
            TimedObservation(index=2, text="正", start_seconds=0.08, end_seconds=0.16),
        ),
        policy=FORCED_ALIGNMENT_GROUPING_POLICY,
    )

    assert receipt.groups[0].text == "首連正"
    assert receipt.groups[0].source_observation_indices == (0, 1, 2)
    assert (receipt.groups[0].start_seconds, receipt.groups[0].end_seconds) == (0.0, 0.16)
    assert receipt.groups[0].timing_basis == "forced_alignment_bounded_group"
    assert receipt.observations == (
        TimedObservation(index=0, text="首", start_seconds=0.0, end_seconds=0.0),
        TimedObservation(index=1, text="連", start_seconds=0.0, end_seconds=0.0),
        TimedObservation(index=2, text="正", start_seconds=0.08, end_seconds=0.16),
    )


def test_negative_duration_observation_is_rejected() -> None:
    with pytest.raises(ValidationError, match="negative duration"):
        TimedObservation(index=0, text="錯", start_seconds=0.2, end_seconds=0.1)


def test_overlapping_or_backwards_observation_sequence_is_rejected() -> None:
    observations = (
        TimedObservation(index=0, text="前", start_seconds=0.0, end_seconds=0.2),
        TimedObservation(index=1, text="後", start_seconds=0.1, end_seconds=0.3),
    )

    with pytest.raises(ValueError, match="overlap or move backwards"):
        build_timed_observation_grouping(
            observations,
            policy=FORCED_ALIGNMENT_GROUPING_POLICY,
        )


def test_trailing_zero_duration_observations_attach_to_the_prior_anchor() -> None:
    receipt = build_timed_observation_grouping(
        (
            TimedObservation(index=0, text="正", start_seconds=0.08, end_seconds=0.16),
            TimedObservation(index=1, text="尾", start_seconds=0.16, end_seconds=0.16),
            TimedObservation(index=2, text="末", start_seconds=0.24, end_seconds=0.24),
        ),
        policy=FORCED_ALIGNMENT_GROUPING_POLICY,
    )

    assert receipt.groups[0].text == "正尾末"
    assert receipt.groups[0].source_observation_indices == (0, 1, 2)
    assert (receipt.groups[0].start_seconds, receipt.groups[0].end_seconds) == (0.08, 0.24)


def test_grouping_receipt_rejects_a_tampered_partition_on_replay() -> None:
    receipt = build_timed_observation_grouping(
        (
            TimedObservation(index=0, text="甲", start_seconds=0.08, end_seconds=0.16),
            TimedObservation(index=1, text="一", start_seconds=0.16, end_seconds=0.16),
            TimedObservation(index=2, text="乙", start_seconds=0.24, end_seconds=0.32),
        ),
        policy=FORCED_ALIGNMENT_GROUPING_POLICY,
    )
    payload = receipt.model_dump(mode="json")
    payload["groups"][1]["source_observation_indices"] = [2]

    with pytest.raises(ValidationError, match="do not replay deterministically"):
        TimedObservationGroupingReceipt.model_validate_json(json.dumps(payload))


def test_observation_indices_must_match_their_ordered_tuple_positions() -> None:
    observations = (TimedObservation(index=1, text="甲", start_seconds=0.0, end_seconds=0.1),)

    with pytest.raises(ValueError, match="ordered and contiguous"):
        build_timed_observation_grouping(
            observations,
            policy=FORCED_ALIGNMENT_GROUPING_POLICY,
        )
