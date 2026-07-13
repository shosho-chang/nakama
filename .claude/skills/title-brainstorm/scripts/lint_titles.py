#!/usr/bin/env python3
"""lint_titles.py — 交付前檢查候選標題是否守約束。

用法:
    python lint_titles.py titles.txt     # 一行一條
    python lint_titles.py -              # 從 stdin 讀（一行一條）

規則:
    FAIL (exit 1) — 長度 > 80 字、含 emoji。
    WARN          — 疑似含簡體字（人工複核；避免誤判不硬擋）。
乾淨則印 "OK" 並 exit 0。
"""

from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

MAX = 80
_SIMPLIFIED = set("这们么关无与长门问对样电话说读点动师应务图书户从业东丝乐")
_EMOJI_RANGES = [
    (0x1F300, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
    (0x1F000, 0x1F0FF),
    (0xFE00, 0xFE0F),
    (0x2190, 0x21FF),
]


def _has_emoji(s: str) -> list:
    return [ch for ch in s if any(lo <= ord(ch) <= hi for lo, hi in _EMOJI_RANGES)]


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else "-"
    raw = sys.stdin.read() if arg == "-" else open(arg, encoding="utf-8").read()
    titles = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not titles:
        sys.stderr.write("lint_titles: 沒有讀到任何標題\n")
        return 2
    failed = False
    for i, t in enumerate(titles, 1):
        problems = []
        if len(t) > MAX:
            problems.append(f"長度 {len(t)} > {MAX}")
        emo = _has_emoji(t)
        if emo:
            problems.append("含 emoji: " + " ".join(emo))
        simp = sorted(set(t) & _SIMPLIFIED)
        if problems:
            failed = True
            print(f"FAIL  第{i}行  {t}")
            for p in problems:
                print(f"       └ {p}")
        elif simp:
            print(f"WARN  第{i}行  疑似簡體 {simp}: {t}")
        else:
            print(f"OK    第{i}行  ({len(t)}字) {t}")
    if failed:
        print("\n✗ 有 FAIL，修掉再交付。")
        return 1
    print(f"\n✓ OK: {len(titles)} 條全部通過長度/emoji 檢查。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
