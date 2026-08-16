"""Local, content-addressed Reference Evidence extraction and retrieval.

Only caller-enrolled files enter this Adapter.  Their bytes are snapshotted
before parsing, passages retain structural locators, and retrieval returns
bounded verbatim excerpts rather than whole documents or synthesized answers.
"""

from __future__ import annotations

import hashlib
import html
import importlib.metadata
import inspect
import io
import json
import os
import platform
import posixpath
import re
import stat
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator, Literal

from pypinyin import Style, lazy_pinyin

from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    ReferenceArtifact,
    ReferenceAuthorityDescriptor,
    ReferenceEvidence,
    ReferenceExtractionSnapshot,
    ReferenceLocator,
    ReferenceLocatorPart,
    ReferenceRetrievalHit,
    ReferenceRetrievalReceipt,
    ReferenceTextBlock,
    validate_reference_authority_binding,
)

from ..hashing import canonical_json_bytes, hash_object, sha256_bytes
from ..ports import (
    AdapterInputError,
    AdapterIntegrityError,
    ReferenceRetrievalRequest,
)

ReferenceKind = Literal[
    "book",
    "research_report",
    "interview_outline",
    "transcript",
    "knowledge_base",
    "other",
]
ReferenceTrustTier = Literal["authoritative", "curated", "contextual"]
ReferenceSourceFormat = Literal["markdown", "text", "pdf", "docx", "epub"]

SUPPORTED_REFERENCE_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".pdf", ".epub", ".docx"})
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_HTML_BLOCK_RE = re.compile(
    r"<(h[1-6]|p|li|blockquote)\b[^>]*>(.*?)</\1\s*>", re.IGNORECASE | re.DOTALL
)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_LATIN_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'._+-]*", re.IGNORECASE)
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_EXTRACTOR_VERSION = "reference-extraction-v2"
_OFFSET_UNIT = "unicode_scalar_v1"
_PHONETIC_MIN_CHARS = 2
_PHONETIC_MAX_CHARS = 6
_MAX_QUERY_PHONETIC_WINDOWS = 256
_MAX_POSTINGS_PER_QUERY_FEATURE = 64
_MAX_CANDIDATE_PASSAGES_PER_QUERY = 64
_SOURCE_FORMAT_BY_SUFFIX: dict[str, ReferenceSourceFormat] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".pdf": "pdf",
    ".docx": "docx",
    ".epub": "epub",
}
_BIDI_CONTROL_CHARACTERS = frozenset(
    {
        "\u061c",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    }
)


@dataclass(frozen=True, slots=True)
class ReferenceSourceSpec:
    """One source explicitly enrolled by the caller for an episode."""

    path: Path
    source_id: str
    kind: ReferenceKind
    title: str
    version: str
    trust_tier: ReferenceTrustTier
    authority: ReferenceAuthorityDescriptor
    document_date: str = "undated"
    enrollment_manifest_sha256: str | None = None
    author: str | None = None
    publisher: str | None = None

    def __post_init__(self) -> None:
        for name in ("source_id", "title", "version", "document_date"):
            value = str(getattr(self, name))
            maximum = 512 if name == "title" else 256
            _validate_metadata(value, f"ReferenceSourceSpec {name}", maximum=maximum)
            if name == "document_date" and value != "undated":
                try:
                    parsed = date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(
                        "ReferenceSourceSpec document_date must be YYYY-MM-DD or 'undated'"
                    ) from exc
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or (
                    parsed.isoformat() != value
                ):
                    raise ValueError(
                        "ReferenceSourceSpec document_date must be YYYY-MM-DD or 'undated'"
                    )
        for name in ("author", "publisher"):
            value = getattr(self, name)
            if value is not None:
                _validate_metadata(
                    value,
                    f"ReferenceSourceSpec {name}",
                    maximum=512,
                )
        if self.enrollment_manifest_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.enrollment_manifest_sha256
        ):
            raise ValueError(
                "ReferenceSourceSpec enrollment_manifest_sha256 must be lowercase SHA-256"
            )
        validate_reference_authority_binding(
            self.authority,
            kind=self.kind,
            title=self.title,
            author=self.author,
            publisher=self.publisher,
            version=self.version,
            trust_tier=self.trust_tier,
        )
        object.__setattr__(self, "path", Path(self.path))


@dataclass(frozen=True, slots=True)
class ReferenceExactLookupRequest:
    """One caller-declared exact extracted-text range, independent of ranking."""

    source_id: str
    extraction_block_index: int
    excerpt_start: int
    excerpt_end: int

    def __post_init__(self) -> None:
        _validate_metadata(self.source_id, "Reference exact lookup source_id", maximum=256)
        if type(self.extraction_block_index) is not int or self.extraction_block_index < 0:
            raise ValueError("Reference exact lookup block index must be a non-negative integer")
        if type(self.excerpt_start) is not int or self.excerpt_start < 0:
            raise ValueError("Reference exact lookup excerpt_start must be non-negative")
        if type(self.excerpt_end) is not int or self.excerpt_end <= self.excerpt_start:
            raise ValueError("Reference exact lookup range must be non-empty")


@dataclass(frozen=True, slots=True)
class ExtractedPassage:
    """Parser-neutral block with an address back to the source snapshot."""

    text: str
    locator_parts: tuple[ReferenceLocatorPart, ...]

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("ExtractedPassage text must not be blank")
        if not self.locator_parts:
            raise ValueError("ExtractedPassage requires locator_parts")


SourceParser = Callable[[bytes], Sequence[ExtractedPassage]]
PdfPageExtractor = Callable[[bytes], Sequence[str]]


@dataclass(frozen=True, slots=True)
class ReferenceParserIdentity:
    """Immutable identity for code allowed to derive an Extracted Text Snapshot."""

    source_format: ReferenceSourceFormat
    name: str
    version: str
    config_hash: str
    code_hash: str
    runtime_hash: str

    def __post_init__(self) -> None:
        for field_name in ("name", "version"):
            value = str(getattr(self, field_name))
            if not value.strip() or len(value) > 256:
                raise ValueError(f"Reference parser {field_name} must be 1..256 characters")
            if any(
                unicodedata.category(character) in {"Cc", "Cs"}
                or character in _BIDI_CONTROL_CHARACTERS
                for character in value
            ):
                raise ValueError(f"Reference parser {field_name} contains control characters")
        for field_name in ("config_hash", "code_hash", "runtime_hash"):
            value = str(getattr(self, field_name))
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"Reference parser {field_name} must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class RegisteredReferenceParser:
    identity: ReferenceParserIdentity
    parser: SourceParser


class TrustedReferenceParserRegistry:
    """Closed production trust root for deterministic Reference parsers."""

    def __init__(self, registrations: Sequence[RegisteredReferenceParser]) -> None:
        by_format: dict[ReferenceSourceFormat, RegisteredReferenceParser] = {}
        for registration in registrations:
            source_format = registration.identity.source_format
            if source_format in by_format:
                raise ValueError(f"duplicate trusted Reference parser for {source_format}")
            by_format[source_format] = registration
        if not by_format:
            raise ValueError("trusted Reference parser registry cannot be empty")
        self._by_format = by_format

    def resolve(self, source_format: ReferenceSourceFormat) -> RegisteredReferenceParser:
        try:
            return self._by_format[source_format]
        except KeyError as exc:
            raise AdapterInputError(
                f"No trusted Reference parser is registered for {source_format}"
            ) from exc


@dataclass(frozen=True, slots=True)
class ReferenceIndex:
    """Public immutable summary of one local content-addressed index."""

    index_hash: str
    retriever_config_hash: str
    retriever_code_hash: str
    retriever_runtime_hash: str
    artifacts: tuple[ReferenceArtifact, ...]
    passage_count: int


@dataclass(frozen=True, slots=True)
class _IndexedPassage:
    artifact: ReferenceArtifact
    snapshot_path: Path
    locator: ReferenceLocator
    text: str
    passage_hash: str
    block_index: int
    normalized_text: str
    lexical_features: frozenset[str]


@dataclass(frozen=True, slots=True)
class _PhoneticPosting:
    passage_index: int
    start: int
    end: int
    syllables: tuple[str, ...]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_metadata(value: str, label: str, *, maximum: int) -> str:
    if not value.strip():
        raise ValueError(f"{label} must not be blank")
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds {maximum} Unicode scalars")
    if any(
        unicodedata.category(character) in {"Cc", "Cs"}
        or character in _BIDI_CONTROL_CHARACTERS
        for character in value
    ):
        raise ValueError(f"{label} contains forbidden control characters")
    return value


def _extraction_snapshot_bytes(snapshot: ReferenceExtractionSnapshot) -> bytes:
    return canonical_json_bytes(snapshot)


