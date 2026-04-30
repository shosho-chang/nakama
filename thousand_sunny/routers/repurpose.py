"""Bridge UI panel for repurpose runs — /bridge/repurpose/*.

Read-only skeleton (Slice 2).  Mutation logic (edit-in-place, per-channel
approve, blog → Usopp WP draft) lands in Slice 10.

Routes:
    GET /bridge/repurpose              — list all runs in data/repurpose/
    GET /bridge/repurpose/<run_id>     — 3-panel detail view (blog / FB / IG)
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.log import get_logger
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.repurpose")

page_router = APIRouter(prefix="/bridge/repurpose")
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)

_DATA_ROOT = Path("data/repurpose")
FB_TONALS = ("light", "emotional", "serious", "neutral")


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _list_runs() -> list[dict]:
    """Scan data/repurpose/ and return run summaries sorted newest-first."""
    if not _DATA_ROOT.exists():
        return []
    runs = []
    for d in sorted(_DATA_ROOT.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        stage1_path = d / "stage1.json"
        episode_type = ""
        if stage1_path.exists():
            try:
                data = json.loads(stage1_path.read_text(encoding="utf-8"))
                episode_type = data.get("episode_type", "")
            except Exception:
                pass
        artifact_count = sum(
            1
            for f in d.iterdir()
            if f.is_file() and f.suffix in (".md", ".json") and f.name != "stage1.json"
        )
        runs.append(
            {
                "run_id": d.name,
                "episode_type": episode_type,
                "artifact_count": artifact_count,
            }
        )
    return runs


def _load_run(run_id: str) -> dict:
    """Load all artifacts for a single run directory.

    Raises:
        FileNotFoundError: If the run directory does not exist.
    """
    run_dir = _DATA_ROOT / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(run_id)

    def _read(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8") if path.exists() else None
        except Exception:
            return None

    stage1_raw = _read(run_dir / "stage1.json")
    stage1_data: dict = {}
    if stage1_raw:
        try:
            stage1_data = json.loads(stage1_raw)
        except Exception:
            pass

    fb_variants = {t: _read(run_dir / f"fb-{t}.md") for t in FB_TONALS}

    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "stage1": stage1_data,
        "blog": _read(run_dir / "blog.md"),
        "fb": fb_variants,
        "ig": _read(run_dir / "ig-cards.json"),
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
        {"runs": runs},
    )


@page_router.get("/{run_id}", response_class=HTMLResponse)
async def repurpose_detail(
    request: Request,
    run_id: str,
    nakama_auth: str | None = Cookie(None),
):
    """3-panel detail view for a single repurpose run."""
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/repurpose/{run_id}", status_code=302)
    try:
        run = _load_run(run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return _templates.TemplateResponse(
        request,
        "repurpose_detail.html",
        {"run": run, "fb_tonals": FB_TONALS},
    )
