"""Pydantic models for foundry storyboard.yaml (ADR-032 §6).

extra="forbid" on every model per ADR-019 lesson.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Timing(BaseModel):
    model_config = {"extra": "forbid"}

    start: float
    duration: float


class Transitions(BaseModel):
    model_config = {"extra": "forbid"}

    in_transition: str | None = None
    out_transition: str | None = None


class AssetCandidate(BaseModel):
    """外部素材候選（ADR-051 D5：首選＋備選，修修審核時圈選）."""

    model_config = {"extra": "forbid"}

    url: str
    note: str | None = None


class AssetSpec(BaseModel):
    """外部素材 beat 的來源與出處（ADR-051 D5/D6/D8）.

    ``path`` 在素材取得（下載交接驗收 / 修修外供）後才填，episode 目錄
    相對路徑；render dispatcher 對 asset beat 只驗檔案存在，emit 直接引用。
    """

    model_config = {"extra": "forbid"}

    kind: Literal["stock", "kol", "screen_recording", "supplied"]
    path: str | None = None
    # 檔案 SHA-256（下載驗收時填）。防檔案在原路徑被替換後沿用過期的
    # visual_approved / render_status（Codex panel 2026-07-05 指出的 staleness 洞）。
    sha256: str | None = None
    source_url: str | None = None
    # KOL 片段的來源時間區間，"HH:MM:SS-HH:MM:SS"（ADR-051 D6 出處留痕）
    source_span: str | None = None
    # description 出處清單用的一行文字（D6 護欄：asset beat 強制留出處）
    attribution: str | None = None
    candidates: list[AssetCandidate] = Field(default_factory=list)


class BRollSpec(BaseModel):
    model_config = {"extra": "forbid"}

    render_target: Literal["hyperframes", "reader-playwright", "web-playwright", "asset"]
    component: str
    params: dict[str, Any]
    transitions: Transitions
    asset: AssetSpec | None = None

    @model_validator(mode="after")
    def _asset_iff_asset_target(self) -> BRollSpec:
        if self.render_target == "asset" and self.asset is None:
            raise ValueError("render_target='asset' 的 beat 必須帶 broll.asset")
        if self.render_target != "asset" and self.asset is not None:
            raise ValueError("broll.asset 僅限 render_target='asset' 使用")
        return self


class BeatStatus(BaseModel):
    model_config = {"extra": "forbid"}

    text_approved: bool = False
    render_status: Literal["pending", "rendering", "done", "failed"] = "pending"
    visual_approved: bool = False
    # ADR-038 §D2: 16-char sha256 prefix used in `out/b_roll_<cached_hash>.mp4`
    # filename. Filled by render_dispatcher when the beat is queued (or cache-
    # hit) and read by fcpxml_emitter to resolve the rendered mp4. None until
    # the beat has been considered for rendering at least once.
    cached_hash: str | None = None


class UserNote(BaseModel):
    model_config = {"extra": "forbid"}

    timestamp: str
    note: str


class Beat(BaseModel):
    model_config = {"extra": "forbid"}

    beat_id: int
    start_quote: str
    end_quote: str
    timing: Timing | None = None
    srt_line_ids: list[int] | None = None
    broll_decision: Literal["none", "cutaway"]
    layout: str
    broll: BRollSpec | None = None
    status: BeatStatus
    user_notes: list[UserNote]
