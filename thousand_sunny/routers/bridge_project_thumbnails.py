"""Bridge sibling router for the thumbnail pipeline (ADR-033 PR4-A).

Lives separately from ``bridge_projects.py`` per panel finding P4
(``bridge_projects.py`` is already 1952 LOC — adding ~300 more would push
maintenance pain past the threshold). All endpoints under
``/bridge/projects/{slug}/thumbnail/*``.

Endpoints:

- ``POST /bridge/projects/{slug}/thumbnail/brainstorm``
    Read frontmatter (title, one_sentence, search_topic) + attach reference
    library to a vision LLM. Parse 3-idea response. Persist as
    ``thumbnail_ideas: list[str]`` in frontmatter. Return HTMX partial.

- ``POST /bridge/projects/{slug}/thumbnail/render``
    Body field ``idea_index`` (0-2). Parse the corresponding idea, resolve
    emotion via cutout_library, pick a host cutout, render via
    thumbnail_worker, write PNG to ``data/thumbnails/{slug}/runs/{ts}/``,
    return HTMX partial with the rendered PNG.

- ``GET /bridge/projects/{slug}/thumbnail/candidate/{run_ts}/{filename}``
    Stream a candidate PNG out of ``data/thumbnails/{slug}/runs/{run_ts}/``.
    Bridge UI <img> src points here.

- ``POST /bridge/projects/{slug}/thumbnail/commit``
    Body fields ``run_ts`` + ``filename``. Archive any existing chosen
    thumbnail, copy candidate to vault ``Attachments/projects/{slug}/``,
    update frontmatter, write audit log row.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from agents.foundry.render_workers.ai_image_gen import (
    AIImageGenError,
    generate_thumbnail_bg,  # noqa: F401
    generate_thumbnail_from_references,
)
from agents.foundry.render_workers.thumbnail_worker import (
    DEFAULT_VIDEO_DIR as _HYPERFRAMES_VIDEO_DIR,
)
from agents.foundry.render_workers.thumbnail_worker import (
    ThumbnailRenderError,
    render_podcast_still,
    render_youtube_still,  # noqa: F401
)
from agents.foundry.thumbnail_templates import TEMPLATES, get_template
from shared import thumbnail_funnel
from shared.anthropic_client import ask_claude_multi
from shared.config import get_vault_path
from shared.cutout_casting import (
    CutoutCastingError,
    CutoutSelection,
    cast_cutouts,
    default_pose_manifest_path,
    pick_youtube_host_by_pose,
)
from shared.cutout_library import (
    EmotionLookupError,
    pick_podcast_guest,
    pick_podcast_host,
    pick_youtube_host,
)
from shared.llm_router import get_model
from shared.project_indexer import ProjectIndexer, ProjectNotFoundError, normalize_slug
from shared.project_writer import ProjectWriteError, update_frontmatter
from shared.state import record_api_call
from shared.thumbnail_arrangement_eval import rank_arrangement_candidates
from shared.thumbnail_arrangement_generator import (
    candidate_to_component_plan,
    generate_arrangement_candidates,
)
from shared.thumbnail_arrangement_preview import render_arrangement_contact_sheet
from shared.thumbnail_component_compatibility import (
    reference_summary,
    score_arrangement_against_reference,
)
from shared.thumbnail_assets import (
    build_thumbnail_asset_manifest,
    enrich_asset_manifest_for_ui,
    merge_existing_asset_provenance,
    update_asset_manifest_item,
)
from shared.thumbnail_background_plate import render_background_plate_preview
from shared.thumbnail_final_render import render_staged_youtube_thumbnail
from shared.thumbnail_funnel import FunnelError
from shared.thumbnail_idea import (
    IdeaParseError,
    ParsedIdea,
    parse_idea,
    parse_ideas_batch,
)
from shared.thumbnail_layout_blocking import render_layout_blocking_preview
from shared.thumbnail_person_preview import render_person_placement_preview
from shared.thumbnail_playbook import (
    TITLE_ARCHETYPE_EMOTION_MAP,
    format_playbook_index_for_prompt,
    format_title_pool_system_prompt,
    load_playbook_index,
)
from shared.thumbnail_prompt_package import build_thumbnail_prompt_package
from shared.thumbnail_quality import evaluate_thumbnail_render
from shared.thumbnail_host_compatibility import (
    build_cast_request_for_reference,
    check_host_compatibility,
)
from shared.thumbnail_reference_corpus import best_deconstruction_examples
from shared.thumbnail_reference_templates import (
    REFERENCE_TEMPLATE_IDS,
    format_title_template_match_plan_for_prompt,
    get_reference_template,
)
from shared.thumbnail_template_selector import format_title_reference_family_match_plan_for_prompt
from shared.thumbnail_typography_preview import render_typography_preview
from shared.thumbnail_workflow import format_thumbnail_workflow_pack_for_prompt
from thousand_sunny.auth import check_auth

logger = logging.getLogger("nakama.web.bridge_project_thumbnails")

page_router = APIRouter(prefix="/bridge", tags=["bridge-project-thumbnails"])

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_DIR = _REPO_ROOT / "thousand_sunny" / "templates" / "bridge"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

_ACCENT_TOKEN_RE = re.compile(r"^\s*([0-9０-９]+(?:\s*(?:g|G|歲|%|％))?|[?!！？⚡＋+✓×xX])")

# Repo-local working directory for candidates + manifests (gitignored under data/).
# Env override exists so tests don't pollute the real repo data/thumbnails/.
_DEFAULT_THUMBNAILS_DIR = _REPO_ROOT / "data" / "thumbnails"


def _thumbnails_dir() -> Path:
    override = os.environ.get("NAKAMA_THUMBNAILS_DATA_DIR")
    return Path(override) if override else _DEFAULT_THUMBNAILS_DIR


# Vault paths derived inside endpoints via get_vault_path() so tests can monkeypatch.

# Reference library cap for vision LLM attachment (panel P10).
_MAX_REFERENCE_IMAGES = 30

# Maximum image dimension before sending to vision LLM (panel P10).
# (Composition rendering uses original resolution; this only constrains LLM input.)
_MAX_REFERENCE_PIXEL = 512  # placeholder; resize is left as a follow-up — PR4-A
# currently sends the original file. See open question §OQ1 in ADR-033.

# Prompt paths — versioned filename so we can iterate without breaking history.
_BRAINSTORM_PROMPT_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "brainstorm_youtube_v2.md"
_PODCAST_BRAINSTORM_PROMPT_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "brainstorm_podcast_v1.md"
_TITLES_PROMPT_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "brainstorm_titles_v2.md"
# v3 = divergent pool variant — produces 2-3 titles per archetype (~25 total) in
# T-A1...T-A10 grouped schema, with iteration mode that takes anchor titles.
# ADR-036 v1 contract: the Ali/Jeff YouTube brainstorm may only emit visual
# tags implemented by the current renderer. Unsupported tags used to fall back
# silently to T-V1, which made the LLM's stated recipe diverge from the image.
_SUPPORTED_YOUTUBE_VISUAL_TAGS = frozenset(TEMPLATES.keys())
_DISALLOWED_YOUTUBE_VISUAL_TAGS = frozenset({"T-V6"})
_REQUIRED_YOUTUBE_V2_METADATA = (
    "archetype_tags",
    "lane",
    "recipe_id",
    "reference_template_id",
    "title_pairing",
    "asset_queries",
    "component_type",
    "component_text",
    "host_directive",
    "viewer_promise",
    "evidence_fit",
    "trust_risk",
)
_SHOSHO_BENEFIT_TEMPLATE_ID = "shosho_benefit_list_card"
_SHOSHO_BENEFIT_LANE = "Ali Warm Explainer"
_SHOSHO_BENEFIT_DEFAULT_RECIPE = "ali_warm_evidence_list"
_SHOSHO_BENEFIT_ALLOWED_EMOTIONS = frozenset({"explaining", "pointing", "thoughtful"})
_SHOSHO_BENEFIT_DEFAULT_EMOTION_INPUT = "\u89e3\u91cb"
_SHOSHO_BENEFIT_LABEL_FALLBACKS = (
    "\u8b77\u8166",
    "\u6297\u8001",
    "\u8a8d\u77e5",
    "\u589e\u529b",
    "5g",
)

# Pool sidecar (gitignored). One JSON per project; persists 20-30-title pool +
# checked ids + iteration count across page refresh / server restart.
_DEFAULT_TITLE_POOL_DIR = _REPO_ROOT / "data" / "title_brainstorm"


def _title_pool_dir() -> Path:
    override = os.environ.get("NAKAMA_TITLE_POOL_DATA_DIR")
    return Path(override) if override else _DEFAULT_TITLE_POOL_DIR


def _title_pool_path(slug: str) -> Path:
    return _title_pool_dir() / f"{slug}.json"


def _load_title_pool(slug: str) -> dict:
    """Return current pool state or an empty pool shell.

    Schema (v1):
      {
        "iteration": int,         # 0 = no brainstorm yet; 1+ = N rounds run
        "pool": [
          {"id": "t001", "archetype": "T-A1", "title": "..."},
          ...
        ],
        "checked_ids": ["t001", "t005", ...],
        "updated_at": ISO8601,
      }
    """
    path = _title_pool_path(slug)
    if not path.exists():
        return {"iteration": 0, "pool": [], "checked_ids": [], "updated_at": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Defensive defaults for older schema migrations
        data.setdefault("iteration", 0)
        data.setdefault("pool", [])
        data.setdefault("checked_ids", [])
        data.setdefault("updated_at", "")
        return data
    except (OSError, json.JSONDecodeError):
        logger.warning("title pool read failed: %s", path)
        return {"iteration": 0, "pool": [], "checked_ids": [], "updated_at": ""}


def _save_title_pool(slug: str, pool: dict) -> None:
    """Best-effort write; failure is logged but does not break the user flow."""
    path = _title_pool_path(slug)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        pool["updated_at"] = datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
        path.write_text(
            json.dumps(pool, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("title pool write failed: %s", path)


def _group_pool_by_archetype(pool: list[dict]) -> list[dict]:
    """Return a list of {archetype_id, name, emotion, grade, titles[]} preserving
    the canonical T-A1..T-A10 ordering. Used by the grid template to render
    sections in stable order even if the LLM emitted them out of order.
    """
    catalog = get_title_archetypes_for_ui()  # canonical order from playbook
    by_id: dict[str, list[dict]] = {arch["id"]: [] for arch in catalog}
    for t in pool:
        aid = t.get("archetype") or ""
        if aid in by_id:
            by_id[aid].append(t)
    out: list[dict] = []
    for arch in catalog:
        out.append(
            {
                "id": arch["id"],
                "name": arch["name"],
                "emotion": arch["emotion"],
                "grade": arch["brand_fit_grade"],
                "one_line": arch["one_line"],
                "titles": by_id.get(arch["id"], []),
            }
        )
    return out


# Regex pulled from import-time so it's compiled once.
_ARCHETYPE_HEADER_RE = re.compile(r"^\s*(T-A\d+)\s*$", re.MULTILINE)


def _parse_title_pool(response_text: str) -> list[tuple[str, str]]:
    """Parse v3 prompt's grouped schema into a flat ``[(archetype_id, title), ...]``.

    Schema:
        T-A1
        - title 1
        - title 2

        T-A2
        - title 1
        ...

    Bullet markers (``- `` or ``• `` or ``* ``) are stripped. Empty bullets and
    blank lines are skipped. Returns titles in the order they appeared. Caller
    decides whether to clamp counts per archetype.
    """
    out: list[tuple[str, str]] = []
    current_arch: str | None = None
    for raw in response_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _ARCHETYPE_HEADER_RE.match(line)
        if m:
            current_arch = m.group(1)
            continue
        if current_arch is None:
            continue
        # Strip bullet markers + leading numbering
        cleaned = line.lstrip("-•·*").lstrip()
        cleaned = re.sub(r"^\(?\d+[.)]\s*", "", cleaned)
        cleaned = cleaned.strip("\"' 　")
        if cleaned:
            out.append((current_arch, cleaned))
    return out


class U2NetError(RuntimeError):
    """Raised when ``npx hyperframes remove-background`` fails for one image."""


async def _u2net_cutout(src: Path, dst: Path) -> None:
    """Run u2net background removal via Hyperframes CLI for one image.

    Output is a transparent PNG at ``dst``. Mirrors the subprocess pattern in
    ``scripts/import_shosho_cutouts.py`` (the YouTube selfie import). Used by
    the Podcast active-cutouts confirm step (D8 Stage 5).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        "npx",
        "hyperframes",
        "remove-background",
        str(src),
        "-o",
        str(dst),
    ]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(_HYPERFRAMES_VIDEO_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr_bytes.decode(errors="replace")[-500:]
        raise U2NetError(f"remove-background failed for {src.name}: {tail!r}")


_indexer_singleton: ProjectIndexer | None = None


def _indexer() -> ProjectIndexer:
    global _indexer_singleton
    if _indexer_singleton is None:
        _indexer_singleton = ProjectIndexer(vault_root=get_vault_path())
    return _indexer_singleton


def _run_ts() -> str:
    """Timestamped subdirectory name. Asia/Taipei, second precision."""
    return datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%dT%H%M%S")


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")


def _reference_images_for(content_type: str) -> list[Path]:
    """Collect 修修's reference library for the given route.

    For content_type=youtube: vault ``Attachments/cutouts/reference/youtube/{mine,peers}/``
    """
    vault = get_vault_path()
    root = vault / "Attachments" / "cutouts" / "reference" / content_type
    if not root.is_dir():
        return []
    found: list[Path] = []
    for sub in ("mine", "peers"):
        d = root / sub
        if d.is_dir():
            found.extend(
                sorted(p for p in d.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
            )
    return found[:_MAX_REFERENCE_IMAGES]


def _ref_image_to_block(path: Path) -> dict:
    """Encode a reference image as an Anthropic API image content block."""
    suffix = path.suffix.lower().lstrip(".")
    media_type = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
        suffix, "image/png"
    )
    payload = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": payload,
        },
    }


def _load_brainstorm_prompt(content_type: str = "youtube") -> str:
    """Route to YouTube or Podcast brainstorm system prompt by ``content_type``.

    The two prompts share the 5-line idea schema (D3) so the same parser works
    on either response; what differs is style guidance (DOAC layout, two-person
    framing, longer hook text for podcasts).
    """
    if content_type == "podcast":
        return _PODCAST_BRAINSTORM_PROMPT_PATH.read_text(encoding="utf-8")
    return _BRAINSTORM_PROMPT_PATH.read_text(encoding="utf-8")


def _load_titles_prompt() -> str:
    return _TITLES_PROMPT_PATH.read_text(encoding="utf-8")


# 修修-specific 情緒 tag per title archetype — surfaces in the UI chip selector
# so user can pick by emotion (FOMO / 好奇 / 損失厭惡) instead of by abstract ID.
# Sourced from 2026-05-27 archetype-emotion mapping (matches playbook §2 mechanism
# write-ups).
_TITLE_ARCHETYPE_EMOTION_MAP = {
    "T-A1": "好奇 / 可控感",
    "T-A2": "可操作 / 效率",
    "T-A3": "驚訝 / 報復快感",
    "T-A4": "共鳴 / 同理",
    "T-A5": "FOMO / 偷窺欲",
    "T-A6": "認知 gap / 好奇",
    "T-A7": "時間焦慮 / 效率",
    "T-A8": "信任 / 安全",
    "T-A9": "緊迫 / FOMO",
    "T-A10": "損失厭惡 / 恐懼",
}


