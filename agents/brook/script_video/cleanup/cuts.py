"""CutPoint — a region of source audio to remove by ripple delete."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class CutPoint:
    """A contiguous audio region removed from the A-roll by ripple delete.

    Both ``start_sec`` and ``end_sec`` refer to positions in the *original*
    (uncleaned) source recording.  After all cuts are applied, the remaining
    segments are concatenated and their lengths determine FCPXML clip offsets.
    """

    type: Literal["razor", "ripple-delete"]
    start_sec: float
    end_sec: float
    reason: Literal["marker", "alignment-detected"]
    confidence: float  # 0.0 .. 1.0
