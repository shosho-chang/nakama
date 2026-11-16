"""shared/fleet_events.py — 呼叫 freedom-fleet 子行程的包裝（subprocess 全部 mock）。"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from shared import fleet_events
from shared.fleet_events import FleetEventsError


@pytest.fixture
def ff_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEDOM_FLEET_DIR", str(tmp_path))
    monkeypatch.delenv("FREEDOM_FLEET_PYTHON", raising=False)
    return tmp_path


def _proc(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


def test_success_passes_json_and_minimal_env(ff_dir, monkeypatch):
    monkeypatch.setenv("ZOOM_CLIENT_SECRET", "nakama-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "nakama-key")
    out = {"ok": True, "id": 1, "join_url": "u"}
    with patch.object(subprocess, "run", return_value=_proc(json.dumps(out))) as m:
        got = fleet_events.run("zoom", {"title": "會議"})
    assert got == out
    args, kw = m.call_args
    assert args[0] == [str(ff_dir / ".venv" / "bin" / "python"), "-m", "fleet.events", "zoom"]
    assert json.loads(kw["input"]) == {"title": "會議"}
    assert kw["cwd"] == str(ff_dir)
    assert kw["timeout"] == fleet_events.TIMEOUTS_S["zoom"]
    # nakama 的金鑰不能流進 freedom-fleet 子行程
    assert "ZOOM_CLIENT_SECRET" not in kw["env"] and "OPENAI_API_KEY" not in kw["env"]
    assert kw["env"]["PYTHONIOENCODING"] == "utf-8"


def test_python_override(ff_dir, monkeypatch):
    monkeypatch.setenv("FREEDOM_FLEET_PYTHON", "C:/py/python.exe")
    with patch.object(subprocess, "run", return_value=_proc('{"ok": true}')) as m:
        fleet_events.run("cover", {})
    assert m.call_args.args[0][0] == "C:/py/python.exe"


def test_error_json_maps_maybe_created(ff_dir):
    body = json.dumps({"ok": False, "error": "HTTP 500", "maybe_created": True})
    with patch.object(subprocess, "run", return_value=_proc(body, returncode=1)):
        with pytest.raises(FleetEventsError) as ei:
            fleet_events.run("post", {})
    assert "HTTP 500" in str(ei.value) and ei.value.maybe_done is True


def test_error_json_without_flag_is_not_maybe_done(ff_dir):
    body = json.dumps({"ok": False, "error": "缺欄位"})
    with patch.object(subprocess, "run", return_value=_proc(body, returncode=1)):
        with pytest.raises(FleetEventsError) as ei:
            fleet_events.run("post", {})
    assert ei.value.maybe_done is False


@pytest.mark.parametrize(("command", "maybe"), [("zoom", True), ("post", True), ("cover", False)])
def test_timeout(ff_dir, command, maybe):
    exc = subprocess.TimeoutExpired(cmd="x", timeout=1)
    with patch.object(subprocess, "run", side_effect=exc):
        with pytest.raises(FleetEventsError) as ei:
            fleet_events.run(command, {})
    assert ei.value.maybe_done is maybe


def test_garbage_output_for_side_effect_command_is_maybe_done(ff_dir):
    with patch.object(subprocess, "run", return_value=_proc("Traceback...", returncode=1)):
        with pytest.raises(FleetEventsError) as ei:
            fleet_events.run("post", {})
    assert ei.value.maybe_done is True


def test_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEDOM_FLEET_DIR", str(tmp_path / "nope"))
    with pytest.raises(FleetEventsError, match="找不到 freedom-fleet"):
        fleet_events.run("zoom", {})


def test_cannot_start_python_is_not_maybe_done(ff_dir):
    with patch.object(subprocess, "run", side_effect=FileNotFoundError("python")):
        with pytest.raises(FleetEventsError) as ei:
            fleet_events.run("post", {})
    assert ei.value.maybe_done is False


def test_unknown_command(ff_dir):
    with pytest.raises(ValueError):
        fleet_events.run("rm", {})
