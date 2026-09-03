"""Authenticated, episode-first review gate for Podcast IG Carousels."""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Cookie, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.requests import Request

from agents.brook.podcast_carousel_autorun import is_autorunnable
from agents.brook.podcast_carousel_render import _digest_files
from scripts.podcast_carousel_correction_job import (
    CorrectionJobTransitionError,
    correction_job_path,
    create_queued_job,
    list_jobs,
    load_job,
)
from scripts.podcast_carousel_publish_job import (
    PublishJobTransitionError,
    create_or_get_publish_job,
    list_publish_jobs,
    load_publish_job,
    publish_job_path,
    publish_release_lock,
    published_publish_platforms,
    republish_required_platforms,
    supersede_queued_publish_job,
    unfinished_publish_platforms,
)
from shared.log import get_logger
from shared.schemas.carousel_publish import (
    CarouselPublishAsset,
    CarouselPublishJobV1,
    CarouselPublishTarget,
)
from shared.schemas.podcast_carousel import (
    CAROUSEL_ASSET_FIELDS,
    CAROUSEL_DISPLAY_COPY_FIELDS,
    CAROUSEL_TEXT_LAYOUT_REGIONS,
    CAROUSEL_TEXT_SAFE_RECTS,
    CarouselCopyEdit,
    CarouselCorrectionItem,
    CarouselCorrectionJobV1,
    CarouselEditorApplyRequest,
    CarouselFeedbackRevision,
    CarouselPageDecision,
    CarouselReviewFeedbackV1,
    CarouselReviewManifestV1,
    CarouselTextLayoutEdit,
    CoverLayoutOverride,
    PodcastCarouselCopySpecV1,
    receipt_for,
)
from thousand_sunny.auth import WEB_SECRET, check_auth

page_router = APIRouter(prefix="/bridge/ig-cards", tags=["bridge-ig-cards"])
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)
logger = get_logger("nakama.web.carousel_review")


# 純結構化修改在本機直接跑完（見 `podcast_carousel_autorun`）。VPS 那台是 control
# plane，沒有 Chrome、也沒有 footage 磁碟，必須關掉——所以這是真的部署開關，
# 不是測試用的旁門。測試也靠它把出圖擋在外面。
def _autorun_enabled() -> bool:
    """預設**關閉**。要出圖的那台自己開。

    原本預設開啟，而 repo 裡沒有任何地方替 VPS 關掉（2026-09-03 review 抓到）。
    VPS 是 control plane，沒有 Chrome 也沒有 footage 磁碟：背景任務會先把工作
    **認領**走、再因為找不到 Chrome 而標成 `failed`——比原本的行為更糟，因為
    那張單本來還可以留在 `queued` 等真的執行者來接。fail closed。
    """
    return os.environ.get("NAKAMA_CAROUSEL_AUTORUN", "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }


_ROLE_LABELS = {
    "cover": "封面",
    "hook": "開場提問",
    "point": "重點",
    "quote": "引言",
    "cta": "收尾導流",
}
_MAX_FEEDBACK = 1200
_MAX_CAPTION = 5000
_INSTAGRAM_MAX_CAPTION = 2200
_PUBLISH_STATUS_LABELS = {
    "queued": "等待執行者認領",
    "claimed": "執行者已認領",
    "in_progress": "發布進行中",
    "completed": "發布已完成",
    "failed": "發布未完成，可重試",
    "superseded": "發布核准已撤回",
}
_JOB_STATUS_LABELS = {
    "queued": "等待 agent 認領",
    "claimed": "agent 已認領",
    "in_progress": "處理中",
    "completed": "已完成",
    "failed": "未完成",
}
_BASE_HREF_RE = re.compile(r'<base href="[^"]*">')
_EDITOR_PATCH_RE = re.compile(r"\bwindow\.applyEditorPatch\s*=")
_EDITOR_REFIT_RE = re.compile(r"\bwindow\.__carouselRefit\s*=")


def _shosho_asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    digest = hashlib.sha1()
    for name in (
        "tokens.css",
        "bridge.css",
        "bridge-pages.css",
        "carousel-review.css",
        "carousel-publish.css",
        "carousel-preview-bridge.js",
    ):
        asset = static_dir / name
        if asset.is_file():
            digest.update(asset.read_bytes())
    return digest.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


def _preview_asset_token(
    episode_slug: str,
    template_sha256: str,
    manifest_sha256: str,
) -> str:
    key = (WEB_SECRET or "nakama-local-preview").encode()
    scope = f"{episode_slug}:{template_sha256}:{manifest_sha256}".encode()
    return hmac.new(key, scope, hashlib.sha256).hexdigest()


#: 選圖器送進來的檔名——與 `shared.schemas.podcast_carousel._CUTOUT_RE` 同一條規則。
_CUTOUT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(?:png|PNG)$")


def _episode_dir(episode_slug: str) -> Path:
    if (
        not episode_slug
        or len(episode_slug) > 120
        or episode_slug in {".", ".."}
        or any(character in episode_slug for character in ("/", "\\", ":", "\x00"))
    ):
        raise HTTPException(status_code=404, detail="invalid episode slug")
    root_value = os.environ.get("PODCAST_EPISODES_ROOT", "").strip()
    if not root_value:
        raise HTTPException(status_code=503, detail="PODCAST_EPISODES_ROOT is not configured")
    root = Path(root_value)
    if not root.is_dir():
        raise HTTPException(status_code=503, detail="PODCAST_EPISODES_ROOT is not a directory")
    candidate = root / episode_slug
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise HTTPException(status_code=404, detail="invalid episode slug") from error
    if not candidate.is_dir():
        raise HTTPException(status_code=404, detail=f"episode not found: {episode_slug}")
    return candidate


def _contained_file(path_value: str, root: Path) -> Path:
    path = Path(path_value)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail="carousel artifact escapes package root"
        ) from error
    if not path.is_file():
        raise HTTPException(status_code=422, detail=f"carousel artifact missing: {path.name}")
    return path


def _contained_directory(path_value: str, root: Path) -> Path:
    path = Path(path_value)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail="carousel directory escapes package root",
        ) from error
    if not path.is_dir():
        raise HTTPException(status_code=422, detail="carousel directory is missing")
    return path


def _read_verified_bytes(
    path: Path,
    *,
    expected_sha256: str,
    changed_detail: str,
    expected_bytes: int | None = None,
) -> tuple[bytes, str]:
    """Verify and return one immutable read buffer without a check/use gap."""

    try:
        payload = path.read_bytes()
    except OSError as error:
        raise HTTPException(status_code=409, detail=changed_detail) from error
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if (
        expected_bytes is not None
        and len(payload) != expected_bytes
        or not hmac.compare_digest(actual_sha256, expected_sha256)
    ):
        raise HTTPException(status_code=409, detail=changed_detail)
    return payload, actual_sha256


