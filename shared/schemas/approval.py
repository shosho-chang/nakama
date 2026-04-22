"""HITL approval queue payload schemas (ADR-006 §2).

Discriminated union 以 `action_type` 為 discriminator。
Reader 端必須用 `ApprovalPayloadV1Adapter.validate_python()` 才能觸發正確分派。
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter

from shared.schemas.publishing import DraftV1, PublishComplianceGateV1


class PublishWpPostV1(BaseModel):
    """Brook 新發文章入隊；payload 即完整 DraftV1 + HITL 合規 gate 欄位。

    Brook enqueue 前在 compose 層跑一次 PublishComplianceGateV1 scan 填入 compliance_flags；
    Usopp claim 後會在 publish 前再跑一次（defense in depth），兩次結果不一致視為 fail。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)
    schema_version: Literal[1] = 1
    action_type: Literal["publish_post"]
    target_site: Literal["wp_shosho", "wp_fleet"]
    draft: DraftV1
    compliance_flags: PublishComplianceGateV1
    reviewer_compliance_ack: bool = False
    scheduled_at: AwareDatetime | None = None


class UpdateWpPostV1(BaseModel):
    """更新既有 WP post；patch 若觸發 compliance scan，同樣要過 HITL gate。"""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)
    schema_version: Literal[1] = 1
    action_type: Literal["update_post"]
    target_site: Literal["wp_shosho", "wp_fleet"]
    wp_post_id: int
    patch: dict  # 僅變更欄位
    change_summary: str  # 人類可讀修改說明
    draft_id: str
    compliance_flags: PublishComplianceGateV1
    reviewer_compliance_ack: bool = False


# Pydantic v2 discriminated union：新增 action_type 時在此擴充，Pydantic 會自動依 action_type 分派
ApprovalPayloadV1 = Annotated[
    Union[PublishWpPostV1, UpdateWpPostV1],
    Field(discriminator="action_type"),
]

# Reader 端必用 TypeAdapter；避免 BaseModel.model_validate() 在 Union 上 ambiguous 匹配
ApprovalPayloadV1Adapter: TypeAdapter[ApprovalPayloadV1] = TypeAdapter(ApprovalPayloadV1)
