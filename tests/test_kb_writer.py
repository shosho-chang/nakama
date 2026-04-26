"""Unit tests for shared.kb_writer (ADR-011 §3.5).

Coverage:
- v1 → v2 in-memory migration (read_concept_for_diff / list_existing_concepts)
- upsert_concept_page: create / update_merge / update_conflict / noop
- Idempotency: (slug, source_link) repeat calls don't double-append mentioned_in
- Backup mechanism: .bak written + 24h retention sweep
- update_mentioned_in / aggregate_conflict
- write_source_page / upsert_book_entity (chapters_ingested auto-count)
- migrate_v1_to_v2 / backfill_all_v1_pages
- _strip_legacy_update_blocks / _ensure_h2_skeleton / _append_to_section / _path_to_wikilink

LLM is mocked via monkeypatch on `shared.kb_writer._ask_llm`.
Vault is isolated to tmp_path via VAULT_PATH env.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from shared import kb_writer
from shared.schemas.kb import ConflictBlock, FigureRef

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Isolate vault path to tmp_path. Reset shared.config cache."""
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    # Reset config cache so VAULT_PATH env is re-read
    import shared.config as config_mod

    config_mod._config = None
    # Also reset backup dir to be inside tmp so we don't pollute repo data/
    monkeypatch.setattr(kb_writer, "_REPO_ROOT", tmp_path, raising=True)
    return tmp_path


@pytest.fixture
def mock_llm(monkeypatch):
    """Replace _ask_llm with a deterministic stub that records calls."""
    calls: list[dict] = []

    def fake(prompt: str, *, system: str = "", max_tokens: int = 16000) -> str:
        calls.append({"prompt": prompt, "system": system, "max_tokens": max_tokens})
        # Return an 8-H2 body so tests pass _ensure_h2_skeleton invariants
        return (
            "## Definition\n\nMERGED definition\n\n"
            "## Core Principles\n\n- merged principle 1\n- merged principle 2\n\n"
            "## Sub-concepts\n\n_(尚無內容)_\n\n"
            "## Field-level Controversies\n\n_(尚無內容)_\n\n"
            "## 文獻分歧 / Discussion\n\n_(尚無內容)_\n\n"
            "## Practical Applications\n\nMERGED applications\n\n"
            "## Related Concepts\n\n_(尚無內容)_\n\n"
            "## Sources\n\n- [[Sources/x]]\n- [[Sources/y]]\n"
        )

    monkeypatch.setattr(kb_writer, "_ask_llm", fake)
    return calls


def _read_page(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8")
    parts = raw.split("---", 2)
    fm = yaml.safe_load(parts[1])
    body = parts[2].lstrip("\n")
    return fm, body


def _make_v1_concept(vault: Path, slug: str, *, with_legacy_block: bool = False) -> Path:
    """Helper: write a v1-schema concept page to the vault for migration tests."""
    fm = {
        "title": slug,
        "type": "concept",
        "status": "draft",
        "created": "2026-04-13",
        "updated": "2026-04-13",
        "source_refs": ["Sources/foo.md", "Sources/bar.md"],
        "confidence": "medium",
        "tags": ["#concept"],
        "related_pages": [],
    }
    body = f"# {slug}\n\n## Definition\n\nv1 definition.\n\n## Core Principles\n\n- v1 principle.\n"
    if with_legacy_block:
        body += "\n---\n\n## 更新（2026-04-13）\n\n應新增 X、應補充 Y。\n\n來源：[[bar]]\n"
    yaml_str = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False)
    full = f"---\n{yaml_str.strip()}\n---\n\n{body}"
    target = vault / kb_writer.KB_CONCEPTS_DIR / f"{slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(full, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# _path_to_wikilink
# ---------------------------------------------------------------------------


class TestPathToWikilink:
    def test_strips_md(self):
        assert kb_writer._path_to_wikilink("Sources/foo.md") == "[[foo]]"

    def test_handles_nested(self):
        assert kb_writer._path_to_wikilink("KB/Wiki/Sources/Books/foo/ch1.md") == "[[ch1]]"

    def test_no_extension(self):
        assert kb_writer._path_to_wikilink("Sources/foo") == "[[foo]]"


