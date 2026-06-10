"""Reference-template catalog for template-first thumbnail planning.

The brainstorm step should not invent thumbnail structure from scratch. It
first picks one of these reference templates, then writes the brief and later
component plan against that concrete visual grammar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ReferenceTemplate:
    template_id: str
    label: str
    lane: str
    reference_paths: tuple[str, ...]
    best_for: tuple[str, ...]
    title_keywords: tuple[str, ...]
    component_types: tuple[str, ...]
    layout_rules: tuple[str, ...]
    avoid: tuple[str, ...]


@dataclass(frozen=True)
class TemplateMatch:
    template: ReferenceTemplate
    score: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template.template_id,
            "label": self.template.label,
            "lane": self.template.lane,
            "score": round(self.score, 2),
            "reasons": list(self.reasons),
            "reference_paths": list(self.template.reference_paths),
            "component_types": list(self.template.component_types),
            "layout_rules": list(self.template.layout_rules),
        }


@dataclass(frozen=True)
class TitleTemplateOption:
    template: ReferenceTemplate
    score: float
    reasons: tuple[str, ...]
    viewer_promise: str
    visual_payload: str
    component_type: str
    component_text: tuple[str, ...]
    host_directive: str
    background_directive: str
    asset_directive: str
    trust_risk: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template.template_id,
            "label": self.template.label,
            "lane": self.template.lane,
            "score": round(self.score, 2),
            "reasons": list(self.reasons),
            "component_types": list(self.template.component_types),
            "component_type": self.component_type,
            "component_text": list(self.component_text),
            "viewer_promise": self.viewer_promise,
            "visual_payload": self.visual_payload,
            "host_directive": self.host_directive,
            "background_directive": self.background_directive,
            "asset_directive": self.asset_directive,
            "trust_risk": self.trust_risk,
            "layout_rules": list(self.template.layout_rules),
            "reference_paths": list(self.template.reference_paths),
        }


@dataclass(frozen=True)
class TitleTemplateMatch:
    title_id: str
    title: str
    best_template: ReferenceTemplate
    score: float
    reasons: tuple[str, ...]
    runner_up_template_ids: tuple[str, ...]
    template_options: tuple[TitleTemplateOption, ...]
    viewer_promise: str
    visual_payload: str
    component_type: str
    component_text: tuple[str, ...]
    host_directive: str
    background_directive: str
    asset_directive: str
    trust_risk: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "title_id": self.title_id,
            "title": self.title,
            "best_template_id": self.best_template.template_id,
            "score": round(self.score, 2),
            "reasons": list(self.reasons),
            "runner_up_template_ids": list(self.runner_up_template_ids),
            "template_options": [option.to_dict() for option in self.template_options],
            "viewer_promise": self.viewer_promise,
            "visual_payload": self.visual_payload,
            "component_type": self.component_type,
            "component_text": list(self.component_text),
            "host_directive": self.host_directive,
            "background_directive": self.background_directive,
            "asset_directive": self.asset_directive,
            "trust_risk": self.trust_risk,
        }


REFERENCE_TEMPLATES: tuple[ReferenceTemplate, ...] = (
    ReferenceTemplate(
        template_id="jeff_tool_header_panel",
        label="Jeff Su tool header + product card",
        lane="Jeff Clean Tutorial",
        reference_paths=(
            "E:/thumbnail-example/Jeff Su/Learn 80% of Perplexity in under 10 minutes!.jpg",
            "E:/thumbnail-example/Jeff Su/Learn 80% of NotebookLM in Under 13 Minutes!.jpg",
            "E:/thumbnail-example/Jeff Su/Learn 80% of Claude Cowork in Under 20 Minutes.jpg",
        ),
        best_for=(
            "AI tools",
            "software tutorials",
            "learn X in Y minutes",
            "clean productivity walkthrough",
        ),
        title_keywords=(
            "ai",
            "chatgpt",
            "claude",
            "gemini",
            "notebooklm",
            "perplexity",
            "tool",
            "app",
            "tutorial",
            "minutes",
            "learn",
            "master",
            "教學",
            "工具",
            "學會",
            "分鐘",
            "入門",
        ),
        component_types=("tool_label", "ui_panel", "logo_tile"),
        layout_rules=(
            "branded blurred A-roll background",
            "host sits on one third; product payload owns the opposite third",
            "one large rounded UI panel or search bar; one logo tile max",
            "component text must be readable at thumbnail size",
        ),
        avoid=(
            "more than one competing app panel",
            "tiny UI screenshots",
            "decorative icons without product meaning",
        ),
    ),
    ReferenceTemplate(
        template_id="jeff_command_panel",
        label="Jeff Su dark command panel",
        lane="Jeff Clean Tutorial",
        reference_paths=(
            "E:/thumbnail-example/Jeff Su/95% of People STILL Prompt ChatGPT-5 Wrong.jpg",
            "E:/thumbnail-example/Jeff Su/How to Create Cinematic AI Videos (No-BS Guide).jpg",
        ),
        best_for=(
            "mistake correction",
            "do this instead",
            "prompting workflow",
            "clear before after tutorial",
        ),
        title_keywords=(
            "wrong",
            "correct",
            "instead",
            "mistake",
            "prompt",
            "no-bs",
            "guide",
            "avoid",
            "錯",
            "錯誤",
            "不要",
            "改成",
            "提示詞",
            "取代",
        ),
        component_types=("command_panel", "action_label", "tool_logo"),
        layout_rules=(
            "one dark UI panel as the main component",
            "host points toward the panel or sits adjacent to it",
            "use one strong action phrase, not a paragraph",
        ),
        avoid=("dense code blocks", "unrelated charts", "multiple small screenshots"),
    ),
    ReferenceTemplate(
        template_id="ali_metric_arrow",
        label="Ali metric contrast + arrow",
        lane="Ali Warm Explainer",
        reference_paths=(
            "E:/thumbnail-example/Ali Abdaal/"
            "If I Wanted to Be a Millionaire Before 30, I'd Do This.jpg",
        ),
        best_for=(
            "before after contrast",
            "money/time/health improvement",
            "simple transformation promise",
        ),
        title_keywords=(
            "before",
            "after",
            "millionaire",
            "$",
            "0",
            "1m",
            "hours",
            "save",
            "之前",
            "之後",
            "改變",
            "變成",
            "財富",
            "省下",
        ),
        component_types=("metric_badge_pair", "arrow", "simple_icon"),
        layout_rules=(
            "two very large metric badges on opposite sides",
            "single arrow connects the transformation",
            "host face remains central and unobstructed",
        ),
        avoid=("small explanatory labels", "more than two numbers", "chart clutter"),
    ),
    ReferenceTemplate(
        template_id="ali_social_quote_card",
        label="Ali social card + advice hook",
        lane="Ali Warm Explainer",
        reference_paths=(
            "E:/thumbnail-example/Ali Abdaal/"
            "My honest advice to someone who wants financial freedom.jpg",
        ),
        best_for=("honest advice", "personal reflection", "soft contrarian framing"),
        title_keywords=(
            "honest",
            "advice",
            "someone",
            "wants",
            "freedom",
            "behind",
            "建議",
            "真心",
            "自由",
            "想要",
            "人生",
        ),
        component_types=("quote_card", "social_post_card"),
        layout_rules=(
            "one clean white card with a short phrase",
            "host occupies the opposite third with approachable expression",
            "card must read as a single message, not a document",
        ),
        avoid=("multiple cards", "dense tweet text", "aggressive warning colors"),
    ),
    ReferenceTemplate(
        template_id="shosho_benefit_list_card",
        label="Shosho/Ali benefit list card",
        lane="Ali Warm Explainer",
        reference_paths=(
            "G:/OneDrive/Mad/Thumbnails/20240926 Outlive 2.jpg",
            "G:/OneDrive/Mad/Thumbnails/20240814 The psychology of money part.1.jpg",
        ),
        best_for=(
            "research-backed health list",
            "book or evidence synthesis",
            "supplement benefits",
        ),
        title_keywords=(
            "benefit",
            "benefits",
            "health",
            "brain",
            "aging",
            "longevity",
            "creatine",
            "study",
            "evidence",
            "guide",
            "list",
            "肌酸",
            "健康",
            "大腦",
            "腦",
            "抗老",
            "認知",
            "長壽",
            "研究",
            "實證",
            "醫療",
            "營養",
            "補充品",
        ),
        component_types=("benefit_list_card", "object_cutout", "small_badge"),
        layout_rules=(
            "one large card with three short benefit lines",
            "card sits in the content third opposite the host gaze",
            "supporting object is optional and must stay secondary",
        ),
        avoid=("medical miracle framing", "more than four list items", "tiny research text"),
    ),
)

REFERENCE_TEMPLATE_IDS = frozenset(t.template_id for t in REFERENCE_TEMPLATES)
_TEMPLATE_BY_ID = {t.template_id: t for t in REFERENCE_TEMPLATES}
_FALLBACK_PRIOR = {
    "shosho_benefit_list_card": 1.4,
    "jeff_tool_header_panel": 1.3,
    "ali_social_quote_card": 1.2,
    "jeff_command_panel": 1.1,
    "ali_metric_arrow": 1.0,
}


def get_reference_template(template_id: str) -> ReferenceTemplate:
    try:
        return _TEMPLATE_BY_ID[template_id]
    except KeyError as exc:
        raise KeyError(f"unknown thumbnail reference template: {template_id}") from exc


def select_reference_templates(
    *,
    title_candidates: Iterable[str],
    project_brief: str = "",
    idea_text: str = "",
    limit: int = 3,
) -> list[TemplateMatch]:
    """Return the best matching reference templates for this project/idea."""

    title_haystack = _normalize(" ".join(title_candidates))
    context_haystack = _normalize(" ".join([project_brief, idea_text]))
    haystack = _normalize(" ".join([title_haystack, context_haystack]))
    matches: list[TemplateMatch] = []
    for template in REFERENCE_TEMPLATES:
        score = 0.0
        reasons: list[str] = []
        for keyword in template.title_keywords:
            normalized_keyword = _normalize(keyword)
            if not normalized_keyword:
                continue
            if normalized_keyword in title_haystack:
                score += 10.0
                reasons.append(f"title_keyword:{keyword}")
            elif normalized_keyword in context_haystack:
                score += 3.0
                reasons.append(f"context_keyword:{keyword}")
        for phrase in template.best_for:
            if _phrase_overlap(phrase, haystack) >= 2:
                score += 4.0
                reasons.append(f"best_for:{phrase}")
        health_tokens = (
            "health",
            "creatine",
            "longevity",
            "brain",
            "aging",
            "肌酸",
            "健康",
            "大腦",
            "認知",
            "長壽",
            "抗老",
            "研究",
        )
        tool_tokens = ("tool", "app", "ai", "minutes", "tutorial", "工具", "教學", "分鐘")
        if template.template_id == "shosho_benefit_list_card":
            if any(token in title_haystack for token in health_tokens):
                score += 10.0
                reasons.append("lane:title-ali-health-fit")
            elif any(token in context_haystack for token in health_tokens):
                score += 3.0
                reasons.append("lane:context-ali-health-fit")
        if template.lane.lower().startswith("jeff"):
            if any(token in title_haystack for token in tool_tokens):
                score += 10.0
                reasons.append("lane:title-jeff-tool-fit")
            elif any(token in context_haystack for token in tool_tokens):
                score += 3.0
                reasons.append("lane:context-jeff-tool-fit")
        if score <= 0:
            score = _FALLBACK_PRIOR.get(template.template_id, 1.0)
            reasons.append("fallback:general")
        matches.append(TemplateMatch(template=template, score=score, reasons=tuple(reasons[:5])))

    matches.sort(key=lambda m: (-m.score, m.template.template_id))
    return matches[: max(1, limit)]


def format_reference_template_pack_for_prompt(
    *,
    title_candidates: Iterable[str],
    project_brief: str = "",
    limit: int = 3,
) -> str:
    """Format selected templates as a compact LLM prompt section."""

    selected = select_reference_templates(
        title_candidates=title_candidates,
        project_brief=project_brief,
        limit=limit,
    )
    lines = [
        "## Reference templates selected before thumbnail brief",
        "",
        (
            "Before writing any thumbnail idea, choose exactly one of these "
            "reference_template IDs. The component brief must follow that "
            "template's layout rules. Do not invent a new layout grammar."
        ),
        "",
    ]
    for match in selected:
        t = match.template
        refs = [p for p in t.reference_paths if Path(p).exists()]
        lines.extend(
            [
                f"### {t.template_id} — {t.label}",
                f"- lane: {t.lane}",
                f"- score_reason: {', '.join(match.reasons)}",
                f"- best_for: {'; '.join(t.best_for)}",
                f"- component_types: {', '.join(t.component_types)}",
                f"- layout_rules: {'; '.join(t.layout_rules)}",
                f"- avoid: {'; '.join(t.avoid)}",
                f"- local_references: {'; '.join(refs) if refs else '(paths unavailable)'}",
                "",
            ]
        )
    lines.extend(
        [
            "Required idea metadata line:",
            "reference_template: <one selected reference_template ID>",
        ]
    )
    return "\n".join(lines)


def build_title_template_match_plan(
    *,
    title_candidates: Iterable[str],
    project_brief: str = "",
    limit: int = 12,
) -> list[TitleTemplateMatch]:
    """Match each title idea to a concrete thumbnail reference template.

    This runs before the LLM writes thumbnail briefs. It turns a broad title
    pool into slot-level visual constraints so the brainstorm prompt no longer
    needs to invent composition grammar from scratch.
    """

    titles = [str(title).strip() for title in title_candidates if str(title).strip()]
    plan: list[TitleTemplateMatch] = []
    for index, title in enumerate(titles[: max(1, limit)], start=1):
        matches = _strong_template_matches(
            select_reference_templates(
                title_candidates=[title],
                project_brief=project_brief,
                limit=5,
            )
        )[:3]
        best = matches[0]
        template_options = tuple(
            _title_template_option(
                title=title,
                project_brief=project_brief,
                match=match,
            )
            for match in matches
        )
        best_option = template_options[0]
        plan.append(
            TitleTemplateMatch(
                title_id=f"T{index:02d}",
                title=title,
                best_template=best.template,
                score=best.score,
                reasons=best.reasons,
                runner_up_template_ids=tuple(match.template.template_id for match in matches[1:]),
                template_options=template_options,
                viewer_promise=best_option.viewer_promise,
                visual_payload=best_option.visual_payload,
                component_type=best_option.component_type,
                component_text=best_option.component_text,
                host_directive=best_option.host_directive,
                background_directive=best_option.background_directive,
                asset_directive=best_option.asset_directive,
                trust_risk=best_option.trust_risk,
            )
        )
    return plan


def _strong_template_matches(matches: list[TemplateMatch]) -> list[TemplateMatch]:
    """Keep prompt options that are real matches, not alphabetical fallbacks."""

    if not matches:
        return matches
    best = matches[0]
    strong: list[TemplateMatch] = []
    for match in matches:
        if "fallback:general" in match.reasons and match is not best:
            continue
        if match is best:
            strong.append(match)
            continue
        if match.score >= 8.0 and match.score >= best.score * 0.35:
            strong.append(match)
    return strong or [best]


def format_title_template_match_plan_for_prompt(
    *,
    title_candidates: Iterable[str],
    project_brief: str = "",
    limit: int = 12,
) -> str:
    """Format the title-template match table consumed by the brainstorm LLM."""

    plan = build_title_template_match_plan(
        title_candidates=title_candidates,
        project_brief=project_brief,
        limit=limit,
    )
    lines = [
        "## Title-template match plan",
        "",
        (
            "Use this match plan as the only source for thumbnail structure. "
            "Pick one title_id as the publish title, then create 3 variants by "
            "choosing exactly one template_option from the chosen title row. "
            "Keep the final brief slot-based and short. Never mix component, "
            "text, host, or background from different template_options."
        ),
        "",
    ]
    for item in plan:
        template = item.best_template
        lines.extend(
            [
                f"### {item.title_id}",
                f"- title: {item.title}",
                f"- best_template: {template.template_id} ({template.label})",
                f"- score_reason: {', '.join(item.reasons)}",
                f"- runner_up_templates: {', '.join(item.runner_up_template_ids) or '(none)'}",
                "- template_options:",
            ]
        )
        for option in item.template_options:
            option_template = option.template
            option_refs = [p for p in option_template.reference_paths if Path(p).exists()]
            lines.extend(
                [
                    (
                        f"  - {option_template.template_id}: "
                        f"label={option_template.label}; "
                        f"lane={option_template.lane}; "
                        f"score={option.score:.2f}; "
                        f"component={option.component_type}; "
                        f"text={' / '.join(option.component_text)}; "
                        f"host={option.host_directive}; "
                        f"background={option.background_directive}"
                    ),
                    f"    viewer_promise: {option.viewer_promise}",
                    f"    visual_payload: {option.visual_payload}",
                    f"    asset: {option.asset_directive}",
                    f"    trust_risk: {option.trust_risk}",
                    f"    layout_rules: {'; '.join(option_template.layout_rules)}",
                    (
                        "    local_references: "
                        f"{'; '.join(option_refs) if option_refs else '(paths unavailable)'}"
                    ),
                ]
            )
        lines.extend(
            [
                (
                    "- brief_contract: choose 1 template_option above; "
                    "copy template/component/text/host/background from that same option; "
                    "visual must be <= 120 chars and use "
                    "`template=...; component=...; text=...; host=...`"
                ),
                "",
            ]
        )
    lines.extend(
        [
            "Required idea metadata lines:",
            "reference_template: <one template_option ID from the chosen title row>",
            "component: <component from the same template_option>",
            "component_text: <labels from the same template_option, separated by />",
            "host: <host directive from the same template_option>",
            "background: <background directive from the same template_option>",
        ]
    )
    return "\n".join(lines)


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().replace("：", ":").split())


def _phrase_overlap(phrase: str, haystack: str) -> int:
    return sum(1 for token in _normalize(phrase).split() if token and token in haystack)


def _primary_component_type(template: ReferenceTemplate) -> str:
    return template.component_types[0] if template.component_types else "primary_payload"


def _title_template_option(
    *,
    title: str,
    project_brief: str,
    match: TemplateMatch,
) -> TitleTemplateOption:
    template = match.template
    return TitleTemplateOption(
        template=template,
        score=match.score,
        reasons=match.reasons,
        viewer_promise=_viewer_promise_for(title, template),
        visual_payload=_visual_payload_for(template),
        component_type=_primary_component_type(template),
        component_text=_component_text_for(
            title=title,
            project_brief=project_brief,
            template=template,
        ),
        host_directive=_host_directive_for(template),
        background_directive=_background_directive_for(template),
        asset_directive=_asset_directive_for(title, template),
        trust_risk=_trust_risk_for(title, template),
    )


def _component_text_for(
    *,
    title: str,
    project_brief: str,
    template: ReferenceTemplate,
) -> tuple[str, ...]:
    text = f"{title} {project_brief}"
    if template.template_id == "shosho_benefit_list_card":
        labels = _dedupe(
            token
            for token in (
                "護腦",
                "抗老",
                "認知",
                "增力",
                "長壽",
                "實證",
                "睡眠",
                "代謝",
            )
            if token in text
        )
        if len(labels) >= 3:
            return tuple(labels[:3])
        return tuple([*labels, "護腦", "抗老", "增力"][:3])
    if template.template_id == "ali_metric_arrow":
        numbers = re.findall(r"\$?\d+[A-Za-z%％]*|[０-９]+", text)
        if len(numbers) >= 2:
            return (numbers[0], numbers[1])
        if numbers:
            return ("Before", numbers[0])
        return ("Before", "After")
    if template.template_id == "ali_social_quote_card":
        return (_compact_phrase(title, max_chars=10),)
    if template.template_id == "jeff_command_panel":
        return ("不要這樣", "改成這樣")
    if template.template_id == "jeff_tool_header_panel":
        tool = _first_latin_product_name(title) or "Tool"
        return (tool, "Start Here")
    return (_compact_phrase(title, max_chars=10),)


def _viewer_promise_for(title: str, template: ReferenceTemplate) -> str:
    if template.template_id == "shosho_benefit_list_card":
        return "快速看懂這集最值得點進來的 3 個健康效益"
    if template.template_id == "ali_metric_arrow":
        return "一眼看懂前後差異或結果變化"
    if template.template_id == "ali_social_quote_card":
        return "得到一個可信、像朋友提醒的核心建議"
    if template.template_id == "jeff_command_panel":
        return "知道目前做錯什麼，以及應該改成什麼"
    if template.template_id == "jeff_tool_header_panel":
        return "快速學會一個工具或流程的起點"
    return f"點進來理解：{_compact_phrase(title, max_chars=18)}"


def _visual_payload_for(template: ReferenceTemplate) -> str:
    mapping = {
        "shosho_benefit_list_card": "one benefit_list_card with 3 short labels",
        "ali_metric_arrow": "two metric badges connected by one arrow",
        "ali_social_quote_card": "one white quote/social card",
        "jeff_command_panel": "one dark command panel with one action label",
        "jeff_tool_header_panel": "one tool label, one UI panel, one logo tile max",
    }
    return mapping.get(template.template_id, "; ".join(template.component_types))


def _host_directive_for(template: ReferenceTemplate) -> str:
    if template.template_id == "ali_metric_arrow":
        return "center third, face large, hands/eyes support the metric contrast"
    if template.template_id.startswith("jeff_"):
        return "one third, friendly, gaze or gesture toward the payload"
    return "left or right third, face large, gaze toward the component card"


def _background_directive_for(template: ReferenceTemplate) -> str:
    if template.template_id.startswith("jeff_"):
        return "blurred branded A-roll background, cool neutral, low detail"
    return "blurred branded A-roll warm study background, low detail"


def _asset_directive_for(title: str, template: ReferenceTemplate) -> str:
    if template.template_id == "shosho_benefit_list_card":
        return "optional supplement/object cutout; do not compete with the card"
    if template.template_id == "ali_metric_arrow":
        return "simple arrow plus optional small topic icon"
    if template.template_id == "ali_social_quote_card":
        return "single clean quote/social card, no extra icons"
    if template.template_id == "jeff_command_panel":
        return "tool logo only if central to the title"
    if template.template_id == "jeff_tool_header_panel":
        return f"logo/UI for {_first_latin_product_name(title) or 'the tool'}"
    return "one supporting asset max"


def _trust_risk_for(title: str, template: ReferenceTemplate) -> str:
    text = f"{title} {' '.join(template.best_for)}"
    if any(token in text.lower() for token in ("health", "creatine", "brain")) or any(
        token in text for token in ("肌酸", "健康", "大腦", "抗老", "醫療")
    ):
        return "避免把健康效益說成治療或神藥"
    if template.template_id.startswith("jeff_"):
        return "避免承諾一看就精通；只承諾起點或正確方向"
    return "避免誇大結果；讓畫面只承諾一個清楚收穫"


def _first_latin_product_name(text: str) -> str:
    for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,24}", text):
        if token.lower() not in {"learn", "master", "guide", "under", "minutes", "wrong"}:
            return token
    return ""


def _compact_phrase(text: str, *, max_chars: int) -> str:
    cleaned = re.sub(r"[：:｜|].*$", "", text).strip()
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned[:max_chars] or "核心重點"


def _dedupe(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
