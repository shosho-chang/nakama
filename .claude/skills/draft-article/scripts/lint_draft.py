#!/usr/bin/env python3
"""§10 self-lint — 交稿前檢查一篇草稿「像不像修修本人」。

把風格側寫 §10 的硬規格固化成確定性檢查，讓 draft-article 在交稿前自動 gate：
正文撒 emoji、台味助詞太少、字數越界、H2 過多 —— 任一硬傷就擋下、要求重寫。
這是這個 skill 的差異化價值：不是「會寫」，是「會自我檢查像不像你」。

純 stdlib，UTF-8 安全（不依賴 PyYAML / repo import；word_count 邊界用 regex 撈 yaml）。

用法：
    python lint_draft.py <draft.md> [--category book-review] [--repo-root .]

退出碼：0 = 沒有 FAIL（可交稿）；1 = 至少一個 FAIL（要重寫）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 正文不可、CTA 可偶見的 emoji（涵蓋 👉🥰🤍🔥💯✨ 等常見區段）
_EMOJI = re.compile(r"[\U0001F1E6-\U0001FAFF☀-⛿✀-➿⬀-⯿️←-⇿]")
# 台味句尾助詞（profile §10 的 8 個）
_PARTICLES = "啦喔耶嘛囉吧吼啊"
# CTA / 收尾區起點標記（emoji 只允許出現在這之後）
_CTA_MARKERS = ("購書連結", "電子報", "shosho.tw/free", "博客來", "訂閱我的", "shosho.cc")
# 招牌斷然短收束句（節奏頓點）
_SHORT_CLOSERS = re.compile(r"(講完。|沒了。|就這麼簡單。|屢試不爽。|故事講完了。)")

_DEFAULTS = {"min": 5000, "max": 12500, "forbid_emoji": False}


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


def _load_bounds(category: str | None, repo_root: Path) -> dict:
    """撈 <category>.yaml 的 word_count / forbid_emoji 邊界（regex，免 PyYAML）。"""
    if not category:
        return dict(_DEFAULTS)
    yaml_path = repo_root / "config" / "style-profiles" / f"{category}.yaml"
    if not yaml_path.exists():
        print(f"  ⚠ 找不到 {yaml_path}，用預設邊界", file=sys.stderr)
        return dict(_DEFAULTS)
    raw = yaml_path.read_text(encoding="utf-8")
    out = dict(_DEFAULTS)
    if m := re.search(r"(?m)^\s*min:\s*(\d+)", raw):
        out["min"] = int(m.group(1))
    if m := re.search(r"(?m)^\s*max:\s*(\d+)", raw):
        out["max"] = int(m.group(1))
    if m := re.search(r"(?m)^\s*forbid_emoji:\s*(true|false)", raw):
        out["forbid_emoji"] = m.group(1) == "true"
    return out


def lint(draft_path: Path, category: str | None, repo_root: Path) -> int:
    text = draft_path.read_text(encoding="utf-8")
    body = _strip_frontmatter(text)
    bounds = _load_bounds(category, repo_root)

    total = len(re.sub(r"\s", "", body))
    cjk = len(re.findall(r"[一-鿿]", body))
    h2 = len(re.findall(r"(?m)^##\s", body))
    particles = sum(body.count(p) for p in _PARTICLES)
    asides = body.count("（")  # 含數字括號雜訊，僅作下限參考
    short_closers = len(_SHORT_CLOSERS.findall(body))

    # emoji 位置：CTA 區之前出現 = 正文 emoji = 硬傷。
    # CTA 區從「含最早 CTA 標記那一行的行首」算起 —— 否則行首 emoji（👉 購書連結）
    # 會被切到正文側誤判。
    marker_idxs = [body.find(m) for m in _CTA_MARKERS if body.find(m) != -1]
    if marker_idxs:
        cta_start = body.rfind("\n", 0, min(marker_idxs)) + 1
    else:
        cta_start = len(body)
    emoji_all = [m.start() for m in _EMOJI.finditer(body)]
    body_emoji = [i for i in emoji_all if i < cta_start]
    cta_emoji = [i for i in emoji_all if i >= cta_start]

    checks: list[tuple[str, str, str]] = []

    def add(name: str, ok: bool, warn: bool, detail: str) -> None:
        status = "PASS" if ok else ("WARN" if warn else "FAIL")
        checks.append((name, status, detail))

    add(
        "字數",
        bounds["min"] <= total <= bounds["max"],
        False,
        f"{total}（CJK {cjk}） · 區間 {bounds['min']}–{bounds['max']}",
    )
    add(
        "H2 區塊數",
        4 <= h2 <= 8,
        1 <= h2 <= 3 or 9 <= h2 <= 10,
        f"{h2} · 最佳 4–8、絕不 >10",
    )
    # 修修明確要求 gate：正文 emoji / 助詞 <8 直接擋下重寫
    add(
        "正文 emoji",
        len(body_emoji) == 0,
        False,
        f"正文 {len(body_emoji)}（必須 0） · CTA 區 {len(cta_emoji)}（允許）",
    )
    add(
        "台味句尾助詞",
        8 <= particles <= 15,
        particles > 15,
        f"{particles} 次（{_PARTICLES}） · 門檻 8–15，<8 會飄成翻譯腔",
    )
    add("括號吐槽", asides >= 3, False, f"{asides} 個「（」（含數字括號，僅看下限 ≥3）")
    add("招牌短收束句", short_closers >= 1, True, f"{short_closers} 句（講完。/沒了。…，建議 ≥1）")

    width = max(len(n) for n, _, _ in checks)
    print(f"\n§10 self-lint · {draft_path.name}" + (f" · {category}" if category else ""))
    print("─" * 56)
    fails = 0
    for name, status, detail in checks:
        mark = {"PASS": "✓", "WARN": "▲", "FAIL": "✗"}[status]
        if status == "FAIL":
            fails += 1
        print(f"  {mark} {name.ljust(width)}  {status:4}  {detail}")
    print("─" * 56)
    if fails:
        print(f"  {fails} 個硬傷 → 重寫後再 lint。\n")
        return 1
    print("  無硬傷。WARN 項是聲音微調，由你判斷。\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="§10 self-lint for a draft article")
    ap.add_argument("draft", type=Path)
    ap.add_argument("--category", default=None, help="book-review / science / people")
    ap.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows cp1252 console 安全
    if not args.draft.exists():
        print(f"draft not found: {args.draft}", file=sys.stderr)
        return 2
    return lint(args.draft, args.category, args.repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
