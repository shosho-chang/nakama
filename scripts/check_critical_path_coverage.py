"""Phase 6 Slice 1 — critical-path coverage gate.

讀 `coverage.json`，對 critical-path 模組逐一驗 ≥ threshold；缺一個就 exit 1，CI 擋。

設計哲學是「不退步 gate」：threshold 用該模組 baseline 取最近 5% round-down，
而非 aspirational 數字。新增 critical-path 模組或調 threshold 看
`docs/runbooks/test-coverage.md`。

用法：
    pytest --cov=shared --cov=thousand_sunny --cov=agents --cov-report=json:coverage.json
    python scripts/check_critical_path_coverage.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Baseline measured 2026-04-26 (Phase 6 Slice 1)
# Plan §Phase 6 A bar 承諾 critical-path ≥ 80%；本 dict round-down 至最近 5%/10% 鎖 baseline
THRESHOLDS: dict[str, float] = {
    "shared/approval_queue.py": 95.0,  # baseline 96.77%（FSM single source of truth）
    "shared/alerts.py": 100.0,  # baseline 100.00%（dedupe + dispatch）
    "shared/incident_archive.py": 90.0,  # baseline 93.13%（Phase 4 archive）
    "shared/heartbeat.py": 100.0,  # baseline 100.00%（per-cron heartbeat）
    "shared/kb_writer.py": 90.0,  # baseline 91.15%（KB 結構寫入 / aggregator）
    "shared/wordpress_client.py": 90.0,  # baseline 90.48%（WP REST + media）
    "thousand_sunny/routers/robin.py": 95.0,  # baseline 97.08%（Robin SSE + 處理頁）
    "thousand_sunny/routers/bridge.py": 80.0,  # baseline 77.56% → Slice 1 補到 ≥ 80%
}


def main() -> int:
    cov_path = Path("coverage.json")
    if not cov_path.exists():
        print(
            "ERROR: coverage.json not found. Run:\n"
            "  pytest --cov=shared --cov=thousand_sunny --cov=agents "
            "--cov-report=json:coverage.json",
            file=sys.stderr,
        )
        return 2

    data = json.loads(cov_path.read_text())
    files = data.get("files", {})

    print("Critical-path coverage gate")
    print("=" * 78)

    failed: list[tuple[str, float | None, float, str]] = []
    for module, threshold in THRESHOLDS.items():
        info = files.get(module)
        if info is None:
            failed.append((module, None, threshold, "missing from coverage report"))
            print(f"  ?  {module:<50s} (not in coverage.json)")
            continue
        pct = info.get("summary", {}).get("percent_covered")
        if pct is None:
            failed.append((module, None, threshold, "no percent_covered"))
            continue
        ok = pct >= threshold
        status = "✓" if ok else "✗"
        print(f"  {status}  {module:<50s} {pct:>6.2f}% (≥ {threshold:.0f}%)")
        if not ok:
            failed.append((module, pct, threshold, "below threshold"))

    print("=" * 78)

    if failed:
        print(f"\nFAILED: {len(failed)} module(s) below critical-path threshold:")
        for module, pct, threshold, reason in failed:
            if pct is None:
                print(f"  - {module}: {reason} (≥ {threshold:.0f}% required)")
            else:
                print(f"  - {module}: {pct:.2f}% < {threshold:.0f}% ({reason})")
        return 1

    print(f"\nPASS: all {len(THRESHOLDS)} critical-path modules meet threshold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