# ---------------------------------------------------------------------------
# _strip_legacy_update_blocks
# ---------------------------------------------------------------------------


class TestStripLegacyUpdateBlocks:
    def test_strips_single_block(self):
        body = (
            "## Definition\n\nfoo\n\n"
            "## Core Principles\n\n- bar\n\n"
            "---\n\n## 更新（2026-04-13）\n\n應新增 X。\n"
        )
        cleaned, n = kb_writer._strip_legacy_update_blocks(body)
        assert n == 1
        assert "## 更新" not in cleaned
        assert "## Definition" in cleaned
        assert "## Core Principles" in cleaned

    def test_strips_multiple_blocks(self):
        body = (
            "## Definition\n\nfoo\n\n"
            "## 更新（2026-04-13）\n\n第一條\n\n"
            "## 更新（2026-04-15）\n\n第二條\n\n"
            "## 更新（2026-04-20）\n\n第三條\n"
        )
        cleaned, n = kb_writer._strip_legacy_update_blocks(body)
        assert n == 3
        assert "## 更新" not in cleaned

    def test_preserves_non_update_h2(self):
        body = "## Definition\n\nfoo\n\n## Core Principles\n\n- bar\n"
        cleaned, n = kb_writer._strip_legacy_update_blocks(body)
        assert n == 0
        assert cleaned.strip() == body.strip()


# ---------------------------------------------------------------------------
# _ensure_h2_skeleton / _append_to_section
# ---------------------------------------------------------------------------


class TestH2Skeleton:
    def test_fills_missing(self):
        body = "# Title\n\n## Definition\n\nfoo\n"
        result = kb_writer._ensure_h2_skeleton(body)
        for h2 in kb_writer.H2_ORDER:
            assert h2 in result
        # Definition content preserved
        assert "foo" in result
        # Other sections placeholder-filled
        assert kb_writer.PLACEHOLDER in result

    def test_idempotent(self):
        body = "# Title\n\n## Definition\n\nfoo\n"
        once = kb_writer._ensure_h2_skeleton(body)
        twice = kb_writer._ensure_h2_skeleton(once)
        assert once.strip() == twice.strip()


class TestAppendToSection:
    def test_replaces_placeholder(self):
        body = "## Definition\n\nfoo\n\n## 文獻分歧 / Discussion\n\n_(尚無內容)_\n"
        result = kb_writer._append_to_section(
            body, "## 文獻分歧 / Discussion", "### Topic: A\n- detail"
        )
        assert "_(尚無內容)_" not in result.split("## 文獻分歧 / Discussion")[1].split("##")[0]
        assert "### Topic: A" in result

    def test_appends_to_existing(self):
        body = "## Definition\n\nfoo\n\n## 文獻分歧 / Discussion\n\n### Topic: A\n- existing\n"
        result = kb_writer._append_to_section(
            body, "## 文獻分歧 / Discussion", "### Topic: B\n- new"
        )
        assert "### Topic: A" in result
        assert "### Topic: B" in result


# ---------------------------------------------------------------------------
# _v1_to_v2_in_memory
# ---------------------------------------------------------------------------


