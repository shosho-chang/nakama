"""EPUB fixture builder for test_epub_sanitizer / test_epub_metadata /
test_book_storage.

Produces real, parseable EPUB 3 blobs in-memory via ``zipfile`` so tests stay
hermetic — no checked-in binary fixtures, no network. Each ``make_epub_blob``
call composes one EPUB from the kwargs, so tests can dial individual fields
(missing creator, malformed opf, embedded ``<script>`` …) without copy-pasting
boilerplate.

Spec compliance kept just tight enough that foliate-js opens it: ``mimetype``
first + uncompressed, ``META-INF/container.xml`` points at ``OEBPS/content.opf``,
content.opf has ``<package>`` + ``<metadata>`` + ``<manifest>`` + ``<spine>``,
each chapter is well-formed XHTML.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field

CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

DEFAULT_NAV_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body>
<nav epub:type="toc" id="toc">
  <h1>Contents</h1>
  <ol>
    <li><a href="ch1.xhtml">Chapter 1</a></li>
    <li><a href="ch2.xhtml">Chapter 2</a></li>
  </ol>
</nav>
</body>
</html>
"""

DEFAULT_CH1 = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 1</title></head>
<body>
<h1>Chapter 1</h1>
<p>Hello world.</p>
<blockquote>哈囉世界。</blockquote>
</body>
</html>
"""

DEFAULT_CH2 = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 2</title></head>
<body>
<h1>Chapter 2</h1>
<p>Second chapter body.</p>
</body>
</html>
"""


@dataclass
class EPUBSpec:
    """Knobs the tests turn — every field has a sensible default."""

    title: str | None = "The Tracer"
    creator: str | None = "Anon"
    language: str | None = "en"
    isbn: str | None = "urn:isbn:9780000000001"
    published: str | None = "2024-03-15"
    cover_item: bool = False
    chapters: dict[str, str] = field(
        default_factory=lambda: {"ch1.xhtml": DEFAULT_CH1, "ch2.xhtml": DEFAULT_CH2}
    )
    nav_xhtml: str | None = DEFAULT_NAV_XHTML
    malformed_opf: bool = False
    extra_files: dict[str, bytes] = field(default_factory=dict)


def _build_opf(spec: EPUBSpec) -> str:
    if spec.malformed_opf:
        return "<package this is not valid xml at all"

    metadata_parts: list[str] = ['<dc:identifier id="bookid">{}</dc:identifier>']
    if spec.title is not None:
        metadata_parts.append(f"<dc:title>{spec.title}</dc:title>")
    if spec.creator is not None:
        metadata_parts.append(f"<dc:creator>{spec.creator}</dc:creator>")
    if spec.language is not None:
        metadata_parts.append(f"<dc:language>{spec.language}</dc:language>")
    if spec.published is not None:
        metadata_parts.append(f"<dc:date>{spec.published}</dc:date>")
    if spec.cover_item:
        metadata_parts.append('<meta name="cover" content="cover-img"/>')

    isbn = spec.isbn or ""
    metadata_block = "\n    ".join(p.format(isbn) for p in metadata_parts)

    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    ]
    for chap_name in spec.chapters:
        item_id = chap_name.replace(".xhtml", "")
        manifest_items.append(
            f'<item id="{item_id}" href="{chap_name}" media-type="application/xhtml+xml"/>'
        )
    if spec.cover_item:
        manifest_items.append(
            '<item id="cover-img" href="cover.png" media-type="image/png" '
            'properties="cover-image"/>'
        )

    spine_refs = "\n    ".join(
        f'<itemref idref="{name.replace(".xhtml", "")}"/>' for name in spec.chapters
    )

    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    {metadata_block}
  </metadata>
  <manifest>
    {chr(10).join("    " + i for i in manifest_items).strip()}
  </manifest>
  <spine>
    {spine_refs}
  </spine>
</package>
"""


def make_epub_blob(spec: EPUBSpec | None = None) -> bytes:
    """Build a minimal EPUB 3 blob in-memory and return its raw bytes."""
    spec = spec or EPUBSpec()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # mimetype must be first + stored (uncompressed) per EPUB spec.
        zf.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", _build_opf(spec))
        if spec.nav_xhtml is not None:
            zf.writestr("OEBPS/nav.xhtml", spec.nav_xhtml)
        for chap_name, body in spec.chapters.items():
            zf.writestr(f"OEBPS/{chap_name}", body)
        if spec.cover_item:
            # 1x1 transparent PNG.
            zf.writestr(
                "OEBPS/cover.png",
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0dIDATx\x9cc\x00"
                b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
            )
        for path, blob in spec.extra_files.items():
            zf.writestr(path, blob)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pre-baked specs for common test scenarios
# ---------------------------------------------------------------------------


def epub_with_script_tag() -> bytes:
    """EPUB whose chapter contains ``<script>alert(1)</script>``."""
    return make_epub_blob(
        EPUBSpec(
            chapters={
                "ch1.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<title>C1</title>
<script>alert(1)</script>
</head>
<body>
<p>before</p>
<script>document.cookie='stolen'</script>
<p>after</p>
</body>
</html>
""",
                "ch2.xhtml": DEFAULT_CH2,
            }
        )
    )


def epub_with_inline_handlers() -> bytes:
    """EPUB whose chapter has on* event handlers on multiple elements."""
    return make_epub_blob(
        EPUBSpec(
            chapters={
                "ch1.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>C1</title></head>
<body onload="trackPageView()">
<p onclick="alert('p')">click me</p>
<a href="#x" onmouseover="leak()">link</a>
<img src="cover.png" onerror="phone()" alt="x"/>
</body>
</html>
""",
                "ch2.xhtml": DEFAULT_CH2,
            }
        )
    )


def epub_clean() -> bytes:
    """A clean EPUB with no scripts or handlers — sanitize should be near-noop."""
    return make_epub_blob()


def epub_minimal_metadata() -> bytes:
    """EPUB with ONLY identifier — title/creator/language/date all missing."""
    return make_epub_blob(
        EPUBSpec(title=None, creator=None, language=None, isbn=None, published=None)
    )


def epub_malformed_opf() -> bytes:
    return make_epub_blob(EPUBSpec(malformed_opf=True))


def epub_with_cover() -> bytes:
    return make_epub_blob(EPUBSpec(cover_item=True))
