"""render_watcher 的狀態機（修修 2026-08-14：存配方 → 自動出圖，但同一份只出一次）。

真正的 render 很貴（Chrome + 字型 + mediapipe），這裡只測「決定要不要跑」那層：
- 新配方 → 待處理
- 同一個 requested_at 已處理 → 不再跑（連按五次存配方也只 render 一次）
- 改了配方（requested_at 變新）→ 再跑一次
- 沒有 render_request 的 cut → 完全不碰
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from scripts.render_watcher import (
    _validate_full_episode_layout,
    dispatch_packaging_agent,
    dispatch_revision_agent,
    filter_render_requests,
    find_packaging_dir,
    load_state,
    main,
    pending_packaging_jobs,
    pending_requests,
    pending_revision_jobs,
    render_one,
    run_packaging_job,
    run_revision_job,
    save_state,
)


def _approval(requested_at: str | None, cut_id: str = "full") -> dict:
    entry: dict = {
        "cut_id": cut_id,
        "approved": False,
        "primary_package": 1,
        "reject_note": None,
        "decided_at": "2026-08-14T00:00:00+00:00",
    }
    if requested_at:
        entry["render_request"] = {
            "title_rank": 2,
            "host_cutout": "Attachments/cutouts/podcast/ep/host_v1_serious.png",
            "guest_cutout": "Attachments/cutouts/podcast/ep/guest_v1_serious.png",
            "big_text": ["每天封鎖", "十個帳號"],
            "highlight_text": "十個",
            "requested_at": requested_at,
            "rendered_png": None,
        }
    return {"episode": "ep-slug", "approvals": [entry]}


@pytest.fixture
def vault(tmp_path):
    d = tmp_path / "Attachments" / "packaging" / "20260721-zhengguowei"
    d.mkdir(parents=True)
    (d / "approval.json").write_text(
        json.dumps(_approval("2026-08-14T10:00:00+00:00"), ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


def test_new_request_is_pending(vault):
    jobs = pending_requests(vault, {})
    assert len(jobs) == 1
    assert jobs[0]["cut_id"] == "full"
    assert jobs[0]["key"] == "20260721-zhengguowei/full"


def test_same_request_is_not_rendered_twice(vault):
    state = {"20260721-zhengguowei/full": {"requested_at": "2026-08-14T10:00:00+00:00"}}
    assert pending_requests(vault, state) == []


def test_edited_request_is_pending_again(vault):
    state = {"20260721-zhengguowei/full": {"requested_at": "2026-08-14T10:00:00+00:00"}}
    path = vault / "Attachments" / "packaging" / "20260721-zhengguowei" / "approval.json"
    path.write_text(
        json.dumps(_approval("2026-08-14T11:30:00+00:00"), ensure_ascii=False), encoding="utf-8"
    )
    jobs = pending_requests(vault, state)
    assert len(jobs) == 1
    assert jobs[0]["req"]["requested_at"] == "2026-08-14T11:30:00+00:00"


def test_cut_without_request_is_ignored(vault):
    path = vault / "Attachments" / "packaging" / "20260721-zhengguowei" / "approval.json"
    path.write_text(json.dumps(_approval(None), ensure_ascii=False), encoding="utf-8")
    assert pending_requests(vault, {}) == []


def test_render_one_persists_running_before_subprocess_and_done_after(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    working = tmp_path / "working"
    working.mkdir()
    state: dict = {}
    requested_at = "2026-08-27T13:17:07+00:00"
    job = {
        "slug": "episode-slug",
        "episode": "episode name",
        "cut_id": "value-L01",
        "package_rank": 1,
        "key": "episode-slug/value-L01/r1",
        "req": {"requested_at": requested_at, "big_text": []},
    }

    monkeypatch.setattr(
        "scripts.render_watcher.find_packaging_dir", lambda *args, **kwargs: working
    )

    def fake_run(*args, **kwargs):
        live = json.loads(state_path.read_text(encoding="utf-8"))[job["key"]]
        assert live["requested_at"] == requested_at
        assert live["status"] == "running"
        assert live["started_at"]
        assert live["last_error"] is None
        return SimpleNamespace(returncode=0, stdout="rendered", stderr="")

    monkeypatch.setattr("scripts.render_watcher.subprocess.run", fake_run)

    assert render_one(job, state, state_path, None)
    terminal = json.loads(state_path.read_text(encoding="utf-8"))[job["key"]]
    assert terminal["status"] == "done"
    assert terminal["rendered_at"]
    assert terminal["last_error"] is None


def test_render_one_persists_failed_terminal_state(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    working = tmp_path / "working"
    working.mkdir()
    state: dict = {}
    job = {
        "slug": "episode-slug",
        "episode": "episode name",
        "cut_id": "value-L01",
        "package_rank": 2,
        "key": "episode-slug/value-L01/r2",
        "req": {"requested_at": "2026-08-27T13:18:00+00:00", "big_text": []},
    }
    monkeypatch.setattr(
        "scripts.render_watcher.find_packaging_dir", lambda *args, **kwargs: working
    )
    monkeypatch.setattr(
        "scripts.render_watcher.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=7, stdout="", stderr="render failed visibly"
        ),
    )

    assert not render_one(job, state, state_path, None)
    terminal = json.loads(state_path.read_text(encoding="utf-8"))[job["key"]]
    assert terminal["status"] == "failed"
    assert "render failed visibly" in terminal["last_error"]


def test_per_package_recipes_are_queued_independently(vault):
    root = vault / "Attachments" / "packaging" / "20260721-zhengguowei"
    packages = {
        "episode": "ep-slug",
        "cuts": [
            {
                "cut_id": "full",
                "packages": [
                    {"title_rank": 1, "render_recipe": None},
                    {
                        "title_rank": 3,
                        "render_recipe": {
                            "title_rank": 3,
                            "big_text": ["分工是昆蟲", "人要變通才"],
                            "requested_at": "2026-08-21T08:05:28+00:00",
                        },
                    },
                ],
            }
        ],
    }
    (root / "packages.json").write_text(json.dumps(packages), encoding="utf-8")

    jobs = pending_requests(vault, {})

    assert len(jobs) == 1
    assert jobs[0]["package_rank"] == 3
    assert jobs[0]["key"] == "20260721-zhengguowei/full/r3"


def test_render_request_filters_are_exact_and_default_to_all():
    jobs = [
        {
            "slug": "20260805-linzhichen",
            "cut_id": "full",
            "package_rank": 3,
            "key": "20260805-linzhichen/full/r3",
        },
        {
            "slug": "20260805-linzhichen",
            "cut_id": "value-L01",
            "package_rank": 1,
            "key": "20260805-linzhichen/value-L01/r1",
        },
        {
            "slug": "another-episode",
            "cut_id": "value-L01",
            "package_rank": 1,
            "key": "another-episode/value-L01/r1",
        },
    ]

    assert filter_render_requests(jobs) == jobs
    assert [
        job["key"]
        for job in filter_render_requests(
            jobs,
            episode_slug="20260805-linzhichen",
            cut_id="value-L01",
            package_rank=1,
        )
    ] == ["20260805-linzhichen/value-L01/r1"]
    assert (
        filter_render_requests(
            jobs,
            episode_slug="20260805-linzhichen",
            cut_id="value-L01",
            package_rank=2,
        )
        == []
    )


def _neutralise_watcher_preflight(monkeypatch, tmp_path) -> None:
    """讓 CLI 的環境前驗過關——這兩支測的是 job 分派，不是這台機器裝了什麼。

    watcher 啟動前會確認 render QA 用的套件與 composition 檔在，缺了就 return 1。
    那個前驗本身有它自己的意義（在 QA 那步才炸太晚），只是跟這裡要驗的事無關。
    """
    import sys
    from types import ModuleType

    for name in ("mediapipe",):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, ModuleType(name))
    request_stub = tmp_path / "render_request.py"
    request_stub.write_text("", encoding="utf-8")
    monkeypatch.setattr("scripts.render_watcher.RENDER_REQUEST", request_stub)


def test_render_requests_only_cli_skips_revision_and_initial_jobs(monkeypatch, tmp_path):
    calls: list[tuple[str, str]] = []
    render_jobs = [
        {
            "slug": "20260805-linzhichen",
            "cut_id": "full",
            "package_rank": 3,
            "key": "20260805-linzhichen/full/r3",
        },
        {
            "slug": "20260805-linzhichen",
            "cut_id": "value-L01",
            "package_rank": 1,
            "key": "20260805-linzhichen/value-L01/r1",
        },
    ]
    revision = {"key": "revision-job"}
    initial = {"key": "initial-packaging-job"}
    _neutralise_watcher_preflight(monkeypatch, tmp_path)
    monkeypatch.setattr("scripts.render_watcher.get_vault_path", lambda: tmp_path)
    monkeypatch.setattr("scripts.render_watcher.pending_revision_jobs", lambda vault: [revision])
    monkeypatch.setattr("scripts.render_watcher.pending_requests", lambda vault, state: render_jobs)
    monkeypatch.setattr("scripts.render_watcher.pending_packaging_jobs", lambda vault: [initial])
    monkeypatch.setattr(
        "scripts.render_watcher.run_revision_job",
        lambda job, log_path=None: calls.append(("revision", job["key"])),
    )
    monkeypatch.setattr(
        "scripts.render_watcher.render_one",
        lambda job, state, state_path, log_path: calls.append(("render", job["key"])),
    )
    monkeypatch.setattr(
        "scripts.render_watcher.run_packaging_job",
        lambda job, log_path=None: calls.append(("initial", job["key"])),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "render_watcher.py",
            "--once",
            "--render-requests-only",
            "--episode-slug",
            "20260805-linzhichen",
            "--cut-id",
            "value-L01",
            "--package-rank",
            "1",
            "--log",
            str(tmp_path / "watcher.log"),
            "--state",
            str(tmp_path / "state.json"),
        ],
    )

    assert main() == 0
    assert calls == [("render", "20260805-linzhichen/value-L01/r1")]


def test_default_cli_still_runs_all_three_job_classes(monkeypatch, tmp_path):
    calls: list[str] = []
    _neutralise_watcher_preflight(monkeypatch, tmp_path)
    monkeypatch.setattr("scripts.render_watcher.get_vault_path", lambda: tmp_path)
    monkeypatch.setattr(
        "scripts.render_watcher.pending_revision_jobs", lambda vault: [{"key": "revision"}]
    )
    monkeypatch.setattr(
        "scripts.render_watcher.pending_requests",
        lambda vault, state: [
            {
                "slug": "episode",
                "cut_id": "value-L01",
                "package_rank": 1,
                "key": "render",
            }
        ],
    )
    monkeypatch.setattr(
        "scripts.render_watcher.pending_packaging_jobs", lambda vault: [{"key": "initial"}]
    )
    monkeypatch.setattr(
        "scripts.render_watcher.run_revision_job",
        lambda job, log_path=None: calls.append(job["key"]),
    )
    monkeypatch.setattr(
        "scripts.render_watcher.render_one",
        lambda job, state, state_path, log_path: calls.append(job["key"]),
    )
    monkeypatch.setattr(
        "scripts.render_watcher.run_packaging_job",
        lambda job, log_path=None: calls.append(job["key"]),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "render_watcher.py",
            "--once",
            "--log",
            str(tmp_path / "watcher.log"),
            "--state",
            str(tmp_path / "state.json"),
        ],
    )

    assert main() == 0
    assert calls == ["revision", "render", "initial"]


def test_queued_rejection_is_a_pending_agent_revision(vault):
    path = vault / "Attachments" / "packaging" / "20260721-zhengguowei" / "approval.json"
    payload = _approval(None)
    payload["approvals"][0]["decision"] = "reject"
    payload["approvals"][0]["reject_note"] = "換更好的 cutout，書封白底要去掉"
    payload["approvals"][0]["revision_job"] = {
        "contract": "packaging-revision-job-v1",
        "request_id": "revision-0123456789abcdef",
        "feedback": "換更好的 cutout，書封白底要去掉",
        "requested_at": "2026-08-21T06:00:00+00:00",
        "source_packages_sha256": "a" * 64,
        "source_assets": {},
        "status": "queued",
        "attempt": 0,
        "started_at": None,
        "finished_at": None,
        "result_receipt": None,
        "error": None,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    jobs = pending_revision_jobs(vault)
    assert len(jobs) == 1
    assert jobs[0]["slug"] == "20260721-zhengguowei"
    assert jobs[0]["cut_id"] == "full"
    assert jobs[0]["request_id"] == "revision-0123456789abcdef"


def _queued_packaging_manifest(vault: Path) -> tuple[Path, Path]:
    vault_ep = vault / "Attachments" / "packaging" / "20260721-zhengguowei"
    manifest = {
        "cuts": {
            "full": {"emitted": "2026-08-26T00:00:00+00:00"},
            "value-L01": {
                "rank": 1,
                "title": "Long 1 work name",
                "selected_at": "2026-08-27T01:00:00+00:00",
                "video": {"status": "ready"},
                "packaging": {"status": "queued"},
            },
        }
    }
    (vault_ep / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (vault_ep / "packages.json").write_text(
        json.dumps({"episode": "episode name", "cuts": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    return vault_ep, vault_ep / "manifest.json"


def test_queued_initial_packaging_job_is_discovered_once(vault):
    _queued_packaging_manifest(vault)

    jobs = pending_packaging_jobs(vault)

    assert [(job["cut_id"], job["rank"]) for job in jobs] == [("value-L01", 1)]


def test_running_initial_packaging_job_is_resumed_after_restart(vault):
    _, manifest_path = _queued_packaging_manifest(vault)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cuts"]["value-L01"]["packaging"] = {
        "status": "running",
        "worker_id": "interrupted-worker",
        "attempt": 1,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    jobs = pending_packaging_jobs(vault)

    assert len(jobs) == 1
    assert jobs[0]["resume"] is True


def test_running_initial_packaging_job_owned_by_live_worker_is_not_duplicated(vault):
    import os
    import socket

    _, manifest_path = _queued_packaging_manifest(vault)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cuts"]["value-L01"]["packaging"] = {
        "status": "running",
        "worker_host": socket.gethostname(),
        "worker_pid": os.getpid(),
        "attempt": 1,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert pending_packaging_jobs(vault) == []


def test_ready_and_failed_initial_packaging_jobs_are_not_dispatched(vault):
    _, manifest_path = _queued_packaging_manifest(vault)
    for status in ("ready", "failed"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["cuts"]["value-L01"]["packaging"] = {"status": status}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        assert pending_packaging_jobs(vault) == []


def test_initial_packaging_dispatch_uses_sol_and_bounded_directories(tmp_path, monkeypatch):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr("scripts.render_watcher._codex_command", lambda: "codex.exe")
    monkeypatch.setattr("scripts.render_watcher.subprocess.run", fake_run)

    dispatch_packaging_agent(
        {
            "cut_id": "value-L01",
            "job_dir": str(job_dir),
            "request_path": str(job_dir / "request.json"),
            "working_packaging_dir": str(tmp_path / "episode" / "packaging"),
            "working_episode_dir": str(tmp_path / "episode"),
            "vault_packaging_dir": str(tmp_path / "vault-packaging"),
            "vault_cutout_dir": str(tmp_path / "vault-cutouts"),
        }
    )

    command = captured["command"]
    assert command[command.index("--model") + 1] == "gpt-5.6-sol"
    assert "--approve-for-me" in command
    prompt = captured["kwargs"]["input"]
    assert "title-brainstorm" in prompt
    assert "thumbnail-brainstorm" in prompt
    assert "value-L01" in prompt


def test_initial_packaging_success_becomes_ready_and_failure_becomes_failed(vault, tmp_path):
    vault_ep, manifest_path = _queued_packaging_manifest(vault)
    episode = tmp_path / "episode"
    working = episode / "packaging"
    working.mkdir(parents=True)
    (episode / "highlights").mkdir()
    (episode / "highlights" / "winners.json").write_text("{}", encoding="utf-8")
    initial_packages = {"episode": "episode name", "generated_at": None, "cuts": []}
    for root in (working, vault_ep):
        (root / "packages.json").write_text(
            json.dumps(initial_packages, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def successful_agent(context: dict) -> SimpleNamespace:
        packages = {
            "episode": "episode name",
            "generated_at": "2026-08-27T03:00:00+00:00",
            "cuts": [
                {
                    "cut_id": "value-L01",
                    "format": "long",
                    "information_origin": "full_text",
                    "visual_recipe": "podcast",
                    "aspect": "16:9",
                    "titles": [
                        {
                            "text": f"title {rank}",
                            "archetype_id": "T-A3",
                            "angle_combo": ["反直覺"],
                            "payoff": "payoff",
                            "cite": "highlights/winners.json#value-L01",
                            "rank": rank,
                            **({"panel_note": "not selected"} if rank >= 4 else {}),
                        }
                        for rank in range(1, 6)
                    ],
                    "packages": [
                        {
                            "title_rank": rank,
                            "thumbnail_png": (
                                "Attachments/packaging/20260721-zhengguowei/"
                                f"pkg-value-L01-{rank}.png"
                            ),
                            "thumb_archetype_id": "T-V3",
                            "joint_pairing_id": f"JP-{rank}",
                            "host_cutout": "Attachments/cutouts/podcast/ep/host.png",
                            "guest_cutout": "Attachments/cutouts/podcast/ep/guest.png",
                        }
                        for rank in range(1, 4)
                    ],
                    "citations": [],
                    "brand_flags": [],
                }
            ],
        }
        payload = json.dumps(packages, ensure_ascii=False, indent=2) + "\n"
        for root in (working, vault_ep):
            (root / "packages.json").write_text(payload, encoding="utf-8")
            for rank in range(1, 4):
                Image.new("RGB", (1280, 720), "black").save(root / f"pkg-value-L01-{rank}.png")
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch_target = "scripts.render_watcher._validate_initial_packaging_outputs"
    # This unit tests worker state transitions; composition geometry is covered by
    # the production validator's own tests and is replaced with a deterministic seam.
    from unittest.mock import patch

    with patch(monkeypatch_target, return_value={"packages_sha256": "a" * 64}):
        job = pending_packaging_jobs(vault)[0]
        assert run_packaging_job(job, packaging_dir=working, agent_runner=successful_agent)
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["cuts"]["value-L01"]["packaging"]["status"] == "ready"
    assert pending_packaging_jobs(vault) == []

    saved["cuts"]["value-L02"] = {
        "rank": 2,
        "video": {"status": "queued"},
        "packaging": {"status": "queued"},
    }
    manifest_path.write_text(json.dumps(saved), encoding="utf-8")
    failed_job = pending_packaging_jobs(vault)[0]
    assert not run_packaging_job(
        failed_job,
        packaging_dir=working,
        agent_runner=lambda _context: SimpleNamespace(returncode=7, stdout="", stderr="boom"),
    )
    failed = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert failed["cuts"]["value-L02"]["packaging"]["status"] == "failed"
    assert "exit 7" in failed["cuts"]["value-L02"]["packaging"]["error"]


def test_revision_dispatch_uses_writable_reviewed_codex_environment(tmp_path, monkeypatch):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr("scripts.render_watcher._codex_command", lambda: "codex.exe")
    monkeypatch.setattr("scripts.render_watcher.subprocess.run", fake_run)
    for name in (
        "CODEX_PERMISSION_PROFILE",
        "CODEX_SANDBOX_NETWORK_DISABLED",
        "CODEX_SESSION_ID",
        "CODEX_THREAD_ID",
    ):
        monkeypatch.setenv(name, "inherited")

    dispatch_revision_agent(
        {
            "request_id": "revision-0123456789abcdef",
            "cut_id": "full",
            "job_dir": str(job_dir),
            "request_path": str(job_dir / "request.json"),
            "working_packaging_dir": str(tmp_path / "working"),
            "working_episode_dir": str(tmp_path / "episode"),
            "vault_packaging_dir": str(tmp_path / "vault-packaging"),
            "vault_cutout_dir": str(tmp_path / "vault-cutouts"),
        }
    )

    command = captured["command"]
    assert "--approve-for-me" in command
    assert "--ignore-rules" in command
    assert "--ignore-user-config" not in command
    assert "--sandbox" not in command
    child_env = captured["kwargs"]["env"]
    assert all(
        name not in child_env
        for name in (
            "CODEX_PERMISSION_PROFILE",
            "CODEX_SANDBOX_NETWORK_DISABLED",
            "CODEX_SESSION_ID",
            "CODEX_THREAD_ID",
        )
    )


def test_revision_agent_success_is_backed_up_and_returns_to_review(tmp_path):
    vault = tmp_path / "vault"
    vault_ep = vault / "Attachments" / "packaging" / "episode-slug"
    working = tmp_path / "episode" / "packaging"
    vault_ep.mkdir(parents=True)
    working.mkdir(parents=True)
    packages = {
        "episode": "episode name",
        "generated_at": "2026-08-21T06:00:00+00:00",
        "cuts": [
            {
                "cut_id": "full",
                "format": "long",
                "information_origin": "full_text",
                "visual_recipe": "podcast",
                "aspect": "16:9",
                "titles": [
                    {
                        "text": f"title {rank}",
                        "archetype_id": "T-A3",
                        "angle_combo": ["angle"],
                        "payoff": "payoff",
                        "cite": "release.srt#1",
                        "rank": rank,
                        **({"panel_note": "not selected"} if rank >= 4 else {}),
                    }
                    for rank in range(1, 6)
                ],
                "packages": [
                    {
                        "title_rank": rank,
                        "thumbnail_png": f"Attachments/packaging/episode-slug/pkg-full-{rank}.png",
                        "thumb_archetype_id": "T-V8",
                        "joint_pairing_id": f"JP-{rank}",
                        "host_cutout": "Attachments/cutouts/podcast/episode-slug/host.png",
                        "guest_cutout": "Attachments/cutouts/podcast/episode-slug/guest.png",
                    }
                    for rank in range(1, 4)
                ],
                "citations": [],
                "brand_flags": [],
            }
        ],
    }
    packages_bytes = (json.dumps(packages, ensure_ascii=False, indent=2) + "\n").encode()
    for root in (vault_ep, working):
        (root / "packages.json").write_bytes(packages_bytes)
        for rank in range(1, 4):
            Image.new("RGB", (1280, 720), "black").save(root / f"pkg-full-{rank}.png")
    cutout_dir = vault / "Attachments" / "cutouts" / "podcast" / "episode-slug"
    cutout_dir.mkdir(parents=True)
    Image.new("RGBA", (900, 900), (255, 255, 255, 255)).save(cutout_dir / "host.png")
    Image.new("RGBA", (900, 900), (255, 255, 255, 255)).save(cutout_dir / "guest.png")
    source_assets = {
        f"Attachments/packaging/episode-slug/pkg-full-{rank}.png": __import__("hashlib")
        .sha256((vault_ep / f"pkg-full-{rank}.png").read_bytes())
        .hexdigest()
        for rank in range(1, 4)
    }
    approval = {
        "episode": "episode name",
        "approvals": [
            {
                "cut_id": "full",
                "approved": False,
                "primary_package": 1,
                "reject_note": "背景書封要去白底",
                "decided_at": "2026-08-21T06:00:00+00:00",
                "decision": "reject",
                "revision_job": {
                    "contract": "packaging-revision-job-v1",
                    "request_id": "revision-0123456789abcdef",
                    "feedback": "背景書封要去白底",
                    "requested_at": "2026-08-21T06:00:00+00:00",
                    "source_packages_sha256": __import__("hashlib")
                    .sha256(packages_bytes)
                    .hexdigest(),
                    "source_assets": source_assets,
                    "status": "queued",
                    "attempt": 0,
                    "started_at": None,
                    "finished_at": None,
                    "result_receipt": None,
                    "error": None,
                },
            }
        ],
    }
    approval_path = vault_ep / "approval.json"
    approval_path.write_text(json.dumps(approval, ensure_ascii=False), encoding="utf-8")
    job = pending_revision_jobs(vault)[0]

    def fake_agent(context: dict) -> SimpleNamespace:
        for path in (Path(context["working_packaging_dir"]), Path(context["vault_packaging_dir"])):
            for rank in range(1, 4):
                Image.new("RGB", (1280, 720), "white").save(path / f"pkg-full-{rank}.png")
        (working / "briefs").mkdir()
        (working / "briefs" / "full.json").write_text(
            json.dumps({"book_cover": {"title": "book"}}), encoding="utf-8"
        )
        specs = []
        for rank in range(1, 4):
            render_spec = working / f"spec-full-{rank}.json"
            render_spec.write_text(
                json.dumps(
                    {
                        "composition": "thumbnail_full",
                        "variables": {
                            "title_lines": ["line one", "line two"],
                            "book_cover_opacity": 0.42,
                            "book_cover_brightness": 0.38,
                            "book_cover_height_pct": 100,
                        },
                        "images": {"book_cover_data_url": "book.png"},
                    }
                ),
                encoding="utf-8",
            )
            specs.append(
                {
                    "title_rank": rank,
                    "thumbnail": str(working / f"pkg-full-{rank}.png"),
                    "render_spec": str(render_spec),
                }
            )
        (working / "specs.json").write_text(json.dumps(specs), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    assert run_revision_job(job, packaging_dir=working, agent_runner=fake_agent)
    saved = json.loads(approval_path.read_text(encoding="utf-8"))["approvals"][0]
    assert saved["approved"] is False
    assert saved["revision_job"]["status"] == "ready_for_review"
    assert saved["revision_job"]["attempt"] == 1
    receipt = working / saved["revision_job"]["result_receipt"]
    assert receipt.is_file()
    before = working / "revisions" / "revision-0123456789abcdef" / "before"
    assert (before / "packages.json").read_bytes() == packages_bytes
    assert (before / "pkg-full-1.png").is_file()


def test_full_episode_rejects_long_highlight_layout_and_tight_cutout(tmp_path):
    packaging = tmp_path / "packaging"
    vault = tmp_path / "vault"
    cutouts = vault / "Attachments" / "cutouts"
    packaging.mkdir()
    cutouts.mkdir(parents=True)
    Image.new("RGBA", (900, 900), "white").save(cutouts / "host.png")
    Image.new("RGBA", (360, 720), "white").save(cutouts / "guest.png")
    spec_path = packaging / "spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "composition": "thumbnail_reaction",
                "variables": {"title_lines": ["one", "two"]},
                "images": {"book_cover_data_url": "book.png"},
            }
        ),
        encoding="utf-8",
    )
    (packaging / "specs.json").write_text(
        json.dumps([{"title_rank": 1, "thumbnail": "pkg.png", "render_spec": str(spec_path)}]),
        encoding="utf-8",
    )
    package = SimpleNamespace(
        title_rank=1,
        thumbnail_png="Attachments/packaging/episode/pkg.png",
        host_cutout="Attachments/cutouts/host.png",
        guest_cutout="Attachments/cutouts/guest.png",
    )
    with pytest.raises(RuntimeError, match="thumbnail_full"):
        _validate_full_episode_layout(
            packaging_dir=packaging,
            vault_root=vault,
            cut=SimpleNamespace(packages=[package]),
        )

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["composition"] = "thumbnail_full"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(RuntimeError, match="cutout 過窄"):
        _validate_full_episode_layout(
            packaging_dir=packaging,
            vault_root=vault,
            cut=SimpleNamespace(packages=[package]),
        )


def test_revision_failure_is_visible_and_never_approves(vault, tmp_path):
    path = vault / "Attachments" / "packaging" / "20260721-zhengguowei" / "approval.json"
    payload = _approval(None)
    payload["approvals"][0]["decision"] = "reject"
    payload["approvals"][0]["reject_note"] = "重做"
    payload["approvals"][0]["revision_job"] = {
        "contract": "packaging-revision-job-v1",
        "request_id": "revision-fedcba9876543210",
        "feedback": "重做",
        "requested_at": "2026-08-21T06:00:00+00:00",
        "source_packages_sha256": "a" * 64,
        "source_assets": {},
        "status": "queued",
        "attempt": 0,
        "started_at": None,
        "finished_at": None,
        "result_receipt": None,
        "error": None,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    working = tmp_path / "episode" / "packaging"
    working.mkdir(parents=True)

    called = False

    def must_not_run(_context: dict) -> SimpleNamespace:
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    assert not run_revision_job(
        pending_revision_jobs(vault)[0], packaging_dir=working, agent_runner=must_not_run
    )
    saved = json.loads(path.read_text(encoding="utf-8"))["approvals"][0]
    assert called is False
    assert saved["approved"] is False
    assert saved["revision_job"]["status"] == "failed"
    assert saved["revision_job"]["error"]


def test_broken_approval_json_does_not_crash_the_loop(vault):
    path = vault / "Attachments" / "packaging" / "20260721-zhengguowei" / "approval.json"
    path.write_text("{ 這不是 JSON", encoding="utf-8")
    assert pending_requests(vault, {}) == []  # 壞檔跳過，watcher 不倒


def test_state_round_trips(tmp_path):
    p = tmp_path / "state.json"
    assert load_state(p) == {}
    save_state(p, {"a/b": {"requested_at": "x"}})
    assert load_state(p)["a/b"]["requested_at"] == "x"


def test_state_survives_corruption(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("garbage", encoding="utf-8")
    assert load_state(p) == {}  # 壞掉就當空的重來，不是 crash


def test_find_packaging_dir_returns_none_when_absent():
    assert find_packaging_dir("no-such-episode-slug-xyz") is None


def test_find_packaging_dir_matches_episode_name_not_vault_slug(tmp_path, monkeypatch):
    footage = tmp_path / "Footages"
    packaging = footage / "20260805 林之晨" / "packaging"
    packaging.mkdir(parents=True)
    (packaging / "packages.json").write_text(
        json.dumps({"episode": "20260805 林之晨"}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr("scripts.render_watcher.FOOTAGE_ROOTS", (footage,))

    assert find_packaging_dir("20260805-linzhichen", episode_name="20260805 林之晨") == packaging


def test_watcher_records_which_cuts_it_is_covering():
    """沒有心跳，Bridge 就分不出「排隊中」和「根本沒人在聽」。"""
    import scripts.render_watcher as watcher

    state = {}
    watcher.record_heartbeat(
        state,
        episode_slug="20260805-linzhichen",
        cut_id="value-L02",
        package_rank=None,
        now="2026-08-29T14:00:00+00:00",
    )

    row = state["_watchers"]["20260805-linzhichen/value-L02/r*"]
    assert row["cut_id"] == "value-L02"
    assert row["seen_at"] == "2026-08-29T14:00:00+00:00"
    assert row["pid"] > 0


def test_a_second_watcher_does_not_erase_the_first():
    import scripts.render_watcher as watcher

    state = {}
    for cut in ("value-L02", "punch-L04"):
        watcher.record_heartbeat(
            state,
            episode_slug="20260805-linzhichen",
            cut_id=cut,
            package_rank=None,
            now="2026-08-29T14:00:00+00:00",
        )

    assert len(state["_watchers"]) == 2
