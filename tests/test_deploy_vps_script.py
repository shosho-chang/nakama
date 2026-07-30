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
