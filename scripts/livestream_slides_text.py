"""社群直播：從投影片（.pptx）抽出每張的文字與講者備註，給字幕稽核當證據。

只用標準函式庫：pptx 是 zip，投影片在 ``ppt/slides/slideN.xml``，講者備註由
``ppt/slides/_rels/slideN.xml.rels`` 指到 ``ppt/notesSlides/*.xml``（備註檔編號不一定
跟投影片一樣，所以照 rels 找）。依投影片編號 N 排序。

每張輸出：
- ``## 投影片 N``
- 投影片文字：每個段落（``a:p``）一行，段內換行（``a:br``）也換行，空段落略過；
- ``[講者備註] …``：備註本文（body placeholder）壓成一行（段落與換行都換成空白）；
  沒有備註就不印。

輸出路徑已存在就覆寫。

用法：
    python scripts/livestream_slides_text.py \
        --pptx "<episode>/refs/slides.pptx" \
        --out "<episode>/refs/slides-text.md"
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_A = "{" + _NS["a"] + "}"
_NOTES_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"
_SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


def _paragraph_lines(p: ET.Element) -> list[str]:
    """一個 a:p 的文字；a:br 拆成多行。"""
    lines = [""]
    for el in p.iter():
        if el.tag == _A + "t":
            lines[-1] += el.text or ""
        elif el.tag == _A + "br":
            lines.append("")
    return [line.strip() for line in lines]


def _text_lines(root: ET.Element) -> list[str]:
    out: list[str] = []
    for p in root.iter(_A + "p"):
        out.extend(line for line in _paragraph_lines(p) if line)
    return out


def _notes_target(z: zipfile.ZipFile, num: int) -> str | None:
    rels = f"ppt/slides/_rels/slide{num}.xml.rels"
    if rels not in z.namelist():
        return None
    root = ET.fromstring(z.read(rels))
    for rel in root.findall("rel:Relationship", _NS):
        if rel.get("Type") == _NOTES_REL:
            target = posixpath.normpath(posixpath.join("ppt/slides", rel.get("Target", "")))
            return target if target in z.namelist() else None
    return None


def _notes_text(z: zipfile.ZipFile, num: int) -> str:
    """備註本文：只取 body placeholder（略過投影片縮圖、頁碼、頁首頁尾）。"""
    target = _notes_target(z, num)
    if target is None:
        return ""
    root = ET.fromstring(z.read(target))
    parts: list[str] = []
    for sp in root.iter("{" + _NS["p"] + "}sp"):
        ph = sp.find("p:nvSpPr/p:nvPr/p:ph", _NS)
        if ph is None or ph.get("type") != "body":
            continue
        parts.extend(_text_lines(sp))
    # 備註的 a:t 裡常夾換行字元，整段壓成一行、連續空白併成一個
    return " ".join(" ".join(parts).split())


def extract(pptx: Path) -> list[tuple[int, list[str], str]]:
    """回傳 [(投影片編號, 文字行, 講者備註)]，依編號排序。"""
    with zipfile.ZipFile(pptx) as z:
        nums = sorted(int(m.group(1)) for name in z.namelist() if (m := _SLIDE.match(name)))
        slides = []
        for num in nums:
            root = ET.fromstring(z.read(f"ppt/slides/slide{num}.xml"))
            slides.append((num, _text_lines(root), _notes_text(z, num)))
    return slides


def render(pptx: Path, slides: list[tuple[int, list[str], str]]) -> str:
    out = [f"# 投影片文字：{pptx.name}（自動抽取，共 {len(slides)} 張）", ""]
    for num, lines, notes in slides:
        out.append(f"## 投影片 {num}")
        out.extend(lines)
        if notes:
            out.append(f"[講者備註] {notes}")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    # Windows 主控台預設 cp1252，印中文會炸；背景模式不一定能加 PYTHONUTF8=1
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pptx", required=True, help="投影片 .pptx")
    ap.add_argument("--out", required=True, help="輸出 markdown（已存在就覆寫）")
    args = ap.parse_args(argv)

    pptx = Path(args.pptx)
    if not pptx.is_file():
        print(f"找不到投影片：{pptx}", file=sys.stderr)
        return 2
    try:
        slides = extract(pptx)
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        print(f"讀不了投影片（不是有效的 pptx）：{exc}", file=sys.stderr)
        return 2
    if not slides:
        print(f"投影片裡沒有任何 ppt/slides/slideN.xml：{pptx}", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(pptx, slides), encoding="utf-8")
    with_notes = sum(1 for _, _, notes in slides if notes)
    print(f"{len(slides)} 張投影片（{with_notes} 張有講者備註）→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
