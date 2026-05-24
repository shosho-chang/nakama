"""Bridge UI panel for repurpose runs — /bridge/repurpose/*.

Slice 10 — adds per-channel edit / approve mutation on top of the Slice 2
read-only skeleton.  Blog → Usopp WP-draft enqueue is stubbed (501) until the
``repurpose blog.md → DraftV1`` adapter (focus keyword / categories / tags
extraction) is implemented in a follow-up — see issue #293 carry-over notes.

Routes:
    GET  /bridge/repurpose                          — list all runs
    GET  /bridge/repurpose/<run_id>                 — 3-panel detail view
    POST /bridge/repurpose/<run_id>/blog            — save blog markdown
    POST /bridge/repurpose/<run_id>/fb/<tonal>      — save fb tonal markdown
    POST /bridge/repurpose/<run_id>/ig              — save ig markdown
    POST /bridge/repurpose/<run_id>/approve/<channel>
        — channel in {blog, fb.light, fb.emotional, fb.serious, fb.neutral, ig}
    POST /bridge/repurpose/<run_id>/publish/blog    — enqueue Usopp WP draft
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Body, Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agents.brook.repurpose_engine import (
    BLOG_FILENAME,
    DATA_ROOT,
    FB_TONALS,
    IG_FILENAME,
    STAGE1_FILENAME,
    fb_filename,
)
from shared.log import get_logger
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.repurpose")

page_router = APIRouter(prefix="/bridge/repurpose")
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)


def _shosho_asset_version() -> str:
    """Return an 8-char hash of the Bridge Shosho design-system CSS files.

    Used to bust Cloudflare's /static/* edge cache when the design-system
    stylesheets change. Mirrors ``_shosho_asset_version()`` in ``bridge.py``.
    """
    import hashlib

    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for css in ("tokens.css", "bridge.css", "bridge-pages.css", "theme.js"):
        path = static_dir / css
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()

# run_id format: YYYY-MM-DD-<slug> where slug is engine-sanitized to [A-Za-z0-9_-]{1,60}.
# Strict regex prevents path traversal (e.g. "..%2F..%2Fetc" would not match).
_RUN_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[A-Za-z0-9_-]{1,60}$")

# Channels that participate in per-channel approve.  Channel name == sentinel
# suffix: ``.approved.<channel>`` lives in the run dir.
_CHANNELS: tuple[str, ...] = ("blog", *(f"fb.{t}" for t in FB_TONALS), "ig")
_CHANNEL_SET = frozenset(_CHANNELS)


def _channel_artifact_path(run_dir: Path, channel: str) -> Path:
    """Map a channel name to the artifact file the channel approves."""
    if channel == "blog":
        return run_dir / BLOG_FILENAME
    if channel == "ig":
        return run_dir / IG_FILENAME
    if channel.startswith("fb."):
        return run_dir / fb_filename(channel.removeprefix("fb."))
    raise ValueError(f"unknown channel {channel!r}")


# ---------------------------------------------------------------------------
# Per-channel status sentinels
# ---------------------------------------------------------------------------


def _sentinel_path(run_dir: Path, kind: str, channel: str) -> Path:
    """Return the sentinel file path for ``.<kind>.<channel>`` (e.g. .approved.blog).

    Caller MUST have already validated ``channel`` against ``_CHANNEL_SET``;
    we re-check here as a defence-in-depth assert because ``channel`` ends up
    in a filesystem path.
    """
    if channel not in _CHANNEL_SET:
        raise ValueError(f"unknown channel {channel!r}")
    if kind not in ("approved", "published"):
        raise ValueError(f"unknown sentinel kind {kind!r}")
    return run_dir / f".{kind}.{channel}"


def _channel_approved(run_dir: Path, channel: str) -> bool:
    return _sentinel_path(run_dir, "approved", channel).exists()


def _run_status(run_dir: Path) -> str:
    """Derive list-view status from sentinel files.

    Returns one of: ``pending`` / ``partially-approved`` / ``approved`` /
    ``published``.  ``published`` is set as soon as the blog has been
    successfully handed to Usopp (sentinel ``.published.blog``).
    """
    if _sentinel_path(run_dir, "published", "blog").exists():
        return "published"
    approved = sum(1 for ch in _CHANNELS if _channel_approved(run_dir, ch))
    if approved == 0:
        return "pending"
    if approved == len(_CHANNELS):
        return "approved"
    return "partially-approved"


def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically: write to .tmp then os.replace().

    Prevents partial writes from corrupting a run artifact mid-save.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _approved_map(run_dir: Path) -> dict[str, bool]:
    """Return ``{channel: approved_bool}`` for all six channels."""
    return {ch: _channel_approved(run_dir, ch) for ch in _CHANNELS}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _list_runs() -> list[dict]:
    """Scan data/repurpose/ and return run summaries sorted newest-first.

    Sort relies on YYYY-MM-DD prefix → reverse lex order == chronological newest-first.
    """
    if not DATA_ROOT.exists():
        return []
    runs = []
    for d in sorted(DATA_ROOT.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        stage1_path = d / STAGE1_FILENAME
        episode_type = ""
        if stage1_path.exists():
            try:
                data = json.loads(stage1_path.read_text(encoding="utf-8"))
                value = data.get("episode_type", "")
                episode_type = value if isinstance(value, str) else ""
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(f"failed to parse {stage1_path}: {exc}")
        artifact_count = sum(
            1
            for f in d.iterdir()
            if f.is_file() and f.suffix in (".md", ".json") and f.name != STAGE1_FILENAME
        )
        runs.append(
            {
                "run_id": d.name,
                "episode_type": episode_type,
                "artifact_count": artifact_count,
                "status": _run_status(d),
            }
        )
    return runs


def _load_run(run_id: str) -> dict:
    """Load all artifacts for a single run directory.

    Raises:
        FileNotFoundError: If the run directory does not exist.

    Note: ``run_id`` is assumed pre-validated against ``_RUN_ID_RE`` by the caller.
    """
    run_dir = DATA_ROOT / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(run_id)

    def _read(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8") if path.exists() else None
        except OSError as exc:
            logger.warning(f"failed to read {path}: {exc}")
            return None

    stage1_raw = _read(run_dir / STAGE1_FILENAME)
    stage1_data: dict = {}
    if stage1_raw:
        try:
            stage1_data = json.loads(stage1_raw)
        except json.JSONDecodeError as exc:
            logger.warning(f"failed to parse stage1 JSON for {run_id}: {exc}")

    fb_variants = {t: _read(run_dir / fb_filename(t)) for t in FB_TONALS}

    return {
        "run_id": run_id,
        "stage1": stage1_data,
        "blog": _read(run_dir / BLOG_FILENAME),
        "fb": fb_variants,
        "ig": _read(run_dir / IG_FILENAME),
        "approved": _approved_map(run_dir),
        "published": _sentinel_path(run_dir, "published", "blog").exists(),
        "status": _run_status(run_dir),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@page_router.get("", response_class=HTMLResponse)
async def repurpose_list(
    request: Request,
    nakama_auth: str | None = Cookie(None),
):
    """List all repurpose runs."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/repurpose", status_code=302)
    runs = _list_runs()
    return _templates.TemplateResponse(
        request,
        "repurpose_list.html",
        {"runs": runs, "asset_version": _SHOSHO_ASSET_VERSION},
    )


