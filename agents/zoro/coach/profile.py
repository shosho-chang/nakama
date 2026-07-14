"""Coach profile + training landmarks for WP2.

Defaults match the locked v2 decisions: goal = balanced, training_status =
intermediate (double progression + 2-for-2). Weekly hard-set landmarks
(MEV/MAV/MRV) are RP-style intermediate defaults — tunable later per WP2 §inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

# muscle -> (MEV, MAV, MRV) weekly hard sets (intermediate defaults).
_LANDMARKS = {
    "chest": (8, 14, 20),
    "back": (10, 16, 22),
    "shoulders": (8, 16, 22),
    "biceps": (6, 12, 18),
    "triceps": (6, 12, 18),
    "traps": (4, 12, 16),
    "quads": (8, 14, 20),
    "hamstrings": (6, 12, 16),
    "glutes": (4, 12, 16),
    "calves": (8, 14, 20),
    "posterior_chain": (6, 12, 16),
    "core": (0, 12, 25),
}

_LOWER_BODY = {"quads", "hamstrings", "glutes", "calves", "posterior_chain"}


@dataclass(frozen=True)
class CoachProfile:
    goal: str = "balanced"
    training_status: str = "intermediate"
    rep_range: tuple[int, int] = (8, 12)   # hypertrophy default (ACSM 2026 容量框架)
    upper_increment_kg: float = 2.5
    lower_increment_kg: float = 5.0

    def landmarks(self, muscle: str) -> tuple[int, int, int]:
        return _LANDMARKS.get(muscle, (8, 14, 20))

    def increment_kg(self, muscle: str) -> float:
        return self.lower_increment_kg if muscle in _LOWER_BODY else self.upper_increment_kg
