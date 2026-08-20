# Usopp Publishing

Usopp owns Stage 6 publishing orchestration: turning approved Stage 5 deliverables into independently traceable platform outcomes without collapsing platform-specific execution state.

## Language

**Release**:
A Stage 6 publication plan that groups one approved deliverable with all selected destination platforms.
_Avoid_: Post, upload job

**Release Target**:
One platform-specific execution unit within a Release, with its own status, receipt, URL, and retry boundary.
_Avoid_: Channel, campaign

**Campaign Anchor**:
The single Asia/Taipei publication datetime shared by every selected Release Target in the first scheduling version.
_Avoid_: Per-platform publish time, calendar event

**Calendar Projection**:
A time-oriented representation of Release state; it is not an independent scheduling source of truth.
_Avoid_: Calendar record, schedule database

**Native Arm**:
An ahead-of-time platform acceptance for a future Campaign Anchor. YouTube `publishAt` and Facebook `SCHEDULED` are stored locally as `uploaded`; neither is proof that the content is public.
_Avoid_: Published, completed schedule

**Due Dispatcher**:
The supervised desktop worker that scans shared Campaign Anchors and, at or after the anchor, atomically dispatches only unfinished Instagram Reels targets. It reports heartbeat health and never auto-retries terminal failures.
_Avoid_: Scheduler database, Instagram native schedule, endless retry worker

**Outcome Reconciler**:
The observer that runs after a Native Arm is due, reads a platform's public outcome once, and conditionally confirms the matching local Release Target outcome. It never schedules, publishes, retries, uploads, or recreates content.
_Avoid_: Scheduler, Publisher, Retry Worker, publish clock

## Relationships

- A **Release** contains one or more **Release Targets**.
- A **Release** has at most one **Campaign Anchor**.
- Every selected **Release Target** inherits the same **Campaign Anchor**.
- A **Calendar Projection** displays the **Campaign Anchor** and actual platform outcomes.
- Changing a **Campaign Anchor** does not approve or publish a **Release**.
- A future Short may be **Native Armed** on YouTube and Facebook while its Instagram Release Target remains `approved` for the **Due Dispatcher**.
- The **Due Dispatcher** uses the Release Target `status + updated_at` lease and preserves checkpoints when reclaiming stale `uploading`; explicit operator retry resets only one failed Target.
- The **Outcome Reconciler** observes only due `uploaded` YouTube/Facebook Native Arms with a stable platform identity, then uses `status + video_id + updated_at` compare-and-set before confirming `published` or explicit terminal `failed`.
- The **Outcome Reconciler** treats private, processing, scheduled, missing, contradictory, transport, and authentication evidence as non-public; uncertainty never becomes a terminal Release Target state.

## Example dialogue

> **Dev:** "修修把一支 Short 移到 8 月 25 日 09:00，要建立三個排程時間嗎？"
> **Domain expert:** "不要。這是一個 Campaign Anchor；YouTube、Instagram、Facebook 的 Release Target 在第一版全部繼承同一時間，但執行結果仍各自記錄。"

## Flagged ambiguities

- 「Post」曾同時指 Stage 5 成品與 Stage 6 平台投放；已拆成 **Release** 與 **Release Target**。
- 「Schedule」可能被誤解為核准或實際平台排程；**Campaign Anchor** 只代表發布意圖，不能隱含 approval 或 publish transition。
