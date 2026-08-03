"""deploy_vps.sh 的行為測試（2026-07-30 dry-run 副作用事故）。

核心不變式：**`--dry-run` 是唯讀的，不可移動 HEAD。**

舊版無條件 `git pull`、只在最後跳過 restart。後果是連續操作會靜默失敗：
先跑 dry-run 看計畫（← 已經把 code pull 下去）→ 再跑正式 deploy → 正式那次
看到 OLD_SHA == NEW_SHA → 「No new commits. Nothing to restart.」→ **服務永遠
不重啟**，VPS 停在「新檔案 + 舊 process」，正是本 script 當初被寫出來要防的
事故（2026-05-28 /bridge/digests 4 天 404）。

測試用真的 git repo（local bare remote），不 mock git。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "deploy_vps.sh"


def _find_bash() -> str | None:
    """Windows 上 PATH 的 `bash` 常是 WSL stub（execvpe /bin/bash 失敗），
    要先找 Git Bash。CI（Linux）直接用 PATH 上的 bash。"""
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    which = shutil.which("bash")
    if which and subprocess.run([which, "-c", "exit 0"], capture_output=True).returncode == 0:
        return which
    return None


_BASH = _find_bash()

pytestmark = pytest.mark.skipif(
    _BASH is None or shutil.which("git") is None,
    reason="需要可用的 bash 與 git",
)


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True
    )
    return out.stdout.strip()


@pytest.fixture
def deployable(tmp_path):
    """origin（bare）+ 一個落後 origin/main 一個 commit 的 clone。"""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    clone = tmp_path / "clone"

    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(seed))
    _git(seed, "config", "user.email", "t@t.t")
    _git(seed, "config", "user.name", "t")
    (seed / "README.md").write_text("v1\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "v1")
    _git(seed, "push", "origin", "main")

    _git(tmp_path, "clone", str(origin), str(clone))
    _git(clone, "config", "user.email", "t@t.t")
    _git(clone, "config", "user.name", "t")
    behind_sha = _git(clone, "rev-parse", "HEAD")

    # origin 前進一個 commit（只動 docs/ → 依 path 對映不需重啟任何服務）
    (seed / "docs").mkdir(exist_ok=True)
    (seed / "docs" / "note.md").write_text("v2\n", encoding="utf-8")
    _git(seed, "add", "docs/note.md")
    _git(seed, "commit", "-m", "v2 docs only")
    _git(seed, "push", "origin", "main")
    ahead_sha = _git(seed, "rev-parse", "HEAD")

    # script 用 `cd "$(dirname "$0")/.."` 推算 repo root → 必須照真實佈局放在 scripts/
    (clone / "scripts").mkdir(exist_ok=True)
    shutil.copy2(_SCRIPT, clone / "scripts" / "deploy_vps.sh")
    (clone / "requirements.txt").write_text("", encoding="utf-8")
    return clone, behind_sha, ahead_sha


def _run(clone: Path, *args: str):
    r = subprocess.run(
        [_BASH, "./scripts/deploy_vps.sh", *args],
        cwd=clone,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # script 沒真的跑起來（例如 bash stub）會讓「HEAD 沒動」變成假通過
    assert "execvpe" not in r.stderr, f"bash 沒跑起來：{r.stderr}"
    return r


def test_dry_run_does_not_move_head(deployable):
    """本次事故的核心回歸：dry-run 跑完 HEAD 必須還在原處。"""
    clone, behind, ahead = deployable
    r = _run(clone, "--dry-run")
    assert r.returncode == 0, f"dry-run 失敗:\n{r.stdout}\n{r.stderr}"
    assert "Dry run" in r.stdout, f"沒走到 dry-run 出口（假通過風險）:\n{r.stdout}"
    assert _git(clone, "rev-parse", "HEAD") == behind, (
        f"dry-run 移動了 HEAD（副作用）— stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    assert behind != ahead  # sanity：fixture 真的有落後


def test_dry_run_still_reports_incoming_commits(deployable):
    """不 pull 也要看得到將要進來什麼——否則 dry-run 沒有用。"""
    clone, _behind, ahead = deployable
    r = _run(clone, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert ahead[:7] in r.stdout or "v2 docs only" in r.stdout
    assert "Dry run" in r.stdout


def test_dry_run_then_real_deploy_still_sees_work(deployable):
    """連續操作不可靜默失敗：dry-run 之後的正式 deploy 仍須認得有新 commit。

    舊版在這裡會印「No new commits. Nothing to restart.」並 exit 0。
    """
    clone, _behind, ahead = deployable
    _run(clone, "--dry-run")
    r = _run(clone)
    assert "No new commits" not in r.stdout, f"dry-run 之後正式 deploy 認不出新 commit:\n{r.stdout}"
    assert _git(clone, "rev-parse", "HEAD") == ahead


def test_no_new_commits_exits_early(deployable):
    """已是最新時（且未 --force-all）不該做任何事。"""
    clone, _behind, _ahead = deployable
    _run(clone)  # 先 deploy 到最新
    r = _run(clone, "--dry-run")
    assert "No new commits" in r.stdout
    assert r.returncode == 0


# ---------------------------------------------------------------------------
# healthz 重試（2026-07-30 假失敗事故）
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_bin(tmp_path):
    """把 sudo / systemctl / pip 換成 stub，讓 healthz 那段在本機也跑得起來。"""
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    (bin_dir / "sudo").write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
    (bin_dir / "systemctl").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "pip").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    return bin_dir


def _delayed_healthz(delay_sec: float):
    """起一個 HTTP server：前 delay_sec 秒回 503，之後回 healthz JSON。

    模擬 systemd restart 後 uvicorn 還沒 listen 的空窗（VPS 實測 ~5s）。
    """
    import http.server
    import json as _json
    import threading
    import time as _time

    started = _time.monotonic()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if _time.monotonic() - started < delay_sec:
                self.send_error(503)
                return
            body = _json.dumps({"uptime_seconds": 3}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # 別把 request log 噴進測試輸出
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/healthz"


def _push_service_change(tmp_path: Path):
    """在 origin 推一個 thousand_sunny/ 變更 → NEED[thousand-sunny]=1，才會走 healthz。"""
    seed = tmp_path / "seed"
    (seed / "thousand_sunny").mkdir(exist_ok=True)
    (seed / "thousand_sunny" / "x.py").write_text("# touch\n", encoding="utf-8")
    _git(seed, "add", "thousand_sunny/x.py")
    _git(seed, "commit", "-m", "touch thousand_sunny")
    _git(seed, "push", "origin", "main")


def _run_env(clone: Path, stub_bin: Path, url: str, timeout: str):
    import os

    return subprocess.run(
        [_BASH, "./scripts/deploy_vps.sh"],
        cwd=clone,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={
            **os.environ,
            "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
            "HEALTHZ_URL": url,
            "HEALTHZ_TIMEOUT": timeout,
        },
    )


def test_healthz_waits_for_slow_starting_service(deployable, stub_bin, tmp_path):
    """舊版單次 curl 必落空 → exit 4 假失敗（deploy 其實成功）。"""
    clone, _behind, _ahead = deployable
    _push_service_change(tmp_path)
    srv, url = _delayed_healthz(delay_sec=4.0)
    try:
        r = _run_env(clone, stub_bin, url, "30")
        assert r.returncode == 0, f"healthz 假失敗（舊版行為）:\n{r.stdout}\n{r.stderr}"
        assert "uptime_seconds=3" in r.stdout, r.stdout
        assert "等待服務就緒" in r.stdout, "沒重試就過了 — 測試沒真的驗到重試路徑"
        assert "Deploy complete" in r.stdout
    finally:
        srv.shutdown()


def test_healthz_still_fails_when_service_never_comes_up(deployable, stub_bin, tmp_path):
    """真故障仍要 exit 4 — 重試不可以把 crash loop 吞掉。"""
    clone, _behind, _ahead = deployable
    _push_service_change(tmp_path)
    r = _run_env(clone, stub_bin, "http://127.0.0.1:59999/healthz", "4")
    assert r.returncode == 4, f"真故障沒被抓到:\n{r.stdout}\n{r.stderr}"
    assert "crash loop" in r.stderr or "沒起來" in r.stderr
