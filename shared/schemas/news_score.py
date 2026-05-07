"""Franky 5-dim news score schema（ADR-023 §7 S2b）。

Pydantic V2 contract for the JSON object returned by the news_score.md prompt
and consumed by agents/franky/news_digest.py._score().

Shadow mode adds a 5th dimension (Relevance) while keeping the legacy 4-dim
pick gate in overall_4dim so retrospective analysis can compare both.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NewsScoreDimsV1(BaseModel):
    """Four legacy scoring dimensions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal: float = Field(ge=1, le=5)
    novelty: float = Field(ge=1, le=5)
    actionability: float = Field(ge=1, le=5)
    noise: float = Field(ge=1, le=5)


class NewsScoreDimsV2(BaseModel):
    """Five-dim scoring dimensions (S2b shadow mode)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal: float = Field(ge=1, le=5)
    novelty: float = Field(ge=1, le=5)
    actionability: float = Field(ge=1, le=5)
    noise: float = Field(ge=1, le=5)
    relevance: float = Field(ge=1, le=5)


class NewsScoreResultV2(BaseModel):
    """Full score result returned by news_score.md prompt (5-dim shadow mode).

    ``overall`` is the 5-dim weighted average.
    ``overall_4dim`` is the legacy 4-dim average used for the pick gate.
    ``pick`` is set by Python based on shadow gate:
        overall_4dim >= 3.5 AND signal >= 3 AND relevance >= 2.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    schema_version: Literal[2] = 2
    scores: NewsScoreDimsV2
    overall: float = Field(ge=0)  # 5-dim overall
    overall_4dim: float = Field(ge=0)  # 4-dim legacy, pick gate base
    relevance_ref: str | None = None  # ADR-N or #issue cited (if relevance ≥ 3)
    one_line_verdict: str
    why_it_matters: str
    key_finding: str
    noise_note: str
    pick: bool
