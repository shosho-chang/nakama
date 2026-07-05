"""score_ledger.py — 多輪／多版本盲評的分數帳本：軌跡表、drop-off 彙整、argmax + pairwise 選版。

用法：
    python score_ledger.py <ledger.json> [--plateau-delta 2]

ledger.json 格式（主 agent 從 persona 輸出彙整）：
{
  "artifact": "財富階梯 deck",
  "rounds": [
    {"round": 1, "version": "v1",
     "scores": [
       {"persona": "小資Kevin", "total": 42,
        "units": {"card-04": 3, "card-08": 2},          // 選填：per-unit 分
        "dropoff": ["card-04", "card-08"]}              // 選填：想滑走的 unit
     ]}
  ],
  "pairwise": [                                          // 選填：盲 pairwise 判定
    {"persona": "小資Kevin", "a": "v1", "b": "v2", "winner": "v2"}   // winner 可為 "tie"
  ]
}

輸出 markdown（貼進 iteration report §1–2）。「兩版無明顯差異」→ 如實回報 plateau，不硬選。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--plateau-delta", type=float, default=2.0,
                        help="版本平均分差 ≤ 此值視為無明顯差異（plateau）")
    args = parser.parse_args()

    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    rounds = ledger.get("rounds", [])
    if not rounds:
        print("ledger 沒有 rounds，無事可做")
        return 1

    personas = sorted({s["persona"] for r in rounds for s in r["scores"]})

    print(f"# score_ledger｜{ledger.get('artifact', '(未命名 artifact)')}")

    # 1. 分數軌跡表（persona × round）
    print("\n## 分數軌跡")
    header = "| persona | " + " | ".join(f"R{r['round']} ({r.get('version', '?')})" for r in rounds) + " |"
    print(header)
    print("|" + "---|" * (len(rounds) + 1))
    for persona in personas:
        cells = []
        for r in rounds:
            score = next((s["total"] for s in r["scores"] if s["persona"] == persona), None)
            cells.append(str(score) if score is not None else "—")
        print(f"| {persona} | " + " | ".join(cells) + " |")

    # 2. 版本平均分（argmax）
    version_scores: dict[str, list[float]] = defaultdict(list)
    for r in rounds:
        version_scores[r.get("version", f"R{r['round']}")].extend(s["total"] for s in r["scores"])
    means = {v: sum(xs) / len(xs) for v, xs in version_scores.items() if xs}
    print("\n## 版本平均分（argmax）")
    for version, mean in sorted(means.items(), key=lambda kv: -kv[1]):
        print(f"- {version}: {mean:.1f}")
    ranked = sorted(means.items(), key=lambda kv: -kv[1])
    argmax_winner = ranked[0][0]
    plateau = len(ranked) >= 2 and (ranked[0][1] - ranked[1][1]) <= args.plateau_delta

    # 3. drop-off 熱點
    dropoff = Counter()
    for r in rounds:
        for s in r["scores"]:
            dropoff.update(s.get("dropoff", []))
    if dropoff:
        print("\n## drop-off 熱點（幾位 persona 在此想滑走）")
        for unit, n in dropoff.most_common():
            print(f"- {unit}: {n}")

    # 4. pairwise 交叉檢查
    pairwise = ledger.get("pairwise", [])
    pairwise_winner = None
    if pairwise:
        tally = Counter(p["winner"] for p in pairwise)
        print("\n## blind pairwise 票數")
        for version, n in tally.most_common():
            print(f"- {version}: {n}")
        non_tie = [(v, n) for v, n in tally.most_common() if v != "tie"]
        if non_tie:
            pairwise_winner = non_tie[0][0]

    # 5. 結論
    print("\n## 結論")
    if plateau:
        print(f"- **plateau**：前兩名平均分差 ≤ {args.plateau_delta:g}，兩版無明顯差異——如實回報，不硬選。")
    else:
        print(f"- argmax 勝者：**{argmax_winner}**")
    if pairwise_winner:
        agree = "一致" if pairwise_winner == argmax_winner else "**不一致，交人裁決**"
        print(f"- pairwise 勝者：**{pairwise_winner}**（與 argmax {agree}）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
