"""Replayable Simplified/Mixed Chinese to Traditional-Taiwan projection.

This stage is deliberately narrower than correction.  It runs the exact,
measured OpenCC ``s2tw`` conversion over one complete Recognition hypothesis,
then projects the equal-length Unicode-scalar result back onto the original
token boundaries.  Raw Recognition Evidence is never rewritten or re-sealed.

``s2twp`` is intentionally not configurable here: its phrase localization can
change valid Taiwan wording (for example ``類型`` to ``型別`` and ``對象`` to
``物件``), which is a semantic/editorial decision rather than orthography.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import sys
import unicodedata
from importlib import resources
from pathlib import Path
from typing import Literal, Sequence

from opencc import OpenCC
from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    RecognitionEvidence,
    recognition_evidence_content_hash,
    recognition_evidence_set_hash,
)

from .hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes

_CONVERSION_NAME = "s2tw"
_DISTRIBUTION_NAME = "opencc-python-reimplemented"
_CONFIG_PATH = "config/s2tw.json"
_DICTIONARY_PATHS = (
    "dictionary/STPhrases.txt",
    "dictionary/STCharacters.txt",
    "dictionary/TWVariants.txt",
)
_RUNTIME_SOURCE_PATHS = ("__init__.py", "opencc.py")
_EXPECTED_CHAIN = ("STPhrases.txt", "STCharacters.txt", "TWVariants.txt")
_PROBES = (
    "类型",
    "约会对象",
    "竞争的对象",
    "头发",
    "干活",
    "干杯",
    "不准",
    "个",
    "Linux 2026類型",
)


class OrthographicProjectionError(ValueError):
    """The projection cannot be reproduced without changing its meaning."""


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class OrthographicInventoryItemV1(_Contract):
    schema_version: Literal[1] = 1
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class OrthographicAmbiguousDictionaryV1(_Contract):
    schema_version: Literal[1] = 1
    path: str
    entry_count: int = Field(ge=0)
    entries_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class OpenCCS2TWIdentityV1(_Contract):
    """Measured trust root for one exact OpenCC ``s2tw`` executable."""

    schema_version: Literal[1] = 1
    name: Literal["s2tw"] = "s2tw"
    implementation: Literal["opencc-python-reimplemented"] = _DISTRIBUTION_NAME
    implementation_version: str
    config_path: Literal["config/s2tw.json"] = _CONFIG_PATH
    dictionary_chain: tuple[Literal["STPhrases.txt", "STCharacters.txt", "TWVariants.txt"], ...]
    inventory: tuple[OrthographicInventoryItemV1, ...]
    inventory_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    ambiguous_dictionaries: tuple[OrthographicAmbiguousDictionaryV1, ...]
    ambiguous_inventory_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    ambiguous_entry_count: int = Field(ge=0)
    multi_option_policy: Literal["first_candidate_with_review_receipt_v1"] = (
        "first_candidate_with_review_receipt_v1"
    )
    adapter_code_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    conversion_probe_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _closed_identity(self) -> OpenCCS2TWIdentityV1:
        if self.dictionary_chain != _EXPECTED_CHAIN:
            raise ValueError("OpenCC s2tw dictionary chain drifted")
        expected_inventory = (_CONFIG_PATH, *_DICTIONARY_PATHS, *_RUNTIME_SOURCE_PATHS)
        if tuple(item.path for item in self.inventory) != expected_inventory:
            raise ValueError("OpenCC s2tw inventory is incomplete or reordered")
        if self.inventory_hash != hash_object(self.inventory):
            raise ValueError("OpenCC s2tw inventory_hash mismatch")
        if self.ambiguous_inventory_hash != hash_object(self.ambiguous_dictionaries):
            raise ValueError("OpenCC ambiguous inventory hash mismatch")
        if self.ambiguous_entry_count != sum(
            item.entry_count for item in self.ambiguous_dictionaries
        ):
            raise ValueError("OpenCC ambiguous entry count mismatch")
        return self


class OrthographicTokenProjectionV1(_Contract):
    schema_version: Literal[1] = 1
    index: int = Field(ge=0)
    source_token_id: str
    source_text: str
    projected_text: str
    source_start_offset: int = Field(ge=0)
    source_end_offset: int = Field(ge=0)
    projected_start_offset: int = Field(ge=0)
    projected_end_offset: int = Field(ge=0)


class OrthographicChangedScalarV1(_Contract):
    schema_version: Literal[1] = 1
    source_token_id: str
    token_index: int = Field(ge=0)
    source_offset: int = Field(ge=0)
    projected_offset: int = Field(ge=0)
    source_token_offset: int = Field(ge=0)
    projected_token_offset: int = Field(ge=0)
    source_scalar: str
    projected_scalar: str


class OrthographicAmbiguityReceiptV1(_Contract):
    """A multi-option dictionary entry actually selected for this source text."""

    schema_version: Literal[1] = 1
    dictionary_path: Literal["dictionary/STPhrases.txt", "dictionary/STCharacters.txt"]
    source_entry: str
    selected_candidate: str
    candidates: tuple[str, ...]
    source_start_offset: int = Field(ge=0)
    source_end_offset: int = Field(gt=0)
    source_token_ids: tuple[str, ...]
    classification: Literal["requires_full_audit"] = "requires_full_audit"

    @model_validator(mode="after")
    def _multi_option(self) -> OrthographicAmbiguityReceiptV1:
        if len(self.candidates) < 2 or len(set(self.candidates)) != len(self.candidates):
            raise ValueError("ambiguity receipt requires distinct multiple candidates")
        if self.selected_candidate != self.candidates[0]:
            raise ValueError("OpenCC selected candidate must be the measured first option")
        if not self.source_token_ids:
            raise ValueError("ambiguity receipt requires source token lineage")
        return self


class OrthographicProjectionEvidenceV1(_Contract):
    """Content-addressed, byte-replayable projection of one raw ASR Evidence."""

    schema_version: Literal[1] = 1
    id: str
    source_recognition_evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_recognition_evidence_set_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_transcript_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    projected_transcript_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    conversion: OpenCCS2TWIdentityV1
    tokens: tuple[OrthographicTokenProjectionV1, ...]
    changes: tuple[OrthographicChangedScalarV1, ...]
    ambiguities: tuple[OrthographicAmbiguityReceiptV1, ...] = ()
    raw_output: ArtifactDigest
    raw_output_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _projection_invariants(self) -> OrthographicProjectionEvidenceV1:
        if not self.tokens:
            raise ValueError("orthographic projection requires source tokens")
        if tuple(item.index for item in self.tokens) != tuple(range(len(self.tokens))):
            raise ValueError("orthographic token projections must be ordered and complete")
        source_cursor = 0
        projected_cursor = 0
        for item in self.tokens:
            if item.source_start_offset != source_cursor:
                raise ValueError("source token offsets are not contiguous")
            if item.projected_start_offset != projected_cursor:
                raise ValueError("projected token offsets are not contiguous")
            if item.source_end_offset - item.source_start_offset != len(item.source_text):
                raise ValueError("source token scalar range mismatch")
            if item.projected_end_offset - item.projected_start_offset != len(item.projected_text):
                raise ValueError("projected token scalar range mismatch")
            source_cursor = item.source_end_offset
            projected_cursor = item.projected_end_offset
        source_text = "".join(item.source_text for item in self.tokens)
        projected_text = "".join(item.projected_text for item in self.tokens)
        if len(source_text) != len(projected_text):
            raise ValueError("orthographic projection changed Unicode scalar length")
        if self.source_transcript_hash != sha256_bytes(source_text.encode("utf-8")):
            raise ValueError("source transcript hash mismatch")
        projected_bytes = projected_text.encode("utf-8")
        if self.projected_transcript_hash != sha256_bytes(projected_bytes):
            raise ValueError("projected transcript hash mismatch")
        if self.raw_output_hash != self.raw_output.sha256:
            raise ValueError("orthographic raw output hash mismatch")
        if self.raw_output.sha256 != sha256_bytes(projected_bytes):
            raise ValueError("orthographic raw output digest mismatch")
        if self.raw_output.size_bytes != len(projected_bytes):
            raise ValueError("orthographic raw output size mismatch")
        expected_changes = _changed_scalars(self.tokens)
        if self.changes != expected_changes:
            raise ValueError("orthographic scalar change receipts are incomplete or reordered")
        _assert_literal_scalars_unchanged(source_text, projected_text)
        if self.content_hash != orthographic_projection_content_hash(self):
            raise ValueError("orthographic projection content_hash mismatch")
        if self.id != f"orthographic-projection-{self.content_hash}":
            raise ValueError("orthographic projection ID is not content addressed")
        return self


def orthographic_projection_content_hash(
    evidence: OrthographicProjectionEvidenceV1,
) -> str:
    payload = evidence.model_dump(mode="json", exclude={"id", "content_hash"})
    return hash_object(payload)


def orthographic_projection_bytes(evidence: OrthographicProjectionEvidenceV1) -> bytes:
    return canonical_json_bytes(evidence)


def orthographic_projection_evidence_set_hash(
    evidence: Sequence[OrthographicProjectionEvidenceV1],
) -> str:
    """Hash a complete unordered set of distinct projected Evidence."""

    hashes = tuple(sorted(item.content_hash for item in evidence))
    if not hashes:
        raise OrthographicProjectionError("orthographic projection Evidence set is empty")
    if len(set(hashes)) != len(hashes):
        raise OrthographicProjectionError(
            "orthographic projection Evidence set contains duplicate content"
        )
    return hash_object(hashes)


def _package_root() -> Path:
    root = resources.files("opencc")
    path = Path(str(root)).resolve()
    if not path.is_dir():
        raise OrthographicProjectionError("OpenCC package resources are not filesystem-backed")
    return path


def _read_dictionary(path: Path) -> dict[str, tuple[str, ...]]:
    entries: dict[str, tuple[str, ...]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            source, raw_candidates = line.split("\t")
        except ValueError as exc:
            raise OrthographicProjectionError(
                f"invalid OpenCC dictionary line {path.name}:{line_number}"
            ) from exc
        candidates = tuple(raw_candidates.split(" "))
        if not source or not candidates or any(not value for value in candidates):
            raise OrthographicProjectionError(
                f"invalid OpenCC dictionary entry {path.name}:{line_number}"
            )
        if source in entries:
            raise OrthographicProjectionError(f"duplicate OpenCC dictionary key {source!r}")
        entries[source] = candidates
    return entries


def _assert_literal_scalars_unchanged(source_text: str, projected_text: str) -> None:
    """Protect literals that an orthographic converter has no authority to edit."""

    for offset, (source, projected) in enumerate(zip(source_text, projected_text, strict=True)):
        category = unicodedata.category(source)
        name = unicodedata.name(source, "")
        protected = source.isascii() or "LATIN" in name or category.startswith("N")
        if protected and source != projected:
            raise OrthographicProjectionError(
                "OpenCC s2tw changed protected ASCII/Latin/numeric scalar at "
                f"offset {offset}: {source!r} -> {projected!r}"
            )


def _dictionary_chain(config: object) -> tuple[str, ...]:
    if not isinstance(config, dict):
        raise OrthographicProjectionError("OpenCC s2tw config must be an object")
    segmentation = config.get("segmentation")
    if not isinstance(segmentation, dict) or segmentation.get("type") != "mmseg":
        raise OrthographicProjectionError("OpenCC s2tw segmentation drifted")
    segmentation_dict = segmentation.get("dict")
    if not isinstance(segmentation_dict, dict) or segmentation_dict.get("file") != (
        "STPhrases.txt"
    ):
        raise OrthographicProjectionError("OpenCC s2tw segmentation dictionary drifted")
    chain = config.get("conversion_chain")
    if not isinstance(chain, list) or len(chain) != 2:
        raise OrthographicProjectionError("OpenCC s2tw conversion chain drifted")
    try:
        first = chain[0]["dict"]
        second = chain[1]["dict"]
        names = tuple(item["file"] for item in first["dicts"]) + (second["file"],)
    except (KeyError, TypeError) as exc:
        raise OrthographicProjectionError("OpenCC s2tw conversion chain is malformed") from exc
    if first.get("type") != "group" or second.get("type") != "txt":
        raise OrthographicProjectionError("OpenCC s2tw conversion chain types drifted")
    if names != _EXPECTED_CHAIN:
        raise OrthographicProjectionError("OpenCC s2tw dictionary chain drifted")
    return names


def measure_opencc_s2tw_identity() -> OpenCCS2TWIdentityV1:
    """Measure config, dictionary, code, runtime and behavior from installed bytes."""

    root = _package_root()
    config_path = root / _CONFIG_PATH
    config = json.loads(config_path.read_text(encoding="utf-8"))
    dictionary_chain = _dictionary_chain(config)
    inventory_paths = (_CONFIG_PATH, *_DICTIONARY_PATHS, *_RUNTIME_SOURCE_PATHS)
    inventory = tuple(
        OrthographicInventoryItemV1(
            path=relative,
            sha256=hash_file(root / relative),
            size_bytes=(root / relative).stat().st_size,
        )
        for relative in inventory_paths
    )
    ambiguous_dictionaries: list[OrthographicAmbiguousDictionaryV1] = []
    for relative in _DICTIONARY_PATHS:
        entries = _read_dictionary(root / relative)
        ambiguous = tuple(
            (source, candidates) for source, candidates in entries.items() if len(candidates) > 1
        )
        ambiguous_dictionaries.append(
            OrthographicAmbiguousDictionaryV1(
                path=relative,
                entry_count=len(ambiguous),
                entries_hash=hash_object(ambiguous),
            )
        )
    source_inventory = tuple(
        (relative, hash_file(root / relative), (root / relative).stat().st_size)
        for relative in _RUNTIME_SOURCE_PATHS
    )
    version = importlib.metadata.version(_DISTRIBUTION_NAME)
    runtime_hash = hash_object(
        {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "python_cache_tag": sys.implementation.cache_tag,
            "platform": platform.platform(),
            "opencc_distribution": _DISTRIBUTION_NAME,
            "opencc_version": version,
            "opencc_source_inventory": source_inventory,
        }
    )
    converter = OpenCC(_CONVERSION_NAME)
    probes = tuple((source, converter.convert(source)) for source in _PROBES)
    return OpenCCS2TWIdentityV1(
        implementation_version=version,
        dictionary_chain=dictionary_chain,
        inventory=inventory,
        inventory_hash=hash_object(inventory),
        ambiguous_dictionaries=tuple(ambiguous_dictionaries),
        ambiguous_inventory_hash=hash_object(tuple(ambiguous_dictionaries)),
        ambiguous_entry_count=sum(item.entry_count for item in ambiguous_dictionaries),
        adapter_code_hash=hash_file(__file__),
        runtime_hash=runtime_hash,
        conversion_probe_hash=hash_object(probes),
    )


def _find_dictionary_matches(
    text: str,
    entries: dict[str, tuple[str, ...]],
    *,
    eligible_ranges: Sequence[tuple[int, int]],
) -> tuple[tuple[int, int, str, tuple[str, ...]], ...]:
    """Reproduce OpenCC's longest-key/first-position selection for one dictionary."""

    if not entries:
        return ()
    maximum = max(map(len, entries))
    minimum = min(map(len, entries))
    matches: list[tuple[int, int, str, tuple[str, ...]]] = []
    pending = list(reversed(tuple(eligible_ranges)))
    while pending:
        range_start, range_end = pending.pop()
        segment = text[range_start:range_end]
        found: tuple[int, int, str, tuple[str, ...]] | None = None
        for width in range(min(maximum, len(segment)), minimum - 1, -1):
            for relative_start in range(0, len(segment) - width + 1):
                source = segment[relative_start : relative_start + width]
                candidates = entries.get(source)
                if candidates is not None:
                    start = range_start + relative_start
                    found = (start, start + width, source, candidates)
                    break
            if found is not None:
                break
        if found is None:
            continue
        start, end, source, candidates = found
        matches.append(found)
        # OpenCC pushes left then right on a LIFO stack, so process right first.
        if range_start < start:
            pending.append((range_start, start))
        if end < range_end:
            pending.append((end, range_end))
    return tuple(sorted(matches))


