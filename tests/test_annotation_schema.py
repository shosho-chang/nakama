"""Unit tests for shared.schemas.annotation (PRD #337 Slice 1).

Coverage incremental — see TDD log below for what's added per cycle.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from shared.schemas.annotation import Annotation, AnnotationSet, Highlight


class TestHighlight:
    def test_round_trip(self):
        ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
        h = Highlight(
            id="hl-abc123",
            reftext="Sleep is essential for memory consolidation",
            created_at=ts,
            modified_at=ts,
        )
        dumped = h.model_dump()
        restored = Highlight.model_validate(dumped)
        assert restored == h
        assert restored.type == "hl"


class TestAnnotation:
    def test_round_trip_with_note(self):
        ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
        a = Annotation(
            id="ann-xyz789",
            reftext="REM sleep particularly relates to procedural memory",
            note="這跟 hippocampus 在 NREM 的 SWR 配對嗎？要查 Buzsaki 的書",
            created_at=ts,
            modified_at=ts,
        )
        dumped = a.model_dump()
        restored = Annotation.model_validate(dumped)
        assert restored == a
        assert restored.type == "ann"
        assert "Buzsaki" in restored.note

    def test_extra_forbidden(self):
        ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(ValidationError):
            Annotation(
                id="ann-1",
                reftext="x",
                note="y",
                created_at=ts,
                modified_at=ts,
                bogus_field="should fail",
            )


class TestAnnotationSet:
    def test_round_trip_mixed_marks(self):
        """Q1+Q8 凍結：Highlight 與 Annotation 共住單一 marks list，
        透過 type discriminator 還原成正確子類。"""
        ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
        s = AnnotationSet(
            source_slug="sleep-paper",
            source_path="KB/Raw/Articles/sleep-paper.md",
            marks=[
                Highlight(id="hl-1", reftext="A", created_at=ts, modified_at=ts),
                Annotation(
                    id="ann-1", reftext="B", note="my thought", created_at=ts, modified_at=ts
                ),
                Highlight(id="hl-2", reftext="C", created_at=ts, modified_at=ts),
            ],
        )
        dumped = s.model_dump()
        restored = AnnotationSet.model_validate(dumped)

        assert restored == s
        assert len(restored.marks) == 3
        assert isinstance(restored.marks[0], Highlight)
        assert isinstance(restored.marks[1], Annotation)
        assert isinstance(restored.marks[2], Highlight)
        assert restored.marks[1].note == "my thought"
