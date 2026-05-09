"""Vision 6-class figure triage for ADR-020 S5.

classify_figure(caption, alt_text):
  Keyword heuristic → if uncertain (score == 0) → LLM fallback with caption/alt_text.

group_figure_panels(fig_paths):
  Groups fig-5-1a.png + fig-5-1b.png → single conceptual unit for one Vision call.

get_vision_prompt(fig_class, panels):
  Returns class-specific Vision prompt string, or None for Decorative (skip Vision).

6 classes:
  Quantitative — axes / data series / error bars / statistics
  Structural   — anatomy / molecular / histology / schematic
  Process      — metabolic pathway / flowchart / cascade / mechanism
  Comparative  — side-by-side / before-after / trained-vs-untrained
  Tabular      — image-as-table / grid of text/numbers
  Decorative   — stock photo / portrait / clipart (skip Vision)
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Callable, Literal

from shared.log import get_logger

logger = get_logger("nakama.shared.figure_triage")

FigureClass = Literal[
    "Quantitative", "Structural", "Process", "Comparative", "Tabular", "Decorative"
]

_ALL_CLASSES: tuple[FigureClass, ...] = (
    "Quantitative",
    "Structural",
    "Process",
    "Comparative",
    "Tabular",
    "Decorative",
)

_KEYWORDS: dict[str, list[str]] = {
    "Quantitative": [
        "axes",
        "axis",
        "graph",
        "chart",
        "plot",
        "bar chart",
        "line graph",
        "scatter",
        "histogram",
        "error bar",
        "error bars",
        " mean ",
        "mean ±",
        "mean and",
        "standard deviation",
        " sd ",
        "correlation",
        "regression",
        "vo2",
        "vo₂",
        "heart rate",
        "blood lactate",
        "% maximum",
        "% of",
        "kinetics",
        "dose-response",
        "changes in",
        "peak ",
        "data point",
        "data series",
    ],
    "Structural": [
        "anatomy",
        "anatomical",
        "schematic of",
        "diagram of",
        "cross-section",
        "cross section",
        "histolog",
        "microscop",
        "electron",
        "sarcomere",
        "mitochondria",
        "membrane",
        "organelle",
        "skeletal muscle",
        "cardiac muscle",
        "receptor",
        "molecular",
        "protein structure",
        "cell structure",
        "ultrastructure",
    ],
    "Process": [
        "pathway",
        "metabolic pathway",
        "mechanism",
        "cascade",
        "signal transduction",
        "signaling",
        "signalling",
        "flow ",
        "overview of",
        "steps in",
        "glycolysis",
        "krebs",
        "citric acid cycle",
        "β-oxidation",
        "beta-oxidation",
        "gluconeogenesis",
        "regulation of",
        "process of",
        "lipolysis",
        "stage",
    ],
    "Comparative": [
        " versus ",
        " vs ",
        " vs. ",
        "before and after",
        "trained vs",
        "untrained",
        "comparison of",
        "effect of",
        "compared with",
        "compared to",
        "healthy vs",
        "A vs B",
        "differences between",
        "differences in",
        "side-by-side",
    ],
    "Tabular": [
        " table ",
        "table of",
        "tabular",
        "summary of",
        "summary table",
        "matrix",
        "classification of",
        "overview table",
        "grid",
    ],
    "Decorative": [
        "photograph",
        "photo of",
        " portrait",
        " athlete ",
        "athletes ",
        "clipart",
        "stock photo",
        "chapter opening",
        "background image",
        "illustration of person",
    ],
}

_LLM_CLASSIFY_PROMPT = """\
Classify the following textbook figure as exactly one of:
Quantitative, Structural, Process, Comparative, Tabular, Decorative

Caption: {caption}
Alt text: {alt_text}

Definitions:
- Quantitative: has axes, data series, error bars, statistics
- Structural: anatomy, molecular structure, histology, schematic
- Process: flowchart, metabolic pathway, cascade, mechanism
- Comparative: side-by-side, before-after, trained vs untrained
- Tabular: image is a table or grid of text/numbers
- Decorative: stock photo, portrait, clipart, no scientific content

Respond with exactly one word (the class name):
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_figure(
    caption: str = "",
    alt_text: str = "",
    *,
    _ask_llm: Callable[[str], str] | None = None,
) -> tuple[FigureClass, float]:
    """Classify a figure using a keyword heuristic with LLM fallback.

    Returns (class, confidence). confidence < 0.6 indicates the LLM was used.
    When no keywords match AND _ask_llm is None, defaults to 'Structural'.
    """
    combined = (caption + " " + alt_text).lower()
    scores: dict[str, int] = {cls: 0 for cls in _ALL_CLASSES}

    for cls, keywords in _KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in combined:
                scores[cls] += 1

    best_cls = max(scores, key=lambda c: scores[c])
    best_score = scores[best_cls]

    if best_score > 0:
        conf = min(0.6 + best_score * 0.1, 1.0)
        return best_cls, conf  # type: ignore[return-value]

    return _llm_fallback(caption, alt_text, _ask_llm=_ask_llm)