def _first_chain_matches(
    text: str,
) -> tuple[tuple[str, int, int, str, tuple[str, ...]], ...]:
    root = _package_root()
    phrase_path = _DICTIONARY_PATHS[0]
    character_path = _DICTIONARY_PATHS[1]
    phrase_entries = _read_dictionary(root / phrase_path)
    character_entries = _read_dictionary(root / character_path)
    phrase_matches = _find_dictionary_matches(
        text,
        phrase_entries,
        eligible_ranges=((0, len(text)),),
    )
    gaps: list[tuple[int, int]] = []
    cursor = 0
    for start, end, _, _ in phrase_matches:
        if cursor < start:
            gaps.append((cursor, start))
        cursor = end
    if cursor < len(text):
        gaps.append((cursor, len(text)))
    character_matches = _find_dictionary_matches(
        text,
        character_entries,
        eligible_ranges=tuple(gaps),
    )
    return tuple(
        sorted(
            (
                *(
                    (phrase_path, start, end, source, candidates)
                    for start, end, source, candidates in phrase_matches
                ),
                *(
                    (character_path, start, end, source, candidates)
                    for start, end, source, candidates in character_matches
                ),
            ),
            key=lambda item: (item[1], item[2], item[0]),
        )
    )


def _token_ids_for_range(
    tokens: Sequence[OrthographicTokenProjectionV1], start: int, end: int
) -> tuple[str, ...]:
    return tuple(
        token.source_token_id
        for token in tokens
        if token.source_start_offset < end and start < token.source_end_offset
    )