class TestV1ToV2InMemory:
    def test_basic_migration(self):
        v1_fm = {
            "title": "肌酸代謝",
            "type": "concept",
            "status": "draft",
            "source_refs": ["Sources/foo.md", "Sources/bar.md"],
            "confidence": "medium",
            "tags": ["#concept"],
            "related_pages": [],
            "created": date(2026, 4, 13),
            "updated": date(2026, 4, 13),
        }
        v2_fm, _, changes = kb_writer._v1_to_v2_in_memory(v1_fm, "body")
        assert v2_fm["schema_version"] == 2
        assert v2_fm["mentioned_in"] == ["[[foo]]", "[[bar]]"]
        assert v2_fm["source_refs"] == ["Sources/foo.md", "Sources/bar.md"]
        assert v2_fm["confidence"] == 0.6
        assert v2_fm["aliases"] == []
        assert v2_fm["discussion_topics"] == []
        # Dropped v1-only fields
        assert "status" not in v2_fm
        assert "related_pages" not in v2_fm
        # Change log mentions key transformations
        assert any("mentioned_in derived" in c for c in changes)
        assert any("dropped v1-only fields" in c for c in changes)
        assert any("schema_version" in c for c in changes)

    def test_already_v2_no_change(self):
        v2_fm = {
            "schema_version": 2,
            "title": "X",
            "type": "concept",
            "domain": "general",
            "mentioned_in": ["[[a]]"],
        }
        out_fm, _, changes = kb_writer._v1_to_v2_in_memory(v2_fm, "body")
        assert changes == []
        assert out_fm["mentioned_in"] == ["[[a]]"]

    def test_confidence_string_to_float(self):
        for s, expected in [("low", 0.3), ("medium", 0.6), ("high", 0.9)]:
            v1 = {"title": "x", "confidence": s, "source_refs": []}
            out, _, _ = kb_writer._v1_to_v2_in_memory(v1, "")
            assert out["confidence"] == expected

    def test_confidence_already_float(self):
        v1 = {"title": "x", "confidence": 0.85, "source_refs": []}
        out, _, _ = kb_writer._v1_to_v2_in_memory(v1, "")
        assert out["confidence"] == 0.85


# ---------------------------------------------------------------------------
# read_concept_for_diff / list_existing_concepts
# ---------------------------------------------------------------------------


class TestReadConceptForDiff:
    def test_missing_returns_none(self, vault):
        assert kb_writer.read_concept_for_diff("nonexistent") is None

    def test_v1_page_lazy_migrated(self, vault):
        _make_v1_concept(vault, "肌酸代謝")
        result = kb_writer.read_concept_for_diff("肌酸代謝")
        assert result["frontmatter"]["schema_version"] == 2
        assert result["frontmatter"]["mentioned_in"] == ["[[foo]]", "[[bar]]"]


class TestListExistingConcepts:
    def test_empty_vault(self, vault):
        assert kb_writer.list_existing_concepts() == {}

    def test_scans_all_concepts(self, vault):
        _make_v1_concept(vault, "肌酸代謝")
        _make_v1_concept(vault, "糖解作用")
        out = kb_writer.list_existing_concepts()
        assert set(out.keys()) == {"肌酸代謝", "糖解作用"}
        assert all(d["frontmatter"]["schema_version"] == 2 for d in out.values())


# ---------------------------------------------------------------------------
# update_mentioned_in
# ---------------------------------------------------------------------------


class TestUpdateMentionedIn:
    def test_appends_new_link(self, vault):
        path = _make_v1_concept(vault, "x")
        # First read+write to v2 form
        v2_fm, body, _ = kb_writer._v1_to_v2_in_memory(*kb_writer._load_page(path))
        kb_writer._write_page_file(path, v2_fm, body)
        added = kb_writer.update_mentioned_in(path, "[[Sources/new]]")
        assert added is True
        fm, _ = _read_page(path)
        assert "[[Sources/new]]" in fm["mentioned_in"]

    def test_idempotent(self, vault):
        path = _make_v1_concept(vault, "x")
        v2_fm, body, _ = kb_writer._v1_to_v2_in_memory(*kb_writer._load_page(path))
        kb_writer._write_page_file(path, v2_fm, body)
        kb_writer.update_mentioned_in(path, "[[Sources/new]]")
        added_again = kb_writer.update_mentioned_in(path, "[[Sources/new]]")
        assert added_again is False
        fm, _ = _read_page(path)
        # Still only one occurrence
        assert fm["mentioned_in"].count("[[Sources/new]]") == 1


# ---------------------------------------------------------------------------
# upsert_concept_page
# ---------------------------------------------------------------------------


