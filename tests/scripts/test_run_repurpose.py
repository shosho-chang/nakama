"""Tests for scripts/run_repurpose.py — Brook Line 1 CLI orchestrator (#292).

Covers:
- argparse: positional + all flags, --skip-channel append, --dry-run boolean
- _build_renderers: all 3 by default, skip-channel filters correctly
- _expected_filenames: predicts canonical filenames per channel
- --dry-run prints plan, instantiates NO RepurposeEngine
- happy path: SRT → engine.run called → artifact summary printed → exit 0
- error paths:
  - missing SRT → exit code 2
  - empty SRT → exit code 2
  - all channels skipped → exit code 2
  - engine returns errors → exit code 1
- cost summary uses start/stop_usage_tracking pattern
- skip-channel actually omits the renderer from engine kwargs
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make scripts/ importable as a package — the script uses relative imports
# only via sys.path manipulation, but for testing we import it directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.brook.repurpose_engine import (  # noqa: E402
    BLOG_FILENAME,
    FB_TONALS,
    IG_FILENAME,
    ChannelArtifact,
    ChannelArtifacts,
    EpisodeMetadata,
    Stage1Result,
    fb_filename,
)
from scripts import run_repurpose  # noqa: E402

# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _run_parse(argv: list[str]):
    """Helper: invoke `_parse_args()` with patched sys.argv."""
    with patch.object(sys, "argv", ["run_repurpose.py", *argv]):
        return run_repurpose._parse_args()


def test_parse_args_minimum_positional_only():
    args = _run_parse(["foo.srt"])
    assert args.srt_path == Path("foo.srt")
    assert args.host == "張修修"  # default
    assert args.guest is None  # default
    assert args.slug is None  # default
    assert args.podcast_url == ""  # default
    assert args.skip_channel == []
    assert args.dry_run is False


def test_parse_args_full_form():
    args = _run_parse(
        [
            "ep99.srt",
            "--host",
            "張修修",
            "--guest",
            "周郁凱",
            "--slug",
            "ep99-yu-kai-chou",
            "--podcast-url",
            "https://example.com/ep99",
        ]
    )
    assert args.srt_path == Path("ep99.srt")
    assert args.host == "張修修"
    assert args.guest == "周郁凱"
    assert args.slug == "ep99-yu-kai-chou"
    assert args.podcast_url == "https://example.com/ep99"


def test_parse_args_skip_channel_single():
    args = _run_parse(["foo.srt", "--skip-channel", "ig"])
    assert args.skip_channel == ["ig"]


def test_parse_args_skip_channel_repeated():
    """--skip-channel uses action=append; multiple flags accumulate."""
    args = _run_parse(["foo.srt", "--skip-channel", "ig", "--skip-channel", "fb"])
    assert args.skip_channel == ["ig", "fb"]


def test_parse_args_skip_channel_invalid_choice_exits():
    with pytest.raises(SystemExit):
        _run_parse(["foo.srt", "--skip-channel", "youtube"])


def test_parse_args_dry_run_flag():
    args = _run_parse(["foo.srt", "--dry-run"])
    assert args.dry_run is True


# ---------------------------------------------------------------------------
# _build_renderers
# ---------------------------------------------------------------------------


def test_build_renderers_all_three_by_default():
    """Empty skip set → blog/fb/ig all instantiated."""
    with (
        patch("scripts.run_repurpose.BlogRenderer") as B,
        patch("scripts.run_repurpose.FBRenderer") as F,
        patch("scripts.run_repurpose.IGRenderer") as IG,
    ):
        renderers = run_repurpose._build_renderers(set())

    assert set(renderers.keys()) == {"blog", "fb", "ig"}
    B.assert_called_once_with()
    F.assert_called_once_with()
    IG.assert_called_once_with()


def test_build_renderers_skip_ig():
    with (
        patch("scripts.run_repurpose.BlogRenderer"),
        patch("scripts.run_repurpose.FBRenderer"),
        patch("scripts.run_repurpose.IGRenderer") as IG,
    ):
        renderers = run_repurpose._build_renderers({"ig"})

    assert "ig" not in renderers
    assert {"blog", "fb"} == set(renderers.keys())
    IG.assert_not_called()


def test_build_renderers_skip_all():
    with (
        patch("scripts.run_repurpose.BlogRenderer") as B,
        patch("scripts.run_repurpose.FBRenderer") as F,
        patch("scripts.run_repurpose.IGRenderer") as IG,
    ):
        renderers = run_repurpose._build_renderers({"blog", "fb", "ig"})

    assert renderers == {}
    B.assert_not_called()
    F.assert_not_called()
    IG.assert_not_called()


def test_build_renderers_dict_insertion_order_blog_fb_ig():
    """dict preserves insertion order (Python 3.7+) — required for human-readable plans."""
    with (
        patch("scripts.run_repurpose.BlogRenderer"),
        patch("scripts.run_repurpose.FBRenderer"),
        patch("scripts.run_repurpose.IGRenderer"),
    ):
        renderers = run_repurpose._build_renderers(set())

    assert list(renderers.keys()) == ["blog", "fb", "ig"]


# ---------------------------------------------------------------------------
# _expected_filenames
# ---------------------------------------------------------------------------


def test_expected_filenames_all_channels():
    files = run_repurpose._expected_filenames(["blog", "fb", "ig"])
    assert BLOG_FILENAME in files
    for tonal in FB_TONALS:
        assert fb_filename(tonal) in files
    assert IG_FILENAME in files
    # Total = 1 (blog) + 4 (fb tonals) + 1 (ig) = 6
    assert len(files) == 6


def test_expected_filenames_blog_only():
    files = run_repurpose._expected_filenames(["blog"])
    assert files == [BLOG_FILENAME]


def test_expected_filenames_no_channels():
    assert run_repurpose._expected_filenames([]) == []


def test_expected_filenames_uses_constants_not_hardcoded():
    """Filenames must come from BLOG_FILENAME/IG_FILENAME/fb_filename(), not literals.

    Drift guard: if FB_TONALS gains a 5th tonal, this test passes; if filename
    constants change (e.g. blog.md → article.md), this test still passes.
    Hardcoded literal in the script would silently drift from engine constants.
    """
    files_full = run_repurpose._expected_filenames(["blog", "fb", "ig"])
    expected = [BLOG_FILENAME, *(fb_filename(t) for t in FB_TONALS), IG_FILENAME]
    assert files_full == expected


# ---------------------------------------------------------------------------
# --dry-run path
# ---------------------------------------------------------------------------


def test_main_dry_run_does_not_instantiate_engine(capsys):
    """--dry-run prints plan without calling RepurposeEngine."""
    with (
        patch("scripts.run_repurpose.BlogRenderer"),
        patch("scripts.run_repurpose.FBRenderer"),
        patch("scripts.run_repurpose.IGRenderer"),
        patch("scripts.run_repurpose.RepurposeEngine") as Engine,
        patch.object(
            sys, "argv", ["run_repurpose.py", "ep99.srt", "--dry-run", "--guest", "周郁凱"]
        ),
    ):
        run_repurpose.main()

    Engine.assert_not_called()
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "no LLM calls will be made" in out
    assert "周郁凱" in out
    assert "ep99.srt" in out


def test_main_dry_run_lists_expected_artifacts(capsys):
    with (
        patch("scripts.run_repurpose.BlogRenderer"),
        patch("scripts.run_repurpose.FBRenderer"),
        patch("scripts.run_repurpose.IGRenderer"),
        patch("scripts.run_repurpose.RepurposeEngine"),
        patch.object(sys, "argv", ["run_repurpose.py", "ep99.srt", "--dry-run"]),
    ):
        run_repurpose.main()

    out = capsys.readouterr().out
    # All 7 outputs (1 stage1 + 1 blog + 4 fb tonals + 1 ig) must be listed
    assert "stage1.json" in out
    assert BLOG_FILENAME in out
    assert IG_FILENAME in out
    for tonal in FB_TONALS:
        assert fb_filename(tonal) in out


def test_main_dry_run_includes_resolved_run_dir(capsys):
    """Dry run preview must include the resolved run dir path (computable, no I/O)."""
    with (
        patch("scripts.run_repurpose.BlogRenderer"),
        patch("scripts.run_repurpose.FBRenderer"),
        patch("scripts.run_repurpose.IGRenderer"),
        patch("scripts.run_repurpose.RepurposeEngine"),
        patch.object(sys, "argv", ["run_repurpose.py", "ep99.srt", "--dry-run", "--slug", "my-ep"]),
    ):
        run_repurpose.main()

    out = capsys.readouterr().out
    assert "Run dir" in out
    # Slug appears in the run dir path
    assert "my-ep" in out
    # data/repurpose/ root path appears
    assert "data" in out and "repurpose" in out


def test_main_dry_run_with_skip_channel_omits_filenames(capsys):
    with (
        patch("scripts.run_repurpose.BlogRenderer"),
        patch("scripts.run_repurpose.FBRenderer"),
        patch("scripts.run_repurpose.IGRenderer"),
        patch("scripts.run_repurpose.RepurposeEngine"),
        patch.object(
            sys, "argv", ["run_repurpose.py", "ep99.srt", "--dry-run", "--skip-channel", "ig"]
        ),
    ):
        run_repurpose.main()

    out = capsys.readouterr().out
    assert IG_FILENAME not in out
    assert BLOG_FILENAME in out
    for tonal in FB_TONALS:
        assert fb_filename(tonal) in out


# ---------------------------------------------------------------------------
# Live engine path
# ---------------------------------------------------------------------------


def _stub_artifacts_result(run_dir: Path) -> ChannelArtifacts:
    """Build a fake successful ChannelArtifacts (6 artifacts: 1 blog + 4 fb + 1 ig)."""
    artifacts = [
        ChannelArtifact(filename=BLOG_FILENAME, content="blog body\n", channel="blog"),
        *(
            ChannelArtifact(filename=fb_filename(t), content=f"fb {t} body\n", channel=f"fb-{t}")
            for t in FB_TONALS
        ),
        ChannelArtifact(filename=IG_FILENAME, content='{"cards": []}\n', channel="ig"),
    ]
    return ChannelArtifacts(
        run_dir=run_dir,
        stage1=Stage1Result(data={}, source_repr="<srt>"),
        artifacts=artifacts,
        errors={},
    )


def test_run_engine_missing_srt_returns_exit_code_2(tmp_path, capsys):
    nonexistent = tmp_path / "missing.srt"
    # Test _run_engine directly with a hand-built Namespace.
    from argparse import Namespace

    args = Namespace(
        srt_path=nonexistent,
        host="張修修",
        guest=None,
        slug=None,
        podcast_url="",
        skip_channel=[],
        dry_run=False,
    )
    rc = run_repurpose._run_engine(args, {})
    assert rc == 2
    err = capsys.readouterr().err
    assert "not found" in err


def test_run_engine_empty_srt_returns_exit_code_2(tmp_path, capsys):
    empty = tmp_path / "empty.srt"
    empty.write_text("", encoding="utf-8")

    from argparse import Namespace

    args = Namespace(
        srt_path=empty,
        host="張修修",
        guest=None,
        slug=None,
        podcast_url="",
        skip_channel=[],
        dry_run=False,
    )
    rc = run_repurpose._run_engine(args, {})
    assert rc == 2
    err = capsys.readouterr().err
    assert "empty" in err


def test_main_all_skipped_exits_2(capsys):
    with patch.object(
        sys,
        "argv",
        [
            "run_repurpose.py",
            "ep99.srt",
            "--skip-channel",
            "blog",
            "--skip-channel",
            "fb",
            "--skip-channel",
            "ig",
        ],
    ):
        with pytest.raises(SystemExit) as exc_info:
            run_repurpose.main()
    assert exc_info.value.code == 2


def test_run_engine_happy_path_returns_zero(tmp_path, capsys):
    """SRT exists + engine succeeds → exit 0 + artifact summary printed."""
    srt = tmp_path / "ep99.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n[SPEAKER_00] 你好\n", encoding="utf-8")

    fake_engine = MagicMock()
    fake_engine.run.return_value = _stub_artifacts_result(tmp_path / "out")

    from argparse import Namespace

    args = Namespace(
        srt_path=srt,
        host="張修修",
        guest="周郁凱",
        slug="ep99",
        podcast_url="https://example.com/ep99",
        skip_channel=[],
        dry_run=False,
    )

    with (
        patch("scripts.run_repurpose.RepurposeEngine", return_value=fake_engine),
        patch("scripts.run_repurpose.Line1Extractor"),
        patch("scripts.run_repurpose.start_usage_tracking"),
        patch("scripts.run_repurpose.stop_usage_tracking", return_value=[]),
    ):
        rc = run_repurpose._run_engine(
            args, {"blog": MagicMock(), "fb": MagicMock(), "ig": MagicMock()}
        )

    assert rc == 0
    fake_engine.run.assert_called_once()
    # The metadata passed in should reflect CLI args
    call_kwargs = fake_engine.run.call_args
    # Position 0 is srt_text, position 1 is metadata
    metadata = call_kwargs.args[1]
    assert isinstance(metadata, EpisodeMetadata)
    assert metadata.slug == "ep99"
    assert metadata.host == "張修修"
    assert metadata.extra["guest"] == "周郁凱"
    assert metadata.extra["podcast_episode_url"] == "https://example.com/ep99"

    out = capsys.readouterr().out
    assert "Run dir:" in out
    assert BLOG_FILENAME in out
    assert IG_FILENAME in out


def test_run_engine_errors_returns_exit_code_1(tmp_path, capsys):
    """If engine reports any channel errors → exit 1."""
    srt = tmp_path / "ep99.srt"
    srt.write_text("dummy", encoding="utf-8")

    result = _stub_artifacts_result(tmp_path / "out")
    result.errors = {"ig": RuntimeError("LLM timeout")}

    fake_engine = MagicMock()
    fake_engine.run.return_value = result

    from argparse import Namespace

    args = Namespace(
        srt_path=srt,
        host="張修修",
        guest=None,
        slug=None,
        podcast_url="",
        skip_channel=[],
        dry_run=False,
    )

    with (
        patch("scripts.run_repurpose.RepurposeEngine", return_value=fake_engine),
        patch("scripts.run_repurpose.Line1Extractor"),
        patch("scripts.run_repurpose.start_usage_tracking"),
        patch("scripts.run_repurpose.stop_usage_tracking", return_value=[]),
    ):
        rc = run_repurpose._run_engine(
            args, {"blog": MagicMock(), "fb": MagicMock(), "ig": MagicMock()}
        )

    assert rc == 1
    out = capsys.readouterr().out
    assert "Errors" in out
    assert "ig" in out
    assert "LLM timeout" in out


def test_run_engine_uses_filename_stem_when_slug_omitted(tmp_path):
    """When --slug is omitted, srt_path.stem is used."""
    srt = tmp_path / "my-episode-99.srt"
    srt.write_text("dummy", encoding="utf-8")

    fake_engine = MagicMock()
    fake_engine.run.return_value = _stub_artifacts_result(tmp_path / "out")

    from argparse import Namespace

    args = Namespace(
        srt_path=srt,
        host="張修修",
        guest=None,
        slug=None,  # Omitted
        podcast_url="",
        skip_channel=[],
        dry_run=False,
    )

    with (
        patch("scripts.run_repurpose.RepurposeEngine", return_value=fake_engine),
        patch("scripts.run_repurpose.Line1Extractor"),
        patch("scripts.run_repurpose.start_usage_tracking"),
        patch("scripts.run_repurpose.stop_usage_tracking", return_value=[]),
    ):
        run_repurpose._run_engine(args, {"blog": MagicMock()})

    metadata = fake_engine.run.call_args.args[1]
    assert metadata.slug == "my-episode-99"  # = srt.stem


# ---------------------------------------------------------------------------
# Cost summary
# ---------------------------------------------------------------------------


def test_print_cost_summary_no_records(capsys):
    run_repurpose._print_cost_summary([])
    out = capsys.readouterr().out
    assert "no main-thread LLM calls tracked" in out


def test_print_cost_summary_records(capsys):
    records = [
        {"model": "claude-sonnet-4-6", "input_tokens": 1000, "output_tokens": 500},
        {"model": "claude-sonnet-4-6", "input_tokens": 2000, "output_tokens": 800},
    ]
    run_repurpose._print_cost_summary(records)
    out = capsys.readouterr().out
    # 2 calls, 3000 in / 1300 out
    assert "2 calls" in out
    assert "in=3,000" in out
    assert "out=1,300" in out
    # Sonnet 4.6: 3000*$3 + 1300*$15 = $9000 + $19500 = $28500 / 1M = $0.0285
    # Tighten substring to exact 4-decimal print to catch magnitude regressions.
    assert "$0.0285" in out
    assert "FBRenderer" in out  # caveat printed


def test_print_cost_summary_includes_cache_tokens(capsys):
    """Cache write/read tokens factored into total cost (Anthropic pricing)."""
    records = [
        {
            "model": "claude-sonnet-4-6",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_write_tokens": 4000,
            "cache_read_tokens": 8000,
        },
    ]
    run_repurpose._print_cost_summary(records)
    out = capsys.readouterr().out
    # Total: 1000*3 + 500*15 + 4000*3.75 + 8000*0.30 = 3000 + 7500 + 15000 + 2400 = 27900 → $0.0279
    assert "$0.0279" in out
    assert "cache_w=4,000" in out
    assert "cache_r=8,000" in out


def test_print_cost_summary_omits_zero_cache_breakdown(capsys):
    """When cache tokens are 0, breakdown only shows in/out (not noise)."""
    records = [{"input_tokens": 1000, "output_tokens": 500}]
    run_repurpose._print_cost_summary(records)
    out = capsys.readouterr().out
    assert "cache_w" not in out
    assert "cache_r" not in out


def test_print_cost_summary_handles_missing_token_keys(capsys):
    """Defensive: records without input_tokens/output_tokens still produce output."""
    records = [{"model": "claude-sonnet-4-6"}]  # no token fields
    run_repurpose._print_cost_summary(records)
    out = capsys.readouterr().out
    assert "1 calls" in out
    assert "in=0" in out