def _changed_scalars(
    tokens: Sequence[OrthographicTokenProjectionV1],
) -> tuple[OrthographicChangedScalarV1, ...]:
    changes: list[OrthographicChangedScalarV1] = []
    for token in tokens:
        if len(token.source_text) != len(token.projected_text):
            raise OrthographicProjectionError("OpenCC s2tw changed a token's Unicode scalar length")
        for offset, (source, projected) in enumerate(
            zip(token.source_text, token.projected_text, strict=True)
        ):
            if source == projected:
                continue
            changes.append(
                OrthographicChangedScalarV1(
                    source_token_id=token.source_token_id,
                    token_index=token.index,
                    source_offset=token.source_start_offset + offset,
                    projected_offset=token.projected_start_offset + offset,
                    source_token_offset=offset,
                    projected_token_offset=offset,
                    source_scalar=source,
                    projected_scalar=projected,
                )
            )
    return tuple(changes)


def project_full_text_exact_length(
    source_parts: Sequence[str], projected_text: str
) -> tuple[str, ...]:
    """Project a full-string result back to token boundaries or fail closed."""

    parts = tuple(source_parts)
    source_text = "".join(parts)
    if len(projected_text) != len(source_text):
        raise OrthographicProjectionError(
            "OpenCC s2tw changed Unicode scalar length; token timing cannot be replayed safely"
        )
    _assert_literal_scalars_unchanged(source_text, projected_text)
    boundaries: list[str] = []
    cursor = 0
    for part in parts:
        next_cursor = cursor + len(part)
        boundaries.append(projected_text[cursor:next_cursor])
        cursor = next_cursor
    return tuple(boundaries)


