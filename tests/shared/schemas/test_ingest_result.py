"""Schema tests for ``shared.schemas.ingest_result.IngestResult`` (Slice 1, issue #352).

Locks the contract that ``URLDispatcher`` produces and ``InboxWriter`` consumes:

- dict / JSON round-trip stays byte-equal (catches serialiser drift).
- Required fields raise ``ValidationError`` when missing.
- ``fulltext_layer`` literal rejects values outside the enum (Slice 2 cannot
  silently introduce a new layer without updating the schema).
- ``image_paths`` defaults to ``[]`` (Slice 1 always empty per PRD).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.schemas.ingest_result import IngestResult


def _ready_result() -> IngestResult:
    return IngestResult(
        status="ready",
        fulltext_layer="readability",
        fulltext_source="Readability",
        markdown="# Hello\n\nbody body body.",
        title="Hello",
        original_url="https://example.com/article",
    )


def _failed_short_content_result() -> IngestResult:
    return IngestResult(
        status="failed",
        fulltext_layer="firecrawl",
        fulltext_source="Firecrawl",
        markdown="",
        title="example.com/blocked",
        original_url="https://example.com/blocked",
        note="抓取結果太短，疑似 bot 擋頁",
    )


# ── Round-trip ───────────────────────────────────────────────────────────────


def test_dict_roundtrip_ready():
    original = _ready_result()
    reborn = IngestResult.model_validate(original.model_dump())
    assert reborn == original


def test_dict_roundtrip_failed_with_note():
    original = _failed_short_content_result()
    reborn = IngestResult.model_validate(original.model_dump())
    assert reborn == original


def test_json_roundtrip_failed_with_note():
    original = _failed_short_content_result()
    reborn = IngestResult.model_validate_json(original.model_dump_json())
    assert reborn == original


# ── Required field enforcement ───────────────────────────────────────────────


def test_missing_required_field_raises():
    """``markdown`` is required (even if empty string for failed results)."""
    with pytest.raises(ValidationError):
        IngestResult.model_validate(
            {
                "status": "ready",
                "fulltext_layer": "readability",
                "fulltext_source": "Readability",
                # missing "markdown"
                "title": "x",
                "original_url": "https://example.com/",
            }
        )


def test_missing_original_url_raises():
    with pytest.raises(ValidationError):
        IngestResult.model_validate(
            {
                "status": "ready",
                "fulltext_layer": "readability",
                "fulltext_source": "Readability",
                "markdown": "body",
                "title": "x",
                # missing "original_url"
            }
        )


# ── Literal enforcement ──────────────────────────────────────────────────────


def test_unknown_layer_rejected():
    """Future Slice 2 layer must extend the literal — drift is caught here."""
    with pytest.raises(ValidationError):
        IngestResult.model_validate(
            {
                "status": "ready",
                "fulltext_layer": "magic_new_engine",  # not in literal
                "fulltext_source": "Magic",
                "markdown": "body",
                "title": "x",
                "original_url": "https://example.com/",
            }
        )


def test_unknown_status_rejected():
    with pytest.raises(ValidationError):
        IngestResult.model_validate(
            {
                "status": "in_progress",  # not in literal — Slice 1 only ready/failed
                "fulltext_layer": "readability",
                "fulltext_source": "Readability",
                "markdown": "body",
                "title": "x",
                "original_url": "https://example.com/",
            }
        )


def test_extra_field_rejected():
    """``extra='forbid'`` — schema drift is caught at parse time."""
    with pytest.raises(ValidationError):
        IngestResult.model_validate(
            {
                "status": "ready",
                "fulltext_layer": "readability",
                "fulltext_source": "Readability",
                "markdown": "body",
                "title": "x",
                "original_url": "https://example.com/",
                "experimental_field": "should be rejected",
            }
        )


# ── Defaults ─────────────────────────────────────────────────────────────────


def test_image_paths_defaults_empty():
    """Slice 1 never populates image_paths — Slice 3 will."""
    result = _ready_result()
    assert result.image_paths == []


def test_error_and_note_default_none():
    result = _ready_result()
    assert result.error is None
    assert result.note is None


# ── Academic layers reserved for Slice 2 ────────────────────────────────────


@pytest.mark.parametrize(
    "layer",
    [
        "academic_pmc",
        "academic_europe_pmc",
        "academic_unpaywall",
        "academic_publisher_html",
        "academic_arxiv",
        "academic_biorxiv",
    ],
)
def test_academic_layers_accepted(layer):
    """Slice 2 will emit these — schema must accept them now to avoid migration."""
    result = IngestResult(
        status="ready",
        fulltext_layer=layer,
        fulltext_source="placeholder",
        markdown="body" * 60,
        title="t",
        original_url="https://example.com/",
    )
    assert result.fulltext_layer == layer
