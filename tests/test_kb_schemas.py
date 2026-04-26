"""Unit tests for shared.schemas.kb (ADR-011 §3.5.3 Pydantic schemas).

Coverage:
- Round-trip: model_dump / model_validate 不損失欄位
- extra="forbid" reject unknown 欄位
- frozen=True reject mutation（FigureRef / ConflictBlock / ConceptAction）
- Mutability：ConceptPageV2 / ChapterSourcePageV2 / MigrationReport 可 mutate（upsert path）
- FigureRefSlug pattern 接受 fig/tab/eq prefix、reject 其他
- ConceptAction 4 種 action 全合法
- Defaults：list 欄位 default_factory 不共享
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from shared.schemas.kb import (
    ChapterSourcePageV2,
    ConceptAction,
    ConceptPageV2,
    ConflictBlock,
    FigureRef,
    MigrationReport,
)

# ---------------------------------------------------------------------------
# FigureRef
# ---------------------------------------------------------------------------


class TestFigureRef:
    def test_valid(self):
        f = FigureRef(
            ref="fig-1-1",
            path="Attachments/Books/foo/ch1/fig-1-1.png",
            caption="Schematic of ATP-PCr kinetics",
            llm_description="Three curves showing PCr depletion 0-30s",
            tied_to_section="1.2 Phosphagen System",
        )
        assert f.ref == "fig-1-1"
        assert f.llm_description.startswith("Three")

    def test_table_and_equation_slugs(self):
        FigureRef(ref="tab-2-3", path="x", caption="y", tied_to_section="z")
        FigureRef(ref="eq-11-1", path="x", caption="y", tied_to_section="z")

    @pytest.mark.parametrize(
        "bad_ref",
        ["chart-1-1", "fig-1", "fig_1_1", "FIG-1-1", "1-1-fig"],
    )
    def test_invalid_ref_pattern(self, bad_ref):
        with pytest.raises(ValidationError):
            FigureRef(ref=bad_ref, path="x", caption="y", tied_to_section="z")

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            FigureRef(
                ref="fig-1-1",
                path="x",
                caption="y",
                tied_to_section="z",
                unknown="oops",
            )

    def test_frozen(self):
        f = FigureRef(ref="fig-1-1", path="x", caption="y", tied_to_section="z")
        with pytest.raises(ValidationError):
            f.path = "mutated"

    def test_llm_description_optional(self):
        f = FigureRef(ref="tab-1-1", path="x", caption="y", tied_to_section="z")
        assert f.llm_description is None

    def test_round_trip(self):
        original = FigureRef(
            ref="fig-1-1",
            path="Attachments/x.png",
            caption="cap",
            llm_description="desc",
            tied_to_section="1.1",
        )
        restored = FigureRef.model_validate(original.model_dump())
        assert restored == original


# ---------------------------------------------------------------------------
# ChapterSourcePageV2
# ---------------------------------------------------------------------------


class TestChapterSourcePageV2:
    def _valid_kwargs(self) -> dict:
        return dict(
            lang="en",
            book_id="biochemistry-sport-exercise-2024",
            chapter_index=1,
            chapter_title="Energy Sources for Muscular Activity",
            section_anchors=["1.1 Introduction", "1.2 Phosphagen System"],
            page_range="4-16",
            ingested_at=date(2026, 4, 26),
            ingested_by="claude-code-opus-4.7",
        )

    def test_minimal(self):
        p = ChapterSourcePageV2(**self._valid_kwargs())
        assert p.schema_version == 2
        assert p.type == "book_chapter"
        assert p.source_type == "book"
        assert p.content_nature == "textbook"
        assert p.figures == []

    def test_with_figures(self):
        figs = [
            FigureRef(ref="fig-1-1", path="x.png", caption="c", tied_to_section="1.2"),
            FigureRef(ref="tab-1-1", path="t.md", caption="c", tied_to_section="1.4"),
        ]
        p = ChapterSourcePageV2(figures=figs, **self._valid_kwargs())
        assert len(p.figures) == 2

    def test_chapter_index_must_be_non_negative(self):
        kw = self._valid_kwargs()
        kw["chapter_index"] = -1
        with pytest.raises(ValidationError):
            ChapterSourcePageV2(**kw)

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            ChapterSourcePageV2(unknown_field="oops", **self._valid_kwargs())

    def test_can_mutate_figures(self):
        # 非 frozen — upsert path 需要 append
        p = ChapterSourcePageV2(**self._valid_kwargs())
        p.figures = [
            FigureRef(ref="fig-1-2", path="y.png", caption="c2", tied_to_section="1.3"),
        ]
        assert len(p.figures) == 1

    def test_round_trip(self):
        original = ChapterSourcePageV2(**self._valid_kwargs())
        dumped = original.model_dump(mode="json")
        restored = ChapterSourcePageV2.model_validate(dumped)
        assert restored.book_id == original.book_id
        assert restored.ingested_at == original.ingested_at


# ---------------------------------------------------------------------------
# ConceptPageV2
# ---------------------------------------------------------------------------


class TestConceptPageV2:
    def _valid_kwargs(self) -> dict:
        return dict(
            title="肌酸代謝",
            domain="bioenergetics",
            created=date(2026, 4, 25),
            updated=date(2026, 4, 26),
        )

    def test_minimal(self):
        p = ConceptPageV2(**self._valid_kwargs())
        assert p.schema_version == 2
        assert p.type == "concept"
        assert p.aliases == []
        assert p.mentioned_in == []
        assert p.discussion_topics == []
        assert p.confidence is None

    def test_full(self):
        p = ConceptPageV2(
            title="肌酸代謝",
            domain="bioenergetics",
            aliases=["creatine metabolism", "PCr metabolism"],
            mentioned_in=["[[Sources/Books/foo/ch1]]", "[[Sources/pubmed-123]]"],
            source_refs=["Sources/Books/foo/ch1.md"],
            discussion_topics=["PCr 主導窗口時長範圍"],
            confidence=0.85,
            tags=["#concept", "#energy-system"],
            created=date(2026, 4, 25),
            updated=date(2026, 4, 26),
        )
        assert "creatine metabolism" in p.aliases
        assert p.confidence == 0.85

    def test_confidence_bounds(self):
        kw = self._valid_kwargs()
        kw["confidence"] = 1.1
        with pytest.raises(ValidationError):
            ConceptPageV2(**kw)
        kw["confidence"] = -0.1
        with pytest.raises(ValidationError):
            ConceptPageV2(**kw)

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            ConceptPageV2(legacy_status="draft", **self._valid_kwargs())

    def test_can_mutate_mentioned_in(self):
        # 非 frozen — upsert path 需要 append mentioned_in
        p = ConceptPageV2(**self._valid_kwargs())
        p.mentioned_in.append("[[Sources/x]]")
        assert "[[Sources/x]]" in p.mentioned_in

    def test_default_lists_independent(self):
        # default_factory 不共享 list reference
        p1 = ConceptPageV2(**self._valid_kwargs())
        p2 = ConceptPageV2(**self._valid_kwargs())
        p1.mentioned_in.append("[[a]]")
        assert p2.mentioned_in == []

    def test_round_trip(self):
        original = ConceptPageV2(
            title="肌酸代謝",
            domain="bioenergetics",
            aliases=["creatine metabolism"],
            mentioned_in=["[[Sources/x]]"],
            discussion_topics=["topic 1"],
            confidence=0.85,
            created=date(2026, 4, 25),
            updated=date(2026, 4, 26),
        )
        dumped = original.model_dump(mode="json")
        restored = ConceptPageV2.model_validate(dumped)
        assert restored == original


# ---------------------------------------------------------------------------
# ConflictBlock
# ---------------------------------------------------------------------------


class TestConflictBlock:
    def test_full(self):
        c = ConflictBlock(
            topic="PCr 主導窗口時長範圍",
            existing_claim="10-15 秒",
            new_claim="1-10 秒",
            possible_reason="不同 ATP depletion endpoint",
            consensus="PCr 是高強度爆發的主能量",
            uncertainty="5-10s 區間 PCr vs 糖解占比",
        )
        assert c.topic.startswith("PCr")

    def test_minimal(self):
        c = ConflictBlock(topic="t", existing_claim="e", new_claim="n")
        assert c.possible_reason is None
        assert c.consensus is None
        assert c.uncertainty is None

    def test_frozen(self):
        c = ConflictBlock(topic="t", existing_claim="e", new_claim="n")
        with pytest.raises(ValidationError):
            c.topic = "mutated"

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            ConflictBlock(topic="t", existing_claim="e", new_claim="n", extra_field="x")


# ---------------------------------------------------------------------------
# ConceptAction
# ---------------------------------------------------------------------------


class TestConceptAction:
    def test_create_action(self):
        a = ConceptAction(slug="新概念", action="create", extracted_body="body content")
        assert a.action == "create"
        assert a.conflict is None

    def test_update_merge_action(self):
        a = ConceptAction(
            slug="肌酸代謝",
            action="update_merge",
            candidate_aliases=["creatine metabolism"],
            extracted_body="merged content",
        )
        assert a.candidate_aliases == ["creatine metabolism"]

    def test_update_conflict_action(self):
        c = ConflictBlock(topic="t", existing_claim="e", new_claim="n")
        a = ConceptAction(slug="肌酸代謝", action="update_conflict", conflict=c)
        assert a.conflict is c

    def test_noop_action(self):
        a = ConceptAction(slug="肌酸代謝", action="noop")
        assert a.extracted_body is None
        assert a.conflict is None

    def test_invalid_action(self):
        with pytest.raises(ValidationError):
            ConceptAction(slug="x", action="delete")

    def test_frozen(self):
        a = ConceptAction(slug="x", action="noop")
        with pytest.raises(ValidationError):
            a.slug = "mutated"

    def test_round_trip_with_nested_conflict(self):
        c = ConflictBlock(
            topic="t",
            existing_claim="e",
            new_claim="n",
            possible_reason="r",
        )
        original = ConceptAction(slug="x", action="update_conflict", conflict=c)
        dumped = original.model_dump(mode="json")
        restored = ConceptAction.model_validate(dumped)
        assert restored == original


# ---------------------------------------------------------------------------
# MigrationReport
# ---------------------------------------------------------------------------


class TestMigrationReport:
    def test_dry_run(self):
        r = MigrationReport(
            slug="肌酸代謝",
            from_version=1,
            to_version=2,
            dry_run=True,
            changes=["+ aliases: []", "- ## 更新（2026-04-13）block (10 lines)"],
        )
        assert r.dry_run is True
        assert len(r.changes) == 2

    def test_skipped(self):
        r = MigrationReport(
            slug="已是v2",
            from_version=2,
            to_version=2,
            dry_run=False,
            skipped_reason="already v2",
        )
        assert r.skipped_reason == "already v2"

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            MigrationReport(
                slug="x",
                from_version=1,
                to_version=2,
                dry_run=False,
                unknown="oops",
            )
