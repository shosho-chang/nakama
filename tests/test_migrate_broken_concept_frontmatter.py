"""scripts.migrate_broken_concept_frontmatter — broken page recovery + idempotency."""

from __future__ import annotations

import textwrap

import yaml

from scripts.migrate_broken_concept_frontmatter import fix

_BROKEN_REF_PREFIX = (
    "KB/Wiki/Sources/International-Society-of-Sports-Nutrition-position-stand-"
    "safety-and-efficacy-of-creatine-supplementation-in-exercise-sport-and-medicine"
)
_BROKEN_ATP = textwrap.dedent(f"""\
    ---
    title: ATP再合成
    type: concept
    status: draft
    created: '2026-04-13'
    updated: '2026-04-13'
    source_refs:
    - KB/Wiki/Sources/Some-Short-Source.md
    - {_BROKEN_REF_PREFIX}
    ---

    Journal-of-the-International-Society-of-Sports-Nutrition.md
    confidence: medium
    tags: []
    related_pages: []
    ---

    # ATP再合成

    Body content here.
    """)


def _split_fm(content: str) -> tuple[dict, str]:
    head, body = content.split("\n---\n", 1)
    assert head.startswith("---\n")
    return yaml.safe_load(head[len("---\n") :]), body


def test_recovers_split_source_ref_and_merges_yaml_fragment():
    result = fix(_BROKEN_ATP)
    assert result is not None
    new_content, summary = result

    fm, body = _split_fm(new_content)
    expected_last_ref = (
        "KB/Wiki/Sources/International-Society-of-Sports-Nutrition-position-stand-"
        "safety-and-efficacy-of-creatine-supplementation-in-exercise-sport-and-"
        "medicine---Journal-of-the-International-Society-of-Sports-Nutrition.md"
    )
    assert fm["source_refs"][-1] == expected_last_ref
    assert fm["confidence"] == "medium"
    assert fm["tags"] == []
    assert fm["related_pages"] == []
    assert body.lstrip().startswith("# ATP再合成")
    assert "recovered source_refs[1] suffix" in summary
    assert "merged keys" in summary


def test_already_good_page_returns_none():
    good = textwrap.dedent("""\
        ---
        title: 健康概念
        type: concept
        source_refs:
        - KB/Wiki/Sources/foo.md
        ---

        # 健康概念
        Body.
        """)
    assert fix(good) is None


def test_idempotent_on_recovered_output():
    """A second pass on the fixed content must be a no-op."""
    first = fix(_BROKEN_ATP)
    assert first is not None
    new_content, _ = first
    assert fix(new_content) is None


def test_recovered_yaml_no_longer_folds_long_string():
    """Roundtrip: dump → load must give the same single-string source_ref (not split)."""
    result = fix(_BROKEN_ATP)
    assert result is not None
    new_content, _ = result
    fm, _ = _split_fm(new_content)
    last = fm["source_refs"][-1]
    assert last.endswith(".md")
    assert "---" in last  # the literal `---` triplet survives the round trip


def test_refuses_multiline_raw_segment():
    """Mystery multi-line raw text → abort, don't guess how to glue."""
    weird = textwrap.dedent("""\
        ---
        title: x
        source_refs:
        - foo
        ---

        line one of mystery text
        line two of mystery text
        confidence: low
        ---

        body
        """)
    assert fix(weird) is None


def test_no_source_refs_still_merges_yaml_fragment():
    """If page has the double-fence pattern but no source_refs, we still merge yaml frag."""
    no_refs = textwrap.dedent("""\
        ---
        title: x
        type: concept
        ---

        confidence: high
        tags: []
        ---

        body
        """)
    result = fix(no_refs)
    assert result is not None
    new_content, summary = result
    fm, _ = _split_fm(new_content)
    assert fm["confidence"] == "high"
    assert "merged keys" in summary


def test_no_yaml_fragment_returns_none():
    """Lost segment with no yaml-key line — abort (we can't tell what it was)."""
    only_raw = textwrap.dedent("""\
        ---
        title: x
        ---

        random orphan paragraph here
        ---

        body
        """)
    assert fix(only_raw) is None


def test_non_frontmatter_page_returns_none():
    plain = "# Just a markdown file\n\nNo frontmatter at all.\n"
    assert fix(plain) is None
