"""EPUB → spine-ordered text extractor (shared/epub_text.py) — route B book ingest."""

from __future__ import annotations

import io
import zipfile

import pytest

from shared.epub_text import EPUBTextError, extract_text


def _epub(chapters, *, with_container=True, opf_dir="OEBPS", spine_order=None) -> bytes:
    """Build a minimal valid EPUB.

    ``chapters``: list of ``(filename, body_xhtml)``. ``spine_order``: indices
    into ``chapters`` for the ``<itemref>`` sequence (default 0..n in order).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        opf_path = f"{opf_dir}/content.opf" if opf_dir else "content.opf"
        if with_container:
            zf.writestr(
                "META-INF/container.xml",
                '<?xml version="1.0"?>'
                '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
                f'<rootfiles><rootfile full-path="{opf_path}" '
                'media-type="application/oebps-package+xml"/></rootfiles></container>',
            )
        items = "".join(
            f'<item id="c{i}" href="{fn}" media-type="application/xhtml+xml"/>'
            for i, (fn, _) in enumerate(chapters)
        )
        order = spine_order if spine_order is not None else list(range(len(chapters)))
        itemrefs = "".join(f'<itemref idref="c{i}"/>' for i in order)
        zf.writestr(
            opf_path,
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            f"<manifest>{items}</manifest><spine>{itemrefs}</spine></package>",
        )
        for fn, body in chapters:
            full = f"{opf_dir}/{fn}" if opf_dir else fn
            zf.writestr(
                full,
                '<?xml version="1.0"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title></head>'
                f"<body>{body}</body></html>",
            )
    return buf.getvalue()


def test_extracts_spine_ordered_text():
    blob = _epub([("a.xhtml", "<p>第一章內容</p>"), ("b.xhtml", "<p>第二章內容</p>")])
    out = extract_text(blob)
    assert "第一章內容" in out
    assert "第二章內容" in out
    assert out.index("第一章內容") < out.index("第二章內容")


def test_spine_order_drives_sequence_not_manifest():
    # spine references chapter 1 before chapter 0 → output reversed vs manifest order.
    blob = _epub([("a.xhtml", "<p>AAA</p>"), ("b.xhtml", "<p>BBB</p>")], spine_order=[1, 0])
    out = extract_text(blob)
    assert out.index("BBB") < out.index("AAA")


def test_strips_tags_and_unescapes_entities():
    blob = _epub([("a.xhtml", "<h1>標題</h1><p>foo &amp; bar &lt;x&gt;</p>")])
    out = extract_text(blob)
    assert "標題" in out
    assert "foo & bar <x>" in out
    assert "<h1>" not in out and "<p>" not in out


def test_drops_script_and_style():
    blob = _epub(
        [("a.xhtml", "<style>.x{color:red}</style><script>alert(1)</script><p>真正內容</p>")]
    )
    out = extract_text(blob)
    assert "真正內容" in out
    assert "alert" not in out
    assert "color:red" not in out


def test_skips_empty_chapters():
    blob = _epub([("a.xhtml", "<p>有內容</p>"), ("b.xhtml", "   ")])
    out = extract_text(blob)
    assert out.strip() == "有內容"


def test_max_chars_truncates():
    blob = _epub([("a.xhtml", "<p>" + "字" * 5000 + "</p>")])
    out = extract_text(blob, max_chars=100)
    assert len(out) <= 100
    assert "字" in out


def test_opf_at_root_no_dir():
    blob = _epub([("a.xhtml", "<p>根目錄 OPF</p>")], opf_dir="")
    assert "根目錄 OPF" in extract_text(blob)


def test_missing_container_raises():
    blob = _epub([("a.xhtml", "<p>x</p>")], with_container=False)
    with pytest.raises(EPUBTextError):
        extract_text(blob)


def test_not_a_zip_raises():
    with pytest.raises(EPUBTextError):
        extract_text(b"this is not a zip at all")
