"""Thumbnail template registry — 5 locked layouts mapped to playbook T-V archetypes.

Each template is a *contract* between three components:

  1. **AI image gen prompt** (``ai_prompt_template``) — asks GPT Image 1 for an
     empty environmental plate only. The host cutout is never sent as an
     identity reference because image edit does not preserve 修修's face.
     We deliberately tell the AI NOT to render people or text — both are
     handled by CSS in hyperframes.

  2. **Composition overlay** (``overlay_archetype``) — slug matched against
     ``video/compositions/thumbnail_youtube/index.html`` ``data-archetype``
     CSS rules. The overlay renders the Chinese title + accent decoration on
     top of the AI-generated bg image. The cutout layer is hidden in
     AI-overlay mode.

  3. **Palette + decoration semantics** — passed through to both prompt
     (telling AI the colour mood) and CSS (rendering the title chrome).

Templates are picked by:
  - Primary: ``T-V*`` visual archetype tag from parsed idea's ``archetype_tags``
  - Fallback: T-V1 (Face-Right Text-Left) — most authoritative default

References:
  - prompts/thumbnail/playbook_data_v1.json (catalog.thumbnail_archetypes)
  - docs/research/2026-05-27-thumbnail-pipeline-community-research.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ThumbnailTemplate:
    """One locked thumbnail layout. Bundles AI prompt + composition overlay + chrome."""

    # Playbook T-V* visual archetype this template implements
    tv_id: str
    # Human-readable name (matches playbook ``name``)
    name: str
    # Composition ``data-archetype`` slug (CSS rules in thumbnail_youtube/index.html)
    overlay_archetype: str
    # Default palette overrides (passed to composition as palette dict)
    palette: dict[str, str | float]
    # AI gen prompt template — see ``build_prompt()`` for variable substitution
    ai_prompt_template: str
    # Whether the AI image gen includes the subject (cutout fed as reference)
    # or just the background (cutout overlaid by CSS afterward)
    ai_includes_subject: bool
    # One-line operator note (shown in UI / logs)
    summary: str


# ────────────────────────────────────────────────────────────────────────────
# Prompt building blocks. The AI image gen prompt is structured as:
#
#   <intro/role>
#   <scene>          ← scene_block per template (where the subject sits + bg)
#   <pose>           ← from parsed.visual (LLM-authored)
#   <decoration>     ← from parsed.decoration (LLM-authored)
#   <bg_detail>      ← from parsed.bg (LLM-authored)
#   <style>          ← style_block per template (color mood, lighting, vibe)
#   <constraints>    ← _COMMON_CONSTRAINTS (no text, photoreal, etc)
#   <composition>    ← composition_block per template (where to leave space)
# ────────────────────────────────────────────────────────────────────────────

_COMMON_CONSTRAINTS = (
    "CRITICAL CONSTRAINTS:\n"
    "- DO NOT render any people, faces, characters, hands, or human figures "
    "anywhere in the image. The host's photo will be composited on top in post — "
    "your job is the EMPTY environmental backdrop only.\n"
    "- DO NOT render any text, letters, numbers, words, captions, watermarks, "
    "or logos anywhere in the image. All text is overlaid in post.\n"
    "- Photorealistic style. No anime, illustration, painting, cartoon.\n"
    "- 16:9 landscape composition optimised for YouTube thumbnail at 1280x720, "
    "must read clearly at 320x180."
)


TEMPLATES: dict[str, ThumbnailTemplate] = {
    # ──────────────────────────────────────────────────────────────────
    # T-V1: Face-Right / Text-Left Dual-Zone
    # Authority lane (Alex Hormozi, Jeff Su signature). Dark studio bg,
    # subject right-half, left-half negative space for big white text.
    # ──────────────────────────────────────────────────────────────────
    "T-V1": ThumbnailTemplate(
        tv_id="T-V1",
        name="Face-Right Text-Left Dual-Zone",
        overlay_archetype="tv1-face-right",
        palette={
            "bg": "#1A1A1A",
            "bg_2": "#0B0B0B",
            "fg": "#FFFFFF",
            "accent": "#FFD340",
            "stroke": "#000000",
            "bg_darken": 0.10,
        },
        ai_prompt_template=(
            "Create an EMPTY background plate for a YouTube thumbnail (16:9, 1536x1024 "
            "landscape). NO people, NO subjects — only environment.\n\n"
            "ENVIRONMENT: Near-black hard-light studio backdrop (#050505 to #151515), "
            "matte wall with faint vertical shadow panels. Avoid smoky amber haze, "
            "romantic bokeh, light leaks, or visible props in the title zone. Use one "
            "hard directional key/rim light from upper-front-left aimed toward the "
            "right half, so a real host portrait composited on the right reads like it "
            "belongs in a high-contrast studio setup.\n\n"
            "ENVIRONMENTAL FLAVOUR (from director, atmospheric only): {bg}\n"
            "AMBIENT PROPS / SET-DRESSING (small, non-human, optional, scattered "
            "in the right half only): {decoration}\n\n"
            "STYLE: Alex Hormozi / business-authority thumbnail energy: graphic, "
            "high-contrast, mostly black edges, hard light, clean negative space. "
            "The plate should feel like a designed Photoshop base, not a misty "
            "cinematic background.\n\n"
            "{constraints}\n\n"
            "COMPOSITION REQUIREMENT: LEFT 48% of the frame must be matte-black, "
            "clean, and visually empty for giant Chinese title typography in white "
            "and #FFD340 yellow. RIGHT 52% may have a subtle hard-light gradient "
            "behind the portrait position only. Keep all edges dark."
        ),
        ai_includes_subject=False,
        summary="權威暗景 — 大字左 + 真實人像右（CSS 疊）",
    ),
    # ──────────────────────────────────────────────────────────────────
    # T-V2: Face-Center Tight Crop with Text Overlay
    # High-emotion confessional / authority statement. Face fills 60-80%
    # of frame, dark abstract bg, text floats over chest area.
    # ──────────────────────────────────────────────────────────────────
    "T-V2": ThumbnailTemplate(
        tv_id="T-V2",
        name="Face-Center Tight Crop",
        overlay_archetype="tv2-face-center",
        palette={
            "bg": "#2B1B3F",
            "bg_2": "#100817",
            "fg": "#FFF8E7",
            "accent": "#E07A5F",
            "stroke": "#0A0510",
            "bg_darken": 0.05,
        },
        ai_prompt_template=(
            "Create an EMPTY background plate for a YouTube thumbnail (16:9, 1536x1024 "
            "landscape). NO people, NO subjects — atmospheric backdrop only.\n\n"
            "ENVIRONMENT: Deep desaturated dark backdrop, soft radial gradient "
            "centered on the frame (deep purple #2B1B3F → near-black #100817), "
            "completely soft / out-of-focus / shallow DoF — a face will be composited "
            "into the center later, so the backdrop must be uniformly soft so the face "
            "pops sharply against it.\n\n"
            "ATMOSPHERE (from director — colour/mood only, NOT objects): {bg}\n"
            "OPTIONAL FAINT TEXTURE (e.g. moody volumetric haze, distant city bokeh, "
            "soft caustic shimmer — completely OUT of focus): {decoration}\n\n"
            "STYLE: Cinematic, moody, high-emotion ambient backdrop. No hard edges, "
            "no sharp objects, no text. Single off-center light source creating "
            "directional glow.\n\n"
            "{constraints}\n\n"
            "COMPOSITION REQUIREMENT: The CENTER 60% of the frame (where a face will "
            "be placed) should be slightly brighter / softer than the edges. Bottom "
            "20% of frame should be SLIGHTLY DARKER (subtle vignette) for a short "
            "Chinese title overlay."
        ),
        ai_includes_subject=False,
        summary="大臉特寫底圖 — 真實人像置中、底部留白給標題",
    ),
    # ──────────────────────────────────────────────────────────────────
    # T-V3: Split-Screen Comparison
    # Before/after, option A vs B. Hard vertical split, two colour temps.
    # ──────────────────────────────────────────────────────────────────
    "T-V3": ThumbnailTemplate(
        tv_id="T-V3",
        name="Split-Screen Comparison",
        overlay_archetype="tv3-split",
        palette={
            "bg": "#1A3A4F",
            "bg_2": "#3A1A1A",
            "fg": "#FFFFFF",
            "accent": "#FFC857",
            "stroke": "#000000",
            "bg_darken": 0.10,
        },
        ai_prompt_template=(
            "Create an EMPTY split-screen background plate for a YouTube thumbnail "
            "(16:9, 1536x1024 landscape). NO people, NO faces — environmental "
            "split only.\n\n"
            "ENVIRONMENT: The frame is divided into two vertical halves by a subtle "
            "soft seam down the center. LEFT half = a 'before' / cold / desaturated "
            "ENVIRONMENT (no person). RIGHT half = an 'after' / warm / saturated "
            "ENVIRONMENT (no person). The two halves contrast in colour temperature, "
            "lighting, and atmospheric mood.\n\n"
            "BACKGROUND THEME / TWO STATES (from director — describes both sides as "
            "environments, NOT as people): {bg}\n"
            "OPTIONAL CONTEXTUAL OBJECTS (one small still-life object per side, "
            "non-human, e.g. a fallen pill bottle vs a fresh fruit bowl): {decoration}\n\n"
            "STYLE: LEFT — cool colour temp (desaturated blue / grey), low-key "
            "lighting. RIGHT — warm colour temp (golden / saturated), high-key "
            "lighting. Each side reads as a distinct environment at thumbnail size.\n\n"
            "{constraints}\n\n"
            "COMPOSITION REQUIREMENT: Each half should leave its CENTER zone "
            "(vertical middle ~60% of each half's width) relatively empty so a person "
            "can be composited in each half later. TOP 20% of full frame should be "
            "DARKER (subtle gradient) for a Chinese title overlay."
        ),
        ai_includes_subject=False,
        summary="對比底圖 — 冷暖色溫二分、兩側留位給人像",
    ),
    # ──────────────────────────────────────────────────────────────────
    # T-V8: High-Saturation Colour Pop with Bold Command Text
    # Single saturated background, minimal scene, command-style text dominates.
    # ──────────────────────────────────────────────────────────────────
    "T-V8": ThumbnailTemplate(
        tv_id="T-V8",
        name="Colour Pop with Command Text",
        overlay_archetype="tv8-color-pop",
        palette={
            "bg": "#D0021B",
            "bg_2": "#7A010E",
            "fg": "#FFFFFF",
            "accent": "#F5C518",
            "stroke": "#000000",
            "bg_darken": 0.0,
        },
        ai_prompt_template=(
            "Create an EMPTY single-colour background plate for a YouTube thumbnail "
            "(16:9, 1536x1024 landscape). NO people, NO faces, NO subjects — "
            "pure colour field with optional minimal prop.\n\n"
            "ENVIRONMENT: Single highly saturated solid (or barely-gradient) colour "
            "filling the entire frame. NO photographic environment — flat 'designed' "
            "feel like a graphic poster.\n\n"
            "BACKGROUND COLOUR DIRECTION (from director — pick one saturated hue "
            "matching the emotional tone): {bg}  (defaults: red #D0021B / yellow "
            "#F5C518 / green #4CAF50 / orange #F97316)\n"
            "OPTIONAL SMALL FLOATING PROP (single object, non-human, placed in the "
            "RIGHT 30% of frame so it sits beside the host's portrait later — small "
            "scale, casting a hard shadow on the colour field): {decoration}\n\n"
            "STYLE: Pattern-interrupt graphic design. Flat saturated colour as the "
            "dominant visual. If a prop is included, it should photographic but "
            "isolated cleanly against the colour, with a single hard-edge shadow.\n\n"
            "{constraints}\n\n"
            "COMPOSITION REQUIREMENT: LEFT 55-65% of frame must be ENTIRELY EMPTY "
            "saturated colour — no prop, no gradient detail, no shadow — so a giant "
            "white Chinese command title can be overlaid there. Right 35-45% may "
            "contain the optional prop (it will sit beside the host portrait that "
            "is composited in post)."
        ),
        ai_includes_subject=False,
        summary="飽和色塊底圖 — 真實人像疊右、大字疊左",
    ),
    # ──────────────────────────────────────────────────────────────────
    # T-V10: Numerical Metric Hero with Reaction Face
    # Number is the largest element, face reacts. Green for positive,
    # red for warning. Number rendered in CSS overlay (not AI) since we
    # need it to be the specific accent_decoration value.
    # ──────────────────────────────────────────────────────────────────
    "T-V10": ThumbnailTemplate(
        tv_id="T-V10",
        name="Numerical Metric Hero",
        overlay_archetype="tv10-number-hero",
        palette={
            "bg": "#0E2A18",
            "bg_2": "#051208",
            "fg": "#FFFFFF",
            "accent": "#4CAF50",
            "stroke": "#000000",
            "bg_darken": 0.15,
        },
        ai_prompt_template=(
            "Create an EMPTY background plate for a YouTube thumbnail (16:9, 1536x1024 "
            "landscape). NO people, NO faces, NO numbers, NO text.\n\n"
            "ENVIRONMENT: Cinematic dark studio or topic-matching minimal environment, "
            "deep colour grade. Positive / financial / growth metrics → deep green "
            "#0E2A18. Warning / loss metrics → deep red #2A0E0E. Soft directional "
            "key light from upper-right (a person will be composited bottom-right "
            "later, lit from that direction).\n\n"
            "ATMOSPHERE (from director — environment, not subject): {bg}\n"
            "SMALL CONTEXTUAL OBJECT (single, non-human, small, placed near right "
            "edge as set-dressing — NEVER a number): {decoration}\n\n"
            "STYLE: Dramatic cinematic colour grade. Soft bokeh in the empty zone. "
            "Slight atmospheric haze. The empty zone should feel 'designed' — uniform "
            "enough that overlaid type pops cleanly.\n\n"
            "{constraints}\n\n"
            "COMPOSITION REQUIREMENT: LEFT 55-65% of frame must be COMPLETELY EMPTY "
            "(solid colour, gentle gradient, or extreme bokeh — nothing the eye can "
            "lock onto). RIGHT 35-45% may carry subtle environmental detail (the host "
            "portrait will be composited there). Top-left 25% of empty zone slightly "
            "darker for the title overlay."
        ),
        ai_includes_subject=False,
        summary="巨大數字底圖 — 數字 + 標題 CSS 疊左，真實人像疊右",
    ),
}


# Default fallback when no T-V tag matches
_DEFAULT_TEMPLATE_ID = "T-V1"


def get_template(archetype_tags: Iterable[str] | None) -> ThumbnailTemplate:
    """Pick a template from a parsed idea's ``archetype_tags``.

    Scans the tag list for a T-V* identifier present in the registry.
    Tags from the playbook are mixed T-A* (title), T-V* (visual), JP-* (joint).
    Returns the T-V1 fallback if no visual tag matches.
    """
    if archetype_tags:
        for tag in archetype_tags:
            tid = str(tag).strip()
            if tid in TEMPLATES:
                return TEMPLATES[tid]
    return TEMPLATES[_DEFAULT_TEMPLATE_ID]


def build_prompt(template: ThumbnailTemplate, parsed: object) -> str:
    """Substitute parsed-idea fields into the template prompt.

    ``parsed`` is a ParsedIdea (or any object exposing ``.visual``, ``.bg``,
    ``.decoration``). Empty strings are tolerated — the AI gen still has the
    scene/style/constraint blocks to work with.
    """
    visual = (getattr(parsed, "visual", "") or "").strip() or "(none specified)"
    bg = (getattr(parsed, "bg", "") or "").strip() or "(none specified)"
    decoration = (getattr(parsed, "decoration", "") or "").strip() or "(none)"
    return template.ai_prompt_template.format(
        visual=visual,
        bg=bg,
        decoration=decoration,
        constraints=_COMMON_CONSTRAINTS,
    )


def list_templates() -> list[ThumbnailTemplate]:
    """Stable-ordered list of registered templates (for UI / docs / testing)."""
    return [TEMPLATES[k] for k in TEMPLATES]
