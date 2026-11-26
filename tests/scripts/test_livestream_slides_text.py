"""`livestream_slides_text.py`：pptx → 每張投影片的文字＋講者備註。

測試自己用 zipfile 生最小 pptx：只放程式會讀的檔（slides、slides/_rels、notesSlides）。
"""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NOTES_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"
_LAYOUT_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"


def _load():
    spec = importlib.util.spec_from_file_location(
        "livestream_slides_text_under_test", _REPO_ROOT / "scripts" / "livestream_slides_text.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


def _para(*runs: str) -> str:
    """一個 a:p；每個參數是一段 run 文字，"<br>" 代表段內換行。"""
    body = "".join("<a:br/>" if r == "<br>" else f"<a:r><a:t>{r}</a:t></a:r>" for r in runs)
    return f"<a:p>{body}</a:p>"


def _sp(paras: list[str], ph_type: str | None = None) -> str:
    ph = f'<p:ph type="{ph_type}"/>' if ph_type else ""
    return (
        f"<p:sp><p:nvSpPr><p:cNvPr id='1' name='s'/><p:cNvSpPr/><p:nvPr>{ph}</p:nvPr></p:nvSpPr>"
        f"<p:txBody>{''.join(paras)}</p:txBody></p:sp>"
    )


def _doc(root: str, shapes: list[str]) -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><p:{root} xmlns:a="{_A}" xmlns:p="{_P}">'
        f"<p:cSld><p:spTree>{''.join(shapes)}</p:spTree></p:cSld></p:{root}>"
    )


def _rels(*rels: tuple[str, str]) -> str:
    items = "".join(
        f'<Relationship Id="rId{i}" Type="{t}" Target="{target}"/>'
        for i, (t, target) in enumerate(rels, 1)
    )
    head = '<?xml version="1.0" encoding="UTF-8"?>'
    return f'{head}<Relationships xmlns="{_REL}">{items}</Relationships>'


def _make_pptx(path: Path) -> Path:
    """三張投影片，故意讓 zip 內順序、備註檔編號都跟投影片編號不同。"""
    with zipfile.ZipFile(path, "w") as z:
        # 投影片 10：有文字、沒有備註（rels 只有 layout）
        z.writestr("ppt/slides/slide10.xml", _doc("sld", [_sp([_para("第十張")])]))
        z.writestr(
            "ppt/slides/_rels/slide10.xml.rels",
            _rels((_LAYOUT_TYPE, "../slideLayouts/slideLayout1.xml")),
        )
        # 投影片 2：兩個 shape、段內換行、空段落；備註檔是 notesSlide7
        z.writestr(
            "ppt/slides/slide2.xml",
            _doc(
                "sld",
                [
                    _sp([_para("LLM 的", "四個天性"), _para()]),
                    _sp([_para("Next Token", "<br>", "Prediction"), _para("Steerability")]),
                ],
            ),
        )
        z.writestr(
            "ppt/slides/_rels/slide2.xml.rels",
            _rels(
                (_NOTES_TYPE, "../notesSlides/notesSlide7.xml"),
                (_LAYOUT_TYPE, "../slideLayouts/slideLayout1.xml"),
            ),
        )
        z.writestr(
            "ppt/notesSlides/notesSlide7.xml",
            _doc(
                "notes",
                [
                    _sp([], "sldImg"),
                    # a:t 裡夾換行（實際投影片就有），備註要壓成一行
                    _sp([_para("[Presenter] 先講特性。"), _para("[Sources]\n-  a.md")], "body"),
                    _sp([_para("2")], "sldNum"),
                ],
            ),
        )
        # 投影片 1：完全空白
        z.writestr("ppt/slides/slide1.xml", _doc("sld", []))
    return path


def test_extract_sorts_by_slide_number_and_reads_notes_via_rels(tmp_path):
    slides = mod.extract(_make_pptx(tmp_path / "deck.pptx"))

    assert [num for num, _, _ in slides] == [1, 2, 10]
    assert slides[0] == (1, [], "")
    assert slides[1] == (
        2,
        ["LLM 的四個天性", "Next Token", "Prediction", "Steerability"],
        "[Presenter] 先講特性。 [Sources] - a.md",
    )
    assert slides[2] == (10, ["第十張"], "")


def test_cli_writes_markdown_and_overwrites(tmp_path, capsys):
    pptx = _make_pptx(tmp_path / "deck.pptx")
    out = tmp_path / "refs" / "slides-text.md"
    out.parent.mkdir()
    out.write_text("舊內容\n" * 50, encoding="utf-8")

    assert mod.main(["--pptx", str(pptx), "--out", str(out)]) == 0

    text = out.read_text(encoding="utf-8")
    assert "舊內容" not in text
    assert text.startswith("# 投影片文字：deck.pptx（自動抽取，共 3 張）\n")
    assert "## 投影片 2\nLLM 的四個天性\nNext Token\nPrediction\nSteerability\n" in text
    assert "[講者備註] [Presenter] 先講特性。 [Sources] - a.md\n" in text
    assert "## 投影片 10\n第十張\n" in text
    assert "2\n## 投影片" not in text  # 頁碼 placeholder 不算備註
    assert text.count("## 投影片 ") == 3
    assert "3 張投影片（1 張有講者備註）" in capsys.readouterr().out


def test_cli_rejects_missing_or_invalid_file(tmp_path):
    out = tmp_path / "slides-text.md"
    assert mod.main(["--pptx", str(tmp_path / "none.pptx"), "--out", str(out)]) == 2

    bad = tmp_path / "bad.pptx"
    bad.write_text("not a zip", encoding="utf-8")
    assert mod.main(["--pptx", str(bad), "--out", str(out)]) == 2
    assert not out.exists()
