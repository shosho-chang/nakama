"""Frontmatter extractor unit tests (ADR-023 §6)."""

from __future__ import annotations

import textwrap

import pytest

from agents.franky.state.frontmatter_extractor import (
    MissingFrontmatterKeyError,
    NoFrontmatterFoundError,
    extract,
    extract_from_path,
)

VAULT_PAGE = textwrap.dedent(
    """\
    ---
    proposal_id: adopt-mcp-serena
    metric_type: checklist
    success_metric: enable serena MCP and run 3 demo queries
    related_adr:
      - ADR-007
      - ADR-022
    related_issues:
      - "#474"
    week_iso: 2026-W18
    ---

    ## Stage 1 candidate

    Body content goes here.
    """
)


GH_ISSUE_BODY = textwrap.dedent(
    """\
    ## Summary

    Some preamble.

    ```yaml frontmatter
    proposal_id: adopt-mcp-serena
    metric_type: human_judged
    success_metric: owner agrees the new MCP is worth keeping
    related_adr: []
    related_issues: ["#474"]
    ```

    ## Acceptance criteria
    - foo
    """
)


def test_extract_from_vault_page_full_block():
    fm = extract(VAULT_PAGE)
    assert fm.proposal_id == "adopt-mcp-serena"
    assert fm.metric_type == "checklist"
    assert fm.related_adr == ["ADR-007", "ADR-022"]
    assert fm.related_issues == ["#474"]


def test_extract_from_gh_issue_fenced_block():
    fm = extract(GH_ISSUE_BODY)
    assert fm.proposal_id == "adopt-mcp-serena"
    assert fm.metric_type == "human_judged"
    assert fm.related_adr == []
    assert fm.related_issues == ["#474"]


def test_extract_no_block_raises():
    with pytest.raises(NoFrontmatterFoundError):
        extract("just a markdown body, no frontmatter at all")


@pytest.mark.parametrize(
    "missing_key",
    [
        "proposal_id",
        "metric_type",
        "success_metric",
        "related_adr",
        "related_issues",
    ],
)
def test_extract_missing_each_required_key(missing_key: str):
    base = {
        "proposal_id": "p-id",
        "metric_type": "checklist",
        "success_metric": "ok",
        "related_adr": [],
        "related_issues": [],
    }
    base.pop(missing_key)
    body = "---\n" + "\n".join(f"{k}: {v!r}" for k, v in base.items()) + "\n---\n"
    with pytest.raises(MissingFrontmatterKeyError) as exc:
        extract(body)
    assert missing_key in exc.value.missing


def test_extract_reports_all_missing_keys_at_once():
    body = "---\nproposal_id: only-this\n---\n"
    with pytest.raises(MissingFrontmatterKeyError) as exc:
        extract(body)
    # 4 of 5 keys missing; all reported.
    assert set(exc.value.missing) == {
        "metric_type",
        "success_metric",
        "related_adr",
        "related_issues",
    }


def test_extract_from_path_round_trip(tmp_path):
    p = tmp_path / "page.md"
    p.write_text(VAULT_PAGE, encoding="utf-8")
    fm = extract_from_path(p)
    assert fm.proposal_id == "adopt-mcp-serena"


def test_extract_coerces_single_value_to_list():
    # YAML lets you write `related_adr: ADR-007` as a scalar; we want it list-y.
    body = textwrap.dedent(
        """\
        ---
        proposal_id: scalar-list
        metric_type: checklist
        success_metric: ok
        related_adr: ADR-007
        related_issues: "#1"
        ---
        """
    )
    fm = extract(body)
    assert fm.related_adr == ["ADR-007"]
    assert fm.related_issues == ["#1"]
