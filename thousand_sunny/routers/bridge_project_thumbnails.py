"""Bridge sibling router for the thumbnail pipeline (ADR-033 PR4-A).

Lives separately from ``bridge_projects.py`` per panel finding P4
(``bridge_projects.py`` is already 1952 LOC — adding ~300 more would push
maintenance pain past the threshold). All endpoints under
``/bridge/projects/{slug}/thumbnail/*``.

Endpoints:

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
from shared.config import get_vault_path
from shared.cutout_library import (
    EmotionLookupError,
    pick_podcast_guest,
    pick_podcast_host,
    pick_youtube_host,
)
from shared.project_indexer import ProjectIndexer, ProjectNotFoundError, normalize_slug
from shared.project_writer import ProjectWriteError, update_frontmatter
from shared.state import record_api_call
from shared.thumbnail_funnel import FunnelError
from shared.thumbnail_idea import (
    IdeaParseError,
    parse_idea,
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


def _safe_ep_slug(slug: str) -> str | None:
    """Validate an episode slug used in packaging paths (ASCII only, no traversal)."""
    import re as _re

    if not _re.fullmatch(r"[A-Za-z0-9._-]+", slug):
        return None
    return slug


@page_router.get("/projects/{slug}/thumbnail/packaging/{episode_slug}/{filename}")
async def thumbnail_packaging_candidate(
    slug: str,
    episode_slug: str,
    filename: str,
    nakama_auth: str | None = Cookie(None),
):
    """Serve a packaging PNG from vault ``Attachments/packaging/{episode_slug}/``.

    Used by S7 gate to display thumbnail candidates generated by the packaging skill.
    Paths are vault-relative and resolved via ``get_vault_path()``.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)
    safe_ep = _safe_ep_slug(episode_slug)
    if safe_ep is None:
        raise HTTPException(status_code=400, detail="invalid episode_slug")
    safe = _safe_filename(filename)
    if safe is None:
        raise HTTPException(status_code=400, detail="invalid filename")
    path = get_vault_path() / "Attachments" / "packaging" / safe_ep / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"packaging candidate missing: {filename}")
    return FileResponse(path, media_type="image/png")


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


# Podcast funnel endpoints (ADR-033 D8 — host/guest cutout extraction)


def _funnel_dir(slug: str, role: str, ts: str) -> Path:
    """Where ffmpeg-extracted frame candidates land before u2net."""
    return _thumbnails_dir() / slug / "funnel" / role / ts


def _resolve_video_path(raw_value: str) -> Path:
    """Frontmatter ``host_video_path`` / ``guest_video_path`` → absolute Path.

    Relative paths resolve against the repo root (so frontmatter can be portable
    across machines if 修修 commits the video into ``data/podcasts/``).

    Defense-in-depth (ADR-033, extended by ADR-054 S6): the resolved path must
    fall within one of the allowed roots:
      1. repo root — always allowed (relative paths, data/podcasts/... etc.)
      2. FOOTAGE_ROOT env var — allowed when set (e.g. G:\\footages on Windows)
         so real footage directories outside the repo can be referenced without
         granting access to arbitrary host paths.
    """
    p = Path(raw_value)
    if not p.is_absolute():
        p = _REPO_ROOT / p
    resolved = p.resolve()

    allowed_roots: list[Path] = [_REPO_ROOT.resolve()]
    footage_root_env = os.environ.get("FOOTAGE_ROOT")
    if footage_root_env:
        allowed_roots.append(Path(footage_root_env).resolve())

    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue

    raise ValueError(
        f"video path outside allowed roots: {raw_value!r} → {resolved} "
        f"(set FOOTAGE_ROOT env to allow footage directories outside the repo)"
    )


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
]
