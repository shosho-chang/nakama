"""Promotion Preflight service (ADR-024 Slice 3 / issue #511).

Deterministic preflight inspector for one normalized Reading Source (#509).
No LLM, no KB write, no UI, no vault mutation. Reads variant bytes via an
injected ``blob_loader: Callable[[str], bytes]`` so production callers and
tests provide their own loader (production wires vault helpers; tests inject
in-memory dict-backed loaders). Preflight NEVER imports
``shared.book_storage`` and NEVER parses ``ReadingSource.source_id``
(per Brief §6 boundary 14 + 19).

Output is a frozen ``PreflightReport`` value-object. On any IO / parse
failure the inspector returns a report with ``error=...`` and
``recommended_action="defer"`` instead of raising.

Scope (Brief §0): NO enumeration / listing API on ``PromotionPreflight``
(``list_*`` / ``iter_*`` / ``enumerate_*`` are explicitly forbidden by T13).
The caller decides which Reading Source to preflight.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from shared.epub_metadata import MalformedEPUBError, extract_metadata
from shared.log import get_logger
from shared.schemas.preflight_report import (
    PreflightAction,
    PreflightReason,
    PreflightReport,
    PreflightRiskFlag,
    PreflightSizeSummary,
)
from shared.schemas.reading_source import ReadingSource, SourceVariant
from shared.utils import extract_frontmatter

# Imported under TYPE_CHECKING to keep ``shared.book_storage`` out of the
# runtime import surface — ``shared.reading_source_registry`` imports
# ``book_storage`` at module load, but preflight never CALLS the registry on
# the hot path; T14 subprocess gate enforces import absence.
if TYPE_CHECKING:
    from shared.reading_source_registry import ReadingSourceRegistry

_logger = get_logger("nakama.shared.promotion_preflight")


BlobLoader = Callable[[str], bytes]
"""Maps a ``SourceVariant.path`` string to raw file bytes.

