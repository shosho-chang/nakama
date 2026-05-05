"""Unit + integration tests for agents.robin.zotero_ingest (Slice 3 #391).

Coverage:
- Two-file output filenames: {slug}.md and {slug}--annotated.md
- Raw page re-extraction isolation: inbox MD mutation doesn't affect raw page
- Annotated page weave: bilingual MD + annotations → callouts woven in
- Frontmatter cross-links: annotated_sibling in raw / raw_source in annotated
- Concept extraction NOT triggered (no KB/Wiki/Concepts/ or KB/Wiki/Entities/ written)
- No bilingual sibling → annotated_path is None
- Integration: POST /zotero-ingest/{slug} → 303 redirect + two files with cross-links
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from tests.agents.robin._zotero_fixture import (
    add_html_snapshot,
    add_journal_article,
    init_zotero_lib,
)


@pytest.fixture(autouse=True)
def _mock_trafilatura(monkeypatch):
    """Stub trafilatura so tests run without lxml_html_clean installed.

    The stub returns a predictable string derived from the HTML content
    (containing "Circadian rhythms") that differs from the inbox body text
    — proving re-extraction isolation.
    """
    mock_traf = ModuleType("trafilatura")
    mock_traf.extract = MagicMock(
        return_value="Circadian rhythms govern sleep architecture in mammals (re-extracted)."
    )
    monkeypatch.setitem(sys.modules, "trafilatura", mock_traf)
    # Patch _extract_attachment to bypass the lazy trafilatura import entirely.
    import agents.robin.zotero_ingest as zi

    monkeypatch.setattr(
        zi,
        "_extract_attachment",
        lambda path, atype: mock_traf.extract(path.read_text(encoding="utf-8")),
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

_SNAPSHOT_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Sleep Research Paper</title></head>
<body>
  <article>
    <h1>Sleep Research Paper</h1>
    <p>Circadian rhythms govern sleep architecture in mammals. This
    introduction paragraph is long enough for Trafilatura to accept it as
    main content rather than navigation or boilerplate chrome.</p>
    <p>The second paragraph covers melatonin secretion patterns and their
    role in regulating the sleep-wake cycle across different light conditions.</p>
  </article>
</body>
</html>
"""

_BILINGUAL_MD = """\
Circadian rhythms govern sleep architecture in mammals.

> 晝夜節律支配哺乳動物的睡眠結構。

The second paragraph covers melatonin secretion patterns.

> 第二段介紹褪黑素分泌模式。
"""

_BILINGUAL_FRONTMATTER = (
    "---\n"
    'title: "Sleep Research Paper — 雙語閱讀版"\n'
    'source: "zotero://select/library/items/SLP12345"\n'
    'original_url: "zotero://select/library/items/SLP12345"\n'
    "source_type: zotero\n"
    "content_nature: research\n"
    "fulltext_status: translated\n"
    "fulltext_layer: zotero_html_snapshot\n"
    'fulltext_source: "Zotero HTML snapshot"\n'
    "bilingual: true\n"
    'derived_from: "Inbox/kb/Sleep-Research-Paper.md"\n'
    "---\n\n"
)

_INBOX_FRONTMATTER_TEMPLATE = (
    "---\n"
    'title: "Sleep Research Paper"\n'
    'source: "zotero://select/library/items/SLP12345"\n'
    'original_url: "zotero://select/library/items/SLP12345"\n'
    "source_type: zotero\n"
    "content_nature: research\n"
    "fulltext_status: translated\n"
    "fulltext_layer: zotero_html_snapshot\n"
    'fulltext_source: "Zotero HTML snapshot"\n'
    "zotero_item_key: SLP12345\n"
    'zotero_attachment_path: "{attachment_path}"\n'
    "attachment_type: text/html\n"
    "---\n\n"
)


def _make_annotation_file(ann_dir: Path, slug: str, ref: str, note: str) -> None:
    """Write a minimal AnnotationSetV1 JSON block to KB/Annotations/{slug}.md."""
    items = [
        {
            "type": "annotation",
            "ref": ref,
            "note": note,
            "created_at": "2026-01-01T00:00:00Z",
            "modified_at": "2026-01-01T00:00:00Z",
        }
    ]
    ann_dir.mkdir(parents=True, exist_ok=True)
    (ann_dir / f"{slug}.md").write_text(
        f"---\nslug: {slug}\nsource: Sleep-Research-Paper-bilingual.md\n"
        f'base: inbox\nupdated_at: "2026-01-01T00:00:00Z"\n---\n\n'
        f"```json\n{json.dumps(items, ensure_ascii=False, indent=2)}\n```\n",
        encoding="utf-8",
    )


