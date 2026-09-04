"""Deterministic file-state mutations for Podcast Carousel correction jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.brook.podcast_carousel_panel import (  # noqa: E402
    PanelResult,
    PanelReview,
    assert_panel_renderable,
)
from agents.brook.podcast_carousel_render import (  # noqa: E402
    _DEFAULT_CHROME,
    _content_sha,
    _digest_files,
    _render_page,
    _write_render_input,
)
from shared.schemas.podcast_carousel import (  # noqa: E402
    CAROUSEL_REQUIRED_REVIEWS,
    ArtifactReceipt,
    CarouselCopyEdit,
    CarouselCorrectionClaim,
    CarouselCorrectionCompletionEvidence,
    CarouselCorrectionItem,
    CarouselCorrectionJobV1,
    CarouselCorrectionProgress,
    CarouselCoverLayoutEdit,
    CarouselQuoteLayoutEdit,
    CarouselReviewerReceipt,
    CarouselReviewManifestV1,
    CarouselTextLayoutEdit,
    PageTextLayoutOverrideV1,
    PodcastCarouselCopySpecV1,
    TemplateSnapshot,
    receipt_for,
)


class CorrectionJobTransitionError(ValueError):
    """Raised when an executor attempts an invalid state transition."""


DEFAULT_LEASE_SECONDS = 30 * 60


def _contained_file(path: Path, package_root: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(package_root.resolve(strict=True))
    except (FileNotFoundError, ValueError, OSError) as error:
        raise CorrectionJobTransitionError(
            f"carousel artifact is missing or outside package: {path}"
        ) from error
    if not resolved.is_file():
        raise CorrectionJobTransitionError(f"carousel artifact is not a file: {path}")
    return resolved


def _verified_receipt(path: Path, package_root: Path) -> tuple[Path, ArtifactReceipt]:
    contained = _contained_file(path, package_root)
    return contained, receipt_for(contained)


def _read_artifact_once(
    path: Path,
    package_root: Path,
    *,
    expected: ArtifactReceipt | None = None,
    changed_message: str = "carousel artifact receipt changed",
) -> tuple[Path, bytes, ArtifactReceipt]:
    """Read one immutable artifact buffer and verify/parse that same buffer."""

    contained = _contained_file(path, package_root)
    try:
        payload = contained.read_bytes()
    except OSError as error:
        raise CorrectionJobTransitionError(changed_message) from error
    actual = ArtifactReceipt(
        path=str(contained.resolve()),
        bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    if expected is not None and actual != expected:
        raise CorrectionJobTransitionError(changed_message)
    return contained, payload, actual


def _verify_manifest_artifacts(
    manifest: CarouselReviewManifestV1,
    *,
    package_root: Path,
    require_render_input: bool = False,
) -> PodcastCarouselCopySpecV1:
    if manifest.render_input is None:
        if require_render_input:
            raise CorrectionJobTransitionError("result manifest has no render input receipt")
    else:
        _read_artifact_once(
            Path(manifest.render_input.path),
            package_root,
            expected=manifest.render_input,
            changed_message="carousel render input receipt changed",
        )
    _, copy_payload, _ = _read_artifact_once(
        Path(manifest.copy_spec.path),
        package_root,
        expected=manifest.copy_spec,
        changed_message="carousel Copy Spec receipt changed",
    )
    try:
        spec = PodcastCarouselCopySpecV1.model_validate_json(copy_payload)
    except (OSError, UnicodeDecodeError, ValidationError) as error:
        raise CorrectionJobTransitionError("carousel Copy Spec is invalid") from error
    if spec.episode_id != manifest.episode_id or spec.revision != manifest.revision:
        raise CorrectionJobTransitionError("carousel Copy Spec identity does not match manifest")
    if len(spec.pages) != len(manifest.pages):
        raise CorrectionJobTransitionError("carousel Copy Spec page count does not match manifest")
    for spec_page, manifest_page in zip(spec.pages, manifest.pages, strict=True):
        if spec_page != manifest_page.copy_page:
            raise CorrectionJobTransitionError(
                f"carousel Copy Spec page differs from manifest: {manifest_page.page_id}"
            )
        _read_artifact_once(
            Path(manifest_page.image.path),
            package_root,
            expected=manifest_page.image,
            changed_message=f"carousel page artifact receipt changed: {manifest_page.page_id}",
        )
    return spec


def _load_current_manifest(
    package_root: Path,
    *,
    require_render_input: bool = False,
) -> tuple[Path, ArtifactReceipt, CarouselReviewManifestV1, PodcastCarouselCopySpecV1]:
    current_path = package_root / "current.json"
    if not current_path.is_file():
        raise CorrectionJobTransitionError("carousel current.json is missing")
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
        manifest_path, manifest_payload, manifest_receipt = _read_artifact_once(
            Path(str(current["manifest"])), package_root
        )
        if current["manifest_sha256"] != manifest_receipt.sha256:
            raise CorrectionJobTransitionError("current carousel manifest receipt changed")
        manifest = CarouselReviewManifestV1.model_validate_json(manifest_payload)
    except CorrectionJobTransitionError:
        raise
    except (OSError, UnicodeDecodeError, KeyError, TypeError, ValueError, ValidationError) as error:
        raise CorrectionJobTransitionError("carousel current package is invalid") from error
    if (
        current.get("episode_id") != manifest.episode_id
        or current.get("revision") != manifest.revision
    ):
        raise CorrectionJobTransitionError("carousel current identity does not match manifest")
    spec = _verify_manifest_artifacts(
        manifest,
        package_root=package_root,
        require_render_input=require_render_input,
    )
    return manifest_path, manifest_receipt, manifest, spec


def _assert_source_integrity(job: CarouselCorrectionJobV1, package_root: Path) -> ArtifactReceipt:
    _, manifest_receipt, manifest, _ = _load_current_manifest(package_root)
    if manifest.episode_id != job.episode_id or manifest.revision != job.source_revision:
        raise CorrectionJobTransitionError("correction source is no longer the current revision")
    if manifest_receipt.sha256 != job.source_manifest_sha256:
        raise CorrectionJobTransitionError("correction source manifest receipt changed")
    page_receipts = {page.page_id: page.image.sha256 for page in manifest.pages}
    requested = [*job.feedback_items, *job.copy_edits, *job.text_layout_overrides]
    if job.layout_overrides is not None:
        requested.append(job.layout_overrides)
    if job.quote_layout_overrides is not None:
        requested.append(job.quote_layout_overrides)
    for item in requested:
        if page_receipts.get(item.page_id) != item.artifact_sha256:
            raise CorrectionJobTransitionError(
                f"correction source page receipt changed: {item.page_id}"
            )
    return manifest_receipt


def correction_jobs_dir(package_root: Path) -> Path:
    return package_root / "correction_jobs"


def correction_job_path(package_root: Path, job_id: str) -> Path:
    if not job_id.startswith("cj-") or len(job_id) != 35:
        raise ValueError("invalid correction job ID")
    try:
        int(job_id[3:], 16)
    except ValueError as error:
        raise ValueError("invalid correction job ID") from error
    return correction_jobs_dir(package_root) / f"{job_id}.json"


def load_job(path: Path) -> CarouselCorrectionJobV1:
    if not path.is_file():
        raise FileNotFoundError(f"correction job not found: {path}")
    return CarouselCorrectionJobV1.model_validate_json(path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, job: CarouselCorrectionJobV1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        pending.write_text(job.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


@contextmanager
def _job_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise CorrectionJobTransitionError("correction job is already being mutated") from error
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode())
        yield
    finally:
        os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def create_queued_job(
    *,
    package_root: Path,
    episode_id: str,
    source_revision: str,
    source_manifest_sha256: str,
    feedback_items: list[CarouselCorrectionItem] | None = None,
    copy_edits: list[CarouselCopyEdit] | None = None,
    layout_overrides: CarouselCoverLayoutEdit | None = None,
    quote_layout_overrides: CarouselQuoteLayoutEdit | None = None,
    text_layout_overrides: list[CarouselTextLayoutEdit] | None = None,
    now: datetime | None = None,
    job_id: str | None = None,
) -> CarouselCorrectionJobV1:
    timestamp = now or datetime.now(UTC)
    identifier = job_id or f"cj-{uuid4().hex}"
    job = CarouselCorrectionJobV1(
        job_id=identifier,
        episode_id=episode_id,
        source_revision=source_revision,
        source_manifest_sha256=source_manifest_sha256,
        feedback_items=feedback_items or [],
        copy_edits=copy_edits or [],
        layout_overrides=layout_overrides,
        quote_layout_overrides=quote_layout_overrides,
        text_layout_overrides=text_layout_overrides or [],
        created_at=timestamp,
        updated_at=timestamp,
    )
    directory = correction_jobs_dir(package_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = correction_job_path(package_root, identifier)
    with _job_lock(directory / ".create"):
        # 「進行中」要看**租約還在不在**，不能只看 status。認領之後行程死掉、
        # 租約過期、或 `.lock` 卡住時，工作會永遠停在 claimed／in_progress，
        # 而 fail_job 自己也要驗租約——於是那個 revision 從此送不出任何新修改，
        # 使用者在 Review Gate 上沒有任何控制項能解開（2026-09-03 review 抓到）。
        # 租約過期的認領本來就允許被別人接手，這裡採同一個判準。
        now = timestamp
        active = [
            existing
            for existing in list_jobs(package_root)
            if existing.source_revision == source_revision
            and existing.source_manifest_sha256 == source_manifest_sha256
            and existing.status in {"queued", "claimed", "in_progress"}
            and (
                existing.status == "queued"
                or existing.claim is None
                or now < existing.claim.lease_expires_at
            )
        ]
        if active:
            raise CorrectionJobTransitionError(
                "an active correction job already exists for this carousel revision"
            )
        if path.exists():
            raise FileExistsError(f"correction job already exists: {identifier}")
        _atomic_write(path, job)
    return job


def list_jobs(package_root: Path) -> list[CarouselCorrectionJobV1]:
    directory = correction_jobs_dir(package_root)
    if not directory.is_dir():
        return []
    jobs = [load_job(path) for path in directory.glob("cj-*.json")]
    return sorted(jobs, key=lambda job: (job.created_at, job.job_id))


def _assert_claim(
    job: CarouselCorrectionJobV1,
    claim_token: str,
    timestamp: datetime,
) -> None:
    if job.claim is None or job.claim.claim_token != claim_token:
        raise CorrectionJobTransitionError("correction job claim token mismatch")
    if timestamp >= job.claim.lease_expires_at:
        raise CorrectionJobTransitionError("correction job claim lease has expired")


def claim_job(
    path: Path,
    *,
    executor: str,
    executor_id: str,
    claim_token: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> CarouselCorrectionJobV1:
    with _job_lock(path):
        job = load_job(path)
        timestamp = now or datetime.now(UTC)
        source_manifest_receipt = _assert_source_integrity(job, path.parent.parent)
        if job.status in {"claimed", "in_progress"}:
            if job.claim is None or timestamp < job.claim.lease_expires_at:
                raise CorrectionJobTransitionError(
                    "cannot claim correction job while its lease is still active"
                )
        elif job.status != "queued":
            raise CorrectionJobTransitionError(f"cannot claim correction job from {job.status}")
        updated = job.model_copy(
            update={
                "status": "in_progress" if job.progress else "claimed",
                "updated_at": timestamp,
                "claim": CarouselCorrectionClaim(
                    executor=executor,
                    executor_id=executor_id,
                    claim_token=claim_token or f"claim-{uuid4().hex}",
                    claimed_at=timestamp,
                    lease_seconds=lease_seconds,
                    lease_expires_at=timestamp + timedelta(seconds=lease_seconds),
                ),
                "source_manifest_receipt": source_manifest_receipt,
            }
        )
        updated = CarouselCorrectionJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def progress_job(
    path: Path,
    *,
    claim_token: str,
    step: str,
    progress_percent: int,
    message: str = "",
    now: datetime | None = None,
) -> CarouselCorrectionJobV1:
    with _job_lock(path):
        job = load_job(path)
        if job.status not in {"claimed", "in_progress"}:
            raise CorrectionJobTransitionError(f"cannot record progress from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        if job.progress and progress_percent < job.progress[-1].progress_percent:
            raise CorrectionJobTransitionError("correction progress percent cannot decrease")
        assert job.claim is not None
        progress = [
            *job.progress,
            CarouselCorrectionProgress(
                sequence=len(job.progress) + 1,
                step=step,
                progress_percent=progress_percent,
                message=message,
                recorded_at=timestamp,
            ),
        ]
        renewed_claim = job.claim.model_copy(
            update={
                "lease_expires_at": timestamp + timedelta(seconds=job.claim.lease_seconds),
            }
        )
        updated = job.model_copy(
            update={
                "status": "in_progress",
                "updated_at": timestamp,
                "claim": renewed_claim,
                "progress": progress,
            }
        )
        updated = CarouselCorrectionJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def _load_claimed_source_spec(
    job: CarouselCorrectionJobV1,
    package_root: Path,
) -> PodcastCarouselCopySpecV1:
    if job.source_manifest_receipt is None:
        raise CorrectionJobTransitionError("correction claim has no verified source receipt")
    _, manifest_payload, _ = _read_artifact_once(
        Path(job.source_manifest_receipt.path),
        package_root,
        expected=job.source_manifest_receipt,
        changed_message="claimed source manifest was changed after claim",
    )
    if job.source_manifest_receipt.sha256 != job.source_manifest_sha256:
        raise CorrectionJobTransitionError("claimed source manifest does not match queued source")
    try:
        manifest = CarouselReviewManifestV1.model_validate_json(manifest_payload)
    except (OSError, UnicodeDecodeError, ValidationError) as error:
        raise CorrectionJobTransitionError("claimed source manifest is invalid") from error
    if manifest.episode_id != job.episode_id or manifest.revision != job.source_revision:
        raise CorrectionJobTransitionError("claimed source manifest identity changed")
    return _verify_manifest_artifacts(manifest, package_root=package_root)


def _load_claimed_source_manifest(
    job: CarouselCorrectionJobV1, package_root: Path
) -> CarouselReviewManifestV1:
    if job.source_manifest_receipt is None:
        raise CorrectionJobTransitionError("correction claim has no verified source receipt")
    _, manifest_payload, _ = _read_artifact_once(
        Path(job.source_manifest_receipt.path),
        package_root,
        expected=job.source_manifest_receipt,
        changed_message="claimed source manifest was changed after claim",
    )
    if job.source_manifest_receipt.sha256 != job.source_manifest_sha256:
        raise CorrectionJobTransitionError("claimed source manifest does not match queued source")
    try:
        manifest = CarouselReviewManifestV1.model_validate_json(manifest_payload)
    except ValidationError as error:
        raise CorrectionJobTransitionError("claimed source manifest is invalid") from error
    if manifest.episode_id != job.episode_id or manifest.revision != job.source_revision:
        raise CorrectionJobTransitionError("claimed source manifest identity changed")
    return manifest


def _assert_affected_pages_rerendered(
    job: CarouselCorrectionJobV1,
    *,
    source_manifest: CarouselReviewManifestV1,
    result_manifest: CarouselReviewManifestV1,
    result_spec: PodcastCarouselCopySpecV1,
    package_root: Path,
) -> None:
    affected = {item.page_id for item in job.feedback_items}
    affected.update(item.page_id for item in job.copy_edits)
    affected.update(item.page_id for item in job.text_layout_overrides)
    if job.layout_overrides is not None:
        affected.add("cover")
    if job.quote_layout_overrides is not None:
        affected.add(job.quote_layout_overrides.page_id)
    if not affected:
        return
    if source_manifest.render_input is None or result_manifest.render_input is None:
        raise CorrectionJobTransitionError(
            "structured edits require canonical render input receipts"
        )
    if source_manifest.render_input.sha256 == result_manifest.render_input.sha256:
        raise CorrectionJobTransitionError("structured edits cannot reuse the source render input")
    source_pages = {page.page_id: page for page in source_manifest.pages}
    result_pages = {page.page_id: page for page in result_manifest.pages}
    result_indexes = {page.page_id: index for index, page in enumerate(result_spec.pages)}
    cutouts_dir = package_root.parent / "packaging" / "cutouts"
    rerendered_hashes = _trusted_rerender_hashes(
        result_spec=result_spec,
        source_template=source_manifest.template,
        result_manifest=result_manifest,
        package_root=package_root,
        page_ids=affected,
    )
    for page_id in affected:
        source = source_pages.get(page_id)
        result = result_pages.get(page_id)
        if source is None or result is None:
            raise CorrectionJobTransitionError(f"affected carousel page is missing: {page_id}")
        # `fit` 是算圖端對版面的判定（重疊量、字級、保護區碰撞），不是「agent 有沒有
        # 照做」。修修 2026-09-02：「跟文字一樣，我送出去的就是 override 全部的規則。
        # 機器看不到我看到的東西。」他在編輯器裡看到的預覽就是同一份算圖 DOM，
        # 重疊多少他自己看得見；那是編輯決定。
        #
        # 前門（Review Gate 的送出）已經改成只提示不擋，這裡不能還留一道後門否決——
        # 否則他送得出去、卻永遠完成不了（實測 r003 就卡在 cover 的
        # 「重疊 267px 超過 240px」）。
        #
        # 下面那幾項照驗：內容雜湊要等於用結果 spec 重算的值、圖片與內容雜湊都必須
        # 真的變了、render input 不可重用。那些才是「agent 有沒有照做」的證據。
        # `fit.status` 仍然存在 manifest 裡，Review Gate 會照常標示「版面需要調整」。
        if result.content_sha256 == source.content_sha256:
            raise CorrectionJobTransitionError(
                f"affected carousel content hash was reused: {page_id}"
            )
        expected_content_sha = _content_sha(
            result_spec,
            result_indexes[page_id],
            result_manifest.template.sha256,
            cutouts_dir,
        )
        if result.content_sha256 != expected_content_sha:
            raise CorrectionJobTransitionError(
                f"affected carousel canonical content hash is invalid: {page_id}"
            )
        if result.image.sha256 == source.image.sha256:
            raise CorrectionJobTransitionError(f"affected carousel image was reused: {page_id}")
        if result.image.sha256 != rerendered_hashes.get(page_id):
            raise CorrectionJobTransitionError(
                f"affected carousel PNG does not match deterministic rerender: {page_id}"
            )


def _trusted_rerender_hashes(
    *,
    result_spec: PodcastCarouselCopySpecV1,
    source_template: TemplateSnapshot,
    result_manifest: CarouselReviewManifestV1,
    package_root: Path,
    page_ids: set[str],
) -> dict[str, str]:
    """Rebuild trusted render input and rerender affected pages before completion."""

    try:
        if result_manifest.template != source_template:
            raise CorrectionJobTransitionError(
                "result template identity does not match correction source"
            )
        template_root = Path(result_manifest.template.root).resolve(strict=True)
        template_root.relative_to(package_root.resolve(strict=True))
        files = [
            (path.relative_to(template_root).as_posix(), path)
            for path in template_root.rglob("*")
            if path.is_file()
        ]
        if _digest_files(files) != result_manifest.template.sha256:
            raise CorrectionJobTransitionError("result template snapshot tree changed")
        if result_manifest.render_input is None:
            raise CorrectionJobTransitionError("result manifest has no render input receipt")
        cutouts_dir = package_root.parent / "packaging" / "cutouts"
        with tempfile.TemporaryDirectory(prefix="carousel-attest-", dir=package_root) as temp_value:
            temp_root = Path(temp_value)
            trusted_render_input = temp_root / "render_input.html"
            _write_render_input(
                snapshot=result_manifest.template,
                spec=result_spec,
                cutouts_dir=cutouts_dir,
                destination=trusted_render_input,
            )
            trusted_receipt = receipt_for(trusted_render_input)
            if (
                trusted_receipt.bytes != result_manifest.render_input.bytes
                or trusted_receipt.sha256 != result_manifest.render_input.sha256
            ):
                raise CorrectionJobTransitionError(
                    "result render input does not match deterministic reconstruction"
                )
            indexes = {page.page_id: index for index, page in enumerate(result_spec.pages)}
            hashes: dict[str, str] = {}
            for page_id in page_ids:
                index = indexes[page_id]
                screenshot = temp_root / f"{index + 1:02d}.png"
                _render_page(
                    chrome=_DEFAULT_CHROME,
                    url=f"{trusted_render_input.resolve().as_uri()}?page={index}",
                    screenshot=screenshot,
                )
                hashes[page_id] = receipt_for(screenshot).sha256
            return hashes
    except CorrectionJobTransitionError:
        raise
    except (FileNotFoundError, KeyError, OSError, ValueError) as error:
        raise CorrectionJobTransitionError(
            "deterministic carousel rerender attestation failed"
        ) from error


def _assert_structured_edits_applied(
    job: CarouselCorrectionJobV1,
    *,
    source_spec: PodcastCarouselCopySpecV1,
    result_spec: PodcastCarouselCopySpecV1,
) -> None:
    # 這個守衛漏掉 `quote_layout_overrides` 兩個月都沒被發現：只調金句幾何的單
    # 會整個跳過 exact diff，於是結果 spec 可以夾帶任何一張卡的文案改動而完成
    # （2026-09-03 review 抓到）。exact diff 是「沿用 panel、不重跑三個 lens」的
    # 唯一授權依據——只要有任何結構化編輯，它就必須跑。
    if not (
        job.copy_edits
        or job.layout_overrides is not None
        or job.quote_layout_overrides is not None
        or job.text_layout_overrides
    ):
        return
    expected = source_spec.model_dump(mode="json")
    expected["revision"] = result_spec.revision
    # 出處紀錄不是內容。修修在 Review Gate 指定的外部事實（職稱、公司…）逐字稿裡
    # 沒有背書，panel 會（正確地）要求記錄來源——但把來源寫進 spec 本身就是一個
    # 「沒有被請求的欄位變動」，於是 exact-diff 擋下整張單。兩條規則互相卡死，
    # 結果是**只要修修指定了一個逐字稿沒有的事實，那張單就永遠完成不了**
    # （2026-09-02 實際卡住 cj-03dc7ba2）。這個欄位只指向出處註記，不影響任何
    # 一張卡片的可見內容，所以允許它變動。
    expected["editorial_direction_path"] = result_spec.editorial_direction_path
    # 同理：panel 繼承宣告是稽核欄位，不是卡片內容。它宣稱「AI 那半邊沒變」，
    # 而下面這個 exact-diff 就是那句宣稱的證明本身。
    expected["panel_inherited_from"] = result_spec.panel_inherited_from
    pages = {page["page_id"]: page for page in expected["pages"]}
    for edit in job.copy_edits:
        page = pages.get(edit.page_id)
        if page is None or page["role"] != edit.role:
            raise CorrectionJobTransitionError(
                f"structured edit page identity changed: {edit.page_id}"
            )
        page.update(edit.fields)
    if job.layout_overrides is not None:
        expected["layout_overrides"]["cover"] = job.layout_overrides.values.model_dump(mode="json")
    if job.quote_layout_overrides is not None:
        expected["layout_overrides"]["quote"] = job.quote_layout_overrides.values.model_dump(
            mode="json"
        )
    text_layouts = {
        (item["page_id"], item["region"]): item
        for item in expected["layout_overrides"].get("text_regions", [])
    }
    for edit in job.text_layout_overrides:
        stored = PageTextLayoutOverrideV1(
            page_id=edit.page_id,
            role=edit.role,
            region=edit.region,
            values=edit.values,
        )
        text_layouts[(edit.page_id, edit.region)] = stored.model_dump(mode="json")
    expected["layout_overrides"]["text_regions"] = list(text_layouts.values())
    try:
        expected_spec = PodcastCarouselCopySpecV1.model_validate(expected)
    except ValidationError as error:
        raise CorrectionJobTransitionError(
            "structured edits do not produce a valid Copy Spec"
        ) from error
    if result_spec != expected_spec:
        raise CorrectionJobTransitionError(
            "result Copy Spec does not exactly apply the requested structured edits"
        )


def _verify_inherited_panel_completion(
    *,
    package_root: Path,
    source_revision: str,
    spec: PodcastCarouselCopySpecV1,
    manifest: CarouselReviewManifestV1,
    manifest_receipt: ArtifactReceipt,
) -> tuple[str, CarouselCorrectionCompletionEvidence]:
    """人類專屬修改的收尾：沿用來源版本已收斂的 panel 與它的三份審查收據。

    這裡**不放寬任何檢查**——來源那份 panel 仍然必須是 converged、仍然必須三個 lens
    齊備，而且必須真的能治理這一版（`assert_panel_renderable` 會比對繼承宣告）。
    省下來的只有「再跑一次會得到同樣結果的那三個 agent」。
    """
    panel_path = package_root / "editorial" / source_revision / "panel_result.v1.json"
    if not panel_path.is_file():
        raise CorrectionJobTransitionError(
            f"inherited panel not found for {source_revision}: {panel_path.name}"
        )
    resolved_panel_path, panel_receipt = _verified_receipt(panel_path, package_root)
    try:
        panel = PanelResult.model_validate_json(resolved_panel_path.read_text(encoding="utf-8"))
        assert_panel_renderable(panel, spec=spec)
    except (OSError, UnicodeDecodeError, ValidationError, ValueError, RuntimeError) as error:
        raise CorrectionJobTransitionError(
            "inherited carousel panel has not validly converged"
        ) from error

    reviewers = tuple(
        CarouselReviewerReceipt(
            lens=lens,
            reviewer_id=f"inherited:{source_revision}:{lens}",
            review=panel_receipt,
        )
        for lens in CAROUSEL_REQUIRED_REVIEWS
    )
    evidence = CarouselCorrectionCompletionEvidence(
        result_manifest=manifest_receipt,
        panel_result=panel_receipt,
        inherited_from=source_revision,
        reviewers=reviewers,
    )
    return manifest.revision, evidence


def _verify_completion_evidence(
    *,
    job: CarouselCorrectionJobV1,
    package_root: Path,
    result_manifest_path: Path,
    panel_result_path: Path | None,
    reviewer_artifacts: list[tuple[str, str, Path]],
) -> tuple[str, CarouselCorrectionCompletionEvidence]:
    source_manifest = _load_claimed_source_manifest(job, package_root)
    source_spec = _verify_manifest_artifacts(source_manifest, package_root=package_root)
    current_manifest_path, manifest_receipt, manifest, spec = _load_current_manifest(
        package_root,
        require_render_input=True,
    )
    explicit_manifest = _contained_file(result_manifest_path, package_root)
    if explicit_manifest != current_manifest_path:
        raise CorrectionJobTransitionError("result manifest is not the current carousel manifest")
    if manifest.episode_id != job.episode_id:
        raise CorrectionJobTransitionError("result manifest episode does not match correction job")
    if int(manifest.revision[1:]) <= int(job.source_revision[1:]):
        raise CorrectionJobTransitionError("result revision is not newer than correction source")
    if manifest.template != source_manifest.template:
        raise CorrectionJobTransitionError(
            "result template identity does not match correction source"
        )
    _assert_structured_edits_applied(job, source_spec=source_spec, result_spec=spec)
    _assert_affected_pages_rerendered(
        job,
        source_manifest=source_manifest,
        result_manifest=manifest,
        result_spec=spec,
        package_root=package_root,
    )

    # 修修 2026-09-02：「Agent review 審的是 AI 的生成內容，人類 review 之後的成果
    # 根本不應該再觸發這個 review。」上面的 exact-diff 已經證明結果 = 來源 + 他明確
    # 要求的欄位，AI 那半邊一個位元組都沒動；既然如此，重跑三個 lens 只會得到同一個
    # 答案。這種單子沿用來源版本的 panel，不需要新的審查產物。
    inherits_panel = spec.panel_inherited_from is not None
    if inherits_panel:
        # 沿用 panel 的授權來自上面那道 exact diff——它證明 AI 生成的那半邊
        # 一個位元組都沒動。**自由文字的修改意見沒有這個保證**：agent 是照著
        # 意圖重寫文案，那正是三個 lens 存在的理由。含 feedback 的單如果也能
        # 宣告沿用，就等於自己簽自己的審查（2026-09-03 review 抓到）。
        if job.feedback_items:
            raise CorrectionJobTransitionError(
                "a feedback-driven correction cannot inherit the source panel"
            )
        if spec.panel_inherited_from != job.source_revision:
            raise CorrectionJobTransitionError(
                "inherited panel must come from this correction job's source revision"
            )
        if reviewer_artifacts:
            raise CorrectionJobTransitionError(
                "an inherited-panel completion does not take reviewer artifacts"
            )
        return _verify_inherited_panel_completion(
            package_root=package_root,
            source_revision=job.source_revision,
            spec=spec,
            manifest=manifest,
            manifest_receipt=manifest_receipt,
        )

    supplied_lenses = [lens for lens, _, _ in reviewer_artifacts]
    if set(supplied_lenses) != set(CAROUSEL_REQUIRED_REVIEWS) or len(supplied_lenses) != len(
        set(supplied_lenses)
    ):
        raise CorrectionJobTransitionError(
            "completion requires exactly one artifact for each canonical reviewer lens"
        )
    reviewer_ids = [reviewer_id for _, reviewer_id, _ in reviewer_artifacts]
    if len(reviewer_ids) != len(set(reviewer_ids)):
        raise CorrectionJobTransitionError("completion reviewer identities must be unique")

    parsed_reviews: dict[str, PanelReview] = {}
    reviewer_receipts: dict[str, CarouselReviewerReceipt] = {}
    review_paths: set[Path] = set()
    for lens, reviewer_id, review_path_value in reviewer_artifacts:
        review_path, review_receipt = _verified_receipt(review_path_value, package_root)
        if review_path in review_paths:
            raise CorrectionJobTransitionError("completion reviewer artifacts must be distinct")
        review_paths.add(review_path)
        try:
            review = PanelReview.model_validate_json(review_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValidationError) as error:
            raise CorrectionJobTransitionError(f"reviewer artifact is invalid: {lens}") from error
        if review.lens != lens:
            raise CorrectionJobTransitionError(f"reviewer artifact lens mismatch: {lens}")
        parsed_reviews[lens] = review
        reviewer_receipts[lens] = CarouselReviewerReceipt(
            lens=lens,
            reviewer_id=reviewer_id,
            review=review_receipt,
        )

    if panel_result_path is None:
        raise CorrectionJobTransitionError(
            "completion requires --panel-result unless the result declares panel_inherited_from"
        )
    panel_path, panel_receipt = _verified_receipt(panel_result_path, package_root)
    if panel_path in review_paths:
        raise CorrectionJobTransitionError("panel result must be distinct from reviewer artifacts")
    try:
        panel = PanelResult.model_validate_json(panel_path.read_text(encoding="utf-8"))
        assert_panel_renderable(panel, spec=spec)
    except (OSError, UnicodeDecodeError, ValidationError, ValueError, RuntimeError) as error:
        raise CorrectionJobTransitionError("carousel panel has not validly converged") from error
    for lens in CAROUSEL_REQUIRED_REVIEWS:
        if panel.reviews[lens] != parsed_reviews[lens]:
            raise CorrectionJobTransitionError(f"panel review does not match receipt: {lens}")

    evidence = CarouselCorrectionCompletionEvidence(
        result_manifest=manifest_receipt,
        panel_result=panel_receipt,
        reviewers=tuple(reviewer_receipts[lens] for lens in CAROUSEL_REQUIRED_REVIEWS),
    )
    return manifest.revision, evidence


def complete_job(
    path: Path,
    *,
    claim_token: str,
    result_manifest_path: Path,
    panel_result_path: Path | None,
    reviewer_artifacts: list[tuple[str, str, Path]],
    now: datetime | None = None,
) -> CarouselCorrectionJobV1:
    with _job_lock(path):
        job = load_job(path)
        if job.status != "in_progress":
            raise CorrectionJobTransitionError(f"cannot complete correction job from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        result_revision, completion_evidence = _verify_completion_evidence(
            job=job,
            package_root=path.parent.parent,
            result_manifest_path=result_manifest_path,
            panel_result_path=panel_result_path,
            reviewer_artifacts=reviewer_artifacts,
        )
        updated = job.model_copy(
            update={
                "status": "completed",
                "updated_at": timestamp,
                "result_revision": result_revision,
                "completion_evidence": completion_evidence,
            }
        )
        updated = CarouselCorrectionJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def fail_job(
    path: Path,
    *,
    claim_token: str,
    error: str,
    now: datetime | None = None,
) -> CarouselCorrectionJobV1:
    with _job_lock(path):
        job = load_job(path)
        if job.status not in {"claimed", "in_progress"}:
            raise CorrectionJobTransitionError(f"cannot fail correction job from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        updated = job.model_copy(
            update={
                "status": "failed",
                "updated_at": timestamp,
                "error": error,
            }
        )
        updated = CarouselCorrectionJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def _reviewer_artifact_arg(value: str) -> tuple[str, str, Path]:
    parts = value.split("=", 2)
    if len(parts) != 3 or not all(part.strip() for part in parts):
        raise argparse.ArgumentTypeError("reviewer receipt must use LENS=REVIEWER_ID=PATH format")
    return parts[0], parts[1], Path(parts[2])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    claim = commands.add_parser("claim")
    claim.add_argument("job", type=Path)
    claim.add_argument("--executor", choices=("codex", "claude_code"), required=True)
    claim.add_argument("--executor-id", required=True)
    claim.add_argument("--claim-token")
    claim.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS)

    progress = commands.add_parser("progress")
    progress.add_argument("job", type=Path)
    progress.add_argument("--claim-token", required=True)
    progress.add_argument("--step", required=True)
    progress.add_argument("--percent", type=int, required=True)
    progress.add_argument("--message", default="")

    complete = commands.add_parser("complete")
    complete.add_argument("job", type=Path)
    complete.add_argument("--claim-token", required=True)
    complete.add_argument("--result-manifest", type=Path, required=True)
    # 結果版本若宣告 `panel_inherited_from`（純人類指定欄位的修改），來源版本的
    # panel 就是治理它的那一份，不需要另外提供審查產物。其餘情況一律必填。
    complete.add_argument("--panel-result", type=Path)
    complete.add_argument(
        "--reviewer-receipt",
        action="append",
        type=_reviewer_artifact_arg,
        default=[],
        metavar="LENS=REVIEWER_ID=PATH",
    )

    fail = commands.add_parser("fail")
    fail.add_argument("job", type=Path)
    fail.add_argument("--claim-token", required=True)
    fail.add_argument("--error", required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "claim":
        job = claim_job(
            args.job,
            executor=args.executor,
            executor_id=args.executor_id,
            claim_token=args.claim_token,
            lease_seconds=args.lease_seconds,
        )
    elif args.command == "progress":
        job = progress_job(
            args.job,
            claim_token=args.claim_token,
            step=args.step,
            progress_percent=args.percent,
            message=args.message,
        )
    elif args.command == "complete":
        job = complete_job(
            args.job,
            claim_token=args.claim_token,
            result_manifest_path=args.result_manifest,
            panel_result_path=args.panel_result,
            reviewer_artifacts=args.reviewer_receipt,
        )
    else:
        job = fail_job(args.job, claim_token=args.claim_token, error=args.error)
    print(json.dumps(job.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