Production callers (consumer slices like #516 Review UI or batch tooling)
inject a loader that resolves vault_root + path and reads from disk. Tests
inject an in-memory dict-backed loader. Preflight NEVER imports
``book_storage`` / vault helpers — path resolution is the loader's job.
"""

# ── Policy thresholds (Brief §4.2) ───────────────────────────────────────────
# Brief calls out these as "starting values" — they're the precedence-ordered
# guard clauses below. Adjusting them is a policy change that should land via
# Brief amendment, not silent edit.

_VERY_SHORT_THRESHOLD = 200
"""``word_count_estimate < _VERY_SHORT_THRESHOLD`` → ``skip``."""

_SHORT_NO_EVIDENCE_THRESHOLD = 1000
"""``has_evidence_track=False`` AND ``word_count_estimate < _SHORT_NO_EVIDENCE_THRESHOLD``
→ ``annotation_only_sync``."""

_WEAK_STRUCTURE_THRESHOLD = 5000
"""``has_evidence_track=False`` AND weak TOC AND ``word_count_estimate <
_WEAK_STRUCTURE_THRESHOLD`` → ``annotation_only_sync``."""

_MIN_TOC_ENTRIES = 3
"""Top-level TOC entry count below this triggers a ``weak_toc`` risk flag."""

_OCR_REPLACEMENT_THRESHOLD = 0.01
"""Fraction of replacement-character / non-printable noise in body text above
which we surface ``ocr_artifact_suspected``. Heuristic — preflight is cheap;
deeper OCR audit is out of scope."""

# Replacement char (U+FFFD) is the canonical "decode failed" marker; combined
# with control chars (excluding \t \n \r) it's a decent OCR-noise smell test.
_OCR_NOISE_CHARS = re.compile(r"[�\x00-\x08\x0b\x0c\x0e-\x1f]")

# Strip XML/HTML tags. EPUB body is XHTML; we want plain text for word-count.
_XML_TAG = re.compile(r"<[^>]+>")

# Markdown ATX heading: 1-6 ``#`` then space then non-empty rest.
_MD_HEADING = re.compile(r"^#{1,6} \S")

# Collapse runs of whitespace so word-count is stable across formatting.
_WHITESPACE_RUN = re.compile(r"\s+")

# EPUB OCF / OPF namespaces (mirror shared/epub_metadata.py constants).
_NS_CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
_NS_OPF = "http://www.idpf.org/2007/opf"


class PromotionPreflight:
    """Deterministic preflight inspector. One ``run(reading_source)`` per
    inspection; no caching, no enumeration.

    Construction takes a ``blob_loader`` (required) and an optional
    ``ReadingSourceRegistry`` for callers that want to chain
    ``resolve(key) → run(rs)`` through one object. The registry is NOT
    consumed on the hot path — ``run()`` accepts a fully-resolved
    ``ReadingSource``.
    """

    def __init__(
        self,
        blob_loader: BlobLoader,
        registry: "ReadingSourceRegistry | None" = None,
    ) -> None:
        self._blob_loader = blob_loader
        self._registry = registry

    # ── Public API ──────────────────────────────────────────────────────────

    def run(self, reading_source: ReadingSource) -> PreflightReport:
        """Inspect ``reading_source`` and return a ``PreflightReport``.

        Variant selection: when ``has_evidence_track=True`` the inspector
        targets the ``role="original"`` variant (the factual evidence track);
        when ``False`` the inspector targets the sole remaining variant
        (``role="display"`` per #509 invariant).
        """
        variant = self._select_variant(reading_source)
        if reading_source.kind == "ebook":
            inspection = self._inspect_ebook(variant)
        else:
            inspection = self._inspect_markdown(variant)

        action, reasons = self._apply_action_policy(reading_source, inspection)

        size = PreflightSizeSummary(
            chapter_count=inspection["chapter_count"],
            word_count_estimate=inspection["word_count_estimate"],
            char_count_estimate=inspection["char_count_estimate"],
            rough_token_estimate=inspection["char_count_estimate"] // 4,
        )

        primary_lang_confidence = (
            "low" if reading_source.evidence_reason == "bilingual_only_inbox" else "high"
        )

        return PreflightReport(
            source_id=reading_source.source_id,
            primary_lang=reading_source.primary_lang,
            primary_lang_confidence=primary_lang_confidence,
            has_evidence_track=reading_source.has_evidence_track,
            evidence_reason=reading_source.evidence_reason,
            size=size,
            risks=inspection["risks"],
            reasons=reasons,
            recommended_action=action,
            error=inspection["error"],
        )

    # ── Variant selection ───────────────────────────────────────────────────

    @staticmethod
    def _select_variant(reading_source: ReadingSource) -> SourceVariant:
        """Pick the variant the inspector reads. Deterministic.

        ``has_evidence_track=True``  → ``role="original"`` (factual evidence).
        ``has_evidence_track=False`` → the one ``role="display"`` variant.
        """
        if reading_source.has_evidence_track:
            for v in reading_source.variants:
                if v.role == "original":
                    return v
            # ReadingSource enforces this invariant in #509, but be loud if
            # someone bypasses construction validation.
            raise ValueError(
                f"has_evidence_track=True but no variant has role='original' "
                f"(source_id={reading_source.source_id!r})"
            )
        for v in reading_source.variants:
            if v.role == "display":
                return v
        raise ValueError(
            f"has_evidence_track=False but no variant has role='display' "
            f"(source_id={reading_source.source_id!r})"
        )

    # ── Inspectors ──────────────────────────────────────────────────────────

    def _inspect_ebook(self, variant: SourceVariant) -> dict:
        """Inspect an EPUB variant. Returns a dict with raw inspection
        metrics; ``run()`` composes the PreflightReport from this.

        Failure mode: any IO / parse failure → return a populated dict with
        ``error`` set and zero counts. NEVER raises.
        """
        try:
            blob = self._blob_loader(variant.path)
        except Exception as exc:  # noqa: BLE001 — unified failure policy
            _logger.warning(
                "ebook blob load failed",
                extra={"category": "preflight_ebook_load_failed", "path": variant.path},
            )
            return _empty_inspection(error=f"blob_load_failed: {exc!s}")

        try:
            metadata = extract_metadata(blob)
        except MalformedEPUBError as exc:
            _logger.warning(
                "ebook metadata extract failed",
                extra={"category": "preflight_ebook_parse_failed", "path": variant.path},
            )
            return _empty_inspection(error=f"epub_parse_failed: {exc!s}")
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "ebook metadata extract failed",
                extra={"category": "preflight_ebook_parse_failed", "path": variant.path},
            )
            return _empty_inspection(error=f"epub_parse_failed: {exc!s}")

        try:
            body_text = _extract_epub_body_text(blob)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "ebook body text extract failed",
                extra={"category": "preflight_ebook_body_failed", "path": variant.path},
            )
            return _empty_inspection(error=f"epub_body_failed: {exc!s}")

        chapter_count = len(metadata.toc)
        word_count = _word_count(body_text)
        char_count = len(body_text)

        risks: list[PreflightRiskFlag] = []

        if chapter_count == 0:
            risks.append(
                PreflightRiskFlag(
                    code="weak_toc",
                    severity="medium",
                    description="No chapters detected in EPUB nav",
                )
            )
        elif chapter_count < _MIN_TOC_ENTRIES:
            risks.append(
                PreflightRiskFlag(
                    code="weak_toc",
                    severity="low",
                    description=f"Only {chapter_count} top-level TOC entries",
                )
            )

        ocr_ratio = _ocr_noise_ratio(body_text)
        if ocr_ratio > _OCR_REPLACEMENT_THRESHOLD:
            risks.append(
                PreflightRiskFlag(
                    code="ocr_artifact",
                    severity="medium",
                    description=(
                        f"High replacement-char ratio ({ocr_ratio:.2%}) suggests "
                        "OCR / encoding artifacts"
                    ),
                )
            )

        return {
            "chapter_count": chapter_count,
            "word_count_estimate": word_count,
            "char_count_estimate": char_count,
            "risks": risks,
            "error": None,
        }

    def _inspect_markdown(self, variant: SourceVariant) -> dict:
        """Inspect a markdown variant. Same failure-mode contract as ebook
        inspector.
        """
        try:
            blob = self._blob_loader(variant.path)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "inbox blob load failed",
                extra={"category": "preflight_inbox_load_failed", "path": variant.path},
            )
            return _empty_inspection(error=f"blob_load_failed: {exc!s}")

        try:
            content = blob.decode("utf-8")
        except UnicodeDecodeError as exc:
            _logger.warning(
                "inbox decode failed",
                extra={"category": "preflight_inbox_decode_failed", "path": variant.path},
            )
            return _empty_inspection(error=f"inbox_decode_failed: {exc!s}")

        try:
            frontmatter, body = extract_frontmatter(content)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "inbox frontmatter parse failed",
                extra={"category": "preflight_inbox_fm_failed", "path": variant.path},
            )
            return _empty_inspection(error=f"frontmatter_parse_failed: {exc!s}")

        # Section count: lines that start with one-or-more ``#`` followed by
        # a space and content. Cheap heuristic; deeper structure analysis is
        # out of scope.
        section_count = sum(1 for line in body.splitlines() if _MD_HEADING.match(line))
        word_count = _word_count(body)
        char_count = len(body)

        risks: list[PreflightRiskFlag] = []

        if section_count == 0:
            risks.append(
                PreflightRiskFlag(
                    code="weak_toc",
                    severity="medium",
                    description="No markdown sections detected",
                )
            )
        elif section_count < _MIN_TOC_ENTRIES:
            risks.append(
                PreflightRiskFlag(
                    code="weak_toc",
                    severity="low",
                    description=f"Only {section_count} markdown sections",
                )
            )

        # Frontmatter minimal: missing both lang AND title.
        if not frontmatter.get("lang") and not frontmatter.get("title"):
            risks.append(
                PreflightRiskFlag(
                    code="frontmatter_minimal",
                    severity="low",
                    description="Inbox frontmatter missing both lang and title",
                )
            )

        ocr_ratio = _ocr_noise_ratio(body)
        if ocr_ratio > _OCR_REPLACEMENT_THRESHOLD:
            risks.append(
                PreflightRiskFlag(
                    code="ocr_artifact",
                    severity="medium",
                    description=(
                        f"High replacement-char ratio ({ocr_ratio:.2%}) suggests encoding artifacts"
                    ),
                )
            )

        return {
            "chapter_count": section_count,
            "word_count_estimate": word_count,
            "char_count_estimate": char_count,
            "risks": risks,
            "error": None,
        }

    # ── Action policy (Brief §4.2 — top-down, first-match-wins) ─────────────

    @staticmethod
    def _apply_action_policy(
        reading_source: ReadingSource, inspection: dict
    ) -> tuple[PreflightAction, list[PreflightReason]]:
        """Brief §4.2 mapping table, dispatched in declared row order.

        First match wins; the order encodes precedence (errors before
        thresholds; very-short before missing-evidence; weak-structure
        before length-based defer; high-severity defer before
        proceed-with-warnings).
        """
        if inspection["error"] is not None:
            # Row 1: inspector error → defer with placeholder reason; details
            # in PreflightReport.error field.
            return ("defer", ["frontmatter_minimal"])

        word_count: int = inspection["word_count_estimate"]
        chapter_count: int = inspection["chapter_count"]
        risks: list[PreflightRiskFlag] = inspection["risks"]

        if word_count < _VERY_SHORT_THRESHOLD:
            # Row 2: very short → skip regardless of evidence track.
            return ("skip", ["very_short"])

        if not reading_source.has_evidence_track and word_count < _SHORT_NO_EVIDENCE_THRESHOLD:
            # Row 3: short + no evidence → annotation-only sync.
            return (
                "annotation_only_sync",
                ["missing_evidence_track", "very_short"],
            )

        weak_structure = chapter_count == 0 or any(r.code == "weak_toc" for r in risks)
        if (
            not reading_source.has_evidence_track
            and weak_structure
            and word_count < _WEAK_STRUCTURE_THRESHOLD
        ):
            # Row 4: weak-structure + no evidence + below weak-structure
            # ceiling → annotation-only sync.
            return (
                "annotation_only_sync",
                ["missing_evidence_track", "weak_toc"],
            )

        if not reading_source.has_evidence_track:
            # Row 5: moderate-to-large content + no evidence → defer. Append
            # low-confidence-lang reason for case (b) bilingual-only inbox.
            reasons: list[PreflightReason] = ["missing_evidence_track"]
            if reading_source.evidence_reason == "bilingual_only_inbox":
                reasons.append("low_confidence_lang")
            return ("defer", reasons)

        # Below this point: has_evidence_track == True.
        high_risks = [r for r in risks if r.severity == "high"]
        if high_risks:
            # Row 6: evidence + high-severity risks → defer.
            return ("defer", [_risk_code_to_reason(r.code) for r in high_risks])

        medium_risks = [r for r in risks if r.severity == "medium"]
        if medium_risks:
            # Row 7: evidence + medium-severity risks → proceed with warnings.
            return (
                "proceed_with_warnings",
                [_risk_code_to_reason(r.code) for r in medium_risks],
            )

        # Row 8: evidence + clean → full promotion.
        return ("proceed_full_promotion", ["ok"])


# ── Module-level helpers ────────────────────────────────────────────────────


def _empty_inspection(*, error: str) -> dict:
    """Inspection-result dict for the failure path."""
    return {
        "chapter_count": 0,
        "word_count_estimate": 0,
        "char_count_estimate": 0,
        "risks": [],
        "error": error,
    }


def _word_count(text: str) -> int:
    """Whitespace-split token count. Cheap, language-agnostic enough for
    rough size estimation. Brief §6 boundary 11 forbids tokenizer
    dependencies in this slice."""
    if not text:
        return 0
    return len(_WHITESPACE_RUN.split(text.strip())) if text.strip() else 0


def _ocr_noise_ratio(text: str) -> float:
    """Fraction of replacement-character + non-printable bytes in ``text``.
    Heuristic OCR / encoding-artifact smell test."""
    if not text:
        return 0.0
    noise = len(_OCR_NOISE_CHARS.findall(text))
    return noise / len(text)


def _risk_code_to_reason(code: str) -> PreflightReason:
    """Map a ``PreflightRiskCode`` to its closest ``PreflightReason``.

    Both enums are closed for ``schema_version=1``; this mapping mirrors the
    natural correspondence (weak_toc ↔ weak_toc, ocr_artifact ↔
    ocr_artifact_suspected, etc.). Codes without a 1:1 reason fall back to
    ``frontmatter_minimal`` as a generic "see risk list" placeholder.
    """
    mapping: dict[str, PreflightReason] = {
        "weak_toc": "weak_toc",
        "ocr_artifact": "ocr_artifact_suspected",
        "mixed_language": "mixed_language_suspected",
        "missing_evidence": "missing_evidence_track",
        "low_signal_count": "very_short",
        "frontmatter_minimal": "frontmatter_minimal",
    }
    return mapping.get(code, "frontmatter_minimal")


def _extract_epub_body_text(blob: bytes) -> str:
    """Walk OPF spine items and concatenate their stripped XHTML body text.

    Pure stdlib (``zipfile`` + ``xml.etree`` + regex). No BeautifulSoup, no
    lxml — Brief §3 calls for ``xml.etree``. Failures propagate so the
    caller (`_inspect_ebook`) can wrap them as ``epub_body_failed`` errors.
    """
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = set(zf.namelist())
        if "META-INF/container.xml" not in names:
            raise MalformedEPUBError("Missing META-INF/container.xml")
        opf_path = _find_opf_path(zf.read("META-INF/container.xml"))
        opf_root = ET.fromstring(zf.read(opf_path).decode("utf-8", errors="replace"))
        opf_dir = str(PurePosixPath(opf_path).parent)

        manifest_map = _build_manifest_map(opf_root, opf_dir)
        spine = opf_root.find(f"{{{_NS_OPF}}}spine")
        if spine is None:
            return ""
        for itemref in spine:
            idref = itemref.get("idref")
            if not idref or idref not in manifest_map:
                continue
            href = manifest_map[idref]
            if href not in names:
                continue
            try:
                xhtml = zf.read(href).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 — skip unreadable spine items
                continue
            parts.append(_strip_xml(xhtml))
    return "\n".join(parts)


def _find_opf_path(container_xml: bytes) -> str:
    """Mirrors ``shared.epub_metadata._find_opf_path`` — kept local so this
    module's import surface stays in our control."""
    root = ET.fromstring(container_xml.decode("utf-8", errors="replace"))
    for rf in root.iter(f"{{{_NS_CONTAINER}}}rootfile"):
        path = rf.get("full-path")
        if path:
            return path
    raise MalformedEPUBError("No rootfile found in container.xml")


def _build_manifest_map(opf_root: ET.Element, opf_dir: str) -> dict[str, str]:
    """Map manifest item ``id`` → resolved spine-readable path."""
    mf = opf_root.find(f"{{{_NS_OPF}}}manifest")
    if mf is None:
        return {}
    out: dict[str, str] = {}
    for item in mf:
        item_id = item.get("id")
        href = item.get("href")
        if not item_id or not href:
            continue
        out[item_id] = f"{opf_dir}/{href}" if opf_dir else href
    return out


def _strip_xml(xhtml: str) -> str:
    """Strip XML/HTML tags + collapse whitespace; cheap text extraction."""
    no_tags = _XML_TAG.sub(" ", xhtml)
    return _WHITESPACE_RUN.sub(" ", no_tags).strip()