def project_recognition_evidence(
    evidence: RecognitionEvidence,
    *,
    source_evidence_set_hash: str | None = None,
) -> OrthographicProjectionEvidenceV1:
    """Create immutable projected Evidence while retaining exact raw lineage."""

    source_hash = recognition_evidence_content_hash(evidence)
    set_hash = source_evidence_set_hash or recognition_evidence_set_hash((evidence,))
    source_parts = tuple(token.text for token in evidence.tokens)
    source_text = "".join(source_parts)
    identity = measure_opencc_s2tw_identity()
    projected_text = OpenCC(_CONVERSION_NAME).convert(source_text)
    projected_parts = project_full_text_exact_length(source_parts, projected_text)
    token_projections: list[OrthographicTokenProjectionV1] = []
    source_cursor = 0
    projected_cursor = 0
    for index, (source_token, projected_part) in enumerate(
        zip(evidence.tokens, projected_parts, strict=True)
    ):
        token_projections.append(
            OrthographicTokenProjectionV1(
                index=index,
                source_token_id=source_token.id,
                source_text=source_token.text,
                projected_text=projected_part,
                source_start_offset=source_cursor,
                source_end_offset=source_cursor + len(source_token.text),
                projected_start_offset=projected_cursor,
                projected_end_offset=projected_cursor + len(projected_part),
            )
        )
        source_cursor += len(source_token.text)
        projected_cursor += len(projected_part)
    tokens = tuple(token_projections)
    ambiguities = tuple(
        OrthographicAmbiguityReceiptV1(
            dictionary_path=dictionary_path,
            source_entry=source_entry,
            selected_candidate=candidates[0],
            candidates=candidates,
            source_start_offset=start,
            source_end_offset=end,
            source_token_ids=_token_ids_for_range(tokens, start, end),
        )
        for dictionary_path, start, end, source_entry, candidates in _first_chain_matches(
            source_text
        )
        if len(candidates) > 1
    )
    projected_bytes = projected_text.encode("utf-8")
    raw_hash = sha256_bytes(projected_bytes)
    payload = {
        "source_recognition_evidence_hash": source_hash,
        "source_recognition_evidence_set_hash": set_hash,
        "source_transcript_hash": sha256_bytes(source_text.encode("utf-8")),
        "projected_transcript_hash": raw_hash,
        "conversion": identity,
        "tokens": tokens,
        "changes": _changed_scalars(tokens),
        "ambiguities": ambiguities,
        "raw_output": ArtifactDigest(
            uri=f"generation-artifact://orthographic_projection/raw/{raw_hash}.txt",
            sha256=raw_hash,
            size_bytes=len(projected_bytes),
        ),
        "raw_output_hash": raw_hash,
    }
    content_hash = hash_object({"schema_version": 1, **payload})
    return OrthographicProjectionEvidenceV1(
        id=f"orthographic-projection-{content_hash}",
        content_hash=content_hash,
        **payload,
    )