class TestUpsertCreate:
    def test_writes_v2_page(self, vault):
        path = kb_writer.upsert_concept_page(
            slug="新概念",
            action="create",
            source_link="[[Sources/Books/foo/ch1]]",
            title="新概念",
            domain="bioenergetics",
            extracted_body="## Definition\n\nfoo\n\n## Core Principles\n\n- bar\n",
            tags=["#concept"],
            confidence=0.8,
        )
        assert path.exists()
        fm, body = _read_page(path)
        assert fm["schema_version"] == 2
        assert fm["title"] == "新概念"
        assert fm["domain"] == "bioenergetics"
        assert fm["mentioned_in"] == ["[[Sources/Books/foo/ch1]]"]
        assert fm["confidence"] == 0.8
        # H2 skeleton ensured
        for h2 in kb_writer.H2_ORDER:
            assert h2 in body

    def test_create_requires_title(self, vault):
        with pytest.raises(ValueError, match="title"):
            kb_writer.upsert_concept_page(
                slug="x",
                action="create",
                source_link="[[s]]",
                extracted_body="body",
            )

    def test_create_requires_body(self, vault):
        with pytest.raises(ValueError, match="extracted_body"):
            kb_writer.upsert_concept_page(
                slug="x",
                action="create",
                source_link="[[s]]",
                title="x",
            )

    def test_create_on_existing_falls_back_to_merge(self, vault, mock_llm):
        # First create
        kb_writer.upsert_concept_page(
            slug="x",
            action="create",
            source_link="[[Sources/a]]",
            title="x",
            extracted_body="## Definition\n\nfoo\n",
        )
        # Second "create" with same slug — should fall back to update_merge
        path = kb_writer.upsert_concept_page(
            slug="x",
            action="create",
            source_link="[[Sources/b]]",
            title="x",
            extracted_body="## Definition\n\nbar\n",
        )
        # LLM was called (merge path)
        assert len(mock_llm) == 1
        fm, _ = _read_page(path)
        assert "[[Sources/a]]" in fm["mentioned_in"]
        assert "[[Sources/b]]" in fm["mentioned_in"]


class TestUpsertUpdateMerge:
    def test_calls_llm_and_writes_backup(self, vault, mock_llm):
        _make_v1_concept(vault, "肌酸代謝", with_legacy_block=True)
        result_path = kb_writer.upsert_concept_page(
            slug="肌酸代謝",
            action="update_merge",
            source_link="[[Sources/Books/foo/ch1]]",
            extracted_body="ch1 教科書 PCr 主導 1-10s",
        )
        # LLM called
        assert len(mock_llm) == 1
        # Backup written under tmp data/kb_backup/
        backups = list((vault / "data" / "kb_backup").glob("肌酸代謝-*.md"))
        assert len(backups) == 1
        # Page rewritten — schema upgraded + mentioned_in appended + legacy block stripped
        fm, body = _read_page(result_path)
        assert fm["schema_version"] == 2
        assert "[[Sources/Books/foo/ch1]]" in fm["mentioned_in"]
        assert "## 更新" not in body  # legacy stripped
        assert "MERGED definition" in body  # LLM result written

    def test_idempotent_mentioned_in(self, vault, mock_llm):
        path = _make_v1_concept(vault, "x")
        kb_writer.upsert_concept_page(
            slug="x",
            action="update_merge",
            source_link="[[Sources/new]]",
            extracted_body="extract",
        )
        kb_writer.upsert_concept_page(
            slug="x",
            action="update_merge",
            source_link="[[Sources/new]]",
            extracted_body="extract",
        )
        fm, _ = _read_page(path)
        assert fm["mentioned_in"].count("[[Sources/new]]") == 1
        # Sanity: ensure path still resolves to written file
        assert path.exists()


