"""Unit tests for scripts.run_s8_preflight._assemble_body (Stage 1a).

Each test uses small literal fixtures — no real walker output, no external files.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.run_s8_preflight import _assemble_body
from shared.source_ingest import verbatim_paragraph_match_pct

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Strip wrapper blocks inserted by _assemble_body.  Matches:
#   \n\n### Section concept map\n\n{anything}\n\n### Wikilinks introduced\n\n{bullet lines}
_RE_STRIP_WRAPPER = re.compile(
    r"\n\n### Section concept map\n\n.*?\n\n### Wikilinks introduced\n\n(?:- \[\[.*?\]\]\n)*",
    re.DOTALL,
)
# Reverse V2 figure transform: ![[path]]\n*alt* → ![alt](path)
_RE_REVERSE_V2 = re.compile(r"!\[\[([^\]]+)\]\]\n\*([^*]*)\*")


def _normalize(assembled: str) -> str:
    """Strip wrappers + reverse V2 to recover the verbatim body."""
    stripped = _RE_STRIP_WRAPPER.sub("", assembled)
    return _RE_REVERSE_V2.sub(lambda m: f"![{m.group(2)}]({m.group(1)})", stripped)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_v2_figure_transform_byte_equivalent():
    """Single walker figure ref → exact V2 markdown output byte-for-byte."""
    body = "## Energy\n\nPara 1.\n\n![ATP cycle](Attachments/Books/bse/fig1-1.png)\n\nPara 2."
    sections = [{"anchor": "Energy", "concept_map_md": "map", "wikilinks": []}]
    result = _assemble_body(body, ["Energy"], [], sections, "bse")
    assert "![[Attachments/Books/bse/fig1-1.png]]\n*ATP cycle*" in result
    assert "![ATP cycle](" not in result


def test_wrapper_inserts_after_section_block():
    """2-section chapter → wrappers land on H2 boundaries, not mid-paragraph."""
    body = "# Ch\n\n## Sec A\n\nParagraph A.\n\n## Sec B\n\nParagraph B."
    sections = [
        {"anchor": "Sec A", "concept_map_md": "mapA", "wikilinks": ["TermA"]},
        {"anchor": "Sec B", "concept_map_md": "mapB", "wikilinks": ["TermB"]},
    ]
    result = _assemble_body(body, ["Sec A", "Sec B"], [], sections, "book")
    sec_a_wrapper_pos = result.index("### Section concept map\n\nmapA")
    sec_b_pos = result.index("## Sec B")
    para_a_pos = result.index("Paragraph A.")
    assert para_a_pos < sec_a_wrapper_pos < sec_b_pos


def test_verbatim_100pct_by_construction():
    """Strip wrappers + reverse V2 → equals walker verbatim_body (exact)."""
    verbatim = (
        "# Title\n\n## Sec 1\n\nPara 1. With some text.\n\n## Sec 2\n\nPara 2. With more text."
    )
    sections = [
        {"anchor": "Sec 1", "concept_map_md": "map1", "wikilinks": ["Term1"]},
        {"anchor": "Sec 2", "concept_map_md": "map2", "wikilinks": ["Term2"]},
    ]
    assembled = _assemble_body(verbatim, ["Sec 1", "Sec 2"], [], sections, "book")
    normalized = _normalize(assembled)
    assert verbatim_paragraph_match_pct(verbatim, normalized) == 100.0


def test_multi_section_three_sections():
    """3-section chapter → 3 wrappers inserted in correct order."""
    body = "## S1\n\nText1.\n\n## S2\n\nText2.\n\n## S3\n\nText3."
    sections = [
        {"anchor": "S1", "concept_map_md": "m1", "wikilinks": ["w1"]},
        {"anchor": "S2", "concept_map_md": "m2", "wikilinks": ["w2"]},
        {"anchor": "S3", "concept_map_md": "m3", "wikilinks": ["w3"]},
    ]
    result = _assemble_body(body, ["S1", "S2", "S3"], [], sections, "book")
    idx1 = result.index("m1")
    idx2 = result.index("m2")
    idx3 = result.index("m3")
    assert idx1 < idx2 < idx3
    assert "- [[w1]]" in result
    assert "- [[w2]]" in result
    assert "- [[w3]]" in result


def test_single_section_chapter():
    """1-section chapter → exactly 1 wrapper emitted."""
    body = "## Only Section\n\nSome text."
    sections = [{"anchor": "Only Section", "concept_map_md": "only_map", "wikilinks": ["OnlyTerm"]}]
    result = _assemble_body(body, ["Only Section"], [], sections, "book")
    assert result.count("### Section concept map") == 1
    assert "- [[OnlyTerm]]" in result


def test_zero_section_chapter():
    """0-section walker → body with figure transform only, no wrappers."""
    body = "# Intro\n\nJust text.\n\n![fig](Attachments/Books/b/f.png)"
    result = _assemble_body(body, [], [], [], "b")
    assert "### Section concept map" not in result
    assert "### Wikilinks introduced" not in result
    assert "![[Attachments/Books/b/f.png]]" in result


def test_section_anchor_identity_mismatch_fail_fast():
    """sections_json anchor differs from walker anchor → raises ValueError, no partial output."""
    body = "## Real Section\n\nText."
    sections = [{"anchor": "Wrong Section", "concept_map_md": "map", "wikilinks": []}]
    with pytest.raises(ValueError, match="section anchor mismatch at index 0"):
        _assemble_body(body, ["Real Section"], [], sections, "book")


def test_section_anchor_count_mismatch_fail_fast():
    """LLM JSON has fewer sections than walker → raises ValueError."""
    body = "## Sec A\n\nText A.\n\n## Sec B\n\nText B."
    sections = [{"anchor": "Sec A", "concept_map_md": "map", "wikilinks": []}]
    with pytest.raises(ValueError, match="section anchor mismatch"):
        _assemble_body(body, ["Sec A", "Sec B"], [], sections, "book")


def test_no_mid_paragraph_wrapper_edge_case():
    """'## ' appearing mid-line is NOT a section boundary; wrapper stays on real H2 only."""
    body = (
        "## Real Section\n\nSome text with ## not-a-heading in the middle of a line.\n\nMore text."
    )
    sections = [{"anchor": "Real Section", "concept_map_md": "map", "wikilinks": ["T"]}]
    result = _assemble_body(body, ["Real Section"], [], sections, "book")
    assert result.count("### Section concept map") == 1
    assert "## not-a-heading in the middle of a line." in result
    wrapper_pos = result.index("### Section concept map")
    mid_line_pos = result.index("## not-a-heading")
    assert mid_line_pos < wrapper_pos


# ---------------------------------------------------------------------------
# Stage 1a.1 — NFKC-tolerant anchor comparison tests
# ---------------------------------------------------------------------------


def test_anchor_curly_vs_ascii_apostrophe_tolerated():
    """NFKC + custom norm: curly-apostrophe (U+2019) vs ASCII-apostrophe (U+0027) tolerated.

    This is the exact failure that triggered Stage 1a.1: the walker preserves the
    typographic apostrophe verbatim from the source EPUB, while the LLM silently
    normalizes to ASCII.  After the patch, assembly succeeds and the body H2 retains
    the walker (curly) form — the LLM anchor is used only for matching, never emitted.
    """
    walker_anchor = "1.5 Why Can’t a Marathon be Sprinted?"  # U+2019 curly apostrophe
    llm_anchor = "1.5 Why Can't a Marathon be Sprinted?"  # U+0027 ASCII apostrophe
    body = f"## {walker_anchor}\n\nSome text."
    sections = [{"anchor": llm_anchor, "concept_map_md": "m", "wikilinks": []}]
    result = _assemble_body(body, [walker_anchor], [], sections, "bse")
    # Body uses walker anchor (curly apostrophe), not LLM's ASCII form
    assert f"## {walker_anchor}" in result


def test_anchor_em_dash_vs_hyphen_tolerated():
    """Dash-variant drift: em-dash (U+2014) vs en-dash (U+2013) are both normalized to '-'.

    Both dash variants map to ASCII hyphen-minus after custom normalization, so they
    equalize even though pure NFKC leaves them unchanged.  Note: em-dash (U+2014) vs
    double-ASCII-hyphen '--' is NOT tolerated — they differ in length after normalization.
    """
    walker_anchor = "1.3 ATP—Energy Currency"  # em-dash U+2014
    llm_anchor = "1.3 ATP–Energy Currency"  # en-dash U+2013 (LLM changed variant)
    body = f"## {walker_anchor}\n\nText."
    sections = [{"anchor": llm_anchor, "concept_map_md": "m", "wikilinks": []}]
    result = _assemble_body(body, [walker_anchor], [], sections, "bse")
    # Body retains walker anchor (em-dash)
    assert f"## {walker_anchor}" in result


def test_anchor_real_word_change_still_fails():
    """Genuine word/content change is still rejected even with NFKC-tolerant compare."""
    walker_anchor = "1.5 Why Can’t a Marathon be Sprinted?"
    llm_anchor = "1.5 Why Can a Marathon be Sprinted?"  # removed "n't" — word change
    body = f"## {walker_anchor}\n\nText."
    sections = [{"anchor": llm_anchor, "concept_map_md": "m", "wikilinks": []}]
    with pytest.raises(ValueError, match="section anchor mismatch at index 0"):
        _assemble_body(body, [walker_anchor], [], sections, "bse")


def test_anchor_drift_logs_warning(caplog):
    """Tolerated punctuation drift emits a WARNING with both raw anchor strings visible."""
    import logging

    walker_anchor = "1.5 Why Can’t Stop"  # curly apostrophe U+2019
    llm_anchor = "1.5 Why Can't Stop"  # ASCII apostrophe U+0027
    body = f"## {walker_anchor}\n\nText."
    sections = [{"anchor": llm_anchor, "concept_map_md": "m", "wikilinks": []}]
    with caplog.at_level(logging.WARNING, logger="s8-preflight"):
        _assemble_body(body, [walker_anchor], [], sections, "bse")
    drift_msgs = [r for r in caplog.records if "anchor punctuation drift" in r.getMessage()]
    assert drift_msgs, "expected WARNING about anchor punctuation drift to be logged"
    msg = drift_msgs[0].getMessage()
    # Both raw strings must appear via %r formatting
    assert "walker=" in msg
    assert "llm_json=" in msg
