from __future__ import annotations

import io
import json

from agents.usopp.social_publish import approve_short_targets, ensure_short_targets
from scripts.meta_publish_probe import main as probe_main
from scripts.publish_dispatch import main as dispatch_main
from scripts.publish_dispatch import write_json_output
from shared.release_store import ensure_target, get_release, register_release, update_target


def _approved_short(tmp_path, *, duration_sec: float = 59) -> dict:
    video = tmp_path / "short.mp4"
    video.write_bytes(b"video")
    release_id = register_release(
        "episode", "S1", "short", str(video), duration_sec=duration_sec, file_bytes=5
    )
    youtube_id = ensure_target(release_id, "youtube")
    update_target(youtube_id, title="Short", description="reviewed caption")
    release = get_release("episode", "S1")
    ensure_short_targets(release)
    release = get_release("episode", "S1")
    youtube = next(target for target in release["targets"] if target["platform"] == "youtube")
    approve_short_targets(release, youtube)
    return get_release("episode", "S1")


def test_release_dispatch_is_dry_run_by_default(tmp_path, capsys):
    _approved_short(tmp_path)
    assert dispatch_main(["--release", "--episode", "episode", "--cut", "S1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert {item["platform"] for item in payload["targets"]} == {
        "youtube",
        "instagram_reels",
        "facebook_reels",
    }
    assert all(target["status"] == "approved" for target in get_release("episode", "S1")["targets"])


def test_missing_meta_configuration_fails_only_selected_target(tmp_path, monkeypatch, capsys):
    release = _approved_short(tmp_path)
    for name in (
        "META_GRAPH_API_VERSION",
        "META_PAGE_ID",
        "META_IG_USER_ID",
        "META_PAGE_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    assert (
        dispatch_main(
            [
                "--release",
                "--episode",
                "episode",
                "--cut",
                "S1",
                "--platform",
                "instagram_reels",
                "--execute",
            ]
        )
        == 1
    )
    json.loads(capsys.readouterr().out)
    targets = {target["platform"]: target for target in get_release("episode", "S1")["targets"]}
    assert targets["instagram_reels"]["status"] == "failed"
    assert "no adapter configured" in targets["instagram_reels"]["error"]
    assert targets["youtube"]["status"] == "approved"
    assert targets["facebook_reels"]["status"] == "approved"
    assert release["id"] == get_release("episode", "S1")["id"]


def test_publish_probe_side_effect_commands_are_dry_run_by_default(tmp_path, monkeypatch, capsys):
    first = tmp_path / "01.png"
    second = tmp_path / "02.png"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    monkeypatch.setattr(
        "scripts.meta_publish_probe.build_meta_client",
        lambda: (_ for _ in ()).throw(AssertionError("must not connect during dry-run")),
    )
    assert (
        probe_main(
            [
                "ig-carousel",
                "--file",
                str(first),
                "--file",
                str(second),
                "--caption",
                "probe",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["dry_run"] is True


def test_json_output_is_safe_on_cp1252_console():
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252")

    write_json_output({"status": "completed", "caption": "中文標題"}, stream=console)
    console.flush()

    payload = raw.getvalue().decode("cp1252")
    assert json.loads(payload) == {"status": "completed", "caption": "中文標題"}