def orthographic_projection_raw_output_bytes(
    evidence: OrthographicProjectionEvidenceV1,
) -> bytes:
    """Return the exact synthetic adapter output bytes addressed by the receipt."""

    output = "".join(item.projected_text for item in evidence.tokens).encode("utf-8")
    if (
        sha256_bytes(output) != evidence.raw_output.sha256
        or len(output) != evidence.raw_output.size_bytes
    ):
        raise OrthographicProjectionError("orthographic raw output digest is not reproducible")
    return output


def verify_orthographic_projection(
    projected: OrthographicProjectionEvidenceV1,
    source_evidence: Sequence[RecognitionEvidence],
    *,
    raw_output: bytes | None = None,
) -> None:
    """Rerun installed bytes and require object-identical projected Evidence."""

    evidence = tuple(source_evidence)
    expected_set_hash = recognition_evidence_set_hash(evidence)
    if projected.source_recognition_evidence_set_hash != expected_set_hash:
        raise OrthographicProjectionError("projection Recognition Evidence set hash mismatch")
    matches = tuple(
        item
        for item in evidence
        if recognition_evidence_content_hash(item) == projected.source_recognition_evidence_hash
    )
    if len(matches) != 1:
        raise OrthographicProjectionError(
            "projection must bind exactly one member of the supplied Recognition Evidence set"
        )
    replayed = project_recognition_evidence(matches[0], source_evidence_set_hash=expected_set_hash)
    if replayed != projected:
        raise OrthographicProjectionError(
            "orthographic projection differs from fresh measured s2tw replay"
        )
    expected_output = orthographic_projection_raw_output_bytes(projected)
    if raw_output is not None and raw_output != expected_output:
        raise OrthographicProjectionError(
            "persisted orthographic raw output differs from its measured projection"
        )


def project_recognition_evidence_set(
    source_evidence: Sequence[RecognitionEvidence],
) -> tuple[OrthographicProjectionEvidenceV1, ...]:
    evidence = tuple(source_evidence)
    set_hash = recognition_evidence_set_hash(evidence)
    projected = tuple(
        project_recognition_evidence(item, source_evidence_set_hash=set_hash) for item in evidence
    )
    for item in projected:
        verify_orthographic_projection(item, evidence)
    return projected


__all__ = [
    "OpenCCS2TWIdentityV1",
    "OrthographicAmbiguityReceiptV1",
    "OrthographicChangedScalarV1",
    "OrthographicProjectionError",
    "OrthographicProjectionEvidenceV1",
    "OrthographicTokenProjectionV1",
    "measure_opencc_s2tw_identity",
    "orthographic_projection_bytes",
    "orthographic_projection_content_hash",
    "orthographic_projection_evidence_set_hash",
    "orthographic_projection_raw_output_bytes",
    "project_full_text_exact_length",
    "project_recognition_evidence",
    "project_recognition_evidence_set",
    "verify_orthographic_projection",
]
