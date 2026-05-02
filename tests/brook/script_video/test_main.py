"""Tests for agents.brook.script_video.__main__ — CLI surface (#313 acceptance a)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.brook.script_video.__main__ import main


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """`--help` prints argparse help and exits 0."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Script-Driven Video Production pipeline" in out
    assert "--episode" in out


def test_missing_episode_arg_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    """Missing required `--episode` triggers argparse usage error (exit 2)."""
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_nonexistent_episode_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    """`--episode <id>` for a non-existent directory exits 1 with stderr message."""
    with pytest.raises(SystemExit) as exc:
        main(["--episode", "definitely-does-not-exist-9999"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Error" in err
    assert "Episode directory not found" in err or "not found" in err.lower()


def test_dry_run_validates_inputs_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--dry-run` validates inputs and exits 0 without invoking pipeline.run."""
    from agents.brook.script_video import pipeline

    # Build a minimal episode dir
    monkeypatch.setattr(pipeline, "_DATA_ROOT", tmp_path)
    episode_dir = tmp_path / "ep-dry"
    episode_dir.mkdir()
    (episode_dir / "script.md").write_text("[aroll-full]\nhello\n", encoding="utf-8")

    # Sentinel: pipeline.run must NOT be called in dry-run
    called = {"hit": False}

    def _fail_if_called(*_a, **_kw):
        called["hit"] = True
        raise AssertionError("pipeline.run was called during --dry-run")

    monkeypatch.setattr(pipeline, "run", _fail_if_called)

    main(["--episode", "ep-dry", "--dry-run"])
    out = capsys.readouterr().out
    assert "ep-dry" in out
    assert "OK" in out
    assert called["hit"] is False


def test_dry_run_missing_script_exits_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--dry-run` with missing script.md exits 1."""
    from agents.brook.script_video import pipeline

    monkeypatch.setattr(pipeline, "_DATA_ROOT", tmp_path)
    episode_dir = tmp_path / "ep-no-script"
    episode_dir.mkdir()
    # No script.md created

    with pytest.raises(SystemExit) as exc:
        main(["--episode", "ep-no-script", "--dry-run"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "script.md" in err


@pytest.mark.parametrize(
    "malicious",
    [
        "../etc/passwd",
        "..",
        "../../foo",
        "foo/bar",
        "foo\\bar",
        ".hidden",
        "foo..bar",
        "with space",
        "",
    ],
)
def test_path_traversal_episode_id_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    malicious: str,
) -> None:
    """Path-traversal / disallowed-char episode IDs exit 1 before touching disk."""
    from agents.brook.script_video import pipeline

    monkeypatch.setattr(pipeline, "_DATA_ROOT", tmp_path)

    with pytest.raises(SystemExit) as exc:
        main(["--episode", malicious, "--dry-run"])
    assert exc.value.code in (1, 2)  # argparse may reject empty before us
    err = capsys.readouterr().err
    # Either argparse usage error OR our validator's message
    assert (
        "Invalid episode_id" in err
        or "outside data root" in err
        or "argument --episode" in err  # argparse for empty
        or "expected one argument" in err
    )
