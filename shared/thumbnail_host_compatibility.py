"""Host cutout compatibility gates for template-first thumbnails.

The renderer should not place a technically valid cutout into a template when
the pose contradicts the reference. This module checks the cutout pose tags
against one concrete reference deconstruction record before arrangement/render.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from shared.cutout_casting import CutoutCastRequest


@dataclass(frozen=True)
class HostCompatibilityResult:
    ok: bool
    score: float
    reasons: tuple[str, ...]
    repair_hints: tuple[str, ...]
    needs_new_photo: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "score": round(self.score, 2),
            "reasons": list(self.reasons),
            "repair_hints": list(self.repair_hints),
            "needs_new_photo": self.needs_new_photo,
        }


def build_cast_request_for_reference(
    reference_record: Mapping[str, Any],
    *,
    limit: int = 8,
) -> CutoutCastRequest:
    """Build pose-manifest criteria from a concrete reference record."""

    host = reference_record["host"]
    if not host.get("present"):
        return CutoutCastRequest(limit=limit)

    expression_families = tuple(
        _EXPRESSION_TO_FAMILIES.get(str(host.get("expression")), ("soft_smile", "explain"))
    )
    hands = tuple(_GESTURE_TO_HANDS.get(str(host.get("gesture")), ()))
    gazes = tuple(_GAZE_TO_TAGS.get(str(host.get("gaze")), ()))
    use_contexts = _use_contexts(reference_record)

    return CutoutCastRequest(
        expression_families=expression_families,
        use_contexts=use_contexts,
        hands=hands,
        gazes=gazes,
        max_intensity="medium",
        min_credibility="medium",
        limit=limit,
    )


def check_host_compatibility(
    *,
    reference_record: Mapping[str, Any],
    cutout_candidate: Any,
    minimum_score: float = 70.0,
) -> HostCompatibilityResult:
    """Check whether a cutout candidate can safely fit a reference template."""

    if not bool(reference_record.get("renderable_v1")):
        return HostCompatibilityResult(
            ok=False,
            score=0.0,
            reasons=("reference_not_renderable",),
            repair_hints=("choose a renderable_v1 reference family",),
            needs_new_photo=False,
        )

    host = reference_record["host"]
    if not host.get("present"):
        return HostCompatibilityResult(
            ok=True,
            score=100.0,
            reasons=("reference_has_no_host",),
            repair_hints=(),
            needs_new_photo=False,
        )

    tags = _candidate_tags(cutout_candidate)
    policy = _candidate_value(cutout_candidate, "picker_policy", "eligible")
    reasons: list[str] = []
    repairs: list[str] = []
    hard_failures: list[str] = []
    score = 100.0

    if policy == "manual_only":
        hard_failures.append("candidate_is_manual_only")
        repairs.append("choose an eligible cutout")

    placement = str(host.get("placement") or "")
    expected_gaze = str(host.get("gaze") or "")
    actual_gaze = str(tags.get("gaze") or "")
    if not _gaze_compatible(expected_gaze, actual_gaze, placement=placement):
        hard_failures.append(f"gaze_mismatch:{actual_gaze or 'missing'}")
        repairs.append(_gaze_repair_hint(expected_gaze, placement))
    else:
        reasons.append(f"gaze_ok:{actual_gaze or 'unknown'}")
        if actual_gaze == "camera" and expected_gaze in {"left", "right"}:
            score -= 6
            reasons.append("camera_gaze_tolerated_but_less_interactive")

    expected_hands = _GESTURE_TO_HANDS.get(str(host.get("gesture")), ())
    actual_hands = str(tags.get("hands") or "")
    if expected_hands and actual_hands not in expected_hands:
        hard_failures.append(f"gesture_mismatch:{actual_hands or 'missing'}")
        repairs.append(f"choose cutout with hands in {', '.join(expected_hands)}")
    elif expected_hands:
        reasons.append(f"gesture_ok:{actual_hands}")

    expected_expressions = _EXPRESSION_TO_FAMILIES.get(str(host.get("expression")), ())
    actual_expression = str(tags.get("expression_family") or "")
    if expected_expressions and actual_expression not in expected_expressions:
        score -= 14
        reasons.append(f"expression_soft_mismatch:{actual_expression or 'missing'}")
        repairs.append(f"prefer expression_family in {', '.join(expected_expressions)}")
    elif expected_expressions:
        reasons.append(f"expression_ok:{actual_expression}")

    intensity = str(tags.get("intensity") or "medium")
    if intensity in {"high", "extreme"}:
        score -= 20 if intensity == "high" else 35
        reasons.append(f"intensity_too_loud:{intensity}")
        repairs.append("choose a subtle, mild, or medium intensity cutout")

    credibility = str(tags.get("credibility") or "medium")
    if _needs_high_credibility(reference_record) and credibility != "high":
        score -= 16
        reasons.append(f"credibility_below_reference_need:{credibility}")
        repairs.append("choose a high-credibility health/evidence cutout")

    face_ratio = _candidate_face_height_ratio(cutout_candidate)
    min_face = float(reference_record["generator_constraints"]["minimum_face_height_ratio"])
    if face_ratio is not None and face_ratio < min_face:
        hard_failures.append(f"face_too_small:{face_ratio:.2f}<target:{min_face:.2f}")
        repairs.append("use a larger crop or choose a tighter face/torso cutout")
    elif face_ratio is not None:
        reasons.append(f"face_size_ok:{face_ratio:.2f}")

    if hard_failures:
        reasons = [*hard_failures, *reasons]
        return HostCompatibilityResult(
            ok=False,
            score=max(0.0, min(score, 55.0)),
            reasons=tuple(dict.fromkeys(reasons)),
            repair_hints=tuple(dict.fromkeys(repairs)),
            needs_new_photo=True,
        )

    ok = score >= minimum_score
    return HostCompatibilityResult(
        ok=ok,
        score=max(0.0, score),
        reasons=tuple(dict.fromkeys(reasons or ["passes_host_compatibility_gate"])),
        repair_hints=tuple(dict.fromkeys(repairs)),
        needs_new_photo=not ok,
    )


def _candidate_tags(candidate: Any) -> dict[str, str]:
    value = _candidate_value(candidate, "tags", {}) or {}
    if isinstance(value, Mapping):
        return {str(key): str(item) for key, item in value.items()}
    return {}


def _candidate_face_height_ratio(candidate: Any) -> float | None:
    value = _candidate_value(candidate, "face_height_ratio", None)
    if value is None:
        value = _candidate_value(candidate, "face_height", None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _candidate_value(candidate: Any, key: str, default: Any) -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(key, default)
    return getattr(candidate, key, default)


def _gaze_compatible(expected: str, actual: str, *, placement: str) -> bool:
    if placement == "center":
        return actual in {"camera", "at_asset", "at_text"}
    allowed = _GAZE_TO_TAGS.get(expected, ())
    if not allowed:
        return True
    if actual in allowed:
        return True
    return actual == "camera" and expected in {"left", "right"}


def _gaze_repair_hint(expected: str, placement: str) -> str:
    if placement == "center":
        return "choose a camera-facing cutout for center-host templates"
    allowed = _GAZE_TO_TAGS.get(expected, ())
    return f"choose cutout gaze in {', '.join(allowed)}"


def _needs_high_credibility(reference_record: Mapping[str, Any]) -> bool:
    text = " ".join(
        [
            str(reference_record.get("title", "")),
            " ".join(str(tag) for tag in reference_record.get("title_intent_tags", ())),
            " ".join(str(tag) for tag in reference_record.get("style_tokens", ())),
        ]
    ).lower()
    return any(token in text for token in ("health", "evidence", "study", "research", "creatine"))


def _use_contexts(reference_record: Mapping[str, Any]) -> tuple[str, ...]:
    creator = str(reference_record.get("creator") or "")
    family_id = str(reference_record.get("template_family_candidate") or "")
    title_tags = " ".join(str(tag) for tag in reference_record.get("title_intent_tags", ())).lower()
    contexts: list[str] = []
    if creator == "Jeff Su":
        contexts.append("jeff_clean_tutorial")
    if creator == "Ali Abdaal":
        contexts.append("ali_warm_explainer")
    if any(token in title_tags for token in ("evidence", "health", "research", "study")):
        contexts.insert(0, "evidence_review")
    if "command" in family_id or "before_after" in family_id:
        contexts.insert(0, "myth_busting")
    if "step" in family_id or "roadmap" in family_id:
        contexts.insert(0, "protocol_steps")
    return tuple(dict.fromkeys(contexts))


_GAZE_TO_TAGS: dict[str, tuple[str, ...]] = {
    "camera": ("camera",),
    "left": ("screen_left", "at_asset", "at_text"),
    "right": ("screen_right", "at_asset", "at_text"),
    "up_left": ("up_left", "screen_left"),
    "up_right": ("up_right", "screen_right"),
    "down_left": ("down_left", "screen_left"),
    "down_right": ("down_right", "screen_right"),
    "none": (),
    "unclear": (),
}

_GESTURE_TO_HANDS: dict[str, tuple[str, ...]] = {
    "pointing_left": ("point_screen_left", "open_palm_screen_left"),
    "pointing_right": ("point_screen_right", "open_palm_screen_right"),
    "pointing_up": ("count_one", "point_screen_left", "point_screen_right"),
    "pointing": ("point_screen_left", "point_screen_right"),
    "open_hands": ("open_palms_both", "open_palm_screen_left", "open_palm_screen_right"),
    "chin_touch": ("chin",),
    "holding_object": ("holding_object",),
    "two_hands": ("open_palms_both", "count_two", "count_three"),
}

_EXPRESSION_TO_FAMILIES: dict[str, tuple[str, ...]] = {
    "excited": ("mild_surprise", "soft_smile", "explain"),
    "thoughtful": ("thoughtful", "neutral_trust", "skeptical"),
    "surprised": ("mild_surprise", "explain", "soft_smile"),
    "explaining": ("explain", "soft_smile", "neutral_trust"),
    "serious": ("serious_warning", "neutral_trust", "skeptical"),
    "laughing": ("warm_laugh", "soft_smile"),
    "smiling": ("soft_smile", "neutral_trust", "explain"),
    "smiling_explaining": ("explain", "soft_smile", "neutral_trust"),
    "cheerful": ("soft_smile", "warm_laugh", "explain"),
    "focused": ("neutral_trust", "thoughtful"),
    "confident": ("confident", "explain", "soft_smile"),
    "thinking": ("thoughtful", "neutral_trust"),
    "neutral_smile": ("neutral_trust", "soft_smile"),
    "serious_thoughtful": ("thoughtful", "serious_warning", "skeptical"),
    "neutral": ("neutral_trust", "soft_smile"),
}