@pytest.fixture
def zotero_env(tmp_path: Path, monkeypatch):
    """Set up a minimal Zotero + vault environment for ingest tests."""
    # ── Zotero library ────────────────────────────────────────────────────────
    zotero_root = tmp_path / "Zotero"
    fixture = init_zotero_lib(zotero_root)
    parent_id = add_journal_article(fixture, item_key="SLP12345", title="Sleep Research Paper")
    add_html_snapshot(
        fixture,
        parent_item_id=parent_id,
        attachment_key="HTMLSLP1",
        body=_SNAPSHOT_HTML,
    )
    attachment_path = fixture.storage_dir / "HTMLSLP1" / "snapshot.html"

    # ── Vault layout ──────────────────────────────────────────────────────────
    vault_root = tmp_path / "vault"
    inbox_dir = vault_root / "Inbox" / "kb"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    slug = "Sleep-Research-Paper"

    # Inbox working MD — Windows backslashes in YAML double-quoted scalars must be
    # escaped (`\U` would otherwise be parsed as a Unicode escape and fail the whole
    # frontmatter). Production InboxWriter._yaml_double_quoted does this; the test
    # fixture mirrors that behavior here.
    inbox_fm = _INBOX_FRONTMATTER_TEMPLATE.format(
        attachment_path=str(attachment_path).replace("\\", "\\\\")
    )
    inbox_body = "Circadian rhythms govern sleep architecture in mammals.\n"
    (inbox_dir / f"{slug}.md").write_text(inbox_fm + inbox_body, encoding="utf-8")

    # Bilingual sibling
    (inbox_dir / f"{slug}-bilingual.md").write_text(
        _BILINGUAL_FRONTMATTER + _BILINGUAL_MD, encoding="utf-8"
    )

    # Annotations — keyed on bilingual title slug
    ann_dir = vault_root / "KB" / "Annotations"
    # annotation_slug for "Sleep Research Paper — 雙語閱讀版" → see shared.annotation_store._slugify
    bilingual_ann_slug = "sleep-research-paper-雙語閱讀版"
    _make_annotation_file(ann_dir, bilingual_ann_slug, "晝夜節律", "褪黑素是關鍵")

    monkeypatch.setenv("VAULT_PATH", str(vault_root))
    return {
        "vault_root": vault_root,
        "zotero_root": zotero_root,
        "slug": slug,
        "attachment_path": attachment_path,
        "bilingual_ann_slug": bilingual_ann_slug,
    }


# ── Unit tests ────────────────────────────────────────────────────────────────


def test_produce_two_file_output_filenames(zotero_env):
    """produce_source_pages returns paths {slug}.md and {slug}--annotated.md."""
    from agents.robin.zotero_ingest import produce_source_pages

    slug = zotero_env["slug"]
    raw_path, ann_path = produce_source_pages(
        slug,
        vault_root=zotero_env["vault_root"],
        zotero_root=zotero_env["zotero_root"],
    )

    sources_dir = zotero_env["vault_root"] / "KB" / "Wiki" / "Sources"
    assert raw_path == sources_dir / f"{slug}.md"
    assert ann_path == sources_dir / f"{slug}--annotated.md"
    assert raw_path.exists()
    assert ann_path.exists()


def test_raw_page_re_extraction_isolation(zotero_env):
    """Mutating inbox MD doesn't change raw page content — it re-extracts from attachment."""
    from agents.robin.zotero_ingest import produce_source_pages

    slug = zotero_env["slug"]
    vault_root = zotero_env["vault_root"]

    # Mutate inbox body to something not in the snapshot HTML
    inbox_file = vault_root / "Inbox" / "kb" / f"{slug}.md"
    original_content = inbox_file.read_text(encoding="utf-8")
    # Inject text that is NOT in snapshot.html
    mutated_content = original_content + "MUTATED_CONTENT_NOT_IN_SNAPSHOT\n"
    inbox_file.write_text(mutated_content, encoding="utf-8")

    raw_path, _ = produce_source_pages(
        slug,
        vault_root=vault_root,
        zotero_root=zotero_env["zotero_root"],
    )

    raw_content = raw_path.read_text(encoding="utf-8")
    # Raw page must NOT contain the mutated text
    assert "MUTATED_CONTENT_NOT_IN_SNAPSHOT" not in raw_content
    # Raw page MUST contain content from the snapshot HTML
    assert "Circadian rhythms" in raw_content or "Sleep Research Paper" in raw_content


