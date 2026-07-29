"""social_lint.py — 社群貼文（FB／IG）機械檢查。

門檻**全部來自** `data/brook/style-profiles-fable5/13-genre-social-post.md` 的
實測數字，不是我訂的。禁用詞黑名單直接 import 側寫自己的 `verify_quotes.ZEROS`，
不在這裡重抄一份（抄了就會漂）。

這一層只擋機械可查的東西——版式、字數、禁用詞、markdown 殘留、引號有沒有回檔。
「這篇會不會讓人想點開節目」擋不了，那是冷讀者 persona 那一層的事
（2026-07-05 財富階梯 postmortem 的根因二：**review 方法認證了爛文案**，
機械層過關不等於文案可用）。

用法：
    python social_lint.py <post.md> --platform fb  [--source <transcript_prose.md>]
    python social_lint.py <post.md> --platform ig  [--source ...]
exit 0 = 無 FAIL（WARN 不影響 exit code）。
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ZWSP = "​"
CJK = re.compile(r"[一-鿿]")

# --- 側寫實測值（13-genre-social-post.md）--------------------------------
CHARS_MIN, CHARS_MAX = 877, 1326  # §7e 區間；目標 1,100±200
PARAS_MIN, PARAS_MAX = 19, 30  # §7a 實測 19/22/24/29/30（生成規則寫 20–30，但 19 是真樣本）
PARA_CHARS_MAX = 85  # §7a 單段上限
ONE_SENTENCE_RATIO = 0.60  # §7a 實測 73% 一段一句，留餘裕
EMOJI_ALLOWED = set("🤣😓☺️")  # §9.1 白名單，全語料只用這三個
EMOJI_MAX_FB = 1  # §9.1 訪談宣傳配額 0–1，期望值 0.2
LINKS_MAX = 2  # §9.2 連結密度 0.4／篇 → 上限 2
IG_FOLD_CHARS = 125  # IG caption「⋯更多」截斷點

# AI slop：cowork-outputs/ig-repurpose-skills/AI-SLOP-GOTCHAS.md（修修 2026-07-05）
SLOP_PATTERNS = [
    # ⚠️ 對比句降級為 WARN：gotcha 清單是 2026-07-05 從 IG 圖卡文案抓到的，但
    # 修修自己的 fb-interview-5 用過一次（「不是含金量而已，而是…」）。真語料
    # 打臉的規則不能當 FAIL——擋掉他自己的聲音比放過一次 slop 更糟。
    (r"不是[^，。！？]{1,20}[，、]\s*而是", "「不是 X，而是 Y」對比句（慎用）", "WARN"),
    (r"不是[^，。！？]{1,20}[，、]\s*是[^，。！？]", "「不是 X，是 Y」對比句", "WARN"),
    (r"發一次", "語意含糊的 AI 腔 → 改「發布一則內容」", "FAIL"),
    (r"自述", "第一人稱內容就是修修本人，不需要第三方標籤", "FAIL"),
]

_EMOJI_RE = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f900-\U0001f9ff⬀-⯿️]")


def _load_zeros() -> list[str]:
    """禁用詞黑名單 — 從側寫自己的驗證腳本 import，不重抄。"""
    root = Path(__file__).resolve().parents[4]
    vq = root / "data" / "brook" / "style-profiles-fable5" / "verify_quotes.py"
    if not vq.exists():
        raise SystemExit(
            f"找不到風格側寫 {vq}\n"
            "  social-post skill 依賴 fable5 側寫；側寫不在就不要硬寫（先補側寫）。"
        )
    spec = importlib.util.spec_from_file_location("_vq", vq)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.ZEROS)


def _paragraphs(text: str) -> list[str]:
    """段落 = 非 U+200B 的實際內容行（§7a：段間用單獨一行 U+200B，不是空行）。"""
    return [ln.strip() for ln in text.splitlines() if ln.strip() and ln.strip() != ZWSP]


def _cjk_len(s: str) -> int:
    return len(CJK.findall(s))


def _norm(s: str) -> str:
    return re.sub(r"[\s，。？！、；：…「」『』（）()]", "", s)


def check(text: str, *, platform: str, source: str | None) -> list[tuple[str, str]]:
    """回傳 [(level, message)]；level ∈ {FAIL, WARN}。"""
    out: list[tuple[str, str]] = []
    fail = lambda m: out.append(("FAIL", m))  # noqa: E731
    warn = lambda m: out.append(("WARN", m))  # noqa: E731

    paras = _paragraphs(text)
    chars = _cjk_len(text)

    # --- 篇幅與版式（§7a / §7e）---
    if platform == "fb":
        if not CHARS_MIN <= chars <= CHARS_MAX:
            fail(f"中文字數 {chars} 不在 {CHARS_MIN}–{CHARS_MAX}（目標 1,100±200）")
        if not PARAS_MIN <= len(paras) <= PARAS_MAX:
            fail(f"段落數 {len(paras)} 不在 {PARAS_MIN}–{PARAS_MAX}")
        if ZWSP not in text:
            fail("沒有用 U+200B 分段 — §7a：段間永遠不是空行，是單獨一行零寬空格")
        # 真正的空行 = 原文裡就是空的行。U+200B 那行**不算**空行，它是分段符——
        # 第一版先 replace 掉 ZWSP 再找空行，等於自己造出 false positive，
        # 拿修修 5 篇真貼文當 golden 一跑就 5 篇全紅。
        if any(ln.strip() == "" for ln in text.strip().splitlines()):
            fail("出現空行 — §7a 實測全 10 篇空行數 0（分段要用 U+200B 行）")

    long_paras = [p for p in paras if _cjk_len(p) > PARA_CHARS_MAX]
    if long_paras:
        fail(f"{len(long_paras)} 段超過 {PARA_CHARS_MAX} 字：{long_paras[0][:30]}…")

    with_sent = [p for p in paras if re.search(r"[。！？]", p)]
    if with_sent:
        one = sum(1 for p in with_sent if len(re.findall(r"[。！？]", p)) == 1)
        ratio = one / len(with_sent)
        if ratio < ONE_SENTENCE_RATIO:
            warn(f"一段一句比例 {ratio:.0%} 偏低（§7a 實測 73%）")

    # --- 覆寫後的禁令（§9.2）---
    for pat, why in (
        (r"\*\*", "粗體 ** — FB 不渲染 markdown（§9.2 實測 0 次）"),
        (r"^#{1,6} ", "markdown heading（§9.2 實測 0 次）"),
        (r"^> ", "blockquote 導言（§9.2 實測 0/5）"),
        (r"『.+?』", "『』金句 heading — 金句改放標題行的「」內（§9.2）"),
        (r"🧠", "🧠 品牌標記 — 本文類 df=0（§9.2）"),
    ):
        if re.search(pat, text, re.M):
            fail(why)

    if "；" in text:
        fail("出現分號 — §9.3 本文類密度 0.00，比全域更嚴")

    # --- emoji（§9.1）---
    emojis = _EMOJI_RE.findall(text)
    bad_emoji = [e for e in emojis if e not in EMOJI_ALLOWED and e != "️"]
    if bad_emoji:
        fail(f"用了白名單外的 emoji {set(bad_emoji)} — §9.1 全語料只用 🤣😓☺️")
    real = [e for e in emojis if e in EMOJI_ALLOWED]
    if platform == "fb" and len(real) > EMOJI_MAX_FB:
        fail(f"emoji {len(real)} 個超過訪談宣傳配額 {EMOJI_MAX_FB}（§9.1 期望值 0.2／篇）")
    for p in paras:
        if _EMOJI_RE.match(p):
            fail(f"emoji 開頭違反位置鐵律（§9.1 零開頭）：{p[:20]}…")
        if p.strip() in EMOJI_ALLOWED:
            fail("emoji 單獨成段（§9.1 零單獨成段）")
    if re.search(f"[{''.join(EMOJI_ALLOWED)}]{{2,}}", text):
        fail("連續兩個 emoji（§9.1 零連續）")

    # --- 禁用詞與 AI slop ---
    for w in _load_zeros():
        if w in text:
            fail(f"禁用詞「{w}」— 32 篇語料 df=0（20-negative §1）")
    for pat, why, level in SLOP_PATTERNS:
        m = re.search(pat, text)
        if m:
            (fail if level == "FAIL" else warn)(f"AI slop：{why} — 「{m.group(0)[:24]}」")
    if re.search(r"[一-鿿]*[这为个说么会来对时国还没样种进]", text):
        simplified = set(re.findall(r"[这为个说么会来对时国还没样种进]", text))
        fail(f"簡體字 {simplified}（20-negative §2.8）")

    # --- 連結與 hashtag ---
    links = re.findall(r"https?://\S+", text)
    if len(links) > LINKS_MAX:
        fail(f"連結 {len(links)} 條超過上限 {LINKS_MAX}（§9.2 實測 0.4／篇）")
    tags = re.findall(r"#\S+", text)
    if platform == "fb" and tags:
        fail(f"FB 不用 hashtag（§9.2）：{tags[:3]}")
    if platform == "ig" and not tags:
        warn("IG 沒有 hashtag")

    # --- IG 截斷點 ---
    if platform == "ig":
        head = text[:IG_FOLD_CHARS]
        if not re.search(r"[0-9０-９]|[A-Za-z]{2,}|「", head):
            warn(
                f"IG 前 {IG_FOLD_CHARS} 字沒有具體錨點（數字／專名／引號金句）— "
                "讀者在這裡就決定要不要展開"
            )
        if "http" in head:
            warn("IG 前段放網址沒有意義（caption 連結不可點）")

    # --- 佔位符 ---
    holes = re.findall(r"【[^】]*】|EP\?\?", text)
    if holes:
        warn(f"{len(holes)} 個佔位符待填：{holes[:3]}")

    # --- 引號逐字回檔 ---
    if source:
        src = _norm(Path(source).read_text(encoding="utf-8"))
        for q in re.findall(r"「([^」]{8,})」", text):
            if _norm(q) not in src:
                fail(f"引號內容不在逐字稿裡（誠信紅線 20-negative §4）：「{q[:28]}…」")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="社群貼文機械檢查")
    ap.add_argument("post", help="貼文檔（純文字／md）")
    ap.add_argument("--platform", choices=("fb", "ig"), required=True)
    ap.add_argument("--source", default=None, help="逐字稿路徑（給引號回檔驗證用）")
    args = ap.parse_args(argv)

    text = Path(args.post).read_text(encoding="utf-8")
    results = check(text, platform=args.platform, source=args.source)
    fails = [m for lv, m in results if lv == "FAIL"]
    for lv, m in results:
        print(f"[{lv}] {m}")
    print(
        f"\n{'❌' if fails else '✅'} {args.platform.upper()}："
        f"{len(fails)} FAIL / {len(results) - len(fails)} WARN"
        f"（中文字 {_cjk_len(text)}、段落 {len(_paragraphs(text))}）"
    )
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
