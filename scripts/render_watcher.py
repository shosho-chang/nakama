#!/usr/bin/env python3
"""Desktop media worker — package renders（封面配方出圖）.

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
- **已有成品的配方不出**：`render_recipe.rendered_png` 有值 = 這份配方已經出過圖
  （render_request 回填，或 attach 交付時就填好）。gate 存配方一律不帶它，所以只有
  修修真的存了新配方才會觸發；手改配方要重出圖，得把 rendered_png 清成 null
- working-set packaging 目錄靠掃 `G:/Footages/*/packaging/packages.json` 的
  `episode` 欄位對回來（vault 端沒有這個路徑，也不該有——D10 硬規則①）
- 失敗不靜默：寫 log、記進 state 的 `last_error`，下一輪不會無限重試同一份
  （requested_at 沒變就不再跑，避免壞配方把 GPU/CPU 打滿）
- **不派 packaging 工作**（修修 2026-11-14 裁決）：Highlight shortlist 核准後 manifest 的
  `packaging.status=queued` 由當下對話中的 agent 照 podcast-pipeline S8F 派工圖執行
  title-brainstorm／thumbnail-brainstorm；本 watcher 以前會在背景另開一個 agent 跑，已移除。
  語意工作由當下執行的 agent 做，不在背景另開供應商（也避免兩邊同時寫 packages.json）

手動跑：
    python scripts/render_watcher.py --once      # 掃一輪就結束（測試用）
    python scripts/render_watcher.py             # 常駐
    python scripts/render_watcher.py --render-requests-only `
      --episode-slug 20260805-linzhichen --cut-id value-L01 --package-rank 1
        # 只 render 指定 Long package
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from shared.config import get_vault_path  # noqa: E402
from shared.render_timeout import looks_like_render_timeout  # noqa: E402

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


#: 這一支行程的身分。**不能用 pid**：Windows 會回收 pid，而事故裡那支 watcher 活了
#: 十四小時——重啟後拿到同一個 pid 就會繼承舊行程的連號，一上線就被當成「快重啟我」。
RUN_ID = uuid.uuid4().hex

#: 心跳保留多久。**這不是「進度條該不該動」的那個 120 秒**——那是顯示問題，判錯的
#: 成本是零；這裡授權的是**刪除**，判錯會弄丟一支還活著的 watcher 的連號。一支卡在
#: 600 秒 render 裡的 watcher 是活的、只是不新鮮，兩者分不出來，所以這個 TTL 取得
#: 遠比 render 時間長。它現在唯一的工作是不要讓 state 檔無限長大——讀取端的過期
#: 過濾在 Bridge 那邊已經做了。
WATCHER_ROW_TTL_SEC = 6 * 3600


def _within_ttl(row: object, now: str) -> bool:
    """這一筆心跳還在保留期內嗎？看不懂的一律當過期。"""
    if not isinstance(row, dict):
        return False
    try:
        seen = datetime.fromisoformat(str(row.get("seen_at")))
        current = datetime.fromisoformat(now)
    except (TypeError, ValueError):
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return 0 <= (current - seen).total_seconds() <= WATCHER_ROW_TTL_SEC


def heartbeat_key(episode_slug: str | None, cut_id: str | None, package_rank: int | None) -> str:
    return f"{episode_slug or '*'}/{cut_id or '*'}/r{package_rank if package_rank else '*'}"


def record_heartbeat(
    state: dict, *, episode_slug: str | None, cut_id: str | None, package_rank: int | None, now: str
) -> dict:
    """把這支 watcher 的守備範圍寫進 state。"""
    scope = {
        "episode_slug": episode_slug,
        "cut_id": cut_id,
        "package_rank": package_rank,
    }
    key = heartbeat_key(episode_slug, cut_id, package_rank)
    # 死掉的 watcher 的 row 永遠沒人刪，而 Bridge 讀的是所有 row 的最大值——不清掉的
    # 話，一支卡死的 watcher 留下的連號會永遠掛在畫面上，重啟一百次也清不掉（重啟
    # 換 scope 還會換 key，舊 row 原地不動）。心跳這一圈順手把過期的掃掉。
    watchers = {
        existing_key: row
        for existing_key, row in (state.get(WATCHER_HEARTBEAT_KEY) or {}).items()
        if existing_key == key or _within_ttl(row, now)
    }
    previous = watchers.get(key) or {}
    same_process = previous.get("run_id") == RUN_ID
    watchers[key] = {
        **scope,
        "seen_at": now,
        "pid": os.getpid(),
        "run_id": RUN_ID,
        # 連續逾時是**這支行程**健康與否的指標，不是某一份配方的問題——所以記在
        # 心跳上，不是記在工作上。見 `record_render_outcome`。
        "consecutive_timeouts": int(previous.get("consecutive_timeouts") or 0)
        if same_process
        else 0,
        "last_timeout_at": previous.get("last_timeout_at") if same_process else None,
    }
    state[WATCHER_HEARTBEAT_KEY] = watchers
    return state


def record_render_outcome(state: dict, *, timed_out: bool, now: str) -> dict:
    """把這一輪 render 是否逾時累計到心跳上。

    2026-09-17 蘇予昕 punch-L02：同一支 cut 連續五次撐到 600 秒逾時，而同一條命令
    在前景跑 11 秒就出圖。卡的不是配方也不是素材，是那支活了十四個小時的 watcher
    行程本身——重啟之後 13 秒就過。

    當時 gate 上顯示的是「第一次跑這個版式要先把算圖環境準備好⋯再按一次就會過」。

    `last_timeout_at` 目前**只給人看 state 檔用，沒有任何程式讀它**——留著當鑑識
    欄位，不要當成有語意的契約去依賴。
    修修按了五次都沒過。一次逾時確實可能是冷啟動，**連續逾時不是**——那句話要換掉，
    而畫面要換掉它就得先知道連續了幾次。
    """
    watchers = dict(state.get(WATCHER_HEARTBEAT_KEY) or {})
    changed = {}
    for key, row in watchers.items():
        if not isinstance(row, dict) or row.get("run_id") != RUN_ID:
            changed[key] = row
            continue
        count = int(row.get("consecutive_timeouts") or 0)
        changed[key] = {
            **row,
            # 心跳寫在迴圈開頭，接著 render 可以卡滿 600 秒——等這裡把結果寫回去時，
            # 那筆 `seen_at` 已經是十分鐘前的，Bridge 的過期過濾會把**正在出事的這支
            # watcher 自己**濾掉，連號讀出來是 0，畫面照舊說「再按一次就會過」。
            # 剛寫完一輪結果，本來就是這支行程最近一次證明自己還活著的時刻。
            "seen_at": now,
            "consecutive_timeouts": count + 1 if timed_out else 0,
            "last_timeout_at": now if timed_out else None,
        }
    state[WATCHER_HEARTBEAT_KEY] = changed
    return state


def _timeout_streak(state: dict) -> int:
    """這支行程目前連續逾時幾次。"""
    watchers = state.get(WATCHER_HEARTBEAT_KEY)
    if not isinstance(watchers, dict):
        return 0
    return max(
        (
            int(row.get("consecutive_timeouts") or 0)
            for row in watchers.values()
            if isinstance(row, dict) and row.get("run_id") == RUN_ID
        ),
        default=0,
    )


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
        cuts_with_recipe: set[str] = set()
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
                    cuts_with_recipe.add(cut["cut_id"])
                    if req.get("rendered_png"):
                        # 配方已經有成品：gate 每次存配方都重建、不帶 rendered_png，
                        # 只有出過圖的那一方（render_request／attach）才會填。以前只看
                        # requested_at，於是 attach 一寫配方就被當成新配方——重解幾何、
                        # 蓋掉 attach 剛交付的 PNG（2026-10-09 洪瀞集 full 三張）。
                        # 手改配方要重出圖：把 rendered_png 清成 null 再更新 requested_at。
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
        out.extend(package_jobs)

        # Transitional fallback: cuts written before package.render_recipe.
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
            if entry["cut_id"] in cuts_with_recipe:
                # 這支 cut 已經走 `package.render_recipe`，不管這一輪有沒有待辦
                # （做完了、做失敗了都算沒有待辦）。舊路徑不該再接手同一支：
                #
                # 1. 它用的是另一個 key（`<slug>/<cut>`，少了 `/r<n>`），所以每按一次
                #    〈存配方〉，同一支 cut 會被 render **兩次**——package 路徑一次、
                #    舊路徑一次。log 上的簽名是「兩行 RENDER 相隔 5 秒」（一個 watcher
                #    interval）：2026-09-17 蘇予昕 punch-L02 的 00:37:33 與 00:47:38 就是
                #    一對，各跑滿十分鐘才失敗。同一支 cut 四天前的 09-13 09:53:05 與
                #    10:03:10 也是一對，一樣各十分鐘——這個 bug 不是只燒過一次。
                # 2. 更糟的是舊路徑不帶 `--package-rank`，於是 `render_request.py`
                #    走 `req = entry["render_request"]`，再由 `_write_selected_package`
                #    把那份**舊的 approval 信封整份寫回 package 的 render_recipe**
                #    ——蓋掉 package 路徑剛解算出來的 geometry 與 rendered_png。
                #
                # 判斷要 per-cut，不能 per-episode：同一集裡混著有 recipe 和沒 recipe
                # 的 cut 是 `attach_packages.py` 現行行為就會產生的形狀（它整支
                # `cut["packages"] = packages` 換掉，而 spec 缺 `render_spec` 時
                # `_recipe_from_render_spec` 回 None）。20260805-linzhichen 的
                # value-L02 現在就是三個 recipe 全 null、approval 還在。
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


def _warn_on_timeout_streak(state: dict, log_path: Path | None) -> None:
    """連續第二次起就喊一聲——一次可能是冷啟動，連續不是。"""
    streak = _timeout_streak(state)
    if streak >= 2:
        _log(
            f"⚠ 連續第 {streak} 次 render 逾時。同一條命令在前景跑得過的話，"
            "卡住的是這支 watcher 行程本身——重啟它（2026-09-17 蘇予昕）",
            log_path,
        )


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
    # key 要印出來。兩條派工路徑在 log 上本來長得一模一樣（都是 `slug/cut_id`），
    # 分不出哪一行是 package 路徑、哪一行是舊路徑——2026-09-17 診斷這個 bug 時，
    # 第一版的結論就因此把因果寫錯了。
    _log(
        f"RENDER {job['key']} 大字={job['req'].get('big_text')} → {packaging_dir}",
        log_path,
    )
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
            + (["--package-rank", str(job["package_rank"])] if job.get("package_rank") else []),
            cwd=str(_REPO),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
    except Exception as exc:
        now = datetime.now(timezone.utc).isoformat()
        state[job["key"]] = {
            "requested_at": job["req"]["requested_at"],
            "status": "failed",
            "started_at": started_at,
            "rendered_at": now,
            "ok": False,
            "last_error": str(exc)[-500:],
        }
        record_render_outcome(
            state,
            timed_out=isinstance(exc, subprocess.TimeoutExpired)
            or looks_like_render_timeout(str(exc)),
            now=now,
        )
        save_state(state_path, state)
        _log(f"FAIL {job['key']}: {exc}", log_path)
        _warn_on_timeout_streak(state, log_path)
        return False
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()
    for line in tail[-6:]:
        _log(f"  {line}", log_path)
    ok = proc.returncode == 0
    now = datetime.now(timezone.utc).isoformat()
    state[job["key"]] = {
        "requested_at": job["req"]["requested_at"],
        "status": "done" if ok else "failed",
        "started_at": started_at,
        "rendered_at": now,
        "ok": ok,
        "last_error": None if ok else (proc.stderr or "")[-500:],
    }
    # 600 秒那個逾時**不是 watcher 這一層丟的**：`render_request.py` 自己用
    # `timeout=600` 跑 `render_still.py`，而且沒有 try/except 包住它，所以 child 是帶著
    # traceback 以 exit code 1 結束、`subprocess.run` 正常回傳。只認 `TimeoutExpired`
    # 的話，2026-09-17 蘇予昕 punch-L02 連五次逾時一次都不會被算到。
    timed_out = not ok and looks_like_render_timeout(proc.stderr)
    record_render_outcome(state, timed_out=timed_out, now=now)
    save_state(state_path, state)
    _log(f"{'DONE' if ok else 'FAIL'} {job['key']}", log_path)
    if timed_out:
        _warn_on_timeout_streak(state, log_path)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=float, default=5.0, help="掃描間隔秒數")
    ap.add_argument("--once", action="store_true", help="掃一輪就結束（測試用）")
    ap.add_argument(
        "--render-requests-only",
        action="store_true",
        help="相容舊啟動參數；watcher 現在只處理存配方 render request（不再派 packaging）",
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
        render_jobs = filter_render_requests(
            pending_requests(vault, state),
            episode_slug=args.episode_slug,
            cut_id=args.cut_id,
            package_rank=args.package_rank,
        )
        for job in render_jobs:
            render_one(job, state, args.state, args.log)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
