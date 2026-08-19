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

## Relationships

- A **Release** contains one or more **Release Targets**.
- A **Release** has at most one **Campaign Anchor**.
- Every selected **Release Target** inherits the same **Campaign Anchor**.
- A **Calendar Projection** displays the **Campaign Anchor** and actual platform outcomes.
- Changing a **Campaign Anchor** does not approve or publish a **Release**.

## Example dialogue

> **Dev:** "修修把一支 Short 移到 8 月 25 日 09:00，要建立三個排程時間嗎？"
> **Domain expert:** "不要。這是一個 Campaign Anchor；YouTube、Instagram、Facebook 的 Release Target 在第一版全部繼承同一時間，但執行結果仍各自記錄。"

## Flagged ambiguities

- 「Post」曾同時指 Stage 5 成品與 Stage 6 平台投放；已拆成 **Release** 與 **Release Target**。
- 「Schedule」可能被誤解為核准或實際平台排程；**Campaign Anchor** 只代表發布意圖，不能隱含 approval 或 publish transition。