def get_title_archetypes_for_ui() -> list[dict]:
    """Return 10 title archetypes with 修修 emotion tags for the chip selector.

    Pulls structural data from ``load_playbook_index()`` (cached) and decorates
    each with an emotion label sourced from ``_TITLE_ARCHETYPE_EMOTION_MAP``.
    Used by the T&T tab template to render archetype chips so user can select
    which archetypes the brainstorm LLM should produce titles for.
    """
    idx = load_playbook_index()
    return [
        {
            "id": a.id,
            "name": a.name,
            "one_line": a.one_line,
            "emotion": TITLE_ARCHETYPE_EMOTION_MAP.get(a.id, ""),
            "brand_fit_grade": a.brand_fit_grade,
        }
        for a in idx.title_archetypes
    ]


def _thumbnail_title_inputs(slug: str, entry: object) -> tuple[list[str], str]:
    """Return the title pool the thumbnail brainstorm should evaluate.

    The preferred input is the title-pool checked set: 修修 can mark roughly
    8-12 title ideas during iteration, then ask the thumbnail workflow to pick
    the three strongest visual test styles from that pool. If no checked pool
    exists, fall back to persisted ``title_candidates`` for backward
    compatibility with the older 3-title flow.
    """
    pool_state = _load_title_pool(slug)
    checked_ids = set(pool_state.get("checked_ids", []))
    if checked_ids:
        checked_titles: list[str] = []
        for item in pool_state.get("pool", []):
            if not isinstance(item, dict) or item.get("id") not in checked_ids:
                continue
            title = str(item.get("title") or "").strip()
            if title:
                checked_titles.append(title)
        if checked_titles:
            return checked_titles[:12], "checked_title_pool"

    fallback = [str(t).strip() for t in getattr(entry, "title_candidates", []) if str(t).strip()]
    return fallback[:12], "frontmatter_title_candidates"


def _normalize_contract_text(value: str) -> str:
    """Normalize free text for contract comparisons without changing display."""

    return re.sub(r"\s+", " ", value or "").strip()


def _validate_youtube_v2_ideas(ideas: list[ParsedIdea]) -> None:
    """Enforce the ADR-036 YouTube brainstorm contract before persistence.

    Podcast brainstorms stay backward-compatible with the older 5-line schema.
    YouTube v2 is stricter because the output feeds a renderer with a finite
    T-V template registry and the upload test unit is one title with three
    thumbnail variants.
    """

    errors: list[str] = []
    if len(ideas) != 3:
        errors.append(f"expected exactly 3 ideas, got {len(ideas)}")

    title_pairings: list[str] = []
    for idx, idea in enumerate(ideas, start=1):
        missing: list[str] = []
        for field in _REQUIRED_YOUTUBE_V2_METADATA:
            value = getattr(idea, field, None)
            if isinstance(value, tuple):
                if not value:
                    missing.append(field)
            elif not _normalize_contract_text(str(value or "")):
                missing.append(field)
        if missing:
            errors.append(f"Idea {idx} missing metadata: {', '.join(missing)}")

        reference_template_id = _normalize_contract_text(idea.reference_template_id)
        if reference_template_id and reference_template_id not in REFERENCE_TEMPLATE_IDS:
            errors.append(
                f"Idea {idx} uses unknown reference_template {reference_template_id}; "
                f"supported: {', '.join(sorted(REFERENCE_TEMPLATE_IDS))}"
            )
        elif reference_template_id:
            template = get_reference_template(reference_template_id)
            if idea.component_type and idea.component_type not in template.component_types:
                errors.append(
                    f"Idea {idx} component {idea.component_type} does not match "
                    f"reference_template {reference_template_id}; allowed: "
                    f"{', '.join(template.component_types)}"
                )

        visual = _normalize_contract_text(idea.visual)
        visual_len = len(visual)
        if visual_len > 120:
            errors.append(f"Idea {idx} visual brief too long: {visual_len} chars; max 120")
        if not all(token in visual for token in ("template=", "component=", "host=")):
            errors.append(
                f"Idea {idx} visual brief must use slot syntax: "
                "template=...; component=...; host=..."
            )

        for label in idea.component_text:
            if len(_normalize_contract_text(label)) > 12:
                errors.append(f"Idea {idx} component_text label too long: {label!r}; max 12 chars")

        title = _normalize_contract_text(idea.title_pairing)
        if title:
            title_pairings.append(title)

        tags = tuple(str(tag).strip().upper() for tag in idea.archetype_tags)
        if any(tag in _DISALLOWED_YOUTUBE_VISUAL_TAGS for tag in tags):
            errors.append(
                f"Idea {idx} uses disallowed visual tag(s): "
                f"{', '.join(sorted(set(tags) & _DISALLOWED_YOUTUBE_VISUAL_TAGS))}"
            )

        visual_tags = [tag for tag in tags if tag.startswith("T-V")]
        if len(visual_tags) != 1:
            errors.append(
                f"Idea {idx} must include exactly 1 T-V visual tag; got "
                f"{', '.join(visual_tags) or 'none'}"
            )
        elif visual_tags[0] not in _SUPPORTED_YOUTUBE_VISUAL_TAGS:
            errors.append(
                f"Idea {idx} uses unsupported visual tag {visual_tags[0]}; "
                f"supported: {', '.join(sorted(_SUPPORTED_YOUTUBE_VISUAL_TAGS))}"
            )

    if len(title_pairings) == len(ideas) and len(set(title_pairings)) != 1:
        errors.append("all 3 ideas must share the same title_pairing/publish title")

    if errors:
        raise ValueError("; ".join(errors))


_REFERENCE_TEMPLATE_LINE_RE = re.compile(
    (
        "^[ \\t]*(?:reference_template|reference template|template_id|"
        "template|\\u53c3\\u8003\\u6a21\\u677f|\\u53c3\\u8003\\u7248\\u578b|"
        "\\u7248\\u578b)\\s*[\\uff1a:].*$"
    ),
    re.IGNORECASE,
)
_VISUAL_LINE_RE = re.compile(
    "^[ \\t]*(?:\\u8996\\u89ba|visual)\\s*[\\uff1a:].*$",
    re.IGNORECASE,
)
_LANE_LINE_RE = re.compile(
    "^[ \\t]*(?:lane|\\u98a8\\u683c\\u8def\\u7dda|\\u8def\\u7dda)\\s*[\\uff1a:].*$",
    re.IGNORECASE,
)
_RECIPE_LINE_RE = re.compile(
    "^[ \\t]*(?:recipe|recipe_id|\\u7e2e\\u5716\\u914d\\u65b9|\\u914d\\u65b9)\\s*[\\uff1a:].*$",
    re.IGNORECASE,
)
_EMOTION_LINE_RE = re.compile(
    "^[ \\t]*(?:\\u6211\\u7684\\u8868\\u60c5|\\u8868\\u60c5|emotion)\\s*[\\uff1a:].*$",
    re.IGNORECASE,
)
_COMPONENT_TEXT_LINE_RE = re.compile(
    (
        "^[ \\t]*(?:component_text|component text|\\u5143\\u4ef6\\u6587\\u5b57|"
        "\\u5361\\u7247\\u6587\\u5b57|payload_text)\\s*[\\uff1a:].*$"
    ),
    re.IGNORECASE,
)
_HOST_LINE_RE = re.compile(
    (
        "^[ \\t]*(?:host|person|portrait|\\u4eba\\u50cf|\\u4eba\\u7269|"
        "\\u4e3b\\u9ad4)\\s*[\\uff1a:].*$"
    ),
    re.IGNORECASE,
)


def _normalize_youtube_v2_response_text(
    response_text: str,
    ideas: list[ParsedIdea],
) -> tuple[str, list[ParsedIdea]]:
    """Apply deterministic contract repairs that do not require LLM judgment.

    The model often writes a correct idea but overfills the visual line with
    a long host sentence, or prefixes template IDs with ``T01``. Those are
    transport-format issues, not strategy errors, so normalize them before
    strict validation and persistence.
    """

    blocks = _split_response_into_blocks(response_text, len(ideas))
    if len(blocks) != len(ideas):
        return response_text, ideas
    repaired_blocks = [
        _normalize_youtube_v2_idea_block(block=block, idea=idea)
        for block, idea in zip(blocks, ideas)
    ]
    repaired_text = "\n\n".join(
        f"Idea {index}\n{block}" for index, block in enumerate(repaired_blocks, start=1)
    )
    repaired_ideas = parse_ideas_batch(repaired_text)
    return repaired_text, repaired_ideas


def _normalize_youtube_v2_idea_block(*, block: str, idea: ParsedIdea) -> str:
    idea = _normalize_youtube_v2_policy_idea(idea)
    lines = block.splitlines()
    reference_template_id = _normalize_contract_text(idea.reference_template_id)
    visual_brief = _canonical_visual_brief(idea)

    if reference_template_id:
        lines = _replace_or_append_line(
            lines,
            pattern=_REFERENCE_TEMPLATE_LINE_RE,
            replacement=f"reference_template: {reference_template_id}",
        )
    if visual_brief:
        lines = _replace_or_append_line(
            lines,
            pattern=_VISUAL_LINE_RE,
            replacement=f"\u8996\u89ba\uff1a{visual_brief}",
        )
    if reference_template_id == _SHOSHO_BENEFIT_TEMPLATE_ID:
        lines = _replace_or_append_line(
            lines,
            pattern=_LANE_LINE_RE,
            replacement=f"lane: {_SHOSHO_BENEFIT_LANE}",
        )
        lines = _replace_or_append_line(
            lines,
            pattern=_RECIPE_LINE_RE,
            replacement=f"recipe: {idea.recipe_id}",
        )
        lines = _replace_or_append_line(
            lines,
            pattern=_EMOTION_LINE_RE,
            replacement=f"\u6211\u7684\u8868\u60c5\uff1a{idea.emotion_input}",
        )
        lines = _replace_or_append_line(
            lines,
            pattern=_COMPONENT_TEXT_LINE_RE,
            replacement=f"component_text: {' / '.join(idea.component_text)}",
        )
        lines = _replace_or_append_line(
            lines,
            pattern=_HOST_LINE_RE,
            replacement=f"host: {idea.host_directive}",
        )
    return "\n".join(lines).strip()


def _normalize_youtube_v2_policy_idea(idea: ParsedIdea) -> ParsedIdea:
    """Repair strategy-level drift that is deterministic for current templates."""

    reference_template_id = _normalize_contract_text(idea.reference_template_id)
    if reference_template_id != _SHOSHO_BENEFIT_TEMPLATE_ID:
        return idea

    updates: dict[str, object] = {}
    if _normalize_contract_text(idea.lane) != _SHOSHO_BENEFIT_LANE:
        updates["lane"] = _SHOSHO_BENEFIT_LANE

    recipe_id = _normalize_contract_text(idea.recipe_id)
    if not recipe_id or recipe_id.lower().startswith("jeff_"):
        updates["recipe_id"] = _SHOSHO_BENEFIT_DEFAULT_RECIPE

    if idea.emotion_key not in _SHOSHO_BENEFIT_ALLOWED_EMOTIONS:
        updates["emotion_key"] = "explaining"
        updates["emotion_input"] = _SHOSHO_BENEFIT_DEFAULT_EMOTION_INPUT

    component_text = _normalized_shosho_benefit_labels(idea.component_text)
    if component_text != idea.component_text:
        updates["component_text"] = component_text

    host_directive = _normalize_contract_text(idea.host_directive)
    host_lower = host_directive.lower()
    has_left = "left" in host_lower or "\u5de6" in host_directive
    has_right = "right" in host_lower or "\u53f3" in host_directive
    if not host_directive or (has_left and has_right):
        updates["host_directive"] = (
            "face large on a visual third; gaze or hand toward benefit card"
        )

    return replace(idea, **updates) if updates else idea


