"""Focused defaults for the long-form punch card composition."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WIDE_COMPOSITION = (
    REPO_ROOT / "video" / "compositions" / "punch_card" / "compositions" / "punch_card_wide.html"
)
SHORT_COMPOSITION = WIDE_COMPOSITION.with_name("punch_card.html")


def test_wide_hero_defaults_to_compact_paper_style() -> None:
    html = WIDE_COMPOSITION.read_text(encoding="utf-8")

    assert ('{"id":"style","type":"string","label":"orange|paper|ink","default":"paper"}') in html
    assert ".tier1 .line {\n        font-size: 96px;" in html
    assert '["orange", "paper", "ink"].includes(vars.style)' in html
    assert '? vars.style : "paper";' in html


def test_vertical_short_composition_keeps_its_existing_scale_and_style() -> None:
    html = SHORT_COMPOSITION.read_text(encoding="utf-8")

    assert 'data-width="1080"' in html
    assert '"default":"orange"' in html
    assert ".tier1 .line {\n        font-size: 190px;" in html
