#!/usr/bin/env python3
"""Desktop media worker — package renders plus packaging revisions.

修修：「我按下存配方了，所以你不會自動 render 嗎？」——不會，因為 render 需要
Chrome／hyperframes／LINE Seed 字型，那些只在桌機（ADR-054 D11：VPS 叫不到桌機）。
本 watcher 就是桌機端那隻手：跟 Thousand Sunny 一起開機啟動，看到新配方就出圖。

行為：
- 每 `--interval` 秒掃一次 vault `Attachments/packaging/*/packages.json`
- 某個 package 的 `render_recipe.requested_at` 比上次處理過的新 → 跑
  `.claude/skills/thumbnail-brainstorm/scripts/render_request.py`（含幾何 solver、
  遮蔽收斂、face_measure 交付 gate、回填 rendered_png 與 packages.json）
- **同一份配方只出一次**：狀態記在 `logs/render-watcher-state.json`（key =
  slug/cut_id/rank，value = 已處理的 requested_at）。連按五次「存配方」也只 render 一次
- working-set packaging 目錄靠掃 `G:/Footages/*/packaging/packages.json` 的
  `episode` 欄位對回來（vault 端沒有這個路徑，也不該有——D10 硬規則①）
- 失敗不靜默：寫 log、記進 state 的 `last_error`，下一輪不會無限重試同一份
  （requested_at 沒變就不再跑，避免壞配方把 GPU/CPU 打滿）
- Reject + feedback 產生的 `revision_job.status=queued` → 備份舊版、啟動一個 bounded
  Codex packaging agent、驗證 working/vault/schema/PNG 後只標 `ready_for_review`；永不自動核准
- Highlight shortlist 產生的 `packaging.status=queued` → 以 sol 啟動完整 title + thumbnail
  Packaging agent；驗證三組 package 後標 `ready`。中斷的 running job 由下次 watcher 續跑

手動跑：
    python scripts/render_watcher.py --once      # 掃一輪就結束（測試用）
    python scripts/render_watcher.py             # 常駐
    python scripts/render_watcher.py --render-requests-only `
      --episode-slug 20260805-linzhichen --cut-id value-L01 --package-rank 1
        # 只 render 指定 Long package，不消耗其他 revision / initial packaging job
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PIL import Image

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from scripts.packaging_manifest import (  # noqa: E402
    claim_packaging_job,
    finish_packaging_job,
    load_manifest,
)
from shared.config import get_vault_path  # noqa: E402
from shared.schemas.packaging import parse_packages  # noqa: E402
from thousand_sunny.routers.packaging import _load_composition_receipt  # noqa: E402

RENDER_REQUEST = (
    _REPO / ".claude" / "skills" / "thumbnail-brainstorm" / "scripts" / "render_request.py"
)
FOOTAGE_ROOTS = (Path("G:/Footages"), Path("G:/footages"))


def _log(msg: str, log_path: Path | None) -> None:
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    if log_path:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def find_packaging_dir(episode_slug: str, *, episode_name: str | None = None) -> Path | None:
    """Find the working set by canonical episode name (vault slug may differ)."""
    expected_episode = episode_name or episode_slug
    for root in FOOTAGE_ROOTS:
        if not root.is_dir():
            continue
        for pkg in root.glob("*/packaging/packages.json"):
            try:
                if json.loads(pkg.read_text(encoding="utf-8")).get("episode") == expected_episode:
                    return pkg.parent
            except (json.JSONDecodeError, OSError):
                continue
    return None


def load_state(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


# 心跳：watcher 每一圈寫一次「我在看誰、我還活著」。沒有它，Bridge 分不出
# 「有人在等著做」和「根本沒人在聽」——2026-08-29 修修連續三次對著永遠不動的
# Queued 等，三次都是後者（跑著的 watcher 綁死在別的 cut 上）。
WATCHER_HEARTBEAT_KEY = "_watchers"


def record_heartbeat(state: dict, *, episode_slug: str | None, cut_id: str | None,
                     package_rank: int | None, now: str) -> dict:
    """把這支 watcher 的守備範圍寫進 state。"""
    scope = {
        "episode_slug": episode_slug,
        "cut_id": cut_id,
        "package_rank": package_rank,
    }
    key = f"{episode_slug or '*'}/{cut_id or '*'}/r{package_rank if package_rank else '*'}"
    watchers = dict(state.get(WATCHER_HEARTBEAT_KEY) or {})
    watchers[key] = {**scope, "seen_at": now, "pid": os.getpid()}
    state[WATCHER_HEARTBEAT_KEY] = watchers
    return state


def pending_requests(vault: Path, state: dict) -> list[dict]:
    """回傳需要 render 的配方（requested_at 比 state 記錄的新）。"""
    out: list[dict] = []
    root = vault / "Attachments" / "packaging"
    if not root.is_dir():
        return out
    for episode_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        slug = episode_dir.name
        packages_path = episode_dir / "packages.json"
        package_jobs: list[dict] = []
        if packages_path.is_file():
            try:
                packages = json.loads(packages_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                packages = {}
            for cut in packages.get("cuts", []):
                for package in cut.get("packages", []):
                    req = package.get("render_recipe")
                    if not req or not req.get("requested_at"):
                        continue
                    rank = int(package["title_rank"])
                    key = f"{slug}/{cut['cut_id']}/r{rank}"
                    done = (state.get(key) or {}).get("requested_at")
                    if done == req["requested_at"]:
                        continue
                    package_jobs.append(
                        {
                            "slug": slug,
                            "episode": packages.get("episode", slug),
                            "cut_id": cut["cut_id"],
                            "package_rank": rank,
                            "req": req,
                            "key": key,
                        }
                    )
        if package_jobs:
            out.extend(package_jobs)
            continue

        # Transitional fallback: episodes written before package.render_recipe.
        approval_path = episode_dir / "approval.json"
        if not approval_path.is_file():
            continue
        try:
            data = json.loads(approval_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for entry in data.get("approvals", []):
            req = entry.get("render_request")
            if not req or not req.get("requested_at"):
                continue
            key = f"{slug}/{entry['cut_id']}"
            done = (state.get(key) or {}).get("requested_at")
            if done == req["requested_at"]:
                continue  # 同一份配方已經出過圖
            out.append(
                {
                    "slug": slug,
                    "episode": data.get("episode", slug),
                    "cut_id": entry["cut_id"],
                    "req": req,
                    "key": key,
                }
            )
    return out


def filter_render_requests(
    jobs: list[dict],
    *,
    episode_slug: str | None = None,
    cut_id: str | None = None,
    package_rank: int | None = None,
) -> list[dict]:
    """Apply exact optional CLI scope to render-request jobs only."""
    return [
        job
        for job in jobs
        if (episode_slug is None or job.get("slug") == episode_slug)
        and (cut_id is None or job.get("cut_id") == cut_id)
        and (package_rank is None or job.get("package_rank") == package_rank)
    ]


def pending_revision_jobs(vault: Path) -> list[dict]:
    """Return queued packaging rejections awaiting a desktop revision agent."""
    out: list[dict] = []
    root = vault / "Attachments" / "packaging"
    if not root.is_dir():
        return out
    for approval_path in sorted(root.glob("*/approval.json")):
        try:
            data = json.loads(approval_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for entry in data.get("approvals", []):
            job = entry.get("revision_job")
            if not job or job.get("status") != "queued":
                continue
            out.append(
                {
                    "slug": approval_path.parent.name,
                    "episode": data.get("episode", approval_path.parent.name),
                    "cut_id": entry["cut_id"],
                    "request_id": job["request_id"],
                    "job": job,
                    "approval_path": approval_path,
                }
            )
    return out


def pending_packaging_jobs(vault: Path) -> list[dict]:
    """Return queued initial Packaging jobs, including interrupted running work."""
    out: list[dict] = []
    root = vault / "Attachments" / "packaging"
    if not root.is_dir():
        return out
    for manifest_path in sorted(root.glob("*/manifest.json")):
        episode_dir = manifest_path.parent
        manifest = load_manifest(episode_dir)
        packages_path = episode_dir / "packages.json"
        if not packages_path.is_file():
            continue
        try:
            packages = json.loads(packages_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"Packaging packages.json 無法讀取：{packages_path}") from exc
        episode_name = packages.get("episode")
        if not isinstance(episode_name, str) or not episode_name:
            raise RuntimeError(f"Packaging packages.json 缺 episode：{packages_path}")
        rows = sorted(
            manifest["cuts"].items(),
            key=lambda pair: (int(pair[1].get("rank") or 999), pair[0]),
        )
        for cut_id, row in rows:
            branch = row.get("packaging")
            status = branch.get("status") if isinstance(branch, dict) else None
            if status not in {"queued", "running"}:
                continue
            if status == "running":
                worker_host = branch.get("worker_host")
                worker_pid = branch.get("worker_pid")
                if worker_host and worker_host != socket.gethostname():
                    # A different desktop owns this cut.  Do not steal it merely
                    # because both machines see the same synced vault.
                    continue
                if isinstance(worker_pid, int) and _process_is_running(worker_pid):
                    continue
            out.append(
                {
                    "slug": episode_dir.name,
                    "episode": episode_name,
                    "cut_id": cut_id,
                    "rank": int(row.get("rank") or 0),
                    "title": str(row.get("title") or cut_id),
                    "selected_at": row.get("selected_at"),
                    "resume": status == "running",
                    "manifest_path": manifest_path,
                }
            )
    return out


def _process_is_running(pid: int) -> bool:
    """Best-effort local process check used only to avoid duplicate agent launches."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def _update_revision_job(
    approval_path: Path,
    *,
    cut_id: str,
    request_id: str,
    updates: dict,
) -> dict:
    data = json.loads(approval_path.read_text(encoding="utf-8"))
    entry = next((row for row in data.get("approvals", []) if row.get("cut_id") == cut_id), None)
    if entry is None:
        raise RuntimeError(f"approval cut disappeared: {cut_id}")
    current = entry.get("revision_job") or {}
    if current.get("request_id") != request_id:
        raise RuntimeError(f"revision request changed while worker was running: {request_id}")
    current.update(updates)
    entry["revision_job"] = current
    # A revision worker may never approve its own output.
    entry["approved"] = False
    entry["decision"] = "reject"
    _atomic_json(approval_path, data)
    return current


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _revision_fingerprint(packages_bytes: bytes, assets: dict[str, str]) -> str:
    digest = hashlib.sha256(packages_bytes)
    for name, value in sorted(assets.items()):
        digest.update(b"\0")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
    return digest.hexdigest()