def _normalized_shosho_benefit_labels(labels: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for label in labels:
        cleaned = _normalize_contract_text(label)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
        if len(out) == 3:
            return tuple(out)
    for fallback in _SHOSHO_BENEFIT_LABEL_FALLBACKS:
        if fallback in seen:
            continue
        seen.add(fallback)
        out.append(fallback)
        if len(out) == 3:
            break
    return tuple(out)


def _replace_or_append_line(
    lines: list[str],
    *,
    pattern: re.Pattern[str],
    replacement: str,
) -> list[str]:
    out = list(lines)
    for index, line in enumerate(out):
        if pattern.match(line):
            out[index] = replacement
            return out
    out.append(replacement)
    return out


def _canonical_visual_brief(idea: ParsedIdea) -> str:
    template_id = _normalize_contract_text(idea.reference_template_id)
    component_type = _normalize_contract_text(idea.component_type)
    if not template_id or not component_type:
        return ""
    return f"template={template_id}; component={component_type}; host={_host_token(idea)}"


def _host_token(idea: ParsedIdea) -> str:
    text = _normalize_contract_text(f"{idea.host_directive} {idea.visual}").lower()
    has_right = any(token in text for token in ("right", "\u53f3"))
    has_left = any(token in text for token in ("left", "\u5de6"))
    if has_right and has_left:
        return "card"
    if has_right:
        return "right"
    if has_left:
        return "left"
    if any(token in text for token in ("center", "centre", "\u4e2d\u592e", "\u4e2d\u9593")):
        return "center"
    return "card"


def _brainstorm_user_message(
    *,
    title_candidates: list[str],
    one_sentence: str,
    search_topic: str,
    content_type: str = "youtube",
    title_input_source: str = "frontmatter_title_candidates",
    reference_images: list[Path] | None = None,
    keep_idea_indices: list[int] | None = None,
    kept_ideas_raw: list[str] | None = None,
) -> list[dict]:
    """Build the multi-part user content for the brainstorm LLM call.

    ADR-036 YouTube path: a focused Ali/Jeff workflow pack replaces the
    old broad archetype catalog. Podcast still uses the legacy playbook index.
    ``reference_images`` arg retained for backward compat but defaults to no
    image attachment.

    ``keep_idea_indices`` / ``kept_ideas_raw`` — set when re-rolling a subset
    of ideas (B-min.2). LLM is instructed to KEEP the listed indices verbatim
    and only generate new variants for the remaining slots.
    """
    parts: list[dict] = []
    # Legacy reference-image attachment (optional, off by default in v1.1)
    if reference_images:
        for img in reference_images:
            parts.append(_ref_image_to_block(img))

    # YouTube v2 uses a focused Ali/Jeff workflow pack instead of the full
    # flat archetype catalog. Podcast keeps the legacy playbook index for now.
    try:
        if content_type == "youtube":
            playbook_text = format_thumbnail_workflow_pack_for_prompt()
        else:
            playbook_text = format_playbook_index_for_prompt(load_playbook_index())
    except Exception as exc:  # noqa: BLE001 — fail soft if playbook files missing
        logger.warning("thumbnail workflow pack unavailable, falling back to no pack: %s", exc)
        playbook_text = ""

    title_template_match_text = ""
    concrete_reference_match_text = ""
    if content_type == "youtube":
        try:
            title_template_match_text = format_title_template_match_plan_for_prompt(
                title_candidates=title_candidates,
                project_brief="\n".join(
                    part for part in (search_topic, one_sentence) if part.strip()
                ),
                limit=3,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("title-template match plan unavailable: %s", exc)
        try:
            concrete_reference_match_text = format_title_reference_family_match_plan_for_prompt(
                title_candidates=title_candidates,
                project_brief="\n".join(
                    part for part in (search_topic, one_sentence) if part.strip()
                ),
                limit_per_title=3,
                max_titles=12,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("concrete reference match plan unavailable: %s", exc)

    brief_text = (
        f"## Project brief\n\n"
        f"- search_topic: {search_topic or '（未填）'}\n"
        f"- one_sentence: {one_sentence or '（未填）'}\n"
        f"- title_input_source: {title_input_source}\n"
        f"- title idea pool ({len(title_candidates)} items):\n"
    )
    for i, t in enumerate(title_candidates or ["（未填）"], start=1):
        brief_text += f"  - T{i:02d}: {t}\n"

    # Selective re-roll mode: instruct LLM to keep some ideas verbatim
    if keep_idea_indices and kept_ideas_raw:
        brief_text += "\n## Selective re-roll mode\n\n"
        brief_text += (
            "The user wants to KEEP the following idea(s) and only generate fresh variants "
            "for the remaining slot(s). When you output 3 ideas, the kept ideas MUST appear "
            "VERBATIM at their original indices; the new ideas must be materially different "
            "from the kept ones along ≥2 of the diversity axes.\n\n"
        )
        for idx, raw in zip(keep_idea_indices, kept_ideas_raw):
            brief_text += f"### Keep at index {idx + 1} (verbatim)\n\n```\n{raw}\n```\n\n"

    brief_text += (
        "\nProduce exactly 3 thumbnail idea blocks for YouTube Test & Compare. "
        "First select one publish title from the title pool, then write three "
        "renderable thumbnail variants for that same title. Use the metadata lines "
        "requested by the system prompt. Diversity requirement: the 3 ideas must "
        "differ in lane or recipe, viewer promise, visual metaphor, and asset needs."
    )

    if playbook_text:
        parts.append({"type": "text", "text": playbook_text})
    if title_template_match_text:
        parts.append({"type": "text", "text": title_template_match_text})
    if concrete_reference_match_text:
        parts.append({"type": "text", "text": concrete_reference_match_text})
    parts.append({"type": "text", "text": brief_text})
    return parts


def _reference_record_for_parsed_idea(parsed: ParsedIdea) -> dict | None:
    """Return the concrete Ali/Jeff reference record for a parsed idea, if known."""

    template_id = (parsed.reference_template_id or "").strip()
    if not template_id:
        return None
    try:
        examples = best_deconstruction_examples(template_id, limit=1)
    except KeyError:
        return None
    return examples[0] if examples else None


def _pick_youtube_host_with_reference_gate(
    parsed: ParsedIdea,
    vault: Path,
) -> tuple[Path, dict | None]:
    """Pick a YouTube host cutout, preferring reference-compatible pose tags."""

    reference_record = _reference_record_for_parsed_idea(parsed)
    manifest_path = default_pose_manifest_path()
    if reference_record and manifest_path.is_file():
        request = build_cast_request_for_reference(reference_record)
        candidates = cast_cutouts(
            request,
            manifest_path=manifest_path,
            vault_root=vault,
            require_existing=True,
        )
        failures: list[dict] = []
        for candidate in candidates:
            gate = check_host_compatibility(
                reference_record=reference_record,
                cutout_candidate=candidate,
            )
            if gate.ok:
                selection = CutoutSelection(
                    request=request,
                    manifest_path=manifest_path,
                    candidate=candidate,
                    candidates=tuple(candidates),
                )
                manifest = selection.to_manifest()
                manifest["reference_record"] = {
                    "reference_id": reference_record["reference_id"],
                    "family_id": reference_record["template_family_candidate"],
                    "title": reference_record["title"],
                    "image_path": reference_record["image_path"],
                }
                manifest["host_compatibility"] = gate.to_dict()
                return selection.path, manifest
            failures.append(
                {
                    "cutout_id": candidate.cutout_id,
                    "host_compatibility": gate.to_dict(),
                }
            )

        raise CutoutCastingError(
            "No host cutout is compatible with reference_template "
            f"{reference_record['template_family_candidate']}: "
            f"{json.dumps(failures[:3], ensure_ascii=False)}"
        )

    pose_selection = pick_youtube_host_by_pose(parsed, vault)
    if pose_selection is None:
        return pick_youtube_host(parsed.emotion_key, vault), None
    return pose_selection.path, pose_selection.to_manifest()


@page_router.post("/projects/{slug}/thumbnail/brainstorm")
async def thumbnail_brainstorm(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Brainstorm 3 thumbnail idea candidates via Sonnet 4.6 vision.

    Output 3 idea blocks (5-line each) are parsed → written to frontmatter
    field ``thumbnail_ideas: list[str]``. Returns an HTMX partial rendering
    the 3 idea cards.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if entry.content_type not in ("youtube", "podcast"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"thumbnail brainstorm supports content_type=youtube|podcast; "
                f"got {entry.content_type!r}."
            ),
        )

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    title_inputs, title_input_source = _thumbnail_title_inputs(slug, entry)

    # ADR-036: thumbnail brainstorm prefers checked title-pool inputs and uses
    # the focused Ali/Jeff workflow pack. Reference image lookup is retained for
    # backward compatibility but not invoked by the main brainstorm path.
    user_parts = _brainstorm_user_message(
        title_candidates=title_inputs,
        one_sentence=str(raw_fm.get("one_sentence") or ""),
        search_topic=str(raw_fm.get("search_topic") or ""),
        content_type=entry.content_type,
        title_input_source=title_input_source,
    )

    system_prompt = _load_brainstorm_prompt(entry.content_type)
    messages = [{"role": "user", "content": user_parts}]
    model = get_model(agent="bridge", task="thumbnail_brainstorm")

    try:
        response_text = await asyncio.to_thread(
            ask_claude_multi,
            messages,
            system=system_prompt,
            model=model,
            max_tokens=2048,
        )
    except Exception as exc:  # noqa: BLE001 — surface LLM/network failures cleanly
        logger.exception("thumbnail brainstorm LLM call failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=f"Brainstorm 失敗：{exc}") from exc

    try:
        ideas = parse_ideas_batch(response_text)
    except (IdeaParseError, EmotionLookupError) as exc:
        logger.warning(
            "thumbnail brainstorm parse failed: slug=%s err=%s body=%s",
            slug,
            exc,
            response_text[:500],
        )
        raise HTTPException(
            status_code=502,
            detail=(f"LLM 回應無法解析 5-line idea format: {exc}. 請重試或回報。"),
        ) from exc

    if not ideas:
        raise HTTPException(status_code=502, detail="LLM 沒有產出任何 idea — 請重試。")

    if entry.content_type == "youtube":
        response_text, ideas = _normalize_youtube_v2_response_text(response_text, ideas)
        try:
            _validate_youtube_v2_ideas(ideas)
        except ValueError as exc:
            logger.warning(
                "thumbnail brainstorm contract failed: slug=%s err=%s body=%s",
                slug,
                exc,
                response_text[:500],
            )
            repair_parts = [
                *user_parts,
                {
                    "type": "text",
                    "text": (
                        "## Contract repair request\n\n"
                        "The previous response was rejected by the app validator:\n"
                        f"{exc}\n\n"
                        "Return exactly 3 Idea blocks again. Choose exactly one "
                        "template_option per idea, then copy reference_template, "
                        "component, component_text, host, and background from that "
                        "same template_option. Do not mix slots across templates."
                    ),
                },
            ]
            try:
                retry_response_text = await asyncio.to_thread(
                    ask_claude_multi,
                    [{"role": "user", "content": repair_parts}],
                    system=system_prompt,
                    model=model,
                    max_tokens=2048,
                )
                retry_ideas = parse_ideas_batch(retry_response_text)
                if not retry_ideas:
                    raise ValueError("retry produced no ideas")
                retry_response_text, retry_ideas = _normalize_youtube_v2_response_text(
                    retry_response_text,
                    retry_ideas,
                )
                _validate_youtube_v2_ideas(retry_ideas)
            except (IdeaParseError, EmotionLookupError, ValueError) as retry_exc:
                logger.warning(
                    "thumbnail brainstorm contract repair failed: slug=%s err=%s body=%s",
                    slug,
                    retry_exc,
                    locals().get("retry_response_text", "")[:500],
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "LLM 回應不符合 YouTube thumbnail v2 contract "
                        f"（自動修正後仍失敗）: {retry_exc}"
                    ),
                ) from retry_exc
            except Exception as retry_exc:  # noqa: BLE001
                logger.exception("thumbnail brainstorm contract repair LLM failed: slug=%s", slug)
                raise HTTPException(
                    status_code=500,
                    detail=f"Brainstorm contract repair 失敗：{retry_exc}",
                ) from retry_exc
            response_text = retry_response_text
            ideas = retry_ideas

    # Persist idea blocks as raw strings (round-trippable, editable in textarea).
    raw_blocks = _split_response_into_blocks(response_text, len(ideas))
    try:
        update_frontmatter(
            vault_root=get_vault_path(),
            slug=slug,
            patch={"thumbnail_ideas": raw_blocks},
        )
    except ProjectWriteError as exc:
        logger.exception("thumbnail brainstorm write-back failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Brainstorm meta persistence (A.4 — revealed-preference tracking for v2 grade refinement).
    _persist_brainstorm_meta(
        slug=slug,
        content_type=entry.content_type,
        ideas=ideas,
        raw_response=response_text,
        model=model,
    )
    _persist_thumbnail_asset_manifest(
        slug=slug,
        ideas=ideas,
        source={
            "kind": "thumbnail_brainstorm",
            "content_type": entry.content_type,
            "model": model,
            "title_input_source": title_input_source,
            "reference_templates": [idea.reference_template_id for idea in ideas],
        },
    )

    # Audit log (cost / parse stats observability).
    try:
        record_api_call(
            agent="bridge",
            model=model,
            input_tokens=0,
            output_tokens=0,
            scope_json=json.dumps(
                {
                    "scope": "thumbnail_brainstorm",
                    "project": slug,
                    "content_type": entry.content_type,
                    "n_ideas": len(ideas),
                    "n_references": 0,  # v1.1: playbook text replaces image few-shot
                    "title_input_source": title_input_source,
                    "n_title_inputs": len(title_inputs),
                    "archetype_tags": [list(i.archetype_tags) for i in ideas],
                    "reference_templates": [i.reference_template_id for i in ideas],
                    "title_pairings": [i.title_pairing for i in ideas],
                    "viewer_promises": [i.viewer_promise for i in ideas],
                },
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001 — audit must not block the success path
        logger.exception("audit record failed (non-fatal)")

    response = _templates.TemplateResponse(
        request,
        "projects/_thumbnail_idea_cards.html",
        {
            "slug": slug,
            "ideas": [
                {
                    "index": i,
                    "raw": raw_blocks[i],
                    "parsed": _parsed_idea_for_template(ideas[i], slug=slug, idea_index=i),
                }
                for i in range(len(ideas))
            ],
        },
    )
    return _trigger_asset_manifest_refresh(response)


def _split_response_into_blocks(response_text: str, expected: int) -> list[str]:
    """Split the LLM response by ``Idea N`` headings → ``[block_1, block_2, ...]``.

    Drops preamble before the first heading; tolerates trailing whitespace.
    Returns ``expected``-long list, padding with parsed-text fallback if needed.
    """
    import re as _re

    parts = _re.split(
        r"^[ \t]*(?:Idea|候選)\s*\d+\s*$|^[ \t]*-{3,}\s*$",
        response_text,
        flags=_re.MULTILINE | _re.IGNORECASE,
    )
    blocks = [p.strip() for p in parts if p.strip() and "大字" in p]
    if len(blocks) < expected:
        # Pad with last block (shouldn't happen if parse_ideas_batch succeeded)
        while len(blocks) < expected:
            blocks.append(blocks[-1] if blocks else "")
    return blocks[:expected]


def _brainstorm_meta_path(slug: str) -> Path:
    """Repo-local JSON path for revealed-preference / archetype tag history.

    Co-located with thumbnail run artifacts: ``{thumbnails_dir}/{slug}/brainstorm_meta.json``.
    Env override ``NAKAMA_THUMBNAILS_DATA_DIR`` (used by tests) automatically
    redirects this path too so tests don't write into the real repo.
    """
    return _thumbnails_dir() / slug / "brainstorm_meta.json"


def _asset_manifest_path(slug: str) -> Path:
    """Repo-local JSON path for thumbnail asset search/provenance needs."""

    return _thumbnails_dir() / slug / "asset_manifest.json"


def _write_thumbnail_asset_manifest(slug: str, manifest: dict) -> None:
    """Write the asset manifest sidecar for a project."""

    manifest_path = _asset_manifest_path(slug)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _persist_thumbnail_asset_manifest(
    *,
    slug: str,
    ideas: list[ParsedIdea],
    source: dict,
) -> None:
    """Write the current asset-needs manifest.

    This is search intent only. It deliberately contains empty provenance
    fields for a future asset-sourcing step to fill before an asset is used.
    """

    try:
        manifest = build_thumbnail_asset_manifest(
            slug=slug,
            ideas=ideas,
            generated_at=_now_iso(),
            source=source,
        )
        manifest = merge_existing_asset_provenance(
            manifest,
            _load_manifest(_asset_manifest_path(slug)),
        )
        _write_thumbnail_asset_manifest(slug, manifest)
    except Exception:  # noqa: BLE001
        logger.exception("thumbnail asset manifest persistence failed (non-fatal, slug=%s)", slug)


def _persist_brainstorm_meta(
    *,
    slug: str,
    content_type: str,
    ideas: list[ParsedIdea],
    raw_response: str,
    model: str,
) -> None:
    """Append a brainstorm run record to project's thumbnail_brainstorm_meta.json.

    Captures: archetype tags chosen by LLM, emotion picks, raw LLM output,
    timestamp + model. Used downstream for revealed-preference analysis when
    修修 picks one of the 3 → which archetype was over-/under-represented.

    Failures are logged + swallowed — must never block the brainstorm success path.
    """
    try:
        meta_path = _brainstorm_meta_path(slug)
        meta_path.parent.mkdir(parents=True, exist_ok=True)

        run_record = {
            "ts": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
            "content_type": content_type,
            "model": model,
            "ideas": [
                {
                    "index": i,
                    "archetype_tags": list(idea.archetype_tags),
                    "lane": idea.lane,
                    "recipe_id": idea.recipe_id,
                    "reference_template_id": idea.reference_template_id,
                    "title_pairing": idea.title_pairing,
                    "component_type": idea.component_type,
                    "component_text": list(idea.component_text),
                    "host_directive": idea.host_directive,
                    "asset_queries": list(idea.asset_queries),
                    "viewer_promise": idea.viewer_promise,
                    "evidence_fit": idea.evidence_fit,
                    "trust_risk": idea.trust_risk,
                    "emotion_key": idea.emotion_key,
                    "hook": idea.hook,
                }
                for i, idea in enumerate(ideas)
            ],
            "raw_response_first_chars": raw_response[:1200],  # keep for debug, cap to avoid bloat
        }

        if meta_path.exists():
            try:
                existing = json.loads(meta_path.read_text(encoding="utf-8"))
                runs = existing.get("runs", [])
            except json.JSONDecodeError:
                logger.warning("brainstorm_meta.json corrupt — overwriting (slug=%s)", slug)
                runs = []
        else:
            runs = []

        runs.append(run_record)
        # Cap history at 50 most recent runs to avoid unbounded growth
        if len(runs) > 50:
            runs = runs[-50:]

        meta_path.write_text(
            json.dumps(
                {"schema_version": "v2", "slug": slug, "runs": runs},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 — never block success path
        logger.exception("brainstorm meta persistence failed (non-fatal, slug=%s)", slug)


def prepare_existing_ideas_for_template(raw_frontmatter: dict | None, *, slug: str = "") -> list[dict]:
    """Pre-parse ``thumbnail_ideas`` from frontmatter into the shape the partial expects.

    Called by ``bridge_projects.projects_detail`` on initial page load so the
    template can render the same editable-card partial used by HTMX swaps.
    Each entry: ``{"index": int, "raw": str, "parsed": ParsedIdea.__dict__ | None,
    "parse_error": str | None}``.
    """
    fm = raw_frontmatter if isinstance(raw_frontmatter, dict) else {}
    raw_list = fm.get("thumbnail_ideas") or []
    asset_manifest = _load_manifest(_asset_manifest_path(slug)) if slug else {}
    out: list[dict] = []
    for i, raw in enumerate(raw_list):
        raw_str = str(raw)
        try:
            parsed = parse_idea(raw_str)
            out.append(
                {
                    "index": i,
                    "raw": raw_str,
                    "parsed": _parsed_idea_for_template(
                        parsed,
                        slug=slug,
                        idea_index=i,
                        asset_manifest=asset_manifest,
                    ),
                    "parse_error": None,
                }
            )
        except (IdeaParseError, EmotionLookupError) as exc:
            out.append(
                {
                    "index": i,
                    "raw": raw_str,
                    "parsed": None,
                    "parse_error": str(exc),
                }
            )
    return out


def _parsed_idea_for_template(
    parsed: ParsedIdea,
    *,
    slug: str = "",
    idea_index: int,
    asset_manifest: dict | None = None,
) -> dict:
    """Return ParsedIdea fields plus V3 prompt package for the idea card UI."""

    data = parsed.__dict__.copy()
    try:
        if asset_manifest is None:
            asset_manifest = _load_manifest(_asset_manifest_path(slug)) if slug else {}
        data["prompt_package"] = build_thumbnail_prompt_package(
            parsed,
            idea_index=idea_index,
            asset_manifest=asset_manifest,
        ).to_dict()
    except Exception:  # noqa: BLE001 - prompt package should never break cards
        logger.exception("thumbnail v3 prompt package failed (idea_index=%s)", idea_index)
        data["prompt_package_error"] = "prompt package unavailable"
    return data


def _render_single_idea_card(request: Request, slug: str, idx: int, raw: str) -> Response:
    """Render the single-idea-card partial (used by save-edit + render swap targets).

    Parses ``raw`` and surfaces the structured preview alongside the editable
    textarea. If parsing fails, surfaces the parse error inline so 修修 can
    fix the textarea without leaving the card.
    """
    parsed_dict: dict | None = None
    parse_error: str | None = None
    try:
        parsed = parse_idea(raw)
        parsed_dict = _parsed_idea_for_template(parsed, slug=slug, idea_index=idx)
    except (IdeaParseError, EmotionLookupError) as exc:
        parse_error = str(exc)

    return _templates.TemplateResponse(
        request,
        "projects/_thumbnail_idea_card_single.html",
        {
            "slug": slug,
            "idea": {
                "index": idx,
                "raw": raw,
                "parsed": parsed_dict,
                "parse_error": parse_error,
            },
        },
    )


@page_router.post("/projects/{slug}/thumbnail/idea/{idx}")
async def thumbnail_idea_save_edit(
    request: Request,
    slug: str,
    idx: int,
    value: str = Form(...),
    nakama_auth: str | None = Cookie(None),
):
    """Save 修修's edited idea at index ``idx`` (B-min.1).

    Re-parses the new text + writes ``thumbnail_ideas[idx]`` back to frontmatter.
    Returns the single-card partial with refreshed parse preview (or inline
    parse error if the edit broke the 5-line format).
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    ideas_raw: list[str] = list(raw_fm.get("thumbnail_ideas") or [])
    if idx < 0 or idx >= len(ideas_raw):
        raise HTTPException(
            status_code=400,
            detail=f"idea index {idx} out of range (have {len(ideas_raw)} ideas)",
        )

    new_value = value.strip()
    if not new_value:
        raise HTTPException(status_code=400, detail="idea text cannot be empty")

    ideas_raw[idx] = new_value
    try:
        update_frontmatter(
            vault_root=get_vault_path(),
            slug=slug,
            patch={"thumbnail_ideas": ideas_raw},
        )
    except ProjectWriteError as exc:
        logger.exception("idea save-edit write-back failed: slug=%s idx=%d", slug, idx)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    parsed_for_assets: list[ParsedIdea] = []
    try:
        parsed_for_assets = [parse_idea(raw) for raw in ideas_raw]
    except (IdeaParseError, EmotionLookupError):
        parsed_for_assets = []
    if parsed_for_assets:
        _persist_thumbnail_asset_manifest(
            slug=slug,
            ideas=parsed_for_assets,
            source={"kind": "thumbnail_idea_save_edit", "edited_index": idx},
        )

    response = _render_single_idea_card(request, slug, idx, new_value)
    return _trigger_asset_manifest_refresh(response)


@page_router.post("/projects/{slug}/thumbnail/brainstorm/idea/{idx}")
async def thumbnail_idea_reroll(
    request: Request,
    slug: str,
    idx: int,
    nakama_auth: str | None = Cookie(None),
):
    """Re-roll a single idea slot (B-min.2), keeping the other 2 verbatim.

    Calls the brainstorm LLM with selective re-roll mode: instructs the model
    to keep the kept indices unchanged + produce a fresh variant at ``idx``
    that differs along ≥2 diversity axes from the kept ones. Persists the
    full updated list (kept + new) to frontmatter and returns the full 3-card
    grid for HTMX swap.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if entry.content_type not in ("youtube", "podcast"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"thumbnail brainstorm supports content_type=youtube|podcast; "
                f"got {entry.content_type!r}."
            ),
        )

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    ideas_raw: list[str] = list(raw_fm.get("thumbnail_ideas") or [])
    if idx < 0 or idx >= len(ideas_raw):
        raise HTTPException(
            status_code=400,
            detail=f"idea index {idx} out of range (have {len(ideas_raw)} ideas)",
        )

    keep_indices = [i for i in range(len(ideas_raw)) if i != idx]
    kept_raw = [ideas_raw[i] for i in keep_indices]
    title_inputs, title_input_source = _thumbnail_title_inputs(slug, entry)

    user_parts = _brainstorm_user_message(
        title_candidates=title_inputs,
        one_sentence=str(raw_fm.get("one_sentence") or ""),
        search_topic=str(raw_fm.get("search_topic") or ""),
        content_type=entry.content_type,
        title_input_source=title_input_source,
        keep_idea_indices=keep_indices,
        kept_ideas_raw=kept_raw,
    )

    system_prompt = _load_brainstorm_prompt(entry.content_type)
    messages = [{"role": "user", "content": user_parts}]
    model = get_model(agent="bridge", task="thumbnail_brainstorm")

    try:
        response_text = await asyncio.to_thread(
            ask_claude_multi,
            messages,
            system=system_prompt,
            model=model,
            max_tokens=2048,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("idea re-roll LLM call failed: slug=%s idx=%d", slug, idx)
        raise HTTPException(status_code=500, detail=f"重抽失敗：{exc}") from exc

    try:
        new_ideas = parse_ideas_batch(response_text)
    except (IdeaParseError, EmotionLookupError) as exc:
        logger.warning(
            "idea re-roll parse failed: slug=%s idx=%d err=%s body=%s",
            slug,
            idx,
            exc,
            response_text[:500],
        )
        raise HTTPException(
            status_code=502,
            detail=(f"LLM 回應無法解析 6-line idea format: {exc}. 請重試或回報。"),
        ) from exc

    new_blocks = _split_response_into_blocks(response_text, len(new_ideas))
    if not new_blocks:
        raise HTTPException(status_code=502, detail="LLM 沒有產出任何 idea — 請重試。")

    if entry.content_type == "youtube":
        response_text, new_ideas = _normalize_youtube_v2_response_text(response_text, new_ideas)
        try:
            _validate_youtube_v2_ideas(new_ideas)
        except ValueError as exc:
            logger.warning(
                "idea re-roll contract failed: slug=%s idx=%d err=%s body=%s",
                slug,
                idx,
                exc,
                response_text[:500],
            )
            raise HTTPException(
                status_code=502,
                detail=f"LLM 回應不符合 YouTube thumbnail v2 contract: {exc}",
            ) from exc

    if entry.content_type == "youtube":
        new_blocks = _split_response_into_blocks(response_text, len(new_ideas))

    # Pick the new idea at the same target index if LLM honoured the slot;
    # otherwise fall back to the first new block.
    if idx < len(new_blocks) and "大字" in new_blocks[idx]:
        ideas_raw[idx] = new_blocks[idx]
    else:
        ideas_raw[idx] = new_blocks[0]

    try:
        update_frontmatter(
            vault_root=get_vault_path(),
            slug=slug,
            patch={"thumbnail_ideas": ideas_raw},
        )
    except ProjectWriteError as exc:
        logger.exception("idea re-roll write-back failed: slug=%s idx=%d", slug, idx)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    _persist_brainstorm_meta(
        slug=slug,
        content_type=entry.content_type,
        ideas=new_ideas,
        raw_response=response_text,
        model=model,
    )

    # Parse full updated list for grid display
    parsed_all = []
    parsed_for_assets: list[ParsedIdea] = []
    for i, raw in enumerate(ideas_raw):
        try:
            p = parse_idea(raw)
            parsed_for_assets.append(p)
            parsed_all.append(
                {
                    "index": i,
                    "raw": raw,
                    "parsed": _parsed_idea_for_template(p, slug=slug, idea_index=i),
                    "parse_error": None,
                }
            )
        except (IdeaParseError, EmotionLookupError) as exc:
            parsed_all.append({"index": i, "raw": raw, "parsed": None, "parse_error": str(exc)})

    if len(parsed_for_assets) == len(ideas_raw):
        _persist_thumbnail_asset_manifest(
            slug=slug,
            ideas=parsed_for_assets,
            source={
                "kind": "thumbnail_idea_reroll",
                "rerolled_index": idx,
                "model": model,
                "title_input_source": title_input_source,
            },
        )

    response = _templates.TemplateResponse(
        request,
        "projects/_thumbnail_idea_cards.html",
        {"slug": slug, "ideas": parsed_all},
    )
    return _trigger_asset_manifest_refresh(response)


@page_router.post("/projects/{slug}/thumbnail/render")
async def thumbnail_render(
    request: Request,
    slug: str,
    idea_index: int = Form(...),
    director_notes: str = Form(""),
    stage: str = Form("full"),
    nakama_auth: str | None = Cookie(None),
):
    """Render one thumbnail candidate from frontmatter ``thumbnail_ideas[index]``.

    Writes the PNG to ``data/thumbnails/{slug}/runs/{ts}/v{idea_index}.png``
    and returns an HTMX partial pointing to the candidate-serving URL.

    ``director_notes`` (panel P8 / ADR-033 D3a) — optional refinement layer
    appended to the LLM-derived idea text. PR4-A does NOT re-run an LLM
    parse on notes; they are passed to the composition as a free-form
    ``director_notes`` variable for future composition versions to honour.
    Currently the composition ignores them; the field is preserved in the
    manifest for traceability.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    render_stage = (stage or "full").strip().lower()
    if render_stage in {"typography", "text"}:
        render_stage = "type"
    if render_stage in {"bg", "plate"}:
        render_stage = "background"
    if render_stage in {"component", "asset", "assets"}:
        render_stage = "components"
    if render_stage not in {"person", "layout", "type", "background", "components", "full"}:
        raise HTTPException(status_code=400, detail=f"invalid thumbnail render stage: {stage}")

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if entry.content_type == "podcast":
        render_stage = "full"

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    ideas_raw: list[str] = list(raw_fm.get("thumbnail_ideas") or [])
    if idea_index < 0 or idea_index >= len(ideas_raw):
        raise HTTPException(
            status_code=400,
            detail=(
                f"idea_index {idea_index} out of range — there are "
                f"{len(ideas_raw)} ideas. Run brainstorm first or fix the index."
            ),
        )

    idea_text = ideas_raw[idea_index]
    try:
        parsed = parse_idea(idea_text)
    except (IdeaParseError, EmotionLookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry.content_type == "youtube":
        parsed = _normalize_youtube_v2_policy_idea(parsed)

    vault = get_vault_path()

    ts = _run_ts()
    run_dir = _thumbnails_dir() / slug / "runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    out_png = run_dir / f"v{idea_index}.png"

    # Bg strategy (2026-05-27 pivot — Path A+B locked-template AI gen):
    # YouTube branch: GPT Image 1 edit produces the photographic scene driven
    # by a playbook T-V* template; CSS overlays the Chinese title on top.
    # Podcast branch: still uses static composition (no AI gen yet).
    bg_path = None  # Podcast still uses gradient bg; YouTube replaces this below.
    template = None  # Populated in the YouTube branch only
    ai_meta: dict | None = None
    cutout_casting = None
    person_placement = None
    person_preview_filename = None
    layout_blocking = None
    typography = None
    background_plate = None
    component_plan = None
    stage_preview_filename = None
    stage_label = "Rendered"
    full_render_filename = out_png.name
    candidate_for_qa = out_png

    try:
        if entry.content_type == "podcast":
            try:
                host_cutout_path = pick_podcast_host(slug, parsed.emotion_key, vault, raw_fm)
                # Guest emotion isn't picked by the brainstorm prompt — fall through
                # the resolver with host emotion; pick_podcast_guest falls back to
                # any active cutout when no filename match.
                guest_cutout_path = pick_podcast_guest(slug, parsed.emotion_key, vault, raw_fm)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=412, detail=str(exc)) from exc
            await render_podcast_still(
                title_hook=parsed.hook,
                host_cutout_path=host_cutout_path,
                guest_cutout_path=guest_cutout_path,
                bg_path=bg_path,
                out_png=out_png,
                accent_decoration=_display_accent_token(parsed.decoration),
                palette={"bg_darken": 0.55},
            )
        else:
            try:
                cutout_path, cutout_casting = _pick_youtube_host_with_reference_gate(parsed, vault)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=412, detail=str(exc)) from exc
            except CutoutCastingError as exc:
                raise HTTPException(status_code=412, detail=str(exc)) from exc

            # Step 6 must render from the same person/layout/type/background/
            # component artifacts reviewed in Step 1-5. This keeps the final
            # candidate aligned with the staged plan instead of drifting back
            # to the old broad Hyperframes T-V archetype renderer.
            template = get_template(parsed.archetype_tags)
            cutout_id = None
            if isinstance(cutout_casting, dict):
                selected = cutout_casting.get("selected") or {}
                cutout_id = selected.get("cutout_id")
                pose_tags = selected.get("tags") if isinstance(selected.get("tags"), dict) else None
            else:
                pose_tags = None
            person_preview_png = run_dir / f"v{idea_index}_person.png"
            person_placement_preview = render_person_placement_preview(
                cutout_path=cutout_path,
                out_png=person_preview_png,
                archetype=template.overlay_archetype,
                cutout_id=cutout_id,
                pose_tags=pose_tags,
            )
            person_placement = person_placement_preview.to_dict()
            person_preview_filename = person_preview_png.name
            if render_stage == "person":
                candidate_for_qa = person_preview_png
                stage_preview_filename = person_preview_png.name
                stage_label = "Step 1 - person placement"
                full_render_filename = None
            elif render_stage in {"layout", "type", "background", "components"}:
                layout_png = run_dir / f"v{idea_index}_layout.png"
                layout_preview = render_layout_blocking_preview(
                    out_png=layout_png,
                    person_placement=person_placement,
                    parsed_idea=parsed,
                    director_notes=director_notes,
                )
                layout_blocking = layout_preview.to_dict()
                if render_stage == "layout":
                    candidate_for_qa = layout_png
                    stage_preview_filename = layout_png.name
                    stage_label = "Step 2 - layout blocking"
                    full_render_filename = None
                elif render_stage == "type":
                    type_png = run_dir / f"v{idea_index}_type.png"
                    typography_preview = render_typography_preview(
                        out_png=type_png,
                        person_placement=person_placement,
                        layout_blocking=layout_blocking,
                        parsed_idea=parsed,
                        director_notes=director_notes,
                    )
                    typography = typography_preview.to_dict()
                    candidate_for_qa = type_png
                    stage_preview_filename = type_png.name
                    stage_label = "Step 3 - typography"
                    full_render_filename = None
                elif render_stage == "background":
                    background_png = run_dir / f"v{idea_index}_background.png"
                    background_preview = render_background_plate_preview(
                        out_png=background_png,
                        person_placement=person_placement,
                        layout_blocking=layout_blocking,
                        parsed_idea=parsed,
                        director_notes=director_notes,
                    )
                    background_plate = background_preview.to_dict()
                    typography = {"typography_spec": background_preview.typography_spec}
                    candidate_for_qa = background_png
                    stage_preview_filename = background_png.name
                    stage_label = "Step 4 - background plate"
                    full_render_filename = None
                else:
                    components_png = run_dir / f"v{idea_index}_components.png"
                    component_plan = _render_arrangement_component_plan(
                        out_png=components_png,
                        person_placement=person_placement,
                        layout_blocking=layout_blocking,
                        parsed_idea=parsed,
                        idea_index=idea_index,
                    )
                    candidate_for_qa = components_png
                    stage_preview_filename = components_png.name
                    stage_label = "Step 5 - component plan"
                    full_render_filename = None
            else:
                layout_png = run_dir / f"v{idea_index}_layout.png"
                layout_preview = render_layout_blocking_preview(
                    out_png=layout_png,
                    person_placement=person_placement,
                    parsed_idea=parsed,
                    director_notes=director_notes,
                )
                layout_blocking = layout_preview.to_dict()

                background_png = run_dir / f"v{idea_index}_background.png"
                background_preview = render_background_plate_preview(
                    out_png=background_png,
                    person_placement=person_placement,
                    layout_blocking=layout_blocking,
                    parsed_idea=parsed,
                    director_notes=director_notes,
                )
                background_plate = background_preview.to_dict()
                typography = {"typography_spec": background_preview.typography_spec}

                components_png = run_dir / f"v{idea_index}_components.png"
                component_plan = _render_arrangement_component_plan(
                    out_png=components_png,
                    person_placement=person_placement,
                    layout_blocking=layout_blocking,
                    parsed_idea=parsed,
                    idea_index=idea_index,
                )

                render_staged_youtube_thumbnail(
                    out_png=out_png,
                    parsed_idea=parsed,
                    person_placement=person_placement,
                    layout_blocking=layout_blocking,
                    component_plan=component_plan,
                    director_notes=director_notes,
                )
    except HTTPException:
        raise
    except ThumbnailRenderError as exc:
        logger.exception("thumbnail render failed: slug=%s idea=%d", slug, idea_index)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        # Missing cutout file / missing npx / missing composition asset — surface
        # the actual path so user can fix.
        logger.exception("thumbnail render file missing: slug=%s idea=%d", slug, idea_index)
        raise HTTPException(status_code=412, detail=f"檔案找不到：{exc}") from exc
    except Exception as exc:  # noqa: BLE001 — last resort so 500 has detail
        logger.exception("thumbnail render unexpected: slug=%s idea=%d", slug, idea_index)
        raise HTTPException(
            status_code=500, detail=f"渲圖失敗 ({type(exc).__name__}): {exc}"
        ) from exc

    visual_qa = evaluate_thumbnail_render(candidate_for_qa).to_dict()

    # Per-run manifest (audit traceability — see ADR-033 D7 §thumbnail_run).
    manifest_path = run_dir / "manifest.json"
    manifest = _load_manifest(manifest_path)
    manifest.setdefault("renders", []).append(
        {
            "idea_index": idea_index,
            "rendered_at": _now_iso(),
            "stage": render_stage,
            "filename": candidate_for_qa.name,
            "full_render_filename": full_render_filename,
            "parsed_idea": parsed.__dict__,
            "director_notes": director_notes,
            "cutout_casting": cutout_casting,
            "person_placement": person_placement,
            "layout_blocking": layout_blocking,
            "typography": typography,
            "background_plate": background_plate,
            "component_plan": component_plan,
            "template_tv_id": template.tv_id if template else None,
            "ai_image_gen": ai_meta,
            "visual_qa": visual_qa,
        }
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        record_api_call(
            agent="bridge",
            model="(thumbnail-render)",
            input_tokens=0,
            output_tokens=0,
            scope_json=json.dumps(
                {
                    "scope": "thumbnail_render",
                    "project": slug,
                    "content_type": entry.content_type,
                    "idea_index": idea_index,
                    "run_ts": ts,
                    "emotion": parsed.emotion_key,
                    "stage": render_stage,
                    "visual_qa_status": visual_qa.get("status"),
                    "reference_template": parsed.reference_template_id,
                },
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit record failed (non-fatal)")

    return _templates.TemplateResponse(
        request,
        "projects/_thumbnail_render_result.html",
        {
            "slug": slug,
            "idea_index": idea_index,
            "run_ts": ts,
            "filename": candidate_for_qa.name,
            "stage": render_stage,
            "stage_preview_filename": stage_preview_filename,
            "stage_label": stage_label,
            "full_render_filename": full_render_filename,
            "person_preview_filename": person_preview_filename,
            "person_placement": person_placement,
            "layout_blocking": layout_blocking,
            "typography": typography,
            "background_plate": background_plate,
            "component_plan": component_plan,
            "parsed": parsed.__dict__,
            "visual_qa": visual_qa,
        },
    )


def _render_arrangement_component_plan(
    *,
    out_png: Path,
    person_placement: dict,
    layout_blocking: dict | None,
    parsed_idea: ParsedIdea,
    idea_index: int,
) -> dict:
    reference_record = _reference_record_for_parsed_idea(parsed_idea)
    candidates = rank_arrangement_candidates(
        generate_arrangement_candidates(
            parsed_idea=parsed_idea,
            person_placement=person_placement,
            layout_blocking=layout_blocking,
            idea_index=idea_index + 1,
        ),
        reference_record=reference_record,
    )
    selected = candidates[0]
    render_arrangement_contact_sheet(
        out_png=out_png,
        candidates=candidates,
        selected_candidate_id=selected.candidate_id,
    )
    component_plan = candidate_to_component_plan(selected)
    component_plan["arrangement_candidates"] = [candidate.to_dict() for candidate in candidates]
    if reference_record is not None:
        summary = reference_summary(reference_record)
        component_plan["selected_reference"] = summary
        component_plan["selected_variant"]["reference_record"] = summary
        component_plan["selected_variant"]["component_compatibility"] = (
            score_arrangement_against_reference(
                candidate=selected,
                reference_record=reference_record,
            ).to_dict()
        )
    return component_plan


def _load_manifest(path: Path) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("manifest corrupt — starting fresh: %s", path)
    return {}


def _display_accent_token(decoration: str) -> str:
    """Convert a design instruction into the literal overlay token.

    The brainstorm brief may say ``6（清單卡片左上角...）``. The composition
    should display only ``6``; the parenthesized instruction belongs to later
    asset/layout stages, not to the CSS text overlay.
    """

    text = (decoration or "").strip()
    if not text:
        return ""
    match = _ACCENT_TOKEN_RE.match(text)
    if match:
        return match.group(1).strip()
    if "（" in text or "(" in text:
        return re.split(r"[（(]", text, maxsplit=1)[0].strip()
    return text if len(text) <= 8 else ""


def load_thumbnail_asset_manifest_for_ui(slug: str) -> dict:
    """Load the thumbnail asset manifest decorated for Bridge rendering."""

    slug = normalize_slug(slug)
    manifest = _load_manifest(_asset_manifest_path(slug))
    if not manifest:
        manifest = build_thumbnail_asset_manifest(
            slug=slug,
            ideas=[],
            generated_at=_now_iso(),
            source={"kind": "empty"},
        )
    return enrich_asset_manifest_for_ui(manifest)


def _parse_current_thumbnail_ideas(raw_frontmatter: dict | None) -> list[ParsedIdea]:
    fm = raw_frontmatter if isinstance(raw_frontmatter, dict) else {}
    raw_list = fm.get("thumbnail_ideas") or []
    parsed: list[ParsedIdea] = []
    for idx, raw in enumerate(raw_list, start=1):
        try:
            parsed.append(parse_idea(str(raw)))
        except (IdeaParseError, EmotionLookupError) as exc:
            raise ValueError(f"idea {idx} parse failed: {exc}") from exc
    return parsed


def _render_asset_manifest_response(request: Request, slug: str) -> Response:
    return _templates.TemplateResponse(
        request,
        "projects/_thumbnail_asset_manifest.html",
        {
            "slug": slug,
            "asset_manifest": load_thumbnail_asset_manifest_for_ui(slug),
        },
    )


def _trigger_asset_manifest_refresh(response: Response) -> Response:
    response.headers["HX-Trigger"] = "thumbnail-assets-changed"
    return response


def _direct_openai_thumbnail_prompt(prompt_package: dict, *, feedback: str = "") -> str:
    """Adapt Nakama's base-image package into a complete GPT Image thumbnail prompt."""

    strategy = prompt_package.get("visual_strategy") or {}
    lines = [
        "Create a complete, polished YouTube thumbnail for a health education creator.",
        "",
        "Use the uploaded host reference image as the creator identity reference.",
        "Preserve the creator's facial identity and make the face a dominant focal point.",
        "Generate episode-specific objects, panels, icons, arrows, and background directly from the prompt; do not require separate object/style reference images.",
        "",
        f"Paired YouTube title: {strategy.get('title_pairing') or ''}",
        f"Main subject: {strategy.get('subject') or ''}",
        f"Episode object/context: {strategy.get('object_group') or ''}",
        f"Curiosity promise: {strategy.get('curiosity') or ''}",
        f"Exact thumbnail headline text to render prominently: {prompt_package.get('overlay_text') or ''}",
        "",
        "Composition rules:",
        "- 16:9 YouTube thumbnail composition, cropped-safe for 1280x720 final export.",
        "- Use at most three focal points: creator face, one clear object/component, one curiosity/text element.",
        "- Face should be large and readable at mobile feed size.",
        "- Put the creator on a visual third unless the concept clearly needs a centered face.",
        "- Keep text short, bold, high contrast, and legible.",
        "",
        "Style rules:",
        "- Clean Ali Abdaal warmth plus Jeff Su component clarity.",
        "- Bright enough to stand out in YouTube feed, but credible and not scammy.",
        "- Premium creator thumbnail, not stock-photo generic.",
        "",
        "Hard negatives:",
        "- No medical miracle-cure framing, no hospital ad aesthetic, no supplement scam look.",
        "- No distorted face, hands, body, or unreadable text.",
        "- No extra random labels or unrelated logos.",
    ]
    if feedback.strip():
        lines.extend(["", f"Iteration feedback to follow: {feedback.strip()}"])
    return "\n".join(lines)


async def _save_external_thumbnail_upload(upload: UploadFile, out_png: Path) -> dict:
    """Normalize an external image-model output into a 1280x720 PNG candidate."""

    import io

    from PIL import UnidentifiedImageError

    max_bytes = 24 * 1024 * 1024
    blob = await upload.read(max_bytes + 1)
    if not blob:
        raise HTTPException(status_code=400, detail="uploaded image is empty")
    if len(blob) > max_bytes:
        raise HTTPException(status_code=413, detail="uploaded image is larger than 24MB")

    try:
        meta = _normalize_thumbnail_image_bytes(blob, out_png)
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=400, detail="uploaded file is not a readable image") from exc

    return {
        "original_filename": upload.filename or "",
        "content_type": upload.content_type or "",
        **meta,
    }


def _normalize_thumbnail_image_bytes(blob: bytes, out_png: Path) -> dict:
    """Crop-fit image bytes into Nakama's canonical 1280x720 PNG candidate."""

    import io

    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(blob)) as image:
        source_format = image.format or ""
        source_size = image.size
        normalized = ImageOps.fit(
            image.convert("RGB"),
            (1280, 720),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        out_png.parent.mkdir(parents=True, exist_ok=True)
        normalized.save(out_png, format="PNG", optimize=True)
    return {
        "source_format": source_format,
        "source_width": source_size[0],
        "source_height": source_size[1],
        "normalized_width": 1280,
        "normalized_height": 720,
    }


def _render_record_for_candidate(slug: str, run_ts: str, filename: str) -> dict | None:
    """Return the stored manifest record for a rendered candidate, if present."""

    manifest = _load_manifest(_thumbnails_dir() / slug / "runs" / run_ts / "manifest.json")
    renders = manifest.get("renders") if isinstance(manifest, dict) else None
    if not isinstance(renders, list):
        return None
    for render in reversed(renders):
        if not isinstance(render, dict):
            continue
        if render.get("filename") == filename:
            return render
    return None


def _visual_qa_for_candidate(slug: str, run_ts: str, filename: str) -> dict | None:
    """Return the stored visual QA result for a rendered candidate, if present."""

    render = _render_record_for_candidate(slug, run_ts, filename)
    if render and isinstance(render.get("visual_qa"), dict):
        return render["visual_qa"]
    return None


@page_router.get("/projects/{slug}/thumbnail/assets")
async def thumbnail_assets(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Render the asset sourcing manifest panel for the T&T tab."""

    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _render_asset_manifest_response(request, slug)


@page_router.post("/projects/{slug}/thumbnail/assets/rebuild")
async def thumbnail_assets_rebuild(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Rebuild asset needs from the current frontmatter thumbnail ideas."""

    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        ideas = _parse_current_thumbnail_ideas(
            entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not ideas:
        raise HTTPException(status_code=400, detail="no thumbnail_ideas to rebuild from")

    manifest = build_thumbnail_asset_manifest(
        slug=slug,
        ideas=ideas,
        generated_at=_now_iso(),
        source={"kind": "thumbnail_asset_manifest_rebuild"},
    )
    manifest = merge_existing_asset_provenance(
        manifest,
        _load_manifest(_asset_manifest_path(slug)),
    )
    try:
        _write_thumbnail_asset_manifest(slug, manifest)
    except OSError as exc:
        logger.exception("thumbnail asset manifest rebuild failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _render_asset_manifest_response(request, slug)


@page_router.post("/projects/{slug}/thumbnail/assets/{asset_need_id}")
async def thumbnail_asset_update(
    request: Request,
    slug: str,
    asset_need_id: str,
    nakama_auth: str | None = Cookie(None),
    status: str = Form(""),
    provider: str = Form(""),
    asset_url: str = Form(""),
    provider_asset_id: str = Form(""),
    author: str = Form(""),
    license_name: str = Form(""),
    license_registration: str = Form(""),
    downloaded_at: str = Form(""),
    local_path: str = Form(""),
    evidence_path: str = Form(""),
    notes: str = Form(""),
):
    """Update provenance for one asset need and re-render the full panel."""

    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    manifest_path = _asset_manifest_path(slug)
    manifest = _load_manifest(manifest_path)
    if not manifest:
        raise HTTPException(status_code=404, detail="asset manifest not found")

    provenance_patch = {
        "provider": provider,
        "asset_url": asset_url,
        "provider_asset_id": provider_asset_id,
        "author": author,
        "license_name": license_name,
        "license_registration": license_registration,
        "downloaded_at": downloaded_at,
        "local_path": local_path,
        "evidence_path": evidence_path,
        "notes": notes,
    }
    try:
        updated = update_asset_manifest_item(
            manifest,
            asset_need_id=asset_need_id,
            provenance_patch=provenance_patch,
            status=status or None,
        )
        _write_thumbnail_asset_manifest(slug, updated)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"asset need not found: {asset_need_id}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("thumbnail asset provenance update failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _render_asset_manifest_response(request, slug)


@page_router.post("/projects/{slug}/thumbnail/import")
async def thumbnail_external_import(
    request: Request,
    slug: str,
    idea_index: int = Form(...),
    provider_model: str = Form("gpt-image-2"),
    feedback: str = Form(""),
    generated_image: UploadFile = File(...),
    nakama_auth: str | None = Cookie(None),
):
    """Import an externally generated thumbnail as a commit-ready candidate.

    This is the V3 bridge path: Nakama owns the prompt/reference package and
    history, while GPT Image 2 / Prompt Edit / Scenario can generate the bitmap.
    """

    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    ideas_raw: list[str] = list(raw_fm.get("thumbnail_ideas") or [])
    if idea_index < 0 or idea_index >= len(ideas_raw):
        raise HTTPException(
            status_code=400,
            detail=f"idea_index {idea_index} out of range - there are {len(ideas_raw)} ideas",
        )

    try:
        parsed = parse_idea(str(ideas_raw[idea_index]))
    except (IdeaParseError, EmotionLookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry.content_type == "youtube":
        parsed = _normalize_youtube_v2_policy_idea(parsed)

    ts = _run_ts()
    run_dir = _thumbnails_dir() / slug / "runs" / ts
    out_png = run_dir / f"v{idea_index}_external.png"
    upload_meta = await _save_external_thumbnail_upload(generated_image, out_png)
    visual_qa = evaluate_thumbnail_render(out_png).to_dict()
    asset_manifest = _load_manifest(_asset_manifest_path(slug))
    prompt_package = build_thumbnail_prompt_package(
        parsed,
        idea_index=idea_index,
        asset_manifest=asset_manifest,
        model=(provider_model or "gpt-image-2").strip() or "gpt-image-2",
    ).to_dict()

    manifest_path = run_dir / "manifest.json"
    manifest = _load_manifest(manifest_path)
    manifest.setdefault("renders", []).append(
        {
            "idea_index": idea_index,
            "rendered_at": _now_iso(),
            "stage": "full",
            "filename": out_png.name,
            "source": "external_image_model_import",
            "provider_model": (provider_model or "").strip(),
            "feedback": feedback.strip(),
            "upload": upload_meta,
            "parsed_idea": parsed.__dict__,
            "prompt_package": prompt_package,
            "visual_qa": visual_qa,
        }
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        record_api_call(
            agent="bridge",
            model="(thumbnail-external-import)",
            input_tokens=0,
            output_tokens=0,
            scope_json=json.dumps(
                {
                    "scope": "thumbnail_external_import",
                    "project": slug,
                    "idea_index": idea_index,
                    "run_ts": ts,
                    "filename": out_png.name,
                    "provider_model": (provider_model or "").strip(),
                    "visual_qa_status": visual_qa.get("status"),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit record failed (non-fatal)")

    return _templates.TemplateResponse(
        request,
        "projects/_thumbnail_render_result.html",
        {
            "slug": slug,
            "idea_index": idea_index,
            "run_ts": ts,
            "filename": out_png.name,
            "stage": "full",
            "stage_preview_filename": None,
            "stage_label": "Imported external thumbnail",
            "full_render_filename": out_png.name,
            "person_preview_filename": None,
            "person_placement": None,
            "layout_blocking": None,
            "typography": None,
            "background_plate": None,
            "component_plan": None,
            "parsed": parsed.__dict__,
            "visual_qa": visual_qa,
        },
    )


@page_router.post("/projects/{slug}/thumbnail/generate-openai")
async def thumbnail_generate_openai(
    request: Request,
    slug: str,
    idea_index: int = Form(...),
    quality: str = Form("medium"),
    feedback: str = Form(""),
    reference_mode: str = Form("host_only"),
    nakama_auth: str | None = Cookie(None),
):
    """Generate a complete thumbnail candidate directly from the Web UI."""

    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    ideas_raw: list[str] = list(raw_fm.get("thumbnail_ideas") or [])
    if idea_index < 0 or idea_index >= len(ideas_raw):
        raise HTTPException(
            status_code=400,
            detail=f"idea_index {idea_index} out of range - there are {len(ideas_raw)} ideas",
        )

    try:
        parsed = parse_idea(str(ideas_raw[idea_index]))
    except (IdeaParseError, EmotionLookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry.content_type == "youtube":
        parsed = _normalize_youtube_v2_policy_idea(parsed)

    if entry.content_type != "youtube":
        raise HTTPException(status_code=400, detail="OpenAI direct generation is youtube-only for now")

    try:
        host_reference_path, cutout_casting = _pick_youtube_host_with_reference_gate(
            parsed,
            get_vault_path(),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc
    except CutoutCastingError as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc

    asset_manifest = _load_manifest(_asset_manifest_path(slug))
    model = os.environ.get("THUMBNAIL_AI_MODEL") or "gpt-image-2"
    prompt_package = build_thumbnail_prompt_package(
        parsed,
        idea_index=idea_index,
        asset_manifest=asset_manifest,
        host_reference_path=str(host_reference_path),
        model=model,
    ).to_dict()
    prompt = _direct_openai_thumbnail_prompt(prompt_package, feedback=feedback)

    ts = _run_ts()
    run_dir = _thumbnails_dir() / slug / "runs" / ts
    raw_png = run_dir / f"v{idea_index}_openai_raw.png"
    out_png = run_dir / f"v{idea_index}_openai.png"

    references = [host_reference_path]
    if reference_mode == "host_style":
        style_path = ""
        for binding in prompt_package.get("reference_bindings") or []:
            if binding.get("role") == "style_reference":
                style_path = str(binding.get("local_path") or "")
                break
        if style_path and Path(style_path).is_file():
            references.append(Path(style_path))

    try:
        ai_meta = await generate_thumbnail_from_references(
            prompt=prompt,
            out_png=raw_png,
            reference_images=references,
            quality=quality,
            model=model,
        )
    except AIImageGenError as exc:
        logger.exception("OpenAI thumbnail generation failed: slug=%s idea=%s", slug, idea_index)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    upload_meta = _normalize_thumbnail_image_bytes(raw_png.read_bytes(), out_png)
    visual_qa = evaluate_thumbnail_render(out_png).to_dict()

    manifest_path = run_dir / "manifest.json"
    manifest = _load_manifest(manifest_path)
    manifest.setdefault("renders", []).append(
        {
            "idea_index": idea_index,
            "rendered_at": _now_iso(),
            "stage": "full",
            "filename": out_png.name,
            "source": "openai_direct_generation",
            "provider_model": model,
            "quality": quality,
            "feedback": feedback.strip(),
            "reference_mode": reference_mode,
            "references": [str(path) for path in references],
            "upload": upload_meta,
            "parsed_idea": parsed.__dict__,
            "prompt_package": prompt_package,
            "direct_prompt": prompt,
            "cutout_casting": cutout_casting,
            "ai_image_gen": ai_meta,
            "visual_qa": visual_qa,
        }
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        record_api_call(
            agent="bridge",
            model="(thumbnail-openai-direct)",
            input_tokens=0,
            output_tokens=0,
            scope_json=json.dumps(
                {
                    "scope": "thumbnail_openai_direct",
                    "project": slug,
                    "idea_index": idea_index,
                    "run_ts": ts,
                    "filename": out_png.name,
                    "provider_model": model,
                    "quality": quality,
                    "visual_qa_status": visual_qa.get("status"),
                    "estimated_output_cost_usd": ai_meta.get("estimated_output_cost_usd"),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit record failed (non-fatal)")

    return _templates.TemplateResponse(
        request,
        "projects/_thumbnail_render_result.html",
        {
            "slug": slug,
            "idea_index": idea_index,
            "run_ts": ts,
            "filename": out_png.name,
            "stage": "full",
            "stage_preview_filename": None,
            "stage_label": "OpenAI direct thumbnail",
            "full_render_filename": out_png.name,
            "person_preview_filename": None,
            "person_placement": None,
            "layout_blocking": None,
            "typography": None,
            "background_plate": None,
            "component_plan": None,
            "parsed": parsed.__dict__,
            "visual_qa": visual_qa,
        },
    )


@page_router.get("/projects/{slug}/thumbnail/candidate/{run_ts}/{filename}")
async def thumbnail_candidate(
    slug: str,
    run_ts: str,
    filename: str,
    nakama_auth: str | None = Cookie(None),
):
    """Serve a candidate PNG out of the gitignored ``data/thumbnails/...`` tree."""
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)
    slug = normalize_slug(slug)
    # Validate filename to prevent traversal: must be alphanumeric + dot/underscore + .png
    safe = _safe_filename(filename)
    if safe is None:
        raise HTTPException(status_code=400, detail="invalid filename")
    safe_ts = _safe_ts(run_ts)
    if safe_ts is None:
        raise HTTPException(status_code=400, detail="invalid run_ts")
    path = _thumbnails_dir() / slug / "runs" / safe_ts / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"candidate missing: {filename}")
    return FileResponse(path, media_type="image/png")


def _safe_filename(name: str) -> str | None:
    import re as _re

    if not _re.fullmatch(r"[A-Za-z0-9._-]+\.png", name):
        return None
    return name


def _safe_ts(ts: str) -> str | None:
    import re as _re

    if not _re.fullmatch(r"\d{8}T\d{6}", ts):
        return None
    return ts


@page_router.post("/projects/{slug}/thumbnail/commit")
async def thumbnail_commit(
    request: Request,
    slug: str,
    run_ts: str = Form(...),
    filename: str = Form(...),
    nakama_auth: str | None = Cookie(None),
):
    """Promote a candidate PNG to the project's canonical thumbnail.

    Steps (ADR-033 D7):
      1. Validate the candidate exists under data/thumbnails/{slug}/runs/{ts}/.
      2. If a vault thumbnail already exists, move it to ``_archive/{old_ts}.png``.
      3. Copy the candidate to vault ``Attachments/projects/{slug}/thumbnail.png``.
      4. Update frontmatter (thumbnail, thumbnail_chosen_at, thumbnail_run).
      5. Append an audit row to state.db.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    safe = _safe_filename(filename)
    safe_ts = _safe_ts(run_ts)
    if safe is None or safe_ts is None:
        raise HTTPException(status_code=400, detail="invalid run_ts or filename")

    candidate = _thumbnails_dir() / slug / "runs" / safe_ts / safe
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"candidate missing: {candidate}")

    render_record = _render_record_for_candidate(slug, safe_ts, safe)
    if render_record and render_record.get("stage") not in (None, "full"):
        raise HTTPException(
            status_code=412,
            detail=(
                "Only full thumbnail renders can be committed; finish the staged workflow first."
            ),
        )

    visual_qa = _visual_qa_for_candidate(slug, safe_ts, safe)
    if visual_qa and visual_qa.get("status") == "fail":
        raise HTTPException(
            status_code=412,
            detail=(
                "Visual QA failed for this thumbnail candidate; "
                "render a fixed candidate before commit."
            ),
        )

    vault = get_vault_path()
    project_attach_dir = vault / "Attachments" / "projects" / slug
    archive_dir = project_attach_dir / "_archive"
    project_attach_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    chosen_path = project_attach_dir / "thumbnail.png"

    # Archive any existing chosen thumbnail before overwrite.
    if chosen_path.is_file():
        archived = archive_dir / f"{_run_ts()}.png"
        shutil.move(str(chosen_path), str(archived))
        logger.info("archived previous thumbnail → %s", archived)

    # Copy candidate into vault atomically (write to tmp then rename).
    tmp_path = chosen_path.with_suffix(".png.tmp")
    shutil.copy2(candidate, tmp_path)
    tmp_path.replace(chosen_path)

    vault_rel = f"Attachments/projects/{slug}/{chosen_path.name}"

    try:
        update_frontmatter(
            vault_root=vault,
            slug=slug,
            patch={
                "thumbnail": vault_rel,
                "thumbnail_chosen_at": _now_iso(),
                "thumbnail_run": f"{safe_ts}/{safe}",
            },
        )
    except ProjectWriteError as exc:
        logger.exception("thumbnail commit write-back failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        record_api_call(
            agent="bridge",
            model="(thumbnail-commit)",
            input_tokens=0,
            output_tokens=0,
            scope_json=json.dumps(
                {
                    "scope": "thumbnail_commit",
                    "project": slug,
                    "run": f"{safe_ts}/{safe}",
                    "vault_path": vault_rel,
                    "visual_qa_status": visual_qa.get("status") if visual_qa else None,
                },
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit record failed (non-fatal)")

    return _templates.TemplateResponse(
        request,
        "projects/_thumbnail_commit_result.html",
        {
            "slug": slug,
            "thumbnail_path": vault_rel,
            "chosen_at": _now_iso(),
        },
    )


@page_router.post("/projects/{slug}/thumbnail/brainstorm-titles")
async def thumbnail_brainstorm_titles(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
    archetypes: str = Form(""),
    locked: list[str] = Form([]),
    value: str = Form(""),
):
    """Brainstorm-or-iterate title candidates with playbook archetype constraint.

    Form fields:
      - ``archetypes`` — CSV string like ``"T-A1,T-A3,T-A8"`` of archetype IDs
        the user wants. Empty → LLM picks 3 best for the topic.
      - ``locked`` — repeated form field of indices (``"0"``, ``"2"``) of titles
        in ``value`` that should be kept verbatim across this iteration.
      - ``value`` — current textarea content (newline-separated titles) from a
        previous brainstorm round. Empty on the very first round.

    Behaviour:
      - Fresh round (no locked, no current): generate 3 titles from selected
        archetypes (or LLM picks if empty).
      - Iterate round (some locked indices): keep those titles at their
        positions, generate fresh variants for the remaining slots, constrained
        to selected archetypes if any.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}

    # Parse inputs
    archetype_ids = [a.strip() for a in archetypes.split(",") if a.strip()]
    locked_indices: set[int] = set()
    for raw in locked:
        try:
            locked_indices.add(int(raw))
        except (TypeError, ValueError):
            continue
    current_titles = [ln.strip() for ln in value.splitlines() if ln.strip()]

    target_count = 3
    # Build the kept map — only indices that exist in current_titles AND were locked.
    kept_titles: dict[int, str] = {
        i: current_titles[i] for i in locked_indices if 0 <= i < len(current_titles)
    }
    # Cap kept to target_count (in case user locked >3 — defensive).
    if len(kept_titles) > target_count:
        kept_titles = dict(list(kept_titles.items())[:target_count])
    fresh_needed = target_count - len(kept_titles)

    # If nothing to generate (all 3 locked), just save and return without LLM call.
    if fresh_needed <= 0:
        merged = [kept_titles.get(i, "") for i in range(target_count)]
        merged = [t for t in merged if t]  # drop empty
        try:
            update_frontmatter(
                vault_root=get_vault_path(), slug=slug, patch={"title_candidates": merged}
            )
        except ProjectWriteError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return _templates.TemplateResponse(
            request,
            "projects/_thumbnail_title_edit_pane.html",
            {
                "title_candidates": merged,
                "locked_indices": list(locked_indices & set(range(len(merged)))),
                "selected_archetypes": archetype_ids,
            },
        )

    # Build the LLM request brief
    brief_lines: list[str] = [
        "## Project brief",
        "",
        f"- search_topic: {str(raw_fm.get('search_topic') or '（未填）')}",
        f"- one_sentence: {str(raw_fm.get('one_sentence') or '（未填）')}",
        f"- hook_text: {str(raw_fm.get('hook_text') or '（未填）')}",
        "",
        "## Request",
        "",
    ]
    if archetype_ids:
        brief_lines.append(f"- Use these archetypes (in order): {', '.join(archetype_ids)}")
    else:
        brief_lines.append("- No archetype constraint — pick the 3 best archetypes for this topic.")
    if kept_titles:
        brief_lines.append(
            f"- Locked titles ({len(kept_titles)} of {target_count}) — DO NOT re-emit, "
            "but ensure new variants attack different angles:"
        )
        for i in sorted(kept_titles):
            brief_lines.append(f"  - position {i}: {kept_titles[i]}")
    brief_lines.append(
        f"\nGenerate exactly **{fresh_needed}** fresh title(s) for the unlocked positions. "
        "Output one title per line, no numbering, no preamble. "
        "The fresh titles must be materially different in tone/structure from "
        "any locked titles above."
    )
    brief_text = "\n".join(brief_lines)

    system_prompt = _load_titles_prompt()
    messages = [{"role": "user", "content": brief_text}]
    model = get_model(agent="bridge", task="thumbnail_brainstorm")

    try:
        response_text = await asyncio.to_thread(
            ask_claude_multi,
            messages,
            system=system_prompt,
            model=model,
            max_tokens=512,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("title brainstorm LLM call failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=f"Title brainstorm 失敗：{exc}") from exc

    fresh_titles = _extract_title_lines(response_text)[:fresh_needed]
    if not fresh_titles:
        raise HTTPException(
            status_code=502,
            detail="LLM 沒有輸出任何標題候選，請重試。",
        )

    # Merge: locked at their positions, fresh fill the unlocked positions in order.
    merged: list[str] = []
    fresh_iter = iter(fresh_titles)
    for i in range(target_count):
        if i in kept_titles:
            merged.append(kept_titles[i])
        else:
            merged.append(next(fresh_iter, ""))
    merged = [t for t in merged if t]  # drop trailing empties if LLM under-produced

    try:
        update_frontmatter(
            vault_root=get_vault_path(),
            slug=slug,
            patch={"title_candidates": merged},
        )
    except ProjectWriteError as exc:
        logger.exception("title brainstorm write-back failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        record_api_call(
            agent="bridge",
            model=model,
            input_tokens=0,
            output_tokens=0,
            scope_json=json.dumps(
                {
                    "scope": "title_brainstorm",
                    "project": slug,
                    "n_titles": len(merged),
                    "archetypes": archetype_ids,
                    "n_locked": len(kept_titles),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit record failed (non-fatal)")

    return _templates.TemplateResponse(
        request,
        "projects/_thumbnail_title_edit_pane.html",
        {
            "title_candidates": merged,
            # After iterate, retain the user's lock selections for next round —
            # they probably want to keep locking until they hit the variant they like.
            "locked_indices": list(locked_indices & set(range(len(merged)))),
            "selected_archetypes": archetype_ids,
        },
    )


@page_router.post("/projects/{slug}/thumbnail/brainstorm-titles/idea/{idx}")
async def thumbnail_brainstorm_title_reroll(
    request: Request,
    slug: str,
    idx: int,
    value: str = Form(""),  # current textarea content (may have unsaved edits)
    nakama_auth: str | None = Cookie(None),
):
    """Re-roll a single title at index ``idx`` (B-min.3).

    Reads the current textarea value (3 newline-separated titles) so we honour
    any unsaved 修修 edits. Calls LLM with "keep titles at other indices verbatim,
    generate one fresh variant for idx". Writes back the merged list (kept + new)
    to frontmatter and returns the textarea partial.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Parse current titles from POSTed textarea (preserves unsaved edits).
    # Fall back to frontmatter if textarea was empty.
    current_titles = [ln.strip() for ln in value.splitlines() if ln.strip()]
    if not current_titles:
        raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
        current_titles = [
            str(t).strip() for t in (raw_fm.get("title_candidates") or []) if str(t).strip()
        ]

    if not current_titles:
        raise HTTPException(
            status_code=400,
            detail="先 brainstorm 或填至少一條標題再 re-roll。",
        )
    if idx < 0 or idx >= len(current_titles):
        raise HTTPException(
            status_code=400,
            detail=f"title index {idx} out of range (have {len(current_titles)} titles)",
        )

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    kept = [t for i, t in enumerate(current_titles) if i != idx]

    brief_text = (
        f"## Project brief\n\n"
        f"- search_topic: {str(raw_fm.get('search_topic') or '（未填）')}\n"
        f"- one_sentence: {str(raw_fm.get('one_sentence') or '（未填）')}\n"
        f"- hook_text: {str(raw_fm.get('hook_text') or '（未填）')}\n\n"
        f"## Selective re-roll mode\n\n"
        f"The user wants to KEEP these {len(kept)} title(s) verbatim and only generate "
        f"ONE fresh new variant that differs from them along ≥1 of the angles"
        f" listed in the system prompt. "
        f"Output ONLY the single new title — no numbering, no preamble, no quotes,"
        f" just the title text on its own line.\n\n"
        f"### Keep verbatim (do NOT re-output these)\n\n"
    )
    for t in kept:
        brief_text += f"- {t}\n"
    brief_text += (
        "\n### Your task\n"
        "Produce exactly ONE new title (繁體中文, ≤80 chars) that:\n"
        "1. Attacks a different angle from the kept titles above"
        " (numbered list / question / contrarian / authority / cost-risk"
        " / time-age / counter-intuitive specific).\n"
        "2. Is materially different in tone or structure from the kept ones.\n"
        "Output: just the single title string on one line."
    )

    system_prompt = _load_titles_prompt()
    messages = [{"role": "user", "content": brief_text}]
    model = get_model(agent="bridge", task="thumbnail_brainstorm")

    try:
        response_text = await asyncio.to_thread(
            ask_claude_multi,
            messages,
            system=system_prompt,
            model=model,
            max_tokens=256,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("title re-roll LLM call failed: slug=%s idx=%d", slug, idx)
        raise HTTPException(status_code=500, detail=f"重抽失敗：{exc}") from exc

    new_titles = _extract_title_lines(response_text)
    if not new_titles:
        raise HTTPException(
            status_code=502,
            detail="LLM 沒輸出新標題，請重試。",
        )

    # Merge: kept (at original indices) + new at idx
    merged = list(current_titles)
    merged[idx] = new_titles[0]

    try:
        update_frontmatter(
            vault_root=get_vault_path(),
            slug=slug,
            patch={"title_candidates": merged},
        )
    except ProjectWriteError as exc:
        logger.exception("title re-roll write-back failed: slug=%s idx=%d", slug, idx)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        record_api_call(
            agent="bridge",
            model=model,
            input_tokens=0,
            output_tokens=0,
            scope_json=json.dumps(
                {
                    "scope": "title_brainstorm_reroll",
                    "project": slug,
                    "idx": idx,
                    "n_kept": len(kept),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit record failed (non-fatal)")

    return _templates.TemplateResponse(
        request,
        "projects/_thumbnail_title_candidates_textarea.html",
        {"title_candidates": merged},
    )


def _extract_title_lines(response_text: str) -> list[str]:
    """Pull up to 3 non-empty, non-numbered lines from the LLM response.

    Strips common LLM artefacts (bullet markers, numbering, leading
    whitespace). Caps at 3 titles per ADR-033 D2 (YT Test & Compare slot).
    """
    cleaned: list[str] = []
    for raw in response_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Drop bullet / list markers
        line = line.lstrip("-•·*").lstrip()
        # Drop leading "1." / "1)" / "(1)" patterns
        import re as _re

        line = _re.sub(r"^\(?\d+[.)]\s*", "", line)
        # Drop trailing punctuation common in LLM enumeration
        line = line.rstrip("。.")
        if not line:
            continue
        # Drop obvious preamble lines
        if any(line.startswith(p) for p in ("Here", "以下", "三個", "三條", "標題候選", "Title")):
            continue
        cleaned.append(line)
        if len(cleaned) == 3:
            break
    return cleaned


# Podcast funnel endpoints (ADR-033 D8 — host/guest cutout extraction)


def _funnel_dir(slug: str, role: str, ts: str) -> Path:
    """Where ffmpeg-extracted frame candidates land before u2net."""
    return _thumbnails_dir() / slug / "funnel" / role / ts


def _resolve_video_path(raw_value: str) -> Path:
    """Frontmatter ``host_video_path`` / ``guest_video_path`` → absolute Path.

    Relative paths resolve against the repo root (so frontmatter can be portable
    across machines if 修修 commits the video into ``data/podcasts/``).

    Defense-in-depth: even though frontmatter is single-user-controlled today,
    refuse to resolve outside the repo root. Future imports / sharing flows
    could feed less-trusted frontmatter; this prevents the funnel from reading
    arbitrary ffmpeg-readable files on the host.
    """
    p = Path(raw_value)
    if not p.is_absolute():
        p = _REPO_ROOT / p
    resolved = p.resolve()
    repo_root = _REPO_ROOT.resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"video path escapes repo root: {raw_value!r} → {resolved}") from exc
    return resolved


@page_router.post("/projects/{slug}/thumbnail/podcast/funnel/{role}")
async def thumbnail_podcast_funnel(
    request: Request,
    slug: str,
    role: str,
    nakama_auth: str | None = Cookie(None),
):
    """Run D8 Stages 1+2 against host or guest source video.

    Reads ``{role}_video_path`` from frontmatter, extracts candidate frames via
    :func:`shared.thumbnail_funnel.run`, returns an HTMX partial with the
    candidate grid. 修修 picks 1-3 via the active-cutouts endpoint.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    if role not in {"host", "guest"}:
        raise HTTPException(status_code=400, detail=f"role must be 'host' or 'guest'; got {role!r}")

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if entry.content_type != "podcast":
        raise HTTPException(
            status_code=400,
            detail=(f"podcast funnel applies to content_type=podcast; got {entry.content_type!r}."),
        )

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    field = f"{role}_video_path"
    raw_value = str(raw_fm.get(field) or "").strip()
    if not raw_value:
        raise HTTPException(
            status_code=412,
            detail=(
                f"frontmatter missing '{field}'. Fill it in with the path to the "
                f"{role}'s source video before running the funnel."
            ),
        )

    try:
        video_path = _resolve_video_path(raw_value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not video_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"video file does not exist: {video_path}",
        )

    ts = _run_ts()
    out_dir = _funnel_dir(slug, role, ts)

    try:
        # top_pct 0.5 instead of ADR-033 D8 default 0.25 — Stage 3 vision LLM
        # ranker is deferred per §OQ3, so 修修 needs more candidates visible in
        # the UI to manually pick from. Revisit when Stage 3 lands.
        candidates = await thumbnail_funnel.run(
            video_path, out_dir, mode="conversation", top_pct=0.5
        )
    except FunnelError as exc:
        logger.exception("podcast funnel failed: slug=%s role=%s", slug, role)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        record_api_call(
            agent="bridge",
            model="(thumbnail-funnel)",
            input_tokens=0,
            output_tokens=0,
            scope_json=json.dumps(
                {
                    "scope": "thumbnail_funnel",
                    "project": slug,
                    "role": role,
                    "run_ts": ts,
                    "n_candidates": len(candidates),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit record failed (non-fatal)")

    return _templates.TemplateResponse(
        request,
        "projects/_thumbnail_podcast_funnel_grid.html",
        {
            "slug": slug,
            "role": role,
            "run_ts": ts,
            "candidates": [
                {
                    "filename": c.path.name,
                    "timestamp_sec": c.timestamp_sec,
                    "sample_kind": c.sample_kind,
                    "sharpness": c.sharpness,
                }
                for c in candidates
            ],
        },
    )


@page_router.get("/projects/{slug}/thumbnail/podcast/funnel/{role}/{run_ts}/{filename}")
async def thumbnail_podcast_funnel_candidate(
    slug: str,
    role: str,
    run_ts: str,
    filename: str,
    nakama_auth: str | None = Cookie(None),
):
    """Serve a single funnel-extracted PNG from data/thumbnails/{slug}/funnel/..."""
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)
    if role not in {"host", "guest"}:
        raise HTTPException(status_code=400, detail=f"role must be 'host' or 'guest'; got {role!r}")
    slug = normalize_slug(slug)
    safe = _safe_filename(filename)
    safe_ts = _safe_ts(run_ts)
    if safe is None or safe_ts is None:
        raise HTTPException(status_code=400, detail="invalid run_ts or filename")
    path = _funnel_dir(slug, role, safe_ts) / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"candidate missing: {filename}")
    return FileResponse(path, media_type="image/png")


@page_router.post("/projects/{slug}/thumbnail/podcast/active-cutouts")
async def thumbnail_podcast_active_cutouts(
    request: Request,
    slug: str,
    role: str = Form(...),
    run_ts: str = Form(...),
    selected: list[str] = Form(...),
    nakama_auth: str | None = Cookie(None),
):
    """Confirm 1-3 funnel candidates → u2net → write to vault + frontmatter.

    Body fields:
        role: ``host`` or ``guest``.
        run_ts: identifies the funnel run dir under data/thumbnails/.
        selected: 1-3 candidate filenames (e.g. ``["frame_007.png",
            "frame_018.png"]``).

    Side effects (D8 Stage 5):
        1. ``npx hyperframes remove-background`` each selected candidate.
        2. Wipe existing ``Attachments/cutouts/podcast/{slug}/{role}_v*.png``.
        3. Write transparent PNGs to ``{role}_v1.png``, ``{role}_v2.png`` ...
        4. Update frontmatter ``thumbnail_active_cutouts.{role}`` with the new
           vault-relative paths (the other role's active list is preserved).

    Returns an HTMX partial confirming the active set.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)
    if role not in {"host", "guest"}:
        raise HTTPException(status_code=400, detail=f"role must be 'host' or 'guest'; got {role!r}")
    safe_ts = _safe_ts(run_ts)
    if safe_ts is None:
        raise HTTPException(status_code=400, detail="invalid run_ts")
    if not selected or len(selected) > 3:
        raise HTTPException(
            status_code=400,
            detail=f"select 1-3 candidates per role; got {len(selected)}",
        )

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if entry.content_type != "podcast":
        raise HTTPException(
            status_code=400,
            detail=(f"active-cutouts applies to content_type=podcast; got {entry.content_type!r}."),
        )

    funnel_dir = _funnel_dir(slug, role, safe_ts)
    if not funnel_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"funnel run missing: {funnel_dir}. Run the funnel first.",
        )

    safe_sources: list[Path] = []
    for raw_name in selected:
        safe = _safe_filename(raw_name)
        if safe is None:
            raise HTTPException(status_code=400, detail=f"invalid candidate filename: {raw_name}")
        src = funnel_dir / safe
        if not src.is_file():
            raise HTTPException(status_code=404, detail=f"candidate missing: {src}")
        safe_sources.append(src)

    vault = get_vault_path()
    cutout_dir = vault / "Attachments" / "cutouts" / "podcast" / slug
    cutout_dir.mkdir(parents=True, exist_ok=True)

    # Wipe existing for this role only — preserve guest cutouts when role=host.
    for old in cutout_dir.glob(f"{role}_v*.png"):
        old.unlink(missing_ok=True)

    new_paths: list[str] = []
    for i, src in enumerate(safe_sources, start=1):
        dst = cutout_dir / f"{role}_v{i}.png"
        try:
            await _u2net_cutout(src, dst)
        except U2NetError as exc:
            logger.exception("u2net failed: slug=%s role=%s src=%s", slug, role, src.name)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        new_paths.append(f"Attachments/cutouts/podcast/{slug}/{dst.name}")

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    active = dict(raw_fm.get("thumbnail_active_cutouts") or {})
    active[role] = new_paths
    try:
        update_frontmatter(
            vault_root=vault,
            slug=slug,
            patch={"thumbnail_active_cutouts": active},
        )
    except ProjectWriteError as exc:
        logger.exception("active-cutouts write-back failed: slug=%s role=%s", slug, role)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        record_api_call(
            agent="bridge",
            model="(thumbnail-u2net)",
            input_tokens=0,
            output_tokens=0,
            scope_json=json.dumps(
                {
                    "scope": "thumbnail_active_cutouts",
                    "project": slug,
                    "role": role,
                    "n_active": len(new_paths),
                    "from_run_ts": safe_ts,
                },
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit record failed (non-fatal)")

    return _templates.TemplateResponse(
        request,
        "projects/_thumbnail_podcast_active_cutouts.html",
        {
            "slug": slug,
            "role": role,
            "paths": new_paths,
        },
    )


async def _render_pool_brainstorm_call(
    *,
    project_brief: dict[str, str],
    anchor_titles: list[str],
) -> list[tuple[str, str]]:
    """Shared LLM dispatch for both brainstorm + iterate.

    Builds the v3 user message (project brief + optional anchor list), calls
    Claude with the v3 system prompt, parses the grouped schema back.
    """
    brief_lines = [
        "## Project brief",
        "",
        f"- search_topic: {project_brief.get('search_topic') or '（未填）'}",
        f"- one_sentence: {project_brief.get('one_sentence') or '（未填）'}",
        f"- hook_text: {project_brief.get('hook_text') or '（未填）'}",
        "",
    ]
    if anchor_titles:
        brief_lines.append("## Anchor titles (winners from last round — mirror their patterns)")
        brief_lines.append("")
        for t in anchor_titles:
            brief_lines.append(f"- {t}")
        brief_lines.append("")
    title_archetype_ids = [a.id for a in load_playbook_index().title_archetypes]
    brief_lines.append("## Task: generate the divergent pool")
    brief_lines.append(
        f"Produce 2-3 fresh titles per archetype, covering ALL {len(title_archetype_ids)} "
        f"title archetypes ({', '.join(title_archetype_ids)}). Use the exact grouped "
        "schema (header line + bullet list) specified in the system prompt. No preamble, "
        "no closing remarks."
    )
    brief_text = "\n".join(brief_lines)

    system_prompt = format_title_pool_system_prompt()
    messages = [{"role": "user", "content": brief_text}]
    model = get_model(agent="bridge", task="thumbnail_brainstorm")

    response_text = await asyncio.to_thread(
        ask_claude_multi,
        messages,
        system=system_prompt,
        model=model,
        max_tokens=3072,
    )
    return _parse_title_pool(response_text)


def _build_pool_entries(parsed: list[tuple[str, str]], iteration: int) -> list[dict]:
    """Convert parsed (archetype, title) pairs into pool entries with stable IDs.

    IDs are sequential within an iteration (``t-i{iter}-{seq:03d}``), reset on
    each fresh pool. Caps per archetype at 3 titles to keep the grid scannable.
    """
    per_arch_count: dict[str, int] = {}
    entries: list[dict] = []
    seq = 0
    for arch_id, title in parsed:
        if per_arch_count.get(arch_id, 0) >= 3:
            continue
        seq += 1
        entries.append(
            {
                "id": f"t-i{iteration}-{seq:03d}",
                "archetype": arch_id,
                "title": title,
            }
        )
        per_arch_count[arch_id] = per_arch_count.get(arch_id, 0) + 1
    return entries


def _render_pool_grid_response(
    request: Request, slug: str, entry: object, pool_state: dict
) -> "Response":
    """Render the full title-pool grid + final slots fragment.

    Used as the HTMX swap target after brainstorm / iterate. Pulls archetype
    metadata so each section header has the right name / emotion / grade.
    """
    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    final_slots = [str(t) for t in (raw_fm.get("title_candidates") or [])]
    while len(final_slots) < 3:
        final_slots.append("")

    return _templates.TemplateResponse(
        request,
        "projects/_title_pool_grid.html",
        {
            "slug": slug,
            "iteration": pool_state.get("iteration", 0),
            "archetype_groups": _group_pool_by_archetype(pool_state.get("pool", [])),
            "checked_ids": set(pool_state.get("checked_ids", [])),
            "final_slots": final_slots,
        },
    )


@page_router.post("/projects/{slug}/thumbnail/title-pool/brainstorm")
async def thumbnail_title_pool_brainstorm(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """First-round brainstorm: generate ~25 titles (2-3 per archetype × 10).

    Replaces any existing pool. Resets checked_ids. Increments iteration.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    project_brief = {
        "search_topic": str(raw_fm.get("search_topic") or ""),
        "one_sentence": str(raw_fm.get("one_sentence") or ""),
        "hook_text": str(raw_fm.get("hook_text") or ""),
    }

    pool_state = _load_title_pool(slug)
    new_iter = (pool_state.get("iteration", 0) or 0) + 1

    try:
        parsed = await _render_pool_brainstorm_call(project_brief=project_brief, anchor_titles=[])
    except Exception as exc:  # noqa: BLE001
        logger.exception("title pool brainstorm LLM failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=f"Title pool brainstorm 失敗：{exc}") from exc

    entries = _build_pool_entries(parsed, new_iter)
    if not entries:
        raise HTTPException(status_code=502, detail="LLM 沒輸出可解析的標題池，請重試。")

    pool_state["iteration"] = new_iter
    pool_state["pool"] = entries
    pool_state["checked_ids"] = []
    _save_title_pool(slug, pool_state)

    try:
        record_api_call(
            agent="bridge",
            model=get_model(agent="bridge", task="thumbnail_brainstorm"),
            input_tokens=0,
            output_tokens=0,
            scope_json=json.dumps(
                {
                    "scope": "title_pool_brainstorm",
                    "project": slug,
                    "iteration": new_iter,
                    "n_titles": len(entries),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit record failed (non-fatal)")

    return _render_pool_grid_response(request, slug, entry, pool_state)


@page_router.post("/projects/{slug}/thumbnail/title-pool/iterate")
async def thumbnail_title_pool_iterate(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Iterate round: keep checked titles + replace unchecked with fresh variants.

    Algorithm (2026-05-27 redo per 修修):
      1. Identify kept titles = pool entries whose IDs are in checked_ids.
      2. Per archetype, count how many kept titles are there.
      3. LLM produces a fresh pool using checked titles as anchors (so new
         titles mirror the user-validated patterns).
      4. Merge: for each archetype, keep all kept titles + fill remaining slots
         (target 3 per archetype) with new titles from LLM batch.
      5. checked_ids is preserved verbatim — kept titles' IDs remain valid in
         the merged pool, so their checked state persists across iterate.

    No anchors → 400 (user is asking to iterate without giving direction).
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    project_brief = {
        "search_topic": str(raw_fm.get("search_topic") or ""),
        "one_sentence": str(raw_fm.get("one_sentence") or ""),
        "hook_text": str(raw_fm.get("hook_text") or ""),
    }

    pool_state = _load_title_pool(slug)
    old_pool = pool_state.get("pool", [])
    checked_set = set(pool_state.get("checked_ids", []))

    kept_titles = [t for t in old_pool if isinstance(t, dict) and t.get("id") in checked_set]
    if not kept_titles:
        raise HTTPException(
            status_code=400,
            detail=(
                "還沒勾選任何標題作為 anchor — 先勾幾個喜歡的方向再 iterate"
                "（或直接重新 brainstorm 換一池）。"
            ),
        )

    anchor_titles = [t["title"] for t in kept_titles]
    new_iter = (pool_state.get("iteration", 0) or 0) + 1

    # Bucket kept titles by archetype to know how many slots LLM needs to fill.
    canonical_arch = [f"T-A{i}" for i in range(1, 11)]
    kept_by_arch: dict[str, list[dict]] = {a: [] for a in canonical_arch}
    for t in kept_titles:
        aid = t.get("archetype", "")
        if aid in kept_by_arch:
            kept_by_arch[aid].append(t)

    try:
        parsed = await _render_pool_brainstorm_call(
            project_brief=project_brief, anchor_titles=anchor_titles
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("title pool iterate LLM failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=f"Title pool iterate 失敗：{exc}") from exc

    new_entries = _build_pool_entries(parsed, new_iter)
    if not new_entries and not kept_titles:
        raise HTTPException(status_code=502, detail="LLM 沒輸出可解析的標題池，請重試。")

    # Bucket new entries by archetype too — we only pull from each archetype's
    # bucket up to (3 - kept_in_arch) so kept titles always own their slot.
    new_by_arch: dict[str, list[dict]] = {a: [] for a in canonical_arch}
    for e in new_entries:
        aid = e.get("archetype", "")
        if aid in new_by_arch:
            new_by_arch[aid].append(e)

    target_per_arch = 3
    merged_pool: list[dict] = []
    for arch_id in canonical_arch:
        kept_in_arch = kept_by_arch.get(arch_id, [])
        merged_pool.extend(kept_in_arch)
        slots_left = max(0, target_per_arch - len(kept_in_arch))
        merged_pool.extend(new_by_arch.get(arch_id, [])[:slots_left])

    pool_state["iteration"] = new_iter
    pool_state["pool"] = merged_pool
    # Preserve checked_ids — kept titles' IDs survive merge, so their checked
    # state stays. New titles start unchecked (their IDs aren't in the set).
    _save_title_pool(slug, pool_state)

    try:
        record_api_call(
            agent="bridge",
            model=get_model(agent="bridge", task="thumbnail_brainstorm"),
            input_tokens=0,
            output_tokens=0,
            scope_json=json.dumps(
                {
                    "scope": "title_pool_iterate",
                    "project": slug,
                    "iteration": new_iter,
                    "n_kept": len(kept_titles),
                    "n_new": len(new_entries),
                    "n_merged": len(merged_pool),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit record failed (non-fatal)")

    return _render_pool_grid_response(request, slug, entry, pool_state)


@page_router.post("/projects/{slug}/thumbnail/title-pool/check/{title_id}")
async def thumbnail_title_pool_check(
    request: Request,
    slug: str,
    title_id: str,
    nakama_auth: str | None = Cookie(None),
    state: bool = Form(False),
):
    """Toggle a title's checked state in the sidecar.

    Form field ``state=true`` when checkbox is checked; absent ⇒ False.
    Returns 204 — UI updates optimistically (browser default behaviour).
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    pool_state = _load_title_pool(slug)
    checked = pool_state.get("checked_ids", [])
    if state:
        if title_id not in checked:
            checked.append(title_id)
    else:
        if title_id in checked:
            checked.remove(title_id)
    pool_state["checked_ids"] = checked
    _save_title_pool(slug, pool_state)
    return Response(status_code=204)


@page_router.post("/projects/{slug}/thumbnail/title-pool/promote/{title_id}")
async def thumbnail_title_pool_promote(
    request: Request,
    slug: str,
    title_id: str,
    slot: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    """Promote a pool title into the final A/B/C slot.

    Form field ``slot ∈ {"A", "B", "C"}``. Writes the title into
    ``title_candidates[slot_idx]`` in frontmatter. Returns the updated
    final-slot fragment for HTMX swap.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    if slot not in ("A", "B", "C"):
        raise HTTPException(status_code=400, detail=f"slot must be A|B|C; got {slot!r}")

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    pool_state = _load_title_pool(slug)
    title_obj = next(
        (t for t in pool_state.get("pool", []) if isinstance(t, dict) and t.get("id") == title_id),
        None,
    )
    if title_obj is None:
        raise HTTPException(status_code=404, detail=f"title id {title_id} not in pool")

    slot_idx = {"A": 0, "B": 1, "C": 2}[slot]
    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    final_slots = [str(t) for t in (raw_fm.get("title_candidates") or [])]
    while len(final_slots) < 3:
        final_slots.append("")
    final_slots[slot_idx] = title_obj.get("title", "")

    try:
        update_frontmatter(
            vault_root=get_vault_path(),
            slug=slug,
            patch={"title_candidates": final_slots},
        )
    except ProjectWriteError as exc:
        logger.exception("promote → final write-back failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _templates.TemplateResponse(
        request,
        "projects/_title_final_slots.html",
        {
            "slug": slug,
            "final_slots": final_slots,
        },
    )


@page_router.post("/projects/{slug}/thumbnail/title-pool/save-final")
async def thumbnail_title_pool_save_final(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
    slot_a: str = Form(""),
    slot_b: str = Form(""),
    slot_c: str = Form(""),
):
    """Save the 3 final-slot textareas as ``title_candidates`` frontmatter.

    Empty slots are dropped (final list can have 1-3 items). Returns the
    updated final-slots fragment for HTMX swap.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    final_slots = [slot_a.strip(), slot_b.strip(), slot_c.strip()]
    # Persist as a list, dropping empties — title_candidates has been list[str]
    # in this schema since ADR-031.
    title_candidates = [t for t in final_slots if t]

    try:
        update_frontmatter(
            vault_root=get_vault_path(),
            slug=slug,
            patch={"title_candidates": title_candidates},
        )
    except ProjectWriteError as exc:
        logger.exception("save-final write-back failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Re-render with persisted state (padded back to 3 for the UI)
    while len(final_slots) < 3:
        final_slots.append("")

    return _templates.TemplateResponse(
        request,
        "projects/_title_final_slots.html",
        {
            "slug": slug,
            "final_slots": final_slots,
            "just_saved": True,
        },
    )


__all__ = [
    "page_router",
    "ParsedIdea",
    "load_thumbnail_asset_manifest_for_ui",
]