@lru_cache(maxsize=64)
def _verified_snapshot_receipts(
    template_root_value: str,
    expected_sha256: str,
) -> dict[str, tuple[int, str]]:
    """Verify one immutable Template Snapshot and cache its exact file receipts."""

    template_root = Path(template_root_value).resolve(strict=True)
    files = [
        (path.relative_to(template_root).as_posix(), path)
        for path in template_root.rglob("*")
        if path.is_file()
    ]
    if _digest_files(files) != expected_sha256:
        raise HTTPException(status_code=409, detail="carousel template snapshot changed")
    receipts: dict[str, tuple[int, str]] = {}
    for relative, path in files:
        receipt = receipt_for(path)
        receipts[relative] = (receipt.bytes, receipt.sha256)
    return receipts


def _load_manifest(
    episode_slug: str, *, verify_pages: bool = True
) -> tuple[Path, CarouselReviewManifestV1, str]:
    package_root = _episode_dir(episode_slug) / "ig-carousel"
    current_path = package_root / "current.json"
    if not current_path.is_file():
        raise HTTPException(status_code=404, detail="carousel has not been rendered")
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
        manifest_path = _contained_file(str(current["manifest"]), package_root)
        manifest_payload, manifest_sha256 = _read_verified_bytes(
            manifest_path,
            expected_sha256=str(current["manifest_sha256"]),
            changed_detail="current carousel manifest changed",
        )
        manifest = CarouselReviewManifestV1.model_validate_json(manifest_payload)
    except HTTPException:
        raise
    except (OSError, KeyError, TypeError, ValueError, ValidationError) as error:
        raise HTTPException(status_code=422, detail="invalid carousel review package") from error
    if verify_pages:
        for page in manifest.pages:
            image_path = _contained_file(page.image.path, package_root)
            _read_verified_bytes(
                image_path,
                expected_sha256=page.image.sha256,
                expected_bytes=page.image.bytes,
                changed_detail=f"carousel page changed: {page.page_id}",
            )
    return package_root, manifest, manifest_sha256


def _load_copy_spec(
    package_root: Path, manifest: CarouselReviewManifestV1
) -> PodcastCarouselCopySpecV1:
    copy_path = _contained_file(manifest.copy_spec.path, package_root)
    try:
        payload = copy_path.read_bytes()
    except OSError as error:
        raise HTTPException(status_code=409, detail="carousel copy spec changed") from error
    if len(payload) != manifest.copy_spec.bytes or not hmac.compare_digest(
        hashlib.sha256(payload).hexdigest(), manifest.copy_spec.sha256
    ):
        raise HTTPException(status_code=409, detail="carousel copy spec changed")
    try:
        spec = PodcastCarouselCopySpecV1.model_validate_json(payload)
    except (OSError, ValidationError) as error:
        raise HTTPException(status_code=422, detail="invalid carousel copy spec") from error
    if spec.episode_id != manifest.episode_id or spec.revision != manifest.revision:
        raise HTTPException(status_code=422, detail="carousel copy spec identity mismatch")
    return spec


def _editor_contract_state(
    package_root: Path, manifest: CarouselReviewManifestV1
) -> tuple[str, Path | None, str | None]:
    """Verify immutable render input before advertising editor capability."""

    if manifest.render_input is None:
        return "missing", None, None
    render_input = _contained_file(manifest.render_input.path, package_root)
    try:
        payload = render_input.read_bytes()
        if len(payload) != manifest.render_input.bytes or not hmac.compare_digest(
            hashlib.sha256(payload).hexdigest(), manifest.render_input.sha256
        ):
            return "receipt_changed", render_input, None
        source = payload.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return "invalid", render_input, None
    if not _EDITOR_PATCH_RE.search(source) or not _EDITOR_REFIT_RE.search(source):
        return "precontract", render_input, source
    return "available", render_input, source


def _editor_unavailable_message(state: str) -> str:
    if state == "precontract":
        return (
            "此 immutable revision 的 render_input 不含 canonical editor API；"
            "請用目前 renderer 產生新 revision 後再編輯。"
        )
    if state in {"receipt_changed", "invalid"}:
        return "此版本的安全預覽驗證失敗；請用目前 renderer 產生新 revision 後再編輯。"
    return "此舊版本仍可檢查與填寫修改意見；需先產生含安全預覽收據的新版本。"


def _feedback_path(package_root: Path) -> Path:
    return package_root / "review_feedback.v1.json"


def _load_feedback(package_root: Path, episode_id: str) -> CarouselReviewFeedbackV1:
    path = _feedback_path(package_root)
    if not path.is_file():
        return CarouselReviewFeedbackV1(episode_id=episode_id)
    try:
        feedback = CarouselReviewFeedbackV1.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise HTTPException(status_code=422, detail="invalid carousel review feedback") from error
    if feedback.episode_id != episode_id:
        raise HTTPException(status_code=422, detail="carousel feedback episode mismatch")
    return feedback