def _codex_command() -> str:
    if os.name == "nt":
        local_bin = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
        app_candidates = sorted(
            local_bin.glob("*/codex.exe"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if app_candidates:
            return str(app_candidates[0])
        app_codex = local_bin / "codex.exe"
        if app_codex.is_file():
            return str(app_codex)
        candidate = Path(os.environ.get("APPDATA", "")) / "npm" / "codex.cmd"
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("codex")
    if not found:
        raise RuntimeError("找不到 Codex CLI，無法啟動 Packaging revision agent")
    return found


def dispatch_revision_agent(context: dict) -> subprocess.CompletedProcess[str]:
    """Run one bounded, non-interactive Codex packaging revision agent."""
    job_dir = Path(context["job_dir"])
    prompt = f"""你是獨立的 Podcast Packaging Revision Agent。

先完整讀取 `{_REPO / '.claude' / 'skills' / 'thumbnail-brainstorm' / 'SKILL.md'}`，嚴格遵守。
工作請求在 `{context['request_path']}`，使用者 feedback 必須逐項處理。

允許修改的 production 範圍只有：
- working packaging: `{context['working_packaging_dir']}`
- vault packaging mirror: `{context['vault_packaging_dir']}`
- 本集 cutouts: `{context['vault_cutout_dir']}`

硬規則：
1. 不改任何 repo code、skill、approval.json、字幕、Resolve、YouTube 或發布狀態。
2. 不覆寫 revisions/{context['request_id']}/before 裡的舊版；舊版必須可回復。
3. 依 feedback 重做 `{context['cut_id']}` 的 package。若是封面問題，要重新檢查候選
   frame、cutout 邊緣與背景透明度，不能只改 JSON 宣稱完成。
   `cut_id=full` 是完整節目 N1：必須使用 `thumbnail_full`，絕對不得使用
   長精華 N2 `thumbnail_reaction`。作者訪談若 brief 含書封，書封必須放大、
   變暗並作為背景；封面必須有兩行大字。
   人物必須使用含完整雙肩的上半身 cutout；緊頭裁切、肩膀被原圖邊界切斷
   一律不得交付。
4. 新封面使用新的 request-id 檔名，更新 working 與 vault 的 packages.json；兩邊
   JSON 與所有輸出 PNG bytes 必須一致。
5. 每張封面必須為 1280×720 PNG。長精華 N2 產生／更新 composition
   measurement receipt；完整節目 N1 必須更新 `specs.json` 與 render spec。
6. 至少執行 packages schema、圖片尺寸、layout-specific QA。不可自動 Approve。
7. Cutout 去背必須 bounded：若沒有 GPU provider，先裁切人物並把最長邊縮到 1024px
   以下再推論；單一去背程序 5 分鐘沒有產物就停止並改走較快的既有 pipeline，不可讓
   full-resolution CPU BiRefNet 無界執行。
8. 完成後把簡短摘要寫到 `{job_dir / 'agent-summary.md'}`，然後正常結束；若無法
   符合 feedback，非零退出並說明 blocker。
"""
    command = [
        _codex_command(),
        "exec",
        "--ephemeral",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--approve-for-me",
        "--cd",
        str(job_dir),
        "--add-dir",
        context["working_episode_dir"],
        "--add-dir",
        context["vault_packaging_dir"],
        "--add-dir",
        context["vault_cutout_dir"],
        "--output-last-message",
        str(job_dir / "agent-last-message.txt"),
        "-",
    ]
    child_env = os.environ.copy()
    for inherited_name in (
        "CODEX_PERMISSION_PROFILE",
        "CODEX_SANDBOX_NETWORK_DISABLED",
        "CODEX_SESSION_ID",
        "CODEX_THREAD_ID",
    ):
        child_env.pop(inherited_name, None)
    stdout_path = job_dir / "agent.stdout.log"
    stderr_path = job_dir / "agent.stderr.log"
    with (
        stdout_path.open("w", encoding="utf-8") as stdout_fh,
        stderr_path.open("w", encoding="utf-8") as stderr_fh,
    ):
        result = subprocess.run(
            command,
            input=prompt,
            stdout=stdout_fh,
            stderr=stderr_fh,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(job_dir),
            env=child_env,
            shell=(os.name == "nt"),
            timeout=7200,
        )
    return subprocess.CompletedProcess(
        command,
        result.returncode,
        stdout_path.read_text(encoding="utf-8", errors="replace"),
        stderr_path.read_text(encoding="utf-8", errors="replace"),
    )


def dispatch_packaging_agent(context: dict) -> subprocess.CompletedProcess[str]:
    """Run the full initial title + thumbnail Packaging chain in bounded scope."""
    job_dir = Path(context["job_dir"])
    title_skill = _REPO / ".claude" / "skills" / "title-brainstorm" / "SKILL.md"
    thumbnail_skill = _REPO / ".claude" / "skills" / "thumbnail-brainstorm" / "SKILL.md"
    prompt = f"""你是獨立的 Podcast Long Highlight Packaging Agent。

先完整讀取 `{title_skill}` 與 `{thumbnail_skill}`，包含兩份 skill 指定的必要 reference，
然後依序執行完整 title-brainstorm → thumbnail-brainstorm，不得簡化 title panel 或只用
工作代號充當正式標題。工作請求在 `{context['request_path']}`；只處理
`{context['cut_id']}`。

允許修改的 production 範圍只有：
- working episode: `{context['working_episode_dir']}`
- working packaging: `{context['working_packaging_dir']}`
- vault packaging mirror: `{context['vault_packaging_dir']}`
- 本集 cutouts: `{context['vault_cutout_dir']}`

硬規則：
1. 不改 repo code、skill、approval.json、字幕、Resolve、影片、YouTube 或發布狀態。
2. 這是 format=long：title-brainstorm 必須完成 7 步、Top 5、獨立 cold-reader panel；
   thumbnail-brainstorm 必須為前三名產出三組 1280x720 PNG package。
3. 使用既有 emit/attach/render/schema 工具 merge exact cut，不得覆寫其他 cut。
4. 長精華必須是 thumbnail_reaction 且中央為真實圖像素材，產生 composition receipts；
   人物使用真實訪談畫面，雙肩與麥克風輪廓完整。
5. 中斷重跑時先檢查現有 packages/artifacts，沿用已完成且可驗證的成果，從缺的 stage 續跑；
   不重生已完整的 titles 或 thumbnails。
6. working/vault packages.json 與所有定稿 PNG bytes 必須一致；不可自動 Approve。
7. 完成後把摘要寫到 `{job_dir / 'agent-summary.md'}`。若任何必要輸入、來源或 QA 不成立，
   必須明確失敗，不得寫 READY 或假裝生成完成。
"""
    command = [
        _codex_command(),
        "exec",
        "--ephemeral",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--approve-for-me",
        "--model",
        "gpt-5.6-sol",
        "--cd",
        str(job_dir),
        "--add-dir",
        context["working_episode_dir"],
        "--add-dir",
        context["vault_packaging_dir"],
        "--add-dir",
        context["vault_cutout_dir"],
        "--output-last-message",
        str(job_dir / "agent-last-message.txt"),
        "-",
    ]
    child_env = os.environ.copy()
    for inherited_name in (
        "CODEX_PERMISSION_PROFILE",
        "CODEX_SANDBOX_NETWORK_DISABLED",
        "CODEX_SESSION_ID",
        "CODEX_THREAD_ID",
    ):
        child_env.pop(inherited_name, None)
    stdout_path = job_dir / "agent.stdout.log"
    stderr_path = job_dir / "agent.stderr.log"
    with (
        stdout_path.open("w", encoding="utf-8") as stdout_fh,
        stderr_path.open("w", encoding="utf-8") as stderr_fh,
    ):
        result = subprocess.run(
            command,
            input=prompt,
            stdout=stdout_fh,
            stderr=stderr_fh,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(job_dir),
            env=child_env,
            shell=(os.name == "nt"),
            timeout=14400,
        )
    return subprocess.CompletedProcess(
        command,
        result.returncode,
        stdout_path.read_text(encoding="utf-8", errors="replace"),
        stderr_path.read_text(encoding="utf-8", errors="replace"),
    )


def _validate_revision_outputs(
    *,
    packaging_dir: Path,
    vault_packaging_dir: Path,
    vault_root: Path,
    cut_id: str,
) -> tuple[bytes, dict[str, str]]:
    working_packages = packaging_dir / "packages.json"
    vault_packages = vault_packaging_dir / "packages.json"
    if working_packages.read_bytes() != vault_packages.read_bytes():
        raise RuntimeError("Agent 輸出的 working/vault packages.json 不一致")
    parsed = parse_packages(working_packages)
    cut = next((row for row in parsed.cuts if row.cut_id == cut_id), None)
    if cut is None or (cut.format == "long" and not cut.packages):
        raise RuntimeError(f"Agent 輸出缺少 cut/package: {cut_id}")
    outputs: dict[str, str] = {}
    for package in cut.packages:
        filename = Path(package.thumbnail_png).name
        working_png = packaging_dir / filename
        vault_png = vault_packaging_dir / filename
        if not working_png.is_file() or not vault_png.is_file():
            raise RuntimeError(f"Agent 輸出缺少封面：{filename}")
        if working_png.read_bytes() != vault_png.read_bytes():
            raise RuntimeError(f"Agent 輸出的 working/vault PNG 不一致：{filename}")
        with Image.open(working_png) as image:
            if image.format != "PNG" or image.size != (1280, 720):
                raise RuntimeError(f"封面必須是 1280x720 PNG：{filename}")
        if cut.format == "long" and cut.cut_id != "full":
            _load_composition_receipt(
                vault_packaging_dir,
                episode=parsed.episode,
                cut_id=cut_id,
                package_rank=package.title_rank,
                thumbnail_png=package.thumbnail_png,
                vault_root=vault_root,
            )
        outputs[package.thumbnail_png] = _sha256(working_png)
    if cut.cut_id == "full":
        _validate_full_episode_layout(
            packaging_dir=packaging_dir,
            vault_root=vault_root,
            cut=cut,
        )
    return working_packages.read_bytes(), outputs


def _validate_initial_packaging_outputs(
    *,
    packaging_dir: Path,
    vault_packaging_dir: Path,
    vault_root: Path,
    cut_id: str,
) -> dict[str, object]:
    """Prove that the initial agent emitted a complete reviewable long package."""
    packages_bytes, assets = _validate_revision_outputs(
        packaging_dir=packaging_dir,
        vault_packaging_dir=vault_packaging_dir,
        vault_root=vault_root,
        cut_id=cut_id,
    )
    return {
        "packages_sha256": hashlib.sha256(packages_bytes).hexdigest(),
        "assets": assets,
    }


def _validate_full_episode_layout(*, packaging_dir: Path, vault_root: Path, cut) -> None:
    """Fail closed when a full episode is rendered with the long-highlight N2 layout."""
    manifest_path = packaging_dir / "specs.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("完整節目缺少可驗證的 specs.json") from exc
    if not isinstance(manifest, list):
        raise RuntimeError("完整節目 specs.json 必須是 package list")
    by_rank = {row.get("title_rank"): row for row in manifest if isinstance(row, dict)}
    brief_path = packaging_dir / "briefs" / "full.json"
    try:
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        brief = {}
    author_book = bool(brief.get("book_cover"))

    for package in cut.packages:
        row = by_rank.get(package.title_rank)
        if row is None:
            raise RuntimeError(f"完整節目 specs.json 缺 package rank {package.title_rank}")
        if Path(str(row.get("thumbnail", ""))).name != Path(package.thumbnail_png).name:
            raise RuntimeError(f"完整節目 rank {package.title_rank} thumbnail/specs 不一致")
        spec_path = Path(str(row.get("render_spec", "")))
        if not spec_path.is_absolute():
            spec_path = packaging_dir / spec_path
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"完整節目 rank {package.title_rank} render spec 無法讀取"
            ) from exc
        if spec.get("composition") != "thumbnail_full":
            raise RuntimeError(
                f"完整節目 rank {package.title_rank} 套錯版面："
                "必須是 thumbnail_full，不可使用 thumbnail_reaction"
            )
        variables = spec.get("variables") or {}
        title_lines = variables.get("title_lines")
        if not isinstance(title_lines, list) or len(title_lines) < 2 or not all(title_lines):
            raise RuntimeError(f"完整節目 rank {package.title_rank} 缺少兩行封面大字")
        if author_book:
            images = spec.get("images") or {}
            if not images.get("book_cover_data_url"):
                raise RuntimeError(f"作者訪談 rank {package.title_rank} 缺少書封背景")
            if (
                float(variables.get("book_cover_opacity", 1)) > 0.55
                or float(variables.get("book_cover_brightness", 1)) > 0.65
                or float(variables.get("book_cover_height_pct", 0)) < 90
            ):
                raise RuntimeError(
                    f"作者訪談 rank {package.title_rank} 書封必須放大、變暗作為背景"
                )
        for role, rel in (("host", package.host_cutout), ("guest", package.guest_cutout)):
            try:
                with Image.open(vault_root / rel) as cutout:
                    ratio = cutout.width / cutout.height
            except OSError as exc:
                raise RuntimeError(
                    f"完整節目 rank {package.title_rank} 缺少 {role} cutout"
                ) from exc
            if ratio < 0.75:
                raise RuntimeError(
                    f"完整節目 rank {package.title_rank} {role} cutout 過窄，"
                    "疑似緊頭裁切，無法保證雙肩完整"
                )


def run_revision_job(
    job: dict,
    *,
    packaging_dir: Path | None = None,
    agent_runner: Callable[[dict], subprocess.CompletedProcess[str]] = dispatch_revision_agent,
    log_path: Path | None = None,
) -> bool:
    """Back up, dispatch and validate one queued packaging revision."""
    approval_path = Path(job["approval_path"])
    request = job["job"]
    request_id = job["request_id"]
    cut_id = job["cut_id"]
    packaging_dir = packaging_dir or find_packaging_dir(
        job["slug"], episode_name=job.get("episode")
    )
    if packaging_dir is None:
        _update_revision_job(
            approval_path,
            cut_id=cut_id,
            request_id=request_id,
            updates={
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": "working-set packaging dir not found",
            },
        )
        return False
    vault_packaging_dir = approval_path.parent
    vault_root = approval_path.parents[3]
    working_packages = packaging_dir / "packages.json"
    vault_packages = vault_packaging_dir / "packages.json"
    job_dir = packaging_dir / "revisions" / request_id
    before_dir = job_dir / "before"
    try:
        source_bytes = vault_packages.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != request["source_packages_sha256"]:
            raise RuntimeError("Reject 後 packages.json 已漂移，拒絕對錯版套 feedback")
        if working_packages.read_bytes() != source_bytes:
            raise RuntimeError("Reject 當下 working/vault packages.json 不一致")
        for rel, expected in request.get("source_assets", {}).items():
            vault_asset = vault_root / rel
            working_asset = packaging_dir / Path(rel).name
            if _sha256(vault_asset) != expected or _sha256(working_asset) != expected:
                raise RuntimeError(f"Reject 當下封面已漂移：{rel}")

        before_dir.mkdir(parents=True, exist_ok=True)
        (before_dir / "packages.json").write_bytes(source_bytes)
        for rel in request.get("source_assets", {}):
            source = vault_root / rel
            (before_dir / source.name).write_bytes(source.read_bytes())
        request_path = job_dir / "request.json"
        _atomic_json(
            request_path,
            {
                "contract": "packaging-revision-request-v1",
                "episode_slug": job["slug"],
                "cut_id": cut_id,
                **request,
            },
        )
        started_at = datetime.now(timezone.utc)
        _update_revision_job(
            approval_path,
            cut_id=cut_id,
            request_id=request_id,
            updates={
                "status": "running",
                "attempt": int(request.get("attempt", 0)) + 1,
                "started_at": started_at.isoformat(),
                "finished_at": None,
                "result_receipt": None,
                "error": None,
            },
        )
        context = {
            "request_id": request_id,
            "cut_id": cut_id,
            "job_dir": str(job_dir),
            "request_path": str(request_path),
            "working_packaging_dir": str(packaging_dir),
            "working_episode_dir": str(packaging_dir.parent),
            "vault_packaging_dir": str(vault_packaging_dir),
            "vault_cutout_dir": str(
                vault_root / "Attachments" / "cutouts" / "podcast" / job["slug"]
            ),
        }
        result = agent_runner(context)
        (job_dir / "agent.stdout.log").write_text(result.stdout or "", encoding="utf-8")
        (job_dir / "agent.stderr.log").write_text(result.stderr or "", encoding="utf-8")
        if result.returncode != 0:
            detail = (result.stderr or "")[-500:]
            raise RuntimeError(f"Packaging revision agent exit {result.returncode}: {detail}")
        output_packages, output_assets = _validate_revision_outputs(
            packaging_dir=packaging_dir,
            vault_packaging_dir=vault_packaging_dir,
            vault_root=vault_root,
            cut_id=cut_id,
        )
        before_fingerprint = _revision_fingerprint(
            source_bytes, request.get("source_assets", {})
        )
        after_fingerprint = _revision_fingerprint(output_packages, output_assets)
        if after_fingerprint == before_fingerprint:
            raise RuntimeError("Agent 沒有產生任何可驗證的 package 變更")
        finished_at = datetime.now(timezone.utc)
        receipt_rel = Path("revisions") / request_id / "result.json"
        _atomic_json(
            packaging_dir / receipt_rel,
            {
                "contract": "packaging-revision-result-v1",
                "request_id": request_id,
                "cut_id": cut_id,
                "feedback": request["feedback"],
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "before_fingerprint": before_fingerprint,
                "after_fingerprint": after_fingerprint,
                "output_packages_sha256": hashlib.sha256(output_packages).hexdigest(),
                "output_assets": output_assets,
                "agent_stdout_sha256": _sha256(job_dir / "agent.stdout.log"),
                "agent_stderr_sha256": _sha256(job_dir / "agent.stderr.log"),
                "approved": False,
            },
        )
        _update_revision_job(
            approval_path,
            cut_id=cut_id,
            request_id=request_id,
            updates={
                "status": "ready_for_review",
                "finished_at": finished_at.isoformat(),
                "result_receipt": receipt_rel.as_posix(),
                "error": None,
            },
        )
        _log(f"REVISION READY {job['slug']}/{cut_id} {request_id}", log_path)
        return True
    except KeyboardInterrupt:
        try:
            _update_revision_job(
                approval_path,
                cut_id=cut_id,
                request_id=request_id,
                updates={
                    "status": "failed",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "error": "Packaging revision worker interrupted",
                },
            )
        finally:
            _log(f"REVISION INTERRUPTED {job['slug']}/{cut_id} {request_id}", log_path)
        raise
    except Exception as exc:
        try:
            _update_revision_job(
                approval_path,
                cut_id=cut_id,
                request_id=request_id,
                updates={
                    "status": "failed",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(exc)[-1000:],
                },
            )
        except Exception as update_exc:
            _log(f"REVISION STATUS WRITE FAILED {request_id}: {update_exc}", log_path)
        _log(f"REVISION FAIL {job['slug']}/{cut_id} {request_id}: {exc}", log_path)
        return False


def run_packaging_job(
    job: dict,
    *,
    packaging_dir: Path | None = None,
    agent_runner: Callable[[dict], subprocess.CompletedProcess[str]] = dispatch_packaging_agent,
    log_path: Path | None = None,
) -> bool:
    """Claim, dispatch and validate one approved initial Long Packaging job."""
    manifest_dir = Path(job["manifest_path"]).parent
    cut_id = job["cut_id"]
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    claimed = False
    try:
        claim_packaging_job(
            manifest_dir,
            cut_id,
            worker_id=worker_id,
            worker_host=socket.gethostname(),
            worker_pid=os.getpid(),
            resume_existing=bool(job.get("resume")),
        )
        claimed = True
        packaging_dir = packaging_dir or find_packaging_dir(
            job["slug"], episode_name=job.get("episode")
        )
        if packaging_dir is None:
            raise RuntimeError("working-set packaging dir not found")
        working_episode_dir = packaging_dir.parent
        vault_root = manifest_dir.parents[2]
        vault_cutout_dir = (
            vault_root / "Attachments" / "cutouts" / "podcast" / job["slug"]
        )
        vault_cutout_dir.mkdir(parents=True, exist_ok=True)
        job_key = hashlib.sha256(cut_id.encode("utf-8")).hexdigest()[:16]
        job_dir = packaging_dir / "_jobs" / "initial" / job_key
        job_dir.mkdir(parents=True, exist_ok=True)
        request_path = job_dir / "request.json"
        _atomic_json(
            request_path,
            {
                "contract": "podcast-long-packaging-job-v1",
                "episode": job["episode"],
                "episode_slug": job["slug"],
                "cut_id": cut_id,
                "rank": job["rank"],
                "work_name": job["title"],
                "selected_at": job.get("selected_at"),
                "resume": bool(job.get("resume")),
                "source": {
                    "winners": str(working_episode_dir / "highlights" / "winners.json"),
                    "episode_dir": str(working_episode_dir),
                },
                "outputs": {
                    "working_packaging": str(packaging_dir),
                    "vault_packaging": str(manifest_dir),
                },
            },
        )
        context = {
            "cut_id": cut_id,
            "job_dir": str(job_dir),
            "request_path": str(request_path),
            "working_packaging_dir": str(packaging_dir),
            "working_episode_dir": str(working_episode_dir),
            "vault_packaging_dir": str(manifest_dir),
            "vault_cutout_dir": str(vault_cutout_dir),
        }
        _log(
            f"PACKAGING {'RESUME' if job.get('resume') else 'START'} "
            f"{job['slug']}/{cut_id}",
            log_path,
        )
        result = agent_runner(context)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "")[-500:]
            raise RuntimeError(f"Packaging agent exit {result.returncode}: {detail}")
        validation = _validate_initial_packaging_outputs(
            packaging_dir=packaging_dir,
            vault_packaging_dir=manifest_dir,
            vault_root=vault_root,
            cut_id=cut_id,
        )
        finished_at = datetime.now(timezone.utc).isoformat()
        _atomic_json(
            job_dir / "result.json",
            {
                "contract": "podcast-long-packaging-result-v1",
                "episode": job["episode"],
                "cut_id": cut_id,
                "finished_at": finished_at,
                **validation,
            },
        )
        finish_packaging_job(manifest_dir, cut_id, succeeded=True)
        _log(f"PACKAGING READY {job['slug']}/{cut_id}", log_path)
        return True
    except KeyboardInterrupt:
        # Leave ``running`` durable so the next watcher process resumes this cut.
        _log(f"PACKAGING INTERRUPTED {job['slug']}/{cut_id}", log_path)
        raise
    except Exception as exc:
        if claimed:
            try:
                finish_packaging_job(
                    manifest_dir,
                    cut_id,
                    succeeded=False,
                    error=str(exc),
                )
            except Exception as update_exc:
                _log(f"PACKAGING STATUS WRITE FAILED {cut_id}: {update_exc}", log_path)
        _log(f"PACKAGING FAIL {job['slug']}/{cut_id}: {exc}", log_path)
        return False


