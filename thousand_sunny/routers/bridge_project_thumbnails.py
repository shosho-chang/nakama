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
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agents.brook.script_video.render_workers.thumbnail_worker import (
    DEFAULT_VIDEO_DIR as _HYPERFRAMES_VIDEO_DIR,
)
from agents.brook.script_video.render_workers.thumbnail_worker import (
    ThumbnailRenderError,
    render_podcast_still,
    render_youtube_still,
)
from shared import thumbnail_funnel
from shared.anthropic_client import ask_claude_multi
from shared.config import get_vault_path
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
from shared.thumbnail_funnel import FunnelError
from shared.thumbnail_idea import (
    IdeaParseError,
    ParsedIdea,
    parse_idea,
    parse_ideas_batch,
)
from shared.thumbnail_playbook import (
    format_playbook_index_for_prompt,
    load_playbook_index,
)
from thousand_sunny.auth import check_auth

logger = logging.getLogger("nakama.web.bridge_project_thumbnails")

page_router = APIRouter(prefix="/bridge", tags=["bridge-project-thumbnails"])

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_DIR = _REPO_ROOT / "thousand_sunny" / "templates" / "bridge"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

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
_BRAINSTORM_PROMPT_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "brainstorm_youtube_v1.md"
_PODCAST_BRAINSTORM_PROMPT_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "brainstorm_podcast_v1.md"
_TITLES_PROMPT_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "brainstorm_titles_v1.md"


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


