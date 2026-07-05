"""verify_quotes.py — persona 審稿輸出的引句自動核對（Phase 4 生死線的半自動化）。

用法：
    python verify_quotes.py --review <persona輸出.md> --source <受審文件.md> [--source <另一份.md> ...]
                            [--min-len 6] [--fuzzy-threshold 90]

邏輯：
    1. 抽出 review 中所有「」『』“” 內的字串；
    2. 正規化（NFKC 全半形統一、去空白、去標點）後在正規化的 source 做子字串比對；
    3. 完全命中→pass；未命中→模糊比對（rapidfuzz partial_ratio，沒裝則用最長公共子串比例），
       達門檻標「近似」；低於門檻列入「未命中清單」；
    4. 另抽 review 中的小節編號（\\d+\\.\\d+），檢查 source 是否存在該編號。

輸出：命中率統計＋未命中清單。exit 0=全過、1=有未命中。
注意：未命中≠虛構（persona 可改寫轉述）——exit 1 只是把清單交給主 agent 人工判讀，不自動作廢。
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

# Windows console 預設 cp950 印不出中文
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from rapidfuzz.fuzz import partial_ratio  # type: ignore

    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False

QUOTE_RE = re.compile(r"「([^「」]+)」|『([^『』]+)』|“([^“”]+)”")
SECTION_RE = re.compile(r"\d+\.\d+(?:\.\d+)*")
# 去掉所有非「文字/數字」字元（標點、空白、全形符號都算雜訊）
STRIP_RE = re.compile(r"[^\w]", re.UNICODE)


def normalize(text: str) -> str:
    return STRIP_RE.sub("", unicodedata.normalize("NFKC", text)).lower()


def extract_quotes(review_text: str, min_len: int) -> list[str]:
    quotes = []
    for match in QUOTE_RE.finditer(review_text):
        quote = next(g for g in match.groups() if g)
        if len(normalize(quote)) >= min_len:
            quotes.append(quote)
    return quotes


def lcs_ratio(needle: str, haystack: str) -> float:
    """最長公共子串長度 / 引句長度（rapidfuzz 缺席時的 fallback）。"""
    if not needle:
        return 0.0
    match = SequenceMatcher(None, needle, haystack, autojunk=False).find_longest_match(
        0, len(needle), 0, len(haystack)
    )
    return match.size / len(needle) * 100


ELLIPSIS_RE = re.compile(r"…+|\.{3,}")


def check_fragment(fragment: str, norm_sources: list[str], threshold: float) -> str:
    norm = normalize(fragment)
    for src in norm_sources:
        if norm in src:
            return "pass"
    scorer = partial_ratio if HAS_RAPIDFUZZ else lcs_ratio
    best = max(scorer(norm, src) for src in norm_sources)
    return "near" if best >= threshold else "miss"


def check_quote(quote: str, norm_sources: list[str], threshold: float, min_len: int) -> str:
    """省略號拼接的引句（「A……B」）拆段分別比對：全段命中才算 pass。"""
    fragments = [f for f in ELLIPSIS_RE.split(quote) if len(normalize(f)) >= min_len]
    if not fragments:
        fragments = [quote]
    verdicts = [check_fragment(f, norm_sources, threshold) for f in fragments]
    if all(v == "pass" for v in verdicts):
        return "pass"
    if all(v in ("pass", "near") for v in verdicts):
        return "near"
    return "miss"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path, action="append")
    parser.add_argument("--min-len", type=int, default=6, help="引句最短長度門檻（過短誤報率高）")
    parser.add_argument("--fuzzy-threshold", type=float, default=90.0)
    args = parser.parse_args()

    review_text = args.review.read_text(encoding="utf-8")
    source_texts = [p.read_text(encoding="utf-8") for p in args.source]
    norm_sources = [normalize(t) for t in source_texts]

    quotes = extract_quotes(review_text, args.min_len)
    results = {"pass": [], "near": [], "miss": []}
    for quote in quotes:
        results[check_quote(quote, norm_sources, args.fuzzy_threshold, args.min_len)].append(quote)

    review_sections = set(SECTION_RE.findall(review_text))
    source_sections = set()
    for text in source_texts:
        source_sections.update(SECTION_RE.findall(text))
    missing_sections = sorted(review_sections - source_sections)

    total = len(quotes)
    print(f"# verify_quotes｜{args.review.name}")
    print(f"引句總數（≥{args.min_len} 字）：{total}")
    if total:
        print(f"  pass（原文完全命中）：{len(results['pass'])}")
        print(f"  近似（模糊比對 ≥{args.fuzzy_threshold:g}{'，rapidfuzz' if HAS_RAPIDFUZZ else '，LCS fallback'}）：{len(results['near'])}")
        print(f"  未命中：{len(results['miss'])}")
        hit = (len(results["pass"]) + len(results["near"])) / total * 100
        print(f"  命中率：{hit:.1f}%")
    if results["miss"]:
        print("\n## 未命中清單（交主 agent 人工判讀：改寫轉述 vs 虛構）")
        for quote in results["miss"]:
            print(f"- 「{quote}」")
    if missing_sections:
        print("\n## review 引用但 source 找不到的小節編號")
        for sec in missing_sections:
            print(f"- {sec}")

    return 1 if (results["miss"] or missing_sections) else 0


if __name__ == "__main__":
    sys.exit(main())