def group_figure_panels(fig_paths: list[Path]) -> list[list[Path]]:
    """Group multi-panel figures (fig-5-1a, fig-5-1b → one group).

    Files whose names share the same numeric prefix (e.g. ``fig-5-1``) and
    differ only by a trailing letter suffix are grouped together.  Files
    without a letter suffix form single-element groups.
    """
    grouped: dict[str, list[Path]] = defaultdict(list)

    for path in fig_paths:
        stem = path.stem
        m = re.match(r"^(fig-\d+-\d+)([a-z]+)$", stem, re.IGNORECASE)
        if m:
            key = m.group(1)
        else:
            key = stem
        grouped[key].append(path)

    return [paths for paths in grouped.values()]


def get_vision_prompt(fig_class: FigureClass, panels: list[Path]) -> str | None:
    """Return the class-specific Vision prompt, or None for Decorative.

    The prompt references all panels by name when there are multiple (multi-panel
    conceptual unit — describe all panels as a single coherent figure).
    """
    if fig_class == "Decorative":
        return None

    panel_note = ""
    if len(panels) > 1:
        panel_names = ", ".join(p.name for p in panels)
        panel_note = (
            f"\nThis is a multi-panel figure ({panel_names}). "
            "Describe all panels as a single conceptual unit. "
            "Refer to each panel as Panel A, Panel B, etc.\n"
        )

    templates: dict[str, str] = {
        "Quantitative": (
            "You are annotating a Quantitative figure (data, axes, statistics).\n"
            f"{panel_note}"
            "For each axis: record the label, units, and scale (linear/log).\n"
            "For each data series: name, trend direction, key values at critical points.\n"
            "Transcribe error bars (SD/SEM/CI), legend entries, and regression equations"
            " as $$LaTeX$$.\n"
            "State what experimental condition or comparison the data represent.\n"
            "Structured output — use **bold** for every axis label, series name, and key value."
        ),
        "Structural": (
            "You are annotating a Structural figure (anatomy, molecular, histology, schematic).\n"
            f"{panel_note}"
            "Label every named anatomical component, molecular subunit, or spatial region.\n"
            "Describe the spatial relationship between labelled components.\n"
            "For histology/microscopy: specify stain, magnification if shown,"
            " and cell types visible.\n"
            "Structured output — use **bold** for every anatomical label and component name."
        ),
        "Process": (
            "You are annotating a Process figure (metabolic pathway, flowchart, mechanism).\n"
            f"{panel_note}"
            "Identify every step/stage in order (input → output sequence).\n"
            "Name each intermediate compound, enzyme, or regulatory node.\n"
            "Note regulatory direction (activation ↑ / inhibition ↓) for each step.\n"
            "Structured output — use **bold** for compound names, enzyme names, and stage labels."
        ),
        "Comparative": (
            "You are annotating a Comparative figure"
            " (side-by-side, before-after, group comparison).\n"
            f"{panel_note}"
            "Focus on differences: what changed and in which direction.\n"
            "Describe similarities: what remained constant across conditions.\n"
            "Name every condition/group being compared. State the key contrast in one sentence.\n"
            "Structured output — use **bold** for each condition name and the key difference."
        ),
        "Tabular": (
            "You are annotating a Tabular figure (image-as-table, grid of text/numbers).\n"
            f"{panel_note}"
            "Transcribe the table as a Markdown table, preserving:\n"
            "- All row and column headers\n"
            "- Merged cells (use rowspan/colspan description in a note)\n"
            "- Footnote symbols and their footnote text\n"
            "- Nested structure if present\n"
            "Output the Markdown table, then a one-line summary of what it shows."
        ),
    }

    return templates.get(fig_class, templates["Structural"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _llm_fallback(
    caption: str,
    alt_text: str,
    *,
    _ask_llm: Callable[[str], str] | None,
) -> tuple[FigureClass, float]:
    if _ask_llm is None:
        from shared.kb_writer import _ask_llm as _default

        _ask_llm = _default

    prompt = _LLM_CLASSIFY_PROMPT.format(caption=caption or "(none)", alt_text=alt_text or "(none)")
    response = _ask_llm(prompt).strip()

    for cls in _ALL_CLASSES:
        if cls.lower() in response.lower():
            return cls, 0.55

    logger.warning(
        "figure_triage: LLM returned unrecognised class '%s'; defaulting to Structural",
        response[:40],
    )
    return "Structural", 0.3