def verify_reference_evidence_membership(
    evidence: ReferenceEvidence,
    extraction_snapshot: bytes,
    *,
    enrolled_artifact: ReferenceArtifact,
) -> None:
    """Prove an excerpt is a slice of the explicitly enrolled source extraction."""

    if evidence.artifact != enrolled_artifact:
        raise AdapterIntegrityError(
            "Reference Evidence artifact differs from the explicitly enrolled artifact"
        )
    artifact = enrolled_artifact
    if (
        sha256_bytes(extraction_snapshot) != artifact.extracted_text.sha256
        or len(extraction_snapshot) != artifact.extracted_text.size_bytes
    ):
        raise AdapterIntegrityError("Reference extraction snapshot digest mismatch")
    try:
        payload = json.loads(extraction_snapshot)
        snapshot = ReferenceExtractionSnapshot.model_validate(payload)
    except Exception as exc:
        raise AdapterIntegrityError("Reference extraction snapshot is invalid") from exc
    if canonical_json_bytes(snapshot) != extraction_snapshot:
        raise AdapterIntegrityError("Reference extraction snapshot is not canonical")
    if (
        snapshot.source_sha256 != artifact.digest.sha256
        or snapshot.source_size_bytes != artifact.digest.size_bytes
        or snapshot.source_format != artifact.source_format
        or snapshot.extractor_name != artifact.extractor_name
        or snapshot.extractor_version != artifact.extractor_version
        or snapshot.extractor_config_hash != artifact.extractor_config_hash
        or snapshot.extractor_code_hash != artifact.extractor_code_hash
        or snapshot.extractor_runtime_hash != artifact.extractor_runtime_hash
        or snapshot.offset_unit != artifact.offset_unit
        or len(snapshot.blocks) != artifact.extraction_block_count
    ):
        raise AdapterIntegrityError("Reference extraction snapshot crossed artifact lineage")
    try:
        block = snapshot.blocks[evidence.extraction_block_index]
    except IndexError as exc:
        raise AdapterIntegrityError("Reference Evidence block index is outside snapshot") from exc
    if block.index != evidence.extraction_block_index:
        raise AdapterIntegrityError("Reference Evidence block identity mismatch")
    if block.text_hash != evidence.extraction_block_hash:
        raise AdapterIntegrityError("Reference Evidence block hash mismatch")
    if evidence.excerpt_start < 0 or evidence.excerpt_end <= evidence.excerpt_start:
        raise AdapterIntegrityError("Reference Evidence excerpt range is invalid")
    if block.locator != evidence.locator:
        raise AdapterIntegrityError("Reference Evidence locator differs from extracted block")
    if evidence.excerpt_end > len(block.text):
        raise AdapterIntegrityError("Reference Evidence excerpt range is outside block")
    excerpt = block.text[evidence.excerpt_start : evidence.excerpt_end]
    if excerpt != evidence.excerpt or _sha256(excerpt.encode("utf-8")) != evidence.excerpt_hash:
        raise AdapterIntegrityError("Reference Evidence excerpt is not a snapshot member")
    expected_id = reference_evidence_id(
        artifact=artifact,
        locator=evidence.locator,
        extraction_block_index=evidence.extraction_block_index,
        extraction_block_hash=evidence.extraction_block_hash,
        excerpt_start=evidence.excerpt_start,
        excerpt_end=evidence.excerpt_end,
        excerpt_hash=evidence.excerpt_hash,
    )
    if evidence.id != expected_id:
        raise AdapterIntegrityError("Reference Evidence ID does not match its semantic identity")


def reference_evidence_id(
    *,
    artifact: ReferenceArtifact,
    locator: ReferenceLocator,
    extraction_block_index: int,
    extraction_block_hash: str,
    excerpt_start: int,
    excerpt_end: int,
    excerpt_hash: str,
) -> str:
    """Derive a Reference Evidence ID from every semantic identity field."""

    return "reference-" + hash_object(
        {
            "schema_version": 2,
            "artifact": {
                "schema_version": artifact.schema_version,
                "source_id": artifact.source_id,
                "kind": artifact.kind,
                "source_format": artifact.source_format,
                "source_sha256": artifact.digest.sha256,
                "source_size_bytes": artifact.digest.size_bytes,
                "extracted_text_sha256": artifact.extracted_text.sha256,
                "extracted_text_size_bytes": artifact.extracted_text.size_bytes,
                "extractor_name": artifact.extractor_name,
                "extractor_version": artifact.extractor_version,
                "extractor_config_hash": artifact.extractor_config_hash,
                "extractor_code_hash": artifact.extractor_code_hash,
                "extractor_runtime_hash": artifact.extractor_runtime_hash,
                "offset_unit": artifact.offset_unit,
                "extraction_block_count": artifact.extraction_block_count,
                "title": artifact.title,
                "author": artifact.author,
                "publisher": artifact.publisher,
                "version": artifact.version,
                "document_date": artifact.document_date,
                "enrollment_manifest_sha256": artifact.enrollment_manifest_sha256,
                "trust_tier": artifact.trust_tier,
                "authority": artifact.authority.model_dump(mode="json"),
            },
            "locator": locator.model_dump(mode="json"),
            "extraction_block_index": extraction_block_index,
            "extraction_block_hash": extraction_block_hash,
            "excerpt_start": excerpt_start,
            "excerpt_end": excerpt_end,
            "excerpt_hash": excerpt_hash,
        }
    )


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split()).strip()


def _paragraphs(text: str) -> tuple[str, ...]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n+", normalized)
    return tuple(cleaned for block in blocks if (cleaned := _clean_text(block)))


def _heading_aware_text_passages(text: str, *, markdown: bool) -> tuple[ExtractedPassage, ...]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    passages: list[ExtractedPassage] = []
    paragraph_lines: list[str] = []
    current_heading: str | None = None
    paragraph_number = 0

    def flush() -> None:
        nonlocal paragraph_number
        value = _clean_text(" ".join(paragraph_lines))
        paragraph_lines.clear()
        if not value:
            return
        paragraph_number += 1
        parts: list[ReferenceLocatorPart] = []
        if current_heading:
            parts.append(ReferenceLocatorPart(kind="heading", value=current_heading))
        parts.append(ReferenceLocatorPart(kind="paragraph", value=str(paragraph_number)))
        passages.append(ExtractedPassage(text=value, locator_parts=tuple(parts)))

    for line in lines:
        heading = _HEADING_RE.match(line) if markdown else None
        if heading:
            flush()
            current_heading = _clean_text(heading.group(2))
        elif not line.strip():
            flush()
        else:
            paragraph_lines.append(line)
    flush()
    return tuple(passages)


def _decode_utf8(blob: bytes, *, format_name: str) -> str:
    try:
        return blob.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AdapterInputError(f"{format_name} reference must be UTF-8") from exc


def _parse_markdown(blob: bytes) -> tuple[ExtractedPassage, ...]:
    return _heading_aware_text_passages(_decode_utf8(blob, format_name="Markdown"), markdown=True)


def _parse_text(blob: bytes) -> tuple[ExtractedPassage, ...]:
    return _heading_aware_text_passages(_decode_utf8(blob, format_name="text"), markdown=False)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attribute_by_local_name(element: ET.Element, name: str) -> str | None:
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return value
    return None


def _parse_docx(blob: bytes) -> tuple[ExtractedPassage, ...]:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            document_xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise AdapterInputError("DOCX reference is not a valid Word package") from exc
    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as exc:
        raise AdapterInputError("DOCX word/document.xml is malformed") from exc

    passages: list[ExtractedPassage] = []
    current_heading: str | None = None
    paragraph_number = 0
    for paragraph in (element for element in root.iter() if _local_name(element.tag) == "p"):
        text = _clean_text(
            "".join(
                element.text or ""
                for element in paragraph.iter()
                if _local_name(element.tag) == "t"
            )
        )
        if not text:
            continue
        style = next(
            (
                _attribute_by_local_name(element, "val")
                for element in paragraph.iter()
                if _local_name(element.tag) == "pStyle"
            ),
            None,
        )
        if style and style.casefold().startswith("heading"):
            current_heading = text
            continue
        paragraph_number += 1
        parts: list[ReferenceLocatorPart] = []
        if current_heading:
            parts.append(ReferenceLocatorPart(kind="heading", value=current_heading))
        parts.append(ReferenceLocatorPart(kind="paragraph", value=str(paragraph_number)))
        passages.append(ExtractedPassage(text=text, locator_parts=tuple(parts)))
    if not passages:
        raise AdapterInputError("DOCX reference contains no readable paragraphs")
    return tuple(passages)


def _safe_epub_member(opf_path: str, href: str) -> str:
    member = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), href))
    if member.startswith("../") or member.startswith("/") or member == "..":
        raise AdapterInputError("EPUB manifest contains an unsafe member path")
    return member


def _html_blocks(document: bytes, *, member: str) -> tuple[ExtractedPassage, ...]:
    value = document.decode("utf-8", errors="replace")
    value = _SCRIPT_STYLE_RE.sub(" ", value)
    passages: list[ExtractedPassage] = []
    current_heading: str | None = None
    paragraph_number = 0
    for match in _HTML_BLOCK_RE.finditer(value):
        kind = match.group(1).casefold()
        text = _clean_text(html.unescape(_TAG_RE.sub(" ", match.group(2))))
        if not text:
            continue
        if kind.startswith("h"):
            current_heading = text
            continue
        paragraph_number += 1
        parts: list[ReferenceLocatorPart] = [
            ReferenceLocatorPart(kind="section", value=member)
        ]
        if current_heading:
            parts.append(ReferenceLocatorPart(kind="heading", value=current_heading))
        parts.append(ReferenceLocatorPart(kind="paragraph", value=str(paragraph_number)))
        passages.append(ExtractedPassage(text=text, locator_parts=tuple(parts)))
    return tuple(passages)