def _write_feedback(path: Path, feedback: CarouselReviewFeedbackV1) -> None:
    pending = path.with_suffix(".tmp")
    pending.write_text(feedback.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    pending.replace(path)


def _approved_revision(
    package_root: Path,
    manifest: CarouselReviewManifestV1,
    manifest_sha256: str,
) -> CarouselFeedbackRevision | None:
    matching = [
        revision
        for revision in _load_feedback(package_root, manifest.episode_id).revisions
        if revision.carousel_revision == manifest.revision
        and revision.manifest_sha256 == manifest_sha256
    ]
    latest = matching[-1] if matching else None
    return latest if latest is not None and latest.decision == "approved" else None


def _require_approved_revision(
    package_root: Path,
    manifest: CarouselReviewManifestV1,
    manifest_sha256: str,
) -> CarouselFeedbackRevision:
    approval = _approved_revision(package_root, manifest, manifest_sha256)
    if approval is None:
        raise HTTPException(
            status_code=403,
            detail="current carousel manifest has not passed the Review Gate",
        )
    return approval


def _publish_capabilities(
    publish_compatibility: str, asset_count: int
) -> list[CarouselPublishTarget]:
    required_meta_settings = (
        "META_GRAPH_API_VERSION",
        "META_PAGE_ID",
        "META_IG_USER_ID",
        "META_PAGE_ACCESS_TOKEN",
        "META_MEDIA_R2_ACCOUNT_ID",
        "META_MEDIA_R2_ACCESS_KEY_ID",
        "META_MEDIA_R2_SECRET_ACCESS_KEY",
        "META_MEDIA_R2_BUCKET",
    )
    missing_meta_settings = [
        name for name in required_meta_settings if not os.environ.get(name, "").strip()
    ]
    meta_configured = publish_compatibility == "api_compatible" and not missing_meta_settings
    meta_strategy = "meta_api" if meta_configured else "agent_browser"
    meta_state = "configured" if meta_configured else "agent_browser_required"
    meta_capability = "meta_api" if meta_configured else "browser_session"
    if publish_compatibility == "manual_only":
        meta_note = "這組輪播需使用已登入的瀏覽器發布；即使已設定 Meta 發布連線，也不能改走 API。"
    else:
        meta_note = (
            "已設定 Meta 發布連線；認領的 agent 必須具備對應發布權限。"
            if meta_configured
            else (
                "Meta API 尚未完整設定（缺少："
                + "、".join(missing_meta_settings)
                + "）；請使用已登入該平台的瀏覽器工作階段。"
            )
        )
    youtube_eligible = asset_count <= 10
    youtube_reason = (
        None if youtube_eligible else "YouTube Community 最多接受 10 張圖片；這組輪播已超過上限。"
    )
    return [
        CarouselPublishTarget(
            platform="instagram",
            strategy=meta_strategy,
            configuration_state=meta_state,
            required_executor_capabilities=[meta_capability],
            note=meta_note,
        ),
        CarouselPublishTarget(
            platform="facebook_page",
            strategy=meta_strategy,
            configuration_state=meta_state,
            required_executor_capabilities=[meta_capability],
            note=meta_note,
        ),
        CarouselPublishTarget(
            platform="youtube_community",
            strategy="agent_browser_manual",
            configuration_state="manual_only",
            required_executor_capabilities=["browser_session"],
            note=(
                youtube_reason
                or "YouTube Community 沒有可用的自動發布端點；"
                "請使用已登入的瀏覽器，並在送出前人工確認。"
            ),
            eligible=youtube_eligible,
            ineligibility_reason=youtube_reason,
        ),
    ]


def _publish_assets(manifest: CarouselReviewManifestV1) -> list[CarouselPublishAsset]:
    return [
        CarouselPublishAsset(
            page_id=page.page_id,
            page_number=page.page_number,
            image=page.image,
        )
        for page in manifest.pages
    ]


def _publish_job_payload(
    episode_slug: str,
    package_root: Path,
    job: CarouselPublishJobV1,
) -> dict:
    unfinished = set(unfinished_publish_platforms(job))
    capabilities = sorted(
        {
            capability
            for target in job.targets
            if target.platform in unfinished
            for capability in target.required_executor_capabilities
        }
    )
    job_file = publish_job_path(package_root, job.job_id)

    def claim_command(executor: str) -> str:
        capability_args = "".join(f" --capability {capability}" for capability in capabilities)
        return (
            "python scripts/podcast_carousel_publish_job.py claim "
            f'"{job_file}" --executor {executor} '
            f"--executor-id <agent-id>{capability_args}"
        )

    return {
        **job.model_dump(mode="json"),
        "status_url": f"/bridge/ig-cards/{episode_slug}/publish/jobs/{job.job_id}",
        "claim_commands": {
            "codex": claim_command("codex"),
            "claude_code": claim_command("claude_code"),
        },
    }


def _matching_publish_jobs(
    package_root: Path,
    manifest: CarouselReviewManifestV1,
    manifest_sha256: str,
) -> list[CarouselPublishJobV1]:
    return [
        job
        for job in list_publish_jobs(package_root)
        if job.source_revision == manifest.revision
        and job.source_manifest_sha256 == manifest_sha256
    ]


def _published_platforms(jobs: list[CarouselPublishJobV1]) -> list[str]:
    return sorted({platform for job in jobs for platform in published_publish_platforms(job)})


def _validated_publish_request(form, manifest: CarouselReviewManifestV1):
    caption = str(form.get("caption", "")).strip()
    if not caption:
        raise HTTPException(status_code=400, detail="publish caption is required")
    if len(caption) > _MAX_CAPTION:
        raise HTTPException(status_code=400, detail="publish caption is too long")
    requested = [str(value) for value in form.getlist("platforms")]
    if not requested:
        raise HTTPException(status_code=400, detail="select at least one publish platform")
    if len(requested) != len(set(requested)):
        raise HTTPException(status_code=400, detail="publish platforms must be unique")
    if "instagram" in requested and len(caption) > _INSTAGRAM_MAX_CAPTION:
        raise HTTPException(
            status_code=400,
            detail="Instagram caption cannot exceed 2,200 characters",
        )
    capability_by_platform = {
        item.platform: item
        for item in _publish_capabilities(manifest.publish_compatibility, len(manifest.pages))
    }
    unknown = sorted(set(requested) - set(capability_by_platform))
    if unknown:
        raise HTTPException(status_code=400, detail=f"unsupported publish platform: {unknown[0]}")
    targets = [capability_by_platform[platform] for platform in requested]
    ineligible = next((target for target in targets if not target.eligible), None)
    if ineligible is not None:
        raise HTTPException(
            status_code=400,
            detail=ineligible.ineligibility_reason or "publish platform is not eligible",
        )
    return caption, targets


def _context(episode_slug: str) -> dict:
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    spec = _load_copy_spec(package_root, manifest)
    feedback = _load_feedback(package_root, manifest.episode_id)
    editor_state, _, _ = _editor_contract_state(package_root, manifest)
    matching = [
        revision
        for revision in feedback.revisions
        if revision.carousel_revision == manifest.revision
        and revision.manifest_sha256 == manifest_sha256
    ]
    latest = matching[-1] if matching else None
    matching_publish_jobs = _matching_publish_jobs(package_root, manifest, manifest_sha256)
    latest_publish_job = matching_publish_jobs[-1] if matching_publish_jobs else None
    latest_by_id = {page.page_id: page for page in latest.pages} if latest else {}
    rows = []
    for page in manifest.pages:
        decision = latest_by_id.get(page.page_id)
        copy_values = page.copy_page.model_dump(mode="json")
        rows.append(
            {
                "page": page,
                "role_label": _ROLE_LABELS[page.role],
                "status": decision.status if decision else "pending",
                "feedback": decision.feedback if decision else "",
                "editor_fields": [
                    {"name": name, "value": copy_values[name]}
                    for name in CAROUSEL_DISPLAY_COPY_FIELDS[page.role]
                    if copy_values.get(name) is not None
                ],
            }
        )
    # 這一集能選的去背照。修修 2026-09-02：「我可能會重複選擇不同的卡，看看整個
    # 畫面的感覺」——所以選圖要跟即時預覽在同一個地方：封面與金句的卡片編輯器內。
    # 第一版把清單放在頁面最上方、點一下直接開修正單，等於沒看到結果就先送出，
    # 修修當場反映邏輯不對。清單本身跟卡片無關（同一批去背照兩張卡共用），
    # 但「選哪一張」屬於某一張卡，所以資料一份、控制項在編輯器裡。
    cutouts_dir = _episode_dir(episode_slug) / "packaging" / "cutouts"
    guest_cutouts = (
        sorted(
            path.name
            for path in cutouts_dir.glob("*.png")
            # `.pre-YYYYMMDD-…` 是被取代的備份版本，不該出現在可選清單裡。
            if path.name.startswith("guest_") and ".pre-" not in path.name
        )
        if cutouts_dir.is_dir()
        else []
    )
    return {
        "episode_slug": episode_slug,
        "manifest": manifest,
        "manifest_sha256": manifest_sha256,
        "rows": rows,
        "guest_cutouts": guest_cutouts,
        # 介面文案要跟實際行為一致：autorun 開著時不該再叫使用者去找 agent 認領。
        "autorun_enabled": _autorun_enabled(),
        "editor_available": editor_state == "available",
        "editor_unavailable_reason": _editor_unavailable_message(editor_state),
        "editor_pages": [
            {
                "page_id": row["page"].page_id,
                "page_number": row["page"].page_number,
                "role": row["page"].role,
                "artifact_sha256": row["page"].image.sha256,
                "field_order": [item["name"] for item in row["editor_fields"]],
                "fields": {item["name"]: item["value"] for item in row["editor_fields"]},
                # 素材欄位跟文字欄位分開送：`field_order` 決定要長出哪些輸入框，
                # 選圖不是打字，混進去會變成要人手打檔名。
                "asset_fields": {
                    name: getattr(row["page"].copy_page, name)
                    for name in CAROUSEL_ASSET_FIELDS.get(row["page"].role, ())
                    if getattr(row["page"].copy_page, name, None)
                },
            }
            for row in rows
        ],
        "decision_count": len(matching),
        "approved": bool(latest and latest.decision == "approved"),
        "latest_publish_job": latest_publish_job,
        "latest_publish_status_label": (
            _PUBLISH_STATUS_LABELS[latest_publish_job.status]
            if latest_publish_job
            else "尚未建立發布工作"
        ),
        "publish_url": f"/bridge/ig-cards/{episode_slug}/publish",
        # 金句刻意沒有 schema default（A/B 兩版算圖預設不同，寫死一組必然對其中
        # 一版說謊）。沒有 override 時送 null，由預覽量出來的基準值當起點。
        "quote_layout": (
            spec.layout_overrides.quote.model_dump(mode="json")
            if spec.layout_overrides.quote is not None
            else None
        ),
        "cover_layout": (spec.layout_overrides.cover or CoverLayoutOverride()).model_dump(
            mode="json"
        ),
        "text_layout_overrides": [
            item.model_dump(mode="json") for item in spec.layout_overrides.text_regions
        ],
        "text_layout_registry": {
            role: list(regions) for role, regions in CAROUSEL_TEXT_LAYOUT_REGIONS.items()
        },
        "text_layout_safe_rects": {
            f"{role}.{region}": list(rect)
            for (role, region), rect in CAROUSEL_TEXT_SAFE_RECTS.items()
        },
        "asset_version": _SHOSHO_ASSET_VERSION,
    }


@page_router.get("/{episode_slug}", response_class=HTMLResponse)
async def carousel_review_board(
    request: Request,
    episode_slug: str,
    saved: bool = False,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/ig-cards/{episode_slug}", status_code=302)
    context = _context(episode_slug)
    context["saved"] = saved
    return _templates.TemplateResponse(request, "carousel_review.html", context)


@page_router.get("/{episode_slug}/publish", response_class=HTMLResponse)
async def carousel_publish_board(
    request: Request,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Render the explicit Stage 6 hand-off after Review Gate approval."""

    if not check_auth(nakama_auth):
        return RedirectResponse(
            f"/login?next=/bridge/ig-cards/{episode_slug}/publish",
            status_code=302,
        )
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    approval = _require_approved_revision(package_root, manifest, manifest_sha256)
    matching_jobs = _matching_publish_jobs(package_root, manifest, manifest_sha256)
    latest_job = matching_jobs[-1] if matching_jobs else None
    capabilities = _publish_capabilities(manifest.publish_compatibility, len(manifest.pages))
    republish_required = (
        republish_required_platforms(
            package_root=package_root,
            source_revision=manifest.revision,
            source_manifest_sha256=manifest_sha256,
            source_publish_compatibility=manifest.publish_compatibility,
            caption=latest_job.caption,
            targets=latest_job.targets,
        )
        if latest_job is not None
        else []
    )
    return _templates.TemplateResponse(
        request,
        "carousel_publish.html",
        {
            "episode_slug": episode_slug,
            "manifest": manifest,
            "manifest_sha256": manifest_sha256,
            "approval": approval,
            "capabilities": capabilities,
            "latest_job": latest_job,
            "republish_required_platforms": republish_required,
            "latest_job_payload": (
                _publish_job_payload(episode_slug, package_root, latest_job) if latest_job else None
            ),
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@page_router.post("/{episode_slug}/publish/preflight")
async def carousel_publish_preflight(
    request: Request,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Revalidate the exact Stage 6 request immediately before submission."""

    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    form = await request.form()
    package_root = _episode_dir(episode_slug) / "ig-carousel"
    with publish_release_lock(package_root):
        package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
        _assert_current_manifest(form, manifest_sha256)
        _require_approved_revision(package_root, manifest, manifest_sha256)
        caption, targets = _validated_publish_request(form, manifest)
        required = republish_required_platforms(
            package_root=package_root,
            source_revision=manifest.revision,
            source_manifest_sha256=manifest_sha256,
            source_publish_compatibility=manifest.publish_compatibility,
            caption=caption,
            targets=targets,
        )
    return {
        "source_revision": manifest.revision,
        "source_manifest_sha256": manifest_sha256,
        "republish_required_platforms": required,
    }


@page_router.get("/{episode_slug}/publish/context")
async def carousel_publish_context(
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Return fresh release context for client-side republish revalidation."""

    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    _require_approved_revision(package_root, manifest, manifest_sha256)
    matching_jobs = _matching_publish_jobs(package_root, manifest, manifest_sha256)
    latest_job = matching_jobs[-1] if matching_jobs else None
    return {
        "source_revision": manifest.revision,
        "source_manifest_sha256": manifest_sha256,
        "published_platforms": _published_platforms(matching_jobs),
        "latest_job": (
            _publish_job_payload(episode_slug, package_root, latest_job) if latest_job else None
        ),
    }


@page_router.post("/{episode_slug}/publish/jobs")
async def carousel_publish_create_job(
    request: Request,
    response: Response,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Create a local Stage 6 job; this endpoint never publishes externally."""

    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    form = await request.form()
    allow_republish = str(form.get("confirm_republish", "")).lower() == "true"
    package_root = _episode_dir(episode_slug) / "ig-carousel"
    with publish_release_lock(package_root):
        package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
        _assert_current_manifest(form, manifest_sha256)
        approval = _require_approved_revision(package_root, manifest, manifest_sha256)
        caption, targets = _validated_publish_request(form, manifest)
        try:
            job, created = create_or_get_publish_job(
                package_root=package_root,
                episode_id=manifest.episode_id,
                source_revision=manifest.revision,
                source_manifest_sha256=manifest_sha256,
                source_publish_compatibility=manifest.publish_compatibility,
                approval_revision_number=approval.revision_number,
                approved_at=approval.created_at,
                caption=caption,
                assets=_publish_assets(manifest),
                targets=targets,
                allow_republish=allow_republish,
            )
        except PublishJobTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    response.status_code = 201 if created else 200
    return {
        **_publish_job_payload(episode_slug, package_root, job),
        "idempotent": not created,
    }


@page_router.get(
    "/{episode_slug}/publish/jobs/{job_id}",
    response_model=CarouselPublishJobV1,
)
async def carousel_publish_job_status(
    episode_slug: str,
    job_id: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root = _episode_dir(episode_slug) / "ig-carousel"
    try:
        return load_publish_job(publish_job_path(package_root, job_id))
    except (FileNotFoundError, OSError, ValueError, ValidationError) as error:
        raise HTTPException(status_code=404, detail="publish job not found") from error


@page_router.get("/{episode_slug}/media/{page_id}")
async def carousel_review_media(
    episode_slug: str,
    page_id: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root, manifest, _ = _load_manifest(episode_slug, verify_pages=False)
    page = next((item for item in manifest.pages if item.page_id == page_id), None)
    if page is None:
        raise HTTPException(status_code=404, detail="carousel page not found")
    image_path = _contained_file(page.image.path, package_root)
    payload, _ = _read_verified_bytes(
        image_path,
        expected_sha256=page.image.sha256,
        expected_bytes=page.image.bytes,
        changed_detail=f"carousel page changed: {page.page_id}",
    )
    return Response(content=payload, media_type="image/png")


@page_router.get("/{episode_slug}/cutout/{name}")
async def carousel_review_cutout(
    episode_slug: str,
    name: str,
    nakama_auth: str | None = Cookie(None),
):
    """回傳一張候選去背照，供頁面上方的選圖器顯示縮圖。

    `name` 由使用者提供，會被拿去組路徑，所以先用 schema 的檔名規則過濾再
    `_contained_file` 二次確認——只認 `packaging/cutouts` 底下的單純檔名。
    """
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    if not _CUTOUT_NAME_RE.fullmatch(name):
        raise HTTPException(status_code=404, detail="cutout not found")
    cutouts_dir = (_episode_dir(episode_slug) / "packaging" / "cutouts").resolve()
    try:
        path = _contained_file(cutouts_dir / name, cutouts_dir)
    except HTTPException:
        raise
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=404, detail="cutout not found") from error
    if not path.is_file():
        raise HTTPException(status_code=404, detail="cutout not found")
    return Response(content=path.read_bytes(), media_type="image/png")


@page_router.get("/{episode_slug}/preview/{page_id}", response_class=HTMLResponse)
async def carousel_editor_preview(
    episode_slug: str,
    page_id: str,
    manifest_sha256: str,
    nakama_auth: str | None = Cookie(None),
):
    """Serve a receipt-verified render DOM in an opaque-origin sandbox."""

    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root, manifest, current_sha256 = _load_manifest(episode_slug)
    if manifest_sha256 != current_sha256:
        raise HTTPException(status_code=409, detail="carousel revision changed; reload editor")
    page = next((item for item in manifest.pages if item.page_id == page_id), None)
    if page is None:
        raise HTTPException(status_code=404, detail="carousel page not found")
    editor_state, render_input, source = _editor_contract_state(package_root, manifest)
    if editor_state == "missing":
        raise HTTPException(
            status_code=409,
            detail="legacy carousel revision has no trusted editor preview; render a new revision",
        )
    if editor_state == "receipt_changed":
        raise HTTPException(status_code=409, detail="carousel render input changed")
    if editor_state == "invalid":
        raise HTTPException(status_code=422, detail="carousel render input is invalid")
    if editor_state == "precontract":
        raise HTTPException(
            status_code=409,
            detail="carousel revision predates canonical editor API; render a new revision",
        )
    assert render_input is not None and source is not None
    if len(_BASE_HREF_RE.findall(source)) != 1:
        raise HTTPException(status_code=422, detail="carousel preview has invalid base href")
    page_index = page.page_number - 1
    base = (
        f"/bridge/ig-cards/{quote(episode_slug, safe='')}/preview-assets/"
        f"{manifest.template.sha256}/"
        f"{_preview_asset_token(episode_slug, manifest.template.sha256, current_sha256)}/"
    )
    source = _BASE_HREF_RE.sub(f'<base href="{base}">', source, count=1)
    source = source.replace(
        "</head>",
        f'<script>history.replaceState(null,"",`?page={page_index}`);</script></head>',
        1,
    )
    if "</body>" not in source:
        raise HTTPException(status_code=422, detail="carousel preview has invalid body")
    bridge_path = (
        Path(__file__).resolve().parent.parent / "static" / "shosho" / "carousel-preview-bridge.js"
    )
    bridge_source = bridge_path.read_text(encoding="utf-8")
    source = source.replace(
        "</body>",
        f"<script>{bridge_source}</script></body>",
        1,
    )
    return HTMLResponse(
        source,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; img-src 'self' data:; font-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                "connect-src 'none'; form-action 'none'; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'self'"
            ),
        },
    )


@page_router.get("/{episode_slug}/preview-assets/{template_sha256}/{asset_token}/{asset_path:path}")
async def carousel_editor_preview_asset(
    episode_slug: str,
    template_sha256: str,
    asset_token: str,
    asset_path: str,
    nakama_auth: str | None = Cookie(None),
):
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    expected_token = _preview_asset_token(episode_slug, manifest.template.sha256, manifest_sha256)
    if not check_auth(nakama_auth) and not hmac.compare_digest(asset_token, expected_token):
        raise HTTPException(status_code=401, detail="authentication required")
    if template_sha256 != manifest.template.sha256:
        raise HTTPException(status_code=409, detail="carousel template changed")
    if not hmac.compare_digest(asset_token, expected_token):
        raise HTTPException(status_code=409, detail="carousel preview token changed")
    template_root = _contained_directory(manifest.template.root, package_root)
    try:
        expected_receipts = _verified_snapshot_receipts(
            str(template_root.resolve(strict=True)),
            manifest.template.sha256,
        )
    except (FileNotFoundError, OSError, ValueError) as error:
        raise HTTPException(
            status_code=409,
            detail="carousel template snapshot changed",
        ) from error
    asset = _contained_file(str(template_root / asset_path), template_root).resolve(strict=True)
    relative = asset.relative_to(template_root.resolve(strict=True)).as_posix()
    expected_receipt = expected_receipts.get(relative)
    if expected_receipt is None:
        raise HTTPException(status_code=409, detail="carousel template snapshot changed")
    try:
        payload = asset.read_bytes()
    except OSError as error:
        raise HTTPException(
            status_code=409,
            detail="carousel template snapshot changed",
        ) from error
    actual_receipt = (len(payload), hashlib.sha256(payload).hexdigest())
    if not hmac.compare_digest(actual_receipt[1], expected_receipt[1]) or (
        actual_receipt[0] != expected_receipt[0]
    ):
        raise HTTPException(status_code=409, detail="carousel template snapshot changed")
    media_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
    return Response(content=payload, media_type=media_type)


def _active_job_conflict(package_root: Path, manifest, manifest_sha256: str) -> str:
    """一次只允許一張進行中的修正單——但要講清楚是哪一張、內容是什麼。

    原本只回一句英文 `correction job is still active`，使用者在畫面上看不到那張
    單存在，也不知道要怎麼往下走（修修 2026-09-02 就卡在這裡：送出後只看到
    一句看不懂的錯誤，而擋住他的是他自己幾小時前送出、還沒有 agent 認領的
    那張換去背照的單）。
    """
    active = [
        job
        for job in list_jobs(package_root)
        if job.source_revision == manifest.revision
        and job.source_manifest_sha256 == manifest_sha256
        and job.status in {"queued", "claimed", "in_progress"}
    ]
    if not active:
        return "correction job is still active"
    job = active[-1]
    changed = sorted(
        {name for edit in job.copy_edits for name in edit.fields}
        | ({"cover_layout"} if job.layout_overrides else set())
        | ({"quote_layout"} if job.quote_layout_overrides else set())
        | {f"{item.region}" for item in job.text_layout_overrides}
    )
    summary = "、".join(changed) if changed else "回饋意見"
    status = _JOB_STATUS_LABELS.get(job.status, job.status)
    return (
        f"已經有一張待處理的修改工作 {job.job_id}"
        f"（{status}，內容：{summary}）。同一個版本一次只能有一張；"
        "請先讓 agent 認領處理完，或把那張標記為失敗，再送出新的修改。"
    )


def _autorun_structured_job(episode_slug: str, job_id: str) -> None:
    """背景執行一張純結構化修正單。失敗會落在工作上，不會靜默。

    Review Gate 看到 `failed` 會顯示原因並把草稿還給使用者，所以這裡的責任只是
    「跑，並且不要吞掉錯誤」。
    """
    from agents.brook.podcast_carousel_autorun import (
        StructuredAutorunError,
        execute_structured_job,
    )

    episode_dir = _episode_dir(episode_slug)
    job_path = episode_dir / "ig-carousel" / "correction_jobs" / f"{job_id}.json"
    try:
        result = execute_structured_job(
            episode_dir=episode_dir,
            job_path=job_path,
            executor_id="thousand-sunny-autorun",
        )
        logger.info(
            "carousel autorun completed job=%s revision=%s fields=%s",
            job_id,
            result.result_revision,
            result.changed_fields,
        )
    except StructuredAutorunError as error:
        logger.warning("carousel autorun failed job=%s: %s", job_id, error)
    except Exception:  # noqa: BLE001 — 背景任務不能把例外丟進虛空
        logger.exception("carousel autorun crashed job=%s", job_id)


def _assert_current_manifest(form, manifest_sha256: str) -> None:
    if str(form.get("manifest_sha256", "")) != manifest_sha256:
        raise HTTPException(
            status_code=409,
            detail="carousel revision changed; reload before saving",
        )


def _assert_manifest_sha256(value: str, manifest_sha256: str) -> None:
    if value != manifest_sha256:
        raise HTTPException(
            status_code=409,
            detail="carousel revision changed; reload before applying edits",
        )


def _append_feedback_revision(
    *,
    package_root: Path,
    manifest: CarouselReviewManifestV1,
    manifest_sha256: str,
    decisions: list[CarouselPageDecision],
    decision: str,
) -> None:
    feedback_store = _load_feedback(package_root, manifest.episode_id)
    feedback_store.revisions.append(
        CarouselFeedbackRevision(
            revision_number=len(feedback_store.revisions) + 1,
            created_at=datetime.now(UTC),
            carousel_revision=manifest.revision,
            manifest_sha256=manifest_sha256,
            decision=decision,
            pages=decisions,
        )
    )
    _write_feedback(_feedback_path(package_root), feedback_store)


@page_router.post(
    "/{episode_slug}/apply-edits",
    response_model=CarouselCorrectionJobV1,
    status_code=201,
)
async def carousel_review_apply_edits(
    request: Request,
    episode_slug: str,
    background: BackgroundTasks,
    nakama_auth: str | None = Cookie(None),
):
    """Validate structured edits and queue them without mutating rendered artifacts."""

    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    spec = _load_copy_spec(package_root, manifest)
    editor_state, _, _ = _editor_contract_state(package_root, manifest)
    if editor_state != "available":
        detail = (
            "carousel revision predates canonical editor API; render a new revision"
            if editor_state in {"missing", "precontract"}
            else "carousel editor preview is not receipt-verified; render a new revision"
        )
        raise HTTPException(status_code=409, detail=detail)
    try:
        payload = CarouselEditorApplyRequest.model_validate(await request.json())
    except (json.JSONDecodeError, TypeError, ValidationError) as error:
        raise HTTPException(status_code=422, detail="invalid structured carousel edits") from error
    _assert_manifest_sha256(payload.manifest_sha256, manifest_sha256)

    feedback_store = _load_feedback(package_root, manifest.episode_id)
    if any(
        revision.carousel_revision == manifest.revision
        and revision.manifest_sha256 == manifest_sha256
        and revision.decision == "approved"
        for revision in feedback_store.revisions
    ):
        raise HTTPException(status_code=409, detail="approved carousel revision is read-only")

    manifest_by_id = {page.page_id: page for page in manifest.pages}
    spec_by_id = {page.page_id: page for page in spec.pages}
    effective_copy_edits: list[CarouselCopyEdit] = []
    for edit in payload.copy_edits:
        review_page = manifest_by_id.get(edit.page_id)
        source_page = spec_by_id.get(edit.page_id)
        if review_page is None or source_page is None:
            raise HTTPException(status_code=422, detail=f"unknown carousel page: {edit.page_id}")
        if edit.role != review_page.role or edit.role != source_page.role:
            raise HTTPException(
                status_code=422, detail=f"carousel page role changed: {edit.page_id}"
            )
        if edit.artifact_sha256 != review_page.image.sha256:
            raise HTTPException(status_code=409, detail=f"carousel page changed: {edit.page_id}")
        changed_fields = {
            name: value
            for name, value in edit.fields.items()
            if getattr(source_page, name) != value
        }
        if not changed_fields:
            continue
        source_payload = source_page.model_dump(mode="json")
        try:
            updated_page = type(source_page).model_validate({**source_payload, **changed_fields})
        except ValidationError as error:
            raise HTTPException(
                status_code=422, detail=f"invalid display copy for {edit.page_id}"
            ) from error
        if updated_page.evidence != source_page.evidence:
            raise HTTPException(status_code=422, detail="transcript evidence is immutable")
        effective_copy_edits.append(edit.model_copy(update={"fields": changed_fields}))

    effective_layout = payload.layout_overrides
    if effective_layout is not None:
        cover = manifest_by_id.get("cover")
        if cover is None or cover.role != "cover":
            raise HTTPException(status_code=422, detail="cover page is missing")
        if effective_layout.artifact_sha256 != cover.image.sha256:
            raise HTTPException(status_code=409, detail="carousel page changed: cover")
        current_layout = spec.layout_overrides.cover or CoverLayoutOverride()
        if effective_layout.values == current_layout:
            effective_layout = None

    current_text_layouts = {
        (item.page_id, item.region): item for item in spec.layout_overrides.text_regions
    }
    effective_text_layouts: list[CarouselTextLayoutEdit] = []
    for edit in payload.text_layout_overrides:
        review_page = manifest_by_id.get(edit.page_id)
        source_page = spec_by_id.get(edit.page_id)
        if review_page is None or source_page is None:
            raise HTTPException(status_code=422, detail=f"unknown carousel page: {edit.page_id}")
        if edit.role != review_page.role or edit.role != source_page.role:
            raise HTTPException(
                status_code=422, detail=f"carousel page role changed: {edit.page_id}"
            )
        if edit.artifact_sha256 != review_page.image.sha256:
            raise HTTPException(status_code=409, detail=f"carousel page changed: {edit.page_id}")
        current = current_text_layouts.get((edit.page_id, edit.region))
        if current is None or current.values != edit.values:
            effective_text_layouts.append(edit)

    effective_quote_layout = payload.quote_layout_overrides
    if effective_quote_layout is not None:
        quote_page = manifest_by_id.get(effective_quote_layout.page_id)
        if quote_page is None or quote_page.role != "quote":
            raise HTTPException(status_code=422, detail="quote page is missing")
        if effective_quote_layout.artifact_sha256 != quote_page.image.sha256:
            raise HTTPException(
                status_code=409, detail=f"carousel page changed: {quote_page.page_id}"
            )
        if effective_quote_layout.values == spec.layout_overrides.quote:
            effective_quote_layout = None

    if (
        not effective_copy_edits
        and effective_layout is None
        and effective_quote_layout is None
        and not effective_text_layouts
    ):
        raise HTTPException(status_code=400, detail="at least one changed edit is required")
    prospective = spec.model_dump(mode="json")
    prospective_pages = {page["page_id"]: page for page in prospective["pages"]}
    for edit in effective_copy_edits:
        prospective_pages[edit.page_id].update(edit.fields)
    if effective_layout is not None:
        prospective["layout_overrides"]["cover"] = effective_layout.values.model_dump(mode="json")
    if effective_quote_layout is not None:
        prospective["layout_overrides"]["quote"] = effective_quote_layout.values.model_dump(
            mode="json"
        )
    prospective_text_layouts = {
        (item["page_id"], item["region"]): item
        for item in prospective["layout_overrides"].get("text_regions", [])
    }
    for edit in effective_text_layouts:
        prospective_text_layouts[(edit.page_id, edit.region)] = {
            "page_id": edit.page_id,
            "role": edit.role,
            "region": edit.region,
            "values": edit.values.model_dump(mode="json"),
        }
    prospective["layout_overrides"]["text_regions"] = list(prospective_text_layouts.values())
    try:
        PodcastCarouselCopySpecV1.model_validate(prospective)
    except ValidationError as error:
        raise HTTPException(
            status_code=422, detail="invalid prospective structured carousel edits"
        ) from error
    try:
        job = create_queued_job(
            package_root=package_root,
            episode_id=manifest.episode_id,
            source_revision=manifest.revision,
            source_manifest_sha256=manifest_sha256,
            copy_edits=effective_copy_edits,
            layout_overrides=effective_layout,
            quote_layout_overrides=effective_quote_layout,
            text_layout_overrides=effective_text_layouts,
        )
        # 修修 2026-09-03：「以後不能改成送出就自動驅動 Agent 去 render 嗎？
        # 多一個動作覺得不好。」純結構化修改的套用是決定性的，一步都用不到
        # LLM——那就不該再要一個人來按同樣那幾個指令。自由文字意見不在此列。
        if _autorun_enabled() and is_autorunnable(job):
            background.add_task(_autorun_structured_job, episode_slug, job.job_id)
        return job
    except CorrectionJobTransitionError as error:
        raise HTTPException(
            status_code=409,
            detail=_active_job_conflict(package_root, manifest, manifest_sha256),
        ) from error


@page_router.post(
    "/{episode_slug}/feedback",
    response_model=CarouselCorrectionJobV1,
    status_code=201,
)
async def carousel_review_feedback(
    request: Request,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Queue revision-bound corrections without invoking an executor."""

    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    form = await request.form()
    _assert_current_manifest(form, manifest_sha256)
    if any(str(form.get(f"status_{page.page_id}", "")) == "approved" for page in manifest.pages):
        raise HTTPException(status_code=400, detail="approval must use the approve endpoint")

    items: list[CarouselCorrectionItem] = []
    decisions: list[CarouselPageDecision] = []
    for page in manifest.pages:
        feedback = str(form.get(f"feedback_{page.page_id}", "")).strip()
        if len(feedback) > _MAX_FEEDBACK:
            raise HTTPException(status_code=400, detail=f"feedback too long: {page.page_id}")
        status = "needs_changes" if feedback else "pending"
        decisions.append(
            CarouselPageDecision(
                page_id=page.page_id,
                status=status,
                feedback=feedback,
                artifact_sha256=page.image.sha256,
            )
        )
        if feedback:
            items.append(
                CarouselCorrectionItem(
                    page_id=page.page_id,
                    artifact_sha256=page.image.sha256,
                    feedback=feedback,
                )
            )
    if not items:
        raise HTTPException(status_code=400, detail="at least one non-empty correction is required")

    with publish_release_lock(package_root):
        active = [
            correction_job
            for correction_job in list_jobs(package_root)
            if correction_job.source_revision == manifest.revision
            and correction_job.source_manifest_sha256 == manifest_sha256
            and correction_job.status in {"queued", "claimed", "in_progress"}
        ]
        if active:
            raise HTTPException(
                status_code=409,
                detail=_active_job_conflict(package_root, manifest, manifest_sha256),
            )

        matching_publish = [
            publish_job
            for publish_job in list_publish_jobs(package_root)
            if publish_job.source_revision == manifest.revision
            and publish_job.source_manifest_sha256 == manifest_sha256
        ]
        if any(
            publish_job.status in {"claimed", "in_progress"} for publish_job in matching_publish
        ):
            raise HTTPException(
                status_code=409,
                detail="publish job is active; fail it before requesting corrections",
            )
        for publish_job in matching_publish:
            if publish_job.status == "queued":
                supersede_queued_publish_job(
                    publish_job_path(package_root, publish_job.job_id),
                    reason="new correction feedback revoked the release approval",
                    release_locked=True,
                )

        try:
            job = create_queued_job(
                package_root=package_root,
                episode_id=manifest.episode_id,
                source_revision=manifest.revision,
                source_manifest_sha256=manifest_sha256,
                feedback_items=items,
            )
        except CorrectionJobTransitionError as error:
            raise HTTPException(
                status_code=409,
                detail=_active_job_conflict(package_root, manifest, manifest_sha256),
            ) from error
        _append_feedback_revision(
            package_root=package_root,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            decisions=decisions,
            decision="draft",
        )
    return job


@page_router.get(
    "/{episode_slug}/jobs/{job_id}",
    response_model=CarouselCorrectionJobV1,
)
async def carousel_correction_job_status(
    episode_slug: str,
    job_id: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root = _episode_dir(episode_slug) / "ig-carousel"
    try:
        job = load_job(correction_job_path(package_root, job_id))
    except (FileNotFoundError, OSError, ValueError, ValidationError) as error:
        raise HTTPException(status_code=404, detail="correction job not found") from error
    return job


@page_router.post("/{episode_slug}/approve")
async def carousel_review_approve(
    request: Request,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Record a review approval only; publishing remains a separate stage."""

    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    form = await request.form()
    _assert_current_manifest(form, manifest_sha256)
    if any(str(form.get(f"feedback_{page.page_id}", "")).strip() for page in manifest.pages):
        raise HTTPException(status_code=400, detail="approval cannot include correction feedback")
    with publish_release_lock(package_root):
        feedback_store = _load_feedback(package_root, manifest.episode_id)
        active = [
            job
            for job in list_jobs(package_root)
            if job.source_revision == manifest.revision
            and job.source_manifest_sha256 == manifest_sha256
            and job.status in {"queued", "claimed", "in_progress"}
        ]
        if active:
            raise HTTPException(
                status_code=409,
                detail=_active_job_conflict(package_root, manifest, manifest_sha256),
            )
        matching_revisions = [
            revision
            for revision in feedback_store.revisions
            if revision.carousel_revision == manifest.revision
            and revision.manifest_sha256 == manifest_sha256
        ]
        latest_matching = matching_revisions[-1] if matching_revisions else None
        if latest_matching is None or latest_matching.decision != "approved":
            decisions = [
                CarouselPageDecision(
                    page_id=page.page_id,
                    status="approved",
                    feedback="",
                    artifact_sha256=page.image.sha256,
                )
                for page in manifest.pages
            ]
            _append_feedback_revision(
                package_root=package_root,
                manifest=manifest,
                manifest_sha256=manifest_sha256,
                decisions=decisions,
                decision="approved",
            )
    return {
        "approved": True,
        "revision": manifest.revision,
        "manifest_sha256": manifest_sha256,
        "published": False,
        "publish_url": f"/bridge/ig-cards/{episode_slug}/publish",
    }


@page_router.post("/{episode_slug}/decide")
async def carousel_review_decide(
    request: Request,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/ig-cards/{episode_slug}", status_code=302)
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    form = await request.form()
    if str(form.get("manifest_sha256", "")) != manifest_sha256:
        raise HTTPException(
            status_code=409,
            detail="carousel revision changed; reload before saving",
        )
    decisions = []
    for page in manifest.pages:
        status = str(form.get(f"status_{page.page_id}", "pending"))
        feedback = str(form.get(f"feedback_{page.page_id}", "")).strip()
        if len(feedback) > _MAX_FEEDBACK:
            raise HTTPException(status_code=400, detail=f"feedback too long: {page.page_id}")
        try:
            decisions.append(
                CarouselPageDecision(
                    page_id=page.page_id,
                    status=status,
                    feedback=feedback,
                    artifact_sha256=page.image.sha256,
                )
            )
        except ValidationError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    decision = "approved" if all(item.status == "approved" for item in decisions) else "draft"
    with publish_release_lock(package_root):
        if decision == "approved":
            active_corrections = [
                job
                for job in list_jobs(package_root)
                if job.source_revision == manifest.revision
                and job.source_manifest_sha256 == manifest_sha256
                and job.status in {"queued", "claimed", "in_progress"}
            ]
            if active_corrections:
                raise HTTPException(status_code=409, detail="correction job is still active")
        else:
            matching_publish = [
                job
                for job in list_publish_jobs(package_root)
                if job.source_revision == manifest.revision
                and job.source_manifest_sha256 == manifest_sha256
            ]
            if any(job.status in {"claimed", "in_progress"} for job in matching_publish):
                raise HTTPException(
                    status_code=409,
                    detail="publish job is active; fail it before saving a review draft",
                )
            for job in matching_publish:
                if job.status == "queued":
                    supersede_queued_publish_job(
                        publish_job_path(package_root, job.job_id),
                        reason="new review draft revoked the release approval",
                        release_locked=True,
                    )
        _append_feedback_revision(
            package_root=package_root,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            decisions=decisions,
            decision=decision,
        )
    return RedirectResponse(f"/bridge/ig-cards/{episode_slug}?saved=1", status_code=303)
