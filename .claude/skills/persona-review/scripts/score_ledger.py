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
                        help="版本平均分差 ≤ 此值視為無明顯差異（plateau）。"
                             "依 rubric 量表挑：100 分制約 2、0-10 制約 0.5")
    args = parser.parse_args()

    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    rounds = ledger.get("rounds", [])
    if not rounds:
        print("ledger 沒有 rounds，無事可做")
        return 1

    # 每輪的顯示標籤只算一次：version 缺席時 fallback 到 R{round}，round 也缺就用序號
    labels = []
    for i, r in enumerate(rounds):
        round_no = r.get("round", i + 1)
        labels.append(r.get("version") or f"R{round_no}")
        for s in r.get("scores", []):
            if "persona" not in s or "total" not in s:
                print(f"ledger 格式錯誤：rounds[{i}].scores 有項目缺 persona/total 欄位：{s}")
                return 1

    personas = sorted({s["persona"] for r in rounds for s in r["scores"]})

    print(f"# score_ledger｜{ledger.get('artifact', '(未命名 artifact)')}")

    # 1. 分數軌跡表（persona × round）
    print("\n## 分數軌跡")
    print("| persona | " + " | ".join(labels) + " |")
    print("|" + "---|" * (len(rounds) + 1))
    for persona in personas:
        cells = []
        for r in rounds:
            score = next((s["total"] for s in r["scores"] if s["persona"] == persona), None)
            cells.append(str(score) if score is not None else "—")
        print(f"| {persona} | " + " | ".join(cells) + " |")

    # 2. per-unit 平均分（有 units 欄位才印；抓「總分升但某卡退步」）
    unit_scores: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for label, r in zip(labels, rounds):
        for s in r["scores"]:
            for unit, val in s.get("units", {}).items():
                unit_scores[unit][label].append(val)
    if unit_scores:
        print("\n## per-unit 平均分（unit × 輪）")
        print("| unit | " + " | ".join(labels) + " |")
        print("|" + "---|" * (len(labels) + 1))
        for unit in sorted(unit_scores):
            cells = []
            for label in labels:
                vals = unit_scores[unit].get(label)
                cells.append(f"{sum(vals) / len(vals):.1f}" if vals else "—")
            print(f"| {unit} | " + " | ".join(cells) + " |")

    # 3. 版本平均分（argmax）
    version_scores: dict[str, list[float]] = defaultdict(list)
    for label, r in zip(labels, rounds):
        version_scores[label].extend(s["total"] for s in r["scores"])
    means = {v: sum(xs) / len(xs) for v, xs in version_scores.items() if xs}
    if not means:
        print("\nledger 所有 rounds 的 scores 都是空的，無法選版")
        return 1
    ranked = sorted(means.items(), key=lambda kv: -kv[1])
    print("\n## 版本平均分（argmax）")
    for version, mean in ranked:
        print(f"- {version}: {mean:.1f}")
    argmax_winner = ranked[0][0]
    plateau = len(ranked) >= 2 and (ranked[0][1] - ranked[1][1]) <= args.plateau_delta

    # 4. drop-off 熱點
    dropoff = Counter()
    for r in rounds:
        for s in r["scores"]:
            dropoff.update(s.get("dropoff", []))
    if dropoff:
        print("\n## drop-off 熱點（幾位 persona 在此想滑走）")
        for unit, n in dropoff.most_common():
            print(f"- {unit}: {n}")

    # 5. pairwise 交叉檢查
    pairwise = ledger.get("pairwise", [])
    pairwise_winner = None
    pairwise_tied = False
    if pairwise:
        tally = Counter(p["winner"] for p in pairwise)
        print("\n## blind pairwise 票數")
        for version, n in tally.most_common():
            print(f"- {version}: {n}")
        non_tie = [(v, n) for v, n in tally.most_common() if v != "tie"]
        if non_tie:
            # 最高票同票 = 平手，不能靠 Counter 插入序硬選
            pairwise_tied = len(non_tie) >= 2 and non_tie[0][1] == non_tie[1][1]
            if not pairwise_tied:
                pairwise_winner = non_tie[0][0]

    # 6. 結論
    print("\n## 結論")
    if plateau:
        print(f"- **plateau**：前兩名平均分差 ≤ {args.plateau_delta:g}，"
              "兩版無明顯差異——如實回報，不硬選。")
    else:
        print(f"- argmax 勝者：**{argmax_winner}**")
    if pairwise_tied:
        print("- pairwise **平手**——不硬選，交人裁決。")
    elif pairwise_winner:
        agree = "一致" if pairwise_winner == argmax_winner else "**不一致，交人裁決**"
        print(f"- pairwise 勝者：**{pairwise_winner}**（與 argmax {agree}）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