def _parse_epub(blob: bytes) -> tuple[ExtractedPassage, ...]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise AdapterInputError("EPUB reference is not a valid package") from exc
    with archive:
        try:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            opf_path = next(
                value
                for element in container.iter()
                if _local_name(element.tag) == "rootfile"
                and (value := _attribute_by_local_name(element, "full-path"))
            )
            opf = ET.fromstring(archive.read(opf_path))
        except (ET.ParseError, KeyError, StopIteration) as exc:
            raise AdapterInputError("EPUB reference has invalid OCF/OPF metadata") from exc

        manifest: dict[str, str] = {}
        for element in opf.iter():
            if _local_name(element.tag) != "item":
                continue
            item_id = _attribute_by_local_name(element, "id")
            href = _attribute_by_local_name(element, "href")
            if item_id and href:
                manifest[item_id] = href
        members = set(archive.namelist())
        passages: list[ExtractedPassage] = []
        for element in opf.iter():
            if _local_name(element.tag) != "itemref":
                continue
            item_id = _attribute_by_local_name(element, "idref")
            if not item_id or item_id not in manifest:
                continue
            member = _safe_epub_member(opf_path, manifest[item_id])
            if member not in members:
                raise AdapterInputError(f"EPUB spine member is missing: {member}")
            passages.extend(_html_blocks(archive.read(member), member=member))
    if not passages:
        raise AdapterInputError("EPUB reference contains no readable paragraphs")
    return tuple(passages)


def _default_pdf_page_extractor(blob: bytes) -> Sequence[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - optional runtime dependency
        raise AdapterInputError(
            "PDF references require optional dependency pypdf>=5.0"
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(blob))
        return tuple(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # pragma: no cover - provider parser variations
        raise AdapterInputError("PDF reference could not be parsed") from exc


def _parse_pdf(blob: bytes, extractor: PdfPageExtractor) -> tuple[ExtractedPassage, ...]:
    try:
        pages = tuple(extractor(blob))
    except AdapterInputError:
        raise
    except Exception as exc:
        raise AdapterInputError("PDF extractor failed") from exc
    passages: list[ExtractedPassage] = []
    for page_number, page_text in enumerate(pages, start=1):
        if not isinstance(page_text, str):
            raise AdapterInputError("PDF extractor must return page strings")
        for paragraph_number, paragraph in enumerate(_paragraphs(page_text), start=1):
            passages.append(
                ExtractedPassage(
                    text=paragraph,
                    locator_parts=(
                        ReferenceLocatorPart(kind="page", value=str(page_number)),
                        ReferenceLocatorPart(kind="paragraph", value=str(paragraph_number)),
                    ),
                )
            )
    if not passages:
        raise AdapterInputError("PDF reference contains no readable paragraphs")
    return tuple(passages)


def _parse_pdf_default(blob: bytes) -> tuple[ExtractedPassage, ...]:
    return _parse_pdf(blob, _default_pdf_page_extractor)


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _parser_code_hash(*functions: Callable[..., object]) -> str:
    return hash_object(
        {
            "functions": [
                {
                    "module": function.__module__,
                    "qualname": function.__qualname__,
                    "source": inspect.getsource(function),
                }
                for function in functions
            ]
        }
    )


def _parser_runtime_hash(*, dependencies: Sequence[str] = ()) -> str:
    return hash_object(
        {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "python_cache_tag": sys.implementation.cache_tag,
            "unicode_database_version": unicodedata.unidata_version,
            "dependencies": {
                dependency: _package_version(dependency)
                for dependency in sorted(dependencies)
            },
        }
    )


def _builtin_parser_identity(
    source_format: ReferenceSourceFormat,
    *,
    functions: Sequence[Callable[..., object]],
    dependencies: Sequence[str] = (),
) -> ReferenceParserIdentity:
    return ReferenceParserIdentity(
        source_format=source_format,
        name=f"nakama.reference.{source_format}",
        version=_EXTRACTOR_VERSION,
        config_hash=hash_object(
            {
                "source_format": source_format,
                "clean_text": "collapse-unicode-whitespace-v1",
                "locator_schema": 1,
                "snapshot_encoding": "canonical-json-utf8",
                "offset_unit": _OFFSET_UNIT,
            }
        ),
        code_hash=_parser_code_hash(*functions),
        runtime_hash=_parser_runtime_hash(dependencies=dependencies),
    )


def default_trusted_parser_registry() -> TrustedReferenceParserRegistry:
    """Return the closed built-in production parser registry with exact identities."""

    common_text = (_clean_text, _heading_aware_text_passages, _decode_utf8)
    return TrustedReferenceParserRegistry(
        (
            RegisteredReferenceParser(
                _builtin_parser_identity(
                    "markdown",
                    functions=(*common_text, _parse_markdown),
                ),
                _parse_markdown,
            ),
            RegisteredReferenceParser(
                _builtin_parser_identity(
                    "text",
                    functions=(*common_text, _parse_text),
                ),
                _parse_text,
            ),
            RegisteredReferenceParser(
                _builtin_parser_identity(
                    "docx",
                    functions=(
                        _clean_text,
                        _local_name,
                        _attribute_by_local_name,
                        _parse_docx,
                    ),
                ),
                _parse_docx,
            ),
            RegisteredReferenceParser(
                _builtin_parser_identity(
                    "epub",
                    functions=(
                        _clean_text,
                        _local_name,
                        _attribute_by_local_name,
                        _safe_epub_member,
                        _html_blocks,
                        _parse_epub,
                    ),
                ),
                _parse_epub,
            ),
            RegisteredReferenceParser(
                _builtin_parser_identity(
                    "pdf",
                    functions=(
                        _clean_text,
                        _paragraphs,
                        _default_pdf_page_extractor,
                        _parse_pdf,
                        _parse_pdf_default,
                    ),
                    dependencies=("pypdf",),
                ),
                _parse_pdf_default,
            ),
        )
    )


def _decoded_text_hash(blob: bytes, source_format: ReferenceSourceFormat) -> str | None:
    if source_format == "markdown":
        return _sha256(_decode_utf8(blob, format_name="Markdown").encode("utf-8"))
    if source_format == "text":
        return _sha256(_decode_utf8(blob, format_name="text").encode("utf-8"))
    return None


def _extract_with_registration(
    blob: bytes,
    registration: RegisteredReferenceParser,
) -> tuple[ExtractedPassage, ...]:
    try:
        result = tuple(registration.parser(blob))
    except AdapterInputError:
        raise
    except Exception as exc:
        raise AdapterInputError(
            f"Reference parser failed for {registration.identity.source_format}"
        ) from exc
    if not result or not all(isinstance(item, ExtractedPassage) for item in result):
        raise AdapterInputError(
            "Reference parser must return one or more ExtractedPassage values"
        )
    return result


def _build_extraction_snapshot(
    blob: bytes,
    registration: RegisteredReferenceParser,
) -> ReferenceExtractionSnapshot:
    identity = registration.identity
    extracted = _extract_with_registration(blob, registration)
    return ReferenceExtractionSnapshot(
        source_sha256=_sha256(blob),
        source_size_bytes=len(blob),
        source_format=identity.source_format,
        decoded_text_sha256=_decoded_text_hash(blob, identity.source_format),
        extractor_name=identity.name,
        extractor_version=identity.version,
        extractor_config_hash=identity.config_hash,
        extractor_code_hash=identity.code_hash,
        extractor_runtime_hash=identity.runtime_hash,
        offset_unit=_OFFSET_UNIT,
        blocks=tuple(
            ReferenceTextBlock(
                index=index,
                locator=ReferenceLocator(parts=passage.locator_parts),
                text=passage.text,
                text_hash=_sha256(passage.text.encode("utf-8")),
            )
            for index, passage in enumerate(extracted)
        ),
    )


def verify_reference_extraction_derivation(
    source_bytes: bytes,
    extraction_snapshot: bytes,
    *,
    enrolled_artifact: ReferenceArtifact,
    parser_registry: TrustedReferenceParserRegistry | None = None,
) -> None:
    """Re-run a trusted parser and prove the stored snapshot derives from source bytes."""

    if (
        _sha256(source_bytes) != enrolled_artifact.digest.sha256
        or len(source_bytes) != enrolled_artifact.digest.size_bytes
    ):
        raise AdapterIntegrityError("Reference source bytes differ from enrolled artifact")
    registry = parser_registry or default_trusted_parser_registry()
    registration = registry.resolve(enrolled_artifact.source_format)
    identity = registration.identity
    if (
        identity.name != enrolled_artifact.extractor_name
        or identity.version != enrolled_artifact.extractor_version
        or identity.config_hash != enrolled_artifact.extractor_config_hash
        or identity.code_hash != enrolled_artifact.extractor_code_hash
        or identity.runtime_hash != enrolled_artifact.extractor_runtime_hash
    ):
        raise AdapterIntegrityError("Enrolled Reference parser is outside the trusted registry")
    expected = _extraction_snapshot_bytes(
        _build_extraction_snapshot(source_bytes, registration)
    )
    if expected != extraction_snapshot:
        raise AdapterIntegrityError(
            "Reference extraction snapshot is not derived by the enrolled trusted parser"
        )
    if (
        _sha256(extraction_snapshot) != enrolled_artifact.extracted_text.sha256
        or len(extraction_snapshot) != enrolled_artifact.extracted_text.size_bytes
    ):
        raise AdapterIntegrityError("Derived Reference extraction digest mismatch")


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _is_regional_indicator(character: str) -> bool:
    return "\U0001f1e6" <= character <= "\U0001f1ff"


def _is_grapheme_extend(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character).startswith("M")
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
        or 0x1F3FB <= codepoint <= 0x1F3FF
    )


def _grapheme_spans(value: str) -> tuple[tuple[int, int], ...]:
    """Conservative extended-grapheme spans sufficient for safe excerpt edges."""

    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(value):
        start = index
        first = value[index]
        index += 1
        if _is_regional_indicator(first) and index < len(value) and _is_regional_indicator(
            value[index]
        ):
            index += 1
        while index < len(value):
            if _is_grapheme_extend(value[index]):
                index += 1
                continue
            if value[index] == "\u200d" and index + 1 < len(value):
                index += 2
                continue
            break
        spans.append((start, index))
    return tuple(spans)


def _normalized_with_original_spans(
    value: str,
) -> tuple[str, tuple[tuple[int, int], ...]]:
    normalized_parts: list[str] = []
    original_spans: list[tuple[int, int]] = []
    for start, end in _grapheme_spans(value):
        normalized = _normalized(value[start:end])
        normalized_parts.append(normalized)
        original_spans.extend((start, end) for _ in normalized)
    return "".join(normalized_parts), tuple(original_spans)


def _lexical_features(value: str) -> frozenset[str]:
    normalized = _normalized(value)
    latin_words = tuple(_LATIN_WORD_RE.findall(normalized))
    features = set(latin_words)
    for word in latin_words:
        for size in range(3, min(6, len(word)) + 1):
            features.update(
                word[index : index + size]
                for index in range(len(word) - size + 1)
            )
    for run in _CJK_RUN_RE.findall(normalized):
        features.add(run)
        if len(run) == 1:
            features.add(run)
        else:
            for size in range(2, min(6, len(run)) + 1):
                if len(run) >= size:
                    features.update(
                        run[index : index + size]
                        for index in range(len(run) - size + 1)
                    )
    return frozenset(features)


@dataclass(frozen=True, slots=True)
class _PhoneticAnchor:
    start: int
    end: int
    matched_text: str
    syllable_count: int
    differing_syllables: int
    query_start: int
    query_end: int


def _pinyin_initial(syllable: str) -> str:
    for initial in ("zh", "ch", "sh"):
        if syllable.startswith(initial):
            return initial
    return syllable[:1]


def _single_edit_or_equal(left: str, right: str) -> bool:
    """Return whether two short ASCII pinyin syllables are <=1 edit apart."""

    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right, strict=True)) == 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    short_index = 0
    long_index = 0
    skipped = False
    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
            long_index += 1
            continue
        if skipped:
            return False
        skipped = True
        long_index += 1
    return True


