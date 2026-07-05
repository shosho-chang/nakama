"""verify_quotes.py — persona 審稿輸出的引句自動核對（Phase 4 生死線的半自動化）。

用法：
    python verify_quotes.py --review <persona輸出.md> --source <受審文件.md>
                            [--source <另一份.md> ...] [--min-len 6] [--fuzzy-threshold 90]

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
except ImportError:
    partial_ratio = None

QUOTE_RE = re.compile(r"「([^「」]+)」|『([^『』]+)』|“([^“”]+)”")
# 去掉所有非「文字/數字」字元（標點、空白、全形符號都算雜訊）
STRIP_RE = re.compile(r"[^\w]", re.UNICODE)


def normalize(text: str) -> str:
    return STRIP_RE.sub("", unicodedata.normalize("NFKC", text)).lower()


def extract_quotes(review_text: str, min_len: int) -> list[str]:
    quotes = []
    for match in QUOTE_RE.finditer(review_text):
        quote = next((g for g in match.groups() if g is not None), None)
        if quote and len(normalize(quote)) >= min_len:
            quotes.append(quote)
    return quotes


def extract_sections(text: str, section_re: re.Pattern, filter_scores: bool) -> set[str]:
    """抽小節編號。filter_scores=True 時排除分數/百分比/倍率語境（8.5/10、91.7%、1.5 倍）。"""
    sections = set()
    for m in section_re.finditer(text):
        if filter_scores:
            before = text[max(0, m.start() - 1):m.start()]
            after = text[m.end():m.end() + 1]
            if before == "/" or after in ("/", "%", "％", "倍"):
                continue
        sections.add(m.group())
    return sections


def lcs_ratio(needle: str, haystack: str) -> float:
    """最長公共子串長度 / 引句長度（rapidfuzz 缺席時的 fallback）。"""
    if not needle:
        return 0.0
    match = SequenceMatcher(None, needle, haystack, autojunk=False).find_longest_match(
        0, len(needle), 0, len(haystack)
    )
    return match.size / len(needle) * 100


ELLIPSIS_RE = re.compile(r"…+|\.{3,}")


SCORER, SCORER_NAME = (partial_ratio, "rapidfuzz") if partial_ratio else (lcs_ratio, "LCS fallback")


def check_fragment(fragment: str, norm_sources: list[str], threshold: float) -> str:
    norm = normalize(fragment)
    for src in norm_sources:
        if norm in src:
            return "pass"
    if any(SCORER(norm, src) >= threshold for src in norm_sources):
        return "near"
    return "miss"


def check_quote(quote: str, norm_sources: list[str], threshold: float) -> str:
    """省略號拼接的引句（「A……B」）拆段分別比對：全段命中才算 pass。
    片段不套 min_len（整句已過門檻；「先存錢……再投資」的三字片段是合法節錄）。"""
    fragments = [f for f in ELLIPSIS_RE.split(quote) if normalize(f)]
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
    parser.add_argument("--section-pattern", default=r"\d+\.\d+(?:\.\d+)*",
                        help="小節編號 regex（非 X.Y 制的 artifact 如 card-\\d+ 可換）")
    args = parser.parse_args()
    section_re = re.compile(args.section_pattern)

    review_text = args.review.read_text(encoding="utf-8")
    source_texts = [p.read_text(encoding="utf-8") for p in args.source]
    norm_sources = [normalize(t) for t in source_texts]

    quotes = extract_quotes(review_text, args.min_len)
    results = {"pass": [], "near": [], "miss": []}
    for quote in quotes:
        results[check_quote(quote, norm_sources, args.fuzzy_threshold)].append(quote)

    # review 側過濾分數語境（8.5/10、91.7%）；source 側全收，寧可多不漏
    review_sections = extract_sections(review_text, section_re, filter_scores=True)
    source_sections = set()
    for text in source_texts:
        source_sections.update(extract_sections(text, section_re, filter_scores=False))
    missing_sections = sorted(review_sections - source_sections)

    total = len(quotes)
    print(f"# verify_quotes｜{args.review.name}")
    print(f"引句總數（≥{args.min_len} 字）：{total}")
    if total == 0:
        print("⚠️ 0 個引句被抽出——「全過」不成立。檢查 persona 輸出的引號格式"
              "（偵測集：「」『』“”；straight quotes \"...\" 不在內），生死線改人工執行。")
        return 1
    print(f"  pass（原文完全命中）：{len(results['pass'])}")
    print(f"  近似（模糊比對 ≥{args.fuzzy_threshold:g}，{SCORER_NAME}）：{len(results['near'])}")
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