def _brainstorm_user_message(
    *,
    title_candidates: list[str],
    one_sentence: str,
    search_topic: str,
    reference_images: list[Path] | None = None,
    keep_idea_indices: list[int] | None = None,
    kept_ideas_raw: list[str] | None = None,
) -> list[dict]:
    """Build the multi-part user content for the brainstorm LLM call.

    v1.1 playbook-integrated: distilled archetype catalog (text) replaces
    vision few-shot reference-image attachment (per ADR-033 D4 redesign).
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

    # Playbook archetype index injection (~1.5K tokens, distilled from 140-row corpus)
    try:
        playbook_text = format_playbook_index_for_prompt(load_playbook_index())
    except Exception as exc:  # noqa: BLE001 — fail soft if playbook files missing
        logger.warning("playbook index unavailable, falling back to no archetype tags: %s", exc)
        playbook_text = ""

    brief_text = (
        f"## Project brief\n\n"
        f"- search_topic: {search_topic or '（未填）'}\n"
        f"- one_sentence: {one_sentence or '（未填）'}\n"
        f"- title candidates:\n"
    )
    for t in title_candidates or ["（未填）"]:
        brief_text += f"  - {t}\n"

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
        "\nProduce 3 distinct thumbnail idea blocks per the 6-line format "
        "(archetype tag line + 5 content lines). "
        "Diversity requirement: at least 2 differing axes "
        "(title archetype, hook, emotion, bg, decoration)."
    )

    if playbook_text:
        parts.append({"type": "text", "text": playbook_text})
    parts.append({"type": "text", "text": brief_text})
    return parts


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

    # v1.1: vision few-shot images replaced by distilled playbook text injection
    # (ADR-033 D4 redesign). Reference image lookup retained for backward compat
    # but not invoked by the main brainstorm path. See shared/thumbnail_playbook.py.
    user_parts = _brainstorm_user_message(
        title_candidates=list(entry.title_candidates),
        one_sentence=str(raw_fm.get("one_sentence") or ""),
        search_topic=str(raw_fm.get("search_topic") or ""),
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
                    "archetype_tags": [list(i.archetype_tags) for i in ideas],
                },
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001 — audit must not block the success path
        logger.exception("audit record failed (non-fatal)")

    return _templates.TemplateResponse(
        request,
        "projects/_thumbnail_idea_cards.html",
        {
            "slug": slug,
            "ideas": [
                {"index": i, "raw": raw_blocks[i], "parsed": ideas[i].__dict__}
                for i in range(len(ideas))
            ],
        },
    )


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
                {"schema_version": "v1", "slug": slug, "runs": runs},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 — never block success path
        logger.exception("brainstorm meta persistence failed (non-fatal, slug=%s)", slug)


def prepare_existing_ideas_for_template(raw_frontmatter: dict | None) -> list[dict]:
    """Pre-parse ``thumbnail_ideas`` from frontmatter into the shape the partial expects.

    Called by ``bridge_projects.projects_detail`` on initial page load so the
    template can render the same editable-card partial used by HTMX swaps.
    Each entry: ``{"index": int, "raw": str, "parsed": ParsedIdea.__dict__ | None,
    "parse_error": str | None}``.
    """
    fm = raw_frontmatter if isinstance(raw_frontmatter, dict) else {}
    raw_list = fm.get("thumbnail_ideas") or []
    out: list[dict] = []
    for i, raw in enumerate(raw_list):
        raw_str = str(raw)
        try:
            parsed = parse_idea(raw_str)
            out.append(
                {
                    "index": i,
                    "raw": raw_str,
                    "parsed": parsed.__dict__,
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


def _render_single_idea_card(request: Request, slug: str, idx: int, raw: str) -> str:
    """Render the single-idea-card partial (used by save-edit + render swap targets).

    Parses ``raw`` and surfaces the structured preview alongside the editable
    textarea. If parsing fails, surfaces the parse error inline so 修修 can
    fix the textarea without leaving the card.
    """
    parsed_dict: dict | None = None
    parse_error: str | None = None
    try:
        parsed = parse_idea(raw)
        parsed_dict = parsed.__dict__
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

    return _render_single_idea_card(request, slug, idx, new_value)


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

    user_parts = _brainstorm_user_message(
        title_candidates=list(entry.title_candidates),
        one_sentence=str(raw_fm.get("one_sentence") or ""),
        search_topic=str(raw_fm.get("search_topic") or ""),
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
    for i, raw in enumerate(ideas_raw):
        try:
            p = parse_idea(raw)
            parsed_all.append({"index": i, "raw": raw, "parsed": p.__dict__, "parse_error": None})
        except (IdeaParseError, EmotionLookupError) as exc:
            parsed_all.append({"index": i, "raw": raw, "parsed": None, "parse_error": str(exc)})

    return _templates.TemplateResponse(
        request,
        "projects/_thumbnail_idea_cards.html",
        {"slug": slug, "ideas": parsed_all},
    )


@page_router.post("/projects/{slug}/thumbnail/render")
async def thumbnail_render(
    request: Request,
    slug: str,
    idea_index: int = Form(...),
    director_notes: str = Form(""),
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

    vault = get_vault_path()

    ts = _run_ts()
    run_dir = _thumbnails_dir() / slug / "runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    out_png = run_dir / f"v{idea_index}.png"

    # PR4 bg strategy: solid/gradient via composition CSS — no bg image until
    # PR5 Unsplash integration. The composition handles bg_data_url="" by hiding
    # the <img> and falling back to the palette gradient.
    bg_path = None  # PR5: replace with Unsplash query / AI gen driven by parsed.bg

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
                accent_decoration=parsed.decoration,
                palette={"bg_darken": 0.55},
            )
        else:
            try:
                cutout_path = pick_youtube_host(parsed.emotion_key, vault)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=412, detail=str(exc)) from exc
            await render_youtube_still(
                title_hook=parsed.hook,
                cutout_path=cutout_path,
                bg_path=bg_path,
                out_png=out_png,
                accent_decoration=parsed.decoration,
                palette={"bg_darken": 0.0},
            )
    except ThumbnailRenderError as exc:
        logger.exception("thumbnail render failed: slug=%s idea=%d", slug, idea_index)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Per-run manifest (audit traceability — see ADR-033 D7 §thumbnail_run).
    manifest_path = run_dir / "manifest.json"
    manifest = _load_manifest(manifest_path)
    manifest.setdefault("renders", []).append(
        {
            "idea_index": idea_index,
            "rendered_at": _now_iso(),
            "filename": out_png.name,
            "parsed_idea": parsed.__dict__,
            "director_notes": director_notes,
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
            "parsed": parsed.__dict__,
        },
    )


def _load_manifest(path: Path) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("manifest corrupt — starting fresh: %s", path)
    return {}


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
):
    """Brainstorm 3 A/B title candidates via Sonnet 4.6 (text only).

    Reads frontmatter brief context (search_topic, one_sentence, hook_text),
    calls the LLM, splits the response by newlines, and writes the first 3
    non-empty lines to ``title_candidates`` (replacing existing). Returns an
    HTMX partial with the populated textarea.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    brief_text = (
        f"## Project brief\n\n"
        f"- search_topic: {str(raw_fm.get('search_topic') or '（未填）')}\n"
        f"- one_sentence: {str(raw_fm.get('one_sentence') or '（未填）')}\n"
        f"- hook_text: {str(raw_fm.get('hook_text') or '（未填）')}\n\n"
        "Produce exactly 3 title candidates, one per line, no preamble."
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
            max_tokens=512,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("title brainstorm LLM call failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=f"Title brainstorm 失敗：{exc}") from exc

    titles = _extract_title_lines(response_text)
    if not titles:
        raise HTTPException(
            status_code=502,
            detail="LLM 沒有輸出任何標題候選，請重試。",
        )

    try:
        update_frontmatter(
            vault_root=get_vault_path(),
            slug=slug,
            patch={"title_candidates": titles},
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
                    "n_titles": len(titles),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit record failed (non-fatal)")

    return _templates.TemplateResponse(
        request,
        "projects/_thumbnail_title_candidates_textarea.html",
        {"title_candidates": titles},
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


__all__ = [
    "page_router",
    "ParsedIdea",
]
