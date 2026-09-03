"""結構化修正單的自動執行：套用 → 出圖 → 沿用 panel → 完成。

修修 2026-09-03：「以後不能改成送出就自動驅動 Agent 去 render 嗎？多一個動作
覺得不好。」

ADR-063 原本明文規定「建立或輪詢工作不會派工或喚醒 Codex／Claude Code」，理由是
網頁端沒有執行者。但那條規則把兩種本質不同的工作混在一起了：

- **純結構化修改**（文字欄位、去背照、幾何、文字區塊）：套用它們**不需要任何
  判斷**——把值寫進 Copy Spec、重新出圖、沿用上一版已收斂的 panel、跑完驗收。
  整條路徑是決定性的，一步都用不到 LLM。手動跑三次（r003、r004、r005）之後，
  每一步都只是在複述修正單裡已經寫死的值。
- **自由文字的修改意見**：「這句太繞了幫我改順」沒辦法機械套用，需要 agent。

所以這支只接第一種。有任何 `feedback_items` 一律拒絕，留給 agent。

**這不是放寬驗收**：完成時仍然走 `complete_job`，該驗的一項不少——來源收據、
決定性重建、逐頁 PNG 比對、exact diff（結果必須等於來源加上修正單裡明確要求的
欄位，其餘一字未動）、以及沿用 panel 的 converged 檢查。自動化省掉的只有
「叫一個人來按下同樣那幾個指令」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from shared.schemas.podcast_carousel import (
    CarouselCorrectionJobV1,
    PodcastCarouselCopySpecV1,
)


class StructuredAutorunError(RuntimeError):
    """這張單不能自動執行，或執行途中壞掉。"""


@dataclass(frozen=True)
class AutorunResult:
    job: CarouselCorrectionJobV1
    result_revision: str
    changed_fields: int


def is_autorunnable(job: CarouselCorrectionJobV1) -> bool:
    """只有純結構化修改能自動跑。自由文字意見要 agent 讀懂它才動得了。"""
    if job.feedback_items:
        return False
    return bool(
        job.copy_edits
        or job.layout_overrides is not None
        or job.quote_layout_overrides is not None
        or job.text_layout_overrides
    )


def next_revision(current: str) -> str:
    return f"r{int(current[1:]) + 1:03d}"


def build_result_spec(
    *, source_spec: PodcastCarouselCopySpecV1, job: CarouselCorrectionJobV1
) -> tuple[dict, int]:
    """來源 spec ＋ 修正單裡**明確要求**的欄位，其餘一字不動。

    回傳 (spec dict, 變動欄位數)。變動數只用於進度訊息與稽核，不參與判斷——
    真正的把關是 `complete_job` 的 exact diff。
    """
    spec = json.loads(source_spec.model_dump_json())
    before = json.loads(json.dumps(spec))

    spec["revision"] = next_revision(source_spec.revision)
    # 宣告指向**來源版本**（完成驗收也是這樣比對）。沿用的 panel 本身可能是鏈上
    # 更早那一版——見 `assert_panel_renderable` 的說明。
    spec["panel_inherited_from"] = job.source_revision

    pages = {page["page_id"]: page for page in spec["pages"]}
    for edit in job.copy_edits:
        if edit.page_id not in pages:
            raise StructuredAutorunError(f"修正單指向不存在的卡片：{edit.page_id}")
        pages[edit.page_id].update(edit.fields)
    if job.layout_overrides is not None:
        spec["layout_overrides"]["cover"] = job.layout_overrides.values.model_dump(mode="json")
    if job.quote_layout_overrides is not None:
        spec["layout_overrides"]["quote"] = job.quote_layout_overrides.values.model_dump(
            mode="json"
        )
    regions = {
        (item["page_id"], item["region"]): item
        for item in spec["layout_overrides"].get("text_regions", [])
    }
    for edit in job.text_layout_overrides:
        regions[(edit.page_id, edit.region)] = {
            "page_id": edit.page_id,
            "role": edit.role,
            "region": edit.region,
            "values": edit.values.model_dump(mode="json"),
        }
    spec["layout_overrides"]["text_regions"] = list(regions.values())

    return spec, _changed_field_count(before, spec)


def _flatten(value, prefix: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _flatten(item, f"{prefix}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _flatten(item, f"{prefix}[{index}]")
    else:
        yield prefix, value


def _changed_field_count(before: dict, after: dict) -> int:
    left = dict(_flatten(before))
    right = dict(_flatten(after))
    keys = set(left) | set(right)
    sentinel = object()
    return sum(1 for key in keys if left.get(key, sentinel) != right.get(key, sentinel))


def execute_structured_job(
    *,
    episode_dir: Path,
    job_path: Path,
    executor_id: str,
    spec_destination: Path | None = None,
) -> AutorunResult:
    """認領 → 套用 → 出圖 → 完成。失敗時把工作標成 failed 並附上原因。

    失敗不是死路：Review Gate 看到 failed 會把草稿還給使用者，可以改完再送。
    """
    # 延後匯入：出圖鏈很重，而這支只有在真的要跑的時候才需要它。
    from scripts.podcast_carousel_correction_job import (
        CorrectionJobTransitionError,
        claim_job,
        complete_job,
        fail_job,
        load_job,
        progress_job,
    )

    episode_dir = Path(episode_dir)
    package_root = episode_dir / "ig-carousel"
    job = load_job(job_path)
    if not is_autorunnable(job):
        raise StructuredAutorunError("這張單含有自由文字的修改意見，需要 agent 讀懂之後才動得了")

    claim_token = uuid4().hex
    try:
        job = claim_job(
            job_path,
            executor="claude_code",
            executor_id=executor_id,
            claim_token=claim_token,
            # 出圖 + 完成驗收（每張受影響的卡都要重新啟動一次 Chrome 做決定性
            # 重建）可能遠超過預設的 30 分鐘租約。租約一過期，連 `fail_job`
            # 都會被自己的 `_assert_claim` 擋下——失敗就再也落不到工作上。
            lease_seconds=4 * 60 * 60,
        )
    except CorrectionJobTransitionError as error:
        raise StructuredAutorunError(f"無法認領：{error}") from error

    try:
        source_spec = _load_source_spec(package_root, job.source_revision)
        spec_payload, changed = build_result_spec(source_spec=source_spec, job=job)
        PodcastCarouselCopySpecV1.model_validate(spec_payload)
        # **不可以**寫進 `correction_jobs/`——`list_jobs` 用 `cj-*.json` 掃那個
        # 資料夾，一份 `cj-….copy_spec.v1.json` 會被當成工作去解析，整個
        # 建立／認領流程都會炸掉（2026-09-03 實際踩到）。
        destination = spec_destination or (
            package_root / "correction_jobs" / "specs" / f"{job.job_id}.copy_spec.v1.json"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(spec_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        progress_job(
            job_path,
            claim_token=claim_token,
            step="apply",
            progress_percent=30,
            message=f"套用結構化修改，建立 {spec_payload['revision']}（{changed} 欄變動）",
        )

        _render(episode_dir=episode_dir, copy_spec=destination)
        progress_job(
            job_path,
            claim_token=claim_token,
            step="render",
            progress_percent=80,
            message=f"{spec_payload['revision']} 出圖完成",
        )

        manifest_path = (
            package_root / "revisions" / spec_payload["revision"] / "review_manifest.v1.json"
        )
        job = complete_job(
            job_path,
            claim_token=claim_token,
            result_manifest_path=manifest_path,
            panel_result_path=None,  # spec 宣告 panel_inherited_from
            reviewer_artifacts=[],
        )
    except Exception as error:  # noqa: BLE001 — 任何失敗都要落到工作上，不能靜默
        try:
            fail_job(job_path, claim_token=claim_token, error=str(error)[:900])
        except CorrectionJobTransitionError as fail_error:
            # 標記失敗**也**可能失敗（租約過期／狀態已變）。吞掉它會讓工作停在
            # claimed，而那個 revision 就再也送不出新修改。至少要留下痕跡。
            raise StructuredAutorunError(
                f"{error}（另外：標記失敗時也失敗了——{fail_error}；"
                "該工作的租約過期後會自動不再擋住新的送出）"
            ) from error
        raise StructuredAutorunError(str(error)) from error

    return AutorunResult(job=job, result_revision=job.result_revision or "", changed_fields=changed)


def _load_source_spec(package_root: Path, revision: str) -> PodcastCarouselCopySpecV1:
    path = package_root / "revisions" / revision / "copy_spec.v1.json"
    if not path.is_file():
        raise StructuredAutorunError(f"找不到來源 Copy Spec：{path}")
    return PodcastCarouselCopySpecV1.model_validate_json(path.read_text(encoding="utf-8"))


def _render(*, episode_dir: Path, copy_spec: Path) -> None:
    import argparse

    from scripts.run_podcast_carousel import DEFAULT_TEMPLATE
    from scripts.run_podcast_carousel import run as run_carousel

    run_carousel(
        argparse.Namespace(
            episode_dir=episode_dir,
            copy_spec=copy_spec,
            panel_result=None,  # spec 宣告了 panel_inherited_from
            template_dir=DEFAULT_TEMPLATE,
            force=True,  # 產生新 revision，既有版本不動
        )
    )
