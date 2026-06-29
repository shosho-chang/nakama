#!/usr/bin/env python3
"""理性骨架 lint — 守住 draft-article 的草稿「保持理性、別讓聲音跑進來」。

draft-article 只出理性骨架（書的論點 ＋ 使用者的論據，低修飾）；情緒、口語、
起承轉合是使用者後面自己加的。所以這支 lint 的職責跟「成品聲音檢查」相反：
它防的是 **ornament creep** —— 台味句尾助詞、emoji、過多驚嘆號滲進草稿，代表
compose 又退回去模仿聲音了，違反「骨架交給人去加肉」的分工。

覆蓋度與不虛構是語意問題，無法用字串確定性檢查，列成 checklist 由 Claude 交稿前自審。

純 stdlib、UTF-8。退出碼 0 = 無硬傷；1 = 有 ornament 硬傷（要清掉再交）。

用法：
    python lint_draft.py <draft.md>
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 聲音 ornament 偵測：emoji 與台味句尾助詞在理性草稿應趨近 0
_EMOJI = re.compile(r"[\U0001F1E6-\U0001FAFF☀-⛿✀-➿⬀-⯿️←-⇿]")
_PARTICLES = "啦喔耶嘛囉吧吼啊"


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


def lint(draft_path: Path) -> int:
    body = _strip_frontmatter(draft_path.read_text(encoding="utf-8"))
    total = len(re.sub(r"\s", "", body))
    cjk = len(re.findall(r"[一-鿿]", body))
    h2 = len(re.findall(r"(?m)^##\s", body))
    particles = sum(body.count(p) for p in _PARTICLES)
    emoji = len(_EMOJI.findall(body))
    bangs = body.count("！") + body.count("!")

    checks: list[tuple[str, str, str]] = []

    def add(name: str, ok: bool, warn: bool, detail: str) -> None:
        checks.append((name, "PASS" if ok else ("WARN" if warn else "FAIL"), detail))

    # 硬傷：emoji 與大量台味助詞 = 聲音滲入理性草稿
    add("emoji", emoji == 0, False, f"{emoji} 個（理性骨架應為 0；emoji 是成品 CTA 才有的）")
    add(
        "台味句尾助詞",
        particles <= 4,
        particles <= 8,
        f"{particles} 次（{_PARTICLES}）· 理性應趨近 0，>8 = 聲音滲入",
    )
    add("驚嘆號", bangs <= 3, bangs <= 8, f"{bangs} 個（！/!）· 理性陳述少用驚嘆")
    add("篇幅", total >= 1500, total >= 800, f"{total} 字（CJK {cjk}）· 太短可能覆蓋不足")
    checks.append(("H2 區塊", "INFO", f"{h2}"))

    width = max(len(n) for n, _, _ in checks)
    print(f"\n理性骨架 lint · {draft_path.name}")
    print("─" * 60)
    fails = 0
    for name, status, detail in checks:
        mark = {"PASS": "✓", "WARN": "▲", "FAIL": "✗", "INFO": "·"}[status]
        if status == "FAIL":
            fails += 1
        print(f"  {mark} {name.ljust(width)}  {status:4}  {detail}")
    print("─" * 60)
    print("  語意自審（Claude 交稿前逐項確認，lint 無法代驗）：")
    print("    □ 書的每個主要論點都在")
    print("    □ 使用者每條 note:: 論據都 surface 了，沒漏")
    print("    □ 沒有虛構任何數字 / 經歷（每個個人立場都對得到某條 note::）")
    print("    □ 沒有情緒鋪陳或起承轉合銜接語（那是使用者的工作）")
    if fails:
        print(f"\n  {fails} 個 ornament 硬傷 → 清掉聲音、回到理性，再交。\n")
        return 1
    print("\n  無 ornament 硬傷。WARN 項由你判斷。\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="rational-scaffold lint for draft-article")
    ap.add_argument("draft", type=Path)
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows cp1252 console 安全
    if not args.draft.exists():
        print(f"draft not found: {args.draft}", file=sys.stderr)
        return 2
    return lint(args.draft)


if __name__ == "__main__":
    raise SystemExit(main())
