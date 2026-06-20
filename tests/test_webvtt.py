"""Tests for shared.webvtt — the VTT→prose adapter feeding video KB ingest.

The cue-stream parser itself (parse_webvtt / coalesce / tag-stripping) is
exercised in detail by tests/test_av_reader.py, which imports the same code
through the back-compat alias in thousand_sunny.routers.robin. Here we cover
the public shared API and the new webvtt_to_prose() entry point that ingest
relies on.
"""

from __future__ import annotations

from shared.webvtt import parse_webvtt, webvtt_to_prose, webvtt_to_transcript_markdown

SAMPLE_VTT = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:03.000
Hello world.

00:00:03.000 --> 00:00:05.000
This is a test of the system.
"""


def test_parse_webvtt_public_api_shape():
    cues = parse_webvtt(SAMPLE_VTT)
    assert cues, "expected non-empty cue list"
    assert all({"start", "end", "label", "text"} <= set(c) for c in cues)
    assert any("Hello world." in c["text"] for c in cues)


def test_webvtt_to_prose_strips_timing_and_markup():
    prose = webvtt_to_prose(SAMPLE_VTT)
    # No WebVTT scaffolding leaks into the LLM-facing text.
    assert "-->" not in prose
    assert "00:00:01" not in prose
    assert "WEBVTT" not in prose
    # The actual spoken words survive.
    assert "Hello world." in prose
    assert "This is a test of the system." in prose


def test_webvtt_to_prose_empty_or_malformed_returns_empty_string():
    assert webvtt_to_prose("") == ""
    assert webvtt_to_prose("WEBVTT\n\nnot a cue line\n") == ""


def test_webvtt_to_prose_breaks_into_paragraphs():
    # Many short, distinct sentences → multiple blank-line-separated paragraphs
    # once the running paragraph exceeds paragraph_chars. Distinct text avoids
    # the adjacent-duplicate dedup in parse_webvtt.
    cues = "\n\n".join(
        f"00:00:{i:02d}.000 --> 00:00:{i + 1:02d}.000\nThis is sentence number {i} here."
        for i in range(1, 30)
    )
    prose = webvtt_to_prose("WEBVTT\n\n" + cues, paragraph_chars=120)
    assert prose
    assert "\n\n" in prose, "expected paragraph breaks for a long transcript"
    # Every paragraph stays a single line (sentences joined by spaces, not newlines).
    assert all("\n" not in para for para in prose.split("\n\n"))


def test_webvtt_to_prose_unescapes_html_entities():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n&gt;&gt; Welcome back &amp; hello.\n"
    prose = webvtt_to_prose(vtt)
    assert "&gt;" not in prose
    assert "&amp;" not in prose
    assert ">>" in prose


def test_webvtt_to_transcript_markdown_timestamped_paragraphs():
    md = webvtt_to_transcript_markdown(SAMPLE_VTT)
    assert md
    # Each paragraph carries its start timecode as a bold prefix (navigation +
    # LLM provenance anchor), but no raw vtt timecodes leak.
    assert "**[00:01]**" in md
    assert "Hello world." in md
    assert "This is a test of the system." in md
    assert "-->" not in md
    assert "00:00:01" not in md


def test_webvtt_to_transcript_markdown_empty_returns_empty():
    assert webvtt_to_transcript_markdown("") == ""
    assert webvtt_to_transcript_markdown("WEBVTT\n\nnot a cue line\n") == ""


def test_webvtt_to_transcript_markdown_multiple_paragraphs():
    cues = "\n\n".join(
        f"00:00:{i:02d}.000 --> 00:00:{i + 1:02d}.000\nThis is sentence number {i} here."
        for i in range(1, 30)
    )
    md = webvtt_to_transcript_markdown("WEBVTT\n\n" + cues, paragraph_chars=120)
    paras = md.split("\n\n")
    assert len(paras) >= 2  # exercises the mid-loop paragraph flush + label reset
    assert all(p.startswith("**[") for p in paras)
