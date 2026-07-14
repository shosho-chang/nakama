"""Coach (Zoro fitness) HITL payload sub-schemas.

Mirrors shared/schemas/publishing.py's role for Brook: a serializable compliance
summary + the workout step model that travel inside an approval payload. The WP3
guardrail produces the compliance summary; it is re-checkable at claim time
(defense in depth), matching the PublishComplianceGateV1 pattern.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CoachComplianceV1(BaseModel):
    """Flattened guardrail result carried with a coach approval payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    guardrail_ok: bool
    blocks: list[str] = Field(default_factory=list)
    warns: list[str] = Field(default_factory=list)


class StrengthBuilderStepV1(BaseModel):
    """One Garmin Strength Builder step (the unit the user one-taps to create)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    order: int
    exercise_key: str
    category: str
    sets: int
    reps: str
    target_weight_kg: float | None = None
    rest_sec: int
    is_warmup: bool = False