class TestUpsertUpdateConflict:
    def test_writes_conflict_block_and_topic(self, vault):
        _make_v1_concept(vault, "磷酸肌酸系統")
        conflict = ConflictBlock(
            topic="PCr 主導窗口時長範圍",
            existing_claim="10-15 秒",
            new_claim="1-10 秒",
            possible_reason="不同 ATP depletion endpoint",
            consensus="PCr 是高強度爆發的主能量",
        )
        result = kb_writer.upsert_concept_page(
            slug="磷酸肌酸系統",
            action="update_conflict",
            source_link="[[Sources/Books/foo/ch1]]",
            conflict=conflict,
        )
        fm, body = _read_page(result)
        assert "PCr 主導窗口時長範圍" in fm["discussion_topics"]
        assert "[[Sources/Books/foo/ch1]]" in fm["mentioned_in"]
        assert "### Topic: PCr 主導窗口時長範圍" in body
        assert "**[[Sources/Books/foo/ch1]]**: 1-10 秒" in body
        # Backup written
        backups = list((vault / "data" / "kb_backup").glob("磷酸肌酸系統-*.md"))
        assert len(backups) == 1

    def test_requires_conflict_arg(self, vault):
        _make_v1_concept(vault, "x")
        with pytest.raises(ValueError, match="conflict"):
            kb_writer.upsert_concept_page(
                slug="x",
                action="update_conflict",
                source_link="[[s]]",
            )


class TestUpsertNoop:
    def test_appends_mentioned_in_only(self, vault):
        path = _make_v1_concept(vault, "x")
        kb_writer.upsert_concept_page(
            slug="x",
            action="noop",
            source_link="[[Sources/new]]",
        )
        fm, _ = _read_page(path)
        assert "[[Sources/new]]" in fm["mentioned_in"]
        # No backup for noop
        backup_dir = vault / "data" / "kb_backup"
        assert not backup_dir.exists() or not list(backup_dir.glob("*.md"))

    def test_idempotent(self, vault):
        path = _make_v1_concept(vault, "x")
        kb_writer.upsert_concept_page(slug="x", action="noop", source_link="[[Sources/new]]")
        kb_writer.upsert_concept_page(slug="x", action="noop", source_link="[[Sources/new]]")
        fm, _ = _read_page(path)
        assert fm["mentioned_in"].count("[[Sources/new]]") == 1


# ---------------------------------------------------------------------------
# Backup mechanism
# ---------------------------------------------------------------------------


class TestBackupMechanism:
    def test_backup_written(self, vault):
        bpath = kb_writer._backup_concept("test-slug", "test content")
        assert bpath.exists()
        assert bpath.read_text(encoding="utf-8") == "test content"

    def test_sweep_removes_old_backups(self, vault):
        # Write 3 backups; mtime back-dated
        bdir = vault / "data" / "kb_backup"
        bdir.mkdir(parents=True)
        old1 = bdir / "old-1.md"
        old1.write_text("x", encoding="utf-8")
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).timestamp()
        import os

        os.utime(old1, (old_ts, old_ts))

        recent = bdir / "recent.md"
        recent.write_text("y", encoding="utf-8")

        deleted = kb_writer._sweep_old_backups()
        assert deleted == 1
        assert not old1.exists()
        assert recent.exists()


# ---------------------------------------------------------------------------
# aggregate_conflict (direct API)
# ---------------------------------------------------------------------------


class TestAggregateConflict:
    def test_writes_block(self, vault):
        path = _make_v1_concept(vault, "x")
        v2_fm, body, _ = kb_writer._v1_to_v2_in_memory(*kb_writer._load_page(path))
        kb_writer._write_page_file(path, v2_fm, body)
        kb_writer.aggregate_conflict(
            page_path=path,
            topic="窗口時長",
            source_link="[[Sources/new]]",
            existing_claim="10-15s",
            new_claim="1-10s",
            possible_reason="endpoint 差異",
        )
        fm, body = _read_page(path)
        assert "窗口時長" in fm["discussion_topics"]
        assert "**可能原因**: endpoint 差異" in body


# ---------------------------------------------------------------------------
# write_source_page
# ---------------------------------------------------------------------------


