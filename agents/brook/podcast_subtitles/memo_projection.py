"""Project corrected timed tokens onto Memo's native subtitle boundaries.

The correction token stream is the only text authority.  Memo contributes
display-cue times and candidate boundaries; its recognised words are used only
to align those boundaries.  A boundary that falls inside one correction token
is moved to a neighbouring token edge, never through the token itself.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.brook.podcast_subtitles.accurate_correction import (  # noqa: E402
    AccurateCorrectionResult,
    parse_accurate_correction_json,
)
from agents.brook.podcast_subtitles.episode_edits import (  # noqa: E402
    EpisodeTranscriptEdit,
    apply_episode_transcript_edits,
)

_SRT_TIME_RE = re.compile(r"^(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})$")


class BoundaryRetentionError(ValueError):
    """Raised when too few Memo boundaries survive token-safe projection."""


@dataclass(frozen=True, slots=True)
class SourceToken:
    id: str
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class MemoCue:
    index: int
    start_ms: int
    end_ms: int
    text: str
    source_indexes: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectedCue:
    index: int
    start_ms: int
    end_ms: int
    text: str
    token_ids: tuple[str, ...]
    memo_cue_indexes: tuple[int, ...]
    boundary_provenance: str


@dataclass(frozen=True, slots=True)
class AlignmentMetrics:
    source_characters: int
    target_characters: int
    matching_characters: int
    ratio: float
    source_text: str
    target_text: str


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    cues: tuple[ProjectedCue, ...]
    alignment: AlignmentMetrics
    original_boundary_count: int
    retained_boundary_count: int
    boundary_retention_ratio: float
    alignment_non_equal_boundary_count: int
    token_snap_boundary_count: int
    merged_boundary_count: int
    dropped_empty_cue_count: int
    boundary_deltas_ms: tuple[int, ...]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _time_to_ms(value: str) -> int:
    match = _SRT_TIME_RE.match(value.strip())
    if match is None:
        raise ValueError(f"invalid SRT timestamp: {value!r}")
    hours, minutes, seconds, millis = map(int, match.groups())
    return (((hours * 60) + minutes) * 60 + seconds) * 1000 + millis


def _ms_to_time(value: int) -> str:
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def parse_srt(payload: str) -> tuple[MemoCue, ...]:
    blocks = re.split(r"\r?\n\s*\r?\n", payload.lstrip("\ufeff").strip())
    cues: list[MemoCue] = []
    for expected_index, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        if len(lines) < 3:
            raise ValueError(f"SRT block {expected_index} has no content line")
        try:
            index = int(lines[0].strip())
        except ValueError as exc:
            raise ValueError(f"invalid SRT index in block {expected_index}") from exc
        timing = re.split(r"\s*-->\s*", lines[1].strip())
        if len(timing) != 2:
            raise ValueError(f"invalid SRT timing in block {expected_index}")
        text = "".join(line.strip() for line in lines[2:])
        start_ms, end_ms = map(_time_to_ms, timing)
        if not text:
            raise ValueError(f"Memo SRT cue {index} is empty")
        if end_ms <= start_ms:
            raise ValueError(f"Memo SRT cue {index} has non-positive duration")
        if cues and start_ms < cues[-1].end_ms:
            raise ValueError(f"Memo SRT cue {index} overlaps its predecessor")
        cues.append(MemoCue(index, start_ms, end_ms, text))
    if not cues:
        raise ValueError("Memo SRT has no cues")
    return tuple(cues)


def _memo_source_indexes(cue: MemoCue) -> tuple[int, ...]:
    return cue.source_indexes or (cue.index,)


def load_boundary_merge_proposals(path: Path) -> tuple[dict[str, object], ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("boundary_proposals")
    if not isinstance(raw, list):
        raise ValueError("boundary edits JSON must be an array or contain boundary_proposals")
    proposals: list[dict[str, object]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"boundary proposal {index} must be an object")
        if item.get("action") != "merge":
            raise ValueError(f"boundary proposal {index} only supports action=merge")
        cue_ids = item.get("cue_ids")
        if (
            not isinstance(cue_ids, list)
            or len(cue_ids) < 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in cue_ids)
        ):
            raise ValueError(f"boundary proposal {index} requires 2+ integer cue_ids")
        proposals.append(item)
    return tuple(proposals)


def apply_boundary_merge_proposals(
    cues: Sequence[MemoCue], proposals: Sequence[dict[str, object]]
) -> tuple[MemoCue, ...]:
    """Apply disjoint adjacent raw-Memo cue merges without changing text."""

    cues = tuple(cues)
    position_by_id = {cue.index: position for position, cue in enumerate(cues)}
    if len(position_by_id) != len(cues):
        raise ValueError("Memo cue indexes must be unique")
    merge_by_first_position: dict[int, tuple[int, dict[str, object]]] = {}
    occupied: set[int] = set()
    for proposal_index, proposal in enumerate(proposals):
        cue_ids = tuple(proposal["cue_ids"])  # type: ignore[arg-type]
        missing = [cue_id for cue_id in cue_ids if cue_id not in position_by_id]
        if missing:
            raise ValueError(
                f"boundary proposal {proposal_index} references missing cues {missing}"
            )
        positions = tuple(position_by_id[cue_id] for cue_id in cue_ids)
        expected = tuple(range(positions[0], positions[0] + len(positions)))
        if positions != expected:
            raise ValueError(
                f"boundary proposal {proposal_index} cue_ids are not adjacent and ordered"
            )
        if occupied.intersection(positions):
            raise ValueError(f"boundary proposal {proposal_index} overlaps another merge")
        selected = cues[positions[0] : positions[-1] + 1]
        current_lines = proposal.get("current_lines")
        if current_lines is not None and current_lines != [cue.text for cue in selected]:
            raise ValueError(f"boundary proposal {proposal_index} current_lines are stale")
        concatenated = "".join(cue.text for cue in selected)
        if proposal.get("result_line") is not None and proposal["result_line"] != concatenated:
            raise ValueError(f"boundary proposal {proposal_index} result_line changes Memo text")
        occupied.update(positions)
        merge_by_first_position[positions[0]] = (positions[-1], proposal)

    merged: list[MemoCue] = []
    position = 0
    while position < len(cues):
        record = merge_by_first_position.get(position)
        if record is None:
            merged.append(cues[position])
            position += 1
            continue
        last_position, _ = record
        selected = cues[position : last_position + 1]
        merged.append(
            MemoCue(
                index=selected[0].index,
                start_ms=selected[0].start_ms,
                end_ms=selected[-1].end_ms,
                text="".join(cue.text for cue in selected),
                source_indexes=tuple(
                    source_index for cue in selected for source_index in _memo_source_indexes(cue)
                ),
            )
        )
        position = last_position + 1
    if "".join(cue.text for cue in merged) != "".join(cue.text for cue in cues):
        raise ValueError("boundary merges changed Memo recognition text")
    return tuple(merged)


def _target_boundary_characters(
    source_text: str,
    target_text: str,
    source_boundaries: Sequence[int],
) -> tuple[tuple[int, ...], tuple[bool, ...], AlignmentMetrics]:
    matcher = difflib.SequenceMatcher(None, source_text, target_text, autojunk=False)
    opcodes = matcher.get_opcodes()
    matching_characters = sum(i2 - i1 for tag, i1, i2, _, _ in opcodes if tag == "equal")
    mapped: list[int] = []
    alignment_uncertain: list[bool] = []
    opcode_index = 0
    for boundary in source_boundaries:
        while opcode_index + 1 < len(opcodes) and boundary > opcodes[opcode_index][2]:
            opcode_index += 1
        tag, i1, i2, j1, j2 = opcodes[opcode_index]
        # A boundary is stable only when both adjacent source characters live
        # inside the same equal island.  Touching a replace/insert/delete edge
        # is retained as Memo evidence, but called out for later review.
        alignment_uncertain.append(tag != "equal" or not i1 < boundary < i2)
        if tag == "equal":
            target = j1 + min(max(boundary - i1, 0), j2 - j1)
        elif i2 == i1:
            target = j2
        else:
            fraction = (boundary - i1) / (i2 - i1)
            target = j1 + round(fraction * (j2 - j1))
        mapped.append(min(max(target, 0), len(target_text)))
    ratio = (
        (2 * matching_characters / (len(source_text) + len(target_text)))
        if (source_text or target_text)
        else 1.0
    )
    return (
        tuple(mapped),
        tuple(alignment_uncertain),
        AlignmentMetrics(
            source_characters=len(source_text),
            target_characters=len(target_text),
            matching_characters=matching_characters,
            ratio=ratio,
            source_text=source_text,
            target_text=target_text,
        ),
    )


def _alignment_text(value: str) -> str:
    """Return length-stable comparison units without changing output text.

    NFKC and case-folding remove presentation-only variation. Punctuation and
    whitespace are excluded because Memo and correction disagree on whether
    those characters are tokens.  We intentionally do not convert simplified
    and traditional Chinese here: such differences remain visible as
    alignment uncertainty instead of becoming an undeclared textual edit.
    """

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _token_character_edges(tokens: Sequence[SourceToken]) -> tuple[int, ...]:
    edges = [0]
    for token in tokens:
        if not token.text:
            raise ValueError(f"source token {token.id!r} has empty text")
        if "\n" in token.text or "\r" in token.text:
            raise ValueError(f"source token {token.id!r} contains a line break")
        if token.end_ms <= token.start_ms:
            raise ValueError(f"source token {token.id!r} has non-positive duration")
        edges.append(edges[-1] + len(token.text))
    return tuple(edges)


def _nearest_token_edge(
    target_character: int,
    token_edges: Sequence[int],
    tokens: Sequence[SourceToken],
    memo_time_ms: int,
) -> int:
    import bisect

    left_position = bisect.bisect_left(token_edges, target_character)
    right_position = bisect.bisect_right(token_edges, target_character)
    if left_position < right_position:
        candidate_indexes = set(range(left_position, right_position))
    else:
        candidate_indexes = {
            max(0, left_position - 1),
            min(len(token_edges) - 1, left_position),
        }

    def rank(edge_index: int) -> tuple[int, int, int]:
        if edge_index == 0:
            token_time = tokens[0].start_ms
        elif edge_index == len(tokens):
            token_time = tokens[-1].end_ms
        else:
            token_time = (tokens[edge_index - 1].end_ms + tokens[edge_index].start_ms) // 2
        return (
            abs(token_edges[edge_index] - target_character),
            abs(token_time - memo_time_ms),
            edge_index,
        )

    return min(candidate_indexes, key=rank)


def project_tokens_to_memo_cues(
    tokens: Sequence[SourceToken],
    memo_cues: Sequence[MemoCue],
    *,
    min_boundary_retention: float = 0.95,
    min_alignment_ratio: float = 0.90,
) -> ProjectionResult:
    tokens = tuple(tokens)
    memo_cues = tuple(memo_cues)
    if not tokens or not memo_cues:
        raise ValueError("projection requires source tokens and Memo cues")
    if not 0 <= min_boundary_retention <= 1 or not 0 <= min_alignment_ratio <= 1:
        raise ValueError("thresholds must be within 0..1")
    _token_character_edges(tokens)  # validates source tokens before alignment
    memo_alignment_parts = tuple(_alignment_text(cue.text) for cue in memo_cues)
    token_alignment_parts = tuple(_alignment_text(token.text) for token in tokens)
    memo_text = "".join(memo_alignment_parts)
    canonical_alignment_text = "".join(token_alignment_parts)
    if not canonical_alignment_text or not memo_text:
        raise ValueError("source and Memo text require alphanumeric alignment units")
    memo_char_edges: list[int] = []
    cursor = 0
    for cue_text in memo_alignment_parts[:-1]:
        cursor += len(cue_text)
        memo_char_edges.append(cursor)
    mapped_characters, alignment_uncertain, alignment = _target_boundary_characters(
        memo_text, canonical_alignment_text, memo_char_edges
    )
    if alignment.ratio < min_alignment_ratio:
        raise ValueError(
            f"global text alignment ratio {alignment.ratio:.6f} is below {min_alignment_ratio:.6f}"
        )

    projected_edges: list[int] = []
    token_alignment_edges = [0]
    for part in token_alignment_parts:
        token_alignment_edges.append(token_alignment_edges[-1] + len(part))
    token_snap = 0
    deltas: list[int] = []
    for index, (memo_boundary, mapped_character) in enumerate(
        zip(memo_cues[:-1], mapped_characters, strict=True)
    ):
        edge_index = _nearest_token_edge(
            mapped_character, token_alignment_edges, tokens, memo_boundary.end_ms
        )
        projected_edges.append(edge_index)
        if token_alignment_edges[edge_index] != mapped_character:
            token_snap += 1
        if edge_index == 0:
            projected_time = tokens[0].start_ms
        elif edge_index == len(tokens):
            projected_time = tokens[-1].end_ms
        else:
            projected_time = (tokens[edge_index - 1].end_ms + tokens[edge_index].start_ms) // 2
        deltas.append(projected_time - memo_boundary.end_ms)

    original = max(0, len(memo_cues) - 1)
    retained = 0
    last_retained_edge = 0
    for edge in projected_edges:
        if last_retained_edge < edge < len(tokens):
            retained += 1
            last_retained_edge = edge
    retention = retained / original if original else 1.0
    if retention < min_boundary_retention:
        raise BoundaryRetentionError(
            f"Memo boundary retention {retention:.6f} is below {min_boundary_retention:.6f}"
        )

    groups: list[tuple[int, int, int, int]] = []
    token_start = 0
    memo_start = 0
    for memo_boundary_index, edge in enumerate(projected_edges, start=1):
        if edge <= token_start or edge >= len(tokens):
            continue
        groups.append((token_start, edge, memo_start, memo_boundary_index))
        token_start = edge
        memo_start = memo_boundary_index
    groups.append((token_start, len(tokens), memo_start, len(memo_cues)))

    projected: list[ProjectedCue] = []
    for output_index, (first_token, last_token, first_memo, last_memo) in enumerate(
        groups, start=1
    ):
        contributing = memo_cues[first_memo:last_memo]
        if not contributing:
            raise ValueError("projection created a cue without Memo timing evidence")
        text = "".join(token.text for token in tokens[first_token:last_token])
        projected.append(
            ProjectedCue(
                index=output_index,
                start_ms=contributing[0].start_ms,
                end_ms=contributing[-1].end_ms,
                text=text,
                token_ids=tuple(token.id for token in tokens[first_token:last_token]),
                memo_cue_indexes=tuple(
                    source_index
                    for cue in contributing
                    for source_index in _memo_source_indexes(cue)
                ),
                boundary_provenance=(
                    "memo_native_exact"
                    if sum(len(_memo_source_indexes(cue)) for cue in contributing) == 1
                    else "memo_native_adjacent_merge"
                ),
            )
        )
    merged = len(memo_cues) - len(projected)
    result = ProjectionResult(
        cues=tuple(projected),
        alignment=alignment,
        original_boundary_count=original,
        retained_boundary_count=retained,
        boundary_retention_ratio=retention,
        alignment_non_equal_boundary_count=sum(alignment_uncertain),
        token_snap_boundary_count=token_snap,
        merged_boundary_count=merged,
        dropped_empty_cue_count=merged,
        boundary_deltas_ms=tuple(deltas),
    )
    _validate_projection(tokens, result)
    return result


def _validate_projection(tokens: Sequence[SourceToken], result: ProjectionResult) -> None:
    canonical_text = "".join(token.text for token in tokens)
    if "".join(cue.text for cue in result.cues) != canonical_text:
        raise ValueError("candidate text is not an exact canonical copy")
    if [token.id for token in tokens] != [
        token_id for cue in result.cues for token_id in cue.token_ids
    ]:
        raise ValueError("candidate token lineage is incomplete or reordered")
    for index, cue in enumerate(result.cues):
        if not cue.text or "\n" in cue.text or "\r" in cue.text:
            raise ValueError("candidate cue is empty or multi-line")
        if cue.end_ms <= cue.start_ms:
            raise ValueError("candidate cue has non-positive duration")
        if index and cue.start_ms < result.cues[index - 1].end_ms:
            raise ValueError("candidate cues overlap")


def render_srt(cues: Sequence[ProjectedCue]) -> str:
    return (
        "\n\n".join(
            f"{cue.index}\n{_ms_to_time(cue.start_ms)} --> {_ms_to_time(cue.end_ms)}\n{cue.text}"
            for cue in cues
        )
        + "\n"
    )


def _load_episode_edits(path: Path) -> tuple[EpisodeTranscriptEdit, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("episode edits JSON root must be an array")
    allowed = {"id", "start_ms", "end_ms", "current", "replacement", "evidence", "confidence"}
    edits: list[EpisodeTranscriptEdit] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) - allowed:
            raise ValueError(f"episode edit {index} does not match EpisodeTranscriptEdit schema")
        edits.append(EpisodeTranscriptEdit(**item))
    return tuple(edits)


def _source_tokens(correction: AccurateCorrectionResult) -> tuple[SourceToken, ...]:
    return tuple(
        SourceToken(token.id, token.text, token.start_ms, token.end_ms)
        for token in correction.tokens
    )


def _percentile_95(values: Sequence[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(abs(value) for value in values)
    return float(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--correction", type=Path, required=True)
    parser.add_argument("--memo-srt", type=Path, required=True)
    parser.add_argument("--output-srt", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--episode-edits-json", type=Path)
    parser.add_argument("--boundary-edits-json", type=Path)
    parser.add_argument("--min-boundary-retention", type=float, default=0.95)
    parser.add_argument("--min-alignment-ratio", type=float, default=0.90)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    correction_payload = args.correction.read_bytes()
    memo_payload = args.memo_srt.read_bytes()
    correction = parse_accurate_correction_json(correction_payload)
    original_text = correction.text
    edits: tuple[EpisodeTranscriptEdit, ...] = ()
    edits_hash: str | None = None
    if args.episode_edits_json is not None:
        edits_hash = _sha256_file(args.episode_edits_json)
        edits = _load_episode_edits(args.episode_edits_json)
        correction = apply_episode_transcript_edits(correction, edits)
    tokens = _source_tokens(correction)
    memo_cues = parse_srt(memo_payload.decode("utf-8-sig"))
    raw_memo_cue_count = len(memo_cues)
    boundary_edits: tuple[dict[str, object], ...] = ()
    boundary_edits_hash: str | None = None
    if args.boundary_edits_json is not None:
        boundary_edits_hash = _sha256_file(args.boundary_edits_json)
        boundary_edits = load_boundary_merge_proposals(args.boundary_edits_json)
        memo_cues = apply_boundary_merge_proposals(memo_cues, boundary_edits)
    result = project_tokens_to_memo_cues(
        tokens,
        memo_cues,
        min_boundary_retention=args.min_boundary_retention,
        min_alignment_ratio=args.min_alignment_ratio,
    )
    rendered = render_srt(result.cues).encode("utf-8")
    args.output_srt.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_srt.write_bytes(rendered)
    canonical_text = correction.text
    sidecar = {
        "schema_version": "memo-corrected-projection.v1",
        "episode_id": correction.episode_id,
        "normalized_audio_hash": correction.normalized_audio_hash,
        "inputs": {
            "correction": {
                "path": str(args.correction.resolve()),
                "sha256": _sha256_bytes(correction_payload),
            },
            "memo_srt": {
                "path": str(args.memo_srt.resolve()),
                "sha256": _sha256_bytes(memo_payload),
            },
            "episode_edits": {
                "path": None
                if args.episode_edits_json is None
                else str(args.episode_edits_json.resolve()),
                "sha256": edits_hash,
                "count": len(edits),
            },
            "boundary_edits": {
                "path": None
                if args.boundary_edits_json is None
                else str(args.boundary_edits_json.resolve()),
                "sha256": boundary_edits_hash,
                "count": len(boundary_edits),
            },
        },
        "derived": {
            "source_canonical_text_sha256": _sha256_bytes(original_text.encode("utf-8")),
            "canonical_text_sha256": _sha256_bytes(canonical_text.encode("utf-8")),
            "canonical_token_count": len(tokens),
            "canonical_character_count": len(canonical_text),
        },
        "alignment": {
            key: value
            for key, value in asdict(result.alignment).items()
            if key not in {"source_text", "target_text"}
        },
        "boundaries": {
            "raw_memo_cue_count": raw_memo_cue_count,
            "memo_cue_count_after_requested_merges": len(memo_cues),
            "candidate_cue_count": len(result.cues),
            "original_boundary_count": result.original_boundary_count,
            "retained_boundary_count": result.retained_boundary_count,
            "retention_ratio": result.boundary_retention_ratio,
            "alignment_non_equal_boundary_count": result.alignment_non_equal_boundary_count,
            "token_snap_boundary_count": result.token_snap_boundary_count,
            "merged_boundary_count": result.merged_boundary_count,
            "dropped_empty_cue_count": result.dropped_empty_cue_count,
            "boundary_delta_ms_p95": _percentile_95(result.boundary_deltas_ms),
        },
        "qc": {
            "exact_canonical_copy": "".join(cue.text for cue in result.cues) == canonical_text,
            "single_line_cues": all(
                "\n" not in cue.text and "\r" not in cue.text for cue in result.cues
            ),
            "empty_cues": sum(not cue.text for cue in result.cues),
            "nonpositive_cues": sum(cue.end_ms <= cue.start_ms for cue in result.cues),
            "overlapping_cues": sum(
                result.cues[i].start_ms < result.cues[i - 1].end_ms
                for i in range(1, len(result.cues))
            ),
            "split_source_tokens": False,
            "max_characters_per_cue": max(len(cue.text) for cue in result.cues),
        },
        "outputs": {
            "candidate_srt": {
                "path": str(args.output_srt.resolve()),
                "sha256": _sha256_bytes(rendered),
            },
        },
        "cues": [asdict(cue) for cue in result.cues],
    }
    args.output_json.write_text(
        json.dumps(sidecar, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "candidate_srt": str(args.output_srt),
                "candidate_cues": len(result.cues),
                "alignment_ratio": result.alignment.ratio,
                "boundary_retention_ratio": result.boundary_retention_ratio,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