def _phonetic_syllables(value: str) -> tuple[str, ...]:
    return tuple(
        lazy_pinyin(
            value,
            style=Style.NORMAL,
            strict=True,
            neutral_tone_with_five=False,
            errors="ignore",
        )
    )


def _phonetic_windows(
    value: str,
    *,
    max_windows: int | None = _MAX_QUERY_PHONETIC_WINDOWS,
) -> tuple[tuple[int, int, tuple[str, ...]], ...]:
    """Build bounded CJK windows once per run, preserving source character offsets."""

    windows: list[tuple[int, int, tuple[str, ...]]] = []
    for match in _CJK_RUN_RE.finditer(value):
        run = match.group(0)
        syllables = _phonetic_syllables(run)
        if len(syllables) != len(run):
            continue
        maximum = min(_PHONETIC_MAX_CHARS, len(run))
        sizes = range(_PHONETIC_MIN_CHARS, maximum + 1)
        for size in sizes:
            for offset in range(len(run) - size + 1):
                windows.append(
                    (
                        match.start() + offset,
                        match.start() + offset + size,
                        syllables[offset : offset + size],
                    )
                )
                if max_windows is not None and len(windows) > max_windows:
                    raise AdapterInputError(
                        "Reference query exceeds the bounded phonetic-window budget"
                    )
    return tuple(windows)


def _phonetic_lookup_keys(syllables: tuple[str, ...]) -> tuple[str, ...]:
    """Exact plus one-position-wildcard keys for bounded near-homophone lookup."""

    if len(syllables) < _PHONETIC_MIN_CHARS:
        return ()
    keys = ["exact:" + "/".join(syllables)]
    for index, syllable in enumerate(syllables):
        masked = (*syllables[:index], "*", *syllables[index + 1 :])
        keys.append(
            f"near:{index}:{_pinyin_initial(syllable)}:" + "/".join(masked)
        )
    return tuple(keys)


def _near_phonetic_match(
    observed: tuple[str, ...],
    source: tuple[str, ...],
) -> int | None:
    """Conservative match: one close syllable at most, all initials preserved."""

    if len(observed) != len(source) or len(observed) < 2:
        return None
    differing = tuple(
        (left, right)
        for left, right in zip(observed, source, strict=True)
        if left != right
    )
    if len(differing) > 1:
        return None
    if differing:
        left, right = differing[0]
        if _pinyin_initial(left) != _pinyin_initial(right):
            return None
        if not _single_edit_or_equal(left, right):
            return None
    return len(differing)


_SHORT_TERM_CONTEXT_RE = re.compile(
    r"(?:專有名詞|術語|正式(?:名稱|寫法|用字)|(?:名為|稱為|稱作|叫做)|"
    r"作者|受訪者|書名|題名|人名|品牌|機構|藥名|疾病|研究名|《|》|「|」)"
)


def _short_phonetic_anchor_is_supported(text: str, anchor: _PhoneticAnchor) -> bool:
    """Two-character homophones need local term evidence, not sound alone."""

    if anchor.syllable_count > 2:
        return True
    context_start = max(0, anchor.start - 16)
    context_end = min(len(text), anchor.end + 16)
    return _SHORT_TERM_CONTEXT_RE.search(text[context_start:context_end]) is not None


def _score_passage(
    text: str,
    *,
    normalized_text: str | None = None,
    passage_features: frozenset[str] | None = None,
    observed_text: str,
    candidate_terms: tuple[str, ...],
    phonetic_anchor: _PhoneticAnchor | None = None,
) -> int:
    normalized_text = normalized_text if normalized_text is not None else _normalized(text)
    score = 0
    for index, term in enumerate(candidate_terms):
        normalized_term = _normalized(term).strip()
        if normalized_term and normalized_term in normalized_text:
            score += 1_000_000 - min(index, 999) * 1_000
    normalized_observed = _normalized(observed_text).strip()
    if normalized_observed and normalized_observed in normalized_text:
        score += 100_000
    passage_features = passage_features or _lexical_features(text)
    candidate_features = _lexical_features(" ".join(candidate_terms))
    observed_features = _lexical_features(observed_text)
    score += 2_000 * len(passage_features & candidate_features)
    score += 200 * len(passage_features & observed_features)
    if phonetic_anchor is not None:
        score += (
            25_000
            + 5_000 * phonetic_anchor.syllable_count
            - 2_500 * phonetic_anchor.differing_syllables
        )
    return score


