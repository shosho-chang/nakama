"""Tests for ``shared.entity_extractor.DryRunEntityExtractor``
(ADR-034 v2 PR4)."""

from __future__ import annotations

from shared.entity_extractor import DryRunEntityExtractor
from shared.schemas.reading_source import ReadingSource, SourceVariant
from shared.schemas.source_map import SourceMapBuildResult


def _reading_source() -> ReadingSource:
    return ReadingSource(
        source_id="inbox:Inbox/web/example.md",
        annotation_key="example",
        kind="inbox_document",
        title="Example",
        author=None,
        primary_lang="en",
        has_evidence_track=True,
        evidence_reason=None,
        variants=[
            SourceVariant(
                role="original",
                format="markdown",
                lang="en",
                path="Inbox/web/example.md",
            ),
        ],
        metadata={},
    )


def _empty_source_map() -> SourceMapBuildResult:
    return SourceMapBuildResult(
        source_id="inbox:Inbox/web/example.md",
        primary_lang="en",
        has_evidence_track=True,
        chapters_inspected=0,
        items=[],
        risks=[],
        error=None,
    )


def test_dry_run_returns_empty_list() -> None:
    extractor = DryRunEntityExtractor()
    assert extractor.extract(_reading_source(), _empty_source_map()) == []


def test_dry_run_is_deterministic() -> None:
    extractor = DryRunEntityExtractor()
    a = extractor.extract(_reading_source(), _empty_source_map())
    b = extractor.extract(_reading_source(), _empty_source_map())
    assert a == b
