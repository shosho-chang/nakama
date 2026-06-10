"""Build V3 external image-model prompt packages for thumbnail ideas.

This is the first thin slice of ADR-046 Thumbnail V3: keep the existing
``thumbnail_ideas`` contract, but compile each parsed idea into a prompt package
that can be copied into GPT Image 2, Prompt Edit, Scenario, or similar tools.

No OpenAI call happens here. The output is deterministic and testable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from shared.thumbnail_idea import ParsedIdea
from shared.thumbnail_reference_templates import get_reference_template

_DEFAULT_PROVIDER = "gpt-image-2"
_DEFAULT_SIZE = "1536x1024"
_DEFAULT_QUALITY = "high"


@dataclass(frozen=True)
class VisualStrategyV3:
    """Provider-neutral thumbnail visual strategy."""

    schema_version: str
    visual_strategy_id: str
    reference_template_id: str
    style_contract_id: str
    title_pairing: str
    subject: str
    object_group: str
    curiosity: str
    thumbnail_text: str
    text_rendering_policy: str
    focal_points: tuple[str, ...]
    composition_rules: tuple[str, ...]
    style_rules: tuple[str, ...]
    negative_rules: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReferenceBindingV3:
    """Reference upload slot for manual external tools."""

    reference_id: str
    provider_label: str
    role: str
    required: bool
    status: str
    usage: str
    path_hint: str = ""
    local_path: str = ""
    readiness_label: str = ""
    asset_need_id: str = ""
    search_query: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GenerationPromptPackageV3:
    """Copyable prompt package shown in the Title & Thumbnail UI."""

    schema_version: str
    package_id: str
    provider_profile: str
    model: str
    size: str
    quality: str
    visual_strategy: VisualStrategyV3
    reference_bindings: tuple[ReferenceBindingV3, ...]
    prompt_text: str
    negative_prompt: str
    overlay_text: str
    provider_notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["visual_strategy"] = self.visual_strategy.to_dict()
        data["reference_bindings"] = [binding.to_dict() for binding in self.reference_bindings]
        return data


def build_thumbnail_prompt_package(
    idea: ParsedIdea,
    *,
    idea_index: int = 0,
    provider_profile: str = "openai_image_api",
    model: str = _DEFAULT_PROVIDER,
    asset_manifest: dict[str, Any] | None = None,
    host_reference_path: str = "",
) -> GenerationPromptPackageV3:
    """Compile a parsed thumbnail idea into a V3 prompt package."""

    strategy = build_visual_strategy(idea, idea_index=idea_index)
    bindings = build_reference_bindings(
        idea,
        idea_index=idea_index,
        asset_manifest=asset_manifest,
        host_reference_path=host_reference_path,
    )
    prompt = _compile_prompt(strategy, bindings, model=model)
    negative_prompt = _compile_negative_prompt(strategy)

    return GenerationPromptPackageV3(
        schema_version="thumbnail_generation_prompt_package_v3",
        package_id=f"thumb-v3-prompt-{idea_index + 1:02d}",
        provider_profile=provider_profile,
        model=model,
        size=_DEFAULT_SIZE,
        quality=_DEFAULT_QUALITY,
        visual_strategy=strategy,
        reference_bindings=bindings,
        prompt_text=prompt,
        negative_prompt=negative_prompt,
        overlay_text=idea.hook.strip(),
        provider_notes=(
            "Upload references in the listed order when using manual tools.",
            "Generate the image without final Traditional Chinese headline text.",
            "Nakama will overlay exact Traditional Chinese text after import.",
        ),
    )


def build_visual_strategy(idea: ParsedIdea, *, idea_index: int = 0) -> VisualStrategyV3:
    template_id = idea.reference_template_id or _fallback_template_id(idea)
    style_contract_id = idea.recipe_id or _fallback_style_contract(idea, template_id)
    object_group = _object_group(idea)

    focal_points = tuple(
        item
        for item in (
            "Shosho face / main subject",
            object_group,
            f"reserved lower-third text zone for {idea.hook.strip()}",
        )
        if item
    )[:3]

    return VisualStrategyV3(
        schema_version="thumbnail_visual_strategy_v3",
        visual_strategy_id=f"thumb-v3-strategy-{idea_index + 1:02d}",
        reference_template_id=template_id,
        style_contract_id=style_contract_id,
        title_pairing=idea.title_pairing.strip(),
        subject=_subject(idea),
        object_group=object_group,
        curiosity=idea.viewer_promise.strip() or idea.visual.strip(),
        thumbnail_text=idea.hook.strip(),
        text_rendering_policy="deterministic_overlay",
        focal_points=focal_points,
        composition_rules=(
            _composition_rule(idea),
            "Keep the main face readable at 160x90 YouTube feed size.",
            "Reserve a clean high-contrast lower-third area for later text overlay.",
            "Use at most three focal points.",
        ),
        style_rules=(
            "Warm, credible educational YouTube thumbnail.",
            "Ali Abdaal warmth plus Jeff Su clean component clarity.",
            "High contrast, crisp lighting, premium but not hypey.",
        ),
        negative_rules=(
            "No final Traditional Chinese headline text inside the generated image.",
            "No extra labels, captions, UI words, logos, or random stickers.",
            "No hospital, miracle-cure, supplement-ad, or fake-science aesthetic.",
            "No distorted face, hands, or body.",
            "No cluttered object pile; keep the scene simple.",
        ),
    )


def build_reference_bindings(
    idea: ParsedIdea,
    *,
    idea_index: int = 0,
    asset_manifest: dict[str, Any] | None = None,
    host_reference_path: str = "",
) -> tuple[ReferenceBindingV3, ...]:
    host_path = host_reference_path.strip()
    bindings = [
        ReferenceBindingV3(
            reference_id="shosho-host-reference",
            provider_label="img 1",
            role="person_reference",
            required=True,
            status="ready" if host_path and Path(host_path).is_file() else "missing",
            readiness_label="Ready" if host_path and Path(host_path).is_file() else "Needs clean host photo",
            usage="Host identity, face, body pose, and overall likeness.",
            path_hint="Attach a clean, well-lit Shosho portrait or selfie.",
            local_path=host_path if host_path and Path(host_path).is_file() else "",
        )
    ]

    if idea.asset_queries or idea.component_type or idea.component_text:
        asset = _best_asset_manifest_item(asset_manifest, idea_index=idea_index)
        provenance = asset.get("provenance") if isinstance(asset, dict) else {}
        local_path = str(provenance.get("local_path") or "").strip() if isinstance(provenance, dict) else ""
        ready = bool(local_path and Path(local_path).is_file())
        bindings.append(
            ReferenceBindingV3(
                reference_id="episode-object-reference",
                provider_label=f"img {len(bindings) + 1}",
                role="object_reference",
                required=False,
                status="ready" if ready else "optional_missing",
                readiness_label="Ready" if ready else "Optional asset not selected",
                usage=_object_reference_usage(idea),
                path_hint="Use a licensed product/object/component reference when it matters.",
                local_path=local_path if ready else "",
                asset_need_id=str(asset.get("asset_need_id") or "") if isinstance(asset, dict) else "",
                search_query=str(asset.get("query") or "") if isinstance(asset, dict) else "",
            )
        )

    template_id = idea.reference_template_id or _fallback_template_id(idea)
    style_path = _style_reference_path(template_id)
    style_ready = bool(style_path and Path(style_path).is_file())
    bindings.append(
        ReferenceBindingV3(
            reference_id=template_id,
            provider_label=f"img {len(bindings) + 1}",
            role="style_reference",
            required=True,
            status="ready" if style_ready else "missing",
            readiness_label="Ready" if style_ready else "Reference template image missing",
            usage="Use composition/style inspiration only; do not copy exact content.",
            path_hint="Use the selected Ali/Jeff/Shosho reference template image.",
            local_path=style_path if style_ready else "",
        )
    )
    return tuple(bindings)


def _compile_prompt(
    strategy: VisualStrategyV3,
    bindings: tuple[ReferenceBindingV3, ...],
    *,
    model: str,
) -> str:
    binding_lines = "\n".join(
        f"- Use [{binding.provider_label}] as {binding.role}: {binding.usage}"
        for binding in bindings
    )
    focal_lines = "\n".join(
        f"{idx}. {point}" for idx, point in enumerate(strategy.focal_points, start=1)
    )
    composition_lines = "\n".join(f"- {rule}" for rule in strategy.composition_rules)
    style_lines = "\n".join(f"- {rule}" for rule in strategy.style_rules)

    title_line = (
        f"Paired YouTube title: {strategy.title_pairing}\n"
        if strategy.title_pairing
        else ""
    )

    return (
        f"Create a professional 16:9 YouTube thumbnail base image for {model}.\n\n"
        f"{title_line}"
        "Reference bindings:\n"
        f"{binding_lines}\n\n"
        "Scene:\n"
        f"- Main subject: {strategy.subject}\n"
        f"- Episode object/context: {strategy.object_group}\n"
        f"- Curiosity promise: {strategy.curiosity}\n\n"
        "Composition:\n"
        f"{composition_lines}\n\n"
        "Focal points, exactly three or fewer:\n"
        f"{focal_lines}\n\n"
        "Text policy:\n"
        f"- Do not render the final Traditional Chinese headline text.\n"
        f"- Leave a clean lower-third area where Nakama can overlay: {strategy.thumbnail_text}\n"
        "- Blank cards or blank label rows are acceptable when the component needs labels later.\n\n"
        "Style:\n"
        f"{style_lines}\n\n"
        "Output:\n"
        "- Landscape 16:9 composition.\n"
        "- No watermark, no fake platform chrome, no extra text.\n"
    )


def _compile_negative_prompt(strategy: VisualStrategyV3) -> str:
    return " ".join(strategy.negative_rules)


def _subject(idea: ParsedIdea) -> str:
    directive = idea.host_directive.strip()
    if directive:
        return f"Shosho as the host; {directive}"
    return f"Shosho as the host; expression should feel {idea.emotion_key}"


def _object_group(idea: ParsedIdea) -> str:
    labels = [label for label in idea.component_text if label]
    if labels:
        return f"{idea.component_type or 'clean component card'} with labels: {', '.join(labels[:3])}"
    if idea.decoration:
        return idea.decoration.strip()
    if idea.asset_queries:
        return "; ".join(idea.asset_queries[:2])
    return idea.visual.strip()


def _composition_rule(idea: ParsedIdea) -> str:
    host = idea.host_directive.lower()
    if "left" in host and "right" not in host:
        return "Place host on the left third and object/context on the right third."
    if "right" in host and "left" not in host:
        return "Place host on the right third and object/context on the left third."
    return "Place host on a visual third and reserve the opposite third for the object/context."


def _object_reference_usage(idea: ParsedIdea) -> str:
    if idea.asset_queries:
        return f"Episode-specific object reference for: {'; '.join(idea.asset_queries[:3])}."
    if idea.component_text:
        return f"Component/content reference for labels: {', '.join(idea.component_text[:3])}."
    return "Episode-specific object/component reference."


def _best_asset_manifest_item(
    manifest: dict[str, Any] | None,
    *,
    idea_index: int,
) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        return {}
    items = manifest.get("items")
    if not isinstance(items, list):
        return {}
    candidates = [
        item
        for item in items
        if isinstance(item, dict) and _safe_idea_index(item) == idea_index
    ]
    if not candidates:
        return {}

    def rank(item: dict[str, Any]) -> tuple[int, str]:
        provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
        local_path = str(provenance.get("local_path") or "").strip()
        has_file = bool(local_path and Path(local_path).is_file())
        status = str(item.get("status") or "")
        if has_file and status == "licensed":
            return (0, str(item.get("asset_need_id") or ""))
        if has_file:
            return (1, str(item.get("asset_need_id") or ""))
        if status == "candidate_found":
            return (2, str(item.get("asset_need_id") or ""))
        return (3, str(item.get("asset_need_id") or ""))

    candidates.sort(key=rank)
    return candidates[0]


def _safe_idea_index(item: dict[str, Any]) -> int | None:
    try:
        return int(item.get("idea_index", -1))
    except (TypeError, ValueError):
        return None


def _style_reference_path(template_id: str) -> str:
    try:
        template = get_reference_template(template_id)
    except KeyError:
        return ""
    for raw_path in template.reference_paths:
        path = str(raw_path).strip()
        if path and Path(path).is_file():
            return path
    return str(template.reference_paths[0]) if template.reference_paths else ""


def _fallback_template_id(idea: ParsedIdea) -> str:
    text = " ".join(
        (
            idea.reference_template_id,
            idea.recipe_id,
            idea.lane,
            idea.component_type,
            idea.visual,
        )
    ).lower()
    if "jeff" in text or "tool" in text or "tutorial" in text:
        return "jeff_tool_header_panel"
    if "metric" in text or "arrow" in text:
        return "ali_metric_arrow"
    if "quote" in text or "social" in text:
        return "ali_social_quote_card"
    return "shosho_benefit_list_card"


def _fallback_style_contract(idea: ParsedIdea, template_id: str) -> str:
    if idea.recipe_id:
        return idea.recipe_id
    if template_id.startswith("jeff"):
        return "jeff_clean_component"
    if template_id.startswith("ali"):
        return "ali_warm_explainer"
    return "shosho_external_image_base"
