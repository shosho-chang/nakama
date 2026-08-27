#!/usr/bin/env python3
"""packaging_manifest.py — packaging 段的 resume 帳本（ADR-054 D14）。

podcast-pipeline 的 packaging 末段逐支影片跑 titles → thumbnails → emitted 三個
stage；本 script 是唯一讀寫 `<packaging_dir>/manifest.json` 的機械層——skill 不
手改 JSON。中斷重跑時 `status` 告訴編排者哪支該續（已完成的 stage 不重生）。

用法：
    python scripts/packaging_manifest.py status "G:/footages/<ep>/packaging"
    python scripts/packaging_manifest.py mark   "G:/footages/<ep>/packaging" \
        --cut punch-L5 --stage titles

規則：
- stage 依序 titles → thumbnails → emitted；mark 冪等（重標同 stage 無害）
- 跳序 mark（titles 未完成就標 thumbnails）→ 直接報錯 — 停段不跳段（D14）
- manifest 壞損（非法 JSON）→ fail loud 附修復指引，不靜默重建
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

STAGES = ("titles", "thumbnails", "emitted")
WORK_STATUSES = ("queued", "running", "ready", "failed")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _load(packaging_dir: Path) -> dict:
    path = packaging_dir / "manifest.json"
    if not path.is_file():
        return {"cuts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(
            f"manifest.json 壞損（{exc}）— 不自動重建以免已完成進度蒸發。"
            f"人工檢查 {path}，確認各 cut 實際產物後修正或移除該檔再重跑。"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("cuts"), dict):
        raise SystemExit("manifest.json 壞損（cuts 必須是物件）— 不自動重建。")
    if any(
        not isinstance(cut_id, str) or not isinstance(row, dict)
        for cut_id, row in data["cuts"].items()
    ):
        raise SystemExit("manifest.json 壞損（每個 cut 必須是具名物件）— 不自動重建。")
    return data


def load_manifest(packaging_dir: Path) -> dict:
    """Public validated reader for consumers that render manifest progress."""
    return _load(packaging_dir)


def _save(packaging_dir: Path, data: dict) -> None:
    path = packaging_dir / "manifest.json"
    packaging_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=packaging_dir,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        json.dump(data, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
    os.replace(temporary.name, path)


def stage_parallel_jobs(packaging_dir: Path, jobs: list[dict]) -> dict:
    """Atomically register approved Long Highlight work without marking a stage done.

    This keeps ``manifest.json`` writes inside this mechanical layer while allowing
    Bridge to expose queued video/Packaging branches before title assets exist.
    Existing stage timestamps and non-queued worker states survive a repeated gate
    decision, so re-approval never rewinds completed work.
    """
    data = _load(packaging_dir)
    cuts = data["cuts"]
    cut_ids: set[str] = set()
    ranks: set[int] = set()
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("parallel job must be an object")
        cut_id = job.get("cut_id")
        rank = job.get("rank")
        if not isinstance(cut_id, str) or not cut_id or cut_id in cut_ids:
            raise ValueError(f"parallel job cut_id must be unique: {cut_id!r}")
        if not isinstance(rank, int) or not 1 <= rank <= 3 or rank in ranks:
            raise ValueError(f"parallel job rank must be unique and within 1..3: {rank!r}")
        cut_ids.add(cut_id)
        ranks.add(rank)
        for branch in ("video", "packaging"):
            branch_work = job.get(branch)
            if not isinstance(branch_work, dict):
                raise ValueError(f"{cut_id} {branch} must be an object")
            status = branch_work.get("status")
            if status not in WORK_STATUSES:
                raise ValueError(f"{cut_id} {branch}.status is invalid: {status!r}")

    for job in jobs:
        cut_id = job["cut_id"]
        current = dict(cuts.get(cut_id) or {})
        current_video = current.get("video") if isinstance(current.get("video"), dict) else {}
        current_packaging = (
            current.get("packaging") if isinstance(current.get("packaging"), dict) else {}
        )
        video_status = current_video.get("status", job["video"]["status"])
        if "emitted" in current:
            packaging_status = "ready"
        elif "titles" in current or "thumbnails" in current:
            packaging_status = "running"
        else:
            packaging_status = current_packaging.get("status", job["packaging"]["status"])
        cuts[cut_id] = {
            **current,
            "rank": job["rank"],
            "title": job.get("title", cut_id),
            "selected_at": current.get("selected_at", job.get("selected_at")),
            "video": {**current_video, "status": video_status},
            "packaging": {**current_packaging, "status": packaging_status},
        }
    _save(packaging_dir, data)
    return data


def claim_packaging_job(
    packaging_dir: Path,
    cut_id: str,
    *,
    worker_id: str,
    worker_host: str | None = None,
    worker_pid: int | None = None,
    resume_existing: bool = False,
) -> dict:
    """Durably claim or resume one initial Packaging job.

    ``running`` is deliberately reclaimable: the desktop watcher is a single
    supervised process, so a leftover running state means the previous process
    stopped before writing a terminal state.  The attempt counter exposes that
    resume and the agent is instructed to continue from actual artifacts.
    """
    data = _load(packaging_dir)
    cut = data["cuts"].get(cut_id)
    if not isinstance(cut, dict):
        raise ValueError(f"unknown packaging cut: {cut_id}")
    branch = cut.get("packaging")
    if not isinstance(branch, dict):
        raise ValueError(f"{cut_id} packaging work is missing")
    current_status = branch.get("status")
    if current_status not in {"queued", "running"}:
        raise ValueError(f"{cut_id} packaging work is not claimable: {current_status!r}")
    if current_status == "running" and not resume_existing:
        raise ValueError(f"{cut_id} packaging work is already running")
    now = datetime.now(timezone.utc).isoformat()
    branch.update(
        {
            "status": "running",
            "attempt": int(branch.get("attempt") or 0) + 1,
            "worker_id": worker_id,
            "worker_host": worker_host,
            "worker_pid": worker_pid,
            "started_at": branch.get("started_at") or now,
            "last_started_at": now,
            "finished_at": None,
            "error": None,
        }
    )
    cut["packaging"] = branch
    _save(packaging_dir, data)
    return dict(branch)


def finish_packaging_job(
    packaging_dir: Path,
    cut_id: str,
    *,
    succeeded: bool,
    error: str | None = None,
) -> dict:
    """Write the terminal state for a claimed initial Packaging job."""
    data = _load(packaging_dir)
    cut = data["cuts"].get(cut_id)
    if not isinstance(cut, dict):
        raise ValueError(f"unknown packaging cut: {cut_id}")
    branch = cut.get("packaging")
    if not isinstance(branch, dict) or branch.get("status") != "running":
        raise ValueError(f"{cut_id} packaging work is not running")
    now = datetime.now(timezone.utc).isoformat()
    if succeeded:
        # These timestamps mean the final output validator proved the complete
        # title → thumbnail → emitted chain.  Partial agent output is never marked.
        for stage in STAGES:
            cut.setdefault(stage, now)
        branch.update({"status": "ready", "finished_at": now, "error": None})
    else:
        branch.update(
            {
                "status": "failed",
                "finished_at": now,
                "error": str(error or "Packaging worker failed")[-1000:],
            }
        )
    cut["packaging"] = branch
    _save(packaging_dir, data)
    return dict(branch)


def status(packaging_dir: Path) -> dict:
    data = _load(packaging_dir)
    out: dict = {"cuts": {}, "next": None}
    for cut_id, stages in data.get("cuts", {}).items():
        done = [s for s in STAGES if s in stages]
        pending = [s for s in STAGES if s not in stages]
        out["cuts"][cut_id] = {"done": done, "pending": pending}
        if pending and out["next"] is None:
            out["next"] = {"cut_id": cut_id, "stage": pending[0]}
    return out


def mark(packaging_dir: Path, cut_id: str, stage: str) -> dict:
    if stage not in STAGES:
        raise SystemExit(f"unknown stage: {stage!r}（合法：{', '.join(STAGES)}）")
    data = _load(packaging_dir)
    cut = data.setdefault("cuts", {}).setdefault(cut_id, {})
    idx = STAGES.index(stage)
    missing = [s for s in STAGES[:idx] if s not in cut]
    if missing:
        raise SystemExit(
            f"{cut_id} 的 {stage} 不能先標 — 前置 stage 未完成：{', '.join(missing)}"
            "（D14 停段不跳段；照序補跑前置再標）"
        )
    if stage not in cut:  # 冪等：已標過不覆寫時間戳
        cut[stage] = datetime.now(timezone.utc).isoformat()
    _save(packaging_dir, data)
    return {"cut_id": cut_id, "stage": stage, "at": cut[stage]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status")
    p_status.add_argument("packaging_dir", type=Path)

    p_mark = sub.add_parser("mark")
    p_mark.add_argument("packaging_dir", type=Path)
    p_mark.add_argument("--cut", required=True)
    p_mark.add_argument("--stage", required=True, choices=STAGES)

    args = parser.parse_args()
    args.packaging_dir.mkdir(parents=True, exist_ok=True)
    if args.cmd == "status":
        print(json.dumps(status(args.packaging_dir), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(mark(args.packaging_dir, args.cut, args.stage), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
