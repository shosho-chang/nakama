"""One fixture builder for the plan record that ADR-069 made the durable record.

以前每一支測試都手寫一次 `_seal_finished_cut_release(...)` 的二十個欄位，其中十幾個
跟那支測試要驗的事情無關。這裡把無關的欄位收成預設值：測試只寫它真正在乎的那幾格，
讀的人也就看得出那幾格才是重點。
"""

from __future__ import annotations

from agents.brook.script_video.finished_cut_production._plan_record import (
    PlanRecord,
    PlanTimeline,
)
from agents.brook.script_video.finished_cut_production._records import (
    EventRecord,
    ProjectedComponent,
    ReleaseArtifact,
)

FIXTURE_ARTIFACT = ReleaseArtifact(path="fixture", bytes=1, sha256="a" * 64)


def plan_record(
    *,
    plan_id: str = "plan-1",
    episode_id: str = "episode-1",
    cut_id: str = "cut-1",
    format: str = "long",
    command_id: str = "approved-cut-1",
    run_id: str = "run-1",
    editorial_master_id: str = "master-1",
    winner_id: str = "winner-1",
    tight_cut_id: str = "tight-1",
    director_acceptance_id: str = "director-1",
    dp_acceptance_id: str = "dp-1",
    visual_acceptance_id: str = "visual-1",
    timeline: str = "長1 - fixture（緊·導播）",
    timeline_uid: str = "fixture-timeline-uid",
    transaction_id: str = "resolve-" + "1" * 24,
    duration_sec: float = 600.0,
    preview: ReleaseArtifact | None = None,
    subtitle: ReleaseArtifact | None = None,
    events: tuple[EventRecord, ...] = (),
    components: tuple[ProjectedComponent, ...] = (),
) -> PlanRecord:
    return PlanRecord(
        plan_id=plan_id,
        command_id=command_id,
        run_id=run_id,
        episode_id=episode_id,
        cut_id=cut_id,
        format=format,  # type: ignore[arg-type]
        editorial_master_id=editorial_master_id,
        winner_id=winner_id,
        tight_cut_id=tight_cut_id,
        director_acceptance_id=director_acceptance_id,
        dp_acceptance_id=dp_acceptance_id,
        visual_acceptance_id=visual_acceptance_id,
        timeline=PlanTimeline(name=timeline, uid=timeline_uid),
        transaction_id=transaction_id,
        duration_sec=duration_sec,
        preview=preview or FIXTURE_ARTIFACT,
        subtitle=subtitle or FIXTURE_ARTIFACT,
        events=events,
        components=components,
    )


__all__ = ["FIXTURE_ARTIFACT", "plan_record"]
