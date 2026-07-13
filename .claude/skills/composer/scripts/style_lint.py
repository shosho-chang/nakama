#!/usr/bin/env python3
"""聲音初稿 lint — 守住 composer 草稿的「像修修、不犯修修大忌」機械層。

與 draft-article 的 lint_draft.py 互為鏡像：那支防「聲音滲入理性骨架」，
這支防兩頭——(a) 大忌滲入（禁用詞、emoji、簡體、殘渣、長段落），
(b) 聲音失衡（過平＝沒有修修味；過載＝助詞/驚嘆號灑滿地的拙劣模仿）。

規則的單一事實來源是 data/brook/style-profiles-fable5/（20-negative §1-2、30 §4a、02 劑量）；
本檔只把「機械可查」的子集寫死成檢查。側寫大改版時要同步這裡的清單。

語意層（個人錨、素材覆蓋、不虛構）無法字串檢查，印成 checklist 由 Claude 交稿前自審。

純 stdlib、UTF-8。退出碼 0 = 無硬傷；1 = 有硬傷（修到 0 再交）；2 = 用法錯誤。

用法：
    python style_lint.py <draft.md> --genre book-review|science|people
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_EMOJI = re.compile(r"[\U0001F1E6-\U0001FAFF☀-⛿✀-➿⬀-⯿️←-⇿]")
_PARTICLES = "喔啦嘛耶欸吼齁唷勒"

# fable5 20-negative §1：語料 32 篇零命中（或 df=1 視同禁用）的詞
_BANNED = [
    "綜上所述", "總而言之", "總的來說", "總結來說", "歸根結底", "由此可見",
    "不難發現", "不言而喻", "眾所周知", "值得一提", "值得注意的是", "毫無疑問",
    "在這個快節奏", "在當今", "深度剖析", "令人驚嘆", "令人震驚", "舉足輕重",
    "蛻變", "絕不僅僅", "親愛的讀者", "讓我們一起", "篇章",
    "乾貨", "揭秘", "懶人包", "溫馨提醒", "小確幸", "賦能", "抓手", "閉環", "痛點",
    "視頻", "網絡", "軟件", "質量", "信息", "智能", "小夥伴", "家人們", "寶子",
    "首先，", "其次，", "再者，",
]
# 低頻警戒詞（語料 1-2 次）
_WARN_WORDS = ["此外，", "更重要的是", "深入探討", "不可或缺", "引人入勝", "琳瑯滿目", "解鎖"]
# 不會出現在台灣正體文的簡體字（排除里/后/干等兩用字）
_SIMPLIFIED = "这说为们时还让谁买卖钱难写读学号张发经验营养动见问间东车电话语请谢边远运过达邮门视频优应该质惊变岁"
# 匯出殘渣
_RESIDUE = ["notionvc", "## __", "wp:paragraph", "chars_fetched"]

_PLACEHOLDER = re.compile(r"【(修修素材|研究來源待補|來賓語錄待補|時效事實待確認)[：:]([^】]{0,160})】")

# 文類門檻：(zh 字數 WARN 下限, 助詞 FAIL 上限, 助詞 WARN 帶, ！FAIL 上限, ！WARN 帶, 導言必須, CTA 預期)
_GENRE = {
    "book-review": dict(zh_floor=4500, p_hi=45, p_band=(10, 30), b_hi=30, b_band=(5, 20), lede=True, cta=True),
    "science": dict(zh_floor=4500, p_hi=45, p_band=(10, 30), b_hi=30, b_band=(5, 20), lede=True, cta=True),
    "people": dict(zh_floor=1000, p_hi=25, p_band=(2, 15), b_hi=15, b_band=(1, 8), lede=False, cta=False),
}


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


def _blocks(body: str) -> list[str]:
    """回傳純文字段落 block（跳過 code fence、heading、blockquote、表格、清單接續）。"""
    out, buf, in_fence = [], [], False
    for ln in body.split("\n"):
        s = ln.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not s:
            if buf:
                out.append(" ".join(buf))
                buf = []
            continue
        if s.startswith(("#", ">", "|", "---")):
            if buf:
                out.append(" ".join(buf))
                buf = []
            continue
        buf.append(s)
    if buf:
        out.append(" ".join(buf))
    return out


def _find_lines(raw: str, needle: str) -> list[int]:
    return [i for i, ln in enumerate(raw.split("\n"), 1) if needle in ln]


def lint(path: Path, genre: str) -> int:
    raw = path.read_text(encoding="utf-8")
    body = _strip_frontmatter(raw)
    g = _GENRE[genre]

    zh = len(re.findall(r"[一-鿿]", body))
    particles = sum(body.count(p) for p in _PARTICLES)
    bangs = body.count("！")
    semis = body.count("；")
    ni = body.count("你")
    emoji_hits = [m for m in _EMOJI.finditer(body)]
    bold_pairs = body.count("**") // 2
    dashes = body.count("——")

    checks: list[tuple[str, str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append((name, status, detail))

    def grade(name: str, val, fail: bool, warn: bool, detail: str) -> None:
        add(name, "FAIL" if fail else ("WARN" if warn else "PASS"), detail)

    # --- 大忌層（FAIL 就是 FAIL）---
    hits = [(w, _find_lines(body, w)) for w in _BANNED if w in body]
    grade("禁用詞（fable5 零命中表）", hits, bool(hits), False,
          "; ".join(f"{w}@L{ls[:3]}" for w, ls in hits) or "0 個")

    simp = sorted({c for c in body if c in _SIMPLIFIED})
    grade("簡體字", simp, bool(simp), False, "".join(simp) or "無")

    resid = [r for r in _RESIDUE if r in body]
    grade("匯出殘渣", resid, bool(resid), False, ", ".join(resid) or "無")

    # 正文 emoji（購書連結行的 👉🥰 豁免）
    bad_emoji = []
    for ln in body.split("\n"):
        if "購書連結" in ln:
            continue
        bad_emoji += _EMOJI.findall(ln)
    grade("正文 emoji", bad_emoji, bool(bad_emoji), False,
          f"{len(bad_emoji)} 個 {''.join(bad_emoji[:8])}（正文應為 0）")

    grade("分號；", semis, semis > 3, semis > 1, f"{semis} 個（≤3；想用分號時八成該用句號）")

    blocks = _blocks(body)
    long_b = [b for b in blocks if len(b) > 350]
    warn_b = [b for b in blocks if 240 < len(b) <= 350]
    grade("段落長度", long_b, bool(long_b), bool(warn_b),
          f"最長 {max((len(b) for b in blocks), default=0)} 字（>240 WARN、>350 FAIL；修修是一句一段）")

    # --- 聲音平衡層（過平、過載都是病）---
    grade("台味助詞", particles,
          particles > g["p_hi"] or (genre != "people" and particles == 0),
          not (g["p_band"][0] <= particles <= g["p_band"][1]),
          f"{particles} 次（{_PARTICLES}）· 目標帶 {g['p_band']}，0=過平、>{g['p_hi']}=過載拙劣模仿")

    grade("驚嘆號！", bangs,
          bangs > g["b_hi"] or (genre != "people" and bangs == 0),
          not (g["b_band"][0] <= bangs <= g["b_band"][1]),
          f"{bangs} 個 · 目標帶 {g['b_band']}（放真實情緒高點，不連發）")

    grade("對讀者喊話「你」", ni,
          ni == 0 and genre != "people", ni == 0,
          f"{ni} 次（各節收束處該對你喊話）")

    if g["lede"]:
        first = next((ln.strip() for ln in body.split("\n") if ln.strip()), "")
        grade("導言格式", first, not first.startswith("> **"), False,
              "開頭是 > ** 粗體導言" if first.startswith("> **") else "缺 > ** 粗體 blockquote 導言")

    # --- 提醒層 ---
    ww = [(w, len(_find_lines(body, w))) for w in _WARN_WORDS if w in body]
    add("低頻警戒詞", "WARN" if ww else "PASS", "; ".join(f"{w}×{n}" for w, n in ww) or "無")

    unsp = len(re.findall(r"[一-鿿][0-9]", body))
    sp = len(re.findall(r"[一-鿿] [0-9]", body))
    ratio = unsp / (unsp + sp) if (unsp + sp) >= 8 else 0.0
    add("盤古之白（中英數空格）", "WARN" if ratio > 0.3 else "PASS",
        f"未空格 {unsp}/共 {unsp + sp}（>30% 提醒）")

    if g["cta"]:
        tail = body[-1500:]
        has_cta = any(k in tail for k in ("shosho.tw/free", "電子報", "分享給"))
        add("收尾 CTA", "PASS" if has_cta else "WARN", "有" if has_cta else "結尾 1500 字內沒看到電子報/分享 CTA")

    add("篇幅", "WARN" if zh < g["zh_floor"] else "PASS", f"{zh} 中文字（{genre} 下限參考 {g['zh_floor']}）")
    add("粗體重音", "WARN" if bold_pairs == 0 else "PASS", f"{bold_pairs} 處（金句/關鍵數字該有 bold）")
    add("破折號——", "WARN" if dashes > 6 else "PASS", f"{dashes} 次（堆疊是 AI 味）")

    phs = _PLACEHOLDER.findall(body)
    stray = body.count("【") - len(phs)
    add("佔位符", "WARN" if stray > 0 else "INFO",
        f"合法 {len(phs)} 個" + (f"；疑似格式不符的【 {stray} 個" if stray > 0 else "") +
        ("｜" + "、".join(f"{k}" for k, _ in phs[:6]) if phs else ""))

    # --- 輸出 ---
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    width = max(len(n) for n, _, _ in checks)
    print(f"\n聲音初稿 lint · {path.name} · genre={genre}")
    print("─" * 64)
    fails = 0
    for name, status, detail in checks:
        mark = {"PASS": "✓", "WARN": "▲", "FAIL": "✗", "INFO": "·"}[status]
        if status == "FAIL":
            fails += 1
        print(f"  {mark} {name.ljust(width)}  {status:4}  {detail}")
    print("─" * 64)
    print("  語意自審（lint 無法代驗，交稿前逐項確認）：")
    print("    □ 每個主要觀點都有修修的個人錨或佔位符，沒有整節裸奔")
    print("    □ 素材編號清單逐號覆蓋，捨棄的有一句理由")
    print("    □ 來賓語錄逐字、研究引用全部對得到素材來源（零虛構）")
    print("    □ 佔位符清單會列在交付訊息裡（沒有假資料冒充）")
    print("    □ 盲測自問：隨機抽三段，分不出是 AI 還是修修")
    if fails:
        print(f"\n  {fails} 個硬傷 → 修到 0 再交，不討價還價。\n")
        return 1
    print("\n  無硬傷。WARN 項逐一判斷並在交付訊息說明去留。\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="voice-draft style lint for composer")
    ap.add_argument("draft", type=Path)
    ap.add_argument("--genre", choices=sorted(_GENRE), default="science")
    args = ap.parse_args()
    if not args.draft.exists():
        print(f"draft not found: {args.draft}", file=sys.stderr)
        return 2
    return lint(args.draft, args.genre)


if __name__ == "__main__":
    raise SystemExit(main())