def render_one(job: dict, state: dict, state_path: Path, log_path: Path | None) -> bool:
    slug, cut_id = job["slug"], job["cut_id"]
    packaging_dir = find_packaging_dir(slug, episode_name=job.get("episode"))
    if packaging_dir is None:
        _log(
            f"SKIP {slug}/{cut_id}：找不到 working-set packaging 目錄（G:/Footages/*/packaging）",
            log_path,
        )
        state[job["key"]] = {
            "requested_at": job["req"]["requested_at"],
            "status": "failed",
            "rendered_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "last_error": "packaging dir not found",
        }
        save_state(state_path, state)
        return False

    started_at = datetime.now(timezone.utc).isoformat()
    state[job["key"]] = {
        "requested_at": job["req"]["requested_at"],
        "status": "running",
        "started_at": started_at,
        "rendered_at": None,
        "ok": None,
        "last_error": None,
    }
    save_state(state_path, state)
    _log(f"RENDER {slug}/{cut_id} 大字={job['req'].get('big_text')} → {packaging_dir}", log_path)
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(RENDER_REQUEST),
                "--episode-slug",
                slug,
                "--packaging-dir",
                str(packaging_dir),
                "--cut-id",
                cut_id,
            ]
            + (
                ["--package-rank", str(job["package_rank"])]
                if job.get("package_rank")
                else []
            ),
            cwd=str(_REPO),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
    except Exception as exc:
        state[job["key"]] = {
            "requested_at": job["req"]["requested_at"],
            "status": "failed",
            "started_at": started_at,
            "rendered_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "last_error": str(exc)[-500:],
        }
        save_state(state_path, state)
        _log(f"FAIL {slug}/{cut_id}: {exc}", log_path)
        return False
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()
    for line in tail[-6:]:
        _log(f"  {line}", log_path)
    ok = proc.returncode == 0
    state[job["key"]] = {
        "requested_at": job["req"]["requested_at"],
        "status": "done" if ok else "failed",
        "started_at": started_at,
        "rendered_at": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "last_error": None if ok else (proc.stderr or "")[-500:],
    }
    save_state(state_path, state)
    _log(f"{'DONE' if ok else 'FAIL'} {slug}/{cut_id}", log_path)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=float, default=5.0, help="掃描間隔秒數")
    ap.add_argument("--once", action="store_true", help="掃一輪就結束（測試用）")
    ap.add_argument(
        "--render-requests-only",
        action="store_true",
        help="只處理存配方 render request；跳過 revision 與 initial packaging jobs",
    )
    ap.add_argument("--episode-slug", help="只處理完全相符 episode slug 的 render request")
    ap.add_argument("--cut-id", help="只處理完全相符 cut id 的 render request")
    ap.add_argument(
        "--package-rank",
        type=int,
        choices=(1, 2, 3),
        help="只處理完全相符 package rank 的 render request",
    )
    ap.add_argument("--log", type=Path, default=_REPO / "logs" / "render-watcher.log")
    ap.add_argument(
        "--state",
        type=Path,
        default=_REPO / "logs" / "render-watcher-state.json",
        help="已處理配方的時間戳（同一份只 render 一次）",
    )
    args = ap.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)

    # 依賴先驗，缺什麼立刻講——不要跑到一半才在 QA 那步炸
    missing = []
    for mod in ("mediapipe", "PIL", "numpy"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        _log(f"FATAL 缺套件：{', '.join(missing)}（這個 venv 跑不了 render QA）", args.log)
        return 1
    if not RENDER_REQUEST.is_file():
        _log(f"FATAL 找不到 {RENDER_REQUEST}", args.log)
        return 1

    vault = get_vault_path()
    _log(f"watcher 啟動：vault={vault} interval={args.interval}s python={sys.executable}", args.log)

    while True:
        state = load_state(args.state)
        record_heartbeat(
            state,
            episode_slug=args.episode_slug,
            cut_id=args.cut_id,
            package_rank=args.package_rank,
            now=datetime.now(timezone.utc).isoformat(),
        )
        save_state(args.state, state)
        if not args.render_requests_only:
            for revision in pending_revision_jobs(vault):
                run_revision_job(revision, log_path=args.log)
        render_jobs = filter_render_requests(
            pending_requests(vault, state),
            episode_slug=args.episode_slug,
            cut_id=args.cut_id,
            package_rank=args.package_rank,
        )
        for job in render_jobs:
            render_one(job, state, args.state, args.log)
        if not args.render_requests_only:
            for packaging_job in pending_packaging_jobs(vault):
                run_packaging_job(packaging_job, log_path=args.log)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
