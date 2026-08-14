#!/usr/bin/env python3
"""render_watcher.py — 盯 gate 存的封面配方，自動 render 一次（修修 2026-08-14 裁決）。

修修：「我按下存配方了，所以你不會自動 render 嗎？」——不會，因為 render 需要
Chrome／hyperframes／LINE Seed 字型，那些只在桌機（ADR-054 D11：VPS 叫不到桌機）。
本 watcher 就是桌機端那隻手：跟 Thousand Sunny 一起開機啟動，看到新配方就出圖。

行為：
- 每 `--interval` 秒掃一次 vault `Attachments/packaging/*/approval.json`
- 某支 cut 的 `render_request.requested_at` 比上次處理過的新 → 跑
  `.claude/skills/thumbnail-brainstorm/scripts/render_request.py`（含幾何 solver、
  遮蔽收斂、face_measure 交付 gate、回填 rendered_png 與 packages.json）
- **同一份配方只出一次**：狀態記在 `logs/render-watcher-state.json`（key =
  slug/cut_id，value = 已處理的 requested_at）。連按五次「存配方」也只 render 一次
- working-set packaging 目錄靠掃 `G:/Footages/*/packaging/packages.json` 的
  `episode` 欄位對回來（vault 端沒有這個路徑，也不該有——D10 硬規則①）
- 失敗不靜默：寫 log、記進 state 的 `last_error`，下一輪不會無限重試同一份
  （requested_at 沒變就不再跑，避免壞配方把 GPU/CPU 打滿）

手動跑：
    python scripts/render_watcher.py --once      # 掃一輪就結束（測試用）
    python scripts/render_watcher.py             # 常駐
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
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


def find_packaging_dir(episode_slug: str) -> Path | None:
    """slug → working-set packaging 目錄（掃 footage 根目錄比對 packages.json 的 episode）。"""
    for root in FOOTAGE_ROOTS:
        if not root.is_dir():
            continue
        for pkg in root.glob("*/packaging/packages.json"):
            try:
                if json.loads(pkg.read_text(encoding="utf-8")).get("episode") == episode_slug:
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
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pending_requests(vault: Path, state: dict) -> list[dict]:
    """回傳需要 render 的配方（requested_at 比 state 記錄的新）。"""
    out: list[dict] = []
    root = vault / "Attachments" / "packaging"
    if not root.is_dir():
        return out
    for approval_path in sorted(root.glob("*/approval.json")):
        slug = approval_path.parent.name
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
            out.append({"slug": slug, "cut_id": entry["cut_id"], "req": req, "key": key})
    return out


def render_one(job: dict, state: dict, state_path: Path, log_path: Path | None) -> bool:
    slug, cut_id = job["slug"], job["cut_id"]
    packaging_dir = find_packaging_dir(slug)
    if packaging_dir is None:
        _log(
            f"SKIP {slug}/{cut_id}：找不到 working-set packaging 目錄"
            "（G:/Footages/*/packaging）",
            log_path,
        )
        state[job["key"]] = {
            "requested_at": job["req"]["requested_at"],
            "last_error": "packaging dir not found",
        }
        save_state(state_path, state)
        return False

    _log(f"RENDER {slug}/{cut_id} 大字={job['req'].get('big_text')} → {packaging_dir}", log_path)
    proc = subprocess.run(
        [
            sys.executable, str(RENDER_REQUEST),
            "--episode-slug", slug,
            "--packaging-dir", str(packaging_dir),
            "--cut-id", cut_id,
        ],
        cwd=str(_REPO), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=1800,
    )
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()
    for line in tail[-6:]:
        _log(f"  {line}", log_path)
    ok = proc.returncode == 0
    state[job["key"]] = {
        "requested_at": job["req"]["requested_at"],
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
    ap.add_argument("--log", type=Path, default=_REPO / "logs" / "render-watcher.log")
    ap.add_argument(
        "--state", type=Path, default=_REPO / "logs" / "render-watcher-state.json",
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
        for job in pending_requests(vault, state):
            render_one(job, state, args.state, args.log)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