def test_annotated_page_weave_correctness(zotero_env):
    """Annotations are woven into the annotated source page."""
    from agents.robin.zotero_ingest import produce_source_pages

    slug = zotero_env["slug"]
    _, ann_path = produce_source_pages(
        slug,
        vault_root=zotero_env["vault_root"],
        zotero_root=zotero_env["zotero_root"],
    )

    annotated_content = ann_path.read_text(encoding="utf-8")
    # Annotation callout should be present
    assert "> [!annotation]" in annotated_content
    # Annotation note
    assert "褪黑素是關鍵" in annotated_content
    # Bilingual content preserved
    assert "晝夜節律" in annotated_content


def test_frontmatter_cross_links(zotero_env):
    """Raw page has annotated_sibling; annotated page has raw_source."""
    from agents.robin.zotero_ingest import produce_source_pages

    slug = zotero_env["slug"]
    raw_path, ann_path = produce_source_pages(
        slug,
        vault_root=zotero_env["vault_root"],
        zotero_root=zotero_env["zotero_root"],
    )

    raw_fm = raw_path.read_text(encoding="utf-8")
    ann_fm = ann_path.read_text(encoding="utf-8")

    assert f'annotated_sibling: "{slug}--annotated.md"' in raw_fm
    assert f'raw_source: "{slug}.md"' in ann_fm


def test_concept_extraction_not_triggered(zotero_env, tmp_path):
    """No Concepts or Entities written during produce_source_pages."""
    from agents.robin.zotero_ingest import produce_source_pages

    slug = zotero_env["slug"]
    vault_root = zotero_env["vault_root"]

    produce_source_pages(
        slug,
        vault_root=vault_root,
        zotero_root=zotero_env["zotero_root"],
    )

    concepts_dir = vault_root / "KB" / "Wiki" / "Concepts"
    entities_dir = vault_root / "KB" / "Wiki" / "Entities"
    # Neither directory should exist (no concept extraction triggered)
    assert not concepts_dir.exists() or len(list(concepts_dir.iterdir())) == 0
    assert not entities_dir.exists() or len(list(entities_dir.iterdir())) == 0


def test_no_bilingual_sibling_returns_none(zotero_env):
    """When bilingual sibling absent, annotated_path is None."""
    from agents.robin.zotero_ingest import produce_source_pages

    slug = zotero_env["slug"]
    vault_root = zotero_env["vault_root"]

    # Remove bilingual sibling
    bilingual_file = vault_root / "Inbox" / "kb" / f"{slug}-bilingual.md"
    bilingual_file.unlink()

    raw_path, ann_path = produce_source_pages(
        slug,
        vault_root=vault_root,
        zotero_root=zotero_env["zotero_root"],
    )

    assert raw_path.exists()
    assert ann_path is None


def test_inbox_files_persist_after_ingest(zotero_env):
    """Inbox files and annotations are NOT deleted after ingest."""
    from agents.robin.zotero_ingest import produce_source_pages

    slug = zotero_env["slug"]
    vault_root = zotero_env["vault_root"]

    produce_source_pages(
        slug,
        vault_root=vault_root,
        zotero_root=zotero_env["zotero_root"],
    )

    assert (vault_root / "Inbox" / "kb" / f"{slug}.md").exists()
    assert (vault_root / "Inbox" / "kb" / f"{slug}-bilingual.md").exists()
    bilingual_ann_slug = zotero_env["bilingual_ann_slug"]
    assert (vault_root / "KB" / "Annotations" / f"{bilingual_ann_slug}.md").exists()


# ── Integration test: route ───────────────────────────────────────────────────


@pytest.fixture
def route_client(zotero_env, monkeypatch):
    """TestClient for the robin router with the zotero_env vault."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("ZOTERO_LIBRARY_PATH", str(zotero_env["zotero_root"]))

    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(robin_module)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(robin_module.router)
    return TestClient(app, follow_redirects=False), zotero_env


def test_zotero_ingest_route_end_to_end(route_client):
    """POST /zotero-ingest/{slug} → 303 redirect + two source files exist."""
    client, env = route_client
    slug = env["slug"]
    vault_root = env["vault_root"]

    resp = client.post(f"/zotero-ingest/{slug}")

    assert resp.status_code == 303
    assert resp.headers["location"] == "/"

    sources_dir = vault_root / "KB" / "Wiki" / "Sources"
    raw_path = sources_dir / f"{slug}.md"
    ann_path = sources_dir / f"{slug}--annotated.md"

    assert raw_path.exists(), f"Raw source page missing: {raw_path}"
    assert ann_path.exists(), f"Annotated source page missing: {ann_path}"

    raw_content = raw_path.read_text(encoding="utf-8")
    ann_content = ann_path.read_text(encoding="utf-8")

    # Cross-link frontmatter
    assert f'annotated_sibling: "{slug}--annotated.md"' in raw_content
    assert f'raw_source: "{slug}.md"' in ann_content
