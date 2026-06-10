"""Deterministic cheap eval gates for thumbnail arrangement candidates."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Mapping

from shared.thumbnail_arrangement import ArrangementCandidate, CheapEvalSummary, LayerSpec, PixelBox
from shared.thumbnail_component_compatibility import score_arrangement_against_reference
from shared.thumbnail_template_contracts import (
    OverlapRule,
    find_overlap_rule,
    get_template_contract,
)


def score_arrangement_candidate(
    candidate: ArrangementCandidate,
    *,
    reference_record: Mapping[str, Any] | None = None,
) -> CheapEvalSummary:
    """Score a candidate with cheap geometry-only gates."""

    contract = get_template_contract(candidate.template_id)
    gates = contract.cheap_eval_gates
    score = 100.0
    reasons: list[str] = []
    repairs: list[str] = []

    def penalize(reason: str, repair: str, points: float) -> None:
        nonlocal score
        score -= points
        reasons.append(reason)
        repairs.append(repair)

    width, height = candidate.canvas_size
    canvas_area = width * height
    face_boxes = candidate.face_boxes()
    face_height_ratio = max((box.height / height for box in face_boxes), default=0.0)
    min_face_ratio = gates.get("min_face_height_ratio", 0.0)
    if face_height_ratio < min_face_ratio:
        penalize(
            f"face height ratio {face_height_ratio:.2f} below {min_face_ratio:.2f}",
            "increase_or_replace_host_cutout",
            22,
        )

    primary_payload_layers = _primary_payload_layers(candidate)
    payload_area_ratio = sum(
        _visible_area(layer.box, candidate.canvas_size) for layer in primary_payload_layers
    )
    payload_area_ratio /= max(1, canvas_area)
    min_payload_area = gates.get("min_payload_area_ratio", 0.0)
    max_payload_area = gates.get("max_payload_area_ratio", 1.0)
    if payload_area_ratio < min_payload_area:
        penalize(
            f"primary payload area ratio {payload_area_ratio:.2f} below {min_payload_area:.2f}",
            "enlarge_primary_payload",
            16,
        )
    if payload_area_ratio > max_payload_area:
        penalize(
            f"primary payload area ratio {payload_area_ratio:.2f} above {max_payload_area:.2f}",
            "shrink_primary_payload",
            12,
        )

    max_primary_payloads = int(gates.get("max_primary_payloads", 99))
    if len(primary_payload_layers) > max_primary_payloads:
        penalize(
            f"too many primary payloads: {len(primary_payload_layers)} > {max_primary_payloads}",
            "reduce_primary_payload_count",
            18,
        )

    _score_template_semantics(candidate, penalize=penalize)
    min_edge_margin = round(min(width, height) * gates.get("min_edge_margin_ratio", 0.0))
    for layer in _component_layers(candidate):
        edge_margin = _edge_margin(layer.box, candidate.canvas_size)
        if edge_margin < min_edge_margin:
            penalize(
                f"{layer.layer_id} edge margin {edge_margin}px below {min_edge_margin}px",
                f"move_inward:{layer.layer_id}",
                8,
            )

    _score_overlap_rules(
        candidate=candidate,
        penalize=penalize,
    )

    if reference_record is not None:
        compatibility = score_arrangement_against_reference(
            candidate=candidate,
            reference_record=reference_record,
        )
        if compatibility.ok:
            reasons.append(compatibility.reasons[0])
        else:
            points = min(60.0, max(18.0, 100.0 - compatibility.score))
            repair = (
                compatibility.repair_hints[0]
                if compatibility.repair_hints
                else "repair_component_reference_mismatch"
            )
            penalize(
                f"reference mismatch: {'; '.join(compatibility.reasons[:2])}",
                repair,
                points,
            )

    status = "pass" if score >= 80 and not repairs else "fail"
    if not reasons:
        reasons.append("passes deterministic arrangement gates")
    return CheapEvalSummary(
        status=status,
        score=max(0.0, round(score, 2)),
        reasons=tuple(reasons),
        repairs=tuple(dict.fromkeys(repairs)),
    )


def attach_cheap_eval(
    candidate: ArrangementCandidate,
    *,
    reference_record: Mapping[str, Any] | None = None,
) -> ArrangementCandidate:
    """Return a copy of the candidate with cheap_eval populated."""

    return replace(
        candidate,
        cheap_eval=score_arrangement_candidate(candidate, reference_record=reference_record),
    )


def rank_arrangement_candidates(
    candidates: tuple[ArrangementCandidate, ...] | list[ArrangementCandidate],
    *,
    reference_record: Mapping[str, Any] | None = None,
) -> tuple[ArrangementCandidate, ...]:
    """Attach cheap eval and rank best-first."""

    scored = [
        attach_cheap_eval(candidate, reference_record=reference_record)
        for candidate in candidates
    ]
    return tuple(
        sorted(
            scored,
            key=lambda candidate: (
                candidate.cheap_eval.status != "pass",
                -candidate.cheap_eval.score,
                candidate.candidate_id,
            ),
        )
    )


def _score_overlap_rules(
    *,
    candidate: ArrangementCandidate,
    penalize: object,
) -> None:
    contract = get_template_contract(candidate.template_id)
    host_faces = candidate.face_boxes()
    host_hands = candidate.hand_boxes()
    layers = candidate.layers

    for face in host_faces:
        for layer in layers:
            if layer.role == "payload_card":
                _apply_overlap_rule(
                    rule=find_overlap_rule(
                        contract,
                        foreground_role="host_face",
                        background_role="payload_card",
                    ),
                    foreground_box=face,
                    background_layer=layer,
                    penalize=penalize,
                )

    for hand in host_hands:
        for layer in layers:
            if layer.role == "payload_card":
                _apply_overlap_rule(
                    rule=find_overlap_rule(
                        contract,
                        foreground_role="host_hand",
                        background_role="payload_card",
                    ),
                    foreground_box=hand,
                    background_layer=layer,
                    penalize=penalize,
                )
                for text_box in layer.protected_text_boxes:
                    text_iou = hand.iou(text_box)
                    if text_iou > 0.02:
                        penalize(
                            (
                                f"host hand overlaps protected text in {layer.layer_id}: "
                                f"iou {text_iou:.3f}"
                            ),
                            f"move_hand_or_text:{layer.layer_id}",
                            26,
                        )

    for headline in (layer for layer in layers if layer.role == "headline_text"):
        for face in host_faces:
            rule = find_overlap_rule(
                contract,
                foreground_role="headline_text",
                background_role="host_face",
            )
            if rule and headline.box.iou(face) > rule.max_iou:
                penalize(
                    f"headline overlaps host face: iou {headline.box.iou(face):.3f}",
                    f"move_headline_away_from_face:{headline.layer_id}",
                    30,
                )


def _score_template_semantics(
    candidate: ArrangementCandidate,
    *,
    penalize: object,
) -> None:
    if candidate.template_id == "ali_metric_arrow":
        metrics = [
            str(item).strip()
            for layer in candidate.layers
            if layer.role == "payload_metric"
            for item in layer.content
            if str(item).strip()
        ]
        if len(metrics) < 2:
            penalize(
                "ali_metric_arrow requires two concrete metric labels",
                "replace_template_or_metric_labels",
                28,
            )
            return
        weak = [metric for metric in metrics[:2] if not _looks_like_metric_label(metric)]
        if weak:
            weak_text = ", ".join(weak)
            penalize(
                f"ali_metric_arrow metric labels are not concrete numeric contrast: {weak_text}",
                "replace_template_or_metric_labels",
                28,
            )


def _apply_overlap_rule(
    *,
    rule: OverlapRule | None,
    foreground_box: PixelBox,
    background_layer: LayerSpec,
    penalize: object,
) -> None:
    if not rule:
        return
    iou = foreground_box.iou(background_layer.box)
    if iou <= rule.max_iou:
        return
    points = 30 if rule.status == "forbidden" else 14
    penalize(
        (
            f"{rule.foreground_role} overlaps {background_layer.layer_id} "
            f"as {rule.background_role}: iou {iou:.3f} > {rule.max_iou:.3f}"
        ),
        f"resolve_overlap:{rule.foreground_role}:{background_layer.layer_id}",
        points,
    )


def _primary_payload_layers(candidate: ArrangementCandidate) -> tuple[LayerSpec, ...]:
    return tuple(
        layer
        for layer in candidate.layers
        if layer.role in {"payload_card", "payload_metric"} and layer.kind not in {"arrow"}
    )


def _component_layers(candidate: ArrangementCandidate) -> tuple[LayerSpec, ...]:
    return tuple(layer for layer in candidate.layers if layer.role not in {"background", "host"})


def _visible_area(box: PixelBox, canvas_size: tuple[int, int]) -> int:
    clipped = _clip_box(box, canvas_size)
    return clipped.area if clipped else 0


def _clip_box(box: PixelBox, canvas_size: tuple[int, int]) -> PixelBox | None:
    width, height = canvas_size
    x0 = max(0, min(width, box.x0))
    y0 = max(0, min(height, box.y0))
    x1 = max(0, min(width, box.x1))
    y1 = max(0, min(height, box.y1))
    if x1 <= x0 or y1 <= y0:
        return None
    return PixelBox(x0, y0, x1, y1)


def _edge_margin(box: PixelBox, canvas_size: tuple[int, int]) -> int:
    width, height = canvas_size
    return min(box.x0, box.y0, width - box.x1, height - box.y1)


def _looks_like_metric_label(value: str) -> bool:
    text = value.strip().replace(" ", "")
    if not text:
        return False
    return bool(
        re.fullmatch(
            r"(?:\$|NT\$|US\$)?\d+(?:\.\d+)?(?:%|％|x|X|倍|k|K|m|M|g|G|mg|kg|min|h|hr|hrs|分鐘|小時|歲|年)?",
            text,
        )
    )