@page_router.get("/{run_id}", response_class=HTMLResponse)
async def repurpose_detail(
    request: Request,
    run_id: str,
    nakama_auth: str | None = Cookie(None),
):
    """3-panel detail view for a single repurpose run.

    Validates ``run_id`` against ``_RUN_ID_RE`` BEFORE auth redirect to prevent
    path-traversal smuggling via login `next` parameter.
    """
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=404, detail="invalid run_id")
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/repurpose/{run_id}", status_code=302)
    try:
        run = _load_run(run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found") from None
    return _templates.TemplateResponse(
        request,
        "repurpose_detail.html",
        {"run": run, "fb_tonals": FB_TONALS, "asset_version": _SHOSHO_ASSET_VERSION},
    )


# ---------------------------------------------------------------------------
# Mutation routes (Slice 10)
# ---------------------------------------------------------------------------
#
# Auth model:
#   Mutation endpoints return 401 (not 302 redirect) on missing/invalid auth
#   so the JSON fetch() from the client surfaces it as a proper error instead
#   of following a redirect to /login HTML.  GET pages still redirect.
#
# Body shape:
#   Save endpoints accept ``{"content": "<markdown>"}`` JSON.  Approve and
#   publish endpoints accept an empty JSON body ``{}`` — they carry no
#   user-supplied content, only the run_id + channel from the URL path.
#
# CSRF:
#   The whole Bridge UI is single-user (修修) behind cookie auth and same-origin.
#   No CSRF token layer exists elsewhere in Bridge (cf. PR #140 bridge.py
#   mutation routes); we follow that convention.  When multi-user lands the
#   token layer goes in shared/auth, not here.


def _validate_run_id(run_id: str) -> Path:
    """Return the run dir, raising 404 on invalid or missing run_id."""
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=404, detail="invalid run_id")
    run_dir = DATA_ROOT / run_id
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return run_dir


