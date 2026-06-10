"""Corpus-backed thumbnail reference selection.

This selector works one layer below the current production template list. It
chooses a concrete Ali/Jeff family and a concrete reference image record from
the normalized corpus, so later steps can compare against an actual thumbnail
instead of a vague creator style.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from shared.thumbnail_reference_corpus import (
    best_deconstruction_examples,
    format_deconstruction_record_for_prompt,
    format_template_family_for_prompt,
    list_template_families,
    load_deconstruction_records,
    load_family_taxonomy,
)
from shared.thumbnail_reference_templates import REFERENCE_TEMPLATE_IDS

_EXECUTABLE_REFERENCE_FAMILY_IDS = REFERENCE_TEMPLATE_IDS - {"shosho_benefit_list_card"}
_MIN_EXECUTABLE_PROMPT_SCORE = 2.0


@dataclass(frozen=True)
class ReferenceFamilyMatch:
    family_id: str
    reference_id: str
    score: float
    reasons: tuple[str, ...]
    family: dict[str, Any]
    record: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "reference_id": self.reference_id,
            "score": round(self.score, 2),
            "reasons": list(self.reasons),
            "creator_style": self.family["creator_style"],
            "label": self.family["label"],
            "production_status": self.family["production_status"],
            "image_path": self.record["image_path"],
            "component_types": list(
                self.family["slot_model"]["primary_payload"]["allowed_component_types"]
            ),
            "host_policy": dict(self.family["slot_model"]["host"]),
        }


def select_reference_family_matches(
    *,
    title: str,
    project_brief: str = "",
    limit: int = 5,
    include_non_renderable: bool = False,
    taxonomy: dict[str, Any] | None = None,
    records: list[dict[str, Any]] | None = None,
) -> list[ReferenceFamilyMatch]:
    """Return best Ali/Jeff family matches plus concrete reference records."""

    data = taxonomy or load_family_taxonomy()
    corpus_records = records or load_deconstruction_records()
    haystack = _normalize(" ".join([title, project_brief]))
    title_norm = _normalize(title)
    matches: list[ReferenceFamilyMatch] = []

    for family in list_template_families(taxonomy=data):
        if not include_non_renderable and family["production_status"] != "renderable_v1":
            continue
        score, reasons = _score_family(family, title_norm=title_norm, haystack=haystack)
        examples = best_deconstruction_examples(
            family["family_id"],
            limit=5,
            renderable_only=not include_non_renderable,
            taxonomy=data,
            records=corpus_records,
        )
        if not examples:
            continue
        record, record_score, record_reasons = _best_record_for_title(
            examples,
            title_norm=title_norm,
            haystack=haystack,
        )
        total_score = score + record_score
        if total_score <= 0:
            total_score = 1.0
            reasons.append("fallback:available-renderable-family")
        matches.append(
            ReferenceFamilyMatch(
                family_id=family["family_id"],
                reference_id=record["reference_id"],
                score=total_score,
                reasons=tuple([*reasons, *record_reasons][:8]),
                family=family,
                record=record,
            )
        )

    matches.sort(key=lambda item: (-item.score, item.family_id, item.reference_id))
    return matches[: max(1, limit)]


def build_title_reference_family_match_plan(
    *,
    title_candidates: Iterable[str],
    project_brief: str = "",
    limit_per_title: int = 3,
    max_titles: int = 12,
) -> list[dict[str, Any]]:
    """Build a concrete reference family plan for a title pool."""

    titles = [str(title).strip() for title in title_candidates if str(title).strip()]
    plan: list[dict[str, Any]] = []
    for index, title in enumerate(titles[: max(1, max_titles)], start=1):
        matches = select_reference_family_matches(
            title=title,
            project_brief=project_brief,
            limit=limit_per_title,
        )
        plan.append(
            {
                "title_id": f"T{index:02d}",
                "title": title,
                "matches": [match.to_dict() for match in matches],
            }
        )
    return plan


def format_title_reference_family_match_plan_for_prompt(
    *,
    title_candidates: Iterable[str],
    project_brief: str = "",
    limit_per_title: int = 3,
    max_titles: int = 12,
) -> str:
    """Format concrete reference-family matches for a brainstorm/eval prompt."""

    plan = build_title_reference_family_match_plan(
        title_candidates=title_candidates,
        project_brief=project_brief,
        limit_per_title=limit_per_title,
        max_titles=max_titles,
    )
    lines = [
        "## Concrete Ali/Jeff reference match plan",
        "",
        (
            "For each title, choose one concrete reference_id and preserve its "
            "family grammar. Do not mix a host pose, component type, or text "
            "density from another family."
        ),
        "",
    ]
    for item in plan:
        executable_matches = [
            match
            for match in item["matches"]
            if match["family_id"] in _EXECUTABLE_REFERENCE_FAMILY_IDS
            and float(match["score"]) >= _MIN_EXECUTABLE_PROMPT_SCORE
        ][:limit_per_title]
        lines.extend([f"### {item['title_id']}", f"- title: {item['title']}", "- matches:"])
        if not executable_matches:
            lines.extend(
                [
                    (
                        "  - none: no strong executable Ali/Jeff reference match; "
                        "follow the Title-template match plan instead."
                    ),
                    "",
                ]
            )
            continue
        for match in executable_matches:
            lines.extend(
                [
                    (
                        f"  - {match['family_id']} / {match['reference_id']}: "
                        f"score={match['score']}; label={match['label']}; "
                        f"components={', '.join(match['component_types'])}"
                    ),
                    f"    reasons: {', '.join(match['reasons'])}",
                    f"    image_path: {match['image_path']}",
                ]
            )
        lines.append("")
    return "\n".join(lines)


def format_selected_reference_context(match: ReferenceFamilyMatch) -> str:
    """Format family and concrete record context for an LLM repair/eval step."""

    return "\n\n".join(
        [
            "## Template family",
            format_template_family_for_prompt(match.family_id, max_examples=2),
            "## Concrete reference",
            format_deconstruction_record_for_prompt(match.record),
        ]
    )


def _score_family(
    family: dict[str, Any],
    *,
    title_norm: str,
    haystack: str,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    family_id = family["family_id"]
    best_for = " ".join(str(item) for item in family["best_for"])

    overlap = _phrase_overlap(best_for, haystack)
    if overlap:
        score += overlap * 3.0
        reasons.append(f"best_for_overlap:{overlap}")

    for token, weighted_families in _INTENT_WEIGHTS.items():
        if token not in haystack:
            continue
        weight = weighted_families.get(family_id)
        if weight:
            score += weight
            reasons.append(f"intent:{token}")

    if family["creator_style"] == "Jeff Su" and any(token in haystack for token in _TOOL_TOKENS):
        score += 3.0
        reasons.append("creator_style:jeff_tool_fit")
    if family["creator_style"] == "Ali Abdaal" and any(token in haystack for token in _ALI_TOKENS):
        score += 3.0
        reasons.append("creator_style:ali_personal_fit")

    family_tokens = set(_normalize(family_id.replace("_", " ")).split())
    title_tokens = set(title_norm.split())
    family_overlap = len(family_tokens & title_tokens)
    if family_overlap:
        score += family_overlap
        reasons.append(f"family_token_overlap:{family_overlap}")

    return score, reasons


def _best_record_for_title(
    records: list[dict[str, Any]],
    *,
    title_norm: str,
    haystack: str,
) -> tuple[dict[str, Any], float, list[str]]:
    best = records[0]
    best_score = -1.0
    best_reasons: list[str] = []
    for record in records:
        record_title = _normalize(record["title"])
        overlap = _phrase_overlap(record_title, haystack)
        score = float(overlap)
        reasons: list[str] = []
        if overlap:
            reasons.append(f"reference_title_overlap:{overlap}")
        if record["priority"] == "gold":
            score += 1.0
            reasons.append("reference_priority:gold")
        if any(token in title_norm for token in _normalize(record["template_family_candidate"]).split("_")):
            score += 0.5
        if score > best_score:
            best = record
            best_score = score
            best_reasons = reasons
    return best, max(0.0, best_score), best_reasons


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().replace("：", ":").split())


def _phrase_overlap(phrase: str, haystack: str) -> int:
    tokens = [
        token
        for token in re.findall(r"[a-z0-9$%]+|[\u4e00-\u9fff]+", _normalize(phrase))
        if len(token) > 1
    ]
    return sum(1 for token in tokens if token in haystack)


_TOOL_TOKENS = {
    "ai",
    "app",
    "chatgpt",
    "claude",
    "gemini",
    "google",
    "notebooklm",
    "notion",
    "perplexity",
    "tool",
    "tutorial",
    "工具",
    "教學",
}

_ALI_TOKENS = {
    "advice",
    "behind",
    "dopamine",
    "habits",
    "life",
    "money",
    "rich",
    "self",
    "wasting",
}

_INTENT_WEIGHTS: dict[str, dict[str, float]] = {
    "wrong": {"jeff_command_panel": 18.0, "ali_before_after_split": 8.0},
    "correct": {"jeff_command_panel": 18.0, "jeff_tool_header_panel": 6.0},
    "instead": {"jeff_command_panel": 16.0, "ali_before_after_split": 8.0},
    "mistake": {"jeff_command_panel": 14.0, "ali_before_after_split": 10.0},
    "fail": {"jeff_command_panel": 12.0, "jeff_text_overlay_panel": 8.0},
    "prompt": {"jeff_command_panel": 12.0, "jeff_tool_header_panel": 8.0},
    "chatgpt": {"jeff_command_panel": 10.0, "jeff_tool_header_panel": 10.0,
                "jeff_logo_cluster_dark": 8.0},
    "claude": {"jeff_tool_header_panel": 12.0, "jeff_ui_panel_host_side": 9.0,
               "jeff_logo_cluster_dark": 8.0},
    "gemini": {"jeff_tool_header_panel": 12.0, "jeff_ui_panel_host_side": 8.0},
    "notion": {"jeff_ui_panel_host_side": 12.0, "jeff_tool_header_panel": 8.0},
    "notebooklm": {"jeff_tool_header_panel": 18.0},
    "perplexity": {"jeff_tool_header_panel": 18.0},
    "google": {"jeff_tool_header_panel": 8.0, "jeff_ui_panel_host_side": 8.0},
    "learn": {"jeff_tool_header_panel": 10.0, "jeff_roadmap_board": 6.0},
    "master": {"jeff_tool_header_panel": 8.0, "jeff_ui_panel_host_side": 8.0},
    "minutes": {"jeff_tool_header_panel": 8.0},
    "millionaire": {"ali_metric_arrow": 18.0, "ali_income_cards": 10.0},
    "$": {"ali_metric_arrow": 12.0, "ali_income_cards": 12.0},
    "income": {"ali_income_cards": 16.0, "ali_icon_cluster_host": 6.0},
    "rich": {"ali_command_text_host": 12.0, "ali_income_cards": 10.0},
    "advice": {"ali_social_quote_card": 18.0, "ali_command_text_host": 6.0},
    "honest": {"ali_social_quote_card": 18.0},
    "behind": {"ali_social_quote_card": 12.0},
    "freedom": {"ali_social_quote_card": 10.0, "ali_income_cards": 8.0},
    "dopamine": {"ali_before_after_split": 12.0, "ali_whiteboard_diagram": 8.0},
    "distracted": {"ali_before_after_split": 12.0},
    "reset": {"ali_before_after_split": 10.0},
    "year": {"ali_year_hero": 14.0},
    "2026": {"ali_year_hero": 8.0, "jeff_text_overlay_panel": 4.0},
    "productivity": {"ali_icon_cluster_host": 8.0, "jeff_step_cards": 8.0,
                     "jeff_ui_panel_host_side": 7.0},
    "system": {"jeff_roadmap_board": 10.0, "jeff_step_cards": 8.0},
    "agent": {"jeff_roadmap_board": 14.0, "jeff_logo_cluster_dark": 8.0},
    "工具": {"jeff_tool_header_panel": 12.0, "jeff_logo_cluster_dark": 8.0},
    "教學": {"jeff_tool_header_panel": 12.0},
    "錯": {"jeff_command_panel": 14.0},
    "指令": {"jeff_command_panel": 12.0},
}
