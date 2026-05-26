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

from agents.foundry.render_workers.thumbnail_worker import (
    ThumbnailRenderError,
    render_youtube_still,
)
from shared.anthropic_client import ask_claude_multi
from shared.config import get_vault_path
from shared.cutout_library import EmotionLookupError, pick_youtube_host
from shared.llm_router import get_model
from shared.project_indexer import ProjectIndexer, ProjectNotFoundError, normalize_slug
from shared.project_writer import ProjectWriteError, update_frontmatter
from shared.state import record_api_call
from shared.thumbnail_idea import (
    IdeaParseError,
    ParsedIdea,
    parse_idea,
    parse_ideas_batch,
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
_TITLES_PROMPT_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "brainstorm_titles_v1.md"


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


def _load_brainstorm_prompt() -> str:
    return _BRAINSTORM_PROMPT_PATH.read_text(encoding="utf-8")


def _load_titles_prompt() -> str:
    return _TITLES_PROMPT_PATH.read_text(encoding="utf-8")


def _brainstorm_user_message(
    *,
    title_candidates: list[str],
    one_sentence: str,
    search_topic: str,
    reference_images: list[Path],
) -> list[dict]:
    """Build the multi-part user content for the brainstorm LLM call."""
    parts: list[dict] = []
    for img in reference_images:
        parts.append(_ref_image_to_block(img))

    brief_text = (
        f"## Project brief\n\n"
        f"- search_topic: {search_topic or '（未填）'}\n"
        f"- one_sentence: {one_sentence or '（未填）'}\n"
        f"- title candidates:\n"
    )
    for t in title_candidates or ["（未填）"]:
        brief_text += f"  - {t}\n"
    brief_text += (
        "\nProduce 3 distinct thumbnail idea blocks per the 5-line format. "
        "Diversity requirement: at least 2 differing axes across the 3 ideas."
    )
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

    if entry.content_type != "youtube":
        raise HTTPException(
            status_code=400,
            detail=(
                f"thumbnail brainstorm currently supports content_type=youtube; "
                f"got {entry.content_type!r}. Podcast support lands in PR4-B."
            ),
        )

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    references = _reference_images_for("youtube")

    user_parts = _brainstorm_user_message(
        title_candidates=list(entry.title_candidates),
        one_sentence=str(raw_fm.get("one_sentence") or ""),
        search_topic=str(raw_fm.get("search_topic") or ""),
        reference_images=references,
    )

    system_prompt = _load_brainstorm_prompt()
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
                    "n_ideas": len(ideas),
                    "n_references": len(references),
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
    try:
        cutout_path = pick_youtube_host(parsed.emotion_key, vault)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc

    # PR4-A bg strategy: solid/gradient via composition CSS — no bg image until
    # PR5 Unsplash integration. The composition handles bg_data_url="" by hiding
    # the <img> and falling back to the palette gradient.
    bg_path = None  # PR5: replace with Unsplash query / AI gen driven by parsed.bg

    ts = _run_ts()
    run_dir = _thumbnails_dir() / slug / "runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    out_png = run_dir / f"v{idea_index}.png"

    try:
        await render_youtube_still(
            title_hook=parsed.hook,
            cutout_path=cutout_path,
            bg_path=bg_path,
            out_png=out_png,
            accent_decoration=parsed.decoration,
            palette={"bg_darken": 0.0},  # no overlay needed when bg is solid gradient
        )
    except (FileNotFoundError, ThumbnailRenderError) as exc:
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


__all__ = [
    "page_router",
    "ParsedIdea",
]
