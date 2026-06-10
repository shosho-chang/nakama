"""Pose-aware host cutout casting for thumbnail production.

The legacy thumbnail renderer selects a random PNG from
``Attachments/cutouts/shosho/{emotion}/``. That is too coarse for Ali Abdaal /
Jeff Su style thumbnails, where a mild thoughtful look and an extreme reaction
face can live under the same broad "surprised" or "thoughtful" bucket.

This module reads a pose manifest generated from the host cutout library and
returns ranked candidates with explicit reasons. If no manifest exists, callers
can keep using the legacy picker.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MANIFEST = _REPO_ROOT / "data" / "thumbnail_cutouts" / "shosho_pose_manifest.json"
_MANIFEST_ENV = "NAKAMA_CUTOUT_POSE_MANIFEST"

_INTENSITY_ORDER = {
    "subtle": 0,
    "mild": 1,
    "medium": 2,
    "high": 3,
    "extreme": 4,
}
_CREDIBILITY_ORDER = {
    "low": 0,
    "medium": 1,
    "high": 2,
}

_EMOTION_DEFAULTS: dict[str, dict[str, Any]] = {
    "thoughtful": {
        "expression_families": ("thoughtful", "soft_smile", "explain", "skeptical"),
        "use_contexts": ("ali_warm_explainer", "evidence_review", "personal_story"),
        "max_intensity": "mild",
        "min_credibility": "medium",
    },
    "explaining": {
        "expression_families": ("explain", "soft_smile", "thoughtful"),
        "use_contexts": (
            "ali_warm_explainer",
            "jeff_clean_tutorial",
            "evidence_review",
            "protocol_steps",
        ),
        "max_intensity": "medium",
        "min_credibility": "medium",
    },
    "pointing": {
        "expression_families": ("explain", "soft_smile"),
        "use_contexts": ("jeff_clean_tutorial", "protocol_steps", "myth_busting"),
        "max_intensity": "medium",
        "min_credibility": "medium",
    },
    "serious": {
        "expression_families": ("serious_warning", "skeptical", "thoughtful"),
        "use_contexts": ("evidence_review", "warning", "myth_busting"),
        "max_intensity": "medium",
        "min_credibility": "medium",
    },
    "surprised": {
        "expression_families": ("thoughtful", "soft_smile", "mild_surprise", "skeptical"),
        "use_contexts": ("ali_warm_explainer", "evidence_review", "myth_busting"),
        "max_intensity": "medium",
        "min_credibility": "medium",
    },
    "excited": {
        "expression_families": ("mild_surprise", "soft_smile", "explain"),
        "use_contexts": ("myth_busting", "personal_story", "ali_warm_explainer"),
        "max_intensity": "medium",
        "min_credibility": "medium",
    },
    "laughing": {
        "expression_families": ("warm_laugh", "soft_smile"),
        "use_contexts": ("personal_story", "ali_warm_explainer"),
        "max_intensity": "medium",
        "min_credibility": "medium",
    },
}
_SHOSHO_BENEFIT_TEMPLATE_ID = "shosho_benefit_list_card"


class CutoutCastingError(ValueError):
    """Raised when a pose manifest exists but yields no usable candidates."""


@dataclass(frozen=True)
class CutoutCastRequest:
    """Structured selection criteria for one thumbnail idea."""

    emotion_key: str = ""
    expression_families: tuple[str, ...] = ()
    use_contexts: tuple[str, ...] = ()
    avoid_contexts: tuple[str, ...] = ()
    hands: tuple[str, ...] = ()
    body_angles: tuple[str, ...] = ()
    gazes: tuple[str, ...] = ()
    max_intensity: str = "medium"
    min_credibility: str = "medium"
    allow_manual_only: bool = False
    limit: int = 6

    def to_manifest(self) -> dict[str, Any]:
        return {
            "emotion_key": self.emotion_key,
            "expression_families": list(self.expression_families),
            "use_contexts": list(self.use_contexts),
            "avoid_contexts": list(self.avoid_contexts),
            "hands": list(self.hands),
            "body_angles": list(self.body_angles),
            "gazes": list(self.gazes),
            "max_intensity": self.max_intensity,
            "min_credibility": self.min_credibility,
            "allow_manual_only": self.allow_manual_only,
            "limit": self.limit,
        }


@dataclass(frozen=True)
class CutoutCandidate:
    """One scored manifest entry."""

    cutout_id: str
    path: Path
    vault_relative_path: str
    original_emotion_folder: str
    tags: dict[str, str]
    use_context: tuple[str, ...]
    avoid_context: tuple[str, ...]
    picker_policy: str
    confidence: float
    score: float
    reasons: tuple[str, ...]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "cutout_id": self.cutout_id,
            "path": self.path.as_posix(),
            "vault_relative_path": self.vault_relative_path,
            "original_emotion_folder": self.original_emotion_folder,
            "tags": self.tags,
            "use_context": list(self.use_context),
            "avoid_context": list(self.avoid_context),
            "picker_policy": self.picker_policy,
            "confidence": self.confidence,
            "score": round(self.score, 3),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class CutoutSelection:
    """The winner plus the ranked candidate list used to pick it."""

    request: CutoutCastRequest
    manifest_path: Path
    candidate: CutoutCandidate
    candidates: tuple[CutoutCandidate, ...]

    @property
    def path(self) -> Path:
        return self.candidate.path

    def to_manifest(self) -> dict[str, Any]:
        return {
            "strategy": "pose_manifest",
            "manifest_path": self.manifest_path.as_posix(),
            "request": self.request.to_manifest(),
            "selected": self.candidate.to_manifest(),
            "candidates": [c.to_manifest() for c in self.candidates],
        }


def default_pose_manifest_path() -> Path:
    """Return the configured pose manifest path."""

    override = os.environ.get(_MANIFEST_ENV)
    if override:
        return Path(override)
    return _DEFAULT_MANIFEST


def load_pose_manifest(path: Path | None = None) -> dict[str, Any]:
    """Load a cutout pose manifest from disk."""

    manifest_path = path or default_pose_manifest_path()
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def build_cast_request_from_idea(idea: Any, *, limit: int = 6) -> CutoutCastRequest:
    """Derive casting criteria from a parsed thumbnail idea.

    ``idea`` may be ``ParsedIdea`` or a mapping with similarly named keys. This
    keeps the selector independent from the parser module and easy to test.
    """

    emotion_key = str(_get_value(idea, "emotion_key", "") or "").strip()
    reference_template_id = str(_get_value(idea, "reference_template_id", "") or "").strip()
    if reference_template_id == _SHOSHO_BENEFIT_TEMPLATE_ID:
        return _build_shosho_benefit_cast_request(idea, emotion_key=emotion_key, limit=limit)

    defaults = _EMOTION_DEFAULTS.get(emotion_key, {})
    expression_families = _tuple(defaults.get("expression_families"))
    use_contexts = list(_tuple(defaults.get("use_contexts")))

    text_parts = [
        _get_value(idea, "hook", ""),
        _get_value(idea, "visual", ""),
        _get_value(idea, "decoration", ""),
        _get_value(idea, "bg", ""),
    ]
    inferred = _infer_contexts(" ".join(str(p) for p in text_parts))
    for context in reversed(inferred):
        if context in use_contexts:
            use_contexts.remove(context)
        use_contexts.insert(0, context)

    return CutoutCastRequest(
        emotion_key=emotion_key,
        expression_families=expression_families,
        use_contexts=tuple(use_contexts),
        avoid_contexts=("comedy_only",),
        max_intensity=str(defaults.get("max_intensity", "medium")),
        min_credibility=str(defaults.get("min_credibility", "medium")),
        limit=limit,
    )


def _build_shosho_benefit_cast_request(
    idea: Any,
    *,
    emotion_key: str,
    limit: int,
) -> CutoutCastRequest:
    """Benefit-list cards need a presenter pose, not warning/comedy energy."""

    if emotion_key == "pointing":
        return CutoutCastRequest(
            emotion_key=emotion_key,
            expression_families=("explain", "soft_smile", "thoughtful"),
            use_contexts=(
                "jeff_clean_tutorial",
                "protocol_steps",
                "ali_warm_explainer",
                "evidence_review",
            ),
            avoid_contexts=("warning", "comedy_only"),
            hands=(
                "point_screen_left",
                "point_screen_right",
                "open_palm_screen_right",
                "open_palm_screen_left",
                "chin",
            ),
            max_intensity="mild",
            min_credibility="high",
            limit=limit,
        )

    expression_families = (
        ("thoughtful", "explain", "soft_smile")
        if emotion_key == "thoughtful"
        else ("explain", "soft_smile", "thoughtful")
    )
    return CutoutCastRequest(
        emotion_key=emotion_key,
        expression_families=expression_families,
        use_contexts=("ali_warm_explainer", "evidence_review"),
        avoid_contexts=("warning", "comedy_only"),
        hands=(
            "open_palm_screen_right",
            "point_screen_right",
            "open_palm_screen_left",
            "point_screen_left",
            "chin",
        ),
        max_intensity="mild",
        min_credibility="high",
        limit=limit,
    )


def cast_cutouts(
    request: CutoutCastRequest,
    *,
    manifest_path: Path | None = None,
    vault_root: Path | None = None,
    require_existing: bool = True,
) -> list[CutoutCandidate]:
    """Return ranked cutout candidates for ``request``."""

    path = manifest_path or default_pose_manifest_path()
    manifest = load_pose_manifest(path)
    candidates: list[CutoutCandidate] = []

    for entry in manifest.get("entries", []):
        score = _score_entry(entry, request)
        if score is None:
            continue
        resolved_path = _resolve_entry_path(entry, vault_root)
        if require_existing and not resolved_path.is_file():
            continue
        value, reasons = score
        candidates.append(
            CutoutCandidate(
                cutout_id=str(entry.get("cutout_id", "")),
                path=resolved_path,
                vault_relative_path=str(entry.get("vault_relative_path", "")),
                original_emotion_folder=str(entry.get("original_emotion_folder", "")),
                tags={str(k): str(v) for k, v in (entry.get("tags") or {}).items()},
                use_context=_tuple(entry.get("use_context")),
                avoid_context=_tuple(entry.get("avoid_context")),
                picker_policy=str(entry.get("picker_policy", "eligible")),
                confidence=float(entry.get("confidence", 0.0) or 0.0),
                score=value,
                reasons=tuple(reasons),
            )
        )

    candidates.sort(key=lambda c: (-c.score, c.cutout_id))
    return candidates[: max(0, request.limit)]


def pick_youtube_host_by_pose(
    idea: Any,
    vault_root: Path,
    *,
    manifest_path: Path | None = None,
    require_existing: bool = True,
    limit: int = 6,
) -> CutoutSelection | None:
    """Pick the best host cutout using the pose manifest.

    Returns ``None`` when no manifest file exists so callers can keep the old
    random emotion-folder fallback. Raises ``CutoutCastingError`` when a manifest
    exists but all entries are disqualified.
    """

    path = manifest_path or default_pose_manifest_path()
    if not path.is_file():
        return None

    request = build_cast_request_from_idea(idea, limit=limit)
    candidates = cast_cutouts(
        request,
        manifest_path=path,
        vault_root=vault_root,
        require_existing=require_existing,
    )
    if not candidates:
        raise CutoutCastingError(
            f"No eligible host cutout matched the pose manifest request: {request.to_manifest()}"
        )
    return CutoutSelection(
        request=request,
        manifest_path=path,
        candidate=candidates[0],
        candidates=tuple(candidates),
    )


def write_candidate_contact_sheet(
    candidates: list[CutoutCandidate] | tuple[CutoutCandidate, ...],
    out_path: Path,
    *,
    columns: int = 3,
    thumb_size: tuple[int, int] = (260, 280),
) -> Path:
    """Write a PNG contact sheet for visual review."""

    if not candidates:
        raise ValueError("candidates must not be empty")

    from PIL import Image, ImageDraw, ImageFont, ImageOps

    columns = max(1, columns)
    label_h = 54
    pad = 16
    tile_w = thumb_size[0] + pad * 2
    tile_h = thumb_size[1] + label_h + pad * 2
    rows = (len(candidates) + columns - 1) // columns
    sheet = Image.new("RGB", (tile_w * columns, tile_h * rows), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()

    for idx, candidate in enumerate(candidates):
        col = idx % columns
        row = idx // columns
        x0 = col * tile_w
        y0 = row * tile_h
        draw.rectangle((x0, y0, x0 + tile_w - 1, y0 + tile_h - 1), outline=(220, 224, 230))

        with Image.open(candidate.path) as raw:
            img = raw.convert("RGBA")
            img = ImageOps.contain(img, thumb_size)
            px = x0 + (tile_w - img.width) // 2
            py = y0 + pad + (thumb_size[1] - img.height) // 2
            sheet.paste(Image.new("RGB", img.size, "white"), (px, py))
            sheet.paste(img, (px, py), img)

        tags = candidate.tags
        label = f"{candidate.cutout_id} score={candidate.score:.1f}"
        detail = (
            f"{tags.get('expression_family', '?')} / {tags.get('hands', '?')} / "
            f"{tags.get('credibility', '?')}"
        )
        draw.text((x0 + pad, y0 + pad + thumb_size[1] + 6), label, fill=(17, 24, 39), font=font)
        draw.text((x0 + pad, y0 + pad + thumb_size[1] + 24), detail, fill=(75, 85, 99), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path


def _score_entry(
    entry: dict[str, Any],
    request: CutoutCastRequest,
) -> tuple[float, list[str]] | None:
    tags = entry.get("tags") or {}
    policy = str(entry.get("picker_policy", "eligible"))
    if policy == "manual_only" and not request.allow_manual_only:
        return None

    intensity = str(tags.get("intensity", "medium"))
    if _rank(intensity, _INTENSITY_ORDER) > _rank(request.max_intensity, _INTENSITY_ORDER):
        return None

    credibility = str(tags.get("credibility", "medium"))
    if _rank(credibility, _CREDIBILITY_ORDER) < _rank(request.min_credibility, _CREDIBILITY_ORDER):
        return None

    entry_use = set(_tuple(entry.get("use_context")))
    entry_avoid = set(_tuple(entry.get("avoid_context")))
    request_avoid = set(request.avoid_contexts)
    blocking_contexts = set(request.use_contexts[:2])
    if blocking_contexts and entry_avoid.intersection(blocking_contexts):
        return None
    if request_avoid and entry_use.intersection(request_avoid):
        return None

    score = 0.0
    reasons: list[str] = []

    expression_family = str(tags.get("expression_family", ""))
    if request.expression_families:
        if expression_family in request.expression_families:
            idx = request.expression_families.index(expression_family)
            value = 44 - idx * 4
            score += value
            reasons.append(f"expression:{expression_family}+{value}")
        elif entry.get("original_emotion_folder") == request.emotion_key:
            score += 8
            reasons.append(f"legacy_emotion:{request.emotion_key}+8")
        else:
            score -= 12
            reasons.append(f"expression_miss:{expression_family}-12")

    for idx, context in enumerate(request.use_contexts):
        if context in entry_use:
            value = max(4, 16 - idx * 2)
            score += value
            reasons.append(f"context:{context}+{value}")

    credibility_value = _rank(credibility, _CREDIBILITY_ORDER) * 8
    score += credibility_value
    reasons.append(f"credibility:{credibility}+{credibility_value}")

    max_intensity = _rank(request.max_intensity, _INTENSITY_ORDER)
    intensity_value = max(0, max_intensity - _rank(intensity, _INTENSITY_ORDER) + 1) * 2
    score += intensity_value
    reasons.append(f"intensity:{intensity}+{intensity_value}")

    confidence = float(entry.get("confidence", 0.0) or 0.0)
    score += confidence * 5
    reasons.append(f"confidence:{confidence:.2f}+{confidence * 5:.1f}")

    score += _axis_bonus(tags, "hands", request.hands, reasons)
    score += _axis_bonus(tags, "body_angle", request.body_angles, reasons)
    score += _axis_bonus(tags, "gaze", request.gazes, reasons)

    return score, reasons


def _axis_bonus(
    tags: dict[str, Any],
    axis: str,
    requested: tuple[str, ...],
    reasons: list[str],
) -> int:
    if not requested:
        return 0
    value = str(tags.get(axis, ""))
    if value not in requested:
        return 0
    bonus = 6 - min(requested.index(value), 4)
    reasons.append(f"{axis}:{value}+{bonus}")
    return bonus


def _infer_contexts(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    contexts: list[str] = []
    keyword_map: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("warning", ("警告", "危險", "風險", "不要", "avoid", "risk", "danger")),
        (
            "evidence_review",
            ("研究", "實證", "證據", "science", "study", "research", "evidence", "meta-analysis"),
        ),
        ("protocol_steps", ("步驟", "流程", "protocol", "routine", "checklist", "每天", "5g")),
        ("myth_busting", ("迷思", "破解", "真相", "錯", "myth", "mistake", "真的嗎")),
        ("personal_story", ("我", "親身", "故事", "personal", "story")),
    )
    for context, keywords in keyword_map:
        if any(keyword in lowered for keyword in keywords):
            contexts.append(context)
    return tuple(contexts)


def _resolve_entry_path(entry: dict[str, Any], vault_root: Path | None) -> Path:
    source_path = str(entry.get("source_path", "") or "")
    if source_path:
        path = Path(source_path)
        if path.is_file():
            return path
    relative = str(entry.get("vault_relative_path", "") or "")
    if relative and vault_root is not None:
        return vault_root / relative
    if source_path:
        return Path(source_path)
    return Path(relative)


def _rank(value: str, order: dict[str, int]) -> int:
    return order.get(value, max(order.values()))


def _tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _get_value(obj: Any, key: str, default: Any) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
