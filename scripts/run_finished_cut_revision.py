"""把一次 targeted revision 從頭推到 preview，機械的部分不用人重複做。

2026-09-17 修一句話的 b-roll，實際花掉的步驟是：`request-revision` → `advance` →
等 packet → 回答 → `advance` → 回答 → `advance` → 發現缺 Resolve 環境變數 → 補上
再 `advance`。中途被 handoff 逾時砍掉一次、被一個填錯的欄位砍掉一次，整條重開三遍。

**創意判斷不在這裡**：每一個 packet 仍然由當下的 agent 讀 `prompt.md`、自己寫
`response.json`。這支腳本只負責：

* Resolve 的三個環境變數（少一個就是 `Resolve scripting module is unavailable`）
* 反覆 `advance`，逾時就再來一次（packet 是持久的，補完答案重跑同一個 command）
* 每出現一個新 packet 就把路徑印出來，不用自己去翻 `semantic-handoff` 目錄
* 走到 `needs_review` 時把 `review_reason` 印出來——以前這裡只有三個字

用法：

    python scripts/run_finished_cut_revision.py \
        --episode-id "20260901 蘇予昕" --plan-ref plan-xxxx \
        --event-id punch-L02-dir-broll08 --feedback "這句畫面不對，要……"

    # 接續一個已經下好的 command（例如答到一半離開）
    python scripts/run_finished_cut_revision.py --episode-id "…" --command-id targeted-revision:xxxx

event_id 用 `run_finished_cut_production.py inspect-cuts` 查。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRODUCTION_CLI = REPO_ROOT / "scripts" / "run_finished_cut_production.py"

#: 少任何一個，物化那一站就會是 `Resolve scripting module is unavailable`，
#: 而訊息不會告訴你缺的是環境變數。預設值與 `build_resolve_project.py` 一致。
RESOLVE_ENV = {
    "RESOLVE_SCRIPT_API": (
        r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
    ),
    "RESOLVE_SCRIPT_LIB": r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll",
}
#: `DaVinciResolveScript` 住在這裡；不放進 PYTHONPATH 就 import 不到。
RESOLVE_MODULES = Path(RESOLVE_ENV["RESOLVE_SCRIPT_API"]) / "Modules"

TERMINAL_STATES = {"preview_ready", "review_ready", "registered"}


def _env() -> dict[str, str]:
    env = dict(os.environ)
    for key, value in RESOLVE_ENV.items():
        env.setdefault(key, value)
    existing = env.get("PYTHONPATH", "")
    modules = str(RESOLVE_MODULES)
    if modules not in existing.split(os.pathsep):
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (modules, existing)))
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _run(base: list[str], *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PRODUCTION_CLI), *base, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _last_status(output: str) -> dict | None:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith('{"command_id"'):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return None
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument("--episode-id", required=True)
    parser.add_argument(
        "--runtime-root", type=Path, default=Path("E:/nakama/data/finished-cut-runtime")
    )
    parser.add_argument("--episodes-root", type=Path, default=Path("G:/Footages"))
    parser.add_argument("--resolve-config", type=Path)
    parser.add_argument("--plan-ref")
    parser.add_argument("--event-id")
    parser.add_argument("--feedback")
    parser.add_argument("--command-id", help="接續一個已經下好的 targeted revision")
    parser.add_argument("--max-advances", type=int, default=12)
    args = parser.parse_args(argv)

    env = _env()
    handoff = args.runtime_root / "semantic-handoff"
    config = args.resolve_config or (
        args.runtime_root / "config" / f"resolve-{args.episode_id.split()[0]}.json"
    )
    base = [
        "--runtime-root",
        str(args.runtime_root),
        "--episodes-root",
        str(args.episodes_root),
        "--episode-id",
        args.episode_id,
        "--semantic-worker",
        "handoff",
    ]
    if config.is_file():
        base += ["--resolve-config", str(config)]
    else:
        print(f"[!] 找不到 {config}——物化那一站會停在 resolve_binding_not_configured")

    command_id = args.command_id
    if command_id is None:
        if not (args.plan_ref and args.event_id and args.feedback):
            parser.error("沒有 --command-id 時，--plan-ref／--event-id／--feedback 三個都要給")
        done = _run(base, "request-revision", args.plan_ref, args.event_id, args.feedback, env=env)
        if done.returncode != 0:
            print(done.stdout or "", done.stderr or "", file=sys.stderr)
            return 1
        command_id = json.loads(done.stdout.strip().splitlines()[-1])["command_id"]
    print(f"command: {command_id}", flush=True)

    known = {p.name for p in handoff.iterdir() if p.name.startswith("request-")}
    for step in range(1, args.max_advances + 1):
        child = subprocess.Popen(
            [sys.executable, str(PRODUCTION_CLI), *base, "advance", command_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            env=env,
        )
        while child.poll() is None:
            for path in handoff.iterdir():
                if path.name in known or not path.name.startswith("request-"):
                    continue
                if not (path / "packet.json").is_file():
                    continue
                known.add(path.name)
                stage = json.loads((path / "packet.json").read_text(encoding="utf-8"))["request"]
                print(f"\n>>> 要你回答：{stage['stage']} · {stage.get('event_id') or '整段'}")
                print(f"    packet : {path / 'packet.json'}")
                print(f"    寫回答到: {path / 'response.json'}\n", flush=True)
            time.sleep(1.0)
        status = _last_status(child.communicate()[0] or "")
        if status is None:
            print("[!] advance 沒有回傳狀態，停下來人工看", file=sys.stderr)
            return 1
        state, stage = status.get("state"), status.get("current_stage")
        print(f"[{step}] state={state} stage={stage}", flush=True)
        if status.get("review_reason"):
            print(f"    退件理由：{status['review_reason']}", flush=True)
        if status.get("reason_code"):
            print(f"    reason_code：{status['reason_code']}", flush=True)
        if state in TERMINAL_STATES:
            print(f"\n完成：{state}", flush=True)
            return 0
        if state == "needs_review":
            print("    停在 needs_review——看上面的理由，修好再重跑同一個 --command-id")
            return 1
    print("[!] advance 次數用完還沒到終點", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
