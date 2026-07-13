#!/usr/bin/env python3
"""extract_text.py — 把文章／逐字稿檔案抽成乾淨純文字，給 title-brainstorm skill 用。

支援 .txt / .md / .srt / .vtt / .docx。**stdlib only**（.docx 走 zipfile + 正則解 XML，
不需要安裝 python-docx，任何機器都能跑）。

用法:
    python extract_text.py <path>            # 純文字印到 stdout，metadata 印到 stderr
    python extract_text.py <path> --json     # 輸出 {path,type,chars,has_timestamps,text} JSON

行為:
    .srt / .vtt : 去掉序號與時間軸，去掉 <tag> 與 {styling}，去掉連續重複字幕，
                  把連續 cue 併成段落，每段前綴 [HH:MM:SS] 時間錨點方便「溯源」。
    .docx       : 解 zip 讀 word/document.xml，依 <w:p> 分段抽 <w:t> 文字。
    .txt / .md  : 原樣讀入（.md 去掉圖片語法雜訊，保留文字與連結文字）。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import signal
import sys
import zipfile

try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError, OSError):
    pass

# Windows 主控台 cp1252 印不了中文 → 強制 UTF-8（nakama 已知踩過的坑）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _read_text(path: str) -> str:
    data = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "cp950", "big5", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "ignore")


_TIME = re.compile(r"(\d{1,2}:\d{2}:\d{2})[.,]\d{1,3}\s*-->")


def _parse_cues(path: str) -> list:
    raw = _read_text(path).lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        start = None
        text_lines = []
        for ln in lines:
            m = _TIME.search(ln)
            if m and start is None:
                start = m.group(1)
                continue
            s = ln.strip()
            if s.isdigit() or s.upper().startswith("WEBVTT") or "-->" in s:
                continue
            text_lines.append(s)
        text = " ".join(text_lines).strip()
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\{[^}]*\}", "", text)
        text = text.strip()
        if text:
            cues.append((start, text))
    return cues


def _merge_cues(cues: list, target: int = 180) -> str:
    paras = []
    cur = []
    cur_start = None
    cur_len = 0
    last = None
    for start, text in cues:
        if text == last:
            continue
        last = text
        if cur_start is None:
            cur_start = start
        cur.append(text)
        cur_len += len(text)
        if cur_len >= target:
            paras.append((cur_start, " ".join(cur)))
            cur, cur_start, cur_len = [], None, 0
    if cur:
        paras.append((cur_start, " ".join(cur)))
    return "\n\n".join((f"[{st}] " if st else "") + txt for st, txt in paras)


def _docx(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    out = []
    for para in re.split(r"</w:p>", xml):
        runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S)
        text = html.unescape("".join(runs)).strip()
        if text:
            out.append(text)
    return "\n\n".join(out)


def extract(path: str):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".srt", ".vtt"):
        return _merge_cues(_parse_cues(path)), ext, True
    if ext == ".docx":
        return _docx(path), ext, False
    text = _read_text(path)
    if ext in (".md", ".markdown"):
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    return text.strip(), ext, False


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract clean text from article/transcript files.")
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if not os.path.isfile(args.path):
        sys.stderr.write(f"[extract_text] 找不到檔案: {args.path}\n")
        return 2
    text, ext, has_ts = extract(args.path)
    if args.json:
        print(json.dumps(
            {"path": args.path, "type": ext, "chars": len(text),
             "has_timestamps": has_ts, "text": text},
            ensure_ascii=False))
    else:
        sys.stderr.write(f"[extract_text] type={ext} chars={len(text)} timestamps={has_ts}\n")
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
