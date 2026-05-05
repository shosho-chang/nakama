"""Strip XSS vectors from EPUB chapters before foliate-js renders them.

Second line of defense: CSP ``script-src 'self'`` is the first; this pass
removes ``<script>`` elements and ``on*`` event-handler attributes so a bypass
of the CSP header cannot steal session cookies.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

_XHTML_NS = "http://www.w3.org/1999/xhtml"

ET.register_namespace("", _XHTML_NS)


class EPUBStructureError(ValueError):
    """Raised when the blob is not a valid OCF package."""


def sanitize_epub(blob: bytes) -> bytes:
    """Return new EPUB bytes with all <script> + on* handlers stripped.

    Raises EPUBStructureError if the blob is not a valid OCF package
    (non-zip, missing META-INF/container.xml).
    """
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = zf.namelist()
            if "META-INF/container.xml" not in names:
                raise EPUBStructureError("Missing META-INF/container.xml — not a valid OCF package")
            files: dict[str, bytes] = {n: zf.read(n) for n in names}
    except zipfile.BadZipFile as exc:
        raise EPUBStructureError("Not a valid zip/EPUB") from exc

    for name in list(files):
        if name.endswith(".xhtml"):
            files[name] = _sanitize_xhtml(files[name])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as out_zf:
        out_zf.writestr(
            zipfile.ZipInfo("mimetype"),
            files.get("mimetype", b"application/epub+zip"),
            compress_type=zipfile.ZIP_STORED,
        )
        for name in names:
            if name == "mimetype":
                continue
            out_zf.writestr(name, files[name])

    return buf.getvalue()


def _sanitize_xhtml(content: bytes) -> bytes:
    try:
        root = ET.fromstring(content.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return content
    _strip_elem(root)
    return ET.tostring(root, encoding="unicode").encode("utf-8")


def _strip_elem(elem: ET.Element) -> None:
    to_remove = [child for child in elem if _local(child.tag) == "script"]
    for child in to_remove:
        elem.remove(child)

    for attr in list(elem.attrib):
        if _local(attr).lower().startswith("on"):
            del elem.attrib[attr]

    for child in elem:
        _strip_elem(child)


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag
