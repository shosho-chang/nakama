#!/usr/bin/env python3
"""VPS baseline 壓測監控 — 每 60 秒採樣 CPU/RAM/swap/disk/load，跑 24h 自動停。

ADR-007 §6 的硬前置：接受標準 CPU p95 < 60% / RAM 使用 < 3GB / Available ≥ 500MB。

用法（VPS 上）：
    nohup python3 /home/nakama/scripts/vps_baseline_monitor.py \
        > /home/nakama/data/vps_baseline.log 2>&1 &
    echo $! > /home/nakama/data/vps_baseline.pid

停止：
    kill $(cat /home/nakama/data/vps_baseline.pid)

分析（24h 後）：
    python3 /home/nakama/scripts/vps_baseline_monitor.py --analyze
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

OUT = Path("/home/nakama/data/vps_baseline.jsonl")
INTERVAL_S = 60
DURATION_S = 24 * 60 * 60

_running = True


def _stop(signum, frame):
    global _running
    _running = False


def collect() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    start = time.time()
    with OUT.open("a") as f:
        while _running and (time.time() - start) < DURATION_S:
            ts = datetime.now(timezone.utc).isoformat()
            vm = psutil.virtual_memory()
            sw = psutil.swap_memory()
            disk = psutil.disk_usage("/")
            cpu = psutil.cpu_percent(interval=1)
            la = psutil.getloadavg()
            row = {
                "ts": ts,
                "cpu_percent": cpu,
                "load_1": la[0],
                "load_5": la[1],
                "load_15": la[2],
                "ram_total_mb": vm.total // 1024 // 1024,
                "ram_used_mb": vm.used // 1024 // 1024,
                "ram_available_mb": vm.available // 1024 // 1024,
                "ram_percent": vm.percent,
                "swap_used_mb": sw.used // 1024 // 1024,
                "swap_percent": sw.percent,
                "disk_used_gb": disk.used // 1024 // 1024 // 1024,
                "disk_percent": disk.percent,
            }
            f.write(json.dumps(row) + "\n")
            f.flush()
            time.sleep(max(0, INTERVAL_S - 1))


def analyze() -> None:
    if not OUT.exists():
        print(f"no data at {OUT}", file=sys.stderr)
        sys.exit(1)

    rows = [json.loads(line) for line in OUT.read_text().splitlines() if line.strip()]
    if not rows:
        print("empty data", file=sys.stderr)
        sys.exit(1)

    def p(name: str, pct: float) -> float:
        vals = sorted(r[name] for r in rows)
        k = int(len(vals) * pct / 100)
        return vals[min(k, len(vals) - 1)]

    def peak(name: str) -> float:
        return max(r[name] for r in rows)

    def lo(name: str) -> float:
        return min(r[name] for r in rows)

    span_h = (
        datetime.fromisoformat(rows[-1]["ts"]) - datetime.fromisoformat(rows[0]["ts"])
    ).total_seconds() / 3600

    print("# VPS Baseline Report")
    print(f"samples: {len(rows)}  span: {span_h:.1f}h  interval: {INTERVAL_S}s")
    print()
    print("## CPU")
    print(f"  p50:  {p('cpu_percent', 50):.1f}%")
    print(f"  p95:  {p('cpu_percent', 95):.1f}%  (ADR-007 threshold: < 60%)")
    print(f"  peak: {peak('cpu_percent'):.1f}%")
    print()
    print("## RAM")
    print(f"  used p50:  {p('ram_used_mb', 50):.0f} MB")
    print(f"  used p95:  {p('ram_used_mb', 95):.0f} MB  (ADR-007 threshold: < 3072)")
    print(f"  used peak: {peak('ram_used_mb'):.0f} MB")
    print(f"  avail min: {lo('ram_available_mb'):.0f} MB  (ADR-007 threshold: >= 500)")
    print()
    print("## Swap")
    print(f"  used p95:  {p('swap_used_mb', 95):.0f} MB")
    print(f"  used peak: {peak('swap_used_mb'):.0f} MB")
    print()
    print("## Load")
    print(f"  1m  p95: {p('load_1', 95):.2f}")
    print(f"  5m  p95: {p('load_5', 95):.2f}")
    print(f"  15m p95: {p('load_15', 95):.2f}")
    print()
    print("## Disk")
    print(f"  used peak: {peak('disk_used_gb'):.0f} GB ({peak('disk_percent'):.0f}%)")
    print()

    verdict = []
    if p("cpu_percent", 95) >= 60:
        verdict.append("FAIL: CPU p95 >= 60%")
    if p("ram_used_mb", 95) >= 3072:
        verdict.append("FAIL: RAM p95 >= 3GB")
    if lo("ram_available_mb") < 500:
        verdict.append("FAIL: available RAM dipped below 500MB")
    if verdict:
        print("## Verdict: RED")
        for v in verdict:
            print(f"  - {v}")
    else:
        print("## Verdict: GREEN (all ADR-007 §6 thresholds met)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--analyze", action="store_true", help="print report from existing jsonl")
    args = ap.parse_args()
    if args.analyze:
        analyze()
    else:
        collect()
