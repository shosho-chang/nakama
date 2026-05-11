"""Tests for shared.figure_triage (ADR-020 S5).

Heuristic classify_figure tests are deterministic.
LLM fallback path accepts _ask_llm stub.
"""

from __future__ import annotations

from pathlib import Path

from shared.figure_triage import (
    classify_figure,
    get_vision_prompt,
    group_figure_panels,
)

# ---------------------------------------------------------------------------
# classify_figure — heuristic rules
# ---------------------------------------------------------------------------


def test_quantitative_axes_keyword():
    cls, conf = classify_figure(caption="Graph showing VO2max response across intensities.")
    assert cls == "Quantitative"
    assert conf >= 0.6


def test_quantitative_error_bar_keyword():
    cls, _ = classify_figure(caption="Mean ± SD values with error bars for each condition.")
    assert cls == "Quantitative"


def test_structural_anatomy_keyword():
    cls, conf = classify_figure(caption="Schematic diagram of skeletal muscle sarcomere structure.")
    assert cls == "Structural"
    assert conf >= 0.6


def test_structural_histology_keyword():
    cls, _ = classify_figure(caption="Histological cross-section of cardiac muscle.")
    assert cls == "Structural"


def test_process_pathway_keyword():
    cls, conf = classify_figure(caption="Overview of glycolysis metabolic pathway.")
    assert cls == "Process"
    assert conf >= 0.6


def test_process_signaling_keyword():
    cls, _ = classify_figure(caption="Cascade of signal transduction mechanism.")
    assert cls == "Process"


def test_comparative_versus_keyword():
    cls, conf = classify_figure(caption="Comparison of trained vs. untrained muscle fibre types.")
    assert cls == "Comparative"
    assert conf >= 0.6


def test_comparative_before_after_keyword():
    cls, _ = classify_figure(caption="Muscle fibre density before and after training.")
    assert cls == "Comparative"


def test_tabular_table_keyword():
    cls, conf = classify_figure(caption="Summary table of energy systems.")
    assert cls == "Tabular"
    assert conf >= 0.6


def test_decorative_photo_keyword():
    cls, conf = classify_figure(caption="Photograph of athlete performing sprint exercise.")
    assert cls == "Decorative"
    assert conf >= 0.6


def test_decorative_portrait_keyword():
    cls, _ = classify_figure(alt_text="Portrait photo of the researcher.")
    assert cls == "Decorative"


# ---------------------------------------------------------------------------
# classify_figure — alt_text also contributes
# ---------------------------------------------------------------------------


def test_alt_text_contributes_to_classification():
    cls, _ = classify_figure(caption="Figure 3.2", alt_text="bar chart showing mean values.")
    assert cls == "Quantitative"


# ---------------------------------------------------------------------------
# classify_figure — LLM fallback for uncertain cases
# ---------------------------------------------------------------------------


def test_uncertain_falls_back_to_llm():
    cls, _ = classify_figure(
        caption="Figure 1.1",
        alt_text="",
        _ask_llm=lambda p: "Structural",
    )
    assert cls == "Structural"


def test_uncertain_llm_classifies_process():
    cls, _ = classify_figure(
        caption="See text for details.",
        alt_text="",
        _ask_llm=lambda p: "Process",
    )
    assert cls == "Process"


def test_uncertain_invalid_llm_defaults_to_structural():
    cls, _ = classify_figure(
        caption="Figure 1.1",
        alt_text="",
        _ask_llm=lambda p: "something invalid XYZ",
    )
    assert cls == "Structural"


# ---------------------------------------------------------------------------
# group_figure_panels
# ---------------------------------------------------------------------------


def test_group_abc_panels():
    paths = [Path("fig-5-1a.png"), Path("fig-5-1b.png"), Path("fig-5-1c.png")]
    groups = group_figure_panels(paths)
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_group_single_fig_no_suffix():
    paths = [Path("fig-5-1.png")]
    groups = group_figure_panels(paths)
    assert len(groups) == 1
    assert len(groups[0]) == 1


def test_group_multiple_independent_figs():
    paths = [Path("fig-5-1.png"), Path("fig-5-2.png"), Path("fig-5-3.png")]
    groups = group_figure_panels(paths)
    assert len(groups) == 3
    assert all(len(g) == 1 for g in groups)


def test_group_mixed():
    paths = [
        Path("fig-5-1a.png"),
        Path("fig-5-1b.png"),
        Path("fig-5-2.png"),
        Path("fig-5-3a.png"),
        Path("fig-5-3b.png"),
    ]
    groups = group_figure_panels(paths)
    assert len(groups) == 3
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 2, 2]


def test_group_preserves_order():
    paths = [Path("fig-2-1a.png"), Path("fig-2-1b.png")]
    groups = group_figure_panels(paths)
    names = [p.name for p in groups[0]]
    assert names == ["fig-2-1a.png", "fig-2-1b.png"]


# ---------------------------------------------------------------------------
# get_vision_prompt
# ---------------------------------------------------------------------------


def test_decorative_returns_none():
    assert get_vision_prompt("Decorative", [Path("fig-5-1.png")]) is None


def test_quantitative_returns_string():
    prompt = get_vision_prompt("Quantitative", [Path("fig-5-1.png")])
    assert isinstance(prompt, str)
    assert len(prompt) > 50


def test_quantitative_prompt_mentions_axes():
    prompt = get_vision_prompt("Quantitative", [Path("fig-5-1.png")])
    assert "axes" in prompt.lower() or "axis" in prompt.lower() or "data" in prompt.lower()


def test_structural_prompt_mentions_components():
    prompt = get_vision_prompt("Structural", [Path("fig-5-1.png")])
    assert any(w in prompt.lower() for w in ("component", "label", "spatial", "anatomical"))


def test_process_prompt_mentions_sequence():
    prompt = get_vision_prompt("Process", [Path("fig-5-1.png")])
    assert any(w in prompt.lower() for w in ("step", "sequence", "input", "output", "stage"))


def test_comparative_prompt_mentions_differences():
    prompt = get_vision_prompt("Comparative", [Path("fig-5-1.png")])
    assert any(w in prompt.lower() for w in ("difference", "similar", "contrast", "comparison"))


def test_tabular_prompt_mentions_table():
    prompt = get_vision_prompt("Tabular", [Path("fig-5-1.png")])
    assert "table" in prompt.lower() or "markdown" in prompt.lower()


def test_multi_panel_prompt_references_panels():
    panels = [Path("fig-5-1a.png"), Path("fig-5-1b.png")]
    prompt = get_vision_prompt("Process", panels)
    assert "panel" in prompt.lower() or "a" in prompt.lower()


def test_get_vision_prompt_all_non_decorative_classes():
    for cls in ("Quantitative", "Structural", "Process", "Comparative", "Tabular"):
        prompt = get_vision_prompt(cls, [Path("fig-1-1.png")])
        assert prompt is not None
        assert isinstance(prompt, str)