def _require_mutation_auth(nakama_auth: str | None) -> None:
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")


def _extract_content(body: dict) -> str:
    """Pull ``content`` field from a JSON save body, validating type + length.

    Cap at 200 KB — Brook artifacts are markdown / small JSON, anything larger
    is almost certainly an accidental paste or attack.
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    content = body.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content must be a string")
    if len(content.encode("utf-8")) > 200_000:
        raise HTTPException(status_code=413, detail="content exceeds 200 KB cap")
    return content


def _save_artifact(
    run_id: str,
    nakama_auth: str | None,
    body: dict,
    filename_fn: Callable[[Path], Path],
    log_label: str,
) -> dict:
    _require_mutation_auth(nakama_auth)
    run_dir = _validate_run_id(run_id)
    content = _extract_content(body)
    target = filename_fn(run_dir)
    _atomic_write_text(target, content)
    logger.info(f"saved {log_label} for run_id={run_id} bytes={len(content.encode())}")
    return {"ok": True, "bytes": len(content.encode("utf-8"))}


@page_router.post("/{run_id}/blog")
async def save_blog(
    run_id: str,
    body: dict = Body(default_factory=dict),
    nakama_auth: str | None = Cookie(None),
):
    """Overwrite ``blog.md`` for this run."""
    return _save_artifact(run_id, nakama_auth, body, lambda d: d / BLOG_FILENAME, "blog.md")


@page_router.post("/{run_id}/fb/{tonal}")
async def save_fb(
    run_id: str,
    tonal: str,
    body: dict = Body(default_factory=dict),
    nakama_auth: str | None = Cookie(None),
):
    """Overwrite ``fb-<tonal>.md`` for this run."""
    if tonal not in FB_TONALS:
        raise HTTPException(status_code=404, detail=f"unknown tonal {tonal!r}")
    return _save_artifact(
        run_id,
        nakama_auth,
        body,
        lambda d, t=tonal: d / fb_filename(t),
        f"fb-{tonal}.md",
    )


@page_router.post("/{run_id}/ig")
async def save_ig(
    run_id: str,
    body: dict = Body(default_factory=dict),
    nakama_auth: str | None = Cookie(None),
):
    """Overwrite ``ig-cards.json`` for this run.

    Validation note: the IG artifact filename has a ``.json`` suffix but is
    accepted as opaque text here.  Brook's IG renderer owns the schema; the
    review surface trusts 修修's edits and only enforces UTF-8 + size cap.
    Downstream consumers re-parse and surface errors at publish time.
    """
    return _save_artifact(run_id, nakama_auth, body, lambda d: d / IG_FILENAME, "ig-cards.json")


@page_router.post("/{run_id}/approve/{channel}")
async def approve_channel(
    run_id: str,
    channel: str,
    nakama_auth: str | None = Cookie(None),
):
    """Write the ``.approved.<channel>`` sentinel for this run.

    ``channel`` must be one of: ``blog``, ``fb.light``, ``fb.emotional``,
    ``fb.serious``, ``fb.neutral``, ``ig``.  The underlying artifact file
    (``blog.md`` / ``fb-<tonal>.md`` / ``ig-cards.json``) must exist — 409
    if Brook never produced it (no phantom approvals).  Idempotent: re-
    approving an already-approved channel is a no-op.
    """
    _require_mutation_auth(nakama_auth)
    run_dir = _validate_run_id(run_id)
    if channel not in _CHANNEL_SET:
        raise HTTPException(status_code=404, detail=f"unknown channel {channel!r}")
    artifact = _channel_artifact_path(run_dir, channel)
    if not artifact.exists():
        raise HTTPException(
            status_code=409,
            detail=f"cannot approve {channel}: artifact {artifact.name} not yet produced",
        )
    sentinel = _sentinel_path(run_dir, "approved", channel)
    sentinel.touch()
    logger.info(f"approved {channel} for run_id={run_id}")
    return {"ok": True, "channel": channel, "status": _run_status(run_dir)}


@page_router.post("/{run_id}/publish/blog")
async def publish_blog(
    run_id: str,
    nakama_auth: str | None = Cookie(None),
):
    """Hand the blog markdown to Usopp for WordPress draft creation.

    Slice 10 carry-over: the ``repurpose blog.md → DraftV1`` adapter
    (title / excerpt / slug_candidates / primary_category / secondary_categories
    / tags / focus_keyword / meta_description extraction) is **not** in scope
    for this PR — Brook's blog renderer (Slice 6) produces raw markdown only.
    Returning 501 keeps the UI honest about the gap.  Tracked separately
    against #283.
    """
    _require_mutation_auth(nakama_auth)
    run_dir = _validate_run_id(run_id)
    if not _channel_approved(run_dir, "blog"):
        raise HTTPException(status_code=409, detail="blog not yet approved")
    if not (run_dir / BLOG_FILENAME).exists():
        raise HTTPException(status_code=404, detail="blog.md not found")
    # Hook point: the adapter call lives here when implemented.  See
    # ``_enqueue_blog_to_usopp`` below — overrideable in tests via monkeypatch.
    try:
        result = _enqueue_blog_to_usopp(run_dir)
    except NotImplementedError as exc:
        return JSONResponse(
            status_code=501,
            content={
                "ok": False,
                "error": "adapter_missing",
                "detail": str(exc),
                "hint": "blog.md → DraftV1 adapter is a carry-over from #293",
            },
        )
    # Adapter success path: write the published sentinel and bubble the result.
    _sentinel_path(run_dir, "published", "blog").touch()
    logger.info(f"published blog for run_id={run_dir.name} draft_id={result.get('draft_id')}")
    return {"ok": True, "status": "published", **result}


def _enqueue_blog_to_usopp(run_dir: Path) -> dict:
    """Adapter: repurpose blog.md → approval_queue DraftV1 → Usopp picks up.

    NOT YET IMPLEMENTED (Slice 10 carry-over).  The blog renderer (Brook
    Slice 6) produces raw markdown without the structured fields that
    ``DraftV1`` requires (slug_candidates / primary_category / focus_keyword
    / meta_description).  A follow-up issue against #283 will:

      1. Extend Brook's blog renderer to emit a sidecar ``blog.meta.json``
         carrying these fields, OR
      2. Add a Stage-3 LLM pass at approve-time to extract them.

    Tests monkeypatch this function to simulate both success and failure paths
    without standing up the full Usopp pipeline.
    """
    raise NotImplementedError("repurpose blog.md → DraftV1 adapter is a Slice 10 carry-over")