def _minimal_excerpt(
    text: str,
    *,
    terms: tuple[str, ...],
    max_chars: int,
    anchor: tuple[int, int] | None = None,
) -> tuple[str, int, int]:
    if len(text) <= max_chars:
        return text, 0, len(text)
    normalized_text, offset_map = _normalized_with_original_spans(text)
    normalized_matches = [
        (position, position + len(normalized))
        for term in terms
        if (normalized := _normalized(term).strip()) and normalized in normalized_text
        for position in (normalized_text.find(normalized),)
    ]
    if normalized_matches:
        normalized_start, normalized_end = min(normalized_matches)
        target_start = offset_map[normalized_start][0]
        target_end = offset_map[normalized_end - 1][1]
    elif anchor is not None:
        target_start, target_end = anchor
    else:
        target_start = target_end = 0
    start = max(0, min(target_start - max_chars // 3, len(text) - max_chars))
    if target_end > start + max_chars:
        start = max(0, target_end - max_chars)
    end = min(len(text), start + max_chars)

    graphemes = _grapheme_spans(text)
    start = next(
        (cluster_start for cluster_start, _ in graphemes if cluster_start >= start),
        len(text),
    )
    end = next(
        (
            cluster_end
            for _, cluster_end in reversed(graphemes)
            if cluster_end <= end
        ),
        start,
    )
    if target_end and (start > target_start or end < target_end):
        raise AdapterIntegrityError("Reference excerpt window cannot preserve matched graphemes")
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return text[start:end], start, end


def _query_support(
    request: ReferenceRetrievalRequest,
    passage: _IndexedPassage,
    phonetic_anchor: _PhoneticAnchor | None,
) -> tuple[int, int, str, int | None] | None:
    """Return the strongest query support that overlaps this request's anchor."""

    anchor_start = request.context.anchor_query_start
    anchor_end = request.context.anchor_query_end

    def overlaps(start: int, end: int) -> bool:
        return start < anchor_end and end > anchor_start

    normalized_passage = passage.normalized_text
    for index, term in enumerate(request.candidate_terms):
        normalized = _normalized(term).strip()
        if not normalized or normalized not in normalized_passage:
            continue
        position = _normalized(request.observed_text).find(normalized)
        if position >= 0 and overlaps(position, position + len(normalized)):
            return position, position + len(normalized), "candidate_term_exact", index
        # Candidate terms are admitted only after the Module has scoped their
        # detector issue or explicit-vocabulary occurrence to this exact anchor.
        # The persisted request therefore provides typed anchor support even
        # when the observed spelling is too corrupt for string alignment.
        return anchor_start, anchor_end, "candidate_term_exact", index

    normalized_query = _normalized(request.observed_text)
    if normalized_query and normalized_query in normalized_passage:
        if overlaps(0, len(request.observed_text)):
            return 0, len(request.observed_text), "query_exact", None

    if phonetic_anchor is not None and overlaps(
        phonetic_anchor.query_start, phonetic_anchor.query_end
    ):
        kind = (
            "phonetic_exact"
            if phonetic_anchor.differing_syllables == 0
            else "phonetic_near"
        )
        return phonetic_anchor.query_start, phonetic_anchor.query_end, kind, None

    passage_features = passage.lexical_features
    for size in range(min(6, anchor_end - anchor_start), 0, -1):
        for start in range(anchor_start, anchor_end - size + 1):
            end = start + size
            text = request.observed_text[start:end]
            if _lexical_features(text) & passage_features:
                return start, end, "lexical", None
    return None


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _safe_read_source(path: Path, *, source_root: Path | None) -> bytes:
    """Read once from one non-symlink descriptor and reject identity/content races."""

    original = path.absolute()
    try:
        resolved = original.resolve(strict=True)
        containment_root = (
            source_root.resolve(strict=True)
            if source_root is not None
            else original.parent.resolve(strict=True)
        )
        resolved.relative_to(containment_root)
        before = os.lstat(original)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise AdapterInputError(
            f"Reference source is missing or outside its allowed root: {path}"
        ) from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise AdapterInputError("Reference source must be a regular non-symlink file")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(original, flags)
    except OSError as exc:
        raise AdapterInputError(f"Reference source could not be opened safely: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_file_identity(before, opened):
            raise AdapterIntegrityError("Reference source identity changed while opening")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            blob = stream.read()
            after = os.fstat(stream.fileno())
        if not _same_file_identity(opened, after):
            raise AdapterIntegrityError("Reference source identity changed while reading")
        if (
            opened.st_size != after.st_size
            or opened.st_mtime_ns != after.st_mtime_ns
            or len(blob) != after.st_size
        ):
            raise AdapterIntegrityError("Reference source changed while reading")
        return blob
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    """Cross-process lock without racing to initialize a one-byte lock file."""

    try:
        from filelock import FileLock, Timeout
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise AdapterInputError(
            "cross-process Reference CAS locking requires filelock"
        ) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(path), timeout=30):
            yield
    except Timeout as exc:
        raise AdapterIntegrityError(
            f"timed out acquiring Reference CAS lock: {path}"
        ) from exc


def _persist_content_addressed(path: Path, payload: bytes, *, label: str) -> None:
    digest = _sha256(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / ".content-addressed.lock"
    with _exclusive_file_lock(lock_path):
        if path.exists():
            existing = _safe_read_source(path, source_root=path.parent)
            if _sha256(existing) != digest or existing != payload:
                raise AdapterIntegrityError(f"Content-addressed {label} is corrupt: {path}")
            return
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, path)
            temporary_name = None
            persisted = _safe_read_source(path, source_root=path.parent)
            if persisted != payload or _sha256(persisted) != digest:
                raise AdapterIntegrityError(
                    f"Content-addressed {label} collision after atomic write: {path}"
                )
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)


class LocalReferenceRetriever:
    """Deterministic local Reference Retriever backed by immutable snapshots."""

    RETRIEVER_VERSION = "local-reference-v5"

    def __init__(
        self,
        snapshot_root: str | Path,
        sources: Sequence[ReferenceSourceSpec],
        *,
        max_excerpt_chars: int = 480,
        pdf_page_extractor: PdfPageExtractor | None = None,
        parser_overrides: Mapping[str, SourceParser] | None = None,
        parser_identities: Mapping[str, ReferenceParserIdentity] | None = None,
        pdf_parser_identity: ReferenceParserIdentity | None = None,
        allow_untrusted_parser_overrides: bool = False,
        trusted_parser_registry: TrustedReferenceParserRegistry | None = None,
        source_root: str | Path | None = None,
    ) -> None:
        if max_excerpt_chars < 80:
            raise ValueError("max_excerpt_chars must be at least 80")
        if not sources:
            raise ValueError("LocalReferenceRetriever requires explicitly enrolled sources")
        source_ids = [source.source_id for source in sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("Reference source_id values must be unique within an index")
        self._snapshot_root = Path(snapshot_root).resolve()
        self._snapshot_root.mkdir(parents=True, exist_ok=True)
        self._source_root = Path(source_root) if source_root is not None else None
        self._max_excerpt_chars = max_excerpt_chars
        self._adapter_code_hash = _sha256(Path(__file__).read_bytes())
        try:
            pypinyin_version = importlib.metadata.version("pypinyin")
        except importlib.metadata.PackageNotFoundError as exc:  # pragma: no cover
            raise AdapterInputError("Local Reference retrieval requires pypinyin") from exc
        self._adapter_runtime_hash = hash_object(
            {
                "python_implementation": platform.python_implementation(),
                "python_version": tuple(sys.version_info[:3]),
                "pypinyin_version": pypinyin_version,
            }
        )
        self._adapter_config_hash = hash_object(
            {
                "retriever_version": self.RETRIEVER_VERSION,
                "max_excerpt_chars": max_excerpt_chars,
                "lexical_ngram_range": (2, 6),
                "phonetic_min_chars": _PHONETIC_MIN_CHARS,
                "phonetic_max_chars": _PHONETIC_MAX_CHARS,
                "max_query_phonetic_windows": _MAX_QUERY_PHONETIC_WINDOWS,
                "max_postings_per_query_feature": _MAX_POSTINGS_PER_QUERY_FEATURE,
                "max_candidate_passages_per_query": _MAX_CANDIDATE_PASSAGES_PER_QUERY,
                "phonetic_policy": "same-initial-at-most-one-single-edit-syllable-v1",
            }
        )
        self._trusted_parser_registry = (
            trusted_parser_registry or default_trusted_parser_registry()
        )
        normalized_overrides = {
            key.casefold() if key.startswith(".") else f".{key.casefold()}": value
            for key, value in dict(parser_overrides or {}).items()
        }
        normalized_identities = {
            key.casefold() if key.startswith(".") else f".{key.casefold()}": value
            for key, value in dict(parser_identities or {}).items()
        }
        if pdf_page_extractor is not None:
            if ".pdf" in normalized_overrides:
                raise ValueError("pdf_page_extractor conflicts with parser_overrides['.pdf']")
            normalized_overrides[".pdf"] = lambda blob: _parse_pdf(blob, pdf_page_extractor)
            if pdf_parser_identity is not None:
                normalized_identities[".pdf"] = pdf_parser_identity
        if normalized_overrides and not allow_untrusted_parser_overrides:
            raise AdapterInputError(
                "Custom Reference parsers are untrusted by default; use a closed "
                "TrustedReferenceParserRegistry for production or explicitly enable "
                "untrusted parser overrides in tests"
            )
        missing_identities = set(normalized_overrides) - set(normalized_identities)
        extra_identities = set(normalized_identities) - set(normalized_overrides)
        if missing_identities or extra_identities:
            raise ValueError(
                "Every custom Reference parser requires exactly one explicit identity; "
                f"missing={sorted(missing_identities)!r}, extra={sorted(extra_identities)!r}"
            )
        self._untrusted_parser_overrides: dict[
            ReferenceSourceFormat, RegisteredReferenceParser
        ] = {}
        for suffix, parser in normalized_overrides.items():
            if suffix not in _SOURCE_FORMAT_BY_SUFFIX:
                raise AdapterInputError(f"Unsupported Reference parser suffix: {suffix}")
            source_format = _SOURCE_FORMAT_BY_SUFFIX[suffix]
            identity = normalized_identities[suffix]
            if identity.source_format != source_format:
                raise ValueError(
                    f"Custom parser identity format {identity.source_format!r} "
                    f"does not match suffix {suffix!r}"
                )
            if source_format in self._untrusted_parser_overrides:
                raise ValueError(f"duplicate custom Reference parser for {source_format}")
            self._untrusted_parser_overrides[source_format] = RegisteredReferenceParser(
                identity,
                parser,
            )
        self._source_snapshots: dict[str, bytes] = {}
        self._extraction_snapshots: dict[str, bytes] = {}
        indexed = [self._snapshot_and_index(source) for source in sources]
        indexed.sort(key=lambda group: (group[0].source_id, group[0].digest.sha256))
        self._passages = tuple(passage for _, group in indexed for passage in group)
        self._lexical_postings, self._phonetic_postings = self._build_inverted_indexes(
            self._passages
        )
        artifacts = tuple(artifact for artifact, _ in indexed)
        self._artifacts_by_source_id = {
            artifact.source_id: artifact for artifact in artifacts
        }
        self._index = ReferenceIndex(
            index_hash=self._compute_index_hash(
                artifacts,
                self._passages,
                retriever_config_hash=self._adapter_config_hash,
                retriever_code_hash=self._adapter_code_hash,
                retriever_runtime_hash=self._adapter_runtime_hash,
            ),
            retriever_config_hash=self._adapter_config_hash,
            retriever_code_hash=self._adapter_code_hash,
            retriever_runtime_hash=self._adapter_runtime_hash,
            artifacts=artifacts,
            passage_count=len(self._passages),
        )

    @property
    def index(self) -> ReferenceIndex:
        return self._index

    @property
    def adapter_name(self) -> str:
        return "local-reference"

    @property
    def adapter_version(self) -> str:
        return self.RETRIEVER_VERSION

    @property
    def adapter_config_hash(self) -> str:
        return self._adapter_config_hash

    @property
    def adapter_code_hash(self) -> str:
        return self._adapter_code_hash

    @property
    def adapter_runtime_hash(self) -> str:
        return self._adapter_runtime_hash

    def extraction_snapshot(self, artifact: ReferenceArtifact | str) -> bytes:
        """Return verified canonical extraction bytes for persistence by the Module."""

        digest = (
            artifact.extracted_text.sha256
            if isinstance(artifact, ReferenceArtifact)
            else artifact
        )
        try:
            payload = self._extraction_snapshots[digest]
        except KeyError as exc:
            raise AdapterInputError(f"Unknown Reference extraction snapshot: {digest}") from exc
        if sha256_bytes(payload) != digest:
            raise AdapterIntegrityError("Reference extraction snapshot changed after indexing")
        return payload

    def source_snapshot(self, artifact: ReferenceArtifact | str) -> bytes:
        """Return the exact single-descriptor source bytes captured at enrollment."""

        digest = artifact.digest.sha256 if isinstance(artifact, ReferenceArtifact) else artifact
        try:
            payload = self._source_snapshots[digest]
        except KeyError as exc:
            raise AdapterInputError(f"Unknown Reference source snapshot: {digest}") from exc
        if _sha256(payload) != digest:
            raise AdapterIntegrityError("Reference source snapshot changed after indexing")
        return payload

    def verify(self, evidence: ReferenceEvidence) -> None:
        try:
            enrolled_artifact = self._artifacts_by_source_id[evidence.artifact.source_id]
        except KeyError as exc:
            raise AdapterInputError(
                f"Reference source is not enrolled: {evidence.artifact.source_id}"
            ) from exc
        verify_reference_evidence_membership(
            evidence,
            self.extraction_snapshot(evidence.artifact),
            enrolled_artifact=enrolled_artifact,
        )

    def verify_index(self) -> ReferenceIndex:
        """Recompute the complete index identity from enrolled artifacts/passages."""

        self._verify_snapshots()
        artifacts = tuple(
            self._artifacts_by_source_id[source_id]
            for source_id in sorted(self._artifacts_by_source_id)
        )
        if artifacts != self._index.artifacts:
            raise AdapterIntegrityError("Reference index artifact coverage/order drift")
        if any(
            passage.artifact != self._artifacts_by_source_id.get(
                passage.artifact.source_id
            )
            for passage in self._passages
        ):
            raise AdapterIntegrityError("Reference index passage Artifact binding drift")
        if (
            self._index.retriever_config_hash != self._adapter_config_hash
            or self._index.retriever_code_hash != self._adapter_code_hash
            or self._index.retriever_runtime_hash != self._adapter_runtime_hash
        ):
            raise AdapterIntegrityError("Reference index executable identity drift")
        expected = self._compute_index_hash(
            artifacts,
            self._passages,
            retriever_config_hash=self._adapter_config_hash,
            retriever_code_hash=self._adapter_code_hash,
            retriever_runtime_hash=self._adapter_runtime_hash,
        )
        if self._index.index_hash != expected:
            raise AdapterIntegrityError("Reference index content hash drift")
        return self._index

    def verify_derivation(self, artifact: ReferenceArtifact) -> None:
        """Re-run a production-trusted parser over this enrollment's source bytes."""

        verify_reference_extraction_derivation(
            self.source_snapshot(artifact),
            self.extraction_snapshot(artifact),
            enrolled_artifact=artifact,
            parser_registry=self._trusted_parser_registry,
        )

    def _registration_for_format(
        self, source_format: ReferenceSourceFormat
    ) -> RegisteredReferenceParser:
        override = self._untrusted_parser_overrides.get(source_format)
        if override is not None:
            return override
        return self._trusted_parser_registry.resolve(source_format)

    def _snapshot_and_index(
        self, source: ReferenceSourceSpec
    ) -> tuple[ReferenceArtifact, tuple[_IndexedPassage, ...]]:
        original = Path(source.path)
        suffix = original.suffix.casefold()
        if suffix not in SUPPORTED_REFERENCE_SUFFIXES:
            raise AdapterInputError(f"Unsupported Reference source format: {suffix or '<none>'}")
        source_format = _SOURCE_FORMAT_BY_SUFFIX[suffix]
        blob = _safe_read_source(original, source_root=self._source_root)
        digest = _sha256(blob)
        snapshot_dir = self._snapshot_root / "snapshots" / digest
        snapshot = snapshot_dir / f"source{suffix}"
        _persist_content_addressed(snapshot, blob, label="Reference source snapshot")
        self._source_snapshots[digest] = blob

        registration = self._registration_for_format(source_format)
        extraction = _build_extraction_snapshot(blob, registration)
        extraction_bytes = _extraction_snapshot_bytes(extraction)
        extraction_digest = sha256_bytes(extraction_bytes)
        extraction_dir = self._snapshot_root / "extractions" / extraction_digest
        extraction_path = extraction_dir / "extracted-text.json"
        _persist_content_addressed(
            extraction_path,
            extraction_bytes,
            label="Reference extraction",
        )
        self._extraction_snapshots[extraction_digest] = extraction_bytes

        identity = registration.identity
        artifact = ReferenceArtifact(
            source_id=source.source_id,
            kind=source.kind,
            source_format=source_format,
            digest=ArtifactDigest(
                uri=f"reference-snapshot://sha256/{digest}/source{suffix}",
                sha256=digest,
                size_bytes=len(blob),
            ),
            extracted_text=ArtifactDigest(
                uri=f"reference-extraction://sha256/{extraction_digest}/extracted-text.json",
                sha256=extraction_digest,
                size_bytes=len(extraction_bytes),
            ),
            extractor_name=identity.name,
            extractor_version=identity.version,
            extractor_config_hash=identity.config_hash,
            extractor_code_hash=identity.code_hash,
            extractor_runtime_hash=identity.runtime_hash,
            offset_unit=_OFFSET_UNIT,
            extraction_block_count=len(extraction.blocks),
            title=source.title,
            author=source.author,
            publisher=source.publisher,
            version=source.version,
            document_date=source.document_date,
            enrollment_manifest_sha256=source.enrollment_manifest_sha256,
            trust_tier=source.trust_tier,
            authority=source.authority,
        )
        if source_format not in self._untrusted_parser_overrides:
            verify_reference_extraction_derivation(
                blob,
                extraction_bytes,
                enrolled_artifact=artifact,
                parser_registry=self._trusted_parser_registry,
            )
        passages = tuple(
            _IndexedPassage(
                artifact=artifact,
                snapshot_path=snapshot,
                locator=block.locator,
                text=block.text,
                passage_hash=block.text_hash,
                block_index=block.index,
                normalized_text=_normalized(block.text),
                lexical_features=_lexical_features(block.text),
            )
            for block in extraction.blocks
        )
        return artifact, passages

    @staticmethod
    def _build_inverted_indexes(
        passages: tuple[_IndexedPassage, ...],
    ) -> tuple[
        dict[str, tuple[int, ...]],
        dict[str, tuple[_PhoneticPosting, ...]],
    ]:
        lexical: dict[str, list[int]] = {}
        phonetic: dict[str, list[_PhoneticPosting]] = {}
        for passage_index, passage in enumerate(passages):
            for feature in sorted(passage.lexical_features):
                lexical.setdefault(feature, []).append(passage_index)
            for start, end, syllables in _phonetic_windows(
                passage.text,
                max_windows=None,
            ):
                posting = _PhoneticPosting(
                    passage_index=passage_index,
                    start=start,
                    end=end,
                    syllables=syllables,
                )
                for key in _phonetic_lookup_keys(syllables):
                    phonetic.setdefault(key, []).append(posting)
        return (
            {key: tuple(values) for key, values in lexical.items()},
            {key: tuple(values) for key, values in phonetic.items()},
        )

    @staticmethod
    def _compute_index_hash(
        artifacts: tuple[ReferenceArtifact, ...],
        passages: tuple[_IndexedPassage, ...],
        *,
        retriever_config_hash: str,
        retriever_code_hash: str,
        retriever_runtime_hash: str,
    ) -> str:
        payload = {
            "retriever": LocalReferenceRetriever.RETRIEVER_VERSION,
            "retriever_config_hash": retriever_config_hash,
            "retriever_code_hash": retriever_code_hash,
            "retriever_runtime_hash": retriever_runtime_hash,
            "artifacts": [
                {
                    "source_id": artifact.source_id,
                    "kind": artifact.kind,
                    "source_format": artifact.source_format,
                    "sha256": artifact.digest.sha256,
                    "size_bytes": artifact.digest.size_bytes,
                    "extracted_text_sha256": artifact.extracted_text.sha256,
                    "extracted_text_size_bytes": artifact.extracted_text.size_bytes,
                    "extractor_name": artifact.extractor_name,
                    "extractor_version": artifact.extractor_version,
                    "extractor_config_hash": artifact.extractor_config_hash,
                    "extractor_code_hash": artifact.extractor_code_hash,
                    "extractor_runtime_hash": artifact.extractor_runtime_hash,
                    "offset_unit": artifact.offset_unit,
                    "extraction_block_count": artifact.extraction_block_count,
                    "title": artifact.title,
                    "author": artifact.author,
                    "publisher": artifact.publisher,
                    "version": artifact.version,
                    "document_date": artifact.document_date,
                    "enrollment_manifest_sha256": artifact.enrollment_manifest_sha256,
                    "trust_tier": artifact.trust_tier,
                    "authority": artifact.authority.model_dump(mode="json"),
                }
                for artifact in artifacts
            ],
            "passages": [
                {
                    "source_id": passage.artifact.source_id,
                    "block_index": passage.block_index,
                    "locator": passage.locator.model_dump(mode="json"),
                    "passage_hash": passage.passage_hash,
                }
                for passage in passages
            ],
        }
        return hash_object(payload)

    def _verify_snapshots(self) -> None:
        checked: set[Path] = set()
        checked_extractions: set[str] = set()
        for passage in self._passages:
            if passage.snapshot_path in checked:
                continue
            checked.add(passage.snapshot_path)
            source_payload = _safe_read_source(
                passage.snapshot_path,
                source_root=self._snapshot_root,
            )
            actual_hash = _sha256(source_payload)
            if actual_hash != passage.artifact.digest.sha256:
                raise AdapterIntegrityError(
                    "Reference snapshot hash mismatch: "
                    f"expected {passage.artifact.digest.sha256}, got {actual_hash}"
                )
            if len(source_payload) != passage.artifact.digest.size_bytes:
                raise AdapterIntegrityError("Reference snapshot size differs from its digest")
            extraction_digest = passage.artifact.extracted_text.sha256
            if extraction_digest not in checked_extractions:
                checked_extractions.add(extraction_digest)
                extraction_path = (
                    self._snapshot_root
                    / "extractions"
                    / extraction_digest
                    / "extracted-text.json"
                )
                extraction_payload = _safe_read_source(
                    extraction_path,
                    source_root=self._snapshot_root,
                )
                if (
                    _sha256(extraction_payload) != extraction_digest
                    or len(extraction_payload) != passage.artifact.extracted_text.size_bytes
                ):
                    raise AdapterIntegrityError(
                        "Reference extraction digest differs from its artifact"
                    )
                extraction = self.extraction_snapshot(passage.artifact)
                if extraction_payload != extraction:
                    raise AdapterIntegrityError(
                        "Reference extraction storage differs from indexed bytes"
                    )

    def _candidate_passage_indexes(
        self,
        request: ReferenceRetrievalRequest,
    ) -> tuple[tuple[int, ...], dict[int, _PhoneticAnchor], str]:
        lexical_query = tuple(
            sorted(
                _lexical_features(
                    " ".join((*request.candidate_terms, request.observed_text))
                ),
                key=lambda item: (-len(item), item),
            )
        )
        phonetic_windows = _phonetic_windows(request.observed_text)
        phonetic_windows_by_key: dict[
            str, list[tuple[int, int, tuple[str, ...]]]
        ] = {}
        for window in phonetic_windows:
            for key in _phonetic_lookup_keys(window[2]):
                phonetic_windows_by_key.setdefault(key, []).append(window)
        phonetic_query_keys = tuple(
            phonetic_windows_by_key
        )
        candidate_votes: dict[int, int] = {}
        best_anchors: dict[int, tuple[tuple[int, int, int], _PhoneticAnchor]] = {}

        def add_postings(postings: tuple[int, ...], weight: int) -> None:
            if len(postings) > _MAX_POSTINGS_PER_QUERY_FEATURE:
                return
            for passage_index in postings:
                candidate_votes[passage_index] = candidate_votes.get(passage_index, 0) + weight

        for feature in lexical_query:
            add_postings(self._lexical_postings.get(feature, ()), 100 + len(feature))
        source_postings: dict[
            tuple[int, int, int, tuple[str, ...]],
            tuple[tuple[int, int, int, int], _PhoneticPosting, str],
        ] = {}
        for query_key_index, key in enumerate(phonetic_query_keys):
            postings = self._phonetic_postings.get(key, ())
            if len(postings) > _MAX_POSTINGS_PER_QUERY_FEATURE:
                continue
            for posting in postings:
                identity = (
                    posting.passage_index,
                    posting.start,
                    posting.end,
                    posting.syllables,
                )
                exact = 0 if key.startswith("exact:") else 1
                rank = (-len(posting.syllables), exact, query_key_index, posting.start)
                current = source_postings.get(identity)
                if current is None or rank < current[0]:
                    source_postings[identity] = (rank, posting, key)
        ranked_postings = sorted(
            source_postings.values(),
            key=lambda item: (
                item[0],
                item[1].passage_index,
                item[1].start,
                item[1].end,
            ),
        )
        phonetic_postings_examined = 0
        for _, posting, lookup_key in ranked_postings:
            if posting.passage_index in best_anchors:
                continue
            phonetic_postings_examined += 1
            passage = self._passages[posting.passage_index]
            matching_anchors: list[
                tuple[tuple[int, int, int], _PhoneticAnchor]
            ] = []
            for observed_start, _, observed_syllables in phonetic_windows_by_key[
                lookup_key
            ]:
                differing = _near_phonetic_match(observed_syllables, posting.syllables)
                if differing is None:
                    continue
                anchor = _PhoneticAnchor(
                    start=posting.start,
                    end=posting.end,
                    matched_text=passage.text[posting.start : posting.end],
                    syllable_count=len(posting.syllables),
                    differing_syllables=differing,
                    query_start=observed_start,
                    query_end=observed_start + len(observed_syllables),
                )
                if not _short_phonetic_anchor_is_supported(passage.text, anchor):
                    continue
                rank = (-anchor.syllable_count, differing, observed_start)
                matching_anchors.append((rank, anchor))
            if not matching_anchors:
                continue
            rank, anchor = min(matching_anchors, key=lambda item: item[0])
            best_anchors[posting.passage_index] = (rank, anchor)
            candidate_votes[posting.passage_index] = (
                candidate_votes.get(posting.passage_index, 0)
                + 40
                + 10 * anchor.syllable_count
                - 5 * anchor.differing_syllables
            )
            if len(best_anchors) >= _MAX_CANDIDATE_PASSAGES_PER_QUERY:
                break
        ranked = tuple(
            passage_index
            for passage_index, _ in sorted(
                candidate_votes.items(),
                key=lambda item: (-item[1], item[0]),
            )[:_MAX_CANDIDATE_PASSAGES_PER_QUERY]
        )
        query_plan_hash = hash_object(
            {
                "schema_version": 1,
                "index_hash": self._index.index_hash,
                "lexical_query_features": lexical_query,
                "phonetic_query_keys": phonetic_query_keys,
                "phonetic_postings_examined": phonetic_postings_examined,
                "selected_passage_indexes": ranked,
                "limits": {
                    "max_postings_per_feature": _MAX_POSTINGS_PER_QUERY_FEATURE,
                    "max_candidate_passages": _MAX_CANDIDATE_PASSAGES_PER_QUERY,
                },
            }
        )
        return (
            ranked,
            {passage_index: value[1] for passage_index, value in best_anchors.items()},
            query_plan_hash,
        )

    def _retrieve_verified(
        self,
        request: ReferenceRetrievalRequest,
    ) -> ReferenceRetrievalReceipt:
        allowed = set(request.allowed_artifact_ids)
        candidate_indexes, phonetic_anchors, query_plan_hash = (
            self._candidate_passage_indexes(request)
        )
        ranked: list[
            tuple[
                int,
                str,
                str,
                _IndexedPassage,
                _PhoneticAnchor | None,
                tuple[int, int, str, int | None],
            ]
        ] = []
        examined = 0
        for passage_index in candidate_indexes:
            passage = self._passages[passage_index]
            if allowed and passage.artifact.source_id not in allowed:
                continue
            examined += 1
            phonetic_anchor = phonetic_anchors.get(passage_index)
            score = _score_passage(
                passage.text,
                normalized_text=passage.normalized_text,
                passage_features=passage.lexical_features,
                observed_text=request.observed_text,
                candidate_terms=request.candidate_terms,
                phonetic_anchor=phonetic_anchor,
            )
            if score <= 0:
                continue
            support = _query_support(request, passage, phonetic_anchor)
            if support is None:
                continue
            locator_hash = hash_object(passage.locator)
            ranked.append(
                (
                    -score,
                    passage.artifact.source_id,
                    locator_hash,
                    passage,
                    phonetic_anchor,
                    support,
                )
            )
        ranked.sort(key=lambda item: item[:3])
        selected = ranked[: request.max_results]

        evidence: list[ReferenceEvidence] = []
        hits: list[ReferenceRetrievalHit] = []
        terms = (*request.candidate_terms, request.observed_text)
        maximum_score = max((-item[0] for item in selected), default=1)
        for rank, (
            negative_score,
            _,
            _,
            passage,
            phonetic_anchor,
            support,
        ) in enumerate(selected, start=1):
            excerpt, excerpt_start, excerpt_end = _minimal_excerpt(
                passage.text,
                terms=terms,
                max_chars=self._max_excerpt_chars,
                anchor=(phonetic_anchor.start, phonetic_anchor.end)
                if phonetic_anchor is not None
                else None,
            )
            locator = passage.locator
            excerpt_hash = _sha256(excerpt.encode("utf-8"))
            evidence_id = reference_evidence_id(
                artifact=passage.artifact,
                locator=locator,
                extraction_block_index=passage.block_index,
                extraction_block_hash=passage.passage_hash,
                excerpt_start=excerpt_start,
                excerpt_end=excerpt_end,
                excerpt_hash=excerpt_hash,
            )
            evidence.append(
                ReferenceEvidence(
                    id=evidence_id,
                    artifact=passage.artifact,
                    locator=locator,
                    extraction_block_index=passage.block_index,
                    extraction_block_hash=passage.passage_hash,
                    excerpt_start=excerpt_start,
                    excerpt_end=excerpt_end,
                    excerpt=excerpt,
                    excerpt_hash=excerpt_hash,
                )
            )
            self.verify(evidence[-1])
            support_start, support_end, support_kind, candidate_term_index = support
            hits.append(
                ReferenceRetrievalHit(
                    evidence_id=evidence[-1].id,
                    rank=rank,
                    relevance=min(1.0, (-negative_score) / maximum_score),
                    query_support_start=support_start,
                    query_support_end=support_end,
                    support_kind=support_kind,
                    candidate_term_index=candidate_term_index,
                )
            )

        allowed_ids = tuple(sorted(allowed))
        query_id = "reference-query-" + hash_object(
            {
                "episode_id": request.episode_id,
                "invocation_id": request.invocation_id,
                "audio_span_id": request.audio_span_id,
                "request_schema_version": request.schema_version,
                "observed_text": request.observed_text,
                "context": request.context,
                "policy": request.policy,
                "candidate_terms": request.candidate_terms,
                "allowed_source_ids": allowed_ids,
                "max_results": request.max_results,
                "index_hash": self._index.index_hash,
                "retriever_config_hash": self._adapter_config_hash,
                "retriever_code_hash": self._adapter_code_hash,
                "retriever_runtime_hash": self._adapter_runtime_hash,
                "query_plan_hash": query_plan_hash,
                "candidate_passages_examined": examined,
            }
        )
        return ReferenceRetrievalReceipt(
            query_id=query_id,
            episode_id=request.episode_id,
            invocation_id=request.invocation_id,
            audio_span_id=request.audio_span_id,
            query=request.observed_text,
            context=request.context,
            policy=request.policy,
            candidate_terms=request.candidate_terms,
            allowed_source_ids=allowed_ids,
            retriever="local-reference",
            retriever_version=self.RETRIEVER_VERSION,
            retriever_config_hash=self._adapter_config_hash,
            retriever_code_hash=self._adapter_code_hash,
            retriever_runtime_hash=self._adapter_runtime_hash,
            index_hash=self._index.index_hash,
            query_plan_hash=query_plan_hash,
            candidate_passages_examined=examined,
            max_results=request.max_results,
            status="completed",
            hits=tuple(hits),
            evidence=tuple(evidence),
        )

    def retrieve(self, request: ReferenceRetrievalRequest) -> ReferenceRetrievalReceipt:
        self._verify_snapshots()
        return self._retrieve_verified(request)

    def retrieve_many(
        self,
        requests: Sequence[ReferenceRetrievalRequest],
    ) -> tuple[ReferenceRetrievalReceipt, ...]:
        """Verify the immutable corpus once, then execute bounded per-span plans."""

        self._verify_snapshots()
        return tuple(self._retrieve_verified(request) for request in requests)

    def lookup_exact(
        self,
        requests: Sequence[ReferenceExactLookupRequest],
    ) -> tuple[ReferenceEvidence, ...]:
        """Resolve every declared exact range without relevance ranking or a result cap."""

        self.verify_index()
        keys = [
            (
                item.source_id,
                item.extraction_block_index,
                item.excerpt_start,
                item.excerpt_end,
            )
            for item in requests
        ]
        if len(set(keys)) != len(keys):
            raise AdapterInputError("Reference exact lookup requests must be unique")
        passage_by_key = {
            (item.artifact.source_id, item.block_index): item for item in self._passages
        }
        evidence: list[ReferenceEvidence] = []
        for source_id, block_index, excerpt_start, excerpt_end in sorted(keys):
            try:
                passage = passage_by_key[(source_id, block_index)]
            except KeyError as exc:
                raise AdapterInputError(
                    "Reference exact lookup does not match an enrolled extracted-text block: "
                    f"{source_id}:{block_index}"
                ) from exc
            if excerpt_end > len(passage.text):
                raise AdapterInputError("Reference exact lookup range exceeds extracted text")
            excerpt = passage.text[excerpt_start:excerpt_end]
            excerpt_hash = _sha256(excerpt.encode("utf-8"))
            item = ReferenceEvidence(
                id=reference_evidence_id(
                    artifact=passage.artifact,
                    locator=passage.locator,
                    extraction_block_index=passage.block_index,
                    extraction_block_hash=passage.passage_hash,
                    excerpt_start=excerpt_start,
                    excerpt_end=excerpt_end,
                    excerpt_hash=excerpt_hash,
                ),
                artifact=passage.artifact,
                locator=passage.locator,
                extraction_block_index=passage.block_index,
                extraction_block_hash=passage.passage_hash,
                excerpt_start=excerpt_start,
                excerpt_end=excerpt_end,
                excerpt=excerpt,
                excerpt_hash=excerpt_hash,
            )
            self.verify(item)
            evidence.append(item)
        return tuple(evidence)

    def replay(
        self,
        request: ReferenceRetrievalRequest,
        stored: ReferenceRetrievalReceipt,
    ) -> ReferenceRetrievalReceipt:
        """Offline exact replay against the current request, index and snapshots."""

        current = self.retrieve(request)

        def semantic_receipt(receipt: ReferenceRetrievalReceipt) -> ReferenceRetrievalReceipt:
            evidence = tuple(
                item.model_copy(
                    update={
                        "artifact": item.artifact.model_copy(
                            update={
                                "digest": item.artifact.digest.model_copy(
                                    update={"uri": f"sha256://{item.artifact.digest.sha256}"}
                                ),
                                "extracted_text": item.artifact.extracted_text.model_copy(
                                    update={
                                        "uri": (
                                            "sha256://"
                                            f"{item.artifact.extracted_text.sha256}"
                                        )
                                    }
                                ),
                            }
                        )
                    }
                )
                for item in receipt.evidence
            )
            return receipt.model_copy(update={"evidence": evidence})

        if semantic_receipt(current) != semantic_receipt(stored):
            raise AdapterIntegrityError(
                "stored Reference Retrieval Receipt does not exactly replay"
            )
        return stored


__all__ = [
    "ExtractedPassage",
    "LocalReferenceRetriever",
    "PdfPageExtractor",
    "ReferenceIndex",
    "ReferenceExactLookupRequest",
    "ReferenceParserIdentity",
    "ReferenceSourceSpec",
    "RegisteredReferenceParser",
    "SUPPORTED_REFERENCE_SUFFIXES",
    "SourceParser",
    "TrustedReferenceParserRegistry",
    "default_trusted_parser_registry",
    "reference_evidence_id",
    "verify_reference_evidence_membership",
    "verify_reference_extraction_derivation",
]
