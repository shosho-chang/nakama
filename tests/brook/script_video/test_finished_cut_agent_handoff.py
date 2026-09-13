"""語意工作交接給當下 agent 的 runner（取代寫死的 Codex 子行程）。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.brook.script_video.finished_cut_production._agent_handoff import (  # noqa: E402
    AgentHandoffProcessRunner,
    HandoffPaths,
    SemanticHandoffError,
)


def _argv(workspace: Path, *, request_id: str | None = None) -> tuple[str, ...]:
    schema = workspace / "schema.json"
    schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    (workspace / "packet.json").write_text(
        json.dumps({"request": {"request_id": request_id}} if request_id else {"request": {}}),
        encoding="utf-8",
    )
    return (
        "codex",
        "exec",
        "--output-schema",
        str(schema),
        "--output-last-message",
        str(workspace / "out" / "last-message.json"),
        "-",
    )


def _clock():
    """單調時鐘：每次被問就前進一秒，讓逾時測試不必真的睡。"""
    ticks = iter(range(0, 10_000))
    return lambda: float(next(ticks))


def test_answer_written_by_the_agent_reaches_the_adapter(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    root = tmp_path / "handoff"
    answer = {"events": [{"event_id": "e1", "implementation_kind": "stock_video"}]}

    seen: list[HandoffPaths] = []

    def announce(paths: HandoffPaths) -> None:
        seen.append(paths)
        # agent 在這一刻把答案寫出來——真實情境是人／agent 在另一個 shell 寫檔
        paths.response.write_text(json.dumps(answer), encoding="utf-8")

    runner = AgentHandoffProcessRunner(root, announce=announce, sleep=lambda _: None)
    result = runner.run(
        _argv(workspace), cwd=workspace, prompt="請為這個 beat 選素材", timeout_sec=30
    )

    assert result.returncode == 0
    assert not result.timed_out
    # adapter 只讀 --output-last-message 指到的那個檔，內容必須逐字相同
    written = json.loads((workspace / "out" / "last-message.json").read_text(encoding="utf-8"))
    assert written == answer
    # packet 要留在持久目錄——adapter 的 workspace 是 TemporaryDirectory，回傳即消失
    assert seen[0].prompt.read_text(encoding="utf-8") == "請為這個 beat 選素材"
    assert json.loads(seen[0].schema.read_text(encoding="utf-8")) == {"type": "object"}


def test_timeout_leaves_the_packet_on_disk_and_says_so(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    root = tmp_path / "handoff"

    runner = AgentHandoffProcessRunner(
        root, announce=lambda _: None, sleep=lambda _: None, now=_clock()
    )
    result = runner.run(_argv(workspace), cwd=workspace, prompt="p", timeout_sec=3)

    assert result.timed_out
    assert result.returncode is None
    assert "response.json" in result.stderr
    # 逾時不得吃掉 packet：補完答案重跑同一個 command 要能接上
    assert (root / "out" / "prompt.md").is_file()


def test_malformed_answer_fails_loud_instead_of_reaching_the_adapter(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    root = tmp_path / "handoff"

    def announce(paths: HandoffPaths) -> None:
        paths.response.write_text("{ 這不是 JSON", encoding="utf-8")

    runner = AgentHandoffProcessRunner(root, announce=announce, sleep=lambda _: None)
    result = runner.run(_argv(workspace), cwd=workspace, prompt="p", timeout_sec=30)

    assert result.returncode == 1
    assert "不是合法 JSON" in result.stderr
    assert not (workspace / "out" / "last-message.json").exists()


def test_packet_without_output_path_is_refused(tmp_path):
    runner = AgentHandoffProcessRunner(tmp_path / "handoff")
    with pytest.raises(SemanticHandoffError):
        runner.run(("codex", "exec", "-"), cwd=tmp_path, prompt="p", timeout_sec=1)


def test_packet_travels_with_the_prompt_and_names_the_directory(tmp_path):
    """prompt 第一句就叫 worker 讀 packet.json，而 workspace 回傳即消失。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    root = tmp_path / "handoff"

    def announce(paths: HandoffPaths) -> None:
        paths.response.write_text(json.dumps({"events": []}), encoding="utf-8")

    runner = AgentHandoffProcessRunner(root, announce=announce, sleep=lambda _: None)
    runner.run(
        _argv(workspace, request_id="request-abc123"),
        cwd=workspace,
        prompt="p",
        timeout_sec=30,
    )

    handoff = root / "request-abc123"
    assert (
        json.loads((handoff / "packet.json").read_text(encoding="utf-8"))["request"]["request_id"]
        == "request-abc123"
    )


def test_preview_media_directory_travels_to_the_agent(tmp_path):
    """visual_review 的畫格在 workspace 的 media/ 子目錄——不帶過去，審查員就只能照文字猜。"""
    workspace = tmp_path / "ws"
    (workspace / "media").mkdir(parents=True)
    (workspace / "media" / "component-0001.png").write_bytes(b"PNG-frame-bytes")
    root = tmp_path / "handoff"

    def announce(paths: HandoffPaths) -> None:
        paths.response.write_text(json.dumps({"events": []}), encoding="utf-8")

    runner = AgentHandoffProcessRunner(root, announce=announce, sleep=lambda _: None)
    runner.run(
        _argv(workspace, request_id="request-media"),
        cwd=workspace,
        prompt="p",
        timeout_sec=30,
    )

    assert (
        root / "request-media" / "media" / "component-0001.png"
    ).read_bytes() == b"PNG-frame-bytes"
