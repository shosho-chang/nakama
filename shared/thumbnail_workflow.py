"""Title-driven thumbnail workflow pack for YouTube brainstorm.

This module sits between the broad thumbnail playbook and the prompt that
creates three thumbnail candidates. The playbook remains the reference library;
this file is the compact per-video decision pack we actually inject into the
LLM so it does not choose from a flat, over-broad menu.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThumbnailRecipeCard:
    """A compact, LLM-consumable thumbnail strategy card."""

    recipe_id: str
    lane: str
    label: str
    title_patterns: tuple[str, ...]
    visual_tags: tuple[str, ...]
    best_for: tuple[str, ...]
    visual_grammar: tuple[str, ...]
    asset_needs: tuple[str, ...]
    avoid: tuple[str, ...]


PRIMARY_TITLE_ARCHETYPES: tuple[str, ...] = (
    "T-A1",  # Numbered Listicle
    "T-A2",  # How-to / explainer
    "T-A3",  # Soft contrarian / corrective
    "T-A4",  # Personal system / blueprint
    "T-A7",  # Time-compression tutorial
    "T-A8",  # Authority / research synthesis
)

MODIFIER_ARCHETYPES: dict[str, str] = {
    "T-A9": (
        "M-1 Year / recency modifier; attach only when the content is genuinely time-sensitive."
    ),
    "T-A10": "M-2 Loss / risk modifier; attach only as factual risk framing, never as fear bait.",
}

DISALLOWED_VISUAL_ARCHETYPES: dict[str, str] = {
    "T-V6": "Avoid for health/longevity: question-mark breakthrough overlays read as medical hype.",
}

SUPPORTED_RENDERABLE_VISUAL_TAGS: tuple[str, ...] = (
    "T-V1",
    "T-V2",
    "T-V3",
    "T-V8",
    "T-V10",
)


RECIPE_CARDS: tuple[ThumbnailRecipeCard, ...] = (
    ThumbnailRecipeCard(
        recipe_id="ali_warm_evidence_list",
        lane="Ali Warm Explainer",
        label="Warm evidence-backed listicle",
        title_patterns=("T-A1", "T-A2", "T-A8"),
        visual_tags=("T-V3", "T-V10"),
        best_for=(
            "health habits",
            "supplement basics",
            "science-backed practical advice",
        ),
        visual_grammar=(
            "warm real workspace or clean lifestyle background",
            "host appears approachable, thoughtful, or explaining",
            "one large phrase plus one evidence/number cue",
            "soft contrast, no aggressive command-text posture",
        ),
        asset_needs=(
            "warm desk or lifestyle stock photo",
            "food/supplement/object photo or simple icon",
            "paper/highlighter texture if the topic is research synthesis",
        ),
        avoid=(
            "dark Hormozi studio",
            "medical miracle language",
            "claiming causality beyond the script evidence",
        ),
    ),
    ThumbnailRecipeCard(
        recipe_id="ali_actually_corrective",
        lane="Ali Warm Explainer",
        label="Actually-works corrective",
        title_patterns=("T-A2", "T-A3", "T-A8"),
        visual_tags=("T-V3",),
        best_for=(
            "common misconception with a gentle correction",
            "routine/habit content",
            "viewer has tried before and failed",
        ),
        visual_grammar=(
            "before/after or wrong/right split without shaming the viewer",
            "host expression is thoughtful or explaining, not accusatory",
            "thumbnail text states the corrected lens, not the full answer",
        ),
        asset_needs=(
            "split-screen background or two contrasting still-life assets",
            "simple check/cross icons",
            "one chart-like visual if the evidence is numeric",
        ),
        avoid=(
            "'you are wrong' phrasing",
            "red alarm palettes",
            "dense unreadable medical diagrams",
        ),
    ),
    ThumbnailRecipeCard(
        recipe_id="ali_personal_system",
        lane="Ali Warm Explainer",
        label="Personal system / blueprint",
        title_patterns=("T-A4", "T-A8", "T-A2"),
        visual_tags=("T-V1", "T-V10"),
        best_for=(
            "creator-tested routine",
            "longer guide",
            "protocol or framework reveal",
        ),
        visual_grammar=(
            "host points toward a clean protocol board or checklist",
            "board shows 3-5 named blocks, not tiny paragraphs",
            "warm-but-credible, more notebook/workshop than lecture hall",
        ),
        asset_needs=(
            "clean whiteboard/notebook texture",
            "minimal checklist or flowchart elements",
            "workspace background with negative space",
        ),
        avoid=(
            "pretending a simple topic needs a huge system",
            "overcrowded diagram",
            "grandiose full-blueprint claims without a real protocol",
        ),
    ),
    ThumbnailRecipeCard(
        recipe_id="jeff_clean_tutorial_dual_zone",
        lane="Jeff Clean Tutorial",
        label="Clean tutorial dual-zone",
        title_patterns=("T-A2", "T-A7", "T-A9"),
        visual_tags=("T-V1",),
        best_for=(
            "how-to content",
            "wearable/app/tool explanation",
            "health protocol that can be learned quickly",
        ),
        visual_grammar=(
            "white or light-gray background with a clean information panel",
            "host points or smiles toward the payload",
            "payload is a formula, checklist, or simple UI mockup",
            "3-4 elements max in the payload zone",
        ),
        asset_needs=(
            "generic dashboard or app UI kit",
            "minimal vector icon pack",
            "device mockup if the topic involves a wearable/app",
        ),
        avoid=(
            "photoreal chaos behind the payload",
            "logos unless they are truly central and safe to show",
            "tutorial promise for nuanced medical topics",
        ),
    ),
    ThumbnailRecipeCard(
        recipe_id="jeff_80_percent_protocol",
        lane="Jeff Clean Tutorial",
        label="80 percent in N minutes / protocol compression",
        title_patterns=("T-A7", "T-A2"),
        visual_tags=("T-V10",),
        best_for=(
            "bounded protocol",
            "beginner onboarding",
            "a topic where 80/20 simplification is honest",
        ),
        visual_grammar=(
            "large number or time promise as the dominant graphic",
            "host reacts with friendly confidence",
            "supporting panel shows the 3 most important steps",
        ),
        asset_needs=(
            "simple timer/clock icon",
            "three-step process icons",
            "clean light background or dashboard card",
        ),
        avoid=(
            "80/20 framing for complex diagnosis/treatment claims",
            "fake precision",
            "tiny explanatory text inside the thumbnail",
        ),
    ),
)


DEFERRED_RECIPE_CARDS: tuple[ThumbnailRecipeCard, ...] = (
    ThumbnailRecipeCard(
        recipe_id="jeff_stack_or_tool_cluster",
        lane="Jeff Clean Tutorial",
        label="Tool / icon / protocol stack",
        title_patterns=("T-A1", "T-A2", "T-A3"),
        visual_tags=("T-V7", "T-V9"),
        best_for=(
            "multiple tools/apps/supplements/behaviors",
            "curated stack",
            "comparison or best-of episode",
        ),
        visual_grammar=(
            "4-6 recognizable icons or object cards arranged around the host",
            "host is excited or explaining",
            "one strong umbrella hook ties the stack together",
        ),
        asset_needs=(
            "consistent icon pack",
            "object cutouts for supplement/food/tool cards",
            "light or dark neutral backdrop that lets assets pop",
        ),
        avoid=(
            "more than 6 icons",
            "mixing unrelated icon styles",
            "implying breadth when the video is a single-topic deep dive",
        ),
    ),
)


def format_thumbnail_workflow_pack_for_prompt() -> str:
    """Return the focused Ali/Jeff decision pack injected into brainstorm."""

    lines: list[str] = [
        "## Ali/Jeff thumbnail workflow pack (focused playbook)",
        "",
        (
            "Use this compact pack instead of the full 140-reference playbook. "
            "The goal is to choose one publish title from the provided title pool, "
            "then generate exactly three YouTube-testable thumbnail variants for "
            "that same title."
        ),
        "",
        "### Selection rules",
        "",
        (
            "- Prefer Ali Abdaal + Jeff Su visual language: warm evidence explainer "
            "or clean tutorial UI."
        ),
        "- Treat T-A9 and T-A10 as modifiers only, never standalone title strategies.",
        "- Do not use T-V6 for health/longevity topics.",
        f"- Use only renderable visual tags: {', '.join(SUPPORTED_RENDERABLE_VISUAL_TAGS)}.",
        "- Avoid Alex Hormozi-style dark aggression unless the user explicitly asks for that lane.",
        (
            "- The three output ideas must be meaningfully different in viewer "
            "promise, visual metaphor, and asset needs."
        ),
        (
            "- All three ideas must use the same publish title candidate from the "
            "input pool or a very small fusion of two candidates."
        ),
        "- Thumbnail text should complement the title, not repeat it.",
        "",
        "### Primary title archetypes",
        "",
        ", ".join(PRIMARY_TITLE_ARCHETYPES),
        "",
        "### Modifier archetypes",
        "",
    ]
    for aid, desc in MODIFIER_ARCHETYPES.items():
        lines.append(f"- {aid}: {desc}")

    lines.extend(["", "### Disallowed visual archetypes", ""])
    for aid, desc in DISALLOWED_VISUAL_ARCHETYPES.items():
        lines.append(f"- {aid}: {desc}")

    lines.extend(["", "### Renderable visual tags", ""])
    lines.append(", ".join(SUPPORTED_RENDERABLE_VISUAL_TAGS))

    lines.extend(["", "### Recipe cards", ""])
    for card in RECIPE_CARDS:
        lines.extend(
            [
                f"#### {card.recipe_id} — {card.label}",
                f"- lane: {card.lane}",
                f"- title_patterns: {', '.join(card.title_patterns)}",
                f"- visual_tags: {', '.join(card.visual_tags)}",
                f"- best_for: {'; '.join(card.best_for)}",
                f"- visual_grammar: {'; '.join(card.visual_grammar)}",
                f"- asset_needs: {'; '.join(card.asset_needs)}",
                f"- avoid: {'; '.join(card.avoid)}",
                "",
            ]
        )

    lines.extend(
        [
            "### Required output metadata",
            "",
            (
                "Each idea should preserve these lines when possible: archetype, "
                "lane, recipe, reference_template, title_pairing, viewer_promise, "
                "component, component_text, host, evidence_fit, trust_risk, 大字, "
                "我的表情, 視覺, 數字/圖示, 背景, 素材需求."
            ),
            "",
            "### Deferred recipes",
            "",
            (
                "Do not output deferred recipes in v1; they need a richer asset "
                "layer or renderer support first: "
            )
            + ", ".join(card.recipe_id for card in DEFERRED_RECIPE_CARDS),
        ]
    )
    return "\n".join(lines)
