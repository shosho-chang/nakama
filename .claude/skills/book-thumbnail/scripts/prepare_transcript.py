#!/usr/bin/env python3
"""prepare_transcript.py — 把修修的書籍介紹逐字稿（TaskNotes .md）整理成發想用的乾淨稿。

用法:
    python prepare_transcript.py "<逐字稿.md>" --packaging-dir "<work>/packaging"

做的事（全部 deterministic，不改寫任何一句話）:
    - 去 YAML frontmatter
    - 去 Obsidian callout / 引用區塊（`>` 開頭的行——那是審稿意見，不是口白）
    - 去「## 製作註記」「## Reference」之後的整段（出處清單、製作備註）
    - 第一行若是「標題:xxx」→ 抽成 working_title，不放進正文（避免標題發想被工作標題錨定）
寫出:
    <packaging-dir>/transcript.txt          乾淨正文（給 title-brainstorm 的 extract_text 吃）
    <packaging-dir>/transcript_meta.json    來源路徑、SHA-256、字數、working_title
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

CUT_SECTIONS = re.compile(r"^##\s*(製作註記|Reference|References|參考資料)\s*$")
TITLE_LINE = re.compile(r"^\s*標題\s*[:：]\s*(.+?)\s*$")


def clean(raw: str) -> tuple[str, str | None]:
    lines = raw.replace("\r\n", "\n").split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1 :]
                break
    working_title = None
    out: list[str] = []
    for ln in lines:
        if CUT_SECTIONS.match(ln.strip()):
            break
        if ln.lstrip().startswith(">"):
            continue
        m = TITLE_LINE.match(ln)
        if m and working_title is None and not any(x.strip() for x in out):
            working_title = m.group(1)
            continue
        out.append(ln.rstrip())
    text = "\n".join(out).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\n-{3,}\s*$", "", text).strip()
    return text, working_title


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("--packaging-dir", required=True, type=Path)
    a = ap.parse_args()
    if not a.source.is_file():
        sys.stderr.write(f"找不到逐字稿：{a.source}\n")
        return 1
    raw = a.source.read_text(encoding="utf-8")
    text, working_title = clean(raw)
    if len(text) < 500:
        sys.stderr.write(f"整理後只剩 {len(text)} 字——來源可能不是完整逐字稿，停下來確認。\n")
        return 1
    a.packaging_dir.mkdir(parents=True, exist_ok=True)
    (a.packaging_dir / "transcript.txt").write_text(text + "\n", encoding="utf-8")
    meta = {
        "source": str(a.source.resolve()).replace("\\", "/"),
        "source_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "chars": len(text),
        "working_title": working_title,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
    (a.packaging_dir / "transcript_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"OK — 正文 {len(text)} 字；working_title={working_title!r}")
    print(f"  → {a.packaging_dir / 'transcript.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
