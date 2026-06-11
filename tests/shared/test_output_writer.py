"""Tests for shared.output_writer (N524 — KB/Wiki/Outputs/ storage layer, D-19/D-18).

Storage layer only: write_output_page assumes the confirm step (D-18) already
happened in the query workflow. Red line 5 (terminal evidence) and red line 1
(never KB/Permanent/) are enforced at the write chokepoint.
"""

from __future__ import annotations

from datetime import date

import pytest

from shared import output_writer
from shared.provenance_linter import ProvenanceViolation


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    import shared.config as config_mod

    config_mod._config = None
    return tmp_path


def test_writes_output_page_with_frontmatter(vault):
    res = output_writer.write_output_page(
        "意志力與對齊",
        title="意志力要用在對齊的任務",
        body="## 結論\n\n意志力是有限資源。^p-3\n\n## Sources\n\n- [[Sources/willpower]]\n",
        from_query="2026-06-11",
        confidence="high",
        source_refs=["[[Sources/willpower]]"],
        today=date(2026, 6, 11),
    )
    assert res.written is True
    dest = vault / "KB" / "Wiki" / "Outputs" / "意志力與對齊.md"
    assert dest.exists()
    text = dest.read_text(encoding="utf-8")
    assert "type: output" in text
    assert "author: agent_query" in text
    assert "from_query: '2026-06-11'" in text or "from_query: 2026-06-11" in text


def test_red_line_5_rejects_concept_cite_in_body(vault):
    """Output terminal evidence pointing to a Concept → ProvenanceViolation."""
    with pytest.raises(ProvenanceViolation):
        output_writer.write_output_page(
            "bad",
            title="laundered output",
            body="## 結論\n\nx\n\n## Sources\n\n- [[Concepts/some-concept]]\n",
        )
    assert not (vault / "KB" / "Wiki" / "Outputs" / "bad.md").exists()


def test_red_line_5_rejects_concept_in_source_refs(vault):
    with pytest.raises(ProvenanceViolation):
        output_writer.write_output_page(
            "bad2",
            title="x",
            body="## 結論\n\nx\n",
            source_refs=["[[Concepts/laundered]]"],
        )


def test_output_in_related_concepts_is_allowed(vault):
    """Concept links in a relation section are not terminal evidence → allowed."""
    res = output_writer.write_output_page(
        "ok",
        title="x",
        body=(
            "## 結論\n\nx\n\n"
            "## Related Concepts\n\n- [[Concepts/related]]\n\n"
            "## Sources\n\n- [[Sources/real]]\n"
        ),
    )
    assert res.written is True


def test_invalid_slug_rejected(vault):
    with pytest.raises(ValueError):
        output_writer.write_output_page("../escape", title="x", body="y")