class TestWriteSourcePage:
    def test_writes_chapter_source(self, vault):
        path = kb_writer.write_source_page(
            book_id="biochemistry-sport-exercise-2024",
            chapter_index=1,
            chapter_title="Energy Sources for Muscular Activity",
            source_md="# Ch.1\n\n## 1.1 Introduction\n\nbody...\n",
            section_anchors=["1.1 Introduction", "1.2 Phosphagen System"],
            page_range="4-16",
            figures=[
                FigureRef(
                    ref="fig-1-1",
                    path="Attachments/.../fig-1-1.png",
                    caption="Schematic",
                    llm_description="Three curves",
                    tied_to_section="1.2 Phosphagen System",
                ),
            ],
        )
        assert path.exists()
        assert "biochemistry-sport-exercise-2024/ch1.md" in str(path).replace("\\", "/")
        fm, body = _read_page(path)
        assert fm["book_id"] == "biochemistry-sport-exercise-2024"
        assert fm["chapter_index"] == 1
        assert len(fm["figures"]) == 1
        assert fm["figures"][0]["ref"] == "fig-1-1"
        assert "## 1.1 Introduction" in body


# ---------------------------------------------------------------------------
# upsert_book_entity
# ---------------------------------------------------------------------------


class TestUpsertBookEntity:
    def test_counts_existing_chapters(self, vault):
        # Pre-create two chapter source pages
        kb_writer.write_source_page(
            book_id="foo",
            chapter_index=1,
            chapter_title="Ch1",
            source_md="body 1",
        )
        kb_writer.write_source_page(
            book_id="foo",
            chapter_index=2,
            chapter_title="Ch2",
            source_md="body 2",
        )
        path = kb_writer.upsert_book_entity(
            book_id="foo",
            title="Foo Book",
            authors=["Alice", "Bob"],
            publisher="Wiley",
            pub_year=2024,
            book_subtype="textbook_pro",
            domain="bioenergetics",
            chapters_total=11,
        )
        fm, body = _read_page(path)
        assert fm["chapters_ingested"] == 2
        assert fm["chapters_total"] == 11
        assert fm["status"] == "partial"
        assert "[[ch1]]" in body
        assert "[[ch2]]" in body

    def test_preserves_created_on_update(self, vault):
        path = kb_writer.upsert_book_entity(book_id="foo", title="Foo", chapters_total=11)
        fm1, _ = _read_page(path)
        original_created = fm1["created"]

        # Change today's date by mutating fm1 then re-upsert
        kb_writer.upsert_book_entity(book_id="foo", title="Foo", chapters_total=11)
        fm2, _ = _read_page(path)
        assert fm2["created"] == original_created


# ---------------------------------------------------------------------------
# migrate_v1_to_v2 / backfill_all_v1_pages
# ---------------------------------------------------------------------------


class TestMigrateV1ToV2:
    def test_dry_run_no_write(self, vault):
        path = _make_v1_concept(vault, "x", with_legacy_block=True)
        before = path.read_text(encoding="utf-8")
        report = kb_writer.migrate_v1_to_v2("x", dry_run=True)
        assert report.from_version == 1
        assert report.to_version == 2
        assert report.dry_run is True
        # File untouched
        assert path.read_text(encoding="utf-8") == before
        # Changes logged
        assert any("legacy" in c for c in report.changes)

    def test_actual_write(self, vault):
        path = _make_v1_concept(vault, "x", with_legacy_block=True)
        report = kb_writer.migrate_v1_to_v2("x", dry_run=False)
        assert report.dry_run is False
        fm, body = _read_page(path)
        assert fm["schema_version"] == 2
        assert "## 更新" not in body

    def test_already_v2_skipped(self, vault):
        # write a v2 page first via upsert
        kb_writer.upsert_concept_page(
            slug="v2-page",
            action="create",
            source_link="[[s]]",
            title="v2-page",
            extracted_body="## Definition\n\nfoo\n",
        )
        report = kb_writer.migrate_v1_to_v2("v2-page", dry_run=True)
        assert report.skipped_reason == "already v2"

    def test_not_found(self, vault):
        report = kb_writer.migrate_v1_to_v2("nonexistent", dry_run=True)
        assert report.skipped_reason == "page not found"


class TestBackfillAll:
    def test_scans_all(self, vault):
        _make_v1_concept(vault, "a")
        _make_v1_concept(vault, "b")
        _make_v1_concept(vault, "c")
        reports = kb_writer.backfill_all_v1_pages(dry_run=True)
        assert len(reports) == 3
        assert all(r.from_version == 1 and r.to_version == 2 for r in reports)
