"""Scimago Journal Rank ETL — 從原始 CSV 抽出期刊查詢用的精簡資料。

用途：
    Robin PubMed digest 需要 journal tier 當 LLM curation 訊號。
    Scimago 免費公開 SJR 排名（含 Q1-Q4 quartile），但原始 CSV 有 26 欄 ~11MB，
    多數欄位我們不需要。這支腳本抽出 7 欄，轉成 UTF-8 標準格式。

使用方式：
    1. 到 https://www.scimagojr.com/journalrank.php 右上角「Download data」
       下載當年度的 Excel/CSV，另存為 data/_scimago_raw.csv
    2. python scripts/update_scimago.py
    3. git add data/scimago_journals.csv && git commit

輸入檔：
    data/_scimago_raw.csv (gitignored, ~11 MB, ~32k 筆, semicolon-delimited, European decimal)

輸出檔：
    data/scimago_journals.csv (committed, ~3 MB, ~27k 筆, comma-delimited, . decimal)

輸出欄位：
    title, issn, sjr, quartile, h_index, country, categories
    （全部小寫蛇形命名，issn 去掉連字號與空白，sjr 是浮點數）
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

# Windows cp1252 stdout 無法印中文 — 統一 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "_scimago_raw.csv"
OUT = ROOT / "data" / "scimago_journals.csv"

# 原始 CSV 欄位索引（Publisher 在 Scimago export 重複兩次，第 5 與第 22 欄；
# 我們只抓第 5 欄，其餘以位置取值，避免 dict collision）
IDX = {
    "title": 2,
    "type": 3,
    "issn": 4,
    "sjr": 8,
    "quartile": 9,
    "h_index": 10,
    "country": 20,
    "categories": 24,
}


def parse_sjr(raw: str) -> float | None:
    """歐式小數 '104,065' → 104.065。空字串或無法解析回傳 None。"""
    s = raw.strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_issns(raw: str) -> list[str]:
    """原始 ISSN 欄位可能是 '15424863, 00079235' — 拆成 list，去掉連字號與空白。"""
    return [part.strip().replace("-", "") for part in raw.split(",") if part.strip()]


def main() -> int:
    if not RAW.exists():
        print(f"[ERROR] 找不到原始檔：{RAW}", file=sys.stderr)
        print("請先到 scimagojr.com/journalrank.php 下載並另存為該路徑。", file=sys.stderr)
        return 1

    kept = 0
    skipped = 0
    with (
        open(RAW, encoding="utf-8-sig") as f_in,
        open(OUT, "w", encoding="utf-8", newline="") as f_out,
    ):
        reader = csv.reader(f_in, delimiter=";")
        writer = csv.writer(f_out)

        header = next(reader)
        if header[IDX["title"]] != "Title" or header[IDX["sjr"]] != "SJR":
            print(
                f"[ERROR] 欄位位置不符預期，Scimago 可能改格式了。Header: {header}",
                file=sys.stderr,
            )
            return 2

        writer.writerow(["title", "issn", "sjr", "quartile", "h_index", "country", "categories"])

        for row in reader:
            if not row:
                continue
            # 只保留 journal，跳過 "book series" / "conference and proceedings" / "trade journal"
            if row[IDX["type"]].strip().lower() != "journal":
                skipped += 1
                continue

            issns = parse_issns(row[IDX["issn"]])
            writer.writerow(
                [
                    row[IDX["title"]].strip(),
                    "|".join(issns),  # pipe-delimited 避免跟 CSV 的 comma 撞
                    parse_sjr(row[IDX["sjr"]]) or "",
                    row[IDX["quartile"]].strip(),
                    row[IDX["h_index"]].strip(),
                    row[IDX["country"]].strip(),
                    row[IDX["categories"]].strip(),
                ]
            )
            kept += 1

    size_mb = OUT.stat().st_size / (1024 * 1024)
    print(f"[OK] 寫入 {OUT.relative_to(ROOT)}")
    print(f"     保留 {kept} 筆 journal（跳過 {skipped} 筆非期刊類型）")
    print(f"     檔案大小：{size_mb:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
