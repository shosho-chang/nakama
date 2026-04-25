"""Tests for scripts/release.py — semver bump + changelog rendering."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts import release


def _commit(type_: str = "feat", desc: str = "add x", breaking: bool = False, scope=None):
    return release.Commit(sha="abc1234", type=type_, scope=scope, desc=desc, breaking=breaking)


# ---- _compute_bump ---------------------------------------------------------


def test_bump_breaking_change_overrides_everything():
    commits = [_commit("feat"), _commit("fix"), _commit("feat", breaking=True)]
    assert release._compute_bump(commits) == "major"


def test_bump_feat_yields_minor():
    commits = [_commit("feat"), _commit("fix"), _commit("docs")]
    assert release._compute_bump(commits) == "minor"


def test_bump_fix_only_yields_patch():
    commits = [_commit("fix"), _commit("docs"), _commit("chore")]
    assert release._compute_bump(commits) == "patch"


def test_bump_force_override_skips_auto_detection():
    commits = [_commit("feat", breaking=True)]  # would normally be major
    assert release._compute_bump(commits, force="patch") == "patch"


# ---- _bump_version ---------------------------------------------------------


@pytest.mark.parametrize(
    "last,level,expected",
    [
        ("v0.4.1", "patch", "0.4.2"),
        ("v0.4.1", "minor", "0.5.0"),
        ("v0.4.1", "major", "1.0.0"),
        ("v1.2.3", "major", "2.0.0"),
        (None, "patch", "0.0.1"),
        (None, "minor", "0.1.0"),
    ],
)
def test_bump_version(last, level, expected):
    assert release._bump_version(last, level) == expected


def test_bump_version_rejects_garbage_tag():
    with pytest.raises(SystemExit, match="can't parse"):
        release._bump_version("nightly", "patch")


# ---- _section_for ---------------------------------------------------------


@pytest.mark.parametrize(
    "ctype,expected",
    [
        ("feat", "Added"),
        ("fix", "Fixed"),
        ("docs", "Changed"),
        ("refactor", "Changed"),
        ("revert", "Removed"),
        ("chore", "Other"),
        ("nonexistent", "Other"),  # default fallback
    ],
)
def test_section_for(ctype, expected):
    c = _commit(type_=ctype)
    assert release._section_for(c) == expected


# ---- render_release_block --------------------------------------------------


def test_render_release_block_groups_by_section():
    plan = release.ReleasePlan(
        last_tag="v0.4.0",
        next_version="0.5.0",
        bump_level="minor",
        commits=[],
        sections={
            "Added": ["- feat: alpha (abc1234)", "- feat(api): beta (def5678)"],
            "Fixed": ["- fix: gamma (xyz9999)"],
        },
    )

    out = release.render_release_block(plan, today_iso="2026-04-25")

    assert "## [0.5.0] - 2026-04-25" in out
    assert "### Added" in out
    assert "### Fixed" in out
    # Order: Added before Fixed (per _SECTION_ORDER)
    assert out.index("### Added") < out.index("### Fixed")
    # No empty sections rendered
    assert "### Removed" not in out


def test_render_release_block_omits_empty_sections():
    plan = release.ReleasePlan(
        last_tag="v0.1.0",
        next_version="0.1.1",
        bump_level="patch",
        commits=[],
        sections={"Fixed": ["- fix: foo (abc1234)"]},
    )
    out = release.render_release_block(plan, today_iso="2026-04-25")
    assert "### Added" not in out
    assert "### Fixed" in out


# ---- apply_to_changelog ---------------------------------------------------


def test_apply_replaces_unreleased_block(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Added\n- feat: prior work\n\n---\n\n"
        "## [0.1.0] - 2026-01-01\n\n### Added\n- initial\n",
        encoding="utf-8",
    )
    plan = release.ReleasePlan(
        last_tag="v0.1.0",
        next_version="0.2.0",
        bump_level="minor",
        commits=[],
        sections={"Added": ["- feat: shipped now (abc1234)"]},
    )

    release.apply_to_changelog(plan, today_iso="2026-04-25", path=changelog)

    text = changelog.read_text(encoding="utf-8")
    # New empty Unreleased exists
    assert "## [Unreleased]" in text
    assert "(no entries yet" in text  # placeholder
    # New dated release block in place
    assert "## [0.2.0] - 2026-04-25" in text
    assert "feat: shipped now" in text
    # Old [0.1.0] section preserved
    assert "## [0.1.0] - 2026-01-01" in text
    assert "- initial" in text


def test_apply_raises_when_no_unreleased_block(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\nno unreleased here\n", encoding="utf-8")
    plan = release.ReleasePlan(
        last_tag="v0", next_version="0.0.1", bump_level="patch", commits=[], sections={}
    )
    with pytest.raises(SystemExit, match="no \\[Unreleased\\]"):
        release.apply_to_changelog(plan, today_iso="2026-04-25", path=changelog)


# ---- build_release_plan (mocked git) ---------------------------------------


def test_build_release_plan_raises_on_no_commits():
    with (
        patch("scripts.release._last_tag", return_value="v0.1.0"),
        patch("scripts.release._commits_since", return_value=[]),
    ):
        with pytest.raises(SystemExit, match="nothing to release"):
            release.build_release_plan()


def test_build_release_plan_full_flow():
    fake_commits = [
        release.Commit(sha="aaa1111", type="feat", scope=None, desc="add foo", breaking=False),
        release.Commit(sha="bbb2222", type="fix", scope="api", desc="bar", breaking=False),
        release.Commit(sha="ccc3333", type="docs", scope=None, desc="readme", breaking=False),
    ]
    with (
        patch("scripts.release._last_tag", return_value="v0.4.0"),
        patch("scripts.release._commits_since", return_value=fake_commits),
    ):
        plan = release.build_release_plan()

    assert plan.last_tag == "v0.4.0"
    assert plan.bump_level == "minor"  # has feat
    assert plan.next_version == "0.5.0"
    assert len(plan.commits) == 3
    assert "Added" in plan.sections
    assert "Fixed" in plan.sections
    assert "Changed" in plan.sections  # docs goes to Changed
