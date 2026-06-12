"""N524 — route C (article) end-to-end ingest wiring test.

Acceptance (task prompt §5): one article fixture, Reader 劃線 → ingest →
KB/Literature/ + Wiki/Sources/ + Wiki/Concepts/ + index/log all produced, off a
fixture vault (NOT the real vault). LLM is mocked. The red line 5 citation lint
is exercised adversarially in test_kb_writer.py / test_provenance_linter.py; this
test asserts the happy-path pipeline produces every artifact and that a Concept
whose extracted body cites another Concept in ## Sources is rejected mid-pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.robin import ingest as mod
from agents.robin.ingest import IngestPipeline
from shared.annotation_store import get_annotation_store
from shared.schemas.annotations import AnnotationSetV3, HighlightV3


@pytest.fixture
def route_c_vault(tmp_path, monkeypatch):
    """A real fixture vault wired through shared.config (VAULT_PATH env).

    Patches ingest.py's own bindings (get_vault_path / kb_log / list_files /
    set_current_agent) but NOT kb_writer / literature_writer — those run for
    real against the fixture vault so the test exercises the actual write chain.
    """
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    import shared.config as config_mod

    config_mod._config = None

    monkeypatch.setattr(mod, "get_vault_path", lambda: tmp_path)
    monkeypatch.setattr("shared.obsidian_writer.get_vault_path", lambda: tmp_path)
    monkeypatch.setattr(mod, "get_context", lambda *a, **k: "")
    monkeypatch.setattr(mod, "remember", lambda **k: None)
    monkeypatch.setattr(mod, "kb_log", lambda *a, **k: None)
    monkeypatch.setattr(mod, "list_files", lambda p: [])
    # kb_backup dir → tmp (don't pollute repo data/)
    from shared import kb_writer

    monkeypatch.setattr(kb_writer, "_REPO_ROOT", tmp_path, raising=True)
    # 🔗 KB 相關 zone search → no-op (avoid FTS5 / index dependency in unit test)
    monkeypatch.setattr(
        "agents.robin.kb_search.search_kb",
        lambda *a, **k: [],
    )
    return tmp_path


def _seed_annotation_set(slug: str) -> None:
    """Reader 劃線 fixture：a V3 article annotation set (two highlights)."""
    ann_set = AnnotationSetV3(
        slug=slug,
        base="inbox",
        source_filename=f"{slug}.md",
        items=[
            HighlightV3(text_excerpt="意志力是有限資源", text="意志力是有限資源"),
            HighlightV3(text_excerpt="睡眠壓力由腺苷累積驅動", text="睡眠壓力由腺苷累積驅動"),
        ],
    )
    get_annotation_store().save(ann_set)


def test_route_c_end_to_end_produces_all_artifacts(route_c_vault, monkeypatch):
    vault: Path = route_c_vault
    slug = "willpower-and-sleep"
    _seed_annotation_set(slug)

    # Raw article file in the vault.
    raw = vault / "KB" / "Raw" / "Articles" / f"{slug}.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(
        "---\ntitle: Willpower and Sleep\nauthor: Jane Doe\n---\nbody content about willpower\n",
        encoding="utf-8",
    )

    # Prompt loader → marker string (we don't assert prompt content here).
    monkeypatch.setattr(mod, "load_prompt", lambda agent, name, **k: f"<{name}>")

    # LLM: summary calls return prose; the concept-plan call (the one whose
    # system prompt carries 回傳純 JSON) returns a valid v2 plan with a clean
    # concept (cites a Source, never a Concept — red line 5 compliant).
    concept_body = (
        f"## Definition\n\n意志力是有限資源。^p-1\n\n## Sources\n\n- [[Sources/{slug}]]\n"
    )
    plan = {
        "concepts": [
            {
                "slug": "willpower-as-finite-resource",
                "action": "create",
                "title": "Willpower as a Finite Resource",
                "domain": "psychology",
                "extracted_body": concept_body,
            }
        ],
        "entities": [],
    }

    def fake_ask(**kwargs):
        if "回傳純 JSON" in (kwargs.get("system") or ""):
            return json.dumps(plan, ensure_ascii=False)
        return "AI 綜整摘要：本文討論意志力與睡眠。^p-1"

    monkeypatch.setattr(mod, "ask", fake_ask)

    pipe = IngestPipeline()
    pipe.ingest(raw, source_type="article", annotation_slug=slug)

    # Phase 1: Literature note rendered from the annotation set.
    lit = vault / "KB" / "Literature" / f"{slug}.md"
    assert lit.exists(), "Phase 1 Literature note missing"
    lit_text = lit.read_text(encoding="utf-8")
    assert "意志力是有限資源" in lit_text
    assert "type: literature" in lit_text

    # Phase 2 — Source page.
    source = vault / "KB" / "Wiki" / "Sources" / "Willpower-and-Sleep.md"
    assert source.exists(), "Source digest page missing"
    src_text = source.read_text(encoding="utf-8")
    assert "author: agent_robin" in src_text  # P-3 §5 provenance separation

    # Phase 2 — Concept page (real upsert, passed red line 5 lint).
    concept = vault / "KB" / "Wiki" / "Concepts" / "willpower-as-finite-resource.md"
    assert concept.exists(), "Concept page missing"

    # index.md updated.
    index = (vault / "KB" / "index.md").read_text(encoding="utf-8")
    assert "[[Willpower-and-Sleep]]" in index


def test_route_c_rejects_concept_self_feeding(route_c_vault, monkeypatch):
    """Red line 5: a concept whose ## Sources cites another Concept is rejected.

    The pipeline must not silently write the laundered concept page. The
    rejection surfaces as a logged error inside _execute_concept_action
    (which catches the upsert exception); the page must be absent.
    """
    vault: Path = route_c_vault
    slug = "self-feed"
    _seed_annotation_set(slug)
    raw = vault / "KB" / "Raw" / "Articles" / f"{slug}.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("---\ntitle: Self Feed\n---\nbody\n", encoding="utf-8")

    monkeypatch.setattr(mod, "load_prompt", lambda agent, name, **k: f"<{name}>")

    laundered = "## Definition\n\nx\n\n## Sources\n\n- [[Concepts/another-concept]]\n"
    plan = {
        "concepts": [
            {
                "slug": "laundered-concept",
                "action": "create",
                "title": "Laundered",
                "extracted_body": laundered,
            }
        ],
        "entities": [],
    }

    def fake_ask(**kwargs):
        if "回傳純 JSON" in (kwargs.get("system") or ""):
            return json.dumps(plan, ensure_ascii=False)
        return "summary"

    monkeypatch.setattr(mod, "ask", fake_ask)

    IngestPipeline().ingest(raw, source_type="article", annotation_slug=slug)

    # red line 5 → concept page rejected, never written
    assert not (vault / "KB" / "Wiki" / "Concepts" / "laundered-concept.md").exists()


def test_route_c_without_annotation_slug_skips_literature(route_c_vault, monkeypatch):
    """Backward-compat: no annotation_slug → no Literature render, no crash."""
    vault: Path = route_c_vault
    raw = vault / "plain.md"
    raw.write_text("---\ntitle: Plain\n---\nbody\n", encoding="utf-8")
    monkeypatch.setattr(mod, "load_prompt", lambda agent, name, **k: f"<{name}>")
    monkeypatch.setattr(mod, "ask", lambda **k: '{"concepts":[],"entities":[]}')

    IngestPipeline().ingest(raw, source_type="article")  # no annotation_slug
    assert not (vault / "KB" / "Literature").exists()
    assert (vault / "KB" / "Wiki" / "Sources" / "Plain.md").exists()
